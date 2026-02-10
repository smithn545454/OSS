"""Tests for gates/stage.py HardGatesStage.

Covers:
- execute() with empty evaluations
- execute() with mixed pass/fail results
- _persist_results run_id stamping
- _persist_results partial failure → RuntimeError
- persist_results=False skips DB writes
- run_hard_gates convenience function
- extract_gate_results_for_decision
- get_evaluations_passing_gates / get_evaluations_failing_gates
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.schemas import (
    DTEBucket,
    Evaluation,
    GateConfig,
    GateOperator,
    GateResult,
    Opportunity,
    OptionType,
    PipelineStage,
    ScannerTrigger,
    ScannerType,
    DirectionHint,
)
from app.gates.models import GateEvaluation
from app.gates.stage import (
    HardGatesStage,
    extract_gate_results_for_decision,
    get_evaluations_failing_gates,
    get_evaluations_passing_gates,
    run_hard_gates,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_evaluation(eval_id="eval-001"):
    return Evaluation(
        evaluation_id=eval_id,
        opportunity_id="opp-001",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL260320C00185000",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=62,
        strike=185.0,
        underlying_price=189.0,
        moneyness_pct=-2.12,
        bid=8.50, ask=8.80, mid=8.65,
        spread_abs=0.30, spread_pct=3.47,
        iv=0.32, delta=0.55, gamma=0.03, theta=-0.08, vega=0.25,
        open_interest=5000, volume=500,
        breakeven_price=193.65,
        required_move_pct=2.46, expected_move_pct=5.0,
        feasibility_ratio=0.49, time_adjusted_feasibility=0.45,
        dte_bucket=DTEBucket.C,
        rank_score=85.0,
        policy_version="v2.0.0",
        policy_hash="test-hash",
    )


def _make_gate_result(eval_id="eval-001", gate_id="GATE_MIN_OI", passed=True):
    return GateResult(
        evaluation_id=eval_id,
        gate_id=gate_id,
        enabled=True,
        passed=passed,
        measured_value=5000,
        threshold_value=300,
        operator=GateOperator.GTE,
        units="contracts",
        reason_code="OI_SUFFICIENT" if passed else "OI_INSUFFICIENT",
    )


def _make_gate_evaluation(eval_id="eval-001", all_passed=True, gate_count=3):
    # When all_passed=True, all gates pass. When False, GATE_0 fails.
    results = [
        _make_gate_result(eval_id, f"GATE_{i}", passed=(all_passed or i > 0))
        for i in range(gate_count)
    ]
    # GateEvaluation is a dataclass; all_passed, failed_gates, etc. are computed properties
    return GateEvaluation(
        evaluation_id=eval_id,
        gate_results=results,
    )


def _make_orchestrator():
    orch = AsyncMock()
    orch.update_current_stage.return_value = None
    orch.record_stage_event.return_value = MagicMock()
    return orch


# ============================================================================
# HardGatesStage.execute
# ============================================================================


class TestHardGatesStageExecute:

    @pytest.mark.asyncio
    async def test_empty_evaluations(self):
        orch = _make_orchestrator()
        stage = HardGatesStage(orchestrator=orch)

        result = await stage.execute(
            run_id="run-001",
            evaluations=[],
            feature_sets=[],
            opportunities=[],
        )

        assert result == {}
        orch.record_stage_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_mixed_pass_fail_results(self):
        orch = _make_orchestrator()
        gate_eval_pass = _make_gate_evaluation("e1", all_passed=True)
        gate_eval_fail = _make_gate_evaluation("e2", all_passed=False)

        mock_calc = MagicMock()
        mock_calc.evaluate_gates_batch.return_value = {
            "e1": gate_eval_pass,
            "e2": gate_eval_fail,
        }
        mock_calc.get_failure_summary.return_value = {"GATE_0": 1}

        with patch("app.gates.stage.GateCalculator", return_value=mock_calc), \
             patch("app.gates.stage.GateResultTable.put_batch", new_callable=AsyncMock):
            stage = HardGatesStage(orchestrator=orch)
            result = await stage.execute(
                run_id="run-001",
                evaluations=[_make_evaluation("e1"), _make_evaluation("e2")],
                feature_sets=[],
                opportunities=[],
            )

        assert len(result) == 2
        assert result["e1"].all_passed is True
        assert result["e2"].all_passed is False

        # Verify stage event was called with correct items_out (only passed)
        event_kwargs = orch.record_stage_event.call_args.kwargs
        assert event_kwargs["items_in"] == 2
        assert event_kwargs["items_out"] == 1

    @pytest.mark.asyncio
    async def test_persist_false_skips_db_writes(self):
        orch = _make_orchestrator()
        gate_eval = _make_gate_evaluation("e1", all_passed=True)

        mock_calc = MagicMock()
        mock_calc.evaluate_gates_batch.return_value = {"e1": gate_eval}
        mock_calc.get_failure_summary.return_value = {}

        with patch("app.gates.stage.GateCalculator", return_value=mock_calc), \
             patch("app.gates.stage.GateResultTable.put_batch", new_callable=AsyncMock) as mock_put:
            stage = HardGatesStage(orchestrator=orch)
            await stage.execute(
                run_id="run-001",
                evaluations=[_make_evaluation("e1")],
                feature_sets=[],
                opportunities=[],
                persist_results=False,
            )

        mock_put.assert_not_called()


# ============================================================================
# _persist_results
# ============================================================================


class TestPersistResults:

    @pytest.mark.asyncio
    async def test_stamps_run_id_on_gate_results(self):
        orch = _make_orchestrator()
        stage = HardGatesStage(orchestrator=orch)

        gate_eval = _make_gate_evaluation("e1", all_passed=True, gate_count=2)

        captured_results = []

        async def capture_batch(results):
            captured_results.extend(results)

        with patch("app.gates.stage.GateResultTable.put_batch", side_effect=capture_batch):
            await stage._persist_results("run-XYZ", {"e1": gate_eval})

        # All results should have run_id stamped
        for r in captured_results:
            assert r.run_id == "run-XYZ"

    @pytest.mark.asyncio
    async def test_partial_failure_raises_runtime_error(self):
        orch = _make_orchestrator()
        stage = HardGatesStage(orchestrator=orch)

        gate_eval1 = _make_gate_evaluation("e1", all_passed=True)
        gate_eval2 = _make_gate_evaluation("e2", all_passed=True)

        call_count = 0

        async def fail_on_second(results):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("DynamoDB write error")

        with patch("app.gates.stage.GateResultTable.put_batch", side_effect=fail_on_second):
            with pytest.raises(RuntimeError, match="Failed to persist"):
                await stage._persist_results(
                    "run-001", {"e1": gate_eval1, "e2": gate_eval2}
                )

    @pytest.mark.asyncio
    async def test_all_writes_succeed(self):
        orch = _make_orchestrator()
        stage = HardGatesStage(orchestrator=orch)

        gate_eval = _make_gate_evaluation("e1", all_passed=True)

        with patch("app.gates.stage.GateResultTable.put_batch", new_callable=AsyncMock):
            # Should not raise
            await stage._persist_results("run-001", {"e1": gate_eval})


# ============================================================================
# run_hard_gates convenience function
# ============================================================================


class TestRunHardGates:

    @pytest.mark.asyncio
    async def test_delegates_to_stage(self):
        orch = _make_orchestrator()
        mock_calc = MagicMock()
        mock_calc.evaluate_gates_batch.return_value = {}
        mock_calc.get_failure_summary.return_value = {}

        with patch("app.gates.stage.GateCalculator", return_value=mock_calc):
            result = await run_hard_gates(
                run_id="run-001",
                evaluations=[],
                feature_sets=[],
                opportunities=[],
                orchestrator=orch,
            )

        assert result == {}


# ============================================================================
# extract_gate_results_for_decision
# ============================================================================


class TestExtractGateResults:

    def test_extracts_correctly(self):
        ge_pass = _make_gate_evaluation("e1", all_passed=True, gate_count=3)
        ge_fail = _make_gate_evaluation("e2", all_passed=False, gate_count=3)

        extracted = extract_gate_results_for_decision({"e1": ge_pass, "e2": ge_fail})

        assert extracted["e1"]["all_passed"] is True
        assert extracted["e1"]["failed_gates"] == []
        assert extracted["e2"]["all_passed"] is False
        assert "GATE_0" in extracted["e2"]["failed_gates"]

    def test_empty_input(self):
        assert extract_gate_results_for_decision({}) == {}


# ============================================================================
# get_evaluations_passing_gates / get_evaluations_failing_gates
# ============================================================================


class TestGateFiltering:

    def test_passing_gates(self):
        evals = [_make_evaluation("e1"), _make_evaluation("e2")]
        results = {
            "e1": _make_gate_evaluation("e1", all_passed=True),
            "e2": _make_gate_evaluation("e2", all_passed=False),
        }

        passing = get_evaluations_passing_gates(evals, results)
        assert len(passing) == 1
        assert passing[0].evaluation_id == "e1"

    def test_failing_gates(self):
        evals = [_make_evaluation("e1"), _make_evaluation("e2")]
        results = {
            "e1": _make_gate_evaluation("e1", all_passed=True),
            "e2": _make_gate_evaluation("e2", all_passed=False),
        }

        failing = get_evaluations_failing_gates(evals, results)
        assert len(failing) == 1
        assert failing[0].evaluation_id == "e2"

    def test_all_passing(self):
        evals = [_make_evaluation("e1")]
        results = {"e1": _make_gate_evaluation("e1", all_passed=True)}

        assert len(get_evaluations_passing_gates(evals, results)) == 1
        assert len(get_evaluations_failing_gates(evals, results)) == 0

    def test_eval_not_in_results(self):
        """Evaluations not in gate_results are excluded from both lists."""
        evals = [_make_evaluation("e1"), _make_evaluation("e_missing")]
        results = {"e1": _make_gate_evaluation("e1", all_passed=True)}

        passing = get_evaluations_passing_gates(evals, results)
        failing = get_evaluations_failing_gates(evals, results)
        assert len(passing) == 1
        assert len(failing) == 0
