"""Convex Mode — Backtest harness.

Runs the four-stage Convex pipeline against historical data on a per-day
basis, resolves trade outcomes by walking the future price-history
forward, and computes the validation criteria from §11 of the source plan
(hit rate, expectancy, tier comparison, Smart Money Confirmation cohort).

This module exposes pure orchestration — concrete data sources are
dependency-injected via protocols so production wires Polygon flat-file
readers / IVHistoryTable, while tests substitute stubs without touching
S3.

Validation criteria (Phase 8 acceptance gates per §11):
    - Hit rate ≥ 30% (winners = ≥50% gain before stop or expiry)
    - Avg winner ≥ 3× avg loser (in $ terms)
    - Expectancy positive after slippage
    - Max consecutive losses ≤ 6 within any 8-week window
    - Tier A signals materially outperform Tier C
    - Smart Money Confirmation cohort outperforms non-confirmed
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta
from typing import Any, Optional, Protocol, Sequence

from app.convex.pipeline import ConvexPipeline
from app.convex.providers import PeerReactionsCache
from app.convex.stage2_catalyst import Stage2Inputs
from app.convex.stage3_volatility import Stage3Inputs
from app.convex.stage4_contract import Stage4Inputs
from app.convex.tier import FinalisedConvexCandidate, finalise_candidate
from app.core.schemas import (
    ConvexConfig,
    ConvexUniverseSnapshot,
    PriceHistory,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Historical data-source protocols
# ---------------------------------------------------------------------------


class HistoricalStage2Provider(Protocol):
    """Yields Stage 2 inputs as they would have appeared on ``as_of_date``.

    Production: reads the trailing 252 days of PriceHistory plus the
    catalyst calendar and a historical chain snapshot from parquet.
    Tests inject a stub that returns synthetic Stage2Inputs.
    """

    async def fetch(
        self, ticker: str, sector: Optional[str], as_of_iso: str
    ) -> Optional[Stage2Inputs]:
        ...


class HistoricalStage3Provider(Protocol):
    """Yields Stage 3 inputs as they would have appeared on ``as_of_date``."""

    async def fetch(
        self, ticker: str, catalyst_type: Optional[str], as_of_iso: str
    ) -> Optional[Stage3Inputs]:
        ...


class HistoricalStage4Provider(Protocol):
    """Yields Stage 4 inputs as they would have appeared on ``as_of_date``."""

    async def fetch(  # noqa: PLR0913
        self,
        ticker: str,
        direction: str,
        catalyst_type: Optional[str],
        catalyst_date_iso: Optional[str],
        uv_directional_skew: Optional[str],
        as_of_iso: str,
    ) -> Optional[Stage4Inputs]:
        ...


class FuturePriceHistoryProvider(Protocol):
    """Yields forward-walking price-history bars used for outcome resolution."""

    async def fetch(
        self, ticker: str, start_date_iso: str, days: int
    ) -> list[PriceHistory]:
        ...


class HistoricalOptionPriceProvider(Protocol):
    """Yields a single option's mid price on a target date.

    Used to resolve trade exits. Production reads from a historical chain
    parquet snapshot; tests inject a synthetic price function.
    """

    async def fetch(
        self, option_ticker: str, target_date_iso: str
    ) -> Optional[float]:
        ...


# ---------------------------------------------------------------------------
# Backtest configuration + result structures
# ---------------------------------------------------------------------------


@dataclass
class ConvexBacktestConfig:
    """Configuration for one Convex backtest run.

    Distinct from the legacy ``BacktestRunConfig`` because Convex uses a
    different validation regime (no scanner-source filter, tier-based
    evaluation, etc.).
    """

    start_date: str  # YYYY-MM-DD inclusive
    end_date: str    # YYYY-MM-DD inclusive
    convex_config: ConvexConfig
    universe_snapshot: ConvexUniverseSnapshot
    profit_target_pct: float = 50.0     # Winner threshold per source plan
    stop_loss_pct: float = 50.0          # Premium-down stop
    max_holding_days: int = 30           # Time-based exit
    slippage_pct: float = 5.0            # Each side (entry + exit)


@dataclass
class ConvexBacktestTrade:
    """A single resolved Convex backtest trade."""

    ticker: str
    entry_date_iso: str
    exit_date_iso: Optional[str]
    direction: str
    convex_tier: str  # "A" | "B" | "C"
    smart_money_confirmation: bool
    option_ticker: str
    option_type: str
    strike: float
    expiry: str
    dte_at_entry: int
    entry_price: float
    exit_price: Optional[float]
    exit_reason: Optional[str]  # PROFIT_TARGET | STOP_LOSS | TIME_EXIT | EXPIRATION
    pnl_pct: Optional[float]
    mfe_pct: Optional[float]
    mae_pct: Optional[float]
    days_held: Optional[int]


@dataclass
class ValidationReport:
    """§11 acceptance criteria computed from a backtest's resolved trades."""

    total_trades: int
    winners: int
    hit_rate_pct: float
    avg_winner_pnl_pct: float
    avg_loser_pnl_pct: float
    winner_loser_ratio: Optional[float]
    expectancy_pct: float
    max_consecutive_losses: int
    tier_breakdown: dict[str, "TierStats"] = field(default_factory=dict)
    smart_money_breakdown: dict[str, "TierStats"] = field(default_factory=dict)

    def passes_acceptance(
        self,
        min_hit_rate: float = 30.0,
        min_winner_loser_ratio: float = 3.0,
        max_consecutive_losses: int = 6,
    ) -> bool:
        """True when all source-plan §11 acceptance gates pass."""
        if self.total_trades == 0:
            return False
        if self.hit_rate_pct < min_hit_rate:
            return False
        if (
            self.winner_loser_ratio is None
            or self.winner_loser_ratio < min_winner_loser_ratio
        ):
            return False
        if self.expectancy_pct <= 0:
            return False
        if self.max_consecutive_losses > max_consecutive_losses:
            return False
        # Tier A should outperform Tier C when both have trades.
        a_stats = self.tier_breakdown.get("A")
        c_stats = self.tier_breakdown.get("C")
        if a_stats and c_stats and a_stats.expectancy_pct <= c_stats.expectancy_pct:
            return False
        return True


