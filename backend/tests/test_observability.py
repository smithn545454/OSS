"""Tests for the observability module.

Covers StageMapper and PipelineAggregator functionality for the
Pipeline Monitor display.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    GateOperator,
    GateResult,
    PipelineRun,
    PipelineStage,
    PipelineRunListItem,
    RunStatus,
    StageEvent,
    StageStatus,
)


class TestStageMapper:
    """Test the stage mapper that converts internal stages to display stages."""

    def test_run_to_list_item_basic(self):
        from app.observability.stage_mapper import stage_mapper

        run = PipelineRun(
            run_id="run-001",
            policy_version="v2.0.0",
            total_opportunities=50,
            total_evaluations=20,
            total_approves=5,
            total_watches=8,
            total_rejects=7,
        )
        item = stage_mapper.run_to_list_item(run)

        assert isinstance(item, PipelineRunListItem)
        assert item.id == "run-001"
        assert item.approved_count == 5

    def test_run_to_list_item_with_scanner_type(self):
        from app.core.schemas import ScannerType
        from app.observability.stage_mapper import stage_mapper

        run = PipelineRun(
            run_id="run-002",
            policy_version="v2.0.0",
            scanner_type="UNUSUAL_VOLUME",
        )
        item = stage_mapper.run_to_list_item(run, ScannerType.UNUSUAL_VOLUME)
        assert item.scanner_type == ScannerType.UNUSUAL_VOLUME.value

    @pytest.mark.asyncio
    async def test_build_pipeline_data_empty_events(self):
        """build_pipeline_data should handle empty events gracefully."""
        from app.observability.stage_mapper import stage_mapper

        run = PipelineRun(
            run_id="run-003",
            policy_version="v2.0.0",
            total_opportunities=0,
        )
        data = await stage_mapper.build_pipeline_data(
            run=run,
            events=[],
            gate_results=[],
            time_range_label="Test",
            scanner_label="All",
        )
        assert data is not None
        assert len(data.stages) == 8  # Always 8 display stages

    @pytest.mark.asyncio
    async def test_build_pipeline_data_with_events(self):
        """build_pipeline_data with stage events should populate stage data."""
        from app.observability.stage_mapper import stage_mapper

        run = PipelineRun(
            run_id="run-004",
            policy_version="v2.0.0",
            total_opportunities=10,
            total_evaluations=5,
        )
        events = [
            StageEvent(
                run_id="run-004",
                stage=PipelineStage.OPPORTUNITY_DISCOVERY,
                started_at="2026-01-17T16:00:00",
                completed_at="2026-01-17T16:00:05",
                items_in=100,
                items_out=10,
                items_dropped=90,
            ),
            StageEvent(
                run_id="run-004",
                stage=PipelineStage.UNDERLYING_FILTERS,
                started_at="2026-01-17T16:00:05",
                completed_at="2026-01-17T16:00:08",
                items_in=10,
                items_out=8,
                items_dropped=2,
            ),
        ]

        data = await stage_mapper.build_pipeline_data(
            run=run,
            events=events,
            gate_results=[],
            time_range_label="Test",
            scanner_label="All",
        )
        assert data is not None
        # Stage 1 (Discovery) should have the opportunity data
        stage1 = data.stages[0]
        assert stage1.id == 1
        assert stage1.name == "Opportunity Discovery"


class TestPipelineAggregator:
    """Test the pipeline aggregator for multi-run aggregation."""

    @pytest.mark.asyncio
    async def test_get_runs_for_time_range_empty(self):
        from app.observability.stage_mapper import pipeline_aggregator
        from datetime import datetime, timezone

        with patch("app.db.tables.PipelineRunTable.list") as mock_list:
            mock_list.return_value = []
            start = datetime(2026, 1, 10, tzinfo=timezone.utc)
            end = datetime(2026, 1, 17, tzinfo=timezone.utc)

            runs, total, has_more = await pipeline_aggregator.get_runs_for_time_range(
                start=start, end=end, limit=50, offset=0,
            )

        assert runs == []
        assert total == 0
        assert has_more is False

    @pytest.mark.asyncio
    async def test_build_aggregate_data(self):
        from app.observability.stage_mapper import pipeline_aggregator
        from datetime import datetime, timezone

        with patch("app.db.tables.PipelineRunTable.list") as mock_run_list, \
             patch("app.db.tables.StageEventTable.list_by_run") as mock_event_list, \
             patch("app.db.tables.GateResultTable.list_by_run") as mock_gate_list:
            mock_run_list.return_value = []
            mock_event_list.return_value = []
            mock_gate_list.return_value = []

            start = datetime(2026, 1, 10, tzinfo=timezone.utc)
            end = datetime(2026, 1, 17, tzinfo=timezone.utc)

            data = await pipeline_aggregator.build_aggregate_data(
                start=start, end=end,
            )

        assert data is not None
