"""Tests for backend conviction score calculator.

3-component formula: EV (35%) + Return% (30%) + Pillars (35%).
Validates parity with frontend/src/lib/convictionScore.ts.
"""

from app.scoring.conviction import (
    DEFAULT_EV_BENCHMARK,
    DEFAULT_RETURN_PCT_BENCHMARK,
    CHEAP_UV_PREMIUM_THRESHOLD,
    calculate_composite_pillar,
    calculate_conviction_score,
    determine_urgency,
    normalize_ev,
    normalize_return_pct,
)


class TestNormalizeEV:
    def test_negative_ev(self):
        assert normalize_ev(-5.0) == 0.0

    def test_zero_ev(self):
        assert normalize_ev(0.0) == 0.0

    def test_positive_ev(self):
        assert normalize_ev(15.0) == 50.0

    def test_full_benchmark(self):
        assert normalize_ev(30.0) == 100.0

    def test_above_benchmark_capped(self):
        assert normalize_ev(60.0) == 100.0

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
        result = normalize_return_pct(20.0, 1.0)
        assert result == 100.0

    def test_10_pct_return_equals_50(self):
        result = normalize_return_pct(5.0, 0.50)
        assert result == 50.0

    def test_high_return_capped_at_100(self):
        result = normalize_return_pct(40.0, 1.0)
        assert result == 100.0


class TestCompositePillar:
    def test_average(self):
        scores = {"DIRECTIONAL": 60.0, "VOLATILITY": 80.0, "STRUCTURE": 70.0}
        assert calculate_composite_pillar(scores) == 70.0

    def test_missing_keys_default_zero(self):
        assert calculate_composite_pillar({}) == 0.0


class TestDetermineUrgency:
    def test_breakout(self):
        assert determine_urgency(["BREAKOUT"]) == "act_now"

    def test_unusual_volume_expensive(self):
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=5.0) == "hours"

    def test_unusual_volume_cheap_escalates(self):
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=1.0) == "act_now"
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=CHEAP_UV_PREMIUM_THRESHOLD) == "act_now"

    def test_unusual_volume_no_mid(self):
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=None) == "hours"

    def test_compression(self):
        assert determine_urgency(["COMPRESSION"]) == "patient"

    def test_empty(self):
        assert determine_urgency([]) == "patient"


class TestCalculateConvictionScore:
    """Integration tests for 3-component formula.

    Weights: EV=0.35, ReturnPct=0.30, Pillar=0.35
    """

    def test_cmg_like_trade(self):
        """CMG-like trade: cheap premium, high EV, strong pillars."""
        result = calculate_conviction_score(
            theta_adj_ev=20.85,
            pillar_scores={"DIRECTIONAL": 75.0, "VOLATILITY": 88.0, "STRUCTURE": 72.0},
            gate_margin=50.0,
            scanner_types=["UNUSUAL_VOLUME"],
            mid=1.06,
        )
        # EV: 20.85/30*100=69.5 * 0.35 = 24.3
        # ReturnPct: 19.67% / 20% * 100 = 98.35 * 0.30 = 29.5
        # Pillar: 78.33 * 0.35 = 27.4
        # Total: ~81.2
        assert result.total >= 78

    def test_good_cheap_uv(self):
        """Good cheap UV setup scores in the 50-60 range."""
        result = calculate_conviction_score(
            theta_adj_ev=10.0,
            pillar_scores={"DIRECTIONAL": 70.0, "VOLATILITY": 75.0, "STRUCTURE": 65.0},
            gate_margin=45.0,
            scanner_types=["UNUSUAL_VOLUME"],
            mid=0.80,
        )
        assert 50 <= result.total <= 65

    def test_expensive_option_with_strong_signals(self):
        """Expensive option with strong EV and pillars still scores well."""
        result = calculate_conviction_score(
            theta_adj_ev=315.0,
            pillar_scores={"DIRECTIONAL": 70.0, "VOLATILITY": 80.0, "STRUCTURE": 65.0},
            gate_margin=50.0,
            scanner_types=["UNUSUAL_VOLUME"],
            mid=25.30,
        )
        assert result.total >= 70

    def test_weak_setup_filtered(self):
        """Weak setup scores below 50."""
        result = calculate_conviction_score(
            theta_adj_ev=3.0,
            pillar_scores={"DIRECTIONAL": 55.0, "VOLATILITY": 60.0, "STRUCTURE": 50.0},
            gate_margin=35.0,
            scanner_types=["COMPRESSION"],
            mid=2.0,
        )
        assert result.total < 50

    def test_no_mid_return_pct_is_zero(self):
        """Without mid, return% contributes 0."""
        result = calculate_conviction_score(
            theta_adj_ev=12.0,
            pillar_scores={"DIRECTIONAL": 75.0, "VOLATILITY": 80.0, "STRUCTURE": 70.0},
            gate_margin=60.0,
            scanner_types=["BREAKOUT"],
        )
        assert result.components["return_pct"].raw == 0.0
        assert result.components["return_pct"].weighted == 0.0

    def test_only_three_components_in_breakdown(self):
        """Breakdown should only have 3 components."""
        result = calculate_conviction_score(
            theta_adj_ev=10.0,
            pillar_scores={"DIRECTIONAL": 60.0, "VOLATILITY": 70.0, "STRUCTURE": 80.0},
            gate_margin=55.0,
            scanner_types=["UNUSUAL_VOLUME"],
            mid=1.0,
        )
        assert set(result.components.keys()) == {
            "theta_adjusted_ev", "return_pct", "composite_pillar"
        }

    def test_gate_margin_not_in_score(self):
        """Gate margin is accepted but not used."""
        result_low = calculate_conviction_score(
            theta_adj_ev=10.0,
            pillar_scores={"DIRECTIONAL": 70.0, "VOLATILITY": 70.0, "STRUCTURE": 70.0},
            gate_margin=10.0,
            scanner_types=[],
            mid=1.0,
        )
        result_high = calculate_conviction_score(
            theta_adj_ev=10.0,
            pillar_scores={"DIRECTIONAL": 70.0, "VOLATILITY": 70.0, "STRUCTURE": 70.0},
            gate_margin=90.0,
            scanner_types=[],
            mid=1.0,
        )
        assert result_low.total == result_high.total

    def test_none_inputs_use_defaults(self):
        """None/empty inputs should use safe defaults."""
        result = calculate_conviction_score(
            theta_adj_ev=0.0,
            pillar_scores={},
            gate_margin=50.0,
            scanner_types=[],
        )
        assert result.total == 0.0

    def test_ev_benchmark_override(self):
        """Custom EV benchmark changes normalization."""
        result = calculate_conviction_score(
            theta_adj_ev=10.0,
            pillar_scores={"DIRECTIONAL": 0.0, "VOLATILITY": 0.0, "STRUCTURE": 0.0},
            gate_margin=0.0,
            scanner_types=[],
            ev_benchmark=10.0,
        )
        assert result.components["theta_adjusted_ev"].normalized == 100.0
        assert result.total == 35.0  # 100 * 0.35
