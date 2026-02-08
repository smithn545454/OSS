"""Boundary value tests using @pytest.mark.parametrize.

Tests every threshold in the system at exactly the boundary:
  threshold - epsilon, threshold, threshold + epsilon

This catches off-by-one errors and >= vs > mistakes that hand-picked
tests miss.
"""

from __future__ import annotations

import pytest

from app.core.schemas import (
    DecisionConfig,
    GateConfig,
    GateOperator,
    OptionType,
    QualityTier,
    Verdict,
)
from app.decision.calculator import DecisionCalculator
from app.gates.models import GateContext


# ============================================================================
# Shared gate context builder
# ============================================================================

def _gate_ctx(**overrides) -> GateContext:
    """Build a GateContext with sensible defaults, overriding specified fields."""
    defaults = dict(
        evaluation_id="boundary-test",
        underlying_ticker="TEST", option_ticker="O:TEST",
        option_type="CALL",
        dte=30, strike=100.0, underlying_price=100.0,
        bid=5.0, ask=5.4, mid=5.2, spread_pct=5.0,
        delta=0.50, gamma=0.03, theta=-0.05, vega=0.20, iv=0.30,
        open_interest=500, volume=100,
        time_adjusted_feasibility=0.50,
        iv_percentile=50.0, theta_pct=2.0,
        iv_rv_ratio=1.0, feasibility_ratio=0.50,
        trend_aligned_bullish=True,
        return_5d=2.0, return_20d=4.0,
    )
    defaults.update(overrides)
    return GateContext(**defaults)


# ============================================================================
# Decision Boundary Tests
# ============================================================================


class TestDecisionBoundaries:
    """Test verdict determination at exact threshold boundaries."""

    @pytest.mark.parametrize("score,expected_verdict", [
        (64.99, Verdict.REJECT),
        (65.00, Verdict.WATCH),
        (65.01, Verdict.WATCH),
        (70.00, Verdict.WATCH),
        (74.99, Verdict.WATCH),
        (75.00, Verdict.APPROVE),
        (75.01, Verdict.APPROVE),
        (85.00, Verdict.APPROVE),
        (100.0, Verdict.APPROVE),
    ])
    def test_verdict_at_default_boundaries(self, score, expected_verdict):
        """Test verdict at default approve=75, watch=65 boundaries."""
        calc = DecisionCalculator()
        verdict, _ = calc.determine_verdict(score, all_gates_passed=True)
        assert verdict == expected_verdict, (
            f"Score {score}: expected {expected_verdict}, got {verdict}"
        )

    @pytest.mark.parametrize("score,expected_verdict", [
        (79.99, Verdict.WATCH),
        (80.00, Verdict.APPROVE),
        (80.01, Verdict.APPROVE),
        (59.99, Verdict.REJECT),
        (60.00, Verdict.WATCH),
        (60.01, Verdict.WATCH),
    ])
    def test_verdict_at_custom_boundaries(self, score, expected_verdict):
        """Test with custom thresholds (approve=80, watch=60)."""
        config = DecisionConfig(approve_threshold=80, watch_threshold=60)
        calc = DecisionCalculator(config)
        verdict, _ = calc.determine_verdict(score, all_gates_passed=True)
        assert verdict == expected_verdict

    @pytest.mark.parametrize("score,d_score,v_score,s_score,spread,expected_tier", [
        # TIER_1: score >= 85, all pillars >= 70, spread <= 5%
        (85.0, 70, 70, 70, 5.0, QualityTier.TIER_1),
        (84.99, 70, 70, 70, 5.0, QualityTier.TIER_2),  # Score just below TIER_1
        (85.0, 69, 70, 70, 5.0, QualityTier.TIER_2),   # One pillar below
        (85.0, 70, 70, 70, 5.01, QualityTier.TIER_2),  # Spread too wide
        # TIER_2: all pillars >= 55
        (75.0, 55, 55, 55, 8.0, QualityTier.TIER_2),
        (75.0, 54, 55, 55, 8.0, QualityTier.TIER_3),   # One pillar below TIER_2
        # TIER_3: approved but doesn't meet TIER_2
        (75.0, 40, 55, 55, 8.0, QualityTier.TIER_3),
    ])
    def test_quality_tier_at_boundaries(self, score, d_score, v_score, s_score, spread, expected_tier):
        """Test quality tier assignment at exact boundaries."""
        calc = DecisionCalculator()
        tier = calc.assign_quality_tier(score, d_score, v_score, s_score, spread)
        assert tier == expected_tier, (
            f"Score={score}, pillars=({d_score},{v_score},{s_score}), spread={spread}: "
            f"expected {expected_tier}, got {tier}"
        )