@dataclass
class TierStats:
    """Per-cohort summary used by ValidationReport."""

    label: str
    trades: int
    hit_rate_pct: float
    avg_pnl_pct: float
    expectancy_pct: float


# ---------------------------------------------------------------------------
# Per-day driver
# ---------------------------------------------------------------------------


@dataclass
class HistoricalProviders:
    """Bundle of historical providers used by the backtest harness."""

    stage2: HistoricalStage2Provider
    stage3: HistoricalStage3Provider
    stage4: HistoricalStage4Provider
    future_prices: FuturePriceHistoryProvider
    option_prices: HistoricalOptionPriceProvider


async def run_backtest_day(
    as_of_iso: str,
    config: ConvexBacktestConfig,
    providers: HistoricalProviders,
    peer_cache: PeerReactionsCache,
) -> list[FinalisedConvexCandidate]:
    """Run the four-stage Convex pipeline on a single historical day.

    Returns the finalised CONVEX_APPROVE candidates produced by that day's
    pipeline run. Caller is responsible for resolving each candidate's
    eventual trade outcome (see ``resolve_trade_outcome``).
    """
    # Adapter shims: the protocols accept ``as_of_iso`` while the live
    # ``Stage{2,3,4}InputsProvider`` protocols accept ``today_iso``.
    class _S2Adapter:
        async def fetch(self, ticker, sector, today_iso):
            return await providers.stage2.fetch(ticker, sector, today_iso)

    class _S3Adapter:
        async def fetch(self, ticker, catalyst_type, today_iso):
            return await providers.stage3.fetch(ticker, catalyst_type, today_iso)

    class _S4Adapter:
        async def fetch(  # noqa: PLR0913
            self,
            ticker,
            direction,
            catalyst_type,
            catalyst_date_iso,
            uv_directional_skew,
            today_iso,
        ):
            return await providers.stage4.fetch(
                ticker,
                direction,
                catalyst_type,
                catalyst_date_iso,
                uv_directional_skew,
                today_iso,
            )

    pipeline = ConvexPipeline(
        config=config.convex_config,
        universe_snapshot=config.universe_snapshot,
        stage2_inputs_provider=_S2Adapter(),
        stage3_inputs_provider=_S3Adapter(),
        stage4_inputs_provider=_S4Adapter(),
        as_of_date=as_of_iso,
    )
    pipeline_result = await pipeline.run()

    finalised: list[FinalisedConvexCandidate] = []
    for candidate in pipeline_result.candidates:
        if candidate.advanced_to_stage != 4:
            continue
        evaluation_id = f"backtest-{as_of_iso}-{candidate.ticker}"
        result = finalise_candidate(
            candidate, evaluation_id, "backtest-convex", config.convex_config
        )
        if result is not None:
            finalised.append(result)

    # peer_cache is intentionally accepted but unused at run-day level —
    # callers may pre-populate it for the run. Suppress unused-arg warnings.
    _ = peer_cache
    return finalised


