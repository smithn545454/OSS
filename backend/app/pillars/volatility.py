"""Volatility Pillar Scoring (Section 14.3).

Determines if the option is fairly priced relative to realized volatility
and market conditions.

Subscores:
1. IV vs RV (35%) - Is implied volatility cheap vs realized?
2. IV Percentile (25%) - Where is IV in its historical range?
3. IV Regime (20%) - What regime is IV in?
4. Theta-Adjusted Edge (20%) - Do expected gains outpace theta?
"""

from __future__ import annotations

from typing import Optional

from app.core.schemas import (
    PillarId,
    VolatilityPillarConfig,
    IVRegime,
)
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.utils import (
    clamp_score,
    map_iv_rv_ratio_score,
    map_iv_percentile_score,
    map_theta_adjusted_edge_score,
)


def compute_iv_vs_rv_subscore(ctx: ScoringContext) -> Subscore:
    """Compute IV vs RV subscore (0-100).
    
    Per Section 14.3:
    Lower IV/RV ratio = better (options are cheaper relative to realized movement).
    
    Args:
        ctx: ScoringContext with volatility features
        
    Returns:
        IV vs RV Subscore
    """
    iv_rv_ratio = ctx.iv_rv_ratio
    
    if iv_rv_ratio is None:
        # No historical RV data - use neutral score
        return Subscore(
            name="iv_vs_rv",
            raw_value={"iv": ctx.iv, "rv20": ctx.rv20, "iv_rv_ratio": None},
            score=50.0,
            weight=0.35,
        )
    
    score = map_iv_rv_ratio_score(iv_rv_ratio)
    
    return Subscore(
        name="iv_vs_rv",
        raw_value={
            "iv": ctx.iv,
            "rv20": ctx.rv20,
            "iv_rv_ratio": iv_rv_ratio,
        },
        score=clamp_score(score),
        weight=0.35,
    )


def compute_iv_percentile_subscore(ctx: ScoringContext) -> Subscore:
    """Compute IV percentile subscore (0-100).
    
    Per Section 14.3:
    Lower IV percentile = better buying opportunity (IV is historically cheap).
    
    Args:
        ctx: ScoringContext with IV percentile
        
    Returns:
        IV percentile Subscore
    """
    iv_percentile = ctx.iv_percentile
    
    if iv_percentile is None:
        # No historical IV data - use neutral score
        return Subscore(
            name="iv_percentile",
            raw_value={"iv_percentile": None, "iv": ctx.iv},
            score=50.0,
            weight=0.25,
        )
    
    score = map_iv_percentile_score(iv_percentile)
    
    return Subscore(
        name="iv_percentile",
        raw_value={
            "iv_percentile": iv_percentile,
            "iv": ctx.iv,
        },
        score=clamp_score(score),
        weight=0.25,
    )


def compute_iv_regime_subscore(ctx: ScoringContext) -> Subscore:
    """Compute IV regime subscore (0-100).
    
    Per Section 14.3:
    Different regimes have different implications for option buying.
    
    Regime scores:
    - IV_LOW_REGIME: 80 (favorable entry)
    - IV_COMPRESSED_PRE_CATALYST: 75 (IV likely to expand)
    - IV_NEUTRAL_REGIME: 60 (neutral)
    - IV_TRENDING_DOWN: 55 (improving)
    - IV_TRENDING_UP: 40 (deteriorating)
    - IV_HIGH_REGIME: 30 (expensive)
    - IV_ELEVATED_POST_CATALYST: 25 (IV crush risk)
    - IV_ELEVATED_PRE_CATALYST: 35 (already priced in)
    
    Args:
        ctx: ScoringContext with IV regime
        
    Returns:
        IV regime Subscore
    """
    iv_regime = ctx.iv_regime
    
    # Map regime to score
    regime_scores = {
        "IV_LOW_REGIME": 80.0,
        "IV_COMPRESSED_PRE_CATALYST": 75.0,
        "IV_NEUTRAL_REGIME": 60.0,
        "IV_TRENDING_DOWN": 55.0,
        "IV_TRENDING_UP": 40.0,
        "IV_HIGH_REGIME": 30.0,
        "IV_ELEVATED_POST_CATALYST": 25.0,
        "IV_ELEVATED_PRE_CATALYST": 35.0,
    }
    
    # Handle both string and enum
    regime_key = str(iv_regime)
    if hasattr(iv_regime, 'value'):
        regime_key = iv_regime.value
    
    score = regime_scores.get(regime_key, 60.0)  # Default to neutral
    
    return Subscore(
        name="iv_regime",
        raw_value={
            "iv_regime": regime_key,
            "days_to_earnings": ctx.days_to_earnings,
        },
        score=clamp_score(score),
        weight=0.20,
    )


