"""Tests for the Market API routes.

Covers market context and contract quotes proxy endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestMarketContext:
    """Test GET /api/market/context."""

    @pytest.mark.asyncio
    async def test_context_returns_200(self, client):
        with patch("app.api.routes.market.PolygonClient") as MockPC:
            mock_client = AsyncMock()
            mock_client.get_market_status.return_value = {"market": "open"}
            MockPC.return_value.__aenter__.return_value = mock_client
            response = await client.get("/api/market/context")
        assert response.status_code == 200


class TestContractQuotes:
    """Test GET /api/market/quotes."""

    @pytest.mark.asyncio
    async def test_quotes_returns_200(self, client):
        with patch("app.api.routes.market.PolygonClient") as MockPC:
            mock_client = AsyncMock()
            mock_client.get_last_quote.return_value = {"bid": 5.0, "ask": 5.4}
            MockPC.return_value.__aenter__.return_value = mock_client
            response = await client.get(
                "/api/market/quotes?contracts=O:AAPL260320C00185000"
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_quotes_empty_contracts(self, client):
        response = await client.get("/api/market/quotes?contracts=")
        # Should return 200 with empty or error
        assert response.status_code in (200, 400, 422)
