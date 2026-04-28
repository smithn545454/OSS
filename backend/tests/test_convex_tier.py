"""Tests for Convex Mode tier assignment + Decision emission (Phase 6)."""

from __future__ import annotations

import pytest

from app.convex import (
    ConvexCandidate,
    Tier,
    assign_tier,
    finalise_candidate,
    position_sizing_recommendation,
    within_tier_composite,
)
from app.core.schemas import (
    ConvexConfig,
    ConvexStagePayload,
    ConvexStagesPayload,
    Verdict,
)


def _candidate(
    s2: float = 0.0, s3: float = 0.0, s4: float = 0.0,
    advanced: int = 4,
    direction: str = "bullish",
    smart_money: bool = False,
) -> ConvexCandidate:
    """Build a candidate with stages populated to a target advancement level."""
    stages = ConvexStagesPayload()
    if advanced >= 1:
        stages = stages.model_copy(update={
            "stage_1": ConvexStagePayload(
                stage=1, stage_name="Kinetic Universe",
                result="PASS", summary="x", strength=1.0,
            )
        })
    if advanced >= 2:
        stages = stages.model_copy(update={
            "stage_2": ConvexStagePayload(
                stage=2, stage_name="Catalyst Layer",
                result="PASS", summary="x", strength=s2,
            )
        })
    if advanced >= 3:
        stages = stages.model_copy(update={
            "stage_3": ConvexStagePayload(
                stage=3, stage_name="Volatility Mispricing",
                result="PASS", summary="x", strength=s3,
            )
        })
    if advanced == 4:
        stages = stages.model_copy(update={
            "stage_4": ConvexStagePayload(
                stage=4, stage_name="Contract Selection",
                result="PASS", summary="x", strength=s4,
            )
        })
    c = ConvexCandidate(ticker="NVDA", stages=stages, direction=direction)
    c.smart_money_confirmation = smart_money
    return c


# ---------------------------------------------------------------------------
# assign_tier
# ---------------------------------------------------------------------------


class TestAssignTier:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_returns_none_when_not_advanced_through_all_four(self):
        candidate = _candidate(s2=0.9, s3=0.9, s4=0.9, advanced=3)
        assert assign_tier(candidate, self.cfg) is None

    def test_tier_a_for_high_strength_everywhere(self):
        candidate = _candidate(s2=0.85, s3=0.80, s4=0.90)
        assert assign_tier(candidate, self.cfg) == Tier.A

    def test_tier_b_when_one_dimension_moderate(self):
        # Stage 4 below 0.85 ideal → drops to Tier B
        candidate = _candidate(s2=0.85, s3=0.80, s4=0.70)
        assert assign_tier(candidate, self.cfg) == Tier.B

    def test_tier_b_when_stage2_moderate(self):
        candidate = _candidate(s2=0.60, s3=0.80, s4=0.90)
        assert assign_tier(candidate, self.cfg) == Tier.B

    def test_tier_c_when_borderline(self):
        # Stage 2 = 0.45, Stage 3 = 0.45 — below Tier B floors
        candidate = _candidate(s2=0.45, s3=0.45, s4=0.50)
        assert assign_tier(candidate, self.cfg) == Tier.C

    def test_returns_tier_c_when_stages_minimal(self):
        # All gates passed at floor → Tier C
        candidate = _candidate(s2=0.30, s3=0.20, s4=0.30)
        assert assign_tier(candidate, self.cfg) == Tier.C


# ---------------------------------------------------------------------------
# Within-tier composite + sizing
# ---------------------------------------------------------------------------


