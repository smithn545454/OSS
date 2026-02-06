"""Directional Pillar Scoring (Section 14.2).

Measures how likely the underlying is to move in the option's direction
within the DTE window.

Subscores:
1. Trend Alignment (30%) - close vs SMA20 vs SMA50
2. Momentum (25%) - DTE-adjusted blended returns
3. Signal Confirmation (20%) - Scanner triggers matching contract side
4. Relative Strength (15%) - Performance vs SPY
5. Catalyst (10%) - Earnings proximity
"""

from __future__ import annotations

from typing import Optional

from app.core.schemas import (
    PillarId,
    DirectionalPillarConfig,
    ScannerType,
)
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.utils import (
    blend_values,
    clamp_score,
    map_momentum_score,
    map_rs_score,
)


def compute_trend_alignment_subscore(ctx: ScoringContext) -> Subscore:
    """Compute trend alignment subscore (0-100).
    
    Per Section 14.2:
    - Strong aligned trend (close > sma20 > sma50): 90 for CALL, 10 for PUT
    - Partial alignment: 65/35
    - Neutral: 50/50
    - Partial bearish: 35/65
    - Strong bearish (close < sma20 < sma50): 10 for CALL, 90 for PUT
    
    Args:
        ctx: ScoringContext with features
        
    Returns:
        Trend alignment Subscore
    """
    close = ctx.close
    sma20 = ctx.sma20
    sma50 = ctx.sma50
    is_call = ctx.is_call
    
    # Default to neutral if missing data
    if close is None or sma20 is None or sma50 is None:
        return Subscore(
            name="trend_alignment",
            raw_value={"close": close, "sma20": sma20, "sma50": sma50},
            score=50.0,
            weight=0.30,
        )
    
    # Determine trend state
    if close > sma20 > sma50:
        # Strong bullish trend
        base_score = 90.0
    elif close > sma20 and sma20 <= sma50:
        # Partial bullish (close above sma20 but sma20 not above sma50)
        base_score = 65.0
    elif close < sma20 < sma50:
        # Strong bearish trend
        base_score = 10.0
    elif close < sma20 and sma20 >= sma50:
        # Partial bearish
        base_score = 35.0
    else:
        # Neutral (close between SMAs or SMAs crossed)
        base_score = 50.0
    
    # For PUT contracts, invert the score (bearish = good)
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
        weight=0.30,
    )


def compute_momentum_subscore(ctx: ScoringContext, config: DirectionalPillarConfig) -> Subscore:
    """Compute DTE-adjusted momentum subscore (0-100).
    
    Per Section 14.2:
    - DTE Bucket A (7-21): 70% return_5d, 30% return_20d
    - DTE Bucket B (22-45): 50% each
    - DTE Bucket C (46-75): 30% return_5d, 70% return_20d
    - DTE Bucket D (76-120): 20% return_5d, 80% return_20d
    
    Args:
        ctx: ScoringContext with features
        config: DirectionalPillarConfig with blend weights
        
    Returns:
        Momentum Subscore
    """
    return_5d = ctx.return_5d
    return_20d = ctx.return_20d
    
    # Get the blend weight for 5d return based on DTE bucket
    blend_weight_5d = {
        "A": config.momentum_blend_bucket_a,
        "B": config.momentum_blend_bucket_b,
        "C": config.momentum_blend_bucket_c,
        "D": config.momentum_blend_bucket_d,
    }.get(ctx.dte_bucket, 0.50)
    
    # Blend the returns
    blended_return = blend_values(return_5d, return_20d, blend_weight_5d)
    
    if blended_return is None:
        return Subscore(
            name="momentum",
            raw_value={"return_5d": return_5d, "return_20d": return_20d},
            score=50.0,
            weight=0.25,
        )
    
    # Map blended return to score (direction-aware)
    score = map_momentum_score(blended_return, ctx.is_call)
    
    return Subscore(
        name="momentum",
        raw_value={
            "return_5d": return_5d,
            "return_20d": return_20d,
            "blended_return": blended_return,
            "dte_bucket": ctx.dte_bucket,
            "blend_weight_5d": blend_weight_5d,
        },
        score=clamp_score(score),
        weight=0.25,
    )


