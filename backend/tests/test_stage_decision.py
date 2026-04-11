"""Tests for the Decision Stage (Stage 7).

Covers DecisionStage.execute(), verdict filtering helpers,
and the convenience run_decision_logic().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    Decision,
    DecisionConfig,
    Evaluation,
    GateConfig,
    GateOperator,
    GateResult,
    OptionType,
    PillarWeights,
    Verdict,
)
from app.gates.models import GateEvaluation
from app.pillars.models import PillarResult
from app.decision.stage import (
    DecisionStage,
    get_approved_evaluations,
    get_watch_evaluations,
    get_rejected_evaluations,
    run_decision_logic,
)


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    orch.update_current_stage.return_value = None
    orch.record_stage_event.return_value = MagicMock()
    return orch


def _make_gate_eval(eval_id: str, all_pass: bool = True) -> GateEvaluation:
    """Create a GateEvaluation with all gates passing or one failing."""
    results = [
        GateResult(
            evaluation_id=eval_id,
            gate_id=f"GATE_{i}",
            enabled=True,
            passed=all_pass or i > 0,  # If not all_pass, first gate fails
            measured_value=100.0,
            threshold_value=50.0,
            operator=GateOperator.GTE,
            units="test",
            reason_code="TEST",
        )
        for i in range(3)
    ]
    return GateEvaluation(evaluation_id=eval_id, gate_results=results)


def _make_pillar_results(eval_id: str, d=80, v=80, s=80):
    """Create pillar results for an evaluation (Policy v3.0.0).

    The d/v/s parameter names are kept for test backward compat and map to
    premium_leverage/underlying_behavior/setup_quality respectively.
    """
    return [
        PillarResult(pillar_id="PREMIUM_LEVERAGE", evaluation_id=eval_id, score=d, subscores=[], tags=[]),
        PillarResult(pillar_id="UNDERLYING_BEHAVIOR", evaluation_id=eval_id, score=v, subscores=[], tags=[]),
        PillarResult(pillar_id="SETUP_QUALITY", evaluation_id=eval_id, score=s, subscores=[], tags=[]),
    ]


class TestDecisionStage:
    """Test DecisionStage.execute()."""

    @pytest.mark.asyncio
    async def test_execute_approve_verdict(
        self,
        mock_orchestrator,
        sample_evaluation,
    ):
        """High pillar scores with passing gates should produce APPROVE."""
        with patch("app.decision.stage.EvaluationTable"), \
             patch("app.decision.stage.TradeThesisTable"):
            stage = DecisionStage(mock_orchestrator)
            decisions, theses = await stage.execute(
                run_id="run-001",
                evaluations=[sample_evaluation],
                pillar_results={
                    sample_evaluation.evaluation_id: _make_pillar_results(
                        sample_evaluation.evaluation_id, d=85, v=85, s=85
                    )
                },
                gate_evaluations={
                    sample_evaluation.evaluation_id: _make_gate_eval(
                        sample_evaluation.evaluation_id, all_pass=True
                    )
                },
                persist_decisions=False,
                check_concentration=False,
                generate_theses=False,
            )
        assert sample_evaluation.evaluation_id in decisions
        assert decisions[sample_evaluation.evaluation_id].verdict == Verdict.APPROVE

    @pytest.mark.asyncio
    async def test_execute_reject_on_gate_failure(
        self,
        mock_orchestrator,
        sample_evaluation,
    ):
        """Failed gates should produce REJECT regardless of scores."""
        with patch("app.decision.stage.EvaluationTable"), \
             patch("app.decision.stage.TradeThesisTable"):
            stage = DecisionStage(mock_orchestrator)
            decisions, theses = await stage.execute(
                run_id="run-002",
                evaluations=[sample_evaluation],
                pillar_results={
                    sample_evaluation.evaluation_id: _make_pillar_results(
                        sample_evaluation.evaluation_id, d=90, v=90, s=90
                    )
                },
                gate_evaluations={
                    sample_evaluation.evaluation_id: _make_gate_eval(
                        sample_evaluation.evaluation_id, all_pass=False
                    )
                },
                persist_decisions=False,
                check_concentration=False,
                generate_theses=False,
            )
        assert decisions[sample_evaluation.evaluation_id].verdict == Verdict.REJECT

    @pytest.mark.asyncio
    async def test_execute_empty_evaluations(self, mock_orchestrator):
        """Empty evaluations should return empty dicts."""
        with patch("app.decision.stage.EvaluationTable"), \
             patch("app.decision.stage.TradeThesisTable"):
            stage = DecisionStage(mock_orchestrator)
            decisions, theses = await stage.execute(
                run_id="run-003",
                evaluations=[],
                pillar_results={},
                gate_evaluations={},
                persist_decisions=False,
                check_concentration=False,
                generate_theses=False,
            )
        assert decisions == {}
        assert theses == []


class TestDecisionHelpers:
    """Test verdict filtering helper functions."""

    @pytest.fixture
    def decisions_fixture(self, sample_evaluation):
        """Set of decisions with different verdicts."""
        return {
            "eval-approve": Decision(
                evaluation_id="eval-approve",
                verdict=Verdict.APPROVE,
                final_score=85.0, premium_leverage_score=80.0,
                underlying_behavior_score=85.0, setup_quality_score=80.0,
                primary_reason_code="APPROVED",
                supporting_reason_codes=[], failed_gates=[],
                concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-watch": Decision(
                evaluation_id="eval-watch",
                verdict=Verdict.WATCH,
                final_score=70.0, premium_leverage_score=70.0,
                underlying_behavior_score=70.0, setup_quality_score=70.0,
                primary_reason_code="WATCH_BY_SCORE",
                supporting_reason_codes=[], failed_gates=[],
                concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-reject": Decision(
                evaluation_id="eval-reject",
                verdict=Verdict.REJECT,
                final_score=50.0, premium_leverage_score=50.0,
                underlying_behavior_score=50.0, setup_quality_score=50.0,
                primary_reason_code="REJECTED_BY_SCORE",
                supporting_reason_codes=[], failed_gates=[],
                concentration_warnings=[], policy_version="v2.0.0",
            ),
        }

    def _make_eval(self, eval_id):
        return MagicMock(evaluation_id=eval_id)

    def test_get_approved_evaluations(self, decisions_fixture):
        evals = [self._make_eval(eid) for eid in decisions_fixture]
        result = get_approved_evaluations(evals, decisions_fixture)
        assert len(result) == 1
        assert result[0].evaluation_id == "eval-approve"

    def test_get_watch_evaluations(self, decisions_fixture):
        evals = [self._make_eval(eid) for eid in decisions_fixture]
        result = get_watch_evaluations(evals, decisions_fixture)
        assert len(result) == 1
        assert result[0].evaluation_id == "eval-watch"

    def test_get_rejected_evaluations(self, decisions_fixture):
        evals = [self._make_eval(eid) for eid in decisions_fixture]
        result = get_rejected_evaluations(evals, decisions_fixture)
        assert len(result) == 1
        assert result[0].evaluation_id == "eval-reject"


class TestRunDecisionLogic:
    """Test the convenience run_decision_logic function."""

    @pytest.mark.asyncio
    async def test_run_decision_logic_delegates(
        self,
        mock_orchestrator,
        sample_evaluation,
    ):
        """run_decision_logic should delegate to DecisionStage.execute."""
        with patch("app.decision.stage.EvaluationTable"), \
             patch("app.decision.stage.TradeThesisTable"):
            decisions, theses = await run_decision_logic(
                run_id="run-006",
                evaluations=[sample_evaluation],
                pillar_results={
                    sample_evaluation.evaluation_id: _make_pillar_results(
                        sample_evaluation.evaluation_id
                    )
                },
                gate_evaluations={
                    sample_evaluation.evaluation_id: _make_gate_eval(
                        sample_evaluation.evaluation_id
                    )
                },
                orchestrator=mock_orchestrator,
                persist_decisions=False,
                check_concentration=False,
                generate_theses=False,
            )
        assert isinstance(decisions, dict)
