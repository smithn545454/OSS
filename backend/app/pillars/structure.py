"""Structure Pillar Scoring (Section 14.4).

Assesses tradability, liquidity, and execution quality of the contract.

Subscores:
1. Spread (30%) - Bid-ask spread tightness
2. Open Interest (25%) - Contract liquidity depth
3. Volume (20%) - Daily trading activity
4. Theta Burden (15%) - Daily decay cost
5. Liquidity Trend (10%) - OI change direction
"""

from __future__ import annotations

from typing import Optional

from app.core.schemas import (
    PillarId,
    StructurePillarConfig,
)
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.utils import (
    clamp_score,
    map_spread_score,
    map_open_interest_score,
    map_volume_score,
    map_theta_burden_score,
    map_liquidity_trend_score,
)


def compute_spread_subscore(ctx: ScoringContext) -> Subscore:
    """Compute spread subscore (0-100).
    
    Per Section 14.4:
    Tighter spread = better (lower execution cost).
    
    Mapping:
    - <= 2%: 95
    - 2-4%: Linear 80-95
    - 4-6%: Linear 65-80
    - 6-8%: Linear 50-65
    - 8-10%: Linear 35-50
    - > 10%: 20
    
    Args:
        ctx: ScoringContext with spread data
        
    Returns:
        Spread Subscore
    """
    spread_pct = ctx.spread_pct
    
    score = map_spread_score(spread_pct)
    
    return Subscore(
        name="spread",
        raw_value={
            "spread_pct": spread_pct,
            "mid": ctx.mid,
        },
        score=clamp_score(score),
        weight=0.30,
    )


def compute_open_interest_subscore(ctx: ScoringContext) -> Subscore:
    """Compute open interest subscore (0-100).
    
    Per Section 14.4:
    Higher OI = better liquidity and easier position management.
    
    Mapping:
    - >= 2000: 95
    - 1000-2000: Linear 80-95
    - 500-1000: Linear 65-80
    - 300-500: Linear 50-65
    - 200-300: Linear 35-50
    - < 200: 20
    
    Args:
        ctx: ScoringContext with OI data
        
    Returns:
        Open Interest Subscore
    """
    open_interest = ctx.open_interest
    
    score = map_open_interest_score(open_interest)
    
    return Subscore(
        name="open_interest",
        raw_value={
            "open_interest": open_interest,
        },
        score=clamp_score(score),
        weight=0.25,
    )


def compute_volume_subscore(ctx: ScoringContext) -> Subscore:
    """Compute volume subscore (0-100).
    
    Per Section 14.4:
    Higher daily volume = more active trading and better fills.
    
    Mapping:
    - >= 500: 90
    - 300-500: Linear 75-90
    - 150-300: Linear 60-75
    - 75-150: Linear 45-60
    - 50-75: Linear 35-45
    - < 50: 25
    
    Args:
        ctx: ScoringContext with volume data
        
    Returns:
        Volume Subscore
    """
    volume = ctx.volume
    
    score = map_volume_score(volume)
    
    return Subscore(
        name="volume",
        raw_value={
            "volume": volume,
        },
        score=clamp_score(score),
        weight=0.20,
    )


def compute_theta_burden_subscore(ctx: ScoringContext) -> Subscore:
    """Compute theta burden subscore (0-100).
    
    Per Section 14.4:
    Lower theta % per day = better (less daily decay cost).
    
    Mapping:
    - <= 0.5%: 90
    - 0.5-1.0%: Linear 75-90
    - 1.0-1.5%: Linear 60-75
    - 1.5-2.0%: Linear 45-60
    - 2.0-3.0%: Linear 30-45
    - > 3.0%: 20
    
    Args:
        ctx: ScoringContext with theta data
        
    Returns:
        Theta Burden Subscore
    """
    theta_pct = ctx.theta_pct
    
    score = map_theta_burden_score(theta_pct)
    
    return Subscore(
        name="theta_burden",
        raw_value={
            "theta_pct": theta_pct,
        },
        score=clamp_score(score),
        weight=0.15,
    )


