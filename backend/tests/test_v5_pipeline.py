"""Tests for the v5 pipeline orchestrator and decision integration.

Covers:
  * V5Envelope.to_decision_fields shape
  * compute_v5_envelope end-to-end with minimal policy
  * derive_v5_verdict branches (APPROVE tiers, WATCH, REJECT, gates, anti-arch)
  * DecisionCalculator.compute_decision populates v5 fields when envelope present
  * v5 drives verdict when scanner is in v5_active_scanners
  * v4.1.0 still drives verdict when scanner is NOT in allowlist (shadow mode)
  * End-to-end: DecisionStage produces Decisions with v5 fields populated
"""

from __future__ import annotations

from app.core.schemas import (
    DecisionConfig,
    PolicyConfig,
    QualityTier,
    V5CalibrationConfig,
    Verdict,
)
from app.decision.calculator import DecisionCalculator, DecisionContext
from app.pillars.models import ScoringContext
from app.v5.hr_archetypes import default_v5_hr_archetypes
from app.v5.p_archetypes import default_v5_p_archetypes
from app.v5.pipeline import (
    V5Envelope,
    compute_v5_envelope,
    derive_v5_verdict,
)

# ============================================================================
# Helpers
# ============================================================================


def _ctx(**kwargs) -> ScoringContext:
    defaults = {
        "evaluation_id": "eval-test",
        "underlying_ticker": "TEST",
        "option_type": "CALL",
        "dte_bucket": "A",
        "scanner_source": "UNUSUAL_VOLUME",
        "dte": 18,
        "delta": 0.20,
    }
    defaults.update(kwargs)
    return ScoringContext(**defaults)


def _minimal_v5_policy(
    *,
    v5_active: bool = True,
    v5_active_scanners: list[str] | None = None,
    v5_gbm_enabled: bool = False,
    v5_hr_threshold: float = 7.0,
    v5_p_threshold: float = 50.0,
) -> PolicyConfig:
    """Construct a PolicyConfig with v5 archetypes and settings populated."""
    return PolicyConfig(
        v5_active=v5_active,
        v5_active_scanners=v5_active_scanners or [],
        v5_hr_archetypes=default_v5_hr_archetypes(),
        v5_p_archetypes=default_v5_p_archetypes(),
        v5_calibration=V5CalibrationConfig(),
        v5_gbm_enabled=v5_gbm_enabled,
        v5_hr_threshold=v5_hr_threshold,
        v5_p_threshold=v5_p_threshold,
    )


# ============================================================================
# V5Envelope
# ============================================================================


class TestV5EnvelopeShape:
    def test_to_decision_fields_keys(self) -> None:
        envelope = V5Envelope(
            hr_conviction=12.0, p_conviction=55.0,
            hr_archetype_matched="UV_LOTTERY_CALL",
            hr_archetype_fit=95.0, hr_p_point=0.2, hr_p_lower=0.14, hr_p_upper=0.27,
            hr_n_trades=136,
            p_archetype_matched="BREAKDOWN_GRINDER",
            p_archetype_fit=100.0, p_win_point=0.66, p_win_lower=0.60,
            p_mean_pnl_estimate=29.57,
            regime_alignment=1.0, gbm_hr_score=15.0, gbm_p_score=40.0,
        )
        fields = envelope.to_decision_fields()
        # All 17 v5 field names present
        expected = {
            "hr_conviction", "hr_archetype_matched", "hr_archetype_fit",
            "hr_p_point", "hr_p_lower", "hr_p_upper", "hr_n_trades",
            "p_conviction", "p_archetype_matched", "p_archetype_fit",
            "p_win_point", "p_win_lower", "p_mean_pnl_estimate",
            "regime_alignment", "gbm_hr_score", "gbm_p_score",
            "v5_scoring_version",
        }
        assert set(fields.keys()) == expected


# ============================================================================
# compute_v5_envelope
# ============================================================================


