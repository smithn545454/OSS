"""Directional Pillar Scoring (Section 14.2).

Measures how likely the underlying is to move in the option's direction
within the DTE window.

Subscores:
1. Trend Alignment (25%) - EMA 9/21/50/200 stack classification (SMA fallback)
2. Momentum (25%) - RSI(14) + MACD histogram + DTE-blended returns composite
3. Trend Strength (15%) - ADX(14) with +DI/-DI directional confirmation
4. Signal Confirmation (15%) - Scanner triggers matching contract side
5. Relative Strength (10%) - Performance vs SPY + OBV volume confirmation
6. Catalyst (10%) - Earnings proximity
"""

from __future__ import annotations

from typing import Optional

from app.core.schemas import (
    DirectionalPillarConfig,
    PillarId,
    ScannerType,
)
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.utils import (
    blend_values,
    clamp_score,
    linear_interpolate,
    map_momentum_score,
    map_rs_score,
)

# ---------------------------------------------------------------------------
# EMA alignment score mapping
# ---------------------------------------------------------------------------

_EMA_SCORES_CALL: dict[str, float] = {
    "BULLISH_STACK": 92.0,
    "ABOVE_ALL": 75.0,
    "BELOW_ALL": 25.0,
    "BEARISH_STACK": 8.0,
}


def compute_trend_alignment_subscore(ctx: ScoringContext) -> Subscore:
    """Compute trend alignment subscore (0-100).

    Uses EMA 9/21/50/200 stack classification when available,
    falls back to SMA20/SMA50 5-tier logic for backward compatibility.

    Args:
        ctx: ScoringContext with features

    Returns:
        Trend alignment Subscore
    """
    close = ctx.close
    is_call = ctx.is_call

    # Prefer EMA alignment when available
    if ctx.ema_alignment is not None and close is not None:
        alignment = ctx.ema_alignment

        if alignment in _EMA_SCORES_CALL:
            base_score = _EMA_SCORES_CALL[alignment]
        elif alignment == "MIXED":
            # Distinguish MIXED by price position relative to EMA50
            ema_ref = ctx.ema_50 if ctx.ema_50 is not None else ctx.sma50
            if ema_ref is not None:
                base_score = 58.0 if close > ema_ref else 42.0
            else:
                base_score = 50.0
        else:
            base_score = 50.0

        if not is_call:
            base_score = 100.0 - base_score

        return Subscore(
            name="trend_alignment",
            raw_value={
                "ema_alignment": alignment,
                "ema_9": ctx.ema_9,
                "ema_21": ctx.ema_21,
                "ema_50": ctx.ema_50,
                "ema_200": ctx.ema_200,
                "close": close,
            },
            score=clamp_score(base_score),
            weight=0.25,
        )

    # Fallback: SMA20/SMA50 5-tier logic
    sma20 = ctx.sma20
    sma50 = ctx.sma50

    if close is None or sma20 is None or sma50 is None:
        return Subscore(
            name="trend_alignment",
            raw_value={"close": close, "sma20": sma20, "sma50": sma50},
            score=50.0,
            weight=0.25,
        )

    if close > sma20 > sma50:
        base_score = 90.0
    elif close > sma20 and sma20 <= sma50:
        base_score = 65.0
    elif close < sma20 < sma50:
        base_score = 10.0
    elif close < sma20 and sma20 >= sma50:
        base_score = 35.0
    else:
        base_score = 50.0

    if not is_call:
        base_score = 100.0 - base_score

    return Subscore(
        name="trend_alignment",
        raw_value={
            "close": close,
            "sma20": sma20,
            "sma50": sma50,
            "aligned_bullish": ctx.trend_aligned_bullish,
            "aligned_bearish": ctx.trend_aligned_bearish,
        },
        score=clamp_score(base_score),
        weight=0.25,
    )


# ---------------------------------------------------------------------------
# RSI score mapping breakpoints
# ---------------------------------------------------------------------------

# For CALL: healthy bullish momentum (55-65) scores highest,
# overbought (>70) fades, oversold (<30) is bearish.
_RSI_BREAKPOINTS_CALL: list[tuple[float, float]] = [
    (0.0, 10.0),
    (20.0, 15.0),
    (30.0, 30.0),
    (40.0, 50.0),
    (50.0, 65.0),
    (55.0, 80.0),
    (60.0, 85.0),
    (65.0, 80.0),
    (70.0, 65.0),
    (80.0, 55.0),
    (100.0, 45.0),
]

# For PUT: oversold (RSI < 30) scores highest (strong bearish),
# overbought (> 70) is low (against the trend).
_RSI_BREAKPOINTS_PUT: list[tuple[float, float]] = [
    (0.0, 90.0),
    (20.0, 85.0),
    (30.0, 75.0),
    (40.0, 55.0),
    (50.0, 40.0),
    (55.0, 30.0),
    (60.0, 20.0),
    (65.0, 25.0),
    (70.0, 35.0),
    (80.0, 45.0),
    (100.0, 50.0),
]


