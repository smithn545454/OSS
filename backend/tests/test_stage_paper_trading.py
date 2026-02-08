"""Tests for the Paper Trading Stage (Stage 8).

Covers PaperTradingStage.execute(), position creation,
shadow tracking, and helper functions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    Decision,
    Evaluation,
    GateConfig,
    GateOperator,
    GateResult,
    OptionType,
    QualityTier,
    TrackingConfig,
    Verdict,
)
from app.gates.models import GateEvaluation
from app.paper_trading.stage import (
    PaperTradingStage,
    get_actionable_decisions,
    run_paper_trading,
)


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    orch.update_current_stage.return_value = None
    orch.record_stage_event.return_value = MagicMock()
    return orch


def _make_gate_eval(eval_id, all_pass=True):
    results = [
        GateResult(
            evaluation_id=eval_id, gate_id="GATE_1",
            enabled=True, passed=all_pass,
            measured_value=100, threshold_value=50,
            operator=GateOperator.GTE, units="test", reason_code="TEST",
        )
    ]
    return GateEvaluation(evaluation_id=eval_id, gate_results=results)


class TestPaperTradingStage:
    """Test PaperTradingStage.execute()."""

    @pytest.mark.asyncio
    async def test_execute_with_approve_decision(
        self,
        mock_orchestrator,
        sample_evaluation,
        sample_decision,
    ):
        """APPROVE decisions should trigger position creation."""
        decisions = {sample_evaluation.evaluation_id: sample_decision}
        gate_evals = {
            sample_evaluation.evaluation_id: _make_gate_eval(
                sample_evaluation.evaluation_id
            )
        }

        mock_pos = MagicMock()
        mock_pos.verdict_at_entry = Verdict.APPROVE

        with patch("app.paper_trading.stage.create_positions_from_decisions") as mock_create, \
             patch("app.paper_trading.stage.create_shadow_positions") as mock_shadow, \
             patch("app.paper_trading.stage.select_shadow_candidates") as mock_candidates:
            mock_create.return_value = [mock_pos]  # Returns list of positions
            mock_shadow.return_value = []
            mock_candidates.return_value = []

            stage = PaperTradingStage(mock_orchestrator)
            result = await stage.execute(
                run_id="run-001",
                evaluations=[sample_evaluation],
                decisions=decisions,
                gate_evaluations=gate_evals,
                create_positions=True,
                track_shadows=True,
            )

        assert isinstance(result, dict)
        assert result["positions_created"] == 1
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_empty_evaluations(self, mock_orchestrator):
        """Empty evaluations should return results without error."""
        with patch("app.paper_trading.stage.create_positions_from_decisions") as mock_create, \
             patch("app.paper_trading.stage.create_shadow_positions") as mock_shadow, \
             patch("app.paper_trading.stage.select_shadow_candidates") as mock_candidates:
            mock_create.return_value = []
            mock_shadow.return_value = []
            mock_candidates.return_value = []

            stage = PaperTradingStage(mock_orchestrator)
            result = await stage.execute(
                run_id="run-002",
                evaluations=[],
                decisions={},
                gate_evaluations={},
            )
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_execute_skip_position_creation(
        self,
        mock_orchestrator,
        sample_evaluation,
        sample_decision,
    ):
        """create_positions=False should skip position creation."""
        decisions = {sample_evaluation.evaluation_id: sample_decision}
        gate_evals = {
            sample_evaluation.evaluation_id: _make_gate_eval(
                sample_evaluation.evaluation_id
            )
        }

        with patch("app.paper_trading.stage.create_positions_from_decisions") as mock_create, \
             patch("app.paper_trading.stage.create_shadow_positions") as mock_shadow, \
             patch("app.paper_trading.stage.select_shadow_candidates") as mock_candidates:
            mock_create.return_value = []
            mock_shadow.return_value = []
            mock_candidates.return_value = []

            stage = PaperTradingStage(mock_orchestrator)
            result = await stage.execute(
                run_id="run-003",
                evaluations=[sample_evaluation],
                decisions=decisions,
                gate_evaluations=gate_evals,
                create_positions=False,
                track_shadows=False,
            )
        assert isinstance(result, dict)


class TestHelperFunctions:
    """Test paper trading helper functions."""

    def test_get_actionable_decisions(self, sample_decision):
        """Should return APPROVE and WATCH decisions."""
        decisions = {
            "eval-approve": sample_decision,
            "eval-reject": Decision(
                evaluation_id="eval-reject",
                verdict=Verdict.REJECT,
                final_score=50.0, directional_score=50.0,
                volatility_score=50.0, structure_score=50.0,
                primary_reason_code="REJECTED",
                supporting_reason_codes=[], failed_gates=["GATE_1"],
                concentration_warnings=[], policy_version="v2.0.0",
            ),
        }
        actionable = get_actionable_decisions(decisions)
        # Should include APPROVE (and possibly WATCH) but not REJECT
        eval_ids = [eid for eid, _ in actionable]
        assert "eval-approve" in eval_ids
        assert "eval-reject" not in eval_ids

    def test_get_actionable_decisions_empty(self):
        """Empty decisions returns empty list."""
        assert get_actionable_decisions({}) == []