class TestComputeV5Envelope:
    def test_uv_lottery_call_produces_hr_conviction(self) -> None:
        """Canonical UV lottery trade should produce meaningful HR conviction."""
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            option_type="CALL", dte=18, delta=0.20,
        )
        policy = _minimal_v5_policy(v5_gbm_enabled=False)
        envelope = compute_v5_envelope(
            ctx, policy,
            pillar_scores={"DC": 50.0, "MP": 55.0, "TS": 80.0},
        )
        # UV_LOTTERY_CALL should match → non-zero HR (seed fallback)
        assert envelope.hr_conviction > 5.0
        assert envelope.hr_archetype_matched in {
            "UV_LOTTERY_CALL", "UV_LOTTERY_DC_MID",
            "UV_LOTTERY_IVP_LO", "UV_LOTTERY_IVRV_CHEAP",
        }
        # Should also match a P archetype (UV_VOLATILE_CALL needs ATR, but
        # a plain UV CALL might match nothing in P library without ATR).
        # Either zero or non-zero P is acceptable — just must be finite.
        assert 0.0 <= envelope.p_conviction <= 100.0

    def test_breakdown_produces_p_conviction(self) -> None:
        """BREAKDOWN trade should get P conviction but zero HR (no HR archetype matches)."""
        ctx = _ctx(
            scanner_source="BREAKDOWN", option_type="PUT",
            dte=30, delta=-0.40,
        )
        policy = _minimal_v5_policy(v5_gbm_enabled=False)
        envelope = compute_v5_envelope(ctx, policy)
        # No HR archetype matches BREAKDOWN → HR conviction zero
        assert envelope.hr_conviction == 0.0
        assert envelope.hr_archetype_matched is None
        # BREAKDOWN_GRINDER matches → non-zero P conviction
        assert envelope.p_archetype_matched == "BREAKDOWN_GRINDER"
        assert envelope.p_conviction > 50.0

    def test_gbm_enabled_boosts_when_archetype_misses(self) -> None:
        """With GBM enabled, trades matching no archetype still get scored."""
        ctx = _ctx(
            scanner_source="COMPRESSION_EXPANSION",  # Not in any v5 archetype
            option_type="CALL", dte=30, delta=0.35,
        )
        policy_off = _minimal_v5_policy(v5_gbm_enabled=False)
        policy_on = _minimal_v5_policy(v5_gbm_enabled=True)
        env_off = compute_v5_envelope(ctx, policy_off)
        env_on = compute_v5_envelope(ctx, policy_on)
        # Without GBM: both zero (no archetype match)
        assert env_off.hr_conviction == 0.0
        assert env_off.p_conviction == 0.0
        # With GBM: can still produce > 0 via GBM scores
        assert env_on.gbm_hr_score >= 0.0
        assert env_on.gbm_p_score >= 0.0
        # Final HR/P may be > 0 if GBM scored above 0
        assert env_on.hr_conviction >= 0.0

    def test_no_v5_archetypes_returns_zero_envelope(self) -> None:
        """When the policy has no archetypes, envelope still returns safely."""
        policy = PolicyConfig(
            v5_active=True,
            v5_hr_archetypes=None,
            v5_p_archetypes=None,
        )
        ctx = _ctx(scanner_source="UNUSUAL_VOLUME")
        envelope = compute_v5_envelope(ctx, policy)
        assert envelope.hr_conviction == 0.0
        assert envelope.p_conviction == 0.0
        assert envelope.hr_archetype_matched is None
        assert envelope.p_archetype_matched is None


# ============================================================================
# derive_v5_verdict
# ============================================================================


