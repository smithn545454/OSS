"""Tests for api/routes/scanners.py.

Covers trigger_scan, get_scan_status, list_opportunities,
get_opportunity, and get_scanner_stats.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app():
    return create_app()


class TestTriggerScan:

    @pytest.mark.asyncio
    async def test_trigger_scan(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/scanners/run",
                json={"tickers": ["AAPL"], "run_full_pipeline": True},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert "run_id" in data

    @pytest.mark.asyncio
    async def test_trigger_scan_defaults(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/scanners/run", json={})
        assert resp.status_code == 200


class TestGetScanStatus:

    @pytest.mark.asyncio
    async def test_status_not_found(self, app):
        with patch("app.api.routes.scanners.ScanStatusTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/scanners/status/missing-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_from_memory(self, app):
        from app.api.routes.scanners import _scan_status
        _scan_status["run-123"] = {
            "status": "completed",
            "started_at": "2026-01-17T10:00:00Z",
            "completed_at": "2026-01-17T10:01:00Z",
            "tickers_scanned": 100,
        }
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/scanners/status/run-123")
            assert resp.status_code == 200
            assert resp.json()["status"] == "completed"
        finally:
            _scan_status.pop("run-123", None)

    @pytest.mark.asyncio
    async def test_status_in_memory(self, app):
        from app.api.routes.scanners import _scan_status
        _scan_status["mem-run"] = {"status": "running", "started_at": "2026-01-17"}

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/scanners/status/mem-run")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"
        finally:
            _scan_status.pop("mem-run", None)


class TestListOpportunities:

    @pytest.mark.asyncio
    async def test_list_by_date(self, app):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_date = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/scanners/opportunities?date=2026-01-17")
            assert resp.status_code == 200
            assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_list_by_ticker(self, app):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_ticker = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/scanners/opportunities?ticker=AAPL")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_default(self, app):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_date = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/scanners/opportunities")
            assert resp.status_code == 200


class TestGetOpportunity:

    @pytest.mark.asyncio
    async def test_not_found(self, app):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/scanners/opportunities/opp-1?ticker=AAPL&timestamp=2026-01-17"
                )
            assert resp.status_code == 404


class TestGetScannerStats:

    @pytest.mark.asyncio
    async def test_stats_today(self, app):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_date = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/scanners/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_opportunities"] == 0

    @pytest.mark.asyncio
    async def test_stats_with_date(self, app):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_date = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/scanners/stats?date=2026-01-15")
            assert resp.status_code == 200