# ---------------------------------------------------------------------------
# Outcome resolution
# ---------------------------------------------------------------------------


async def resolve_trade_outcome(
    finalised: FinalisedConvexCandidate,
    config: ConvexBacktestConfig,
    providers: HistoricalProviders,
) -> ConvexBacktestTrade:
    """Walk forward day-by-day from entry, applying exit rules.

    Exit precedence (per source plan §17):
        1. Profit target: option mid up ``profit_target_pct`` from entry
        2. Stop loss: option mid down ``stop_loss_pct`` from entry
        3. Time exit: ``max_holding_days`` reached
        4. Expiration: contract expires before time exit
    """
    candidate = finalised.candidate
    contract = candidate.selected_call or candidate.selected_put
    assert contract is not None, "Stage 4 PASS requires a selected contract"

    entry_iso = _entry_date(finalised)
    entry_price = _entry_price_with_slippage(contract.ask, config.slippage_pct)

    forward_days = await providers.future_prices.fetch(
        candidate.ticker, entry_iso, config.max_holding_days + 1
    )

    mfe_pct = 0.0
    mae_pct = 0.0
    exit_price: Optional[float] = None
    exit_date_iso: Optional[str] = None
    exit_reason: Optional[str] = None
    expiry_iso = contract.expiry

    profit_target_price = entry_price * (1 + config.profit_target_pct / 100.0)
    stop_loss_price = entry_price * (1 - config.stop_loss_pct / 100.0)

    for n, bar in enumerate(forward_days, start=1):
        if bar.date >= expiry_iso:
            # Contract expired in the meantime; valuation is intrinsic.
            exit_reason = "EXPIRATION"
            exit_date_iso = expiry_iso
            exit_price = max(
                0.0,
                _intrinsic_value(contract.option_type, contract.strike, bar.close),
            )
            break

        opt_price = await providers.option_prices.fetch(
            contract.option_ticker, bar.date
        )
        if opt_price is None:
            continue

        pct_move = ((opt_price - entry_price) / entry_price) * 100.0
        mfe_pct = max(mfe_pct, pct_move)
        mae_pct = min(mae_pct, pct_move)

        if opt_price >= profit_target_price:
            exit_reason = "PROFIT_TARGET"
            exit_date_iso = bar.date
            exit_price = opt_price
            break
        if opt_price <= stop_loss_price:
            exit_reason = "STOP_LOSS"
            exit_date_iso = bar.date
            exit_price = opt_price
            break
        if n >= config.max_holding_days:
            exit_reason = "TIME_EXIT"
            exit_date_iso = bar.date
            exit_price = opt_price
            break

    pnl_pct: Optional[float] = None
    days_held: Optional[int] = None
    if exit_price is not None and exit_date_iso is not None:
        # Slip the exit too.
        net_exit = exit_price * (1 - config.slippage_pct / 100.0)
        pnl_pct = ((net_exit - entry_price) / entry_price) * 100.0
        days_held = _date_diff(entry_iso, exit_date_iso)

    return ConvexBacktestTrade(
        ticker=candidate.ticker,
        entry_date_iso=entry_iso,
        exit_date_iso=exit_date_iso,
        direction=candidate.direction or "ambiguous",
        convex_tier=finalised.tier.value,
        smart_money_confirmation=candidate.smart_money_confirmation,
        option_ticker=contract.option_ticker,
        option_type=contract.option_type,
        strike=contract.strike,
        expiry=contract.expiry,
        dte_at_entry=contract.dte,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl_pct=pnl_pct,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        days_held=days_held,
    )


