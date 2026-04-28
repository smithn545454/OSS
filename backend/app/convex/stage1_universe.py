"""Convex Mode — Stage 1: Kinetic Universe Construction.

Monthly job that filters the optionable equity universe down to names
capable of the moves Convex Mode hunts. Output is a versioned snapshot
written to ``oss-dev-convex-universe-snapshots`` and consumed daily by
Stages 2-4.

Gates (all must pass):
    - Liquidity: avg options daily volume > 5,000 contracts; ATM monthly
      bid-ask < 5% of mid
    - Kinetic capability: count of >2σ daily moves (using trailing 60d HV)
      over trailing 252d ≥ 8
    - HV regime: HV20/HV60 ratio in [0.7, 1.5]
    - Market cap floor: ≥ $1B
    - Sector cap: each sector ≤ 25% of universe (post-filter)

Strength inputs preserved per ticker for downstream tier assignment:
    - tail_event_count_252d
    - hv_regime_ratio
    - avg_options_volume_30d
    - historical_max_30d_move_pct
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.core.schemas import (
    ConvexConfig,
    ConvexStagePayload,
    ConvexUniverseEntry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class TickerKineticInputs:
    """Pre-computed inputs for one ticker passed into Stage 1 gates.

    Producers (e.g., the monthly UniverseConstructor) populate this from
    price-history bars, options chain stats, and ticker fundamentals.
    """

    ticker: str
    closes: Sequence[float]  # Trailing 252+ daily closes (most recent last)
    sector: Optional[str] = None
    market_cap: Optional[float] = None
    avg_options_volume_30d: Optional[float] = None
    avg_atm_spread_pct: Optional[float] = None  # 0-100 (percentage)


# ---------------------------------------------------------------------------
# Pure-function gate logic
# ---------------------------------------------------------------------------


def calculate_realized_volatility(
    closes: Sequence[float], window: int
) -> Optional[float]:
    """Annualized realized volatility from a window of closes.

    Returns None when insufficient data or non-positive prices encountered.
    Mirrors app.scanners.utils.calculate_rv but lives here so the Convex
    module is self-contained and can evolve independently.
    """
    if len(closes) < window + 1:
        return None

    relevant = closes[-(window + 1):]
    log_returns: list[float] = []
    for i in range(1, len(relevant)):
        prev = relevant[i - 1]
        cur = relevant[i]
        if prev <= 0 or cur <= 0:
            return None
        log_returns.append(math.log(cur / prev))

    if len(log_returns) < 2:
        return None

    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def count_tail_events(
    closes: Sequence[float],
    hv_window: int = 60,
    lookback_days: int = 252,
    sigma_threshold: float = 2.0,
) -> int:
    """Count trailing-year days where |daily log return| > sigma_threshold × σ.

    σ is the standard deviation of daily log returns over the trailing
    ``hv_window`` days **ending at each evaluation point** (a rolling
    measure). For Phase 2 we approximate with a single trailing-window σ
    computed once over the whole sample — the count is a cheap statistical
    filter, and rolling-σ refinement is a fast-follow if the gate proves
    too noisy in calibration.
    """
    if len(closes) < hv_window + 1:
        return 0

    # Use the most recent ``lookback_days + 1`` closes (need +1 for returns).
    sample = closes[-(lookback_days + 1):] if len(closes) > lookback_days else list(closes)
    log_returns: list[float] = []
    for i in range(1, len(sample)):
        if sample[i - 1] <= 0 or sample[i] <= 0:
            continue
        log_returns.append(math.log(sample[i] / sample[i - 1]))

    if len(log_returns) < hv_window:
        return 0

    # Daily σ from trailing hv_window returns (taken from the start of the
    # sample so it represents a stable baseline).
    baseline = log_returns[:hv_window]
    mean = sum(baseline) / len(baseline)
    variance = sum((r - mean) ** 2 for r in baseline) / (len(baseline) - 1)
    daily_sigma = math.sqrt(variance) if variance > 0 else 0.0

    if daily_sigma <= 0:
        return 0

    threshold = sigma_threshold * daily_sigma
    return sum(1 for r in log_returns if abs(r) > threshold)


def historical_max_30d_move_pct(closes: Sequence[float]) -> Optional[float]:
    """Largest absolute 30-day return (%) seen in the trailing 252 days.

    Used as a tier-strength input. Returns None when the sample has fewer
    than 31 closes (need ≥31 to compute one 30-day return).
    """
    if len(closes) < 31:
        return None

    moves: list[float] = []
    for i in range(30, len(closes)):
        prev = closes[i - 30]
        cur = closes[i]
        if prev <= 0:
            continue
        moves.append(abs((cur - prev) / prev) * 100.0)

    return max(moves) if moves else None


# ---- Gate predicates --------------------------------------------------------


@dataclass
class GateResult:
    """Outcome of a single Stage 1 gate."""

    pass_: bool
    value: str  # Human-readable measured value


def gate_liquidity(
    inputs: TickerKineticInputs, config: ConvexConfig
) -> GateResult:
    vol = inputs.avg_options_volume_30d
    spread = inputs.avg_atm_spread_pct
    if vol is None or spread is None:
        return GateResult(False, "data unavailable")
    vol_ok = vol >= config.universe_min_options_volume
    spread_ok = spread <= config.universe_max_atm_spread_pct
    return GateResult(
        vol_ok and spread_ok,
        f"avg vol {vol:,.0f} contracts, spread {spread:.1f}%",
    )


def gate_kinetic_capability(
    tail_events: int, config: ConvexConfig
) -> GateResult:
    return GateResult(
        tail_events >= config.universe_min_tail_events_252d,
        f"{tail_events} tail events (>={config.universe_min_tail_events_252d} required)",
    )


def gate_hv_regime(
    hv20: Optional[float], hv60: Optional[float], config: ConvexConfig
) -> GateResult:
    if hv20 is None or hv60 is None or hv60 <= 0:
        return GateResult(False, "HV data unavailable")
    ratio = hv20 / hv60
    in_range = (
        config.universe_hv_regime_min <= ratio <= config.universe_hv_regime_max
    )
    range_label = (
        f"{config.universe_hv_regime_min}-{config.universe_hv_regime_max}"
    )
    return GateResult(
        in_range,
        f"HV20/HV60 = {ratio:.2f} (range {range_label})",
    )


def gate_market_cap(
    inputs: TickerKineticInputs, config: ConvexConfig
) -> GateResult:
    if inputs.market_cap is None:
        return GateResult(False, "market cap unavailable")
    passed = inputs.market_cap >= config.universe_min_market_cap
    return GateResult(
        passed, _format_market_cap(inputs.market_cap)
    )


def _format_market_cap(value: float) -> str:
    if value >= 1e12:
        return f"${value / 1e12:.1f}T"
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    return f"${value / 1e6:.0f}M"


# ---- Per-ticker evaluation --------------------------------------------------


@dataclass
class TickerEvaluation:
    """Stage 1 evaluation for a single ticker (pre-sector-cap)."""

    ticker: str
    passed: bool
    payload: ConvexStagePayload
    entry: Optional[ConvexUniverseEntry]  # None when the ticker fails


def evaluate_ticker(
    inputs: TickerKineticInputs, config: ConvexConfig
) -> TickerEvaluation:
    """Run all four pre-sector-cap gates for one ticker.

    Sector cap enforcement is applied afterward across the full set —
    see ``apply_sector_cap``.
    """
    hv20 = calculate_realized_volatility(inputs.closes, 20)
    hv60 = calculate_realized_volatility(inputs.closes, 60)
    tail_events = count_tail_events(inputs.closes)
    max_30d_move = historical_max_30d_move_pct(inputs.closes)

    liquidity = gate_liquidity(inputs, config)
    kinetic = gate_kinetic_capability(tail_events, config)
    hv_regime = gate_hv_regime(hv20, hv60, config)
    market_cap = gate_market_cap(inputs, config)

    all_pass = all(
        g.pass_ for g in (liquidity, kinetic, hv_regime, market_cap)
    )

    hv_ratio = (hv20 / hv60) if (hv20 is not None and hv60 and hv60 > 0) else None

    summary = _build_summary(inputs.ticker, all_pass, tail_events, hv_ratio, liquidity.value)

    payload = ConvexStagePayload(
        stage=1,
        stage_name="Kinetic Universe",
        result="PASS" if all_pass else "FAIL",
        summary=summary,
        criteria={
            "liquidity": {"pass": liquidity.pass_, "value": liquidity.value},
            "kinetic_capability": {"pass": kinetic.pass_, "value": kinetic.value},
            "hv_regime": {"pass": hv_regime.pass_, "value": hv_regime.value},
            "market_cap": {"pass": market_cap.pass_, "value": market_cap.value},
        },
        strength_inputs={
            "tail_event_count_252d": tail_events,
            "hv_regime_ratio": hv_ratio,
            "historical_max_30d_move_pct": max_30d_move,
            "avg_options_volume_30d": inputs.avg_options_volume_30d,
        },
    )

    if not all_pass:
        return TickerEvaluation(
            ticker=inputs.ticker, passed=False, payload=payload, entry=None
        )

    entry = ConvexUniverseEntry(
        ticker=inputs.ticker,
        sector=inputs.sector,
        market_cap=inputs.market_cap,
        avg_options_volume_30d=inputs.avg_options_volume_30d,
        avg_atm_spread_pct=inputs.avg_atm_spread_pct,
        tail_event_count_252d=tail_events,
        hv_regime_ratio=hv_ratio,
        historical_max_30d_move_pct=max_30d_move,
    )
    return TickerEvaluation(
        ticker=inputs.ticker, passed=True, payload=payload, entry=entry
    )


def _build_summary(
    ticker: str,
    passed: bool,
    tail_events: int,
    hv_ratio: Optional[float],
    liquidity_value: str,
) -> str:
    if passed:
        ratio_str = f"{hv_ratio:.2f}" if hv_ratio is not None else "n/a"
        return (
            f"{ticker} qualifies as kinetically capable: {tail_events} tail "
            f"events in trailing year, HV regime ratio {ratio_str}, "
            f"options liquidity {liquidity_value}."
        )
    return f"{ticker} did not qualify for the Convex kinetic universe."


# ---------------------------------------------------------------------------
# Sector cap enforcement
# ---------------------------------------------------------------------------


def apply_sector_cap(
    entries: list[ConvexUniverseEntry],
    config: ConvexConfig,
) -> tuple[list[ConvexUniverseEntry], dict[str, int]]:
    """Trim entries so no sector exceeds ``universe_max_sector_pct``.

    For overrepresented sectors, keep the entries with the highest
    ``tail_event_count_252d`` (proxy for kinetic strength) and drop the
    rest. Returns the trimmed list plus a sector distribution map.

    A null/missing sector is bucketed under ``"Unknown"`` for cap purposes.
    """
    if not entries:
        return entries, {}

    max_per_sector = max(1, int(len(entries) * config.universe_max_sector_pct))

    by_sector: dict[str, list[ConvexUniverseEntry]] = {}
    for e in entries:
        by_sector.setdefault(e.sector or "Unknown", []).append(e)

    trimmed: list[ConvexUniverseEntry] = []
    for sector, members in by_sector.items():
        if len(members) <= max_per_sector:
            trimmed.extend(members)
            continue
        # Keep the highest kinetic-strength members.
        ranked = sorted(
            members,
            key=lambda x: x.tail_event_count_252d,
            reverse=True,
        )
        kept = ranked[:max_per_sector]
        dropped = len(members) - len(kept)
        logger.info(
            "Sector cap: %s had %d members, kept top %d (dropped %d)",
            sector, len(members), len(kept), dropped,
        )
        trimmed.extend(kept)

    distribution = {
        sector: sum(1 for e in trimmed if (e.sector or "Unknown") == sector)
        for sector in by_sector.keys()
    }
    return trimmed, distribution


# ---------------------------------------------------------------------------
# Top-level orchestrator helper
# ---------------------------------------------------------------------------


@dataclass
class UniverseBuildResult:
    """Aggregate result of building a Convex universe snapshot.

    ``entries`` is the post-sector-cap accepted list.
    ``payloads`` maps ticker → Stage 1 payload (for telemetry / debug page).
    ``rejected_tickers`` lists tickers that failed Stage 1 gates.
    ``capped_tickers`` lists tickers that passed gates but were dropped by
    the sector cap.
    ``rejection_breakdown`` counts per-gate failures across all rejected
    tickers (sum may exceed len(rejected_tickers) when multiple gates fail
    on the same ticker — first-failed gate is what's recorded).
    """

    entries: list[ConvexUniverseEntry]
    payloads: dict[str, ConvexStagePayload]
    rejected_tickers: list[str]
    capped_tickers: list[str]
    sector_distribution: dict[str, int]
    rejection_breakdown: dict[str, int] = field(default_factory=dict)


def build_universe(
    inputs: list[TickerKineticInputs],
    config: ConvexConfig,
) -> UniverseBuildResult:
    """Build a kinetic-universe snapshot from precomputed ticker inputs.

    Pure function — caller is responsible for fetching closes / market cap
    / options volume (typically the monthly UniverseConstructor) and for
    persisting the result to ConvexUniverseSnapshotTable.
    """
    payloads: dict[str, ConvexStagePayload] = {}
    pre_cap_entries: list[ConvexUniverseEntry] = []
    rejected: list[str] = []
    rejection_breakdown: dict[str, int] = {
        "liquidity": 0,
        "kinetic_capability": 0,
        "hv_regime": 0,
        "market_cap": 0,
    }

    for inp in inputs:
        ev = evaluate_ticker(inp, config)
        payloads[ev.ticker] = ev.payload
        if ev.passed and ev.entry is not None:
            pre_cap_entries.append(ev.entry)
        else:
            rejected.append(ev.ticker)
            # Record the FIRST failing gate (in evaluation order) so the
            # breakdown reflects the dominant rejector. If multiple gates
            # fail, only the first one shows up in this counter.
            for gate_name in ("liquidity", "kinetic_capability", "hv_regime", "market_cap"):
                gate = ev.payload.criteria.get(gate_name, {})
                if not gate.get("pass", True):
                    rejection_breakdown[gate_name] += 1
                    break

    accepted, distribution = apply_sector_cap(pre_cap_entries, config)
    accepted_tickers = {e.ticker for e in accepted}
    capped = [
        e.ticker for e in pre_cap_entries if e.ticker not in accepted_tickers
    ]

    logger.info(
        "Convex universe built: in=%d, gate-fail=%d, sector-capped=%d, accepted=%d "
        "[breakdown: liq=%d kinetic=%d hv=%d mcap=%d]",
        len(inputs), len(rejected), len(capped), len(accepted),
        rejection_breakdown["liquidity"], rejection_breakdown["kinetic_capability"],
        rejection_breakdown["hv_regime"], rejection_breakdown["market_cap"],
    )

    return UniverseBuildResult(
        entries=accepted,
        payloads=payloads,
        rejected_tickers=rejected,
        capped_tickers=capped,
        sector_distribution=distribution,
        rejection_breakdown=rejection_breakdown,
    )
