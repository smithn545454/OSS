"""Extended tests for the Decision Stage (Stage 7).

Covers the thesis generation path, concentration warning path,
_compute_stats, _persist_decisions, extract_decisions_for_paper_trading,
get_tier_1_evaluations, and additional helper functions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    Decision,
    DecisionConfig,
    Evaluation,
    GateOperator,
    GateResult,
    OptionType,
    PillarWeights,
    QualityTier,
    Verdict,
)
from app.gates.models import GateEvaluation
from app.pillars.models import PillarResult
from app.decision.stage import (
    DecisionStage,
    extract_decisions_for_paper_trading,
    get_approved_evaluations,
    get_tier_1_evaluations,
    get_watch_evaluations,
    get_rejected_evaluations,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    orch.update_current_stage.return_value = None
    orch.record_stage_event.return_value = MagicMock()
    return orch


def _gate_eval(eval_id, all_pass=True):
    results = [
        GateResult(
            evaluation_id=eval_id, gate_id=f"GATE_{i}", enabled=True,
            passed=all_pass or i > 0, measured_value=100.0,
            threshold_value=50.0, operator=GateOperator.GTE,
            units="test", reason_code="TEST",
        )
        for i in range(3)
    ]
    return GateEvaluation(evaluation_id=eval_id, gate_results=results)


def _pillar_results(eval_id, d=80, v=80, s=80):
    return [
        PillarResult(pillar_id="DIRECTIONAL", evaluation_id=eval_id, score=d, subscores=[], tags=[]),
        PillarResult(pillar_id="VOLATILITY", evaluation_id=eval_id, score=v, subscores=[], tags=[]),
        PillarResult(pillar_id="STRUCTURE", evaluation_id=eval_id, score=s, subscores=[], tags=[]),
    ]


def _decision(eval_id, verdict=Verdict.APPROVE, score=85.0, tier=None):
    return Decision(
        evaluation_id=eval_id,
        verdict=verdict,
        final_score=score,
        directional_score=80.0,
        volatility_score=80.0,
        structure_score=80.0,
        primary_reason_code="APPROVED" if verdict == Verdict.APPROVE else "REJECTED_BY_SCORE",
        supporting_reason_codes=[],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v2.0.0",
        quality_tier=tier,
    )


# ---------------------------------------------------------------------------
# Tests: _compute_stats
# ---------------------------------------------------------------------------


class TestComputeStats:

    def test_stats_with_all_verdicts(self, mock_orchestrator, sample_evaluation):
        stage = DecisionStage(mock_orchestrator)
        evals = [sample_evaluation]

        decisions = {
            sample_evaluation.evaluation_id: _decision(
                sample_evaluation.evaluation_id, Verdict.APPROVE, 85.0, QualityTier.TIER_1
            ),
        }

        stats = stage._compute_stats(evals, decisions)
        assert stats["approves"] == 1
        assert stats["total_actionable"] == 1
        assert stats["quality_tiers"]["tier_1"] == 1

    def test_stats_empty(self, mock_orchestrator):
        stage = DecisionStage(mock_orchestrator)
        stats = stage._compute_stats([], {})
        assert stats["approves"] == 0
        assert stats["watches"] == 0
        assert stats["rejects"] == 0

    def test_stats_with_concentration_warnings(self, mock_orchestrator, sample_evaluation):
        stage = DecisionStage(mock_orchestrator)
        d = Decision(
            evaluation_id=sample_evaluation.evaluation_id,
            verdict=Verdict.APPROVE, final_score=85.0,
            directional_score=80.0, volatility_score=80.0, structure_score=80.0,
            primary_reason_code="APPROVED",
            supporting_reason_codes=[], failed_gates=[],
            concentration_warnings=["SAME_TICKER_LIMIT_EXCEEDED", "DIRECTIONAL_SKEW"],
            policy_version="v2.0.0",
        )
        stats = stage._compute_stats([sample_evaluation], {sample_evaluation.evaluation_id: d})
        assert stats["concentration_warnings"]["ticker"] >= 1
        assert stats["concentration_warnings"]["directional"] >= 1


# ---------------------------------------------------------------------------
# Tests: _persist_decisions
# ---------------------------------------------------------------------------


class TestPersistDecisions:

    @pytest.mark.asyncio
    async def test_persist_decisions(self, mock_orchestrator, sample_evaluation):
        with patch("app.decision.stage.EvaluationTable") as mock_table:
            mock_table.put = AsyncMock()
            stage = DecisionStage(mock_orchestrator)
            d = _decision(sample_evaluation.evaluation_id)

            await stage._persist_decisions([sample_evaluation], {sample_evaluation.evaluation_id: d})

        mock_table.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_decisions_error_handling(self, mock_orchestrator, sample_evaluation):
        with patch("app.decision.stage.EvaluationTable") as mock_table:
            mock_table.put = AsyncMock(side_effect=Exception("DB error"))
            stage = DecisionStage(mock_orchestrator)
            d = _decision(sample_evaluation.evaluation_id)

            # Should not raise
            await stage._persist_decisions([sample_evaluation], {sample_evaluation.evaluation_id: d})


# ---------------------------------------------------------------------------
# Tests: execute with concentration
# ---------------------------------------------------------------------------


class TestExecuteWithConcentration:

    @pytest.mark.asyncio
    async def test_execute_with_concentration_check(self, mock_orchestrator, sample_evaluation):
        with patch("app.decision.stage.EvaluationTable"), \
             patch("app.decision.stage.TradeThesisTable"):
            stage = DecisionStage(mock_orchestrator)
            decisions, theses = await stage.execute(
                run_id="run-conc",
                evaluations=[sample_evaluation],
                pillar_results={
                    sample_evaluation.evaluation_id: _pillar_results(
                        sample_evaluation.evaluation_id, d=85, v=85, s=85
                    )
                },
                gate_evaluations={
                    sample_evaluation.evaluation_id: _gate_eval(
                        sample_evaluation.evaluation_id, all_pass=True
                    )
                },
                persist_decisions=False,
                check_concentration=True,
                generate_theses=False,
            )
        assert sample_evaluation.evaluation_id in decisions


# ---------------------------------------------------------------------------
# Tests: extract_decisions_for_paper_trading
# ---------------------------------------------------------------------------


class TestExtractForPaperTrading:

    def test_extracts_approve_and_watch(self):
        decisions = {
            "e1": _decision("e1", Verdict.APPROVE, 85.0, QualityTier.TIER_1),
            "e2": _decision("e2", Verdict.WATCH, 70.0),
            "e3": _decision("e3", Verdict.REJECT, 50.0),
        }
        result = extract_decisions_for_paper_trading(decisions)
        assert "e1" in result
        assert "e2" in result
        assert "e3" not in result
        assert result["e1"]["verdict"] == "APPROVE"

    def test_empty_decisions(self):
        result = extract_decisions_for_paper_trading({})
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: get_tier_1_evaluations
# ---------------------------------------------------------------------------


class TestGetTier1Evaluations:

    def test_filters_tier_1(self):
        e1 = MagicMock(evaluation_id="e1")
        e2 = MagicMock(evaluation_id="e2")
        decisions = {
            "e1": _decision("e1", Verdict.APPROVE, 90.0, QualityTier.TIER_1),
            "e2": _decision("e2", Verdict.APPROVE, 78.0, QualityTier.TIER_2),
        }
        result = get_tier_1_evaluations([e1, e2], decisions)
        assert len(result) == 1
        assert result[0].evaluation_id == "e1"