def compute_momentum_subscore(
    ctx: ScoringContext, config: DirectionalPillarConfig
) -> Subscore:
    """Compute momentum subscore (0-100).

    Composite of RSI(14), MACD histogram, and DTE-blended returns.
    When RSI or MACD are unavailable, their blend weight redistributes
    to the returns component.

    Args:
        ctx: ScoringContext with features
        config: DirectionalPillarConfig with blend weights

    Returns:
        Momentum Subscore
    """
    return_5d = ctx.return_5d
    return_20d = ctx.return_20d
    rsi = ctx.rsi_14
    macd_hist = ctx.macd_histogram
    is_call = ctx.is_call

    # --- Returns component (always available if returns exist) ---
    blend_weight_5d = {
        "A": config.momentum_blend_bucket_a,
        "B": config.momentum_blend_bucket_b,
        "C": config.momentum_blend_bucket_c,
        "D": config.momentum_blend_bucket_d,
    }.get(ctx.dte_bucket, 0.50)

    blended_return = blend_values(return_5d, return_20d, blend_weight_5d)
    if blended_return is not None:
        returns_score = map_momentum_score(blended_return, is_call)
    else:
        returns_score = 50.0

    # --- RSI component ---
    rsi_score: Optional[float] = None
    if rsi is not None:
        breakpoints = _RSI_BREAKPOINTS_CALL if is_call else _RSI_BREAKPOINTS_PUT
        rsi_score = linear_interpolate(rsi, breakpoints)

    # --- MACD component ---
    macd_score: Optional[float] = None
    if macd_hist is not None:
        if macd_hist > 0:
            macd_score = 80.0 if is_call else 25.0
        elif macd_hist < 0:
            macd_score = 25.0 if is_call else 80.0
        else:
            macd_score = 50.0

    # --- Blend with weight redistribution for missing components ---
    rsi_w = config.momentum_rsi_blend if rsi_score is not None else 0.0
    macd_w = config.momentum_macd_blend if macd_score is not None else 0.0
    returns_w = config.momentum_returns_blend

    total_w = rsi_w + macd_w + returns_w
    if total_w == 0:
        score = 50.0
    else:
        score = (
            (rsi_score or 0.0) * rsi_w
            + (macd_score or 0.0) * macd_w
            + returns_score * returns_w
        ) / total_w * 1.0
        # Normalize: since weights may not sum to 1.0 due to redistribution
        # The division by total_w already handles redistribution

    return Subscore(
        name="momentum",
        raw_value={
            "rsi_14": rsi,
            "rsi_score": rsi_score,
            "macd_histogram": macd_hist,
            "macd_score": macd_score,
            "return_5d": return_5d,
            "return_20d": return_20d,
            "blended_return": blended_return,
            "returns_score": returns_score,
            "dte_bucket": ctx.dte_bucket,
        },
        score=clamp_score(score),
        weight=0.25,
    )


def compute_trend_strength_subscore(ctx: ScoringContext) -> Subscore:
    """Compute trend strength subscore (0-100) using ADX(14).

    ADX measures trend strength (not direction). +DI vs -DI determines
    whether the trend is bullish or bearish.

    Args:
        ctx: ScoringContext with ADX features

    Returns:
        Trend strength Subscore
    """
    adx = ctx.adx_14
    plus_di = ctx.plus_di
    minus_di = ctx.minus_di
    is_call = ctx.is_call

    if adx is None or plus_di is None or minus_di is None:
        return Subscore(
            name="trend_strength",
            raw_value={"adx_14": adx, "plus_di": plus_di, "minus_di": minus_di},
            score=50.0,
            weight=0.15,
        )

    # Determine trend direction from DI
    bullish_trend = plus_di > minus_di

    # Map ADX strength to score
    if adx >= 40:
        strength_score = 92.0
    elif adx >= 25:
        # Linear interpolation between 25-40 → 80-92
        strength_score = 80.0 + (adx - 25.0) / 15.0 * 12.0
    elif adx >= 15:
        # Weak trend: 15-25 → 55-80
        strength_score = 55.0 + (adx - 15.0) / 10.0 * 25.0
    else:
        # No meaningful trend
        strength_score = 50.0

    # Direction alignment: if trend direction matches option type, keep score.
    # If trend direction conflicts, invert.
    if is_call:
        if not bullish_trend and adx >= 15:
            strength_score = 100.0 - strength_score
    else:
        if bullish_trend and adx >= 15:
            strength_score = 100.0 - strength_score

    return Subscore(
        name="trend_strength",
        raw_value={
            "adx_14": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "bullish_trend": bullish_trend,
        },
        score=clamp_score(strength_score),
        weight=0.15,
    )


