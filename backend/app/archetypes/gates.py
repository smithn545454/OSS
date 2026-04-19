"""Anti-archetype gates — v4.1.0 hard-REJECT logic.

An anti-archetype fires when all its conditions hold (discrete match, no
feather). When one fires, the Decision is REJECTed with the anti-archetype's
rejection_reason attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.archetypes.matcher import _evaluate_condition
from app.core.schemas import AntiArchetypeConfig
from app.pillars.models import ScoringContext


@dataclass
class AntiArchetypeResult:
    triggered: bool
    anti_archetype_id: Optional[str]
    rejection_reason: Optional[str]


def check_anti_archetypes(
    ctx: ScoringContext,
    config: AntiArchetypeConfig,
    *,
    pillar_scores: Optional[dict[str, float]] = None,
) -> AntiArchetypeResult:
    """Return the first matching anti-archetype, or a no-match result.

    All conditions must have fit == 100.0 (discrete match) for the gate
    to fire. Feather values on ``ArchetypeCondition`` are honoured on the
    positive-archetype side — here we enforce strict-match semantics.
    """
    pillar_scores = pillar_scores or {}
    for aa in config.anti_archetypes:
        if not aa.enabled:
            continue
        all_match = True
        for cond in aa.conditions:
            fit = _evaluate_condition(ctx, cond, pillar_scores)
            if fit.fit < 100.0:
                all_match = False
                break
        if all_match:
            return AntiArchetypeResult(
                triggered=True,
                anti_archetype_id=aa.anti_archetype_id,
                rejection_reason=aa.rejection_reason,
            )
    return AntiArchetypeResult(
        triggered=False,
        anti_archetype_id=None,
        rejection_reason=None,
    )