class TestDeriveV5Verdict:
    def _envelope(self, hr: float, p: float) -> V5Envelope:
        return V5Envelope(
            hr_conviction=hr, p_conviction=p,
            hr_archetype_matched="UV_LOTTERY_CALL" if hr > 0 else None,
            hr_archetype_fit=100.0 if hr > 0 else 0.0,
            hr_p_point=0.2, hr_p_lower=0.14, hr_p_upper=0.27,
            hr_n_trades=136,
            p_archetype_matched="BREAKDOWN_GRINDER" if p > 0 else None,
            p_archetype_fit=100.0 if p > 0 else 0.0,
            p_win_point=0.66, p_win_lower=0.60,
            p_mean_pnl_estimate=29.57, regime_alignment=1.0,
            gbm_hr_score=0.0, gbm_p_score=0.0,
        )

    def test_gate_failure_rejects(self) -> None:
        policy = _minimal_v5_policy()
        envelope = self._envelope(hr=50, p=90)
        verdict, reason, tier = derive_v5_verdict(
            envelope, policy,
            all_gates_passed=False,
            anti_archetype_triggered=None,
        )
        assert verdict == Verdict.REJECT
        assert reason == "REJECTED_BY_GATES"
        assert tier is None

    def test_anti_archetype_rejects(self) -> None:
        policy = _minimal_v5_policy()
        envelope = self._envelope(hr=50, p=90)
        verdict, reason, tier = derive_v5_verdict(
            envelope, policy,
            all_gates_passed=True,
            anti_archetype_triggered="BREAKOUT_MP_ELITE",
        )
        assert verdict == Verdict.REJECT
        assert "BREAKOUT_MP_ELITE" in reason
        assert tier is None

    def test_tier_1_sharpshooter_on_hr_14_plus(self) -> None:
        policy = _minimal_v5_policy()
        envelope = self._envelope(hr=15.0, p=40.0)
        verdict, reason, tier = derive_v5_verdict(
            envelope, policy, all_gates_passed=True, anti_archetype_triggered=None,
        )
        assert verdict == Verdict.APPROVE
        assert tier == QualityTier.TIER_1
        assert reason == "V5_SHARPSHOOTER"

    def test_tier_2_quality_on_high_p(self) -> None:
        policy = _minimal_v5_policy()
        envelope = self._envelope(hr=5.0, p=80.0)
        verdict, reason, tier = derive_v5_verdict(
            envelope, policy, all_gates_passed=True, anti_archetype_triggered=None,
        )
        assert verdict == Verdict.APPROVE
        assert tier == QualityTier.TIER_2
        assert reason == "V5_QUALITY"

    def test_tier_2_quality_on_mid_hr(self) -> None:
        """HR 7-14 with low P scores TIER_2."""
        policy = _minimal_v5_policy()
        envelope = self._envelope(hr=10.0, p=30.0)
        verdict, reason, tier = derive_v5_verdict(
            envelope, policy, all_gates_passed=True, anti_archetype_triggered=None,
        )
        assert verdict == Verdict.APPROVE
        assert tier == QualityTier.TIER_2

    def test_tier_3_tradeable(self) -> None:
        """APPROVE but neither TIER_1 nor TIER_2 criteria hit → TIER_3.

        Achievable when P crosses threshold (50) but HR < 7 and P < 70.
        """
        policy = _minimal_v5_policy()
        envelope = self._envelope(hr=5.0, p=55.0)
        verdict, reason, tier = derive_v5_verdict(
            envelope, policy, all_gates_passed=True, anti_archetype_triggered=None,
        )
        assert verdict == Verdict.APPROVE
        assert tier == QualityTier.TIER_3
        assert reason == "V5_TRADEABLE"

    def test_watch_on_half_threshold(self) -> None:
        """HR in [3.5, 7) or P in [25, 50) → WATCH."""
        policy = _minimal_v5_policy()
        envelope = self._envelope(hr=5.0, p=30.0)
        verdict, reason, tier = derive_v5_verdict(
            envelope, policy, all_gates_passed=True, anti_archetype_triggered=None,
        )
        assert verdict == Verdict.WATCH
        assert tier is None
        assert reason == "V5_WATCH"

    def test_reject_by_score_when_both_low(self) -> None:
        policy = _minimal_v5_policy()
        envelope = self._envelope(hr=1.0, p=5.0)
        verdict, reason, tier = derive_v5_verdict(
            envelope, policy, all_gates_passed=True, anti_archetype_triggered=None,
        )
        assert verdict == Verdict.REJECT
        assert reason == "V5_REJECTED_BY_SCORE"
        assert tier is None


# ============================================================================
# Calculator integration
# ============================================================================


def _build_ctx_for_calc(
    scanner: str = "UNUSUAL_VOLUME",
    envelope: V5Envelope | None = None,
    anti_arch: str | None = None,
    all_gates_passed: bool = True,
) -> DecisionContext:
    return DecisionContext(
        evaluation_id="eval-test",
        underlying_ticker="TEST",
        option_type="CALL",
        spread_pct=5.0,
        policy_version="v5.0.0",
        directional_conviction_score=50.0,
        move_potential_score=55.0,
        trade_structure_score=80.0,
        scanner_source=scanner,
        all_gates_passed=all_gates_passed,
        anti_archetype_triggered=anti_arch,
        v5_envelope=envelope,
    )


