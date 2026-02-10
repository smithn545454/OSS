"""Tests for api/routes/scanners.py.

Covers trigger_scan, get_scan_status, list_opportunities,
get_opportunity, get_scanner_stats, trigger_uv_scan, and process_pipeline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import os

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
    async def test_status_from_db(self, app):
        with patch("app.api.routes.scanners.ScanStatusTable") as mock_table:
            mock_table.get = AsyncMock(return_value={
                "status": "completed",
                "started_at": "2026-01-17T10:00:00Z",
                "completed_at": "2026-01-17T10:01:00Z",
                "tickers_scanned": 100,
            })
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/scanners/status/run-123")
            assert resp.status_code == 200
            assert resp.json()["status"] == "completed"

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


class TestTriggerUvScan:

    @pytest.mark.asyncio
    async def test_uv_scan_not_configured(self, app):
        """When Lambda function names aren't set, should return 503."""
        import app.api.routes.scanners as scanners_mod
        original_pub = scanners_mod.UV_PUBLISHER_FUNCTION_NAME
        original_agg = scanners_mod.UV_AGGREGATOR_FUNCTION_NAME
        try:
            scanners_mod.UV_PUBLISHER_FUNCTION_NAME = ""
            scanners_mod.UV_AGGREGATOR_FUNCTION_NAME = ""
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/scanners/uv-scan-trigger")
            assert resp.status_code == 503
        finally:
            scanners_mod.UV_PUBLISHER_FUNCTION_NAME = original_pub
            scanners_mod.UV_AGGREGATOR_FUNCTION_NAME = original_agg

    @pytest.mark.asyncio
    async def test_uv_scan_configured(self, app):
        import app.api.routes.scanners as scanners_mod
        original_pub = scanners_mod.UV_PUBLISHER_FUNCTION_NAME
        original_agg = scanners_mod.UV_AGGREGATOR_FUNCTION_NAME
        try:
            scanners_mod.UV_PUBLISHER_FUNCTION_NAME = "pub-fn"
            scanners_mod.UV_AGGREGATOR_FUNCTION_NAME = "agg-fn"
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/scanners/uv-scan-trigger")
            assert resp.status_code == 200
            assert resp.json()["status"] == "started"
        finally:
            scanners_mod.UV_PUBLISHER_FUNCTION_NAME = original_pub
            scanners_mod.UV_AGGREGATOR_FUNCTION_NAME = original_agg


class TestProcessPipeline:

    @pytest.mark.asyncio
    async def test_no_candidates(self, app):
        with patch("app.api.routes.scanners.UVCandidateTable") as mock_table:
            mock_table.list_processed_by_scan = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/scanners/process-pipeline/scan-123")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "no_candidates"

    @pytest.mark.asyncio
    async def test_fetch_error(self, app):
        with patch("app.api.routes.scanners.UVCandidateTable") as mock_table:
            mock_table.list_processed_by_scan = AsyncMock(
                side_effect=Exception("DB error")
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/scanners/process-pipeline/scan-err")
            assert resp.status_code == 500
