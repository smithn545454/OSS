"""Pipeline monitoring endpoints.

Implements the Pipeline Monitor API per oss-pipeline-monitor-requirements.md:
- GET /api/pipeline/runs - List recent runs with filters
- GET /api/pipeline/aggregate - Aggregated data for time range
- GET /api/pipeline/runs/{runId} - Single run detail with gate breakdowns
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.core.pipeline import PipelineOrchestrator
from app.core.schemas import (
    GetAggregateResponse,
    GetRunDetailResponse,
    GetRunsResponse,
    PipelineRunCreate,
    PipelineRunListItem,
    ScannerType,
    StageStatus,
    TimeRangeOption,
)
from app.db.tables import GateResultTable, StageEventTable
from app.observability.stage_mapper import pipeline_aggregator, stage_mapper

router = APIRouter()
orchestrator = PipelineOrchestrator()


def parse_time_range(
    time_range: TimeRangeOption,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> tuple[datetime, datetime]:
    """Parse time range option into start/end datetime.
    
    Args:
        time_range: The time range option
        start: Optional custom start (ISO 8601)
        end: Optional custom end (ISO 8601)
        
    Returns:
        Tuple of (start_datetime, end_datetime)
    """
    now = datetime.now(timezone.utc)
    # Compute "today" as Pacific midnight converted to UTC so that
    # at e.g. 7:40 PM PT (3:40 AM next-day UTC) we still see today's runs.
    pacific = get_settings().display_tz
    now_local = now.astimezone(pacific)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
        timezone.utc
    )
    
    if time_range == TimeRangeOption.LAST_HOUR:
        return now - timedelta(hours=1), now
    elif time_range == TimeRangeOption.TODAY:
        return today_start, now
    elif time_range == TimeRangeOption.YESTERDAY:
        yesterday_start = today_start - timedelta(days=1)
        return yesterday_start, today_start
    elif time_range == TimeRangeOption.LAST_7_DAYS:
        return today_start - timedelta(days=7), now
    elif time_range == TimeRangeOption.LAST_30_DAYS:
        return today_start - timedelta(days=30), now
    elif time_range == TimeRangeOption.CUSTOM and start and end:
        return (
            datetime.fromisoformat(start.replace("Z", "+00:00")),
            datetime.fromisoformat(end.replace("Z", "+00:00")),
        )
    else:
        # Default to today
        return today_start, now


@router.get("/runs")
async def list_pipeline_runs(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    time: TimeRangeOption = Query(default=TimeRangeOption.TODAY),
    start: Optional[str] = Query(default=None, description="Custom start datetime (ISO 8601)"),
    end: Optional[str] = Query(default=None, description="Custom end datetime (ISO 8601)"),
    scanner: Optional[ScannerType] = Query(default=None, description="Filter by scanner type"),
) -> GetRunsResponse:
    """List recent pipeline runs (spec section 9.1).
    
    Returns runs within the specified time range with optional scanner filter.
    """
    start_dt, end_dt = parse_time_range(time, start, end)
    
    runs, total, has_more = await pipeline_aggregator.get_runs_for_time_range(
        start=start_dt,
        end=end_dt,
        scanner_type=scanner,
        limit=limit,
        offset=offset,
    )
    
    # Convert to list items
    run_items = [
        stage_mapper.run_to_list_item(run, scanner)
        for run in runs
    ]
    
    return GetRunsResponse(
        runs=run_items,
        total=total,
        has_more=has_more,
    )


@router.get("/runs/{run_id}")
async def get_pipeline_run(
    run_id: str,
    verdict: Optional[str] = Query(default=None, description="Filter by verdict"),
    dte_bucket: Optional[str] = Query(default=None, description="Filter by DTE bucket"),
    option_side: Optional[str] = Query(default=None, description="Filter by option side"),
) -> GetRunDetailResponse:
    """Get details of a specific pipeline run with gate breakdowns (spec section 9.3).
    
    Returns complete pipeline visualization data for a single run.
    """
    run = await orchestrator.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Pipeline run {run_id} not found")
    
    # Get stage events and gate results
    events = await StageEventTable.list_by_run(run_id)
    gate_results = await GateResultTable.list_by_run(run_id)
    
    # Build pipeline display data
    pipeline_data = await stage_mapper.build_pipeline_data(
        run=run,
        events=events,
        gate_results=gate_results,
        time_range_label=f"Run {run_id[:8]}",
        scanner_label="Single Run",
    )
    
    # Convert run to list item
    run_item = stage_mapper.run_to_list_item(run)
    
    return GetRunDetailResponse(
        run=run_item,
        data=pipeline_data,
    )


@router.get("/runs/{run_id}/stages")
async def get_run_stages(run_id: str) -> dict[str, Any]:
    """Get stage events for a pipeline run (legacy endpoint)."""
    events = await orchestrator.get_stage_events(run_id)
    return {
        "run_id": run_id,
        "stages": [e.model_dump() for e in events],
        "count": len(events),
    }


@router.get("/aggregate")
async def get_aggregate_data(
    time: TimeRangeOption = Query(default=TimeRangeOption.TODAY),
    start: Optional[str] = Query(default=None, description="Custom start datetime (ISO 8601)"),
    end: Optional[str] = Query(default=None, description="Custom end datetime (ISO 8601)"),
    scanner: Optional[ScannerType] = Query(default=None, description="Filter by scanner type"),
    verdict: Optional[str] = Query(default=None, description="Filter by verdict (comma-separated)"),
    dte_bucket: Optional[str] = Query(default=None, description="Filter by DTE bucket (comma-separated)"),
    option_side: Optional[str] = Query(default=None, description="Filter by option side (comma-separated)"),
) -> GetAggregateResponse:
    """Get aggregated pipeline data for a time range (spec section 9.2).
    
    Aggregates metrics across multiple pipeline runs within the specified time range.
    """
    start_dt, end_dt = parse_time_range(time, start, end)
    
    # Build aggregate data
    pipeline_data = await pipeline_aggregator.build_aggregate_data(
        start=start_dt,
        end=end_dt,
        scanner_type=scanner,
    )
    
    return GetAggregateResponse(data=pipeline_data)


@router.post("/runs")
async def start_pipeline_run(run_create: PipelineRunCreate) -> dict[str, Any]:
    """Start a new pipeline run."""
    run = await orchestrator.start_run(policy_version=run_create.policy_version)
    return {
        "message": "Pipeline run started",
        "run": run.model_dump(),
    }


@router.post("/runs/{run_id}/complete")
async def complete_pipeline_run(
    run_id: str,
    status: str = "completed",
) -> dict[str, Any]:
    """Mark a pipeline run as complete."""
    run = await orchestrator.complete_run(run_id, status=status)
    if not run:
        raise HTTPException(status_code=404, detail=f"Pipeline run {run_id} not found")
    return {
        "message": f"Pipeline run {run_id} marked as {status}",
        "run": run.model_dump(),
    }


@router.get("/stats")
async def get_pipeline_stats(days: int = 7) -> dict[str, Any]:
    """Get pipeline statistics for the specified time period."""
    stats = await orchestrator.get_stats(days=days)
    return stats