def compute_theta_adjusted_edge_subscore(ctx: ScoringContext) -> Subscore:
    """Compute theta-adjusted edge subscore (0-100).
    
    Per Section 14.3:
    Higher theta-adjusted edge = better (expected gains outpace theta decay).
    
    theta_adjusted_edge = daily_expected_gain / daily_theta_cost
    where:
        daily_expected_move = underlying_price × (iv / sqrt(252))
        daily_theta_cost = abs(theta)
        daily_expected_gain = daily_expected_move × abs(delta)
    
    Interpretation:
    - > 2.5: 95 (excellent)
    - 2.0-2.5: 80-95
    - 1.5-2.0: 65-80
    - 1.0-1.5: 50-65 (breakeven zone)
    - 0.75-1.0: 35-50
    - < 0.75: 20 (theta likely to win)
    
    Args:
        ctx: ScoringContext with theta-adjusted edge
        
    Returns:
        Theta-adjusted edge Subscore
    """
    theta_adjusted_edge = ctx.theta_adjusted_edge
    
    if theta_adjusted_edge is None:
        # Cannot compute - use neutral score
        return Subscore(
            name="theta_adjusted_edge",
            raw_value={"theta_adjusted_edge": None},
            score=50.0,
            weight=0.20,
        )
    
    score = map_theta_adjusted_edge_score(theta_adjusted_edge)
    
    return Subscore(
        name="theta_adjusted_edge",
        raw_value={
            "theta_adjusted_edge": theta_adjusted_edge,
        },
        score=clamp_score(score),
        weight=0.20,
    )


def generate_volatility_tags(subscores: list[Subscore], ctx: ScoringContext) -> list[str]:
    """Generate descriptive tags for volatility pillar.
    
    Tags indicate notable volatility conditions.
    
    Args:
        subscores: List of computed subscores
        ctx: ScoringContext
        
    Returns:
        List of tag strings
    """
    tags = []
    
    for subscore in subscores:
        if subscore.name == "iv_vs_rv":
            if subscore.score >= 85:
                tags.append("IV_CHEAP")
            elif subscore.score <= 30:
                tags.append("IV_EXPENSIVE")
        
        if subscore.name == "iv_percentile":
            if subscore.score >= 85:
                tags.append("IV_LOW_PERCENTILE")
            elif subscore.score <= 30:
                tags.append("IV_HIGH_PERCENTILE")
        
        if subscore.name == "iv_regime":
            raw = subscore.raw_value
            if isinstance(raw, dict):
                regime = raw.get("iv_regime", "")
                if "PRE_CATALYST" in regime:
                    tags.append("PRE_CATALYST")
                elif "POST_CATALYST" in regime:
                    tags.append("POST_CATALYST")
        
        if subscore.name == "theta_adjusted_edge":
            if subscore.score >= 80:
                tags.append("THETA_EDGE")
            elif subscore.score <= 30:
                tags.append("THETA_DRAG")
    
    return tags


def compute_volatility_pillar(
    ctx: ScoringContext,
    config: Optional[VolatilityPillarConfig] = None,
) -> PillarResult:
    """Compute the complete Volatility Pillar score.
    
    Combines all 4 subscores with configurable weights to produce
    a final score from 0-100.
    
    Args:
        ctx: ScoringContext with all features
        config: Optional VolatilityPillarConfig (uses defaults if None)
        
    Returns:
        PillarResult with score, subscores, and tags
    """
    if config is None:
        config = VolatilityPillarConfig()
    
    # Compute all subscores
    subscores = [
        compute_iv_vs_rv_subscore(ctx),
        compute_iv_percentile_subscore(ctx),
        compute_iv_regime_subscore(ctx),
        compute_theta_adjusted_edge_subscore(ctx),
    ]
    
    # Update weights from config
    for subscore in subscores:
        if subscore.name == "iv_vs_rv":
            subscore.weight = config.iv_vs_rv_weight
        elif subscore.name == "iv_percentile":
            subscore.weight = config.iv_percentile_weight
        elif subscore.name == "iv_regime":
            subscore.weight = config.iv_regime_weight
        elif subscore.name == "theta_adjusted_edge":
            subscore.weight = config.theta_adjusted_edge_weight
    
    # Calculate final weighted score
    final_score = sum(s.weighted_contribution for s in subscores)
    
    # Generate tags
    tags = generate_volatility_tags(subscores, ctx)
    
    return PillarResult(
        pillar_id=PillarId.VOLATILITY,
        evaluation_id=ctx.evaluation_id,
        score=clamp_score(final_score),
        subscores=subscores,
        tags=tags,
    )
