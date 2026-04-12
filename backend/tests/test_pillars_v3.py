"""Unit tests for Policy v3.0.0 pillar calculators.

Verifies:
- Each pillar calculator produces scores in [0, 100]
- Direction-agnostic scoring: CALL and PUT with same |delta| score identically
- PillarCalculator integrates all three pillars
- compute_final_score uses the configured weights
- compute_pillars handles missing features gracefully
"""

from __future__ import annotations

import pytest

from app.core.schemas import PillarConfig, PillarId
from app.pillars.calculator import PillarCalculator, compute_final_score
from app.pillars.models import ScoringContext
from app.pillars.premium_leverage import compute_premium_leverage_pillar
from app.pillars.setup_quality import compute_setup_quality_pillar
from app.pillars.underlying_behavior import compute_underlying_behavior_pillar


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def v3_pillar_config() -> PillarConfig:
    """Load the default Policy v3.0.0 pillar config from the seed JSON."""
    return PillarConfig()


def make_context(
    *,
    option_type: str = "CALL",
    delta: float = 0.10,
    iv: float = 0.30,
    iv_percentile: float = 10.0,
    iv_rv_ratio: float = 0.92,
    adx_14: float = 14.0,
    rv20: float = 0.40,
    feasibility_ratio: float = 0.25,
    time_adjusted_feasibility: float = 0.20,
    atr14_pct: float = 3.5,
    volume: int = 150,
    open_interest: int = 300,
    convergence_count: int = 2,
    dte_bucket: str = "A",
) -> ScoringContext:
    """Build a ScoringContext with sensible 'good' defaults."""
    return ScoringContext(
        evaluation_id="test-eval",
        underlying_ticker="TEST",
        option_type=option_type,
        dte_bucket=dte_bucket,
        scanner_triggers=["BREAKOUT"],
        convergence_count=convergence_count,
        close=180.0,
        atr14_pct=atr14_pct,
        iv=iv,
        iv_percentile=iv_percentile,
        iv_rv_ratio=iv_rv_ratio,
        rv20=rv20,
        feasibility_ratio=feasibility_ratio,
        time_adjusted_feasibility=time_adjusted_feasibility,
        delta=delta,
        dte=14,
        open_interest=open_interest,
        volume=volume,
        adx_14=adx_14,
    )


# ============================================================================
# Pillar 1: Premium Leverage
# ============================================================================


class TestPremiumLeverage:
    def test_score_in_range(self, v3_pillar_config):
        ctx = make_context()
        result = compute_premium_leverage_pillar(
            ctx, v3_pillar_config.premium_leverage
        )
        assert result.pillar_id == PillarId.PREMIUM_LEVERAGE
        assert 0 <= result.score <= 100

    def test_direction_agnostic_same_abs_delta(self, v3_pillar_config):
        """CALL(+0.40) and PUT(-0.40) must produce the same Premium Leverage score."""
        ctx_call = make_context(option_type="CALL", delta=0.40)
        ctx_put = make_context(option_type="PUT", delta=-0.40)

        call_result = compute_premium_leverage_pillar(
            ctx_call, v3_pillar_config.premium_leverage
        )
        put_result = compute_premium_leverage_pillar(
            ctx_put, v3_pillar_config.premium_leverage
        )
        assert call_result.score == pytest.approx(put_result.score, rel=1e-6)

    def test_wrong_pillar_id_raises(self, v3_pillar_config):
        ctx = make_context()
        with pytest.raises(ValueError, match="Expected pillar_id"):
            compute_premium_leverage_pillar(ctx, v3_pillar_config.underlying_behavior)

    def test_low_iv_scores_higher_than_extreme_iv(self, v3_pillar_config):
        """IV in the moderate zone should score higher than extreme high IV."""
        ctx_moderate = make_context(iv=0.30, iv_percentile=10)
        ctx_extreme = make_context(iv=1.20, iv_percentile=95)

        moderate = compute_premium_leverage_pillar(
            ctx_moderate, v3_pillar_config.premium_leverage
        )
        extreme = compute_premium_leverage_pillar(
            ctx_extreme, v3_pillar_config.premium_leverage
        )
        assert moderate.score > extreme.score


# ============================================================================
# Pillar 2: Underlying Behavior
# ============================================================================


class TestUnderlyingBehavior:
    def test_score_in_range(self, v3_pillar_config):
        ctx = make_context()
        result = compute_underlying_behavior_pillar(
            ctx, v3_pillar_config.underlying_behavior
        )
        assert result.pillar_id == PillarId.UNDERLYING_BEHAVIOR
        assert 0 <= result.score <= 100

    def test_missing_adx_falls_back_to_other_subscores(self, v3_pillar_config):
        """When ADX is missing, weight should redistribute and score is valid."""
        ctx = make_context(adx_14=None)
        result = compute_underlying_behavior_pillar(
            ctx, v3_pillar_config.underlying_behavior
        )
        assert 0 <= result.score <= 100

    def test_moderate_rv_scores_higher_than_extreme_rv(self, v3_pillar_config):
        ctx_moderate = make_context(rv20=0.40)
        ctx_extreme = make_context(rv20=1.5)

        moderate = compute_underlying_behavior_pillar(
            ctx_moderate, v3_pillar_config.underlying_behavior
        )
        extreme = compute_underlying_behavior_pillar(
            ctx_extreme, v3_pillar_config.underlying_behavior
        )
        assert moderate.score > extreme.score


