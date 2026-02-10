"""API route tests for calibration endpoints.

Tests HTTP behavior for calibration report generation, listing, and
suggestion approval/rejection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.calibration.models import (
    CalibrationReport,
    GateAnalysis,
    RecommendationType,
    SuggestionStatus,
)


@pytest.fixture
async def client():
    """Create an async test client for the FastAPI app."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sample_report_dict():
    """A minimal calibration report dict as stored in DynamoDB."""
    return {
        "report_id": "rpt-test-001",
        "generated_at": "2026-01-17T16:00:00",
        "week_start": "2026-01-10",
        "week_end": "2026-01-17",
        "positions_closed": 10,
        "win_rate": 65.0,
        "avg_return": 8.5,
        "gate_analyses": [],
        "suggestions": [
            {
                "suggestion_id": "sug-001",
                "gate_id": "GATE_MIN_OPEN_INTEREST",
                "field_path": "gates.min_open_interest",
                "current_value": 300,
                "suggested_value": 250,
                "estimated_impact": {
                    "additional_approvals": 3,
                    "estimated_win_rate_change": 1.5,
                },
                "status": "PENDING",
                "reason": "High false negative rate",
            }
        ],
    }


class TestRunCalibration:
    """Tests for POST /api/calibration/run."""

    @pytest.mark.asyncio
    async def test_run_calibration_returns_200(self, client):
        mock_report = CalibrationReport.create(
            week_start="2026-01-10",
            week_end="2026-01-17",
            positions_closed=10,
            win_rate=65.0,
            avg_return=8.5,
        )
        with patch("app.api.routes.calibration.CalibrationReporter") as MockReporter, \
             patch("app.api.routes.calibration.CalibrationReportTable") as MockTable:
            instance = MockReporter.return_value
            instance.generate_report = AsyncMock(return_value=mock_report)
            MockTable.put = AsyncMock()

            resp = await client.post("/api/calibration/run", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert "report" in data

    @pytest.mark.asyncio
    async def test_run_calibration_failure_returns_500(self, client):
        with patch("app.api.routes.calibration.CalibrationReporter") as MockReporter:
            instance = MockReporter.return_value
            instance.generate_report = AsyncMock(
                side_effect=Exception("DB connection failed")
            )

            resp = await client.post("/api/calibration/run", json={})

        assert resp.status_code == 500


class TestListReports:
    """Tests for GET /api/calibration/reports."""

    @pytest.mark.asyncio
    async def test_list_reports_returns_200(self, client, sample_report_dict):
        with patch("app.api.routes.calibration.CalibrationReportTable") as MockTable:
            MockTable.list_recent = AsyncMock(return_value=[sample_report_dict])
            resp = await client.get("/api/calibration/reports")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1


class TestGetReport:
    """Tests for GET /api/calibration/reports/{report_id}."""

    @pytest.mark.asyncio
    async def test_get_report_returns_200(self, client, sample_report_dict):
        with patch("app.api.routes.calibration.CalibrationReportTable") as MockTable:
            MockTable.get = AsyncMock(return_value=sample_report_dict)
            resp = await client.get("/api/calibration/reports/rpt-test-001")

        assert resp.status_code == 200
        assert resp.json()["report_id"] == "rpt-test-001"

    @pytest.mark.asyncio
    async def test_get_report_not_found_returns_404(self, client):
        with patch("app.api.routes.calibration.CalibrationReportTable") as MockTable:
            MockTable.get = AsyncMock(return_value=None)
            resp = await client.get("/api/calibration/reports/nonexistent")

        assert resp.status_code == 404


class TestApproveSuggestion:
    """Tests for POST /api/calibration/suggestions/{id}/approve."""

    @pytest.mark.asyncio
    async def test_approve_non_pending_returns_400(self, client):
        report = {
            "report_id": "rpt-001",
            "suggestions": [
                {"suggestion_id": "sug-001", "status": "APPROVED"},
            ],
        }
        with patch("app.api.routes.calibration.CalibrationReportTable") as MockTable:
            MockTable.list_recent = AsyncMock(return_value=[report])
            resp = await client.post("/api/calibration/suggestions/sug-001/approve")

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_approve_not_found_returns_404(self, client):
        with patch("app.api.routes.calibration.CalibrationReportTable") as MockTable:
            MockTable.list_recent = AsyncMock(return_value=[])
            resp = await client.post("/api/calibration/suggestions/nonexistent/approve")

        assert resp.status_code == 404


class TestRejectSuggestion:
    """Tests for POST /api/calibration/suggestions/{id}/reject."""

    @pytest.mark.asyncio
    async def test_reject_not_found_returns_404(self, client):
        with patch("app.api.routes.calibration.CalibrationReportTable") as MockTable:
            MockTable.list_recent = AsyncMock(return_value=[])
            resp = await client.post("/api/calibration/suggestions/nonexistent/reject")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_non_pending_returns_400(self, client):
        report = {
            "report_id": "rpt-001",
            "suggestions": [
                {"suggestion_id": "sug-001", "status": "REJECTED"},
            ],
        }
        with patch("app.api.routes.calibration.CalibrationReportTable") as MockTable:
            MockTable.list_recent = AsyncMock(return_value=[report])
            resp = await client.post("/api/calibration/suggestions/sug-001/reject")

        assert resp.status_code == 400


class TestCalibrationSummary:
    """Tests for GET /api/calibration/summary."""

    @pytest.mark.asyncio
    async def test_summary_returns_200(self, client, sample_report_dict):
        with patch("app.api.routes.calibration.CalibrationReportTable") as MockTable:
            MockTable.list_recent = AsyncMock(return_value=[sample_report_dict])
            resp = await client.get("/api/calibration/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert "total_reports" in data
        assert "pending_suggestions" in data
        assert data["pending_suggestions"] == 1
