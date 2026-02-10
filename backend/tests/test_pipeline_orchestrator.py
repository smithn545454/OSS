"""Tests for the PipelineOrchestrator (core/pipeline.py).

Covers start_run, get_run, list_runs, record_stage_event,
complete_run, update_current_stage, and get_stats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.pipeline import PipelineOrchestrator
from app.core.schemas import (
    PipelineRun,
    PipelineStage,
    RunStatus,
    StageEvent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def orch():
    return PipelineOrchestrator()


def _make_run(run_id="run-1", status=RunStatus.RUNNING, **kwargs):
    now = datetime.now(timezone.utc).isoformat()
    return PipelineRun(
        run_id=run_id,
        policy_version="v2.0.0",
        status=status,
        started_at=now,
        current_stage=PipelineStage.OPPORTUNITY_DISCOVERY,
        **kwargs,
    )


def _make_event(run_id="run-1", stage=PipelineStage.OPPORTUNITY_DISCOVERY, items_in=10, items_out=5):
    now = datetime.now(timezone.utc).isoformat()
    return StageEvent(
        run_id=run_id,
        stage=stage,
        started_at=now,
        completed_at=now,
        items_in=items_in,
        items_out=items_out,
        items_dropped=items_in - items_out,
    )


# ---------------------------------------------------------------------------
# Tests: start_run
# ---------------------------------------------------------------------------


class TestStartRun:

    @pytest.mark.asyncio
    async def test_start_run(self, orch):
        with patch("app.core.pipeline.PipelineRunTable") as mock_table:
            mock_table.put = AsyncMock()
            run = await orch.start_run("v2.0.0")

        assert run.policy_version == "v2.0.0"
        assert run.status == RunStatus.RUNNING
        mock_table.put.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: get_run / list_runs
# ---------------------------------------------------------------------------


class TestRunQueries:

    @pytest.mark.asyncio
    async def test_get_run_found(self, orch):
        run = _make_run("run-123")
        with patch("app.core.pipeline.PipelineRunTable") as mock_table:
            mock_table.list = AsyncMock(return_value=[run])
            result = await orch.get_run("run-123")
        assert result is not None
        assert result.run_id == "run-123"

    @pytest.mark.asyncio
    async def test_get_run_not_found(self, orch):
        with patch("app.core.pipeline.PipelineRunTable") as mock_table:
            mock_table.list = AsyncMock(return_value=[])
            result = await orch.get_run("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_runs(self, orch):
        runs = [_make_run("r1"), _make_run("r2")]
        with patch("app.core.pipeline.PipelineRunTable") as mock_table:
            mock_table.list = AsyncMock(return_value=runs)
            result = await orch.list_runs(limit=10)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Tests: record_stage_event
# ---------------------------------------------------------------------------


class TestRecordStageEvent:

    @pytest.mark.asyncio
    async def test_record_event(self, orch):
        with patch("app.core.pipeline.StageEventTable") as mock_table:
            mock_table.put = AsyncMock()
            event = await orch.record_stage_event(
                run_id="run-1",
                stage=PipelineStage.OPPORTUNITY_DISCOVERY,
                items_in=100,
                items_out=50,
                drop_reasons={"LOW_VOLUME": 30, "NO_OPTIONS": 20},
            )

        assert isinstance(event, StageEvent)
        assert event.items_in == 100
        assert event.items_out == 50
        assert event.items_dropped == 50
        mock_table.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_event_with_metadata(self, orch):
        with patch("app.core.pipeline.StageEventTable") as mock_table:
            mock_table.put = AsyncMock()
            event = await orch.record_stage_event(
                run_id="run-1",
                stage=PipelineStage.DECISION_LOGIC,
                items_in=20,
                items_out=5,
                metadata={"approves": 3, "watches": 2, "rejects": 15},
            )

        assert event.metadata["approves"] == 3


# ---------------------------------------------------------------------------
# Tests: complete_run
# ---------------------------------------------------------------------------


class TestCompleteRun:

    @pytest.mark.asyncio
    async def test_complete_run_success(self, orch):
        run = _make_run("run-1")
        updated = _make_run("run-1", status=RunStatus.COMPLETED)

        with patch("app.core.pipeline.PipelineRunTable") as mock_run_table, \
             patch("app.core.pipeline.StageEventTable") as mock_event_table:
            mock_run_table.list = AsyncMock(return_value=[run])
            mock_run_table.update = AsyncMock(return_value=updated)
            mock_event_table.list_by_run = AsyncMock(return_value=[
                _make_event(stage=PipelineStage.OPPORTUNITY_DISCOVERY, items_in=100, items_out=50),
                _make_event(stage=PipelineStage.DECISION_LOGIC, items_in=20, items_out=5),
            ])

            result = await orch.complete_run("run-1")

        assert result is not None
        mock_run_table.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_run_not_found(self, orch):
        with patch("app.core.pipeline.PipelineRunTable") as mock_table:
            mock_table.list = AsyncMock(return_value=[])
            result = await orch.complete_run("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_complete_run_failed(self, orch):
        run = _make_run("run-1")
        updated = _make_run("run-1", status=RunStatus.FAILED)

        with patch("app.core.pipeline.PipelineRunTable") as mock_run_table, \
             patch("app.core.pipeline.StageEventTable") as mock_event_table:
            mock_run_table.list = AsyncMock(return_value=[run])
            mock_run_table.update = AsyncMock(return_value=updated)
            mock_event_table.list_by_run = AsyncMock(return_value=[])

            result = await orch.complete_run("run-1", status="failed", error_message="boom")

        assert result is not None


# ---------------------------------------------------------------------------
# Tests: get_stats
# ---------------------------------------------------------------------------


class TestGetStats:

    @pytest.mark.asyncio
    async def test_get_stats_empty(self, orch):
        with patch("app.core.pipeline.PipelineRunTable") as mock_table:
            mock_table.list = AsyncMock(return_value=[])
            stats = await orch.get_stats(days=7)

        assert stats["total_runs"] == 0
        assert stats["approve_rate_pct"] == 0

    @pytest.mark.asyncio
    async def test_get_stats_with_runs(self, orch):
        runs = [
            _make_run("r1", status=RunStatus.COMPLETED, total_opportunities=50,
                       total_evaluations=20, total_approves=5, total_watches=5, total_rejects=10),
            _make_run("r2", status=RunStatus.COMPLETED, total_opportunities=40,
                       total_evaluations=15, total_approves=3, total_watches=4, total_rejects=8),
        ]

        with patch("app.core.pipeline.PipelineRunTable") as mock_table:
            mock_table.list = AsyncMock(return_value=runs)
            stats = await orch.get_stats(days=7)

        assert stats["total_runs"] == 2
        assert stats["completed_runs"] == 2
        assert stats["total_approves"] == 8
        assert stats["approve_rate_pct"] > 0