# ============================================================================
# Pillar 3: Setup Quality
# ============================================================================


class TestSetupQuality:
    def test_score_in_range(self, v3_pillar_config):
        ctx = make_context()
        result = compute_setup_quality_pillar(
            ctx, v3_pillar_config.setup_quality
        )
        assert result.pillar_id == PillarId.SETUP_QUALITY
        assert 0 <= result.score <= 100

    def test_bucket_a_scores_higher_than_bucket_d(self, v3_pillar_config):
        """DTE bucket A (short-term) should score higher than D (long-term)."""
        ctx_a = make_context(dte_bucket="A")
        ctx_d = make_context(dte_bucket="D")

        a = compute_setup_quality_pillar(ctx_a, v3_pillar_config.setup_quality)
        d = compute_setup_quality_pillar(ctx_d, v3_pillar_config.setup_quality)
        assert a.score > d.score

    def test_convergence_increases_score(self, v3_pillar_config):
        ctx_single = make_context(convergence_count=1)
        ctx_multi = make_context(convergence_count=3)

        single = compute_setup_quality_pillar(ctx_single, v3_pillar_config.setup_quality)
        multi = compute_setup_quality_pillar(ctx_multi, v3_pillar_config.setup_quality)
        assert multi.score >= single.score


# ============================================================================
# PillarCalculator integration
# ============================================================================


class TestPillarCalculatorIntegration:
    def test_compute_pillars_returns_three_in_order(self, v3_pillar_config):
        calc = PillarCalculator(v3_pillar_config)
        ctx = make_context()
        results = calc.compute_pillars(
            evaluation=None, feature_set=None, opportunity=None, context=ctx
        )
        assert len(results) == 3
        assert results[0].pillar_id == PillarId.PREMIUM_LEVERAGE
        assert results[1].pillar_id == PillarId.UNDERLYING_BEHAVIOR
        assert results[2].pillar_id == PillarId.SETUP_QUALITY

    def test_good_context_produces_high_composite(self, v3_pillar_config):
        calc = PillarCalculator(v3_pillar_config)
        ctx = make_context()  # good defaults
        results = calc.compute_pillars(
            evaluation=None, feature_set=None, opportunity=None, context=ctx
        )
        scores = {r.pillar_id: r.score for r in results}
        composite = compute_final_score(
            scores[PillarId.PREMIUM_LEVERAGE],
            scores[PillarId.UNDERLYING_BEHAVIOR],
            scores[PillarId.SETUP_QUALITY],
            v3_pillar_config,
        )
        assert composite >= 70.0  # Good setup should produce APPROVE-worthy score

    def test_bad_context_produces_low_composite(self, v3_pillar_config):
        calc = PillarCalculator(v3_pillar_config)
        ctx = make_context(
            delta=0.95,  # Deep ITM
            iv=1.15,  # Extreme high IV
            iv_percentile=90,
            iv_rv_ratio=3.0,
            adx_14=None,
            rv20=1.8,  # Very high RV
            feasibility_ratio=0.005,  # Very hard to reach
            time_adjusted_feasibility=0.002,
            atr14_pct=0.5,  # Very low ATR
            volume=50000,  # High volume
            open_interest=90000,  # High OI
            convergence_count=1,
            dte_bucket="D",  # Longest DTE
        )
        results = calc.compute_pillars(
            evaluation=None, feature_set=None, opportunity=None, context=ctx
        )
        scores = {r.pillar_id: r.score for r in results}
        composite = compute_final_score(
            scores[PillarId.PREMIUM_LEVERAGE],
            scores[PillarId.UNDERLYING_BEHAVIOR],
            scores[PillarId.SETUP_QUALITY],
            v3_pillar_config,
        )
        assert composite < 40.0  # Bad setup should be rejected by score

    def test_call_put_symmetry(self, v3_pillar_config):
        """Full composite score should be identical for CALL/PUT with same |delta|."""
        calc = PillarCalculator(v3_pillar_config)
        ctx_call = make_context(option_type="CALL", delta=0.35)
        ctx_put = make_context(option_type="PUT", delta=-0.35)

        call_results = calc.compute_pillars(
            evaluation=None, feature_set=None, opportunity=None, context=ctx_call
        )
        put_results = calc.compute_pillars(
            evaluation=None, feature_set=None, opportunity=None, context=ctx_put
        )
        call_scores = {r.pillar_id: r.score for r in call_results}
        put_scores = {r.pillar_id: r.score for r in put_results}

        assert call_scores[PillarId.PREMIUM_LEVERAGE] == pytest.approx(
            put_scores[PillarId.PREMIUM_LEVERAGE]
        )
        assert call_scores[PillarId.UNDERLYING_BEHAVIOR] == pytest.approx(
            put_scores[PillarId.UNDERLYING_BEHAVIOR]
        )
        assert call_scores[PillarId.SETUP_QUALITY] == pytest.approx(
            put_scores[PillarId.SETUP_QUALITY]
        )


