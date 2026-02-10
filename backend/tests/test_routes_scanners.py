"""API route tests for scanner endpoints.

Tests HTTP behavior for scan triggering, status checks, and opportunity listing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an async test client for the FastAPI app."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestTriggerScan:
    """Tests for POST /api/scanners/run."""

    @pytest.mark.asyncio
    async def test_trigger_scan_returns_200(self, client):
        resp = await client.post(
            "/api/scanners/run",
            json={"tickers": ["AAPL"], "run_full_pipeline": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert data["status"] == "started"

    @pytest.mark.asyncio
    async def test_trigger_scan_no_body_uses_defaults(self, client):
        resp = await client.post("/api/scanners/run", json={})
        assert resp.status_code == 200


class TestGetScanStatus:
    """Tests for GET /api/scanners/status/{run_id}."""

    @pytest.mark.asyncio
    async def test_status_not_found_returns_404(self, client):
        with patch("app.api.routes.scanners.ScanStatusTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            resp = await client.get("/api/scanners/status/nonexistent-id")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_found_in_dynamodb_returns_200(self, client):
        with patch("app.api.routes.scanners.ScanStatusTable") as mock_table:
            mock_table.get = AsyncMock(return_value={
                "status": "completed",
                "started_at": "2026-01-17T16:00:00",
                "completed_at": "2026-01-17T16:05:00",
                "tickers_scanned": 10,
                "opportunities_created": 5,
            })
            resp = await client.get("/api/scanners/status/run-test-123")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"


class TestListOpportunities:
    """Tests for GET /api/scanners/opportunities."""

    @pytest.mark.asyncio
    async def test_list_by_date_returns_200(self, client, sample_opportunity):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_date = AsyncMock(return_value=[sample_opportunity])
            resp = await client.get("/api/scanners/opportunities?date=2026-01-17")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert "opportunities" in data

    @pytest.mark.asyncio
    async def test_list_by_ticker_returns_200(self, client, sample_opportunity):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_ticker = AsyncMock(return_value=[sample_opportunity])
            resp = await client.get("/api/scanners/opportunities?ticker=AAPL")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_empty_returns_200(self, client):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_date = AsyncMock(return_value=[])
            resp = await client.get("/api/scanners/opportunities?date=2099-01-01")

        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestGetOpportunity:
    """Tests for GET /api/scanners/opportunities/{opportunity_id}."""

    @pytest.mark.asyncio
    async def test_get_opportunity_returns_200(self, client, sample_opportunity):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.get = AsyncMock(return_value=sample_opportunity)
            resp = await client.get(
                "/api/scanners/opportunities/opp-001"
                "?ticker=AAPL&timestamp=2026-01-17T16:00:00+00:00"
            )

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_opportunity_not_found_returns_404(self, client):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            resp = await client.get(
                "/api/scanners/opportunities/nonexistent"
                "?ticker=AAPL&timestamp=2026-01-17"
            )

        assert resp.status_code == 404


class TestScannerStats:
    """Tests for GET /api/scanners/stats."""

    @pytest.mark.asyncio
    async def test_stats_returns_200(self, client):
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_date = AsyncMock(return_value=[])
            resp = await client.get("/api/scanners/stats?date=2026-01-17")

        assert resp.status_code == 200
        data = resp.json()
        assert "total_opportunities" in data
        assert "by_scanner" in data
        assert "by_direction" in data
