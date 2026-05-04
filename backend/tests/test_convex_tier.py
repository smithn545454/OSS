"""Tests for Convex Mode tier assignment + Decision emission.

After the PL/momentum/UV overhaul, tier is assigned from three signals:
    - PL score (recomputed on the Stage 4 selected contract, in extras)
    - momentum_aligned flag (set by Stage 2 in extras)
    - UV signal from the production scanner GSI (passed to assign_tier)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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


@dataclass
class _FakeUVSignal:
    """Minimal stand-in for app.convex.uv_lookup.UVSignal."""

    is_unusual: bool
    skew: str  # "call_heavy" | "put_heavy" | "balanced"

    def aligns_with(self, direction: str) -> bool:
        if direction == "bullish":
            return self.skew == "call_heavy"
        if direction == "bearish":
            return self.skew == "put_heavy"
        return False


def _candidate(
    *,
    pl_score: Optional[float] = None,
    momentum_aligned: bool = False,
    advanced: int = 4,
    direction: str = "bullish",
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
                stage=2, stage_name="Catalyst + Direction",
                result="PASS", summary="x", strength=0.7,
                extras={
                    "direction": direction,
                    "momentum_aligned": momentum_aligned,
                },
            )
        })
    if advanced >= 3:
        stages = stages.model_copy(update={
            "stage_3": ConvexStagePayload(
                stage=3, stage_name="PL Pricing Pre-Screen",
                result="PASS", summary="x", strength=0.7,
            )
        })
    if advanced == 4:
        s4_extras: dict = {}
        if pl_score is not None:
            s4_extras["pl_score"] = pl_score
        stages = stages.model_copy(update={
            "stage_4": ConvexStagePayload(
                stage=4, stage_name="Contract Selection",
                result="PASS", summary="x", strength=0.7,
                extras=s4_extras,
            )
        })
    return ConvexCandidate(ticker="NVDA", stages=stages, direction=direction)


# ---------------------------------------------------------------------------
# assign_tier — new PL + momentum + UV rule
# ---------------------------------------------------------------------------


class TestAssignTier:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_returns_none_when_not_advanced_through_all_four(self):
        candidate = _candidate(pl_score=90.0, advanced=3)
        assert assign_tier(candidate, self.cfg) is None

    def test_returns_none_when_pl_missing(self):
        candidate = _candidate(pl_score=None, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        assert assign_tier(candidate, self.cfg, uv_signal=uv) is None

    def test_tier_a_requires_pl_momentum_and_uv(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        assert assign_tier(candidate, self.cfg, uv_signal=uv) == Tier.A

    def test_tier_b_when_uv_missing(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        # no uv_signal → drops to B (PL ≥ 80 + momentum)
        assert assign_tier(candidate, self.cfg) == Tier.B

    def test_tier_b_when_uv_unaligned(self):
        candidate = _candidate(
            pl_score=82.0, momentum_aligned=True, direction="bullish",
        )
        # Bearish UV against a bullish thesis → not "uv_detected aligned"
        uv = _FakeUVSignal(is_unusual=True, skew="put_heavy")
        assert assign_tier(candidate, self.cfg, uv_signal=uv) == Tier.B

    def test_tier_c_when_pl_alone_above_85(self):
        candidate = _candidate(pl_score=87.0, momentum_aligned=False)
        # No UV, no momentum — but PL alone ≥ 85 qualifies for C.
        assert assign_tier(candidate, self.cfg) == Tier.C

    def test_tier_c_when_pl_above_80_with_uv_only(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=False)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        assert assign_tier(candidate, self.cfg, uv_signal=uv) == Tier.C

    def test_reject_when_pl_below_80(self):
        candidate = _candidate(pl_score=75.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        assert assign_tier(candidate, self.cfg, uv_signal=uv) is None

    def test_reject_when_pl_below_85_alone(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=False)
        # PL ≥ 80 but no momentum and no UV → only Tier C if PL ≥ 85.
        assert assign_tier(candidate, self.cfg) is None

    def test_uv_must_be_unusual(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        # UV present but not unusual → counts as no UV.
        uv = _FakeUVSignal(is_unusual=False, skew="call_heavy")
        assert assign_tier(candidate, self.cfg, uv_signal=uv) == Tier.B


# ---------------------------------------------------------------------------
# Within-tier composite + sizing
# ---------------------------------------------------------------------------


class TestWithinTierComposite:
    """Within-tier ranking is now PL/100 (cheaper convexity ranks first)."""

    def test_returns_pl_over_100(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        assert within_tier_composite(candidate) == pytest.approx(0.82, abs=1e-3)

    def test_falls_back_to_stage4_strength_when_pl_missing(self):
        candidate = _candidate(pl_score=None)
        assert within_tier_composite(candidate) == pytest.approx(0.7, abs=1e-3)

    def test_higher_pl_produces_higher_composite(self):
        weak = _candidate(pl_score=80.0, momentum_aligned=True)
        strong = _candidate(pl_score=95.0, momentum_aligned=True)
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
        candidate = _candidate(pl_score=82.0, momentum_aligned=True, advanced=2)
        assert finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg) is None

    def test_returns_none_when_tier_rejects(self):
        candidate = _candidate(pl_score=70.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        # PL below 80 → rejected by the new rule.
        assert finalise_candidate(
            candidate, "eval-1", "v4.1.1", self.cfg, uv_signal=uv
        ) is None

    def test_emits_decision_with_convex_approve(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        result = finalise_candidate(
            candidate, "eval-1", "v4.1.1", self.cfg, uv_signal=uv,
        )
        assert result is not None
        assert result.tier == Tier.A
        assert result.decision.verdict == Verdict.CONVEX_APPROVE
        assert result.decision.convex_tier == "A"
        assert result.decision.policy_version == "v4.1.1"
        assert result.decision.evaluation_id == "eval-1"

    def test_decision_carries_stages_payload(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        result = finalise_candidate(
            candidate, "eval-1", "v4.1.1", self.cfg, uv_signal=uv,
        )
        assert result.decision.convex_stages is not None
        assert result.decision.convex_stages.stage_1 is not None
        assert result.decision.convex_stages.stage_4 is not None

    def test_smart_money_reflects_uv_alignment(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        result = finalise_candidate(
            candidate, "eval-1", "v4.1.1", self.cfg, uv_signal=uv,
        )
        assert result.decision.smart_money_confirmation is True

    def test_smart_money_false_without_uv(self):
        candidate = _candidate(pl_score=87.0, momentum_aligned=False)
        # PL alone ≥ 85 qualifies for Tier C; no UV passed.
        result = finalise_candidate(candidate, "eval-1", "v4.1.1", self.cfg)
        assert result is not None
        assert result.decision.smart_money_confirmation is False

    def test_sizing_recommendation_in_decision(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        result = finalise_candidate(
            candidate, "eval-1", "v4.1.1", self.cfg, uv_signal=uv,
        )
        assert result.decision.position_sizing_recommendation is not None
        assert "50%" in result.decision.position_sizing_recommendation

    def test_composite_strength_attached(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        result = finalise_candidate(
            candidate, "eval-1", "v4.1.1", self.cfg, uv_signal=uv,
        )
        assert result.decision.convex_strength_composite is not None
        assert 0.0 < result.decision.convex_strength_composite <= 1.0

    def test_reason_codes_descriptive(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        result = finalise_candidate(
            candidate, "eval-1", "v4.1.1", self.cfg, uv_signal=uv,
        )
        assert result.decision.primary_reason_code == "CONVEX_APPROVED_BY_TIER"
        assert any(
            "convex_tier_a" in r for r in result.decision.supporting_reason_codes
        )
        assert any(
            "direction_bullish" in r for r in result.decision.supporting_reason_codes
        )
        assert any(
            "pl_score_82" in r for r in result.decision.supporting_reason_codes
        )

    def test_final_score_sentinel_zero(self):
        candidate = _candidate(pl_score=82.0, momentum_aligned=True)
        uv = _FakeUVSignal(is_unusual=True, skew="call_heavy")
        result = finalise_candidate(
            candidate, "eval-1", "v4.1.1", self.cfg, uv_signal=uv,
        )
        assert result.decision.final_score == 0.0
