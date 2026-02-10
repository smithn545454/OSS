"""Tests for the shadow tracking module (paper_trading/shadow_tracker.py).

Pure business logic — no DB calls needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.schemas import (
    Decision,
    GateOperator,
    GateResult,
    TrackingConfig,
    Verdict,
)
from app.gates.models import GateEvaluation
from app.paper_trading.models import ShadowPosition
from app.paper_trading.shadow_tracker import (
    analyze_shadow_tracking_results,
    create_shadow_positions,
    determine_sample_type,
    get_false_negative_insights,
    is_near_miss,
    is_single_gate_failure,
    select_shadow_candidates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decision(eval_id, verdict=Verdict.REJECT, score=50.0, reason="REJECTED_BY_SCORE", failed=None):
    return Decision(
        evaluation_id=eval_id,
        verdict=verdict,
        final_score=score,
        directional_score=50.0,
        volatility_score=50.0,
        structure_score=50.0,
        primary_reason_code=reason,
        supporting_reason_codes=[],
        failed_gates=failed or [],
        concentration_warnings=[],
        policy_version="v2.0.0",
    )


def _gate_eval(eval_id, num_failures=0):
    results = []
    for i in range(3):
        results.append(GateResult(
            evaluation_id=eval_id,
            gate_id=f"GATE_{i}",
            enabled=True,
            passed=(i >= num_failures),
            measured_value=100.0,
            threshold_value=50.0,
            operator=GateOperator.GTE,
            units="test",
            reason_code="TEST",
        ))
    return GateEvaluation(evaluation_id=eval_id, gate_results=results)


def _eval_mock(eval_id):
    from app.core.schemas import DTEBucket, Evaluation, OptionType

    return Evaluation(
        evaluation_id=eval_id,
        opportunity_id="opp-1",
        underlying_ticker="AAPL",
        option_ticker=f"O:AAPL260320C00{eval_id}",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=62, strike=185.0, underlying_price=189.0, moneyness_pct=-2.0,
        bid=8.0, ask=8.5, mid=8.25, spread_abs=0.5, spread_pct=6.0,
        iv=0.30, delta=0.55, gamma=0.03, theta=-0.08, vega=0.25,
        open_interest=5000, volume=500, breakeven_price=193.25,
        required_move_pct=2.25, expected_move_pct=5.0,
        feasibility_ratio=0.45, time_adjusted_feasibility=0.40,
        dte_bucket=DTEBucket.C, rank_score=80.0,
        policy_version="v2.0.0", policy_hash="hash",
    )


def _shadow(eval_id, sample_type="RANDOM", peak=0.0, target=False, stop=False, trough=0.0, days=5):
    return ShadowPosition(
        evaluation_id=eval_id,
        option_ticker=f"O:TEST{eval_id}",
        entry_price=5.0,
        entry_date="2026-01-17",
        rejection_reason="REJECTED_BY_SCORE",
        final_score=50.0,
        failed_gates=[],
        sample_type=sample_type,
        current_price=5.0,
        current_pnl_pct=0.0,
        peak_pnl_pct=peak,
        trough_pnl_pct=trough,
        would_have_hit_target=target,
        would_have_hit_stop=stop,
        days_tracked=days,
    )


# ---------------------------------------------------------------------------
# Tests: is_near_miss
# ---------------------------------------------------------------------------


class TestIsNearMiss:

    def test_near_miss_in_range(self):
        d = _decision("e1", score=62.0, reason="REJECTED_BY_SCORE")
        assert is_near_miss(d) is True

    def test_not_near_miss_too_low(self):
        d = _decision("e1", score=55.0)
        assert is_near_miss(d) is False

    def test_not_near_miss_too_high(self):
        d = _decision("e1", score=66.0)
        assert is_near_miss(d) is False

    def test_not_near_miss_gate_rejection(self):
        d = _decision("e1", score=62.0, reason="REJECTED_BY_GATES")
        assert is_near_miss(d) is False

    def test_boundary_at_60(self):
        d = _decision("e1", score=60.0)
        assert is_near_miss(d) is True

    def test_boundary_at_65(self):
        d = _decision("e1", score=65.0)
        assert is_near_miss(d) is False  # < 65, not <=


# ---------------------------------------------------------------------------
# Tests: is_single_gate_failure
# ---------------------------------------------------------------------------


class TestIsSingleGateFailure:

    def test_single_failure(self):
        ge = _gate_eval("e1", num_failures=1)
        assert is_single_gate_failure(ge) is True

    def test_zero_failures(self):
        ge = _gate_eval("e1", num_failures=0)
        assert is_single_gate_failure(ge) is False

    def test_two_failures(self):
        ge = _gate_eval("e1", num_failures=2)
        assert is_single_gate_failure(ge) is False


# ---------------------------------------------------------------------------
# Tests: determine_sample_type
# ---------------------------------------------------------------------------


class TestDetermineSampleType:

    def test_near_miss(self):
        d = _decision("e1", score=62.0)
        assert determine_sample_type(d, None) == "NEAR_MISS"

    def test_single_gate(self):
        d = _decision("e1", score=50.0, reason="REJECTED_BY_GATES")
        ge = _gate_eval("e1", num_failures=1)
        assert determine_sample_type(d, ge) == "SINGLE_GATE"

    def test_random(self):
        d = _decision("e1", score=30.0, reason="REJECTED_BY_GATES")
        ge = _gate_eval("e1", num_failures=2)
        assert determine_sample_type(d, ge) == "RANDOM"


# ---------------------------------------------------------------------------
# Tests: select_shadow_candidates
# ---------------------------------------------------------------------------


class TestSelectShadowCandidates:

    def test_selects_near_miss(self):
        evals = [_eval_mock("e1")]
        decisions = {"e1": _decision("e1", score=62.0)}
        gates = {"e1": _gate_eval("e1", 0)}
        result = select_shadow_candidates(evals, decisions, gates)
        assert "e1" in result

    def test_selects_single_gate_failure(self):
        evals = [_eval_mock("e1")]
        decisions = {"e1": _decision("e1", score=30.0, reason="REJECTED_BY_GATES")}
        gates = {"e1": _gate_eval("e1", 1)}
        result = select_shadow_candidates(evals, decisions, gates)
        assert "e1" in result

    def test_no_rejects_returns_empty(self):
        evals = [_eval_mock("e1")]
        decisions = {"e1": _decision("e1", verdict=Verdict.APPROVE, score=85.0)}
        result = select_shadow_candidates(evals, decisions, {})
        assert result == []

    def test_random_sampling_from_pool(self):
        """REJECTs that aren't near-miss or single-gate go into random pool."""
        evals = [_eval_mock(f"e{i}") for i in range(20)]
        decisions = {
            f"e{i}": _decision(f"e{i}", score=30.0, reason="REJECTED_BY_GATES")
            for i in range(20)
        }
        gates = {f"e{i}": _gate_eval(f"e{i}", 2) for i in range(20)}

        result = select_shadow_candidates(evals, decisions, gates)
        assert len(result) >= 1  # At least 1 random sample


