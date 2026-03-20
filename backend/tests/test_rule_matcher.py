"""Tests for setup rule matching engine."""

from app.paper_trading.rule_matcher import matches_rule, match_rules, format_matched_rules


def _eval(**overrides):
    """Helper to build evaluation dict with defaults."""
    base = {
        "option_type": "CALL",
        "dte": 30,
        "iv": 0.45,
        "delta": 0.35,
        "spread_pct": 3.0,
        "open_interest": 500,
        "volume": 100,
    }
    base.update(overrides)
    return base


def _decision(**overrides):
    """Helper to build decision dict with defaults."""
    base = {
        "final_score": 80.0,
        "directional_score": 75.0,
        "volatility_score": 70.0,
        "structure_score": 65.0,
    }
    base.update(overrides)
    return base


def _rule(criteria, **overrides):
    """Helper to build a setup rule dict."""
    base = {
        "rule_id": "test-rule-1",
        "name": "Test Rule",
        "criteria": criteria,
        "is_active": True,
        "mode": "production",
        "performance_at_creation": {"win_rate": 0.7, "sample_size": 15},
    }
    base.update(overrides)
    return base


class TestMatchesRule:
    def test_empty_criteria_matches_everything(self):
        assert matches_rule({}, _eval(), _decision(), ["BREAKOUT"])

    def test_scanner_match(self):
        assert matches_rule(
            {"scanners": ["BREAKOUT"]}, _eval(), _decision(), ["BREAKOUT"]
        )

    def test_scanner_no_match(self):
        assert not matches_rule(
            {"scanners": ["COMPRESSION"]}, _eval(), _decision(), ["BREAKOUT"]
        )

    def test_scanner_any_match(self):
        """At least one scanner from rule must be in evaluation's scanners."""
        assert matches_rule(
            {"scanners": ["COMPRESSION", "BREAKOUT"]},
            _eval(),
            _decision(),
            ["BREAKOUT"],
        )

    def test_scanner_confluence(self):
        assert matches_rule(
            {"scanner_confluence": True},
            _eval(),
            _decision(),
            ["BREAKOUT", "COMPRESSION"],
        )

    def test_scanner_confluence_fails(self):
        assert not matches_rule(
            {"scanner_confluence": True},
            _eval(),
            _decision(),
            ["BREAKOUT"],
        )

    def test_option_type_match(self):
        assert matches_rule(
            {"option_type": "CALL"}, _eval(option_type="CALL"), _decision(), []
        )

    def test_option_type_no_match(self):
        assert not matches_rule(
            {"option_type": "PUT"}, _eval(option_type="CALL"), _decision(), []
        )

    def test_option_type_case_insensitive(self):
        assert matches_rule(
            {"option_type": "call"}, _eval(option_type="CALL"), _decision(), []
        )

    def test_conviction_score_min(self):
        assert matches_rule(
            {"conviction_score_min": 75},
            _eval(),
            _decision(final_score=80),
            [],
        )

    def test_conviction_score_min_fails(self):
        assert not matches_rule(
            {"conviction_score_min": 85},
            _eval(),
            _decision(final_score=80),
            [],
        )

    def test_conviction_score_min_exact(self):
        assert matches_rule(
            {"conviction_score_min": 80},
            _eval(),
            _decision(final_score=80),
            [],
        )

    def test_pillar_directional_min(self):
        assert matches_rule(
            {"pillar_directional_min": 70},
            _eval(),
            _decision(directional_score=75),
            [],
        )

    def test_pillar_directional_min_fails(self):
        assert not matches_rule(
            {"pillar_directional_min": 80},
            _eval(),
            _decision(directional_score=75),
            [],
        )

    def test_pillar_volatility_min(self):
        assert matches_rule(
            {"pillar_volatility_min": 65},
            _eval(),
            _decision(volatility_score=70),
            [],
        )

    def test_pillar_structure_min(self):
        assert matches_rule(
            {"pillar_structure_min": 60},
            _eval(),
            _decision(structure_score=65),
            [],
        )

    def test_dte_range(self):
        assert matches_rule(
            {"dte_min": 10, "dte_max": 50}, _eval(dte=30), _decision(), []
        )

    def test_dte_below_min(self):
        assert not matches_rule(
            {"dte_min": 10, "dte_max": 50}, _eval(dte=5), _decision(), []
        )

    def test_dte_above_max(self):
        assert not matches_rule(
            {"dte_min": 10, "dte_max": 50}, _eval(dte=60), _decision(), []
        )

    def test_entry_iv_range(self):
        assert matches_rule(
            {"entry_iv_min": 0.3, "entry_iv_max": 0.6},
            _eval(iv=0.45),
            _decision(),
            [],
        )

    def test_entry_iv_out_of_range(self):
        assert not matches_rule(
            {"entry_iv_min": 0.3, "entry_iv_max": 0.4},
            _eval(iv=0.45),
            _decision(),
            [],
        )

    def test_multiple_criteria_all_pass(self):
        """All criteria must match (AND logic)."""
        criteria = {
            "scanners": ["BREAKOUT"],
            "option_type": "PUT",
            "conviction_score_min": 72,
            "pillar_directional_min": 75,
            "dte_min": 10,
            "dte_max": 50,
        }
        assert matches_rule(
            criteria,
            _eval(option_type="PUT", dte=30),
            _decision(final_score=80, directional_score=78),
            ["BREAKOUT"],
        )

    def test_multiple_criteria_one_fails(self):
        """If any criterion fails, the whole rule doesn't match."""
        criteria = {
            "scanners": ["BREAKOUT"],
            "option_type": "PUT",
            "conviction_score_min": 85,  # This will fail
        }
        assert not matches_rule(
            criteria,
            _eval(option_type="PUT"),
            _decision(final_score=80),
            ["BREAKOUT"],
        )

    def test_none_criteria_ignored(self):
        assert matches_rule(
            {"option_type": "CALL", "dte_min": None},
            _eval(option_type="CALL"),
            _decision(),
            [],
        )

    def test_unknown_criteria_ignored(self):
        """Unknown criteria should be forward-compatible."""
        assert matches_rule(
            {"future_field": "anything", "option_type": "CALL"},
            _eval(option_type="CALL"),
            _decision(),
            [],
        )

    # --- Volatility feature criteria ---

    def test_iv_percentile_max_passes(self):
        assert matches_rule(
            {"iv_percentile_max": 30},
            _eval(iv_percentile=21.0),
            _decision(),
            [],
        )

    def test_iv_percentile_max_fails(self):
        assert not matches_rule(
            {"iv_percentile_max": 30},
            _eval(iv_percentile=45.0),
            _decision(),
            [],
        )

    def test_iv_percentile_max_exact(self):
        assert matches_rule(
            {"iv_percentile_max": 30},
            _eval(iv_percentile=30.0),
            _decision(),
            [],
        )

    def test_iv_percentile_max_missing_data_fails(self):
        """Missing iv_percentile means we can't confirm the setup — no match."""
        assert not matches_rule(
            {"iv_percentile_max": 30},
            _eval(),  # No iv_percentile field
            _decision(),
            [],
        )

    def test_iv_percentile_min_passes(self):
        assert matches_rule(
            {"iv_percentile_min": 70},
            _eval(iv_percentile=80.0),
            _decision(),
            [],
        )

    def test_iv_percentile_min_fails(self):
        assert not matches_rule(
            {"iv_percentile_min": 70},
            _eval(iv_percentile=50.0),
            _decision(),
            [],
        )

    def test_iv_rv_ratio_max_passes(self):
        """IV < HV means ratio < 1.0."""
        assert matches_rule(
            {"iv_rv_ratio_max": 1.0},
            _eval(iv_rv_ratio=0.87),
            _decision(),
            [],
        )

    def test_iv_rv_ratio_max_fails(self):
        assert not matches_rule(
            {"iv_rv_ratio_max": 1.0},
            _eval(iv_rv_ratio=1.15),
            _decision(),
            [],
        )

    def test_iv_rv_ratio_max_missing_data_fails(self):
        """Missing iv_rv_ratio means we can't confirm the setup — no match."""
        assert not matches_rule(
            {"iv_rv_ratio_max": 1.0},
            _eval(),
            _decision(),
            [],
        )

    def test_iv_rv_ratio_min_passes(self):
        assert matches_rule(
            {"iv_rv_ratio_min": 1.25},
            _eval(iv_rv_ratio=1.5),
            _decision(),
            [],
        )

    def test_iv_rv_ratio_min_fails(self):
        assert not matches_rule(
            {"iv_rv_ratio_min": 1.25},
            _eval(iv_rv_ratio=1.0),
            _decision(),
            [],
        )

    def test_theta_adjusted_edge_min_passes(self):
        assert matches_rule(
            {"theta_adjusted_edge_min": 1.5},
            _eval(theta_adjusted_edge=2.0),
            _decision(),
            [],
        )

    def test_theta_adjusted_edge_min_fails(self):
        assert not matches_rule(
            {"theta_adjusted_edge_min": 1.5},
            _eval(theta_adjusted_edge=1.0),
            _decision(),
            [],
        )

    def test_theta_adjusted_edge_min_missing_data_fails(self):
        """Missing theta_adjusted_edge means we can't confirm the setup — no match."""
        assert not matches_rule(
            {"theta_adjusted_edge_min": 1.5},
            _eval(),
            _decision(),
            [],
        )

    def test_volatility_tailwind_combined(self):
        """Combined IV < HV + low percentile = volatility tailwind setup."""
        criteria = {
            "iv_rv_ratio_max": 1.0,
            "iv_percentile_max": 30,
        }
        # Matches: IV/RV = 0.87 (cheap), percentile = 21 (historically cheap)
        assert matches_rule(
            criteria,
            _eval(iv_rv_ratio=0.87, iv_percentile=21.0),
            _decision(),
            [],
        )

    def test_volatility_tailwind_one_criterion_fails(self):
        """If IV is cheap but percentile is high, rule doesn't match."""
        criteria = {
            "iv_rv_ratio_max": 1.0,
            "iv_percentile_max": 30,
        }
        assert not matches_rule(
            criteria,
            _eval(iv_rv_ratio=0.87, iv_percentile=55.0),
            _decision(),
            [],
        )

    def test_full_volatility_tailwind_with_theta_edge(self):
        """Full tailwind: cheap IV + low percentile + strong theta edge."""
        criteria = {
            "iv_rv_ratio_max": 1.0,
            "iv_percentile_max": 30,
            "theta_adjusted_edge_min": 1.5,
        }
        assert matches_rule(
            criteria,
            _eval(iv_rv_ratio=0.87, iv_percentile=21.0, theta_adjusted_edge=2.3),
            _decision(),
            [],
        )