def compute_signal_confirmation_subscore(ctx: ScoringContext) -> Subscore:
    """Compute signal confirmation subscore (0-100).

    Maps scanner triggers and direction hints to a directional confidence score.

    Args:
        ctx: ScoringContext with scanner triggers

    Returns:
        Signal confirmation Subscore
    """
    triggers = ctx.scanner_triggers
    direction_hint = ctx.direction_hint
    is_call = ctx.is_call

    if not triggers:
        return Subscore(
            name="signal_confirmation",
            raw_value={"triggers": triggers, "direction_hint": direction_hint},
            score=45.0,
            weight=0.15,
        )

    has_breakout = ScannerType.BREAKOUT in triggers or "BREAKOUT" in triggers
    has_breakdown = ScannerType.BREAKDOWN in triggers or "BREAKDOWN" in triggers
    has_compression = (
        ScannerType.COMPRESSION_EXPANSION in triggers
        or "COMPRESSION_EXPANSION" in triggers
    )
    has_unusual_vol = (
        ScannerType.UNUSUAL_VOLUME in triggers or "UNUSUAL_VOLUME" in triggers
    )
    has_cheap = ScannerType.CHEAP_OPTIONS in triggers or "CHEAP_OPTIONS" in triggers

    score = 45.0

    if has_breakout and is_call:
        score = 85.0
    elif has_breakdown and not is_call:
        score = 85.0
    elif has_breakout and not is_call:
        score = 25.0
    elif has_breakdown and is_call:
        score = 25.0
    elif has_compression:
        if direction_hint == "CALL" and is_call:
            score = 75.0
        elif direction_hint == "PUT" and not is_call:
            score = 75.0
        elif direction_hint == "NONE":
            score = 55.0
        else:
            score = 35.0
    elif has_unusual_vol:
        if direction_hint == "CALL" and is_call:
            score = 65.0
        elif direction_hint == "PUT" and not is_call:
            score = 65.0
        elif direction_hint == "NONE":
            score = 55.0
        else:
            score = 40.0
    elif has_cheap:
        score = 70.0

    return Subscore(
        name="signal_confirmation",
        raw_value={
            "triggers": [str(t) for t in triggers],
            "direction_hint": direction_hint,
            "is_call": is_call,
        },
        score=clamp_score(score),
        weight=0.15,
    )


def compute_relative_strength_subscore(
    ctx: ScoringContext, config: DirectionalPillarConfig
) -> Subscore:
    """Compute relative strength subscore (0-100).

    Blends RS performance vs SPY with OBV volume confirmation.
    When OBV is unavailable, full weight goes to RS performance.

    Args:
        ctx: ScoringContext with RS features
        config: DirectionalPillarConfig with RS blend weights

    Returns:
        Relative strength Subscore
    """
    rs_20d = ctx.rs_20d
    obv = ctx.obv_trend
    is_call = ctx.is_call

    # RS performance component
    if rs_20d is not None:
        rs_score = map_rs_score(rs_20d, is_call)
    else:
        rs_score = 50.0

    # OBV confirmation component
    obv_score: Optional[float] = None
    if obv is not None:
        if obv == "RISING":
            obv_score = 80.0 if is_call else 30.0
        elif obv == "FALLING":
            obv_score = 30.0 if is_call else 80.0
        else:  # FLAT
            obv_score = 50.0

    # Blend
    if obv_score is not None:
        score = rs_score * config.rs_performance_blend + obv_score * config.rs_obv_blend
    else:
        score = rs_score

    return Subscore(
        name="relative_strength",
        raw_value={
            "rs_5d": ctx.rs_5d,
            "rs_20d": rs_20d,
            "obv_trend": obv,
            "obv_score": obv_score,
        },
        score=clamp_score(score),
        weight=0.10,
    )


def compute_catalyst_subscore(ctx: ScoringContext) -> Subscore:
    """Compute catalyst subscore (0-100).

    Scores based on earnings proximity and SEC filing presence.

    Args:
        ctx: ScoringContext with catalyst features

    Returns:
        Catalyst Subscore
    """
    days_to_earnings = ctx.days_to_earnings
    recent_sec_filing = ctx.recent_sec_filing

    score = 50.0

    if days_to_earnings is not None:
        if days_to_earnings <= 7:
            score = 70.0
        elif days_to_earnings <= 14:
            score = 60.0
        elif days_to_earnings <= 30:
            score = 55.0

    if recent_sec_filing:
        score = max(score, 60.0)

    return Subscore(
        name="catalyst",
        raw_value={
            "days_to_earnings": days_to_earnings,
            "recent_sec_filing": recent_sec_filing,
        },
        score=clamp_score(score),
        weight=0.10,
    )


