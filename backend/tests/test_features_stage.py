"""Tests for features/stage.py.

Covers FeatureComputationStage and run_feature_computation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.features.stage import FeatureComputationStage, run_feature_computation


class TestFeatureComputationStage:

    @pytest.mark.asyncio
    async def test_empty_evaluations(self):
        polygon = MagicMock()
        orch = MagicMock()
        orch.update_current_stage = AsyncMock()
        orch.record_stage_event = AsyncMock()

        stage = FeatureComputationStage(polygon_client=polygon, orchestrator=orch)
        result = await stage.execute("run-1", [], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_with_evaluations(self):
        polygon = MagicMock()
        orch = MagicMock()
        orch.update_current_stage = AsyncMock()
        orch.record_stage_event = AsyncMock()

        mock_fs = MagicMock()
        mock_fs.evaluation_id = "e1"

        with patch.object(
            FeatureComputationStage, "_fetch_iv_history", new_callable=AsyncMock, return_value={}
        ), patch.object(
            FeatureComputationStage, "_fetch_oi_history", new_callable=AsyncMock, return_value={}
        ), patch(
            "app.features.stage.FeatureComputer"
        ) as MockComputer:
            mock_comp = MockComputer.return_value
            mock_comp.compute_features_batch = AsyncMock(return_value=[mock_fs])

            stage = FeatureComputationStage(polygon_client=polygon, orchestrator=orch)
            stage._computer = mock_comp

            eval_mock = MagicMock()
            eval_mock.underlying_ticker = "AAPL"
            eval_mock.option_ticker = "O:AAPL"

            result = await stage.execute("run-1", [eval_mock], [], persist_features=False)
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_fetch_iv_history_error(self):
        polygon = MagicMock()
        orch = MagicMock()
        orch.update_current_stage = AsyncMock()
        orch.record_stage_event = AsyncMock()

        stage = FeatureComputationStage(polygon_client=polygon, orchestrator=orch)

        with patch("app.features.stage.IVHistoryTable") as mock_iv:
            mock_iv.list_by_ticker = AsyncMock(side_effect=Exception("DB error"))
            result = await stage._fetch_iv_history(["AAPL"])
            assert result == {}

    @pytest.mark.asyncio
    async def test_fetch_oi_history_error(self):
        polygon = MagicMock()
        orch = MagicMock()
        stage = FeatureComputationStage(polygon_client=polygon, orchestrator=orch)

        with patch("app.features.stage.OIHistoryTable") as mock_oi:
            mock_oi.list_by_contract = AsyncMock(side_effect=Exception("DB error"))
            result = await stage._fetch_oi_history(["O:AAPL"])
            assert result == {}

    @pytest.mark.asyncio
    async def test_persist_features_error(self):
        polygon = MagicMock()
        orch = MagicMock()
        stage = FeatureComputationStage(polygon_client=polygon, orchestrator=orch)

        mock_fs = MagicMock()
        mock_fs.evaluation_id = "e1"
        mock_fs.to_feature_values.side_effect = Exception("Convert error")

        with patch("app.features.stage.FeatureValueTable"):
            await stage._persist_features([mock_fs])  # Should not raise


class TestRunFeatureComputation:

    @pytest.mark.asyncio
    async def test_convenience_function(self):
        with patch(
            "app.features.stage.FeatureComputationStage.execute",
            new_callable=AsyncMock,
            return_value=[],
        ):
            polygon = MagicMock()
            orch = MagicMock()
            result = await run_feature_computation("run-1", [], [], polygon, orch)
            assert result == []
