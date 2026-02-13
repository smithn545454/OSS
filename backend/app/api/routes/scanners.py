"""Scanner API endpoints.

Provides endpoints for:
- Triggering manual scans
- Checking scan status
- Listing and viewing opportunities
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from app.core.schemas import DirectionHint
from app.db.tables import OpportunityTable, ScanStatusTable
from app.scanners.orchestrator import ScannerOrchestrator

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory storage for background scan status (would use Redis in production)
_scan_status: dict[str, dict[str, Any]] = {}


class ScanRequest(BaseModel):
    """Request to trigger a scan."""

    tickers: Optional[list[str]] = None
    scanner: Optional[str] = None  # Optional: run specific scanner only
    run_full_pipeline: bool = True  # If False, only run scanners (skip Contract Selection)


class ScanResponse(BaseModel):
    """Response from initiating a scan."""

    run_id: str
    status: str
    message: str


class ScanStatusResponse(BaseModel):
    """Response with scan status."""

    run_id: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    tickers_scanned: Optional[int] = None
    opportunities_created: Optional[int] = None
    evaluations_created: Optional[int] = None
    approve_count: Optional[int] = None
    watch_count: Optional[int] = None
    reject_count: Optional[int] = None
    scanner_stats: Optional[dict[str, Any]] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None


async def _run_scan_background(
    run_id: str,
    tickers: Optional[list[str]] = None,
    run_full_pipeline: bool = True,
) -> None:
    """Run a scan in the background.

    Args:
        run_id: The run ID for tracking
        tickers: Optional list of tickers to scan
        run_full_pipeline: If True, run all stages; if False, only run scanners
    """
    _scan_status[run_id] = {
        "status": "running",
        "started_at": datetime.utcnow().isoformat(),
    }

    try:
        orchestrator = ScannerOrchestrator()
        result = await orchestrator.run_scan(
            tickers=tickers,
            run_id=run_id,
            run_full_pipeline=run_full_pipeline,
        )

        _scan_status[run_id].update({
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat(),
            "tickers_scanned": result.tickers_scanned,
            "opportunities_created": result.opportunities_created,
            "evaluations_created": result.evaluations_created,
            "approve_count": result.approve_count,
            "watch_count": result.watch_count,
            "reject_count": result.reject_count,
            "scanner_stats": result.scanner_stats,
            "duration_ms": result.duration_ms,
        })

    except Exception as e:
        logger.error(f"Background scan {run_id} failed: {e}")
        _scan_status[run_id].update({
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat(),
            "error": str(e),
        })


@router.post("/run", response_model=ScanResponse)
async def trigger_scan(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
) -> ScanResponse:
    """Trigger a new scanner run.

    The scan runs in the background. Use GET /scanners/status/{run_id}
    to check progress.

    Args:
        request: Scan configuration
        background_tasks: FastAPI background tasks

    Returns:
        Response with run_id for status tracking
    """
    import uuid

    run_id = str(uuid.uuid4())

    # Add to background tasks
    background_tasks.add_task(
        _run_scan_background,
        run_id=run_id,
        tickers=request.tickers,
        run_full_pipeline=request.run_full_pipeline,
    )

    return ScanResponse(
        run_id=run_id,
        status="started",
        message=f"Scan started. Poll GET /scanners/status/{run_id} for progress.",
    )


@router.get("/status/{run_id}", response_model=ScanStatusResponse)
async def get_scan_status(run_id: str) -> ScanStatusResponse:
    """Get the status of a scan run.

    Args:
        run_id: The run ID to check

    Returns:
        Current status of the scan
    """
    status = _scan_status.get(run_id)

    if status is None:
        # Fallback to DynamoDB
        status = await ScanStatusTable.get(run_id)

    if status is None:
        raise HTTPException(
            status_code=404,
            detail=f"Scan run {run_id} not found",
        )

    return ScanStatusResponse(
        run_id=run_id,
        status=status.get("status", "unknown"),
        started_at=status.get("started_at"),
        completed_at=status.get("completed_at"),
        tickers_scanned=status.get("tickers_scanned"),
        opportunities_created=status.get("opportunities_created"),
        evaluations_created=status.get("evaluations_created"),
        approve_count=status.get("approve_count"),
        watch_count=status.get("watch_count"),
        reject_count=status.get("reject_count"),
        scanner_stats=status.get("scanner_stats"),
        duration_ms=status.get("duration_ms"),
        error=status.get("error"),
    )


@router.get("/opportunities")
async def list_opportunities(
    date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    direction: Optional[str] = Query(None, description="Filter by direction hint (CALL/PUT/NONE)"),
    limit: int = Query(50, ge=1, le=500, description="Maximum results"),
) -> dict[str, Any]:
    """List recent opportunities.

    Args:
        date: Optional date filter
        ticker: Optional ticker filter
        direction: Optional direction hint filter
        limit: Maximum results to return

    Returns:
        List of opportunities with metadata
    """
    opportunities = []

    if date:
        # Query by date
        opportunities = await OpportunityTable.list_by_date(date, limit=limit)
    elif ticker:
        # Query by ticker
        opportunities = await OpportunityTable.list_by_ticker(ticker.upper(), limit=limit)
    else:
        # Get today's opportunities by default
        today = datetime.now().strftime("%Y-%m-%d")
        opportunities = await OpportunityTable.list_by_date(today, limit=limit)

    # Apply direction filter if specified
    if direction:
        direction_enum = DirectionHint(direction.upper())
        opportunities = [
            o for o in opportunities
            if o.direction_hint == direction_enum
        ]

    return {
        "opportunities": [o.model_dump() for o in opportunities],
        "count": len(opportunities),
        "filters": {
            "date": date,
            "ticker": ticker,
            "direction": direction,
        },
    }


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(
    opportunity_id: str,
    ticker: str = Query(..., description="Ticker symbol (required for lookup)"),
    timestamp: str = Query(..., description="Timestamp UTC (required for lookup)"),
) -> dict[str, Any]:
    """Get a specific opportunity by ID.

    Note: Both ticker and timestamp are required for DynamoDB key lookup.

    Args:
        opportunity_id: The opportunity ID
        ticker: The ticker symbol
        timestamp: The timestamp UTC

    Returns:
        Opportunity details
    """
    opportunity = await OpportunityTable.get(
        ticker.upper(),
        timestamp,
        opportunity_id,
    )

    if opportunity is None:
        raise HTTPException(
            status_code=404,
            detail=f"Opportunity {opportunity_id} not found",
        )

    return opportunity.model_dump()


@router.get("/stats")
async def get_scanner_stats(
    date: Optional[str] = Query(None, description="Date for stats (YYYY-MM-DD)"),
) -> dict[str, Any]:
    """Get scanner statistics.

    Args:
        date: Optional date (defaults to today)

    Returns:
        Scanner statistics and opportunity counts
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    opportunities = await OpportunityTable.list_by_date(date, limit=1000)

    # Count by scanner type
    scanner_counts: dict[str, int] = {}
    for opp in opportunities:
        for trigger in opp.scanner_triggers:
            scanner_type = trigger.scanner_type
            scanner_counts[scanner_type] = scanner_counts.get(scanner_type, 0) + 1

    # Count by direction
    direction_counts = {
        "CALL": sum(1 for o in opportunities if o.direction_hint == DirectionHint.CALL),
        "PUT": sum(1 for o in opportunities if o.direction_hint == DirectionHint.PUT),
        "NONE": sum(1 for o in opportunities if o.direction_hint == DirectionHint.NONE),
    }

    # Priority score distribution
    priority_buckets = {
        "90-100": sum(1 for o in opportunities if 90 <= o.priority_score <= 100),
        "75-89": sum(1 for o in opportunities if 75 <= o.priority_score < 90),
        "60-74": sum(1 for o in opportunities if 60 <= o.priority_score < 75),
        "below_60": sum(1 for o in opportunities if o.priority_score < 60),
    }

    return {
        "date": date,
        "total_opportunities": len(opportunities),
        "by_scanner": scanner_counts,
        "by_direction": direction_counts,
        "by_priority": priority_buckets,
        "multi_scanner": sum(
            1 for o in opportunities if len(o.scanner_triggers) > 1
        ),
    }