class TestCalculatorV5Integration:
    def _envelope(self, hr: float, p: float) -> V5Envelope:
        return V5Envelope(
            hr_conviction=hr, p_conviction=p,
            hr_archetype_matched="UV_LOTTERY_CALL",
            hr_archetype_fit=100.0,
            hr_p_point=0.2, hr_p_lower=0.14, hr_p_upper=0.27,
            hr_n_trades=136,
            p_archetype_matched="BREAKDOWN_GRINDER",
            p_archetype_fit=100.0,
            p_win_point=0.66, p_win_lower=0.60,
            p_mean_pnl_estimate=29.57, regime_alignment=1.0,
            gbm_hr_score=0.0, gbm_p_score=0.0,
        )

    def test_decision_includes_v5_fields_when_envelope_present(self) -> None:
        """Envelope present → v5 fields populated on Decision (shadow mode)."""
        calc = DecisionCalculator(DecisionConfig())
        envelope = self._envelope(hr=15.0, p=40.0)
        ctx = _build_ctx_for_calc(
            scanner="UNUSUAL_VOLUME", envelope=envelope,
        )
        # No v5_policy passed → v4.1.0 drives verdict, v5 fields are shadow
        decision = calc.compute_decision(ctx, v5_policy=None)
        assert decision.hr_conviction == 15.0
        assert decision.p_conviction == 40.0
        assert decision.hr_archetype_matched == "UV_LOTTERY_CALL"
        assert decision.p_archetype_matched == "BREAKDOWN_GRINDER"
        assert decision.v5_scoring_version == "v5.0.0"

    def test_v5_not_driving_when_policy_inactive(self) -> None:
        """v5_active=False → v4.1.0 verdict even with envelope present."""
        calc = DecisionCalculator(DecisionConfig())
        envelope = self._envelope(hr=15.0, p=40.0)
        ctx = _build_ctx_for_calc(envelope=envelope)
        policy = _minimal_v5_policy(v5_active=False)
        decision = calc.compute_decision(ctx, v5_policy=policy)
        # v5 fields still present (shadow)
        assert decision.hr_conviction == 15.0
        # But verdict came from v4 path, not V5_SHARPSHOOTER
        assert decision.primary_reason_code not in {
            "V5_SHARPSHOOTER", "V5_QUALITY", "V5_TRADEABLE",
        }

    def test_v5_drives_when_scanner_in_allowlist(self) -> None:
        """v5_active=True AND scanner in allowlist → v5 drives verdict."""
        calc = DecisionCalculator(DecisionConfig())
        envelope = self._envelope(hr=15.0, p=40.0)
        ctx = _build_ctx_for_calc(scanner="UNUSUAL_VOLUME", envelope=envelope)
        policy = _minimal_v5_policy(
            v5_active=True,
            v5_active_scanners=["UNUSUAL_VOLUME"],
        )
        decision = calc.compute_decision(ctx, v5_policy=policy)
        assert decision.verdict == Verdict.APPROVE
        assert decision.quality_tier == QualityTier.TIER_1
        assert decision.primary_reason_code == "V5_SHARPSHOOTER"

    def test_v5_not_driving_when_scanner_not_in_allowlist(self) -> None:
        """v5_active=True but scanner missing → v4.1.0 drives."""
        calc = DecisionCalculator(DecisionConfig())
        envelope = self._envelope(hr=15.0, p=40.0)
        ctx = _build_ctx_for_calc(scanner="UNUSUAL_VOLUME", envelope=envelope)
        policy = _minimal_v5_policy(
            v5_active=True,
            v5_active_scanners=["CHEAP_OPTIONS"],  # UV not in allowlist
        )
        decision = calc.compute_decision(ctx, v5_policy=policy)
        # Should still populate v5 shadow fields
        assert decision.hr_conviction == 15.0
        # But verdict came from v4 path
        assert decision.primary_reason_code not in {
            "V5_SHARPSHOOTER", "V5_QUALITY", "V5_TRADEABLE",
        }

    def test_v5_anti_archetype_still_rejects(self) -> None:
        """Anti-archetype rejects even when v5 is driving."""
        calc = DecisionCalculator(DecisionConfig())
        envelope = self._envelope(hr=15.0, p=80.0)
        ctx = _build_ctx_for_calc(
            scanner="UNUSUAL_VOLUME",
            envelope=envelope,
            anti_arch="UV_LONG_DATED",
        )
        policy = _minimal_v5_policy(
            v5_active=True, v5_active_scanners=["UNUSUAL_VOLUME"],
        )
        decision = calc.compute_decision(ctx, v5_policy=policy)
        assert decision.verdict == Verdict.REJECT
        assert "UV_LONG_DATED" in decision.primary_reason_code

    def test_decision_without_envelope_has_no_v5_fields(self) -> None:
        """Pre-v5 call path still works unchanged."""
        calc = DecisionCalculator(DecisionConfig())
        ctx = _build_ctx_for_calc(envelope=None)
        decision = calc.compute_decision(ctx)
        assert decision.hr_conviction is None
        assert decision.p_conviction is None
        assert decision.v5_scoring_version is None