def _entry_date(finalised: FinalisedConvexCandidate) -> str:
    """Recover entry date from the synthetic evaluation_id (``backtest-<date>-<ticker>``)."""
    eval_id = finalised.decision.evaluation_id
    parts = eval_id.split("-")
    # Format: backtest, YYYY, MM, DD, ticker → 5+ tokens
    if len(parts) >= 4 and parts[0] == "backtest":
        return f"{parts[1]}-{parts[2]}-{parts[3]}"
    return finalised.decision.decided_at[:10]


def _entry_price_with_slippage(ask: float, slippage_pct: float) -> float:
    return ask * (1 + slippage_pct / 100.0)


def _intrinsic_value(option_type: str, strike: float, close: float) -> float:
    if option_type == "CALL":
        return max(0.0, close - strike)
    return max(0.0, strike - close)


def _date_diff(start_iso: str, end_iso: str) -> int:
    return (_date.fromisoformat(end_iso) - _date.fromisoformat(start_iso)).days


# ---------------------------------------------------------------------------
# Multi-day orchestrator
# ---------------------------------------------------------------------------


def trading_days(start_iso: str, end_iso: str) -> list[str]:
    """Return weekday-only ISO dates between start and end inclusive."""
    start = _date.fromisoformat(start_iso)
    end = _date.fromisoformat(end_iso)
    days: list[str] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


async def run_convex_backtest(
    config: ConvexBacktestConfig,
    providers: HistoricalProviders,
) -> tuple[list[ConvexBacktestTrade], ValidationReport]:
    """Top-level: per-day pipeline → resolve outcomes → validation report."""
    days = trading_days(config.start_date, config.end_date)
    peer_cache = PeerReactionsCache(today_iso=config.start_date)

    trades: list[ConvexBacktestTrade] = []
    for day in days:
        finalised = await run_backtest_day(day, config, providers, peer_cache)
        for f in finalised:
            trade = await resolve_trade_outcome(f, config, providers)
            trades.append(trade)

    report = compute_validation_report(trades)
    return trades, report


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------


def compute_validation_report(
    trades: Sequence[ConvexBacktestTrade],
    winner_threshold_pct: float = 50.0,
) -> ValidationReport:
    """Compute the §11 acceptance metrics from a list of resolved trades."""
    resolved = [t for t in trades if t.pnl_pct is not None]
    if not resolved:
        return ValidationReport(
            total_trades=0,
            winners=0,
            hit_rate_pct=0.0,
            avg_winner_pnl_pct=0.0,
            avg_loser_pnl_pct=0.0,
            winner_loser_ratio=None,
            expectancy_pct=0.0,
            max_consecutive_losses=0,
        )

    winners = [t for t in resolved if (t.pnl_pct or 0) >= winner_threshold_pct]
    losers = [t for t in resolved if (t.pnl_pct or 0) < winner_threshold_pct]

    avg_winner = (
        sum(t.pnl_pct for t in winners) / len(winners) if winners else 0.0  # type: ignore[arg-type]
    )
    avg_loser = (
        sum(t.pnl_pct for t in losers) / len(losers) if losers else 0.0  # type: ignore[arg-type]
    )

    winner_loser_ratio: Optional[float]
    if avg_loser >= 0 or avg_winner == 0:
        winner_loser_ratio = None
    else:
        winner_loser_ratio = avg_winner / abs(avg_loser)

    expectancy = sum(t.pnl_pct for t in resolved) / len(resolved)  # type: ignore[arg-type]

    # Longest streak of consecutive losses (sorted by entry date).
    streak = max_streak = 0
    for t in sorted(resolved, key=lambda x: x.entry_date_iso):
        if (t.pnl_pct or 0) < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    return ValidationReport(
        total_trades=len(resolved),
        winners=len(winners),
        hit_rate_pct=(len(winners) / len(resolved)) * 100.0,
        avg_winner_pnl_pct=avg_winner,
        avg_loser_pnl_pct=avg_loser,
        winner_loser_ratio=winner_loser_ratio,
        expectancy_pct=expectancy,
        max_consecutive_losses=max_streak,
        tier_breakdown=_tier_breakdown(resolved, winner_threshold_pct),
        smart_money_breakdown=_smart_money_breakdown(resolved, winner_threshold_pct),
    )


