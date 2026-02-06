"""Calibration API endpoints.

Per Section 20 of OSS_Complete_Requirements.md.

Provides endpoints for:
- Running weekly calibration analysis
- Listing calibration reports
- Getting specific reports
- Approving/rejecting threshold suggestions
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.calibration.models import CalibrationReport, SuggestionStatus
from app.calibration.reporter import CalibrationReporter
from app.db.dynamodb import get_dynamodb

router = APIRouter()

# In-memory storage for reports (in production, use DynamoDB)
_reports_store: dict[str, CalibrationReport] = {}
_suggestions_store: dict[str, dict] = {}


class RunCalibrationRequest(BaseModel):
    """Request body for running calibration."""
    week_start: Optional[str] = None
    week_end: Optional[str] = None


# ============================================================================
# Report Endpoints
# ============================================================================


@router.post("/run")
async def run_calibration(
    request: Optional[RunCalibrationRequest] = None,
) -> dict[str, Any]:
    """Trigger weekly calibration analysis.
    
    Per Section 20.1 - Automated weekly analysis.
    
    Args:
        request: Optional request with week start/end dates
        
    Returns:
        Generated calibration report
    """
    reporter = CalibrationReporter()
    
    week_start = request.week_start if request else None
    week_end = request.week_end if request else None
    
    try:
        report = await reporter.generate_report(
            week_start=week_start,
            week_end=week_end,
        )
        
        # Store report
        _reports_store[report.report_id] = report
        
        # Store suggestions for approval workflow
        for suggestion in report.suggestions:
            _suggestions_store[suggestion.suggestion_id] = {
                "report_id": report.report_id,
                "suggestion": suggestion,
            }
        
        return {
            "message": "Calibration analysis completed",
            "report": report.to_dict(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Calibration failed: {str(e)}",
        )


@router.get("/reports")
async def list_reports(
    limit: int = 10,
) -> dict[str, Any]:
    """List calibration reports.
    
    Args:
        limit: Maximum reports to return
        
    Returns:
        List of calibration reports
    """
    reports = list(_reports_store.values())
    
    # Sort by generated_at descending
    reports.sort(key=lambda r: r.generated_at, reverse=True)
    reports = reports[:limit]
    
    return {
        "reports": [r.to_dict() for r in reports],
        "count": len(reports),
    }


@router.get("/reports/{report_id}")
async def get_report(report_id: str) -> dict[str, Any]:
    """Get a specific calibration report.
    
    Args:
        report_id: The report ID
        
    Returns:
        Calibration report details
    """
    report = _reports_store.get(report_id)
    if not report:
        raise HTTPException(
            status_code=404,
            detail=f"Report not found: {report_id}",
        )
    
    return report.to_dict()


# ============================================================================
# Suggestion Endpoints
# ============================================================================


@router.post("/suggestions/{suggestion_id}/approve")
async def approve_suggestion(suggestion_id: str) -> dict[str, Any]:
    """Approve a threshold suggestion.
    
    Per Section 20 - No auto-apply, human approval required.
    
    This marks the suggestion as approved. In a full implementation,
    this would also create a new policy version with the suggested change.
    
    Args:
        suggestion_id: The suggestion ID to approve
        
    Returns:
        Updated suggestion with APPROVED status
    """
    if suggestion_id not in _suggestions_store:
        raise HTTPException(
            status_code=404,
            detail=f"Suggestion not found: {suggestion_id}",
        )
    
    suggestion_data = _suggestions_store[suggestion_id]
    suggestion = suggestion_data["suggestion"]
    
    if suggestion.status != SuggestionStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Suggestion is not pending: {suggestion.status.value}",
        )
    
    # Mark as approved
    suggestion.status = SuggestionStatus.APPROVED
    
    # In production, this would:
    # 1. Create a new policy version with the suggested threshold
    # 2. Optionally activate the new policy
    
    # Update in report
    report_id = suggestion_data["report_id"]
    if report_id in _reports_store:
        report = _reports_store[report_id]
        for i, s in enumerate(report.suggestions):
            if s.suggestion_id == suggestion_id:
                report.suggestions[i] = suggestion
                break
    
    return {
        "message": "Suggestion approved",
        "suggestion": suggestion.to_dict(),
        "note": "Create new policy version manually to apply this change",
    }


@router.post("/suggestions/{suggestion_id}/reject")
async def reject_suggestion(suggestion_id: str) -> dict[str, Any]:
    """Reject a threshold suggestion.
    
    Args:
        suggestion_id: The suggestion ID to reject
        
    Returns:
        Updated suggestion with REJECTED status
    """
    if suggestion_id not in _suggestions_store:
        raise HTTPException(
            status_code=404,
            detail=f"Suggestion not found: {suggestion_id}",
        )
    
    suggestion_data = _suggestions_store[suggestion_id]
    suggestion = suggestion_data["suggestion"]
    
    if suggestion.status != SuggestionStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Suggestion is not pending: {suggestion.status.value}",
        )
    
    # Mark as rejected
    suggestion.status = SuggestionStatus.REJECTED
    
    # Update in report
    report_id = suggestion_data["report_id"]
    if report_id in _reports_store:
        report = _reports_store[report_id]
        for i, s in enumerate(report.suggestions):
            if s.suggestion_id == suggestion_id:
                report.suggestions[i] = suggestion
                break
    
    return {
        "message": "Suggestion rejected",
        "suggestion": suggestion.to_dict(),
    }


# ============================================================================
# Summary Endpoint
# ============================================================================


@router.get("/summary")
async def get_calibration_summary() -> dict[str, Any]:
    """Get a summary of calibration status.
    
    Returns:
        Summary with latest report info and pending suggestions
    """
    reports = list(_reports_store.values())
    reports.sort(key=lambda r: r.generated_at, reverse=True)
    
    latest_report = reports[0] if reports else None
    
    pending_suggestions = [
        s["suggestion"].to_dict()
        for s in _suggestions_store.values()
        if s["suggestion"].status == SuggestionStatus.PENDING
    ]
    
    return {
        "total_reports": len(reports),
        "latest_report": latest_report.to_dict() if latest_report else None,
        "pending_suggestions": len(pending_suggestions),
        "pending_suggestion_details": pending_suggestions,
    }
