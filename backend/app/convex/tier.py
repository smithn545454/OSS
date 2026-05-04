"""Convex Mode — Tier Assignment + Final Decision emission.

Tier mapping is driven by three signals on every Stage-4-PASS candidate:

    - PL: the Premium Leverage pillar (0-100) recomputed in Stage 4 on
      the actual selected contract.
    - momentum_aligned: 5-day return ≥ ±5% in the trade direction
      (resolved in Stage 2).
    - uv_detected: the production UV scanner GSI shows an unusual signal
      whose directional skew aligns with the trade direction.

    Tier A: PL ≥ 80 AND momentum_aligned AND uv_detected
    Tier B: PL ≥ 80 AND momentum_aligned
    Tier C: PL ≥ 85 alone, OR PL ≥ 80 + uv_detected
    Reject: anything else (no Decision emitted)

Within-tier ranking uses the PL score directly (PL/100). Cheaper, more
asymmetric contracts rank first.

Position sizing: A = 50%, B = 35%, C = 25% of standard OSS sizing.

Smart Money Confirmation is now a tier-determining input via the UV
lookup, not a visibility-only flag.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.convex._types import Tier
from app.convex.uv_lookup import UVSignal
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
    candidate: "ConvexCandidate",
    config: ConvexConfig,
    uv_signal: Optional[UVSignal] = None,
) -> Optional[Tier]:
    """Map (PL × momentum × UV) onto Tier A / B / C, or None to reject.

    Returns ``None`` when the candidate did not pass all four stages or
    when the new tier rule rejects the combination.
    """
    if candidate.advanced_to_stage < 4:
        return None

    pl_score = _pl_score(candidate.stages)
    if pl_score is None:
        return None

    momentum_aligned = _momentum_aligned(candidate.stages)
    direction = candidate.direction or "ambiguous"
    uv_detected = bool(
        uv_signal
        and uv_signal.is_unusual
        and uv_signal.aligns_with(direction)
    )

    pl_a_min = config.tier_pl_a_min
    pl_c_min = config.tier_pl_c_min

    if pl_score >= pl_a_min and momentum_aligned and uv_detected:
        return Tier.A
    if pl_score >= pl_a_min and momentum_aligned:
        return Tier.B
    if pl_score >= pl_c_min or (pl_score >= pl_a_min and uv_detected):
        return Tier.C
    return None


def _pl_score(stages: ConvexStagesPayload) -> Optional[float]:
    s4 = stages.stage_4
    if s4 is None or s4.result != "PASS":
        return None
    val = s4.extras.get("pl_score")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _momentum_aligned(stages: ConvexStagesPayload) -> bool:
    s2 = stages.stage_2
    if s2 is None:
        return False
    return bool(s2.extras.get("momentum_aligned"))


# ---------------------------------------------------------------------------
# Within-tier composite + sizing
# ---------------------------------------------------------------------------


def within_tier_composite(candidate: "ConvexCandidate") -> float:
    """Single-dimension within-tier ranking score = PL/100.

    Cheaper, more asymmetric contracts (higher PL) rank first within the
    same tier. Falls back to the Stage 4 strength when PL is unavailable
    (shouldn't happen for tier-assigned candidates, but keeps sort stable).
    """
    pl = _pl_score(candidate.stages)
    if pl is not None:
        return round(pl / 100.0, 4)
    s4 = candidate.stages.stage_4
    if s4 is not None and s4.strength is not None:
        return round(float(s4.strength), 4)
    return 0.0


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
    uv_signal: Optional[UVSignal] = None,
) -> Optional[FinalisedConvexCandidate]:
    """Run tier assignment + Decision emission for a candidate.

    Returns ``None`` when the new tier rule rejects the candidate (no
    Decision should be emitted; the candidate is logged but not approved).
    """
    tier = assign_tier(candidate, config, uv_signal=uv_signal)
    if tier is None:
        return None

    candidate.tier = tier
    composite = within_tier_composite(candidate)
    candidate.composite_strength = composite

    # Smart-money flag now reflects whether the production UV scanner
    # corroborated the trade direction (tier-determining for A).
    direction = candidate.direction or "ambiguous"
    smart_money = bool(
        uv_signal and uv_signal.is_unusual and uv_signal.aligns_with(direction)
    )
    candidate.smart_money_confirmation = smart_money

    sizing_note = position_sizing_recommendation(tier, config)
    pl_score = _pl_score(candidate.stages)
    decision = Decision(
        evaluation_id=evaluation_id,
        verdict=Verdict.CONVEX_APPROVE,
        final_score=0.0,  # Sentinel — Convex pipeline doesn't compute composite
        primary_reason_code="CONVEX_APPROVED_BY_TIER",
        supporting_reason_codes=[
            f"convex_tier_{tier.value.lower()}",
            f"direction_{direction}",
            f"pl_score_{int(round(pl_score)) if pl_score is not None else 'na'}",
        ],
        failed_gates=[],
        concentration_warnings=[],
        policy_version=policy_version,
        decided_at=datetime.now(timezone.utc).isoformat(),
        # Convex-specific fields
        convex_tier=tier.value,
        convex_stages=candidate.stages,
        convex_strength_composite=composite,
        smart_money_confirmation=smart_money,
        position_sizing_recommendation=sizing_note,
    )

    return FinalisedConvexCandidate(
        candidate=candidate,
        tier=tier,
        composite=composite,
        decision=decision,
    )
