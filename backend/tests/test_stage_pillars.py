"""Tests for the Pillar Scoring Stage (Stage 5).

Covers PillarScoringStage.execute(), helper functions,
and the convenience run_pillar_scoring().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    DTEBucket,
    Evaluation,
    GateOperator,
    Opportunity,
    OptionType,
    PillarConfig,
    PillarId,
)
from app.features.models import FeatureSet
from app.pillars.models import PillarResult, ScoringContext
from app.pillars.stage import (
    PillarScoringStage,
    extract_pillar_scores_for_decision,
    run_pillar_scoring,
)


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    orch.update_current_stage.return_value = None
    orch.record_stage_event.return_value = MagicMock()
    return orch


class TestPillarScoringStage:
    """Test PillarScoringStage.execute()."""

    @pytest.mark.asyncio
    async def test_execute_returns_results_per_evaluation(
        self,
        mock_orchestrator,
        sample_evaluation,
        sample_opportunity,
        sample_feature_set,
    ):
        """Execute should produce pillar results keyed by evaluation_id."""
        with patch("app.pillars.stage.PillarScoreTable"):
            stage = PillarScoringStage(mock_orchestrator)
            results = await stage.execute(
                run_id="run-001",
                evaluations=[sample_evaluation],
                feature_sets=[sample_feature_set],
                opportunities=[sample_opportunity],
                persist_scores=False,
            )
        assert isinstance(results, dict)
        assert sample_evaluation.evaluation_id in results
        pillar_results = results[sample_evaluation.evaluation_id]
        assert len(pillar_results) == 3  # Directional, Volatility, Structure

    @pytest.mark.asyncio
    async def test_execute_empty_evaluations(self, mock_orchestrator):
        """Empty evaluations should return empty dict."""
        with patch("app.pillars.stage.PillarScoreTable"):
            stage = PillarScoringStage(mock_orchestrator)
            results = await stage.execute(
                run_id="run-002",
                evaluations=[],
                feature_sets=[],
                opportunities=[],
                persist_scores=False,
            )
        assert results == {}

    @pytest.mark.asyncio
    async def test_execute_persists_scores(
        self,
        mock_orchestrator,
        sample_evaluation,
        sample_opportunity,
        sample_feature_set,
    ):
        """When persist_scores=True, scores should be written to DynamoDB."""
        with patch("app.pillars.stage.PillarScoreTable") as mock_table:
            stage = PillarScoringStage(mock_orchestrator)
            await stage.execute(
                run_id="run-003",
                evaluations=[sample_evaluation],
                feature_sets=[sample_feature_set],
                opportunities=[sample_opportunity],
                persist_scores=True,
            )
            # Verify put_batch was called
            assert mock_table.put_batch.called or mock_table.put.called

    @pytest.mark.asyncio
    async def test_execute_pillar_scores_in_valid_range(
        self,
        mock_orchestrator,
        sample_evaluation,
        sample_opportunity,
        sample_feature_set,
    ):
        """All pillar scores must be in [0, 100]."""
        with patch("app.pillars.stage.PillarScoreTable"):
            stage = PillarScoringStage(mock_orchestrator)
            results = await stage.execute(
                run_id="run-004",
                evaluations=[sample_evaluation],
                feature_sets=[sample_feature_set],
                opportunities=[sample_opportunity],
                persist_scores=False,
            )
        for eval_id, pillar_list in results.items():
            for pr in pillar_list:
                assert 0 <= pr.score <= 100, f"Pillar {pr.pillar_id} score {pr.score} out of range"


class TestHelperFunctions:
    """Test helper functions in pillar stage."""

    def test_extract_pillar_scores_for_decision(self):
        """extract_pillar_scores_for_decision should return {eval_id: {pillar_id: score}} (v3.0.0)."""
        results = {
            "eval-001": [
                PillarResult(pillar_id="PREMIUM_LEVERAGE", evaluation_id="eval-001", score=80, subscores=[], tags=[]),
                PillarResult(pillar_id="UNDERLYING_BEHAVIOR", evaluation_id="eval-001", score=75, subscores=[], tags=[]),
                PillarResult(pillar_id="SETUP_QUALITY", evaluation_id="eval-001", score=70, subscores=[], tags=[]),
            ]
        }
        extracted = extract_pillar_scores_for_decision(results)
        assert "eval-001" in extracted
        assert extracted["eval-001"]["premium_leverage"] == 80
        assert extracted["eval-001"]["underlying_behavior"] == 75
        assert extracted["eval-001"]["setup_quality"] == 70

    def test_extract_empty_results(self):
        """Empty results should produce empty dict."""
        assert extract_pillar_scores_for_decision({}) == {}


class TestRunPillarScoring:
    """Test the convenience run_pillar_scoring function."""

    @pytest.mark.asyncio
    async def test_run_pillar_scoring_delegates(
        self,
        mock_orchestrator,
        sample_evaluation,
        sample_opportunity,
        sample_feature_set,
    ):
        """run_pillar_scoring should delegate to PillarScoringStage.execute."""
        with patch("app.pillars.stage.PillarScoreTable"):
            results = await run_pillar_scoring(
                run_id="run-005",
                evaluations=[sample_evaluation],
                feature_sets=[sample_feature_set],
                opportunities=[sample_opportunity],
                orchestrator=mock_orchestrator,
                persist_scores=False,
            )
        assert isinstance(results, dict)
