"""Entry Quality Pillar (stored as STRUCTURE for backward compatibility).

Assesses whether the contract is positioned for asymmetric upside based on
entry characteristics that predict trade outcomes.

Subscores:
1. Delta/Moneyness (50%) - Far OTM = better risk/reward asymmetry
2. Raw IV Level (25%) - Lower IV = cheaper premium = better entry
3. DTE Appropriateness (25%) - Enough time for thesis to play out

Note: This pillar replaced the original Structure & Quality pillar which
scored liquidity metrics (spread, OI, volume). Those liquidity checks now
exist solely as hard gates (Stage 6) where they belong — as tradability
filters, not quality signals.
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
    map_delta_moneyness_score,
    map_raw_iv_score,
    map_dte_appropriateness_score,
)


def compute_delta_moneyness_subscore(ctx: ScoringContext) -> Subscore:
    """Compute delta/moneyness subscore (0-100).

    Far OTM options (low abs delta) have better risk/reward asymmetry.
    Data shows 31pp win rate spread between best and worst quintiles:
    - Q1 (lowest delta): 61% WR
    - Q5 (highest delta): 30% WR

    Args:
        ctx: ScoringContext with delta data

    Returns:
        Delta/Moneyness Subscore
    """
    abs_delta = abs(ctx.delta)

    score = map_delta_moneyness_score(abs_delta)

    return Subscore(
        name="delta_moneyness",
        raw_value={
            "delta": ctx.delta,
            "abs_delta": abs_delta,
        },
        score=clamp_score(score),
        weight=0.50,
    )


def compute_raw_iv_subscore(ctx: ScoringContext) -> Subscore:
    """Compute raw IV level subscore (0-100).

    Lower IV = cheaper premium = better asymmetric upside.
    Data shows correlation r=-0.062 with outcomes.
    Combined with low delta, achieves 62% WR (n=2,518).

    Args:
        ctx: ScoringContext with IV data

    Returns:
        Raw IV Subscore
    """
    iv = ctx.iv

    score = map_raw_iv_score(iv)

    return Subscore(
        name="raw_iv",
        raw_value={
            "iv": iv,
        },
        score=clamp_score(score),
        weight=0.25,
    )


def compute_dte_appropriateness_subscore(ctx: ScoringContext) -> Subscore:
    """Compute DTE appropriateness subscore (0-100).

    Moderate-long DTE gives time for thesis to play out while managing
    theta decay. Data shows low delta + high DTE = 56.4% WR.

    Args:
        ctx: ScoringContext with DTE data

    Returns:
        DTE Appropriateness Subscore
    """
    dte = ctx.dte

    score = map_dte_appropriateness_score(dte)

    return Subscore(
        name="dte_appropriateness",
        raw_value={
            "dte": dte,
            "dte_bucket": ctx.dte_bucket,
        },
        score=clamp_score(score),
        weight=0.25,
    )


def generate_structure_tags(subscores: list[Subscore], ctx: ScoringContext) -> list[str]:
    """Generate descriptive tags for entry quality pillar.

    Tags indicate notable entry quality conditions.

    Args:
        subscores: List of computed subscores
        ctx: ScoringContext

    Returns:
        List of tag strings
    """
    tags = []

    for subscore in subscores:
        if subscore.name == "delta_moneyness":
            if subscore.score >= 90:
                tags.append("SWEET_SPOT_DELTA")
            elif subscore.score <= 30:
                tags.append("HIGH_DELTA")

        if subscore.name == "raw_iv":
            if subscore.score >= 80:
                tags.append("LOW_IV_ENTRY")
            elif subscore.score <= 35:
                tags.append("HIGH_IV_ENTRY")

        if subscore.name == "dte_appropriateness":
            if subscore.score >= 80:
                tags.append("GOOD_DTE")
            elif subscore.score <= 30:
                tags.append("SHORT_DTE")

    # Interaction tag: both delta and IV in sweet spot
    abs_delta = abs(ctx.delta)
    if abs_delta <= 0.15 and ctx.iv is not None and 0 < ctx.iv <= 0.35:
        tags.append("ENTRY_SWEET_SPOT")

    return tags


def compute_structure_pillar(
    ctx: ScoringContext,
    config: Optional[StructurePillarConfig] = None,
) -> PillarResult:
    """Compute the Entry Quality Pillar score (stored as STRUCTURE).

    Combines 3 subscores with configurable weights to produce
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
        compute_delta_moneyness_subscore(ctx),
        compute_raw_iv_subscore(ctx),
        compute_dte_appropriateness_subscore(ctx),
    ]

    # Update weights from config
    for subscore in subscores:
        if subscore.name == "delta_moneyness":
            subscore.weight = config.delta_moneyness_weight
        elif subscore.name == "raw_iv":
            subscore.weight = config.raw_iv_weight
        elif subscore.name == "dte_appropriateness":
            subscore.weight = config.dte_appropriateness_weight

    # Calculate final weighted score
    final_score = sum(s.weighted_contribution for s in subscores)

    # Interaction bonus: reward co-occurrence of low delta + low IV
    # Intelligence data: +15.1pp WR lift when both are in favorable zone (62% WR, n=2,518)
    abs_delta = abs(ctx.delta)
    iv = ctx.iv
    if (
        config.interaction_bonus > 0
        and abs_delta <= config.interaction_delta_threshold
        and iv is not None
        and 0 < iv <= config.interaction_iv_threshold
    ):
        final_score += config.interaction_bonus

    # Generate tags
    tags = generate_structure_tags(subscores, ctx)

    return PillarResult(
        pillar_id=PillarId.STRUCTURE,
        evaluation_id=ctx.evaluation_id,
        score=clamp_score(final_score),
        subscores=subscores,
        tags=tags,
    )
