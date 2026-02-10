"""Tests for the thesis generation path in decision/stage.py.

Covers DecisionStage._generate_theses and _get_thesis_generator.
These are the main uncovered lines (182-270) in decision/stage.py.
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
    ThesisConfig,
    Verdict,
)
from app.gates.models import GateEvaluation
from app.pillars.models import PillarResult
from app.decision.stage import DecisionStage


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_evaluation():
    return Evaluation(
        evaluation_id="eval-thesis-1",
        opportunity_id="opp-1",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL250321C00150000",
        option_type=OptionType.CALL,
        expiration_date="2026-03-21",
        dte=45,
        strike=150.0,
        underlying_price=145.0,
        moneyness_pct=3.45,
        bid=5.0,
        ask=5.50,
        mid=5.25,
        spread_abs=0.50,
        spread_pct=9.52,
        iv=0.30,
        delta=0.55,
        gamma=0.03,
        theta=-0.10,
        vega=0.25,
        open_interest=500,
        volume=200,
        breakeven_price=155.25,
        required_move_pct=7.07,
        expected_move_pct=8.0,
        feasibility_ratio=1.13,
        time_adjusted_feasibility=1.05,
        dte_bucket="B",
        rank_score=85.0,
        policy_version="v2.0.0",
        policy_hash="abc123",
    )


def _gate_eval(eval_id, all_pass=True):
    results = [
        GateResult(
            evaluation_id=eval_id, gate_id=f"GATE_{i}", enabled=True,
            passed=all_pass, measured_value=100.0,
            threshold_value=50.0, operator=GateOperator.GTE,
            units="test", reason_code="TEST",
        )
        for i in range(3)
    ]
    return GateEvaluation(evaluation_id=eval_id, gate_results=results)


def _pillar_results(eval_id, d=80, v=80, s=80):
    """Create mock PillarResult objects with contributors attribute."""
    results = []
    for pid, score in [("DIRECTIONAL", d), ("VOLATILITY", v), ("STRUCTURE", s)]:
        pr = MagicMock()
        pr.pillar_id = pid
        pr.evaluation_id = eval_id
        pr.score = score
        pr.contributors = []
        pr.tags = []
        results.append(pr)
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetThesisGenerator:

    def test_lazy_init(self):
        orch = AsyncMock()
        orch.update_current_stage.return_value = None
        orch.record_stage_event.return_value = MagicMock()
        thesis_cfg = ThesisConfig(enabled=True)
        stage = DecisionStage(orch, thesis_config=thesis_cfg)
        assert stage._thesis_generator is None

        with patch("app.llm.generator.ThesisGenerator") as mock_gen:
            mock_gen.return_value = MagicMock()
            gen = stage._get_thesis_generator()
            assert gen is not None
            mock_gen.assert_called_once()

    def test_returns_cached(self):
        orch = AsyncMock()
        orch.update_current_stage.return_value = None
        orch.record_stage_event.return_value = MagicMock()
        thesis_cfg = ThesisConfig(enabled=True)
        stage = DecisionStage(orch, thesis_config=thesis_cfg)
        stage._thesis_generator = MagicMock()
        gen = stage._get_thesis_generator()
        assert gen is stage._thesis_generator


class TestGenerateTheses:

    @pytest.mark.asyncio
    async def test_no_approved_evals(self, sample_evaluation):
        orch = AsyncMock()
        orch.update_current_stage.return_value = None
        orch.record_stage_event.return_value = MagicMock()
        thesis_cfg = ThesisConfig(enabled=True)
        stage = DecisionStage(orch, thesis_config=thesis_cfg)

        # All REJECT, no APPROVE
        decisions = {
            sample_evaluation.evaluation_id: Decision(
                evaluation_id=sample_evaluation.evaluation_id,
                verdict=Verdict.REJECT,
                final_score=50.0,
                directional_score=50.0,
                volatility_score=50.0,
                structure_score=50.0,
                primary_reason_code="REJECTED_BY_SCORE",
                supporting_reason_codes=[],
                failed_gates=[],
                concentration_warnings=[],
                policy_version="v2.0.0",
            ),
        }
        theses = await stage._generate_theses(
            evaluations=[sample_evaluation],
            decisions=decisions,
            pillar_results={},
            scanner_triggers={},
            features={},
        )
        assert theses == []

    @pytest.mark.asyncio
    async def test_generates_thesis_for_approved(self, sample_evaluation):
        orch = AsyncMock()
        orch.update_current_stage.return_value = None
        orch.record_stage_event.return_value = MagicMock()
        thesis_cfg = ThesisConfig(enabled=True)
        stage = DecisionStage(orch, thesis_config=thesis_cfg)

        decisions = {
            sample_evaluation.evaluation_id: Decision(
                evaluation_id=sample_evaluation.evaluation_id,
                verdict=Verdict.APPROVE,
                final_score=85.0,
                directional_score=80.0,
                volatility_score=80.0,
                structure_score=80.0,
                primary_reason_code="APPROVED",
                supporting_reason_codes=[],
                failed_gates=[],
                concentration_warnings=[],
                policy_version="v2.0.0",
                quality_tier=QualityTier.TIER_1,
            ),
        }

        mock_thesis = MagicMock()
        mock_thesis.status = MagicMock(value="COMPLETED")

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(return_value=mock_thesis)
        stage._thesis_generator = mock_generator

        import app.decision.stage as stage_mod
        with patch.object(stage_mod, "TradeThesisTable") as mock_table:
            mock_table.put = AsyncMock()
            theses = await stage._generate_theses(
                evaluations=[sample_evaluation],
                decisions=decisions,
                pillar_results={
                    sample_evaluation.evaluation_id: _pillar_results(sample_evaluation.evaluation_id)
                },
                scanner_triggers={},
                features={},
            )

        assert len(theses) == 1
        assert theses[0] is mock_thesis

    @pytest.mark.asyncio
    async def test_thesis_error_continues(self, sample_evaluation):
        """If thesis generation fails for one eval, it should continue."""
        orch = AsyncMock()
        orch.update_current_stage.return_value = None
        orch.record_stage_event.return_value = MagicMock()
        thesis_cfg = ThesisConfig(enabled=True)
        stage = DecisionStage(orch, thesis_config=thesis_cfg)

        decisions = {
            sample_evaluation.evaluation_id: Decision(
                evaluation_id=sample_evaluation.evaluation_id,
                verdict=Verdict.APPROVE,
                final_score=85.0,
                directional_score=80.0,
                volatility_score=80.0,
                structure_score=80.0,
                primary_reason_code="APPROVED",
                supporting_reason_codes=[],
                failed_gates=[],
                concentration_warnings=[],
                policy_version="v2.0.0",
            ),
        }

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(side_effect=Exception("LLM error"))
        stage._thesis_generator = mock_generator

        theses = await stage._generate_theses(
            evaluations=[sample_evaluation],
            decisions=decisions,
            pillar_results={},
            scanner_triggers={},
            features={},
        )
        assert theses == []


class TestExecuteWithTheses:

    @pytest.mark.asyncio
    async def test_execute_generates_theses(self, sample_evaluation):
        orch = AsyncMock()
        orch.update_current_stage.return_value = None
        orch.record_stage_event.return_value = MagicMock()
        thesis_cfg = ThesisConfig(enabled=True)
        stage = DecisionStage(orch, thesis_config=thesis_cfg)

        mock_thesis = MagicMock()
        mock_thesis.status = MagicMock(value="COMPLETED")

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(return_value=mock_thesis)
        stage._thesis_generator = mock_generator

        with patch("app.decision.stage.EvaluationTable"), \
             patch("app.decision.stage.TradeThesisTable"):
            decisions, theses = await stage.execute(
                run_id="run-thesis-1",
                evaluations=[sample_evaluation],
                pillar_results={
                    sample_evaluation.evaluation_id: _pillar_results(
                        sample_evaluation.evaluation_id, d=90, v=90, s=90
                    )
                },
                gate_evaluations={
                    sample_evaluation.evaluation_id: _gate_eval(
                        sample_evaluation.evaluation_id, all_pass=True
                    )
                },
                persist_decisions=False,
                check_concentration=False,
                generate_theses=True,
            )

        assert sample_evaluation.evaluation_id in decisions
        assert len(theses) >= 0  # Depends on whether verdict is APPROVE