def compute_liquidity_trend_subscore(ctx: ScoringContext) -> Subscore:
    """Compute liquidity trend subscore (0-100).
    
    Per Section 14.4:
    Growing OI = positive signal (more interest in contract).
    
    Mapping:
    - >= +10%: 85 (growing interest)
    - 0% to +10%: Linear 65-85
    - -10% to 0%: Linear 50-65
    - -20% to -10%: Linear 35-50
    - < -20%: 25 (liquidity deteriorating)
    
    Args:
        ctx: ScoringContext with OI change data
        
    Returns:
        Liquidity Trend Subscore
    """
    oi_5d_change_pct = ctx.oi_5d_change_pct
    
    score = map_liquidity_trend_score(oi_5d_change_pct)
    
    return Subscore(
        name="liquidity_trend",
        raw_value={
            "oi_5d_change_pct": oi_5d_change_pct,
        },
        score=clamp_score(score),
        weight=0.10,
    )


def generate_structure_tags(subscores: list[Subscore], ctx: ScoringContext) -> list[str]:
    """Generate descriptive tags for structure pillar.
    
    Tags indicate notable structural conditions.
    
    Args:
        subscores: List of computed subscores
        ctx: ScoringContext
        
    Returns:
        List of tag strings
    """
    tags = []
    
    for subscore in subscores:
        if subscore.name == "spread":
            if subscore.score >= 85:
                tags.append("TIGHT_SPREAD")
            elif subscore.score <= 30:
                tags.append("WIDE_SPREAD")
        
        if subscore.name == "open_interest":
            if subscore.score >= 85:
                tags.append("HIGH_LIQUIDITY")
            elif subscore.score <= 30:
                tags.append("LOW_LIQUIDITY")
        
        if subscore.name == "volume":
            if subscore.score >= 80:
                tags.append("HIGH_VOLUME")
            elif subscore.score <= 30:
                tags.append("LOW_VOLUME")
        
        if subscore.name == "theta_burden":
            if subscore.score >= 80:
                tags.append("LOW_THETA_BURDEN")
            elif subscore.score <= 30:
                tags.append("HIGH_THETA_BURDEN")
        
        if subscore.name == "liquidity_trend":
            if subscore.score >= 75:
                tags.append("OI_GROWING")
            elif subscore.score <= 35:
                tags.append("OI_DECLINING")
    
    return tags


def compute_structure_pillar(
    ctx: ScoringContext,
    config: Optional[StructurePillarConfig] = None,
) -> PillarResult:
    """Compute the complete Structure Pillar score.
    
    Combines all 5 subscores with configurable weights to produce
    a final score from 0-100.
    
    Args:
        ctx: ScoringContext with all features
        config: Optional StructurePillarConfig (uses defaults if None)
        
    Returns:
        PillarResult with score, subscores, and tags
    """
    if config is None:
        config = StructurePillarConfig()
    
    # Compute all subscores
    subscores = [
        compute_spread_subscore(ctx),
        compute_open_interest_subscore(ctx),
        compute_volume_subscore(ctx),
        compute_theta_burden_subscore(ctx),
        compute_liquidity_trend_subscore(ctx),
    ]
    
    # Update weights from config
    for subscore in subscores:
        if subscore.name == "spread":
            subscore.weight = config.spread_weight
        elif subscore.name == "open_interest":
            subscore.weight = config.open_interest_weight
        elif subscore.name == "volume":
            subscore.weight = config.volume_weight
        elif subscore.name == "theta_burden":
            subscore.weight = config.theta_burden_weight
        elif subscore.name == "liquidity_trend":
            subscore.weight = config.liquidity_trend_weight
    
    # Calculate final weighted score
    final_score = sum(s.weighted_contribution for s in subscores)
    
    # Generate tags
    tags = generate_structure_tags(subscores, ctx)
    
    return PillarResult(
        pillar_id=PillarId.STRUCTURE,
        evaluation_id=ctx.evaluation_id,
        score=clamp_score(final_score),
        subscores=subscores,
        tags=tags,
    )
