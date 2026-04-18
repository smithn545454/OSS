"""Directional Conviction Pillar (Policy v4.0.0).

Measures how confidently the underlying is trending in the option's direction.
This is the dominant pillar (0.40 exponent in the geometric mean composite) —
a weak directional signal collapses the Sharpshooter score even when the move
structure and trade structure are excellent.

Subscores (weights must sum to 1.0 in the active policy):

- **Stage 2 Trend Template** (0.30) — Minervini 7-criteria score (0-100):
  close > sma50, close > ma_150, close > ma_200, sma50 > ma_150, ma_150 > ma_200,
  close > 30% above 52w low, close within 25% of 52w high. Computed on-the-fly
  because the template is a fixed rule, not a tunable breakpoint.
- **Relative Strength 20d vs SPY** (0.20) — via ``rs_20d``.
- **ADX × ±DI Directional Agreement** (0.15) — via ``adx_directional_score``,
  combines ADX magnitude with DI sign agreement to the option direction.
- **Proximity to Breakout Pivot** (0.15) — via ``breakout_proximity_pct``,
  distance from the nearest 52-week extreme in the option direction.
- **Volume / OBV Confirmation** (0.10) — via ``obv_confirmation_score``.
- **Sector Relative Strength** (0.10) — via ``sector_rs_20d``.

Direction handling: all derived values are computed in terms of the option
direction (CALL → bullish, PUT → bearish), so a 95/100 directional conviction
means *aligned* with the option, regardless of call/put.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.schemas import PillarConfigV2, PillarId
from app.pillars.composite import apply_v4_rules, count_available
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.scoring_engine import score_pillar

PILLAR_ID = PillarId.DIRECTIONAL_CONVICTION


def compute_directional_conviction_pillar(
    ctx: ScoringContext,
    config: PillarConfigV2,
) -> PillarResult:
    """Compute the Directional Conviction pillar score.

    Applies the v4 min-subscore rule (return 0 when < 3 subscores available,
    which zero-collapses the geometric-mean composite into REJECT).
    """
    if config.pillar_id != PILLAR_ID:
        raise ValueError(
            f"Expected pillar_id={PILLAR_ID}, got {config.pillar_id}"
        )

    accessor = _DirectionalConvictionAccessor(ctx)
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


class _DirectionalConvictionAccessor:
    """Translates ScoringContext into the feature_field names this pillar reads.

    Subscore configs reference these attributes by name. Each property is
    ``None`` when the underlying inputs are missing so weight redistribution
    and the v4 min-subscore rule work correctly.
    """

    def __init__(self, ctx: ScoringContext) -> None:
        self._ctx = ctx

    def __getattr__(self, name: str) -> Any:
        # Fallback to the raw ScoringContext for fields we haven't derived.
        return getattr(self._ctx, name, None)

    @property
    def stage_2_trend_score(self) -> Optional[float]:
        return _stage_2_trend_template(self._ctx)

    @property
    def rs_20d(self) -> Optional[float]:
        """Relative strength, direction-aware.

        CALL positions reward HIGH relative strength (leadership).
        PUT positions reward LOW relative strength (weakness); we negate
        so the monotonic-upward breakpoint curve rewards weakness on
        puts the same way it rewards strength on calls.
        """
        raw = self._ctx.rs_20d
        if raw is None:
            return None
        return raw if self._ctx.option_type == "CALL" else -raw

    @property
    def adx_directional_score(self) -> Optional[float]:
        return _adx_directional_agreement(self._ctx)

    @property
    def breakout_proximity_pct(self) -> Optional[float]:
        return _breakout_proximity(self._ctx)

    @property
    def obv_confirmation_score(self) -> Optional[float]:
        return _obv_confirmation(self._ctx)

    @property
    def sector_rs_20d(self) -> Optional[float]:
        """Sector relative strength vs SPY, direction-aware.

        CALL positions reward sector outperformance; PUT positions reward
        sector underperformance. Negation flips the monotonic curve so
        the same breakpoints serve both directions.
        """
        raw = self._ctx.sector_rs_20d
        if raw is None:
            return None
        return raw if self._ctx.option_type == "CALL" else -raw


# ============================================================================
# Derived feature computations
# ============================================================================


def _stage_2_trend_template(ctx: ScoringContext) -> Optional[float]:
    """Minervini 7-criteria Stage 2 uptrend template → 0-100 score.

    Criteria (bullish orientation; PUTs invert comparisons):
    1. close > sma50
    2. close > ma_150
    3. close > ma_200
    4. sma50 > ma_150
    5. ma_150 > ma_200
    6. close is at least 30% above low_52w
    7. close is within 25% of high_52w

    Score = (criteria_met / 7) * 100. Returns None when fewer than 5 of the
    7 criteria can be evaluated (insufficient data).
    """
    close = ctx.close
    if close is None:
        return None

    bullish = ctx.option_type == "CALL"
    checks: list[bool] = []

    def _cmp(lhs: Optional[float], rhs: Optional[float]) -> Optional[bool]:
        if lhs is None or rhs is None:
            return None
        return lhs > rhs if bullish else lhs < rhs

    for pair in (
        (close, ctx.sma50),
        (close, ctx.ma_150),
        (close, ctx.ma_200),
        (ctx.sma50, ctx.ma_150),
        (ctx.ma_150, ctx.ma_200),
    ):
        result = _cmp(pair[0], pair[1])
        if result is not None:
            checks.append(result)

    if ctx.low_52w is not None and ctx.high_52w is not None:
        # Criterion 6: close is 30%+ above 52w low (bullish) / below 52w high (bearish).
        if bullish:
            checks.append(close >= ctx.low_52w * 1.30)
        else:
            checks.append(close <= ctx.high_52w * 0.70)

        # Criterion 7: close is within 25% of 52w high (bullish) / low (bearish).
        if bullish and ctx.high_52w > 0:
            checks.append((ctx.high_52w - close) / ctx.high_52w <= 0.25)
        elif not bullish and ctx.low_52w > 0:
            checks.append((close - ctx.low_52w) / ctx.low_52w <= 0.25)

    if len(checks) < 5:
        return None

    met = sum(1 for c in checks if c)
    return (met / len(checks)) * 100.0


def _adx_directional_agreement(ctx: ScoringContext) -> Optional[float]:
    """Combine ADX magnitude with ±DI sign agreement → 0-100 score.

    Strong ADX (>25) combined with +DI > -DI for CALLs (or -DI > +DI for
    PUTs) gives high scores. ADX > 40 caps, weak trends (<15) floor.
    Disagreement penalises regardless of ADX magnitude.
    """
    adx = ctx.adx_14
    plus_di = ctx.plus_di
    minus_di = ctx.minus_di
    if adx is None or plus_di is None or minus_di is None:
        return None

    # Map ADX to a 0-100 base score: weak trend ~15 → 30, strong trend ~40 → 85.
    adx_clamped = max(0.0, min(50.0, float(adx)))
    base = 30.0 + (adx_clamped - 15.0) * (85.0 - 30.0) / (40.0 - 15.0)
    base = max(0.0, min(100.0, base))

    bullish = ctx.option_type == "CALL"
    dominant_di = plus_di if bullish else minus_di
    opposing_di = minus_di if bullish else plus_di
    di_diff = dominant_di - opposing_di  # positive when agreement

    # Agreement bonus: +15 when dominant DI leads by 10+, -25 penalty when losing.
    if di_diff >= 10:
        bonus = 15.0
    elif di_diff >= 0:
        bonus = 5.0
    elif di_diff >= -10:
        bonus = -15.0
    else:
        bonus = -25.0

    return max(0.0, min(100.0, base + bonus))


def _breakout_proximity(ctx: ScoringContext) -> Optional[float]:
    """Distance from the option-direction pivot (52w high for CALL, low for PUT).

    Returned as a percent of underlying price. Positive = underlying is
    above the pivot (post-breakout), negative = below (pre-breakout). The
    policy breakpoints should peak at small positive values (just-broke-out)
    and decay in both directions.
    """
    close = ctx.close
    if close is None or close <= 0:
        return None
    if ctx.option_type == "CALL":
        pivot = ctx.high_52w
    else:
        pivot = ctx.low_52w
    if pivot is None:
        return None
    # Return signed distance in %; positive = close above pivot.
    return ((close - pivot) / close) * 100.0


def _obv_confirmation(ctx: ScoringContext) -> Optional[float]:
    """OBV trend vs option direction → 0-100.

    RISING + CALL or FALLING + PUT → 90 (strong confirmation).
    FLAT → 50.
    Disagreement → 10.
    """
    obv = ctx.obv_trend
    if obv is None:
        return None

    bullish = ctx.option_type == "CALL"
    obv_up = obv == "RISING"
    obv_down = obv == "FALLING"

    if obv_up and bullish:
        return 90.0
    if obv_down and not bullish:
        return 90.0
    if obv == "FLAT":
        return 50.0
    return 10.0


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

    if count_available(subscores) < 6:
        tags.append("PARTIAL_COVERAGE")

    stage_2 = _stage_2_trend_template(ctx)
    if stage_2 is not None and stage_2 >= 70:
        tags.append("STAGE_2_TREND")

    # RS_LEADER / RS_LAGGARD are framed from the option's direction:
    # a CALL is a "leader" when rs_20d is strongly positive; a PUT is a
    # "leader" (for puts) when rs_20d is strongly negative.
    if ctx.rs_20d is not None:
        directional_rs = ctx.rs_20d if ctx.option_type == "CALL" else -ctx.rs_20d
        if directional_rs >= 5.0:
            tags.append("RS_LEADER")
        elif directional_rs <= -5.0:
            tags.append("RS_LAGGARD")

    if (
        ctx.adx_14 is not None
        and ctx.plus_di is not None
        and ctx.minus_di is not None
        and ctx.adx_14 >= 25
    ):
        bullish = ctx.option_type == "CALL"
        agreed = (ctx.plus_di > ctx.minus_di) if bullish else (ctx.minus_di > ctx.plus_di)
        if agreed:
            tags.append("ADX_DIRECTIONAL_AGREE")

    proximity = _breakout_proximity(ctx)
    if proximity is not None and -2.0 <= proximity <= 5.0:
        tags.append("NEAR_BREAKOUT")

    obv_score = _obv_confirmation(ctx)
    if obv_score is not None and obv_score >= 80:
        tags.append("VOLUME_CONFIRMED")

    if ctx.sector_rs_20d is not None:
        directional_sector_rs = (
            ctx.sector_rs_20d if ctx.option_type == "CALL" else -ctx.sector_rs_20d
        )
        if directional_sector_rs >= 2.0:
            tags.append("SECTOR_LEADER")

    return tags
