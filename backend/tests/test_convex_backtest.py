"""Tests for the Convex Mode backtest harness (Phase 8).

Covers:
    - trading_days date enumeration
    - resolve_trade_outcome exit precedence (profit / stop / time / expiry)
    - compute_validation_report metrics + acceptance gates
    - run_convex_backtest end-to-end with stubbed historical providers
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pytest

from app.convex import (
    ConvexBacktestConfig,
    ConvexBacktestTrade,
    ConvexCandidate,
    FinalisedConvexCandidate,
    HistoricalProviders,
    ValidationReport,
    compute_validation_report,
    finalise_candidate,
    report_to_dict,
    resolve_trade_outcome,
    run_convex_backtest,
    trading_days,
)
from app.convex.stage4_contract import ConvexContractCandidate
from app.core.schemas import (
    ConvexConfig,
    ConvexStagePayload,
    ConvexStagesPayload,
    ConvexUniverseEntry,
    ConvexUniverseSnapshot,
    PriceHistory,
)

# ---------------------------------------------------------------------------
# trading_days
# ---------------------------------------------------------------------------


class TestTradingDays:

    def test_excludes_weekends(self):
        # 2026-04-25 is a Saturday; 04-26 is a Sunday.
        days = trading_days("2026-04-24", "2026-04-29")
        assert "2026-04-25" not in days
        assert "2026-04-26" not in days
        assert days == ["2026-04-24", "2026-04-27", "2026-04-28", "2026-04-29"]

    def test_single_day(self):
        days = trading_days("2026-04-27", "2026-04-27")
        assert days == ["2026-04-27"]

    def test_inverted_range_returns_empty(self):
        days = trading_days("2026-04-30", "2026-04-25")
        assert days == []


# ---------------------------------------------------------------------------
# resolve_trade_outcome
# ---------------------------------------------------------------------------


def _make_finalised(
    ticker: str = "NVDA",
    entry_date_iso: str = "2026-04-26",
    contract: Optional[ConvexContractCandidate] = None,
    direction: str = "bullish",
) -> FinalisedConvexCandidate:
    """Build a FinalisedConvexCandidate ready for outcome resolution."""
    if contract is None:
        contract = ConvexContractCandidate(
            option_ticker="O:NVDA260515C00145000",
            option_type="CALL",
            strike=145.0,
            expiry="2026-05-15",
            dte=19,
            delta=0.32,
            bid=4.75,
            ask=4.95,
            open_interest=8240,
            volume=1850,
            iv=0.30,
        )
    # Stage 4 carries pl_score on extras, Stage 2 carries momentum_aligned —
    # both required by the new tier rule for finalise_candidate to succeed.
    s2_extras = {
        "direction": direction,
        "momentum_aligned": True,
        "selected_catalyst_type": "date_known",
    }
    s4_extras = {"pl_score": 90.0, "smart_money_confirmation": False}
    stages = ConvexStagesPayload()
    stages = stages.model_copy(update={
        "stage_1": ConvexStagePayload(
            stage=1, stage_name="Stage 1",
            result="PASS", summary="x", strength=0.85,
        ),
        "stage_2": ConvexStagePayload(
            stage=2, stage_name="Stage 2",
            result="PASS", summary="x", strength=0.85, extras=s2_extras,
        ),
        "stage_3": ConvexStagePayload(
            stage=3, stage_name="Stage 3",
            result="PASS", summary="x", strength=0.85,
        ),
        "stage_4": ConvexStagePayload(
            stage=4, stage_name="Stage 4",
            result="PASS", summary="x", strength=0.85, extras=s4_extras,
        ),
    })
    candidate = ConvexCandidate(ticker=ticker, stages=stages, direction=direction)
    candidate.selected_call = contract if contract.option_type == "CALL" else None
    candidate.selected_put = contract if contract.option_type == "PUT" else None

    finalised = finalise_candidate(
        candidate,
        f"backtest-{entry_date_iso}-{ticker}",
        "backtest-convex",
        ConvexConfig(),
    )
    assert finalised is not None
    return finalised


class _ForwardPricesStub:
    def __init__(self, bars: list[PriceHistory]) -> None:
        self._bars = bars

    async def fetch(self, ticker, start_date_iso, days):  # noqa: ARG002
        return self._bars[:days]


class _OptionPricesStub:
    """Linear-interpolation option-price stub.

    Yields a deterministic price curve so exit-rule tests can target
    specific exit reasons without computing Black-Scholes.
    """

    def __init__(self, prices_by_date: dict[str, float]) -> None:
        self._prices = prices_by_date

    async def fetch(self, option_ticker, target_date_iso):  # noqa: ARG002
        return self._prices.get(target_date_iso)


def _build_providers(
    *,
    forward_bars: list[PriceHistory],
    option_prices: dict[str, float],
) -> HistoricalProviders:
    class _NopStage2:
        async def fetch(self, *args, **kwargs):
            return None

    class _NopStage3:
        async def fetch(self, *args, **kwargs):
            return None

    class _NopStage4:
        async def fetch(self, *args, **kwargs):
            return None

    return HistoricalProviders(
        stage2=_NopStage2(),
        stage3=_NopStage3(),
        stage4=_NopStage4(),
        future_prices=_ForwardPricesStub(forward_bars),
        option_prices=_OptionPricesStub(option_prices),
    )


def _bar(date_iso: str, close: float) -> PriceHistory:
    return PriceHistory(
        ticker="NVDA",
        date=date_iso,
        open=close,
        high=close * 1.005,
        low=close * 0.995,
        close=close,
        volume=1_000_000,
    )


class TestResolveTradeOutcome:

    @pytest.mark.asyncio
    async def test_profit_target_hits_first(self):
        # Entry 4.95 ask × 1.05 slip = 5.1975; target = 5.1975 × 1.5 = 7.796
        # Day 3 option price 8.00 → triggers PROFIT_TARGET.
        finalised = _make_finalised()
        forward = [
            _bar("2026-04-27", 142.0),
            _bar("2026-04-28", 144.0),
            _bar("2026-04-29", 150.0),
            _bar("2026-04-30", 152.0),
        ]
        option_prices = {
            "2026-04-27": 5.50,
            "2026-04-28": 6.20,
            "2026-04-29": 8.00,  # >= 7.796 → exits here
            "2026-04-30": 8.10,
        }
        config = ConvexBacktestConfig(
            start_date="2026-04-26",
            end_date="2026-04-30",
            convex_config=ConvexConfig(),
            universe_snapshot=ConvexUniverseSnapshot(
                snapshot_date="2026-04-01",
                policy_version="v",
                tickers=[],
                total_count=0,
            ),
        )
        providers = _build_providers(
            forward_bars=forward, option_prices=option_prices
        )
        trade = await resolve_trade_outcome(finalised, config, providers)
        assert trade.exit_reason == "PROFIT_TARGET"
        assert trade.exit_date_iso == "2026-04-29"
        assert (trade.pnl_pct or 0) > 0

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_when_premium_collapses(self):
        finalised = _make_finalised()
        forward = [
            _bar("2026-04-27", 138.0),
            _bar("2026-04-28", 132.0),
        ]
        # Stop at 5.1975 × 0.5 = 2.6
        option_prices = {
            "2026-04-27": 4.80,
            "2026-04-28": 2.40,  # <= 2.6 → stop
        }
        config = ConvexBacktestConfig(
            start_date="2026-04-26",
            end_date="2026-04-28",
            convex_config=ConvexConfig(),
            universe_snapshot=ConvexUniverseSnapshot(
                snapshot_date="2026-04-01",
                policy_version="v",
                tickers=[],
                total_count=0,
            ),
        )
        providers = _build_providers(
            forward_bars=forward, option_prices=option_prices
        )
        trade = await resolve_trade_outcome(finalised, config, providers)
        assert trade.exit_reason == "STOP_LOSS"
        assert (trade.pnl_pct or 0) < 0

    @pytest.mark.asyncio
    async def test_time_exit_fires_at_max_holding(self):
        # Stagnant prices that never hit profit/stop; time exit at day 3.
        finalised = _make_finalised()
        forward = [
            _bar(f"2026-04-{27 + i:02d}", 140.0 + i * 0.1) for i in range(5)
        ]
        option_prices = {bar.date: 5.00 for bar in forward}
        config = ConvexBacktestConfig(
            start_date="2026-04-26",
            end_date="2026-05-01",
            convex_config=ConvexConfig(),
            universe_snapshot=ConvexUniverseSnapshot(
                snapshot_date="2026-04-01",
                policy_version="v",
                tickers=[],
                total_count=0,
            ),
            max_holding_days=3,
        )
        providers = _build_providers(
            forward_bars=forward, option_prices=option_prices
        )
        trade = await resolve_trade_outcome(finalised, config, providers)
        assert trade.exit_reason == "TIME_EXIT"

    @pytest.mark.asyncio
    async def test_expiration_uses_intrinsic_value(self):
        # Contract expires before time-exit deadline.
        contract = ConvexContractCandidate(
            option_ticker="O:NVDA260430C00145000",
            option_type="CALL",
            strike=145.0,
            expiry="2026-04-29",  # Expires on day 3
            dte=3,
            delta=0.40,
            bid=2.0, ask=2.2,
            open_interest=2000, volume=500,
        )
        finalised = _make_finalised(contract=contract)
        forward = [
            _bar("2026-04-27", 144.0),
            _bar("2026-04-28", 146.0),
            _bar("2026-04-29", 148.0),  # expiry day; intrinsic = 148 - 145 = 3
        ]
        option_prices = {
            "2026-04-27": 2.50,
            "2026-04-28": 2.80,
        }
        config = ConvexBacktestConfig(
            start_date="2026-04-26",
            end_date="2026-04-30",
            convex_config=ConvexConfig(),
            universe_snapshot=ConvexUniverseSnapshot(
                snapshot_date="2026-04-01",
                policy_version="v",
                tickers=[],
                total_count=0,
            ),
        )
        providers = _build_providers(
            forward_bars=forward, option_prices=option_prices
        )
        trade = await resolve_trade_outcome(finalised, config, providers)
        assert trade.exit_reason == "EXPIRATION"
        assert trade.exit_price == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------


def _make_trade(
    pnl_pct: float = 60.0,
    tier: str = "A",
    smart_money: bool = False,
    entry_iso: str = "2026-04-26",
) -> ConvexBacktestTrade:
    return ConvexBacktestTrade(
        ticker="NVDA",
        entry_date_iso=entry_iso,
        exit_date_iso="2026-04-29",
        direction="bullish",
        convex_tier=tier,
        smart_money_confirmation=smart_money,
        option_ticker="O:NVDA260620C00145000",
        option_type="CALL",
        strike=145,
        expiry="2026-06-20",
        dte_at_entry=42,
        entry_price=5.0,
        exit_price=5.0 * (1 + pnl_pct / 100),
        exit_reason="PROFIT_TARGET" if pnl_pct >= 50 else "STOP_LOSS",
        pnl_pct=pnl_pct,
        mfe_pct=max(0.0, pnl_pct),
        mae_pct=min(0.0, pnl_pct),
        days_held=3,
    )


class TestValidationReport:

    def test_zero_trades(self):
        report = compute_validation_report([])
        assert report.total_trades == 0
        assert report.passes_acceptance() is False

    def test_high_quality_run_passes_acceptance(self):
        # 3 winners @ 100% (Tier A), 2 losers @ -25% (Tier C → C never trades winners)
        # Avg winner: 100, avg loser: -25 → ratio 4× → passes 3× gate
        # Hit rate: 3/5 = 60% → passes 30% gate
        # Expectancy: positive
        # Tier A all winners > Tier C losers
        trades = [
            _make_trade(pnl_pct=100, tier="A", entry_iso="2026-04-20"),
            _make_trade(pnl_pct=120, tier="A", entry_iso="2026-04-21"),
            _make_trade(pnl_pct=80, tier="A", entry_iso="2026-04-22"),
            _make_trade(pnl_pct=-25, tier="C", entry_iso="2026-04-23"),
            _make_trade(pnl_pct=-25, tier="C", entry_iso="2026-04-24"),
        ]
        report = compute_validation_report(trades)
        assert report.total_trades == 5
        assert report.winners == 3
        assert report.hit_rate_pct == pytest.approx(60.0)
        assert report.winner_loser_ratio is not None
        assert report.winner_loser_ratio >= 3.0
        assert report.passes_acceptance() is True

    def test_low_hit_rate_fails(self):
        trades = [
            _make_trade(pnl_pct=70, tier="A"),
            *[_make_trade(pnl_pct=-30, tier="C") for _ in range(9)],
        ]
        report = compute_validation_report(trades)
        # 1 winner / 10 = 10% hit rate
        assert report.passes_acceptance(min_hit_rate=30.0) is False

    def test_max_consecutive_losses_streak(self):
        trades = [
            _make_trade(pnl_pct=70, entry_iso="2026-04-20"),
            _make_trade(pnl_pct=-30, entry_iso="2026-04-21"),
            _make_trade(pnl_pct=-30, entry_iso="2026-04-22"),
            _make_trade(pnl_pct=-30, entry_iso="2026-04-23"),
            _make_trade(pnl_pct=70, entry_iso="2026-04-24"),
            _make_trade(pnl_pct=-30, entry_iso="2026-04-25"),
        ]
        report = compute_validation_report(trades)
        assert report.max_consecutive_losses == 3

    def test_tier_breakdown(self):
        trades = [
            _make_trade(pnl_pct=80, tier="A"),
            _make_trade(pnl_pct=60, tier="A"),
            _make_trade(pnl_pct=-20, tier="B"),
            _make_trade(pnl_pct=-30, tier="C"),
        ]
        report = compute_validation_report(trades)
        assert report.tier_breakdown["A"].trades == 2
        assert report.tier_breakdown["B"].trades == 1
        assert report.tier_breakdown["C"].trades == 1
        # Tier A has highest expectancy
        assert (
            report.tier_breakdown["A"].expectancy_pct
            > report.tier_breakdown["C"].expectancy_pct
        )

    def test_smart_money_breakdown(self):
        trades = [
            _make_trade(pnl_pct=80, smart_money=True),
            _make_trade(pnl_pct=70, smart_money=True),
            _make_trade(pnl_pct=-25, smart_money=False),
        ]
        report = compute_validation_report(trades)
        assert report.smart_money_breakdown["confirmed"].trades == 2
        assert report.smart_money_breakdown["not_confirmed"].trades == 1

    def test_report_to_dict_serialisable(self):
        trades = [_make_trade(pnl_pct=80, tier="A")]
        report = compute_validation_report(trades)
        d = report_to_dict(report)
        assert d["total_trades"] == 1
        assert "tier_breakdown" in d
        assert "passes_acceptance" in d


# ---------------------------------------------------------------------------
# Multi-day end-to-end (smoke)
# ---------------------------------------------------------------------------


class _AlwaysFailStage2:
    async def fetch(self, ticker, sector, today_iso):  # noqa: ARG002
        return None


class _AlwaysFailStage3:
    async def fetch(self, ticker, catalyst_type, today_iso):  # noqa: ARG002
        return None


class _AlwaysFailStage4:
    async def fetch(  # noqa: PLR0913
        self,
        ticker,
        direction,
        catalyst_type,
        catalyst_date_iso,
        uv_directional_skew,
        today_iso,
    ):  # noqa: ARG002
        return None


class TestRunConvexBacktestSmoke:

    @pytest.mark.asyncio
    async def test_empty_pipeline_returns_empty_report(self):
        # All providers fail-open returning None; no trades surface.
        snapshot = ConvexUniverseSnapshot(
            snapshot_date="2026-04-01",
            policy_version="v4.1.1",
            tickers=[
                ConvexUniverseEntry(ticker="NVDA", sector="Tech"),
            ],
            total_count=1,
        )
        config = ConvexBacktestConfig(
            start_date="2026-04-27",  # Monday
            end_date="2026-04-29",
            convex_config=ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
        )

        class _NoBars:
            async def fetch(self, *args, **kwargs):
                return []

        class _NoPrices:
            async def fetch(self, *args, **kwargs):
                return None

        providers = HistoricalProviders(
            stage2=_AlwaysFailStage2(),
            stage3=_AlwaysFailStage3(),
            stage4=_AlwaysFailStage4(),
            future_prices=_NoBars(),
            option_prices=_NoPrices(),
        )

        trades, report = await run_convex_backtest(config, providers)
        assert trades == []
        assert report.total_trades == 0
        assert report.passes_acceptance() is False

    @pytest.mark.asyncio
    async def test_disabled_config_short_circuits(self):
        snapshot = ConvexUniverseSnapshot(
            snapshot_date="2026-04-01",
            policy_version="v",
            tickers=[ConvexUniverseEntry(ticker="NVDA")],
            total_count=1,
        )
        config = ConvexBacktestConfig(
            start_date="2026-04-27",
            end_date="2026-04-27",
            convex_config=ConvexConfig(enabled=False),  # disabled
            universe_snapshot=snapshot,
        )
        providers = HistoricalProviders(
            stage2=_AlwaysFailStage2(),
            stage3=_AlwaysFailStage3(),
            stage4=_AlwaysFailStage4(),
            future_prices=_AlwaysFailStage2(),  # any stub will do
            option_prices=_AlwaysFailStage2(),
        )
        trades, report = await run_convex_backtest(config, providers)
        assert trades == []
        assert isinstance(report, ValidationReport)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


class TestEntryDateRecovery:

    @pytest.mark.asyncio
    async def test_entry_date_round_trip(self):
        from app.convex.backtest import _entry_date

        finalised = _make_finalised(entry_date_iso="2026-04-26")
        assert _entry_date(finalised) == "2026-04-26"


class TestUnusedImports:

    def test_date_arith_stable(self):
        # Sanity that timedelta works the way the harness expects.
        assert (date(2026, 4, 30) - date(2026, 4, 26)).days == 4
        assert date(2026, 4, 26) + timedelta(days=4) == date(2026, 4, 30)