def compute_signal_confirmation_subscore(ctx: ScoringContext) -> Subscore:
    """Compute signal confirmation subscore (0-100).
    
    Per Section 14.2:
    - Breakout + CALL: 85
    - Breakdown + PUT: 85
    - Compression→Expansion Up + CALL: 75
    - Compression→Expansion Down + PUT: 75
    - Unusual Volume (matching direction): 65
    - No matching signal: 45
    - Conflicting signal: 25
    
    Args:
        ctx: ScoringContext with scanner triggers
        
    Returns:
        Signal confirmation Subscore
    """
    triggers = ctx.scanner_triggers
    direction_hint = ctx.direction_hint
    is_call = ctx.is_call
    
    # No scanner triggers
    if not triggers:
        return Subscore(
            name="signal_confirmation",
            raw_value={"triggers": triggers, "direction_hint": direction_hint},
            score=45.0,
            weight=0.20,
        )
    
    # Check for matching signals
    has_breakout = ScannerType.BREAKOUT in triggers or "BREAKOUT" in triggers
    has_breakdown = ScannerType.BREAKDOWN in triggers or "BREAKDOWN" in triggers
    has_compression = ScannerType.COMPRESSION_EXPANSION in triggers or "COMPRESSION_EXPANSION" in triggers
    has_unusual_vol = ScannerType.UNUSUAL_VOLUME in triggers or "UNUSUAL_VOLUME" in triggers
    has_cheap = ScannerType.CHEAP_OPTIONS in triggers or "CHEAP_OPTIONS" in triggers
    
    # Calculate score based on matching signals
    score = 45.0  # Base: no matching signal
    
    # Breakout/Breakdown matching
    if has_breakout and is_call:
        score = 85.0
    elif has_breakdown and not is_call:
        score = 85.0
    # Conflicting breakout/breakdown
    elif has_breakout and not is_call:
        score = 25.0
    elif has_breakdown and is_call:
        score = 25.0
    # Compression expansion
    elif has_compression:
        # Check direction hint for compression direction
        if direction_hint == "CALL" and is_call:
            score = 75.0
        elif direction_hint == "PUT" and not is_call:
            score = 75.0
        elif direction_hint == "NONE":
            score = 55.0  # Neutral compression
        else:
            score = 35.0  # Conflicting direction
    # Unusual volume with direction hint
    elif has_unusual_vol:
        if direction_hint == "CALL" and is_call:
            score = 65.0
        elif direction_hint == "PUT" and not is_call:
            score = 65.0
        elif direction_hint == "NONE":
            score = 55.0
        else:
            score = 40.0  # Conflicting direction
    # Cheap options scanner doesn't have direction
    elif has_cheap:
        score = 50.0
    
    return Subscore(
        name="signal_confirmation",
        raw_value={
            "triggers": [str(t) for t in triggers],
            "direction_hint": direction_hint,
            "is_call": is_call,
        },
        score=clamp_score(score),
        weight=0.20,
    )


def compute_relative_strength_subscore(ctx: ScoringContext) -> Subscore:
    """Compute relative strength subscore (0-100).
    
    Per Section 14.2:
    Maps rs_20d (performance vs SPY) to score.
    For PUT, inverts the RS value before mapping.
    
    Args:
        ctx: ScoringContext with RS features
        
    Returns:
        Relative strength Subscore
    """
    rs_20d = ctx.rs_20d
    
    if rs_20d is None:
        return Subscore(
            name="relative_strength",
            raw_value={"rs_5d": ctx.rs_5d, "rs_20d": rs_20d},
            score=50.0,
            weight=0.15,
        )
    
    score = map_rs_score(rs_20d, ctx.is_call)
    
    return Subscore(
        name="relative_strength",
        raw_value={
            "rs_5d": ctx.rs_5d,
            "rs_20d": rs_20d,
        },
        score=clamp_score(score),
        weight=0.15,
    )


def compute_catalyst_subscore(ctx: ScoringContext) -> Subscore:
    """Compute catalyst subscore (0-100).
    
    Per Section 14.2:
    - Earnings within 7 days: 70
    - Earnings 8-14 days: 60
    - Earnings 15-30 days: 55
    - Recent SEC filing (10 days): 60
    - No catalyst: 50
    
    Args:
        ctx: ScoringContext with catalyst features
        
    Returns:
        Catalyst Subscore
    """
    days_to_earnings = ctx.days_to_earnings
    recent_sec_filing = ctx.recent_sec_filing
    
    # Base score with no catalyst
    score = 50.0
    
    # Earnings proximity scoring
    if days_to_earnings is not None:
        if days_to_earnings <= 7:
            score = 70.0  # High event risk/opportunity
        elif days_to_earnings <= 14:
            score = 60.0
        elif days_to_earnings <= 30:
            score = 55.0
    
    # SEC filing can add to score (take max if both present)
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
        if subscore.name == "trend_alignment" and subscore.score >= 80:
            tags.append("STRONG_TREND")
        elif subscore.name == "trend_alignment" and subscore.score <= 20:
            tags.append("COUNTER_TREND")
        
        if subscore.name == "momentum" and subscore.score >= 80:
            tags.append("HIGH_MOMENTUM")
        elif subscore.name == "momentum" and subscore.score <= 20:
            tags.append("NEGATIVE_MOMENTUM")
        
        if subscore.name == "signal_confirmation" and subscore.score >= 80:
            if ctx.is_call:
                tags.append("BREAKOUT_CONFIRMED")
            else:
                tags.append("BREAKDOWN_CONFIRMED")
        
        if subscore.name == "relative_strength" and subscore.score >= 80:
            tags.append("SECTOR_OUTPERFORM")
        elif subscore.name == "relative_strength" and subscore.score <= 20:
            tags.append("SECTOR_UNDERPERFORM")
        
        if subscore.name == "catalyst":
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
    
    Combines all 5 subscores with configurable weights to produce
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
        compute_signal_confirmation_subscore(ctx),
        compute_relative_strength_subscore(ctx),
        compute_catalyst_subscore(ctx),
    ]
    
    # Update weights from config
    for subscore in subscores:
        if subscore.name == "trend_alignment":
            subscore.weight = config.trend_alignment_weight
        elif subscore.name == "momentum":
            subscore.weight = config.momentum_weight
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
