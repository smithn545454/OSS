"""Trade Structure Pillar (Policy v4.0.0).

Measures the contract-level quality of the trade — did we pick the right
strike, DTE, and IV regime for the setup? Smallest exponent (0.25) in the
v4 geometric-mean composite, but still critical: great direction + great
move potential wrapped in a bad contract structure is still a low-EV trade.

Subscores (weights must sum to 1.0 in the active policy):

- **Delta Sweet Spot** (0.25) — via ``abs_delta``. Peaks near 0.30
  (asymmetric-risk region with positive gamma/delta curvature).
- **Gamma / Theta Ratio** (0.25) — via ``gamma_theta_ratio``, computed as
  ``(gamma * close^2 * iv) / abs(theta)``. Rewards contracts where the
  move convexity outweighs daily decay.
- **DTE Sweet Spot** (0.20) — via ``dte_sweet_spot_score``, catalyst-aware.
- **IV Rank** (0.20) — via ``iv_percentile`` (monotonic: lower is better).
- **Strike Proximity to Pivot** (0.10) — via ``strike_vs_pivot_pct``,
  distance from the nearest 52-week extreme in the option direction.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.schemas import PillarConfigV2, PillarId
from app.pillars.composite import apply_v4_rules, count_available
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.scoring_engine import score_pillar

PILLAR_ID = PillarId.TRADE_STRUCTURE


def compute_trade_structure_pillar(
    ctx: ScoringContext,
    config: PillarConfigV2,
) -> PillarResult:
    """Compute the Trade Structure pillar score."""
    if config.pillar_id != PILLAR_ID:
        raise ValueError(
            f"Expected pillar_id={PILLAR_ID}, got {config.pillar_id}"
        )

    accessor = _TradeStructureAccessor(ctx)
    raw_score, subscores = score_pillar(config, accessor)
    pillar_score = apply_v4_rules(raw_score, subscores)
    tags = _generate_tags(ctx, subscores, pillar_score)

    return PillarResult(
        pillar_id=PILLAR_ID,
        evaluation_id=ctx.evaluation_id,
        score=pillar_score,
        subscores=subscores,
        tags=tags,
    )


# ============================================================================
# Accessor — exposes derived feature values to the scoring engine
# ============================================================================


class _TradeStructureAccessor:
    """Translates ScoringContext into feature_field names this pillar reads."""

    def __init__(self, ctx: ScoringContext) -> None:
        self._ctx = ctx

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name, None)

    @property
    def abs_delta(self) -> Optional[float]:
        return self._ctx.abs_delta

    @property
    def gamma_theta_ratio(self) -> Optional[float]:
        return _gamma_theta_ratio(self._ctx)

    @property
    def dte_sweet_spot_score(self) -> Optional[float]:
        return _dte_sweet_spot(self._ctx)

    @property
    def iv_percentile(self) -> Optional[float]:
        return self._ctx.iv_percentile

    @property
    def strike_vs_pivot_pct(self) -> Optional[float]:
        return _strike_pivot_distance(self._ctx)


# ============================================================================
# Derived feature computations
# ============================================================================


def _gamma_theta_ratio(ctx: ScoringContext) -> Optional[float]:
    """Gamma-scaled convexity over daily decay cost.

    Formula: ``gamma * close^2 * iv / abs(theta)`` — higher = richer
    convexity per dollar of daily bleed. Zero / missing inputs return None.
    """
    gamma = ctx.gamma
    theta = ctx.theta
    close = ctx.close
    iv = ctx.iv

    if gamma is None or theta is None or close is None:
        return None
    if close <= 0 or not iv or abs(theta) < 1e-9:
        return None
    return (gamma * close * close * iv) / abs(theta)


def _dte_sweet_spot(ctx: ScoringContext) -> Optional[float]:
    """Catalyst-aware DTE score.

    Without a catalyst:
        21-45 DTE → high scores (balance of theta decay vs move window)
        <14 DTE → penalized (too-rapid theta, narrow window)
        >60 DTE → penalized (too much vega, capital tie-up)

    With catalyst in window (days_to_earnings <= dte):
        DTE = catalyst_day + 3..10 → highest (time to trade catalyst + post-event drift)
        DTE = catalyst_day + 0..2 → near-miss penalty (decay hits)
    """
    dte = ctx.dte
    if dte is None or dte <= 0:
        return None

    dte_e = ctx.days_to_earnings
    if dte_e is not None and 0 <= dte_e <= dte:
        buffer_days = dte - dte_e
        if 3 <= buffer_days <= 10:
            return 92.0
        if buffer_days < 3:
            return 55.0
        if buffer_days <= 20:
            return 80.0
        return 65.0

    # No catalyst — standard sweet spot around 28-35 DTE.
    if 28 <= dte <= 35:
        return 90.0
    if 21 <= dte <= 45:
        return 78.0
    if 14 <= dte <= 60:
        return 55.0
    if 7 <= dte < 14:
        return 35.0
    return 20.0


def _strike_pivot_distance(ctx: ScoringContext) -> Optional[float]:
    """Distance of the strike from the option-direction pivot, as a % of the pivot.

    CALL pivot = 52w high (the level a Sharpshooter call-trader wants the
    strike close to). PUT pivot = 52w low. Returned as absolute distance
    in percent — breakpoints should peak near 0 (strike at pivot).
    """
    strike = ctx.strike
    if strike is None or strike <= 0:
        return None

    bullish = ctx.option_type == "CALL"
    pivot = ctx.high_52w if bullish else ctx.low_52w
    if pivot is None or pivot <= 0:
        return None
    return abs(strike - pivot) / pivot * 100.0


# ============================================================================
# Tags
# ============================================================================


def _generate_tags(
    ctx: ScoringContext,
    subscores: list[Subscore],
    pillar_score: float,
) -> list[str]:
    tags: list[str] = []

    if pillar_score == 0.0:
        tags.append("INSUFFICIENT_DATA")
        return tags

    if count_available(subscores) < 5:
        tags.append("PARTIAL_COVERAGE")

    abs_delta = ctx.abs_delta
    if abs_delta is not None and 0.25 <= abs_delta <= 0.35:
        tags.append("DELTA_SHARPSHOOTER")

    gt_ratio = _gamma_theta_ratio(ctx)
    if gt_ratio is not None and gt_ratio >= 25:
        tags.append("GAMMA_RICH")

    if ctx.dte:
        dte_e = ctx.days_to_earnings
        if dte_e is not None and 0 <= dte_e <= ctx.dte and 3 <= (ctx.dte - dte_e) <= 10:
            tags.append("DTE_SWEETSPOT")
        elif 21 <= ctx.dte <= 45:
            tags.append("DTE_SWEETSPOT")

    if ctx.iv_percentile is not None:
        if ctx.iv_percentile <= 30:
            tags.append("IV_RANK_CHEAP")
        elif ctx.iv_percentile >= 70:
            tags.append("IV_RANK_RICH")

    strike_dist = _strike_pivot_distance(ctx)
    if strike_dist is not None and strike_dist <= 3:
        tags.append("STRIKE_AT_PIVOT")

    return tags
