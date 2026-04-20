"""v5 P archetype matcher.

Thin wrapper around the v4.1.0 archetype matcher (:mod:`app.archetypes.matcher`).
P archetypes share the same schema shape as HR archetypes — different
library, same matching math.

Keeping a separate module gives v5 a clean import surface and a place
to add P-specific resolution logic later (e.g., an ``rs_contrarian=0``
condition means "RS agrees with direction" which the matcher's existing
``rs_contrarian`` resolver supports — see ``app.archetypes.matcher:
_resolve_feature``).
"""

from __future__ import annotations

from typing import Optional

from app.archetypes.matcher import (
    ArchetypeFit,
    ArchetypeMatchResult,
    compute_archetype_match,
)
from app.core.schemas import ArchetypeConfig
from app.pillars.models import ScoringContext


def match_p_archetypes(
    ctx: ScoringContext,
    config: ArchetypeConfig,
    *,
    pillar_scores: Optional[dict[str, float]] = None,
) -> ArchetypeMatchResult:
    """Match a scoring context against the v5 P archetype library.

    Returns the same :class:`ArchetypeMatchResult` shape used by v4.1.0
    and HR matcher — downstream consumers can treat matcher results
    uniformly across all three axes.

    Args:
        ctx: Scoring context (features + scanner + option type).
        config: P archetype library (typically ``policy.v5_p_archetypes``).
        pillar_scores: Optional pillar-score dict for archetypes that
            reference ``dc_score`` / ``mp_score`` / ``ts_score`` features.

    Returns:
        :class:`ArchetypeMatchResult`. When no archetype matches,
        ``best`` is ``None`` — callers should treat P conviction as 0.
    """
    return compute_archetype_match(ctx, config, pillar_scores=pillar_scores)


__all__ = ["match_p_archetypes", "ArchetypeMatchResult", "ArchetypeFit"]
