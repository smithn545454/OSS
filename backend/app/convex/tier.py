"""Convex Mode — Tier Assignment + Final Decision emission.

Combines stage strengths into a tier (A/B/C) and finalises the
recommendation as an OSS ``Decision`` with ``verdict=CONVEX_APPROVE``.
Tier definitions, position-sizing ratios, and Smart Money handling
follow the source plan §9 and Nick's confirmed launch decisions:

    - Tier A: all four stages PASS with high strength on every dimension
      (Stage 2 strength ≥ 0.75 AND Stage 3 composite ≥ 0.70 AND Stage 4
      parameters in the ideal sub-range).
    - Tier B: all gates passed but one stage at moderate strength.
    - Tier C: all gates passed at borderline levels.

Within-tier ranking uses the geometric mean of stage strengths — sort
order only, never displayed as a primary metric.

Position sizing: A = 50%, B = 35%, C = 25% of standard OSS sizing.

Smart Money Confirmation does NOT promote tiers at launch (visibility
only); the flag is preserved on the Decision so the UI can render it
prominently.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.convex._types import Tier
from app.core.schemas import (
    ConvexConfig,
    ConvexStagesPayload,
    Decision,
    Verdict,
)

# ConvexCandidate is referenced only at type-check time (and via duck-typed
# attribute access at runtime) so we avoid the import-cycle through
# ``app.convex.pipeline``.
if False:  # pragma: no cover — typing-only sentinel
    from app.convex.pipeline import ConvexCandidate  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------


def assign_tier(
    candidate: "ConvexCandidate", config: ConvexConfig
) -> Optional[Tier]:
    """Map candidate stage strengths onto Tier A / B / C.

    Returns None when the candidate did not pass all four stages (no
    tier should be assigned).

    Tier A is awarded only when every dimension is strong:
        - Stage 2 strength ≥ ``tier_a_stage2_strength_min``
        - Stage 3 composite ≥ ``tier_a_stage3_composite_min``
        - Stage 4 strength ≥ a derived ideal threshold (0.85 by
          default — captures delta-band centre, DTE-band centre, and
          tight spread).

    Tier B requires at least the Tier B floors on every stage.
    Tier C catches anything above the Tier B floor on every stage.
    """
    if candidate.advanced_to_stage < 4:
        return None

    s2 = _stage_strength(candidate.stages, 2)
    s3 = _stage_strength(candidate.stages, 3)
    s4 = _stage_strength(candidate.stages, 4)

    if (
        s2 >= config.tier_a_stage2_strength_min
        and s3 >= config.tier_a_stage3_composite_min
        and s4 >= 0.85
    ):
        return Tier.A

    if (
        s2 >= config.tier_b_stage2_strength_min
        and s3 >= config.tier_b_stage3_composite_min
    ):
        return Tier.B

    return Tier.C


def _stage_strength(stages: ConvexStagesPayload, n: int) -> float:
    """Return the stage's strength value (0.0 if missing)."""
    payload = getattr(stages, f"stage_{n}", None)
    if payload is None or payload.strength is None:
        return 0.0
    return float(payload.strength)


# ---------------------------------------------------------------------------
# Within-tier composite + sizing
# ---------------------------------------------------------------------------


def within_tier_composite(candidate: "ConvexCandidate") -> float:
    """Geometric mean of stage strengths used for in-tier ordering only."""
    parts = [
        _stage_strength(candidate.stages, n) for n in (2, 3, 4)
    ]
    parts = [p for p in parts if p > 0]
    if not parts:
        return 0.0
    log_sum = sum(math.log(p) for p in parts)
    return round(math.exp(log_sum / len(parts)), 4)


def position_sizing_recommendation(
    tier: Tier, config: ConvexConfig
) -> str:
    """Human-readable sizing string for the Decision payload."""
    pct = {
        Tier.A: config.sizing_tier_a_pct,
        Tier.B: config.sizing_tier_b_pct,
        Tier.C: config.sizing_tier_c_pct,
    }[tier]
    return f"Tier {tier.value} → {int(pct * 100)}% of standard sizing"


# ---------------------------------------------------------------------------
# Decision emission
# ---------------------------------------------------------------------------


@dataclass
class FinalisedConvexCandidate:
    """A Convex candidate with tier, sizing, and Decision attached."""

    candidate: "ConvexCandidate"
    tier: Tier
    composite: float
    decision: Decision


def finalise_candidate(
    candidate: "ConvexCandidate",
    evaluation_id: str,
    policy_version: str,
    config: ConvexConfig,
) -> Optional[FinalisedConvexCandidate]:
    """Run tier assignment + Decision emission for a candidate.

    Returns None when the candidate failed to advance through all four
    stages (no Decision should be emitted; the candidate is logged but
    not approved).
    """
    tier = assign_tier(candidate, config)
    if tier is None:
        return None

    candidate.tier = tier
    composite = within_tier_composite(candidate)
    candidate.composite_strength = composite

    sizing_note = position_sizing_recommendation(tier, config)
    decision = Decision(
        evaluation_id=evaluation_id,
        verdict=Verdict.CONVEX_APPROVE,
        final_score=0.0,  # Sentinel — Convex pipeline doesn't compute composite
        primary_reason_code="CONVEX_APPROVED_BY_TIER",
        supporting_reason_codes=[
            f"convex_tier_{tier.value.lower()}",
            f"direction_{candidate.direction or 'unknown'}",
        ],
        failed_gates=[],
        concentration_warnings=[],
        policy_version=policy_version,
        decided_at=datetime.now(timezone.utc).isoformat(),
        # Convex-specific fields
        convex_tier=tier.value,
        convex_stages=candidate.stages,
        convex_strength_composite=composite,
        smart_money_confirmation=candidate.smart_money_confirmation,
        position_sizing_recommendation=sizing_note,
    )

    return FinalisedConvexCandidate(
        candidate=candidate,
        tier=tier,
        composite=composite,
        decision=decision,
    )
