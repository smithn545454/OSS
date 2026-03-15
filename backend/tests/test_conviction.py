"""Tests for backend conviction score calculator.

Validates parity with frontend/src/lib/convictionScore.ts.
"""

from app.scoring.conviction import (
    DEFAULT_EV_BENCHMARK,
    calculate_composite_pillar,
    calculate_conviction_score,
    determine_urgency,
    get_convergence_bonus,
    get_time_sensitivity_boost,
    normalize_ev,
)


class TestNormalizeEV:
    def test_negative_ev(self):
        assert normalize_ev(-5.0) == 0.0

    def test_zero_ev(self):
        assert normalize_ev(0.0) == 0.0

    def test_positive_ev(self):
        # $7.50 / $15 benchmark = 50%
        assert normalize_ev(7.5) == 50.0

    def test_full_benchmark(self):
        assert normalize_ev(15.0) == 100.0

    def test_above_benchmark_capped(self):
        assert normalize_ev(30.0) == 100.0

    def test_custom_benchmark(self):
        assert normalize_ev(10.0, benchmark=20.0) == 50.0


class TestCompositePillar:
    def test_all_zeros(self):
        assert calculate_composite_pillar({"DIRECTIONAL": 0, "VOLATILITY": 0, "STRUCTURE": 0}) == 0.0

    def test_average(self):
        scores = {"DIRECTIONAL": 60.0, "VOLATILITY": 80.0, "STRUCTURE": 70.0}
        assert calculate_composite_pillar(scores) == 70.0

    def test_missing_keys_default_zero(self):
        assert calculate_composite_pillar({}) == 0.0

    def test_partial_keys(self):
        assert calculate_composite_pillar({"DIRECTIONAL": 90.0}) == 30.0


class TestConvergenceBonus:
    def test_one_scanner(self):
        assert get_convergence_bonus(1) == 0

    def test_two_scanners(self):
        assert get_convergence_bonus(2) == 50

    def test_three_scanners(self):
        assert get_convergence_bonus(3) == 75

    def test_four_scanners(self):
        assert get_convergence_bonus(4) == 100

    def test_five_scanners_capped(self):
        assert get_convergence_bonus(5) == 100

    def test_zero_scanners(self):
        assert get_convergence_bonus(0) == 0


class TestTimeSensitivity:
    def test_act_now(self):
        assert get_time_sensitivity_boost("act_now") == 100

    def test_hours(self):
        assert get_time_sensitivity_boost("hours") == 50

    def test_patient(self):
        assert get_time_sensitivity_boost("patient") == 0

    def test_unknown(self):
        assert get_time_sensitivity_boost("unknown") == 0


class TestDetermineUrgency:
    def test_breakout(self):
        assert determine_urgency(["BREAKOUT"]) == "act_now"

    def test_breakdown(self):
        assert determine_urgency(["BREAKDOWN"]) == "act_now"

    def test_unusual_volume(self):
        assert determine_urgency(["UNUSUAL_VOLUME"]) == "hours"

    def test_compression(self):
        assert determine_urgency(["COMPRESSION"]) == "patient"

    def test_cheap_options(self):
        assert determine_urgency(["CHEAP_OPTIONS"]) == "patient"

    def test_breakout_takes_priority(self):
        assert determine_urgency(["UNUSUAL_VOLUME", "BREAKOUT"]) == "act_now"

    def test_uv_over_patient(self):
        assert determine_urgency(["COMPRESSION", "UNUSUAL_VOLUME"]) == "hours"

    def test_empty(self):
        assert determine_urgency([]) == "patient"