class TestMatchRules:
    def test_returns_matching_rules(self):
        rules = [
            _rule({"option_type": "CALL"}, rule_id="r1", name="Calls"),
            _rule({"option_type": "PUT"}, rule_id="r2", name="Puts"),
        ]
        matched = match_rules(rules, _eval(option_type="CALL"), _decision(), [])
        assert len(matched) == 1
        assert matched[0]["rule_id"] == "r1"

    def test_filters_inactive(self):
        rules = [
            _rule({"option_type": "CALL"}, rule_id="r1", is_active=False),
            _rule({"option_type": "CALL"}, rule_id="r2", is_active=True),
        ]
        matched = match_rules(rules, _eval(option_type="CALL"), _decision(), [])
        assert len(matched) == 1
        assert matched[0]["rule_id"] == "r2"

    def test_active_only_false(self):
        rules = [
            _rule({"option_type": "CALL"}, rule_id="r1", is_active=False),
        ]
        matched = match_rules(
            rules, _eval(option_type="CALL"), _decision(), [], active_only=False
        )
        assert len(matched) == 1

    def test_mode_filter(self):
        rules = [
            _rule({"option_type": "CALL"}, rule_id="r1", mode="production"),
            _rule({"option_type": "CALL"}, rule_id="r2", mode="test"),
        ]
        matched = match_rules(
            rules,
            _eval(option_type="CALL"),
            _decision(),
            [],
            mode_filter="production",
        )
        assert len(matched) == 1
        assert matched[0]["rule_id"] == "r1"

    def test_multiple_matches(self):
        rules = [
            _rule({"conviction_score_min": 70}, rule_id="r1"),
            _rule({"conviction_score_min": 75}, rule_id="r2"),
            _rule({"conviction_score_min": 90}, rule_id="r3"),
        ]
        matched = match_rules(rules, _eval(), _decision(final_score=80), [])
        assert len(matched) == 2
        assert {r["rule_id"] for r in matched} == {"r1", "r2"}


class TestFormatMatchedRules:
    def test_basic_format(self):
        matched = [_rule({"option_type": "CALL"}, rule_id="r1", name="Test")]
        formatted = format_matched_rules(matched)
        assert len(formatted) == 1
        assert formatted[0]["rule_id"] == "r1"
        assert formatted[0]["name"] == "Test"
        assert formatted[0]["mode"] == "production"
        assert "criteria" not in formatted[0]

    def test_include_criteria(self):
        criteria = {"option_type": "CALL"}
        matched = [_rule(criteria, rule_id="r1")]
        formatted = format_matched_rules(matched, include_criteria=True)
        assert formatted[0]["criteria"] == criteria