class TestWithinTierComposite:
    """Within-tier ranking is now Stage-3-strength only (cheaper convexity ranks first)."""

    def test_returns_stage3_strength_directly(self):
        candidate = _candidate(s2=0.5, s3=0.5, s4=0.5)
        assert within_tier_composite(candidate) == pytest.approx(0.5, abs=1e-3)

    def test_zero_when_all_zero(self):
        candidate = _candidate(s2=0.0, s3=0.0, s4=0.0)
        assert within_tier_composite(candidate) == 0.0

    def test_falls_back_to_stage2_when_stage3_missing(self):
        # Defensive fallback for sort stability when Stage 3 didn't run.
        candidate = _candidate(s2=0.8, s3=0.0, s4=0.6)
        assert within_tier_composite(candidate) == pytest.approx(0.8, abs=1e-3)

    def test_higher_stage3_strength_produces_higher_composite(self):
        weak = _candidate(s2=0.4, s3=0.4, s4=0.4)
        strong = _candidate(s2=0.4, s3=0.9, s4=0.4)  # Stage 3 differentiator
        assert within_tier_composite(strong) > within_tier_composite(weak)


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------


class TestPositionSizing:

    def test_tier_a_50pct(self):
        result = position_sizing_recommendation(Tier.A, ConvexConfig())
        assert "Tier A" in result
        assert "50%" in result

    def test_tier_b_35pct(self):
        result = position_sizing_recommendation(Tier.B, ConvexConfig())
        assert "Tier B" in result
        assert "35%" in result

    def test_tier_c_25pct(self):
        result = position_sizing_recommendation(Tier.C, ConvexConfig())
        assert "Tier C" in result
        assert "25%" in result


# ---------------------------------------------------------------------------
# finalise_candidate (Decision emission)
# ---------------------------------------------------------------------------


class TestFinaliseCandidate:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_returns_none_when_not_advanced(self):
        candidate = _candidate(advanced=2)
        result = finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg)
        assert result is None

    def test_emits_decision_with_convex_approve(self):
        candidate = _candidate(s2=0.85, s3=0.80, s4=0.90)
        result = finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg)
        assert result is not None
        assert result.tier == Tier.A
        assert result.decision.verdict == Verdict.CONVEX_APPROVE
        assert result.decision.convex_tier == "A"
        assert result.decision.policy_version == "v4.1.1"
        assert result.decision.evaluation_id == "eval-1"

    def test_decision_carries_stages_payload(self):
        candidate = _candidate(s2=0.85, s3=0.80, s4=0.90)
        result = finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg)
        assert result.decision.convex_stages is not None
        assert result.decision.convex_stages.stage_1 is not None
        assert result.decision.convex_stages.stage_4 is not None

    def test_smart_money_propagates_to_decision(self):
        candidate = _candidate(s2=0.85, s3=0.80, s4=0.90, smart_money=True)
        result = finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg)
        assert result.decision.smart_money_confirmation is True

    def test_sizing_recommendation_in_decision(self):
        candidate = _candidate(s2=0.85, s3=0.80, s4=0.90)
        result = finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg)
        assert result.decision.position_sizing_recommendation is not None
        assert "50%" in result.decision.position_sizing_recommendation

    def test_composite_strength_attached(self):
        candidate = _candidate(s2=0.85, s3=0.80, s4=0.90)
        result = finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg)
        assert result.decision.convex_strength_composite is not None
        assert 0.0 < result.decision.convex_strength_composite <= 1.0

    def test_reason_codes_descriptive(self):
        candidate = _candidate(s2=0.85, s3=0.80, s4=0.90)
        result = finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg)
        assert result.decision.primary_reason_code == "CONVEX_APPROVED_BY_TIER"
        assert any(
            "convex_tier_a" in r for r in result.decision.supporting_reason_codes
        )
        assert any(
            "direction_bullish" in r for r in result.decision.supporting_reason_codes
        )

    def test_final_score_sentinel_zero(self):
        # Convex doesn't compute a composite score; sentinel 0.0 keeps
        # the schema valid while signalling "not applicable".
        candidate = _candidate(s2=0.85, s3=0.80, s4=0.90)
        result = finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg)
        assert result.decision.final_score == 0.0
