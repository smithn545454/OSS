"""Tests for gates/models.py.

Covers GateContext.from_evaluation and GateEvaluation properties.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.schemas import GateOperator, GateResult, PillarId, PillarWeights
from app.gates.models import GateContext, GateEvaluation
from app.pillars.models import PillarResult


def _make_gate_result(gate_id: str, passed: bool, enabled: bool = True) -> GateResult:
    return GateResult(
        evaluation_id="e1",
        gate_id=gate_id,
        enabled=enabled,
        passed=passed,
        measured_value=100.0,
        threshold_value=50.0,
        operator=GateOperator.GTE,
        units="test",
        reason_code=f"PASS_{gate_id}" if passed else f"FAIL_{gate_id}",
    )


class TestGateEvaluation:
    def test_all_passed_true(self):
        ge = GateEvaluation(
            evaluation_id="e1",
            gate_results=[
                _make_gate_result("G1", True),
                _make_gate_result("G2", True),
            ],
        )
        assert ge.all_passed is True
        assert ge.passed_count == 2
        assert ge.failed_count == 0

    def test_all_passed_false(self):
        ge = GateEvaluation(
            evaluation_id="e1",
            gate_results=[
                _make_gate_result("G1", True),
                _make_gate_result("G2", False),
            ],
        )
        assert ge.all_passed is False
        assert ge.failed_gates == ["G2"]
        assert ge.failed_reason_codes == ["FAIL_G2"]
        assert ge.failed_count == 1

    def test_skipped(self):
        ge = GateEvaluation(
            evaluation_id="e1",
            gate_results=[
                _make_gate_result("G1", True),
                _make_gate_result("G2", False, enabled=False),
            ],
        )
        assert ge.all_passed is True  # Disabled gates don't count
        assert ge.skipped_count == 1

    def test_get_result(self):
        ge = GateEvaluation(
            evaluation_id="e1",
            gate_results=[_make_gate_result("G1", True)],
        )
        assert ge.get_result("G1") is not None
        assert ge.get_result("MISSING") is None


class TestGateContextFromEvaluationAndFeatures:
    def _eval_mock(self):
        m = MagicMock()
        m.evaluation_id = "e1"
        m.underlying_ticker = "AAPL"
        m.option_ticker = "O:AAPL"
        m.option_type = "CALL"
        m.dte = 45
        m.strike = 150.0
        m.underlying_price = 145.0
        m.open_interest = 500
        m.volume = 200
        m.spread_pct = 3.0
        m.iv = 0.30
        m.bid = 5.0
        m.ask = 5.50
        m.mid = 5.25
        m.theta = -0.10
        m.time_adjusted_feasibility = 1.2
        return m

    def test_basic(self):
        ctx = GateContext.from_evaluation_and_features(self._eval_mock())
        assert ctx.evaluation_id == "e1"
        assert ctx.dte == 45
        assert ctx.open_interest == 500

    def test_with_feature_set(self):
        fs_mock = MagicMock()
        fs_mock.trend_aligned_bullish = True
        fs_mock.trend_aligned_bearish = False
        fs_mock.return_5d = 5.0
        fs_mock.return_20d = 10.0
        fs_mock.iv_rv_ratio = 0.9
        fs_mock.feasibility_ratio = 1.5
        fs_mock.iv_percentile = 35.0

        ctx = GateContext.from_evaluation_and_features(self._eval_mock(), feature_set=fs_mock)
        assert ctx.trend_aligned_bullish is True
        assert ctx.iv_rv_ratio == 0.9
        assert ctx.iv_percentile == 35.0

    def test_with_pillar_results(self):
        pillar_results = [
            PillarResult(pillar_id=PillarId.DIRECTIONAL, evaluation_id="e1", score=80.0),
            PillarResult(pillar_id=PillarId.VOLATILITY, evaluation_id="e1", score=70.0),
            PillarResult(pillar_id=PillarId.STRUCTURE, evaluation_id="e1", score=60.0),
        ]

        ctx = GateContext.from_evaluation_and_features(
            self._eval_mock(), pillar_results=pillar_results, pillar_weights=PillarWeights()
        )
        assert ctx.combined_score is not None
        assert ctx.pillar_scores is not None
        assert len(ctx.pillar_scores) == 3

    def test_with_opportunity(self):
        opp = MagicMock()
        trigger = MagicMock()
        trigger.scanner_type = "BREAKOUT"
        trigger.metrics = {"volume_ratio": 2.5}
        opp.scanner_triggers = [trigger]

        ctx = GateContext.from_evaluation_and_features(self._eval_mock(), opportunity=opp)
        assert "BREAKOUT" in ctx.scanner_triggers
        assert ctx.volume_ratio == 2.5

    def test_theta_pct_calculation(self):
        """theta_pct = |theta| / mid * 100."""
        ctx = GateContext.from_evaluation_and_features(self._eval_mock())
        expected = abs(-0.10) / 5.25 * 100
        assert ctx.theta_pct is not None
        assert abs(ctx.theta_pct - expected) < 0.01

    def test_zero_mid_no_theta_pct(self):
        """When mid is 0, theta_pct should be None."""
        m = self._eval_mock()
        m.mid = 0.0
        ctx = GateContext.from_evaluation_and_features(m)
        assert ctx.theta_pct is None
