"""Tests for backend conviction score calculator.

Validates parity with frontend/src/lib/convictionScore.ts.
"""

from app.scoring.conviction import (
    DEFAULT_EV_BENCHMARK,
    DEFAULT_RETURN_PCT_BENCHMARK,
    CHEAP_UV_PREMIUM_THRESHOLD,
    calculate_composite_pillar,
    calculate_conviction_score,
    determine_urgency,
    get_convergence_bonus,
    get_time_sensitivity_boost,
    normalize_ev,
    normalize_return_pct,
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


class TestNormalizeReturnPct:
    def test_zero_mid(self):
        assert normalize_return_pct(10.0, 0.0) == 0.0

    def test_negative_mid(self):
        assert normalize_return_pct(10.0, -1.0) == 0.0

    def test_zero_ev(self):
        assert normalize_return_pct(0.0, 1.0) == 0.0

    def test_negative_ev(self):
        assert normalize_return_pct(-5.0, 1.0) == 0.0

    def test_20_pct_return_equals_100(self):
        # $20 EV on $1.00 option = 20% return → normalized 100
        result = normalize_return_pct(20.0, 1.0)
        assert result == 100.0

    def test_10_pct_return_equals_50(self):
        # $5 EV on $0.50 option = 10% return → normalized 50
        result = normalize_return_pct(5.0, 0.50)
        assert result == 50.0

    def test_high_return_capped_at_100(self):
        # $40 EV on $1.00 option = 40% → capped at 100
        result = normalize_return_pct(40.0, 1.0)
        assert result == 100.0

    def test_custom_benchmark(self):
        # $10 EV on $1.00 option = 10% return / 10% benchmark = 100
        result = normalize_return_pct(10.0, 1.0, benchmark=10.0)
        assert result == 100.0


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

    def test_unusual_volume_expensive(self):
        """UV with expensive premium stays 'hours'."""
        assert determine_urgency(["UNUSUAL_VOLUME"]) == "hours"
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=5.0) == "hours"

    def test_unusual_volume_cheap_escalates(self):
        """UV with cheap premium (mid <= $1.50) escalates to 'act_now'."""
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=1.0) == "act_now"
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=1.50) == "act_now"

    def test_unusual_volume_at_threshold(self):
        """UV at exactly the threshold is act_now."""
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=CHEAP_UV_PREMIUM_THRESHOLD) == "act_now"

    def test_unusual_volume_above_threshold(self):
        """UV just above threshold stays 'hours'."""
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=1.51) == "hours"

    def test_unusual_volume_no_mid(self):
        """UV without mid stays 'hours' (backwards compat)."""
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=None) == "hours"

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
    """Integration tests with known test vectors.

    New weights: EV=0.25, ReturnPct=0.15, Pillar=0.25, Margin=0.15, Convergence=0.10, Time=0.10
    """

    def test_high_conviction_breakout(self):
        """Strong EV, good pillars, breakout scanner → high score."""
        result = calculate_conviction_score(
            theta_adj_ev=12.0,
            pillar_scores={"DIRECTIONAL": 75.0, "VOLATILITY": 80.0, "STRUCTURE": 70.0},
            gate_margin=60.0,
            scanner_types=["BREAKOUT"],
            mid=2.0,
        )
        # EV: 12/15 = 80% → 80 * 0.25 = 20
        # ReturnPct: (12/(2*100))*100 = 6% → 6/20*100 = 30 → 30 * 0.15 = 4.5
        # Pillar: (75+80+70)/3 = 75 → 75 * 0.25 = 18.75 → 18.8
        # Margin: 60 * 0.15 = 9
        # Convergence: 1 → 0 * 0.10 = 0
        # Time: act_now → 100 * 0.10 = 10
        # Total: 20 + 4.5 + 18.75 + 9 + 0 + 10 = 62.25 → round1 = 62.2
        assert result.total == 62.2

    def test_cheap_option_high_return(self):
        """Cheap option with modest EV but excellent return% scores well."""
        result = calculate_conviction_score(
            theta_adj_ev=3.0,
            pillar_scores={"DIRECTIONAL": 75.0, "VOLATILITY": 85.0, "STRUCTURE": 70.0},
            gate_margin=55.0,
            scanner_types=["UNUSUAL_VOLUME"],
            mid=0.50,
        )
        # EV: 3/15 = 20% → 20 * 0.25 = 5
        # ReturnPct: (3/(0.5*100))*100 = 6% → 6/20*100 = 30 → 30 * 0.15 = 4.5
        # Pillar: (75+85+70)/3 ≈ 76.67 → 76.67 * 0.25 = 19.167 → 19.2
        # Margin: 55 * 0.15 = 8.25 → 8.2 (rounded)
        # Convergence: 1 → 0
        # Time: UV + mid=0.50 → act_now → 100 * 0.10 = 10
        # Total: 5 + 4.5 + 19.2 + 8.2 + 0 + 10 = 46.9
        assert result.total == 46.9

    def test_cmg_like_trade(self):
        """CMG-like trade: cheap premium, high EV, unusual volume."""
        result = calculate_conviction_score(
            theta_adj_ev=20.85,
            pillar_scores={"DIRECTIONAL": 75.0, "VOLATILITY": 88.0, "STRUCTURE": 72.0},
            gate_margin=50.0,
            scanner_types=["UNUSUAL_VOLUME"],
            mid=1.06,
        )
        # EV: 20.85/15 = 139% → capped 100 → 100 * 0.25 = 25
        # ReturnPct: (20.85/(1.06*100))*100 = 19.67% → 19.67/20*100 = 98.35 → 98.35 * 0.15 = 14.75
        # Pillar: (75+88+72)/3 = 78.33 → 78.33 * 0.25 = 19.58
        # Margin: 50 * 0.15 = 7.5
        # Convergence: 1 → 0
        # Time: UV + mid=1.06 → act_now → 100 * 0.10 = 10
        # Total: 25 + 14.8 + 19.6 + 7.5 + 0 + 10 = 76.9
        assert result.total >= 75  # High conviction — clears threshold

    def test_no_mid_return_pct_is_zero(self):
        """Without mid, return% component contributes 0 (backwards compat)."""
        result = calculate_conviction_score(
            theta_adj_ev=12.0,
            pillar_scores={"DIRECTIONAL": 75.0, "VOLATILITY": 80.0, "STRUCTURE": 70.0},
            gate_margin=60.0,
            scanner_types=["BREAKOUT"],
        )
        assert result.components["return_pct"].raw == 0.0
        assert result.components["return_pct"].weighted == 0.0

    def test_multi_scanner_convergence(self):
        """Two scanners boost the convergence component."""
        result = calculate_conviction_score(
            theta_adj_ev=15.0,
            pillar_scores={"DIRECTIONAL": 80.0, "VOLATILITY": 80.0, "STRUCTURE": 80.0},
            gate_margin=70.0,
            scanner_types=["BREAKOUT", "UNUSUAL_VOLUME"],
            mid=3.0,
        )
        # Convergence: 2 scanners → 50 * 0.10 = 5
        assert result.components["scanner_convergence"].weighted == 5.0
        # Time: BREAKOUT present → act_now → 100 * 0.10 = 10
        assert result.components["time_sensitivity"].weighted == 10.0

    def test_zero_ev_patient_scanner(self):
        """Negative EV with patient scanner → low score."""
        result = calculate_conviction_score(
            theta_adj_ev=-2.0,
            pillar_scores={"DIRECTIONAL": 50.0, "VOLATILITY": 50.0, "STRUCTURE": 50.0},
            gate_margin=50.0,
            scanner_types=["COMPRESSION"],
        )
        # EV: 0 (negative) + ReturnPct: 0 (negative EV)
        # Pillar: 50 * 0.25 = 12.5
        # Margin: 50 * 0.15 = 7.5
        assert result.total == 20.0

    def test_none_inputs_use_defaults(self):
        """None/empty inputs should use safe defaults."""
        result = calculate_conviction_score(
            theta_adj_ev=0.0,
            pillar_scores={},
            gate_margin=50.0,
            scanner_types=[],
        )
        # Only margin contributes: 50 * 0.15 = 7.5
        assert result.total == 7.5

    def test_breakdown_has_return_pct_component(self):
        """Verify return_pct component is in breakdown."""
        result = calculate_conviction_score(
            theta_adj_ev=10.0,
            pillar_scores={"DIRECTIONAL": 60.0, "VOLATILITY": 70.0, "STRUCTURE": 80.0},
            gate_margin=55.0,
            scanner_types=["UNUSUAL_VOLUME"],
            mid=1.0,
        )
        assert "return_pct" in result.components
        # ReturnPct: (10/(1*100))*100 = 10% → 10/20*100 = 50 → 50 * 0.15 = 7.5
        assert result.components["return_pct"].normalized == 50.0
        assert result.components["return_pct"].weighted == 7.5

    def test_ev_benchmark_override(self):
        """Custom EV benchmark changes normalization."""
        result = calculate_conviction_score(
            theta_adj_ev=10.0,
            pillar_scores={"DIRECTIONAL": 0.0, "VOLATILITY": 0.0, "STRUCTURE": 0.0},
            gate_margin=0.0,
            scanner_types=[],
            ev_benchmark=10.0,  # 10/10 = 100%
        )
        assert result.components["theta_adjusted_ev"].normalized == 100.0
        # EV: 100 * 0.25 = 25
        assert result.total == 25.0
