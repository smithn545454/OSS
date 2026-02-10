"""Tests for the ThresholdSimulator (calibration/simulator.py).

Covers simulate_threshold_change, generate_suggestions,
simulate_gate_counterfactual, simulate_score_threshold,
and internal helper methods.
"""

from __future__ import annotations

import pytest

from app.calibration.models import (
    EstimatedImpact,
    GateAnalysis,
    RecommendationType,
    ThresholdSuggestion,
)
from app.calibration.simulator import (
    GATE_FIELD_MAPPINGS,
    GATE_HIGHER_IS_STRICTER,
    SimulationResult,
    ThresholdSimulator,
)
from app.core.schemas import (
    GateOperator,
    GateResult,
    Policy,
    PolicyConfig,
    Verdict,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_policy() -> Policy:
    config = PolicyConfig()
    return Policy(
        version="v2.0.0",
        policy_hash=Policy.compute_hash(config),
        config=config,
        created_by="test",
        is_active=True,
    )


def _make_eval(eval_id: str):
    """Minimal mock evaluation with required fields."""
    from app.core.schemas import DTEBucket, Evaluation, OptionType

    return Evaluation(
        evaluation_id=eval_id,
        opportunity_id="opp-1",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL260320C00185000",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=62,
        strike=185.0,
        underlying_price=189.0,
        moneyness_pct=-2.0,
        bid=8.0,
        ask=8.5,
        mid=8.25,
        spread_abs=0.50,
        spread_pct=6.06,
        iv=0.30,
        delta=0.55,
        gamma=0.03,
        theta=-0.08,
        vega=0.25,
        open_interest=5000,
        volume=500,
        breakeven_price=193.25,
        required_move_pct=2.25,
        expected_move_pct=5.0,
        feasibility_ratio=0.45,
        time_adjusted_feasibility=0.40,
        dte_bucket=DTEBucket.C,
        rank_score=80.0,
        policy_version="v2.0.0",
        policy_hash="test-hash",
    )


def _make_gate_result(eval_id, gate_id, passed, measured, threshold, operator="gte"):
    return GateResult(
        evaluation_id=eval_id,
        gate_id=gate_id,
        enabled=True,
        passed=passed,
        measured_value=measured,
        threshold_value=threshold,
        operator=GateOperator.GTE if operator == "gte" else GateOperator.LTE,
        units="test",
        reason_code="TEST",
    )


# ---------------------------------------------------------------------------
# Tests: simulate_threshold_change
# ---------------------------------------------------------------------------


class TestSimulateThresholdChange:
    """Test ThresholdSimulator.simulate_threshold_change."""

    def test_loosen_gte_gate_adds_passes(self):
        """Lowering a GTE gate threshold should add passes."""
        policy = _make_policy()
        evals = [_make_eval("eval-1"), _make_eval("eval-2")]
        # eval-1: OI=200 (fails at 300), eval-2: OI=500 (passes at 300)
        gate_results = [
            [_make_gate_result("eval-1", "GATE_MIN_OPEN_INTEREST", False, 200, 300)],
            [_make_gate_result("eval-2", "GATE_MIN_OPEN_INTEREST", True, 500, 300)],
        ]

        sim = ThresholdSimulator(policy, evals, gate_results)
        result = sim.simulate_threshold_change("GATE_MIN_OPEN_INTEREST", -50)

        assert isinstance(result, SimulationResult)
        assert result.gate_id == "GATE_MIN_OPEN_INTEREST"
        assert result.additional_passes >= 1  # eval-1 now passes at 150
        assert result.additional_failures == 0

    def test_tighten_gte_gate_adds_failures(self):
        """Raising a GTE gate threshold should add failures."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        gate_results = [
            [_make_gate_result("eval-1", "GATE_MIN_OPEN_INTEREST", True, 350, 300)],
        ]

        sim = ThresholdSimulator(policy, evals, gate_results)
        result = sim.simulate_threshold_change("GATE_MIN_OPEN_INTEREST", 50)

        assert result.additional_failures >= 1  # 350 < 450

    def test_loosen_lte_gate(self):
        """Raising an LTE gate threshold should add passes."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        # spread=9% (fails at 8%), loosen by 50% -> threshold becomes 12%
        gate_results = [
            [_make_gate_result("eval-1", "GATE_MAX_SPREAD_PCT", False, 9.0, 8.0, "lte")],
        ]

        sim = ThresholdSimulator(policy, evals, gate_results)
        result = sim.simulate_threshold_change("GATE_MAX_SPREAD_PCT", 50)

        assert result.additional_passes >= 1

    def test_unmapped_gate_returns_zeros(self):
        """Gates with no field mapping should return zero-impact result."""
        policy = _make_policy()
        sim = ThresholdSimulator(policy, [], [])
        result = sim.simulate_threshold_change("GATE_GREEKS_COHERENCE", 10)

        assert result.additional_passes == 0
        assert result.additional_failures == 0
        assert result.current_value == 0
        assert result.new_value == 0

    def test_no_evaluations_returns_zeros(self):
        """Empty evaluation list should return zero impact."""
        policy = _make_policy()
        sim = ThresholdSimulator(policy, [], [])
        result = sim.simulate_threshold_change("GATE_MIN_OPEN_INTEREST", -20)

        assert result.additional_passes == 0
        assert result.additional_failures == 0

    def test_disabled_gate_ignored(self):
        """Disabled gates should not contribute to pass/fail counts."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        disabled = GateResult(
            evaluation_id="eval-1", gate_id="GATE_MIN_OPEN_INTEREST",
            enabled=False, passed=False,
            measured_value=100, threshold_value=300,
            operator=GateOperator.GTE, units="test", reason_code="DISABLED",
        )
        sim = ThresholdSimulator(policy, evals, [[disabled]])
        result = sim.simulate_threshold_change("GATE_MIN_OPEN_INTEREST", -50)

        assert result.additional_passes == 0

    def test_win_rate_change_negative_for_loosening(self):
        """Adding passes should estimate a negative win-rate change."""
        policy = _make_policy()
        evals = [_make_eval(f"eval-{i}") for i in range(10)]
        gate_results = [
            [_make_gate_result(f"eval-{i}", "GATE_MIN_OPEN_INTEREST", False, 100, 300)]
            for i in range(10)
        ]

        sim = ThresholdSimulator(policy, evals, gate_results)
        result = sim.simulate_threshold_change("GATE_MIN_OPEN_INTEREST", -80)

        assert result.additional_passes > 0
        assert result.estimated_win_rate_change < 0


# ---------------------------------------------------------------------------
# Tests: generate_suggestions
# ---------------------------------------------------------------------------


class TestGenerateSuggestions:
    """Test ThresholdSimulator.generate_suggestions."""

    def test_loosen_suggestion(self):
        """High false-negative gate should get LOOSEN suggestion."""
        policy = _make_policy()
        evals = [_make_eval(f"eval-{i}") for i in range(5)]
        # measured=270 is just below 300 threshold; 15% decrease → 255 → 270>255 → flips
        gate_results = [
            [_make_gate_result(f"eval-{i}", "GATE_MIN_OPEN_INTEREST", False, 270, 300)]
            for i in range(5)
        ]

        sim = ThresholdSimulator(policy, evals, gate_results)

        analysis = GateAnalysis(
            gate_id="GATE_MIN_OPEN_INTEREST",
            rejection_count=40,
            rejection_rate=40.0,
            false_negative_count=15,
            false_negative_rate=15.0,
            effectiveness_score=0.5,
            recommendation=RecommendationType.LOOSEN,
        )

        suggestions = sim.generate_suggestions([analysis])
        assert len(suggestions) >= 1
        assert suggestions[0].gate_id == "GATE_MIN_OPEN_INTEREST"

    def test_tighten_suggestion(self):
        """Low rejection gate should get TIGHTEN suggestion."""
        policy = _make_policy()
        evals = [_make_eval(f"eval-{i}") for i in range(5)]
        # measured=330 just above 300 threshold; 15% increase → 345 → 330<345 → flips
        gate_results = [
            [_make_gate_result(f"eval-{i}", "GATE_MIN_OPEN_INTEREST", True, 330, 300)]
            for i in range(5)
        ]

        sim = ThresholdSimulator(policy, evals, gate_results)

        analysis = GateAnalysis(
            gate_id="GATE_MIN_OPEN_INTEREST",
            rejection_count=5,
            rejection_rate=5.0,
            false_negative_count=0,
            false_negative_rate=0.0,
            effectiveness_score=0.9,
            recommendation=RecommendationType.TIGHTEN,
        )

        suggestions = sim.generate_suggestions([analysis])
        assert len(suggestions) >= 1

    def test_no_change_skipped(self):
        """NO_CHANGE recommendation should produce no suggestions."""
        policy = _make_policy()
        sim = ThresholdSimulator(policy, [], [])

        analysis = GateAnalysis(
            gate_id="GATE_MIN_OPEN_INTEREST",
            rejection_count=20,
            rejection_rate=20.0,
            false_negative_count=5,
            false_negative_rate=5.0,
            effectiveness_score=0.7,
            recommendation=RecommendationType.NO_CHANGE,
        )

        suggestions = sim.generate_suggestions([analysis])
        assert len(suggestions) == 0

    def test_unmapped_gate_skipped(self):
        """Gates without field mapping should be skipped."""
        policy = _make_policy()
        sim = ThresholdSimulator(policy, [], [])

        analysis = GateAnalysis(
            gate_id="GATE_GREEKS_COHERENCE",
            rejection_count=20,
            rejection_rate=20.0,
            false_negative_count=10,
            false_negative_rate=10.0,
            effectiveness_score=0.6,
            recommendation=RecommendationType.LOOSEN,
        )

        suggestions = sim.generate_suggestions([analysis])
        assert len(suggestions) == 0


# ---------------------------------------------------------------------------
# Tests: simulate_gate_counterfactual
# ---------------------------------------------------------------------------


class TestSimulateGateCounterfactual:
    """Test simulate_gate_counterfactual for verdict-level transitions."""

    def test_loosening_converts_reject_to_approve(self):
        """Loosening a gate should convert REJECT→APPROVE for high-score evals."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        gate_results = [
            [_make_gate_result("eval-1", "GATE_MIN_OPEN_INTEREST", False, 200, 300)],
        ]
        eval_decisions = {
            "eval-1": {
                "verdict": "REJECT",
                "final_score": 85.0,
                "failed_gates": ["GATE_MIN_OPEN_INTEREST"],
            }
        }

        sim = ThresholdSimulator(policy, evals, gate_results, eval_decisions)
        result = sim.simulate_gate_counterfactual("GATE_MIN_OPEN_INTEREST", -50)

        assert "REJECT_to_APPROVE" in result.verdict_changes
        assert result.verdict_changes["REJECT_to_APPROVE"] >= 1
        assert "loosened" in result.scenario_label

    def test_tightening_converts_approve_to_reject(self):
        """Tightening a gate should convert APPROVE→REJECT for marginal evals."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        gate_results = [
            [_make_gate_result("eval-1", "GATE_MIN_OPEN_INTEREST", True, 350, 300)],
        ]
        eval_decisions = {
            "eval-1": {
                "verdict": "APPROVE",
                "final_score": 80.0,
                "failed_gates": [],
            }
        }

        sim = ThresholdSimulator(policy, evals, gate_results, eval_decisions)
        result = sim.simulate_gate_counterfactual("GATE_MIN_OPEN_INTEREST", 50)

        assert "APPROVE_to_REJECT" in result.verdict_changes
        assert "tightened" in result.scenario_label

    def test_no_decisions_returns_empty_counts(self):
        """Missing eval_decisions should produce empty results."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        sim = ThresholdSimulator(policy, evals, [[]])
        result = sim.simulate_gate_counterfactual("GATE_MIN_OPEN_INTEREST", -20)

        assert result.verdict_changes == {}

    def test_unmapped_gate(self):
        """Unmapped gates should still return valid CounterfactualResult."""
        policy = _make_policy()
        sim = ThresholdSimulator(policy, [], [])
        result = sim.simulate_gate_counterfactual("GATE_GREEKS_COHERENCE", 10)

        assert result.original_value == 0.0
        assert result.new_value == 0.0