def _tier_breakdown(
    trades: Sequence[ConvexBacktestTrade], winner_threshold_pct: float
) -> dict[str, TierStats]:
    out: dict[str, TierStats] = {}
    for tier in ("A", "B", "C"):
        cohort = [t for t in trades if t.convex_tier == tier]
        out[tier] = _stats_for_cohort(f"Tier {tier}", cohort, winner_threshold_pct)
    return out


def _smart_money_breakdown(
    trades: Sequence[ConvexBacktestTrade], winner_threshold_pct: float
) -> dict[str, TierStats]:
    confirmed = [t for t in trades if t.smart_money_confirmation]
    not_confirmed = [t for t in trades if not t.smart_money_confirmation]
    return {
        "confirmed": _stats_for_cohort("Smart Money", confirmed, winner_threshold_pct),
        "not_confirmed": _stats_for_cohort(
            "No Smart Money", not_confirmed, winner_threshold_pct
        ),
    }


def _stats_for_cohort(
    label: str,
    cohort: Sequence[ConvexBacktestTrade],
    winner_threshold_pct: float,
) -> TierStats:
    if not cohort:
        return TierStats(
            label=label, trades=0,
            hit_rate_pct=0.0, avg_pnl_pct=0.0, expectancy_pct=0.0,
        )
    pnls = [t.pnl_pct for t in cohort if t.pnl_pct is not None]
    winners = sum(1 for p in pnls if p >= winner_threshold_pct)
    avg = sum(pnls) / len(pnls) if pnls else 0.0
    return TierStats(
        label=label,
        trades=len(cohort),
        hit_rate_pct=(winners / len(cohort)) * 100.0,
        avg_pnl_pct=avg,
        expectancy_pct=avg,
    )


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def report_to_dict(report: ValidationReport) -> dict[str, Any]:
    """Convert a ``ValidationReport`` into a JSON-safe dict for logging."""
    return {
        "total_trades": report.total_trades,
        "winners": report.winners,
        "hit_rate_pct": round(report.hit_rate_pct, 2),
        "avg_winner_pnl_pct": round(report.avg_winner_pnl_pct, 2),
        "avg_loser_pnl_pct": round(report.avg_loser_pnl_pct, 2),
        "winner_loser_ratio": (
            round(report.winner_loser_ratio, 2)
            if report.winner_loser_ratio is not None
            else None
        ),
        "expectancy_pct": round(report.expectancy_pct, 2),
        "max_consecutive_losses": report.max_consecutive_losses,
        "tier_breakdown": {
            tier: _tier_to_dict(stats)
            for tier, stats in report.tier_breakdown.items()
        },
        "smart_money_breakdown": {
            cohort: _tier_to_dict(stats)
            for cohort, stats in report.smart_money_breakdown.items()
        },
        "passes_acceptance": report.passes_acceptance(),
    }


def _tier_to_dict(stats: TierStats) -> dict[str, Any]:
    return {
        "label": stats.label,
        "trades": stats.trades,
        "hit_rate_pct": round(stats.hit_rate_pct, 2),
        "avg_pnl_pct": round(stats.avg_pnl_pct, 2),
        "expectancy_pct": round(stats.expectancy_pct, 2),
    }