# ============================================================================
# compute_final_score formula
# ============================================================================


class TestFinalScoreFormula:
    def test_uses_v31_default_weights(self, v3_pillar_config):
        # v3.1.0 defaults: 0.25 / 0.35 / 0.40
        # 0.25 * 80 + 0.35 * 70 + 0.40 * 90
        # = 20 + 24.5 + 36 = 80.5
        final = compute_final_score(80.0, 70.0, 90.0, v3_pillar_config)
        assert final == pytest.approx(80.5, abs=0.01)

    def test_clamps_to_0_100(self, v3_pillar_config):
        assert compute_final_score(150.0, 150.0, 150.0, v3_pillar_config) == 100.0
        assert compute_final_score(-50.0, -50.0, -50.0, v3_pillar_config) == 0.0

    def test_no_config_uses_defaults(self):
        """compute_final_score(...) without a config should still work."""
        final = compute_final_score(80.0, 70.0, 90.0)
        assert 0.0 <= final <= 100.0


# ============================================================================
# Per-scanner weights (PillarConfig.scanner_weights)
# ============================================================================


class TestPerScannerWeights:
    def test_scanner_specific_weights(self):
        """BREAKOUT scanner uses UB-heavy weights when configured."""
        from app.core.schemas import PillarWeights

        config = PillarConfig(
            scanner_weights={
                "BREAKOUT": PillarWeights(
                    premium_leverage=0.15,
                    underlying_behavior=0.80,
                    setup_quality=0.05,
                ),
            }
        )
        # Global: 0.25*80 + 0.35*70 + 0.40*90 = 80.5
        global_score = compute_final_score(80.0, 70.0, 90.0, config)
        assert global_score == pytest.approx(80.5, abs=0.01)

        # BREAKOUT: 0.15*80 + 0.80*70 + 0.05*90 = 12+56+4.5 = 72.5
        breakout_score = compute_final_score(
            80.0, 70.0, 90.0, config, scanner_source="BREAKOUT"
        )
        assert breakout_score == pytest.approx(72.5, abs=0.01)

    def test_scanner_suffix_stripped(self):
        """Scanner names with _SCANNER suffix are normalised."""
        from app.core.schemas import PillarWeights

        config = PillarConfig(
            scanner_weights={
                "UNUSUAL_VOLUME": PillarWeights(
                    premium_leverage=0.45,
                    underlying_behavior=0.15,
                    setup_quality=0.40,
                ),
            }
        )
        score = compute_final_score(
            80.0, 70.0, 90.0, config, scanner_source="UNUSUAL_VOLUME_SCANNER"
        )
        # 0.45*80 + 0.15*70 + 0.40*90 = 36+10.5+36 = 82.5
        assert score == pytest.approx(82.5, abs=0.01)

    def test_unknown_scanner_falls_back_to_global(self):
        """Unknown scanner → global weights."""
        from app.core.schemas import PillarWeights

        config = PillarConfig(
            scanner_weights={
                "BREAKOUT": PillarWeights(
                    premium_leverage=0.15,
                    underlying_behavior=0.80,
                    setup_quality=0.05,
                ),
            }
        )
        score = compute_final_score(
            80.0, 70.0, 90.0, config, scanner_source="SOME_NEW_SCANNER"
        )
        # Falls back to global: 0.25*80 + 0.35*70 + 0.40*90 = 80.5
        assert score == pytest.approx(80.5, abs=0.01)

    def test_none_scanner_uses_global(self):
        """None scanner_source → global weights."""
        from app.core.schemas import PillarWeights

        config = PillarConfig(
            scanner_weights={
                "BREAKOUT": PillarWeights(
                    premium_leverage=0.15,
                    underlying_behavior=0.80,
                    setup_quality=0.05,
                ),
            }
        )
        score = compute_final_score(80.0, 70.0, 90.0, config, scanner_source=None)
        assert score == pytest.approx(80.5, abs=0.01)

    def test_scanner_weights_validation(self):
        """scanner_weights entries must sum to 1.0."""
        from app.core.schemas import PillarWeights
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PillarConfig(
                scanner_weights={
                    "BREAKOUT": PillarWeights(
                        premium_leverage=0.50,
                        underlying_behavior=0.80,
                        setup_quality=0.05,
                    ),
                }
            )

    def test_scanner_weights_none_deserializes(self):
        """PillarConfig with scanner_weights=None deserializes correctly."""
        config = PillarConfig(scanner_weights=None)
        assert config.scanner_weights is None
        # get_weights still returns global
        assert config.get_weights("BREAKOUT") is config.weights
