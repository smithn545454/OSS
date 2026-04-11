"""Underlying Behavior Pillar (Policy v3.0.0).

Measures how the underlying stock is behaving and whether the required
move is realistic given the option's time horizon. Captures volatility
context, trend strength (ADX), and feasibility of reaching profit
targets within DTE. Direction-agnostic structural measures.

Subscores (empirically derived weights):
- ADX (14): trend strength — moderate ADX performs best (non-monotonic)
- Realized Volatility 20d: moderate RV performs best (non-monotonic)
- Feasibility Ratio: higher = more achievable move
- Time-Adjusted Feasibility: feasibility normalized for DTE
- ATR % (14): average true range — moderate ATR performs best

All subscores are tier2 (FeatureValueTable). ADX has sparse historical
coverage (only ~2.5% of old positions until the backfill), so the
scoring engine's weight redistribution ensures the pillar still
produces a valid score when ADX is absent.
"""

from __future__ import annotations

from app.core.schemas import PillarConfigV2, PillarId
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.scoring_engine import score_pillar

PILLAR_ID = PillarId.UNDERLYING_BEHAVIOR


def compute_underlying_behavior_pillar(
    ctx: ScoringContext,
    config: PillarConfigV2,
) -> PillarResult:
    """Compute the Underlying Behavior pillar score.

    Args:
        ctx: ScoringContext with feature values (adx_14, rv20, feasibility_ratio,
             time_adjusted_feasibility, atr14_pct).
        config: Pillar configuration (must have pillar_id == UNDERLYING_BEHAVIOR).

    Returns:
        PillarResult with final score, subscores, and descriptive tags.
    """
    if config.pillar_id != PILLAR_ID:
        raise ValueError(
            f"Expected pillar_id={PILLAR_ID}, got {config.pillar_id}"
        )

    accessor = _UnderlyingBehaviorAccessor(ctx)

    pillar_score, subscores = score_pillar(config, accessor)
    tags = _generate_tags(ctx, subscores)

    return PillarResult(
        pillar_id=PILLAR_ID,
        evaluation_id=ctx.evaluation_id,
        score=pillar_score,
        subscores=subscores,
        tags=tags,
    )


class _UnderlyingBehaviorAccessor:
    """Accessor that forwards to ScoringContext and resolves field name
    mismatches. ScoringContext doesn't currently carry `atr14_pct`, so we
    expose it via `atr14_pct` using a computed fallback if needed.
    """

    def __init__(self, ctx: ScoringContext) -> None:
        self._ctx = ctx

    def __getattr__(self, name: str):
        if name == "atr14_pct":
            return getattr(self._ctx, "atr14_pct", None)
        if name == "feasibility_ratio":
            return getattr(self._ctx, "feasibility_ratio", None)
        if name == "time_adjusted_feasibility":
            return getattr(self._ctx, "time_adjusted_feasibility", None)
        return getattr(self._ctx, name, None)


def _generate_tags(ctx: ScoringContext, subscores: list[Subscore]) -> list[str]:
    """Generate descriptive tags for observability."""
    tags: list[str] = []

    if ctx.adx_14 is not None:
        if ctx.adx_14 < 15:
            tags.append("ADX_WEAK_TREND")
        elif ctx.adx_14 < 25:
            tags.append("ADX_MODERATE_TREND")
        else:
            tags.append("ADX_STRONG_TREND")

    if ctx.rv20 is not None:
        if ctx.rv20 < 0.25:
            tags.append("RV_LOW")
        elif ctx.rv20 < 0.50:
            tags.append("RV_MODERATE")
        else:
            tags.append("RV_HIGH")

    return tags
