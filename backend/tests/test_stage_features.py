"""Tests for the Feature Computation Stage (Stage 4).

Covers FeatureComputationStage.execute() and feature computation
with mocked Polygon client and DB tables.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    Evaluation,
    Opportunity,
    OptionType,
)
from app.features.models import FeatureSet
from app.features.stage import (
    FeatureComputationStage,
    run_feature_computation,
)


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    orch.update_current_stage.return_value = None
    orch.record_stage_event.return_value = MagicMock()
    return orch


@pytest.fixture
def mock_polygon():
    """Mock Polygon with batch methods returning proper dict-like values."""
    client = AsyncMock()

    # Mock bar data
    bars = [
        MagicMock(
            ticker="AAPL", date=f"2026-01-{i+1:02d}",
            open=180.0 + i, high=185.0 + i,
            low=178.0 + i, close=183.0 + i,
            volume=50_000_000,
        )
        for i in range(30)
    ]
    # get_daily_bars_batch returns {ticker: [bars]}
    client.get_daily_bars_batch.return_value = {
        "AAPL": bars,
        "SPY": bars,
    }
    client.get_daily_bars_parsed.return_value = bars

    return client


class TestFeatureComputationStage:
    """Test FeatureComputationStage.execute()."""

    @pytest.mark.asyncio
    async def test_execute_returns_feature_sets(
        self,
        mock_orchestrator,
        mock_polygon,
        sample_evaluation,
        sample_opportunity,
    ):
        """Execute should return a FeatureSet per evaluation."""
        with patch("app.features.stage.FeatureValueTable"), \
             patch("app.features.stage.IVHistoryTable") as mock_iv, \
             patch("app.features.stage.OIHistoryTable") as mock_oi:
            mock_iv.list_by_ticker.return_value = []
            mock_oi.list_by_contract.return_value = []

            stage = FeatureComputationStage(mock_polygon, mock_orchestrator)
            results = await stage.execute(
                run_id="run-001",
                evaluations=[sample_evaluation],
                opportunities=[sample_opportunity],
                persist_features=False,
            )

        assert isinstance(results, list)
        assert len(results) == 1
        assert isinstance(results[0], FeatureSet)
        assert results[0].evaluation_id == sample_evaluation.evaluation_id

    @pytest.mark.asyncio
    async def test_execute_empty_evaluations(self, mock_orchestrator, mock_polygon):
        """Empty evaluations should return empty list."""
        with patch("app.features.stage.FeatureValueTable"), \
             patch("app.features.stage.IVHistoryTable"), \
             patch("app.features.stage.OIHistoryTable"):
            stage = FeatureComputationStage(mock_polygon, mock_orchestrator)
            results = await stage.execute(
                run_id="run-002",
                evaluations=[],
                opportunities=[],
                persist_features=False,
            )
        assert results == []

    @pytest.mark.asyncio
    async def test_feature_set_has_required_fields(
        self,
        mock_orchestrator,
        mock_polygon,
        sample_evaluation,
        sample_opportunity,
    ):
        """FeatureSets should contain evaluation_id and close price."""
        with patch("app.features.stage.FeatureValueTable"), \
             patch("app.features.stage.IVHistoryTable") as mock_iv, \
             patch("app.features.stage.OIHistoryTable") as mock_oi:
            mock_iv.list_by_ticker.return_value = []
            mock_oi.list_by_contract.return_value = []

            stage = FeatureComputationStage(mock_polygon, mock_orchestrator)
            results = await stage.execute(
                run_id="run-003",
                evaluations=[sample_evaluation],
                opportunities=[sample_opportunity],
                persist_features=False,
            )
        fs = results[0]
        assert fs.evaluation_id is not None
        assert fs.close > 0

    @pytest.mark.asyncio
    async def test_run_feature_computation_convenience(
        self,
        mock_orchestrator,
        mock_polygon,
        sample_evaluation,
        sample_opportunity,
    ):
        """Convenience function should delegate to stage."""
        with patch("app.features.stage.FeatureValueTable"), \
             patch("app.features.stage.IVHistoryTable") as mock_iv, \
             patch("app.features.stage.OIHistoryTable") as mock_oi:
            mock_iv.list_by_ticker.return_value = []
            mock_oi.list_by_contract.return_value = []

            results = await run_feature_computation(
                run_id="run-004",
                evaluations=[sample_evaluation],
                opportunities=[sample_opportunity],
                polygon_client=mock_polygon,
                orchestrator=mock_orchestrator,
                persist_features=False,
            )
        assert isinstance(results, list)