class TestCalculateConvictionScore:
    """Integration tests with known test vectors matching frontend output."""

    def test_high_conviction_breakout(self):
        """Strong EV, good pillars, breakout scanner → high score."""
        result = calculate_conviction_score(
            theta_adj_ev=12.0,
            pillar_scores={"DIRECTIONAL": 75.0, "VOLATILITY": 80.0, "STRUCTURE": 70.0},
            gate_margin=60.0,
            scanner_types=["BREAKOUT"],
        )
        # EV: 12/15 = 80% → 80 * 0.40 = 32
        # Pillar: (75+80+70)/3 = 75 → 75 * 0.25 = 18.75 → 18.8
        # Margin: 60 * 0.15 = 9
        # Convergence: 1 scanner → 0 * 0.10 = 0
        # Time: act_now → 100 * 0.10 = 10
        # Total: 32 + 18.8 + 9 + 0 + 10 = 69.8
        assert result.total == 69.8

    def test_multi_scanner_convergence(self):
        """Two scanners boost the convergence component."""
        result = calculate_conviction_score(
            theta_adj_ev=15.0,
            pillar_scores={"DIRECTIONAL": 80.0, "VOLATILITY": 80.0, "STRUCTURE": 80.0},
            gate_margin=70.0,
            scanner_types=["BREAKOUT", "UNUSUAL_VOLUME"],
        )
        # EV: 15/15 = 100% → 100 * 0.40 = 40
        # Pillar: 80 * 0.25 = 20
        # Margin: 70 * 0.15 = 10.5
        # Convergence: 2 scanners → 50 * 0.10 = 5
        # Time: act_now (BREAKOUT present) → 100 * 0.10 = 10
        # Total: 40 + 20 + 10.5 + 5 + 10 = 85.5
        assert result.total == 85.5

    def test_zero_ev_patient_scanner(self):
        """Negative EV with patient scanner → low score."""
        result = calculate_conviction_score(
            theta_adj_ev=-2.0,
            pillar_scores={"DIRECTIONAL": 50.0, "VOLATILITY": 50.0, "STRUCTURE": 50.0},
            gate_margin=50.0,
            scanner_types=["COMPRESSION"],
        )
        # EV: 0 (negative)
        # Pillar: 50 * 0.25 = 12.5
        # Margin: 50 * 0.15 = 7.5
        # Convergence: 0
        # Time: patient → 0
        # Total: 0 + 12.5 + 7.5 + 0 + 0 = 20.0
        assert result.total == 20.0

    def test_none_inputs_use_defaults(self):
        """None/empty inputs should use safe defaults."""
        result = calculate_conviction_score(
            theta_adj_ev=0.0,
            pillar_scores={},
            gate_margin=50.0,
            scanner_types=[],
        )
        # EV: 0
        # Pillar: 0
        # Margin: 50 * 0.15 = 7.5
        # Convergence: 1 scanner (len([])==0 → default 1) → 0
        # Time: patient → 0
        # Total: 7.5
        assert result.total == 7.5

    def test_breakdown_components(self):
        """Verify breakdown components are populated correctly."""
        result = calculate_conviction_score(
            theta_adj_ev=10.0,
            pillar_scores={"DIRECTIONAL": 60.0, "VOLATILITY": 70.0, "STRUCTURE": 80.0},
            gate_margin=55.0,
            scanner_types=["UNUSUAL_VOLUME"],
        )
        assert "theta_adjusted_ev" in result.components
        assert "composite_pillar" in result.components
        assert "gate_margin" in result.components
        assert "scanner_convergence" in result.components
        assert "time_sensitivity" in result.components

        # Check urgency: UNUSUAL_VOLUME → hours → 50
        assert result.components["time_sensitivity"].raw == 50.0

    def test_ev_benchmark_override(self):
        """Custom EV benchmark changes normalization."""
        result = calculate_conviction_score(
            theta_adj_ev=10.0,
            pillar_scores={"DIRECTIONAL": 0.0, "VOLATILITY": 0.0, "STRUCTURE": 0.0},
            gate_margin=0.0,
            scanner_types=[],
            ev_benchmark=10.0,  # 10/10 = 100%
        )
        # EV: 100 * 0.40 = 40
        assert result.components["theta_adjusted_ev"].normalized == 100.0
        assert result.total == 40.0
