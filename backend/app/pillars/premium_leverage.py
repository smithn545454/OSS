"""Premium Leverage Pillar (Policy v3.0.0).

Measures how cheap and asymmetric the option contract is. Uses
direction-agnostic features only — `abs_delta` (not signed delta), IV,
IV percentile, and IV/RV ratio. This ensures the pillar does not encode
any put-over-call bias from the training sample.

Subscores (empirically derived weights in final_pillar_design.json):
- Implied Volatility (dominant): low IV = cheap premium
- |Entry Delta|: distance from ATM measured without direction
- IV Percentile: historically cheap IV
- IV/RV Ratio: IV fair vs realized volatility

Data tier:
- abs_delta: derived from entry_delta (tier1, denormalized on PaperPosition)
- iv, iv_percentile, iv_rv_ratio: tier2 (pulled from FeatureValueTable)
"""

from __future__ import annotations

from app.core.schemas import PillarConfigV2, PillarId
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.scoring_engine import score_pillar

PILLAR_ID = PillarId.PREMIUM_LEVERAGE


def compute_premium_leverage_pillar(
    ctx: ScoringContext,
    config: PillarConfigV2,
) -> PillarResult:
    """Compute the Premium Leverage pillar score.

    Args:
        ctx: ScoringContext with feature values (abs_delta is a property of
             ScoringContext that returns |delta|; iv, iv_percentile,
             iv_rv_ratio come from FeatureSet).
        config: Pillar configuration (must have pillar_id == PREMIUM_LEVERAGE).

    Returns:
        PillarResult with final score, subscores, and descriptive tags.
    """
    if config.pillar_id != PILLAR_ID:
        raise ValueError(
            f"Expected pillar_id={PILLAR_ID}, got {config.pillar_id}"
        )

    pillar_score, subscores = score_pillar(config, ctx)
    tags = _generate_tags(ctx, subscores)

    return PillarResult(
        pillar_id=PILLAR_ID,
        evaluation_id=ctx.evaluation_id,
        score=pillar_score,
        subscores=subscores,
        tags=tags,
    )


def _generate_tags(ctx: ScoringContext, subscores: list[Subscore]) -> list[str]:
    """Generate descriptive tags for observability."""
    tags: list[str] = []

    # Delta tag
    abs_delta = abs(ctx.delta) if ctx.delta is not None else None
    if abs_delta is not None:
        if abs_delta <= 0.15:
            tags.append("DELTA_FAR_OTM")
        elif abs_delta <= 0.30:
            tags.append("DELTA_OTM")
        elif abs_delta <= 0.55:
            tags.append("DELTA_NEAR_ATM")
        else:
            tags.append("DELTA_ITM")

    # IV tag
    if ctx.iv:
        if ctx.iv < 0.25:
            tags.append("IV_LOW")
        elif ctx.iv < 0.45:
            tags.append("IV_MODERATE")
        elif ctx.iv < 0.70:
            tags.append("IV_ELEVATED")
        else:
            tags.append("IV_HIGH")

    # IV Percentile tag
    if ctx.iv_percentile is not None:
        if ctx.iv_percentile <= 20:
            tags.append("IV_PERCENTILE_CHEAP")
        elif ctx.iv_percentile >= 70:
            tags.append("IV_PERCENTILE_EXPENSIVE")

    return tags
