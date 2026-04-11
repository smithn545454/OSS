"""Setup Quality Pillar (Policy v3.0.0).

Meta-indicators of a high-quality entry:
- Volume (contrarian): within the already-filtered opportunity set,
  lower relative volume has historically predicted better outcomes
  (less crowded trades = more edge).
- Open Interest (contrarian): same pattern.
- Scanner Convergence: when multiple scanners fire on the same ticker,
  outcomes are meaningfully better.
- DTE Bucket: shorter-DTE buckets (A, B) structurally outperform longer
  ones (C, D) because of theta exposure.

Direction-agnostic meta-signal quality measures. Works for both calls
and puts regardless of market regime.
"""

from __future__ import annotations

from app.core.schemas import PillarConfigV2, PillarId
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.scoring_engine import score_pillar

PILLAR_ID = PillarId.SETUP_QUALITY


def compute_setup_quality_pillar(
    ctx: ScoringContext,
    config: PillarConfigV2,
) -> PillarResult:
    """Compute the Setup Quality pillar score.

    Args:
        ctx: ScoringContext with feature values (volume, open_interest,
             convergence_count, dte_bucket).
        config: Pillar configuration (must have pillar_id == SETUP_QUALITY).

    Returns:
        PillarResult with final score, subscores, and descriptive tags.
    """
    if config.pillar_id != PILLAR_ID:
        raise ValueError(
            f"Expected pillar_id={PILLAR_ID}, got {config.pillar_id}"
        )

    accessor = _SetupQualityAccessor(ctx)
    pillar_score, subscores = score_pillar(config, accessor)
    tags = _generate_tags(ctx, subscores)

    return PillarResult(
        pillar_id=PILLAR_ID,
        evaluation_id=ctx.evaluation_id,
        score=pillar_score,
        subscores=subscores,
        tags=tags,
    )


class _SetupQualityAccessor:
    """Forwards to ScoringContext fields used by Setup Quality subscores."""

    def __init__(self, ctx: ScoringContext) -> None:
        self._ctx = ctx

    def __getattr__(self, name: str):
        # dte_bucket is categorical; ensure it's a plain string for the
        # category map lookup (ctx.dte_bucket is already str).
        return getattr(self._ctx, name, None)


def _generate_tags(ctx: ScoringContext, subscores: list[Subscore]) -> list[str]:
    """Generate descriptive tags for observability."""
    tags: list[str] = []

    if ctx.convergence_count >= 2:
        tags.append("SCANNER_CONVERGENCE")

    if ctx.dte_bucket in ("A", "B"):
        tags.append("SHORT_DTE_BUCKET")
    elif ctx.dte_bucket in ("C", "D"):
        tags.append("LONG_DTE_BUCKET")

    return tags
