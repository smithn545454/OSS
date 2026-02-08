"""Tests for the Hard Gates Stage (Stage 6).

Covers HardGatesStage.execute(), helper functions,
and gate evaluation flow.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    Evaluation,
    GateConfig,
    Opportunity,
    OptionType,
    Verdict,
)
from app.features.models import FeatureSet
from app.gates.models import GateEvaluation
from app.gates.stage import (
    HardGatesStage,
    extract_gate_results_for_decision,
    get_evaluations_failing_gates,
    get_evaluations_passing_gates,
    run_hard_gates,
)


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    orch.update_current_stage.return_value = None
    orch.record_stage_event.return_value = MagicMock()
    return orch


class TestHardGatesStage:
    """Test HardGatesStage.execute()."""

    @pytest.mark.asyncio
    async def test_execute_returns_gate_evaluations(
        self,
        mock_orchestrator,
        sample_evaluation,
        sample_opportunity,
        sample_feature_set,
    ):
        """Execute should return dict of GateEvaluation per evaluation_id."""
        with patch("app.gates.stage.GateResultTable"):
            stage = HardGatesStage(mock_orchestrator)
            results = await stage.execute(
                run_id="run-001",
                evaluations=[sample_evaluation],
                feature_sets=[sample_feature_set],
                opportunities=[sample_opportunity],
                persist_results=False,
            )
        assert isinstance(results, dict)
        assert sample_evaluation.evaluation_id in results
        ge = results[sample_evaluation.evaluation_id]
        assert isinstance(ge, GateEvaluation)
        assert len(ge.gate_results) > 0

    @pytest.mark.asyncio
    async def test_execute_empty_evaluations(self, mock_orchestrator):
        """Empty evaluations should return empty dict."""
        with patch("app.gates.stage.GateResultTable"):
            stage = HardGatesStage(mock_orchestrator)
            results = await stage.execute(
                run_id="run-002",
                evaluations=[],
                feature_sets=[],
                opportunities=[],
                persist_results=False,
            )
        assert results == {}

    @pytest.mark.asyncio
    async def test_all_19_gates_evaluated(
        self,
        mock_orchestrator,
        sample_evaluation,
        sample_opportunity,
        sample_feature_set,
    ):
        """All 19 registered gates should appear in gate results."""
        with patch("app.gates.stage.GateResultTable"):
            stage = HardGatesStage(mock_orchestrator)
            results = await stage.execute(
                run_id="run-003",
                evaluations=[sample_evaluation],
                feature_sets=[sample_feature_set],
                opportunities=[sample_opportunity],
                persist_results=False,
            )
        ge = results[sample_evaluation.evaluation_id]
        assert len(ge.gate_results) == 19


class TestGateHelperFunctions:
    """Test helper functions in gates stage."""

    def test_get_evaluations_passing_gates(self, sample_evaluation):
        """Should filter to evaluations where all gates pass."""
        passing = GateEvaluation(
            evaluation_id=sample_evaluation.evaluation_id,
            gate_results=[
                MagicMock(enabled=True, passed=True),
                MagicMock(enabled=True, passed=True),
            ],
        )
        gate_results = {sample_evaluation.evaluation_id: passing}
        result = get_evaluations_passing_gates([sample_evaluation], gate_results)
        assert len(result) == 1

    def test_get_evaluations_failing_gates(self, sample_evaluation):
        """Should filter to evaluations where at least one gate fails."""
        failing = GateEvaluation(
            evaluation_id=sample_evaluation.evaluation_id,
            gate_results=[
                MagicMock(enabled=True, passed=True),
                MagicMock(enabled=True, passed=False),
            ],
        )
        gate_results = {sample_evaluation.evaluation_id: failing}
        result = get_evaluations_failing_gates([sample_evaluation], gate_results)
        assert len(result) == 1

    def test_extract_gate_results_for_decision(self):
        """Should extract gate evaluation info for decision stage."""
        ge = GateEvaluation(
            evaluation_id="eval-001",
            gate_results=[
                MagicMock(gate_id="GATE_A", enabled=True, passed=True),
                MagicMock(gate_id="GATE_B", enabled=True, passed=False),
            ],
        )
        extracted = extract_gate_results_for_decision({"eval-001": ge})
        assert "eval-001" in extracted
        assert extracted["eval-001"]["all_passed"] is False
        assert "GATE_B" in extracted["eval-001"]["failed_gates"]
