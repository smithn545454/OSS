"""API route tests for pipeline monitoring endpoints.

Tests HTTP behavior for pipeline run listing, aggregate data, and run details.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.schemas import (
    DisplayStage,
    PipelineMonitorData,
    PipelineRun,
    PipelineRunListItem,
    RunStatus,
    StageStatus,
)


@pytest.fixture
async def client():
    """Create an async test client for the FastAPI app."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestListPipelineRuns:
    """Tests for GET /api/pipeline/runs."""

    @pytest.mark.asyncio
    async def test_list_runs_returns_200(self, client):
        mock_run_item = PipelineRunListItem(
            id="run-001",
            timestamp="2026-01-17T16:00:00+00:00",
            total_contracts=100,
            approved_count=5,
        )
        with patch("app.api.routes.pipeline.pipeline_aggregator") as mock_agg, \
             patch("app.api.routes.pipeline.stage_mapper") as mock_mapper:
            mock_agg.get_runs_for_time_range = AsyncMock(
                return_value=([MagicMock()], 1, False)
            )
            mock_mapper.run_to_list_item.return_value = mock_run_item
            resp = await client.get("/api/pipeline/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert "total" in data
        assert "has_more" in data

    @pytest.mark.asyncio
    async def test_list_runs_with_time_filter(self, client):
        with patch("app.api.routes.pipeline.pipeline_aggregator") as mock_agg, \
             patch("app.api.routes.pipeline.stage_mapper") as mock_mapper:
            mock_agg.get_runs_for_time_range = AsyncMock(
                return_value=([], 0, False)
            )
            resp = await client.get("/api/pipeline/runs?time=last_7_days")

        assert resp.status_code == 200


class TestGetPipelineRunDetail:
    """Tests for GET /api/pipeline/runs/{run_id}."""

    @pytest.mark.asyncio
    async def test_get_run_returns_404_when_not_found(self, client):
        with patch("app.api.routes.pipeline.orchestrator") as mock_orch:
            mock_orch.get_run = AsyncMock(return_value=None)
            resp = await client.get("/api/pipeline/runs/nonexistent")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_run_returns_200(self, client):
        mock_run = MagicMock()
        mock_run.run_id = "run-001"
        mock_run.started_at = "2026-01-17T16:00:00+00:00"
        mock_run.total_opportunities = 10
        mock_run.total_approves = 2
        mock_run.scanner_type = None

        run_item = PipelineRunListItem(
            id="run-001",
            timestamp="2026-01-17T16:00:00+00:00",
            total_contracts=10,
            approved_count=2,
        )
        pipeline_data = PipelineMonitorData(
            time_range="Run run-001",
            scanner_type="Single Run",
            total_input=0,
            stages=[
                DisplayStage(id=i, name=f"Stage {i}", description="", input=0, output=0)
                for i in range(1, 6)
            ],
        )

        with patch("app.api.routes.pipeline.orchestrator") as mock_orch, \
             patch("app.api.routes.pipeline.StageEventTable") as mock_se, \
             patch("app.api.routes.pipeline.GateResultTable") as mock_gr, \
             patch("app.api.routes.pipeline.stage_mapper") as mock_mapper:
            mock_orch.get_run = AsyncMock(return_value=mock_run)
            mock_se.list_by_run = AsyncMock(return_value=[])
            mock_gr.list_by_run = AsyncMock(return_value=[])
            mock_mapper.build_pipeline_data = AsyncMock(return_value=pipeline_data)
            mock_mapper.run_to_list_item.return_value = run_item
            resp = await client.get("/api/pipeline/runs/run-001")

        assert resp.status_code == 200


class TestGetRunStages:
    """Tests for GET /api/pipeline/runs/{run_id}/stages."""

    @pytest.mark.asyncio
    async def test_get_run_stages_returns_200(self, client):
        with patch("app.api.routes.pipeline.orchestrator") as mock_orch:
            mock_orch.get_stage_events = AsyncMock(return_value=[])
            resp = await client.get("/api/pipeline/runs/run-001/stages")

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run-001"
        assert "stages" in data


class TestGetAggregateData:
    """Tests for GET /api/pipeline/aggregate."""

    @pytest.mark.asyncio
    async def test_aggregate_returns_200(self, client):
        pipeline_data = PipelineMonitorData(
            time_range="Today",
            scanner_type="All",
            total_input=0,
            stages=[
                DisplayStage(id=i, name=f"Stage {i}", description="", input=0, output=0)
                for i in range(1, 6)
            ],
        )

        with patch("app.api.routes.pipeline.pipeline_aggregator") as mock_agg:
            mock_agg.build_aggregate_data = AsyncMock(return_value=pipeline_data)
            resp = await client.get("/api/pipeline/aggregate")

        assert resp.status_code == 200


class TestStartPipelineRun:
    """Tests for POST /api/pipeline/runs."""

    @pytest.mark.asyncio
    async def test_start_run_returns_200(self, client):
        mock_run = MagicMock()
        mock_run.model_dump.return_value = {
            "run_id": "new-run",
            "policy_version": "v2.0.0",
            "status": "RUNNING",
        }
        with patch("app.api.routes.pipeline.orchestrator") as mock_orch:
            mock_orch.start_run = AsyncMock(return_value=mock_run)
            resp = await client.post(
                "/api/pipeline/runs",
                json={"policy_version": "v2.0.0"},
            )

        assert resp.status_code == 200
        assert resp.json()["message"] == "Pipeline run started"


class TestPipelineStats:
    """Tests for GET /api/pipeline/stats."""

    @pytest.mark.asyncio
    async def test_stats_returns_200(self, client):
        with patch("app.api.routes.pipeline.orchestrator") as mock_orch:
            mock_orch.get_stats = AsyncMock(return_value={"runs": 0, "avg_duration": 0})
            resp = await client.get("/api/pipeline/stats")

        assert resp.status_code == 200