def generate_directional_tags(subscores: list[Subscore], ctx: ScoringContext) -> list[str]:
    """Generate descriptive tags for directional pillar.

    Tags indicate strong signals or notable conditions.

    Args:
        subscores: List of computed subscores
        ctx: ScoringContext

    Returns:
        List of tag strings
    """
    tags = []

    for subscore in subscores:
        if subscore.name == "trend_alignment":
            if subscore.score >= 80:
                tags.append("STRONG_TREND")
            elif subscore.score <= 20:
                tags.append("COUNTER_TREND")
            # EMA-specific tags from raw_value
            raw = subscore.raw_value
            if isinstance(raw, dict):
                alignment = raw.get("ema_alignment")
                if alignment == "BULLISH_STACK":
                    tags.append("EMA_BULLISH_STACK")
                elif alignment == "BEARISH_STACK":
                    tags.append("EMA_BEARISH_STACK")

        elif subscore.name == "momentum":
            if subscore.score >= 80:
                tags.append("HIGH_MOMENTUM")
            elif subscore.score <= 20:
                tags.append("NEGATIVE_MOMENTUM")
            raw = subscore.raw_value
            if isinstance(raw, dict):
                rsi = raw.get("rsi_14")
                if rsi is not None:
                    if rsi >= 70:
                        tags.append("RSI_OVERBOUGHT")
                    elif rsi <= 30:
                        tags.append("RSI_OVERSOLD")
                macd_hist = raw.get("macd_histogram")
                if macd_hist is not None:
                    if macd_hist > 0:
                        tags.append("MACD_BULLISH")
                    elif macd_hist < 0:
                        tags.append("MACD_BEARISH")

        elif subscore.name == "trend_strength":
            raw = subscore.raw_value
            if isinstance(raw, dict):
                adx = raw.get("adx_14")
                if adx is not None:
                    if adx >= 25:
                        tags.append("STRONG_ADX")
                    elif adx < 15:
                        tags.append("WEAK_TREND")

        elif subscore.name == "signal_confirmation":
            if subscore.score >= 80:
                if ctx.is_call:
                    tags.append("BREAKOUT_CONFIRMED")
                else:
                    tags.append("BREAKDOWN_CONFIRMED")

        elif subscore.name == "relative_strength":
            if subscore.score >= 80:
                tags.append("SECTOR_OUTPERFORM")
            elif subscore.score <= 20:
                tags.append("SECTOR_UNDERPERFORM")
            raw = subscore.raw_value
            if isinstance(raw, dict) and raw.get("obv_trend") is not None:
                obv_score = raw.get("obv_score")
                if obv_score is not None and obv_score >= 70:
                    tags.append("VOLUME_CONFIRMED")

        elif subscore.name == "catalyst":
            raw = subscore.raw_value
            if isinstance(raw, dict):
                dte = raw.get("days_to_earnings")
                if dte is not None and dte <= 7:
                    tags.append("EARNINGS_IMMINENT")

    return tags


def compute_directional_pillar(
    ctx: ScoringContext,
    config: Optional[DirectionalPillarConfig] = None,
) -> PillarResult:
    """Compute the complete Directional Pillar score.

    Combines all 6 subscores with configurable weights to produce
    a final score from 0-100.

    Args:
        ctx: ScoringContext with all features
        config: Optional DirectionalPillarConfig (uses defaults if None)

    Returns:
        PillarResult with score, subscores, and tags
    """
    if config is None:
        config = DirectionalPillarConfig()

    # Compute all subscores
    subscores = [
        compute_trend_alignment_subscore(ctx),
        compute_momentum_subscore(ctx, config),
        compute_trend_strength_subscore(ctx),
        compute_signal_confirmation_subscore(ctx),
        compute_relative_strength_subscore(ctx, config),
        compute_catalyst_subscore(ctx),
    ]

    # Update weights from config
    for subscore in subscores:
        if subscore.name == "trend_alignment":
            subscore.weight = config.trend_alignment_weight
        elif subscore.name == "momentum":
            subscore.weight = config.momentum_weight
        elif subscore.name == "trend_strength":
            subscore.weight = config.trend_strength_weight
        elif subscore.name == "signal_confirmation":
            subscore.weight = config.signal_confirmation_weight
        elif subscore.name == "relative_strength":
            subscore.weight = config.relative_strength_weight
        elif subscore.name == "catalyst":
            subscore.weight = config.catalyst_weight

    # Calculate final weighted score
    final_score = sum(s.weighted_contribution for s in subscores)

    # Generate tags
    tags = generate_directional_tags(subscores, ctx)

    return PillarResult(
        pillar_id=PillarId.DIRECTIONAL,
        evaluation_id=ctx.evaluation_id,
        score=clamp_score(final_score),
        subscores=subscores,
        tags=tags,
    )
