"""Tests for the Paper Trading API routes.

Covers position listing, metrics, and management endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

_ROUTE = "app.api.routes.paper_trading"
_METRICS = "app.paper_trading.metrics"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _mock_pos_table():
    """Create a mock PaperPositionTable with all async methods."""
    mock = MagicMock()
    mock.list_recent = AsyncMock(return_value=[])
    mock.list_open = AsyncMock(return_value=[])
    mock.list_closed = AsyncMock(return_value=[])
    mock.list_by_status = AsyncMock(return_value=[])
    mock.list_all = AsyncMock(return_value=[])
    mock.list_open_paginated = AsyncMock(return_value=([], None))
    mock.list_closed_paginated = AsyncMock(return_value=([], None))
    mock.get = AsyncMock(return_value=None)
    return mock


class TestListPositions:
    """Test GET /api/paper-trading/positions."""

    @pytest.mark.asyncio
    async def test_list_returns_200(self, client):
        mock = _mock_pos_table()
        with patch(f"{_ROUTE}.PaperPositionTable", mock):
            response = await client.get("/api/paper-trading/positions")
        assert response.status_code == 200


class TestGetMetrics:
    """Test GET /api/paper-trading/metrics."""

    @pytest.mark.asyncio
    async def test_metrics_returns_200(self, client):
        mock = _mock_pos_table()
        with patch(f"{_ROUTE}.PaperPositionTable", mock), \
             patch(f"{_METRICS}.PaperPositionTable", mock):
            response = await client.get("/api/paper-trading/metrics")
        assert response.status_code == 200


class TestTierComparison:
    """Test GET /api/paper-trading/metrics/tiers."""

    @pytest.mark.asyncio
    async def test_tiers_returns_200(self, client):
        mock = _mock_pos_table()
        with patch(f"{_ROUTE}.PaperPositionTable", mock), \
             patch(f"{_METRICS}.PaperPositionTable", mock):
            response = await client.get("/api/paper-trading/metrics/tiers")
        assert response.status_code == 200


class TestGetSummary:
    """Test GET /api/paper-trading/summary."""

    @pytest.mark.asyncio
    async def test_summary_returns_200(self, client):
        mock = _mock_pos_table()
        with patch(f"{_ROUTE}.PaperPositionTable", mock), \
             patch(f"{_METRICS}.PaperPositionTable", mock):
            response = await client.get("/api/paper-trading/summary")
        assert response.status_code == 200


class TestExitAnalysis:
    """Test GET /api/paper-trading/metrics/exits."""

    @pytest.mark.asyncio
    async def test_exits_returns_200(self, client):
        mock = _mock_pos_table()
        with patch(f"{_ROUTE}.PaperPositionTable", mock), \
             patch(f"{_METRICS}.PaperPositionTable", mock):
            response = await client.get("/api/paper-trading/metrics/exits")
        assert response.status_code == 200