# ============================================================================
# Gate Boundary Tests
# ============================================================================


class TestGateBoundaries:
    """Test every gate at its exact threshold boundary."""

    @pytest.mark.parametrize("oi,expected", [
        (0, False),
        (299, False),
        (300, True),
        (301, True),
        (999999, True),
    ])
    def test_min_open_interest_boundary(self, oi, expected):
        from app.gates.gates import check_min_open_interest
        result = check_min_open_interest(_gate_ctx(open_interest=oi), GateConfig())
        assert result.passed is expected, f"OI={oi}: expected {expected}"

    @pytest.mark.parametrize("vol,expected", [
        (0, False),
        (74, False),
        (75, True),
        (76, True),
        (10000, True),
    ])
    def test_min_volume_boundary(self, vol, expected):
        from app.gates.gates import check_min_volume
        result = check_min_volume(_gate_ctx(volume=vol), GateConfig())
        assert result.passed is expected, f"Volume={vol}: expected {expected}"

    @pytest.mark.parametrize("spread,expected", [
        (0.0, True),
        (7.99, True),
        (8.0, True),
        (8.01, False),
        (50.0, False),
    ])
    def test_max_spread_pct_boundary(self, spread, expected):
        from app.gates.gates import check_max_spread_pct
        result = check_max_spread_pct(_gate_ctx(spread_pct=spread), GateConfig())
        assert result.passed is expected, f"Spread={spread}: expected {expected}"

    @pytest.mark.parametrize("dte,expected", [
        (0, False),
        (6, False),
        (7, True),
        (8, True),
        (60, True),
        (119, True),
        (120, True),
        (121, False),
        (365, False),
    ])
    def test_dte_range_boundary(self, dte, expected):
        from app.gates.gates import check_dte_range
        result = check_dte_range(_gate_ctx(dte=dte), GateConfig())
        assert result.passed is expected, f"DTE={dte}: expected {expected}"

    @pytest.mark.parametrize("taf,expected", [
        (0.0, True),
        (1.24, True),
        (1.25, True),
        (1.26, False),
        (5.0, False),
    ])
    def test_move_sufficiency_boundary(self, taf, expected):
        from app.gates.gates import check_move_sufficiency
        result = check_move_sufficiency(
            _gate_ctx(time_adjusted_feasibility=taf), GateConfig()
        )
        assert result.passed is expected, f"TAF={taf}: expected {expected}"

    @pytest.mark.parametrize("iv_pct,expected", [
        (0, True),
        (84, True),
        (85, True),
        (86, False),
        (100, False),
    ])
    def test_iv_percentile_max_boundary(self, iv_pct, expected):
        from app.gates.gates import check_iv_percentile_max
        result = check_iv_percentile_max(
            _gate_ctx(iv_percentile=float(iv_pct)), GateConfig()
        )
        assert result.passed is expected, f"IV_pct={iv_pct}: expected {expected}"

    @pytest.mark.parametrize("theta_pct,expected", [
        (0.0, True),
        (3.99, True),
        (4.0, True),
        (4.01, False),
        (10.0, False),
    ])
    def test_theta_burden_boundary(self, theta_pct, expected):
        from app.gates.gates import check_theta_burden_max
        result = check_theta_burden_max(
            _gate_ctx(theta_pct=theta_pct), GateConfig()
        )
        assert result.passed is expected, f"Theta_pct={theta_pct}: expected {expected}"

    @pytest.mark.parametrize("ratio,expected", [
        (0.5, True),
        (1.49, True),
        (1.5, True),
        (1.51, False),
        (3.0, False),
    ])
    def test_iv_rv_ratio_boundary(self, ratio, expected):
        from app.gates.gates import check_iv_rv_ratio
        result = check_iv_rv_ratio(
            _gate_ctx(iv_rv_ratio=ratio), GateConfig()
        )
        assert result.passed is expected, f"IV/RV={ratio}: expected {expected}"

    @pytest.mark.parametrize("fr,expected", [
        (0.0, True),
        (1.49, True),
        (1.5, True),
        (1.51, False),
        (5.0, False),
    ])
    def test_expected_move_boundary(self, fr, expected):
        from app.gates.gates import check_expected_move
        result = check_expected_move(
            _gate_ctx(feasibility_ratio=fr), GateConfig()
        )
        assert result.passed is expected, f"FR={fr}: expected {expected}"

    @pytest.mark.parametrize("delta,expected", [
        (0.19, False),
        (0.20, True),
        (0.50, True),
        (0.70, True),
        (0.71, False),
    ])
    def test_delta_range_boundary(self, delta, expected):
        from app.gates.gates import check_delta_range
        result = check_delta_range(
            _gate_ctx(delta=delta), GateConfig()
        )
        assert result.passed is expected, f"Delta={delta}: expected {expected}"

    @pytest.mark.parametrize("mid,expected", [
        (0.50, True),
        (19.99, True),
        (20.0, True),
        (20.01, False),
        (100.0, False),
    ])
    def test_max_loss_boundary(self, mid, expected):
        from app.gates.gates import check_max_loss
        result = check_max_loss(
            _gate_ctx(mid=mid), GateConfig()
        )
        assert result.passed is expected, f"Mid={mid}: expected {expected}"

    @pytest.mark.parametrize("score,expected", [
        (74.99, False),
        (75.0, True),
        (75.01, True),
        (100.0, True),
    ])
    def test_combined_score_boundary(self, score, expected):
        from app.gates.gates import check_combined_score
        result = check_combined_score(
            _gate_ctx(combined_score=score), GateConfig()
        )
        assert result.passed is expected, f"Combined={score}: expected {expected}"

    @pytest.mark.parametrize("scores,expected", [
        ({"DIRECTIONAL": 60, "VOLATILITY": 60, "STRUCTURE": 60}, True),
        ({"DIRECTIONAL": 59, "VOLATILITY": 60, "STRUCTURE": 60}, False),
        ({"DIRECTIONAL": 60, "VOLATILITY": 59, "STRUCTURE": 60}, False),
        ({"DIRECTIONAL": 100, "VOLATILITY": 100, "STRUCTURE": 100}, True),
    ])
    def test_pillar_minimum_boundary(self, scores, expected):
        from app.gates.gates import check_pillar_minimum
        result = check_pillar_minimum(
            _gate_ctx(pillar_scores=scores), GateConfig()
        )
        assert result.passed is expected, f"Pillars={scores}: expected {expected}"

    @pytest.mark.parametrize("scores,expected", [
        # Spread = max - min <= 30
        ({"DIRECTIONAL": 70, "VOLATILITY": 70, "STRUCTURE": 70}, True),   # spread=0
        ({"DIRECTIONAL": 85, "VOLATILITY": 55, "STRUCTURE": 70}, True),   # spread=30
        ({"DIRECTIONAL": 86, "VOLATILITY": 55, "STRUCTURE": 70}, False),  # spread=31
        ({"DIRECTIONAL": 50, "VOLATILITY": 80, "STRUCTURE": 90}, False),  # spread=40
    ])
    def test_confidence_interval_boundary(self, scores, expected):
        from app.gates.gates import check_confidence_interval
        result = check_confidence_interval(
            _gate_ctx(pillar_scores=scores), GateConfig()
        )
        assert result.passed is expected, (
            f"Pillar spread={max(scores.values())-min(scores.values())}: expected {expected}"
        )