# ---------------------------------------------------------------------------
# Tests: simulate_score_threshold
# ---------------------------------------------------------------------------


class TestSimulateScoreThreshold:
    """Test simulate_score_threshold for verdict transitions."""

    def test_lower_approve_adds_approvals(self):
        """Lowering approve threshold converts WATCH→APPROVE."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        eval_decisions = {
            "eval-1": {
                "verdict": "WATCH",
                "final_score": 72.0,
                "failed_gates": [],
            }
        }

        sim = ThresholdSimulator(policy, evals, [[]], eval_decisions)
        result = sim.simulate_score_threshold(approve_threshold=70)

        assert "WATCH_to_APPROVE" in result.verdict_changes
        assert result.counterfactual_counts["APPROVE"] >= 1

    def test_raise_approve_removes_approvals(self):
        """Raising approve threshold converts APPROVE→WATCH."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        eval_decisions = {
            "eval-1": {
                "verdict": "APPROVE",
                "final_score": 77.0,
                "failed_gates": [],
            }
        }

        sim = ThresholdSimulator(policy, evals, [[]], eval_decisions)
        result = sim.simulate_score_threshold(approve_threshold=80)

        assert "APPROVE_to_WATCH" in result.verdict_changes

    def test_gate_rejected_stays_reject(self):
        """Gate-rejected evals stay REJECT regardless of score threshold."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        eval_decisions = {
            "eval-1": {
                "verdict": "REJECT",
                "final_score": 90.0,
                "failed_gates": ["GATE_MIN_OI"],
            }
        }

        sim = ThresholdSimulator(policy, evals, [[]], eval_decisions)
        result = sim.simulate_score_threshold(approve_threshold=50)

        assert result.counterfactual_counts["REJECT"] == 1
        assert result.verdict_changes == {}

    def test_custom_watch_threshold(self):
        """Explicit watch_threshold should be applied."""
        policy = _make_policy()
        evals = [_make_eval("eval-1")]
        eval_decisions = {
            "eval-1": {
                "verdict": "REJECT",
                "final_score": 55.0,
                "failed_gates": [],
            }
        }

        sim = ThresholdSimulator(policy, evals, [[]], eval_decisions)
        result = sim.simulate_score_threshold(approve_threshold=70, watch_threshold=50)

        assert result.counterfactual_counts["WATCH"] >= 1


# ---------------------------------------------------------------------------
# Tests: _would_pass_with_threshold / _get_threshold_value
# ---------------------------------------------------------------------------


class TestHelpers:
    """Test internal helper methods."""

    def test_would_pass_gte(self):
        """GTE: measured >= threshold → pass."""
        policy = _make_policy()
        sim = ThresholdSimulator(policy, [], [])
        gr = _make_gate_result("eval", "G", True, 500, 300, "gte")

        assert sim._would_pass_with_threshold(gr, 400, "G") is True
        assert sim._would_pass_with_threshold(gr, 500, "G") is True
        assert sim._would_pass_with_threshold(gr, 501, "G") is False

    def test_would_pass_lte(self):
        """LTE: measured <= threshold → pass."""
        policy = _make_policy()
        sim = ThresholdSimulator(policy, [], [])
        gr = _make_gate_result("eval", "G", True, 5.0, 8.0, "lte")

        assert sim._would_pass_with_threshold(gr, 5.0, "G") is True
        assert sim._would_pass_with_threshold(gr, 4.99, "G") is False
        assert sim._would_pass_with_threshold(gr, 6.0, "G") is True

    def test_get_threshold_value_valid_path(self):
        """Valid field path should return the config value."""
        policy = _make_policy()
        sim = ThresholdSimulator(policy, [], [])
        val = sim._get_threshold_value("gates.min_open_interest")
        assert val == 300.0  # default GateConfig.min_open_interest

    def test_get_threshold_value_invalid_path(self):
        """Invalid field path should return 0.0."""
        policy = _make_policy()
        sim = ThresholdSimulator(policy, [], [])
        val = sim._get_threshold_value("gates.nonexistent_field")
        assert val == 0.0
