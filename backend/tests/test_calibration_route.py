"""Tests for api/routes/calibration.py.

Covers list_reports, get_report, run_calibration,
approve_suggestion, reject_suggestion, get_calibration_summary,
and _find_suggestion_in_report.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.calibration import _find_suggestion_in_report
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


class TestFindSuggestionInReport:

    def test_found(self):
        report = {"suggestions": [{"suggestion_id": "s1", "value": 100}]}
        result = _find_suggestion_in_report(report, "s1")
        assert result is not None
        assert result["suggestion_id"] == "s1"

    def test_not_found(self):
        report = {"suggestions": [{"suggestion_id": "s1"}]}
        assert _find_suggestion_in_report(report, "s99") is None

    def test_empty_suggestions(self):
        report = {"suggestions": []}
        assert _find_suggestion_in_report(report, "s1") is None

    def test_no_suggestions_key(self):
        assert _find_suggestion_in_report({}, "s1") is None


class TestListReports:

    @pytest.mark.asyncio
    async def test_list_reports(self, app):
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.list_recent = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/calibration/reports")
            assert resp.status_code == 200
            assert resp.json()["count"] == 0


class TestGetReport:

    @pytest.mark.asyncio
    async def test_report_found(self, app):
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.get = AsyncMock(return_value={"report_id": "r1"})
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/calibration/reports/r1")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_report_not_found(self, app):
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/calibration/reports/missing")
            assert resp.status_code == 404


class TestRunCalibration:

    @pytest.mark.asyncio
    async def test_run_success(self, app):
        from unittest.mock import MagicMock
        mock_report_dict = {
            "report_id": "r-new",
            "week_start": "2026-01-01",
            "week_end": "2026-01-07",
            "positions_closed": 10,
            "win_rate": 65.0,
            "avg_return": 12.5,
        }
        report_obj = MagicMock()
        report_obj.to_dict.return_value = mock_report_dict
        with patch("app.api.routes.calibration.CalibrationReporter") as mock_reporter, \
             patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            instance = mock_reporter.return_value
            instance.generate_report = AsyncMock(return_value=report_obj)
            mock_table.put = AsyncMock()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/calibration/run", json={})
            assert resp.status_code == 200
            data = resp.json()
            assert data["report"]["report_id"] == "r-new"

    @pytest.mark.asyncio
    async def test_run_error(self, app):
        with patch("app.api.routes.calibration.CalibrationReporter") as mock_reporter:
            instance = mock_reporter.return_value
            instance.generate_report = AsyncMock(side_effect=Exception("fail"))
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/calibration/run", json={})
            assert resp.status_code == 500


class TestApproveSuggestion:

    @pytest.mark.asyncio
    async def test_approve_not_found(self, app):
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.list_recent = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/missing/approve"
                )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_not_pending(self, app):
        report = {
            "report_id": "r1",
            "suggestions": [{"suggestion_id": "s1", "status": "APPROVED"}],
        }
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.list_recent = AsyncMock(return_value=[report])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/s1/approve"
                )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_approve_race_condition(self, app):
        """If another request already claimed the suggestion, returns 409."""
        report = {
            "report_id": "r1",
            "suggestions": [{
                "suggestion_id": "s1",
                "status": "PENDING",
                "field_path": "gates.min_volume",
                "suggested_value": 50,
            }],
        }
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.list_recent = AsyncMock(return_value=[report])
            mock_table.update_suggestion_status = AsyncMock(return_value=False)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/s1/approve"
                )
            assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_approve_no_active_policy(self, app):
        report = {
            "report_id": "r1",
            "suggestions": [{
                "suggestion_id": "s1",
                "status": "PENDING",
                "field_path": "gates.min_volume",
                "suggested_value": 50,
            }],
        }
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table, \
             patch("app.api.routes.calibration.PolicyService") as mock_ps:
            mock_table.list_recent = AsyncMock(return_value=[report])
            mock_table.update_suggestion_status = AsyncMock(return_value=True)
            ps_instance = mock_ps.return_value
            ps_instance.get_active = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/s1/approve"
                )
            assert resp.status_code == 200
            assert "no active policy" in resp.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_approve_success(self, app):
        from unittest.mock import MagicMock
        report = {
            "report_id": "r1",
            "suggestions": [{
                "suggestion_id": "s1",
                "status": "PENDING",
                "field_path": "gates.min_volume",
                "suggested_value": 50,
            }],
        }
        mock_policy = MagicMock()
        mock_policy.version = "v2.1.0"
        mock_policy.policy_hash = "abc123"
        mock_policy.config.model_dump.return_value = {"gates": {"min_volume": 75}}

        new_policy = MagicMock()
        new_policy.version = "v2.2.0"
        new_policy.policy_hash = "def456"

        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table, \
             patch("app.api.routes.calibration.PolicyService") as mock_ps, \
             patch("app.api.routes.calibration.PolicyConfig") as mock_pc:
            mock_table.list_recent = AsyncMock(return_value=[report])
            mock_table.update_suggestion_status = AsyncMock(return_value=True)
            ps_instance = mock_ps.return_value
            ps_instance.get_active = AsyncMock(return_value=mock_policy)
            ps_instance.create_version = AsyncMock(return_value=new_policy)
            mock_pc.return_value = MagicMock()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/s1/approve"
                )
            assert resp.status_code == 200
            data = resp.json()
            assert "approved" in data["message"].lower()
            assert data["new_policy_version"] == "v2.2.0"


class TestRejectSuggestion:

    @pytest.mark.asyncio
    async def test_reject_not_found(self, app):
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.list_recent = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/missing/reject"
                )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_success(self, app):
        report = {
            "report_id": "r1",
            "suggestions": [{"suggestion_id": "s1", "status": "PENDING"}],
        }
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.list_recent = AsyncMock(return_value=[report])
            mock_table.update_suggestion_status = AsyncMock()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/s1/reject"
                )
            assert resp.status_code == 200
            assert "rejected" in resp.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_reject_already_processed(self, app):
        report = {
            "report_id": "r1",
            "suggestions": [{"suggestion_id": "s1", "status": "APPROVED"}],
        }
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.list_recent = AsyncMock(return_value=[report])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/s1/reject"
                )
            assert resp.status_code == 400


class TestGetCalibrationSummary:

    @pytest.mark.asyncio
    async def test_summary_empty(self, app):
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.list_recent = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/calibration/summary")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_reports"] == 0
            assert data["pending_suggestions"] == 0

    @pytest.mark.asyncio
    async def test_summary_with_data(self, app):
        reports = [{
            "report_id": "r1",
            "suggestions": [
                {"suggestion_id": "s1", "status": "PENDING"},
                {"suggestion_id": "s2", "status": "APPROVED"},
            ],
        }]
        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            mock_table.list_recent = AsyncMock(return_value=reports)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/calibration/summary")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_reports"] == 1
            assert data["pending_suggestions"] == 1