# ---------------------------------------------------------------------------
# Tests: create_shadow_positions
# ---------------------------------------------------------------------------


class TestCreateShadowPositions:

    def test_creates_positions_for_selected(self):
        evals = [_eval_mock("e1"), _eval_mock("e2")]
        decisions = {
            "e1": _decision("e1", score=62.0),
            "e2": _decision("e2", score=30.0, reason="REJECTED_BY_GATES"),
        }
        gates = {"e1": _gate_eval("e1", 0), "e2": _gate_eval("e2", 1)}

        positions = create_shadow_positions(evals, decisions, gates, ["e1", "e2"])
        assert len(positions) == 2
        assert all(isinstance(p, ShadowPosition) for p in positions)

    def test_skips_missing_evaluation(self):
        positions = create_shadow_positions([], {}, {}, ["e_missing"])
        assert len(positions) == 0

    def test_sample_type_assigned(self):
        evals = [_eval_mock("e1")]
        decisions = {"e1": _decision("e1", score=62.0)}
        gates = {"e1": _gate_eval("e1", 0)}

        positions = create_shadow_positions(evals, decisions, gates, ["e1"])
        assert positions[0].sample_type == "NEAR_MISS"


# ---------------------------------------------------------------------------
# Tests: analyze_shadow_tracking_results
# ---------------------------------------------------------------------------


class TestAnalyzeShadowResults:

    def test_empty_positions(self):
        result = analyze_shadow_tracking_results([])
        assert result["total_tracked"] == 0
        assert result["false_negatives"] == 0

    def test_no_false_negatives(self):
        shadows = [_shadow("e1", peak=5.0), _shadow("e2", peak=10.0)]
        result = analyze_shadow_tracking_results(shadows)
        assert result["total_tracked"] == 2
        assert result["false_negatives"] == 0
        assert result["false_negative_rate"] == 0.0

    def test_with_false_negatives(self):
        shadows = [
            _shadow("e1", peak=30.0),  # peak > 25 → false negative
            _shadow("e2", target=True),  # hit target → false negative
            _shadow("e3", peak=5.0),  # not a false negative
        ]
        result = analyze_shadow_tracking_results(shadows)
        assert result["false_negatives"] == 2
        assert abs(result["false_negative_rate"] - 66.67) < 0.1

    def test_by_sample_type_breakdown(self):
        shadows = [
            _shadow("e1", sample_type="NEAR_MISS", peak=30.0),
            _shadow("e2", sample_type="SINGLE_GATE", peak=5.0),
            _shadow("e3", sample_type="RANDOM", peak=2.0),
        ]
        result = analyze_shadow_tracking_results(shadows)
        assert "NEAR_MISS" in result["by_sample_type"]
        assert result["by_sample_type"]["NEAR_MISS"]["count"] == 1
        assert result["by_sample_type"]["NEAR_MISS"]["false_negatives"] == 1


# ---------------------------------------------------------------------------
# Tests: get_false_negative_insights
# ---------------------------------------------------------------------------


class TestFalseNegativeInsights:

    def test_no_false_negatives(self):
        shadows = [_shadow("e1", peak=5.0)]
        insights = get_false_negative_insights(shadows)
        assert len(insights) == 0

    def test_insights_for_false_negatives(self):
        shadows = [
            _shadow("e1", sample_type="SINGLE_GATE", peak=30.0, days=10),
            _shadow("e2", sample_type="NEAR_MISS", target=True, peak=55.0, days=15),
        ]
        # Make e1 have a failed gate
        shadows[0].failed_gates = ["GATE_MIN_OPEN_INTEREST"]

        insights = get_false_negative_insights(shadows)
        assert len(insights) == 2

        # Single gate recommendation
        single_gate = [i for i in insights if i["sample_type"] == "SINGLE_GATE"][0]
        assert "relaxing" in single_gate["recommendation"].lower()

        # Near miss recommendation
        near_miss = [i for i in insights if i["sample_type"] == "NEAR_MISS"][0]
        assert "threshold" in near_miss["recommendation"].lower()
