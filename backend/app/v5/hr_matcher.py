"""v5 HR archetype matcher.

Thin wrapper around the v4.1.0 archetype matcher (:mod:`app.archetypes.matcher`).
The two systems share identical condition-matching math (feather-graded fit
scoring, MIN aggregation, per-archetype min_fit_to_match threshold) — what
differs is the library passed in (v5_hr_archetypes vs v4.1.0 archetypes)
and the downstream conviction formula.

Keeping a separate module gives v5 a clean import surface and a place to
add HR-specific logic later (e.g., archetype-id stability checks against
the rates table).
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


def match_hr_archetypes(
    ctx: ScoringContext,
    config: ArchetypeConfig,
    *,
    pillar_scores: Optional[dict[str, float]] = None,
) -> ArchetypeMatchResult:
    """Match a scoring context against the v5 HR archetype library.

    Returns the same :class:`ArchetypeMatchResult` shape used by v4.1.0,
    so downstream consumers can reuse the structure.

    Args:
        ctx: Scoring context (features + scanner + option type).
        config: HR archetype library (typically ``policy.v5_hr_archetypes``).
        pillar_scores: Optional dict of pillar scores keyed by short ID
            (``"DC"`` / ``"MP"`` / ``"TS"``) so archetypes can reference
            ``dc_score`` / ``mp_score`` / ``ts_score`` features.

    Returns:
        :class:`ArchetypeMatchResult` with ``best`` (the matched archetype,
        if any), ``best_match_score`` (its fit score), and ``all_fits``
        (per-archetype fit values for debugging / logging).

        When no archetype matches (no fit ≥ min_fit_to_match), ``best``
        is ``None`` — callers should treat HR conviction as 0.
    """
    return compute_archetype_match(ctx, config, pillar_scores=pillar_scores)


# Re-export for convenience so callers don't need a second import line.
__all__ = ["match_hr_archetypes", "ArchetypeMatchResult", "ArchetypeFit"]
