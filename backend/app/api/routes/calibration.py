"""Calibration API endpoints.

Per Section 20 of OSS_Complete_Requirements.md.

Provides endpoints for:
- Running weekly calibration analysis
- Listing calibration reports
- Getting specific reports
- Approving/rejecting threshold suggestions
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.calibration.models import SuggestionStatus
from app.calibration.reporter import CalibrationReporter
from app.core.policy import PolicyService
from app.core.schemas import PolicyConfig
from app.db.tables import CalibrationReportTable

logger = logging.getLogger(__name__)
router = APIRouter()


class RunCalibrationRequest(BaseModel):
    """Request body for running calibration."""
    week_start: Optional[str] = None
    week_end: Optional[str] = None


# ============================================================================
# Helper Functions
# ============================================================================


def _find_suggestion_in_reports(
    reports: list[dict[str, Any]], suggestion_id: str
) -> tuple[Optional[dict], Optional[dict]]:
    """Find a suggestion across all reports.

    Returns:
        Tuple of (report_dict, suggestion_dict) or (None, None)
    """
    for report in reports:
        for s in report.get("suggestions", []):
            if s.get("suggestion_id") == suggestion_id:
                return report, s
    return None, None


def _set_nested_value(d: dict, path: str, value: Any) -> bool:
    """Set a value in a nested dict using dot-separated path.

    Returns True if the path was valid, False otherwise.
    """
    keys = path.split(".")
    current = d
    for key in keys[:-1]:
        if not isinstance(current, dict) or key not in current:
            return False
        current = current[key]
    if not isinstance(current, dict) or keys[-1] not in current:
        return False
    current[keys[-1]] = value
    return True


def _get_nested_value(d: dict, path: str) -> Any:
    """Get a value from a nested dict using dot-separated path."""
    keys = path.split(".")
    current = d
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


# ============================================================================
# Report Endpoints
# ============================================================================


@router.post("/run")
async def run_calibration(
    request: Optional[RunCalibrationRequest] = None,
) -> dict[str, Any]:
    """Trigger weekly calibration analysis.

    Per Section 20.1 - Automated weekly analysis.
    """
    reporter = CalibrationReporter()

    week_start = request.week_start if request else None
    week_end = request.week_end if request else None

    try:
        report = await reporter.generate_report(
            week_start=week_start,
            week_end=week_end,
        )

        # Persist report to DynamoDB
        await CalibrationReportTable.put(report)

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
    """List calibration reports."""
    reports = await CalibrationReportTable.list_recent(limit=limit)
    return {
        "reports": reports,
        "count": len(reports),
    }


@router.get("/reports/{report_id}")
async def get_report(report_id: str) -> dict[str, Any]:
    """Get a specific calibration report."""
    report = await CalibrationReportTable.get(report_id)
    if not report:
        raise HTTPException(
            status_code=404,
            detail=f"Report not found: {report_id}",
        )
    return report


# ============================================================================
# Suggestion Endpoints
# ============================================================================


@router.post("/suggestions/{suggestion_id}/approve")
async def approve_suggestion(suggestion_id: str) -> dict[str, Any]:
    """Approve a threshold suggestion and create a new policy version.

    Per Section 20 - No auto-apply, human approval required.
    """
    reports = await CalibrationReportTable.list_recent(limit=50)
    report, suggestion = _find_suggestion_in_reports(reports, suggestion_id)

    if suggestion is None:
        raise HTTPException(
            status_code=404,
            detail=f"Suggestion not found: {suggestion_id}",
        )

    if suggestion.get("status") != SuggestionStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Suggestion is not pending: {suggestion.get('status')}",
        )

    # Claim the suggestion (set to APPROVED)
    report_id = report["report_id"]
    await CalibrationReportTable.update_suggestion_status(
        report_id, suggestion_id, SuggestionStatus.APPROVED.value
    )

    # Get active policy and validate field_path
    ps = PolicyService()
    active_policy = await ps.get_active()
    if not active_policy:
        return {
            "message": "Suggestion approved (no active policy to update)",
            "suggestion_id": suggestion_id,
        }

    config_dict = active_policy.config.model_dump()
    field_path = suggestion.get("field_path", "")
    current_value = _get_nested_value(config_dict, field_path)

    if current_value is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid field path: {field_path}",
        )

    # Coerce type if needed
    suggested_value = suggestion.get("suggested_value")
    if isinstance(current_value, int) and not isinstance(suggested_value, int):
        suggested_value = int(round(suggested_value))

    # Apply the suggested value
    _set_nested_value(config_dict, field_path, suggested_value)

    # Create new policy version
    try:
        new_config = PolicyConfig(**config_dict)
        new_policy = await ps.create_version(
            config=new_config,
            user="calibration-auto",
        )
        return {
            "message": "Suggestion approved and policy version created",
            "suggestion_id": suggestion_id,
            "new_policy_version": new_policy.version,
            "policy_hash": new_policy.policy_hash,
        }
    except Exception as e:
        # Rollback: revert suggestion status to PENDING
        await CalibrationReportTable.update_suggestion_status(
            report_id, suggestion_id, SuggestionStatus.PENDING.value
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create policy version: {str(e)}",
        )


@router.post("/suggestions/{suggestion_id}/reject")
async def reject_suggestion(suggestion_id: str) -> dict[str, Any]:
    """Reject a threshold suggestion."""
    reports = await CalibrationReportTable.list_recent(limit=50)
    _, suggestion = _find_suggestion_in_reports(reports, suggestion_id)

    if suggestion is None:
        raise HTTPException(
            status_code=404,
            detail=f"Suggestion not found: {suggestion_id}",
        )

    if suggestion.get("status") != SuggestionStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Suggestion is not pending: {suggestion.get('status')}",
        )

    return {
        "message": "Suggestion rejected",
        "suggestion_id": suggestion_id,
    }


# ============================================================================
# Summary Endpoint
# ============================================================================


@router.get("/summary")
async def get_calibration_summary() -> dict[str, Any]:
    """Get a summary of calibration status."""
    reports = await CalibrationReportTable.list_recent(limit=50)

    latest_report = reports[0] if reports else None

    pending_count = 0
    pending_details = []
    for report in reports:
        for s in report.get("suggestions", []):
            if s.get("status") == SuggestionStatus.PENDING.value:
                pending_count += 1
                pending_details.append(s)

    return {
        "total_reports": len(reports),
        "latest_report": latest_report,
        "pending_suggestions": pending_count,
        "pending_suggestion_details": pending_details,
    }
