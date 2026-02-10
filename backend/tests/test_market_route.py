"""Tests for api/routes/market.py.

Covers get_market_status, calculate_change_percent,
get_market_context, and get_contract_quotes.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.market import (
    calculate_change_percent,
    get_market_status,
)
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


class TestGetMarketStatus:

    def test_returns_valid_status(self):
        status = get_market_status()
        assert status in ("pre", "open", "after", "closed")

    @patch("app.api.routes.market.datetime")
    def test_weekend_closed(self, mock_dt):
        # Saturday
        mock_now = MagicMock()
        mock_now.weekday.return_value = 5
        mock_now.hour = 12
        mock_now.minute = 0
        mock_dt.now.return_value = mock_now
        assert get_market_status() == "closed"

    @patch("app.api.routes.market.datetime")
    def test_market_open_hours(self, mock_dt):
        # Weekday 14:30 UTC = 9:30 ET (market open)
        mock_now = MagicMock()
        mock_now.weekday.return_value = 1  # Tuesday
        mock_now.hour = 15  # 15 UTC = 10 ET
        mock_now.minute = 0
        mock_dt.now.return_value = mock_now
        assert get_market_status() == "open"


class TestCalculateChangePercent:

    def test_basic_change(self):
        assert calculate_change_percent(110.0, 100.0) == pytest.approx(10.0)

    def test_zero_previous(self):
        assert calculate_change_percent(110.0, 0.0) == 0.0

    def test_negative_change(self):
        assert calculate_change_percent(90.0, 100.0) == pytest.approx(-10.0)


class TestGetMarketContext:

    @pytest.mark.asyncio
    async def test_context_with_error(self, app):
        """When Polygon fails, should still return placeholder data."""
        with patch("app.api.routes.market.PolygonClient") as mock_client, \
             patch("app.api.routes.market.PipelineRunTable") as mock_table:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(side_effect=Exception("API error"))
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance
            mock_table.list_recent = AsyncMock(return_value=[])

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/market/context")
            assert resp.status_code == 200
            data = resp.json()
            assert "spy" in data
            assert "vix" in data
            assert "marketStatus" in data


class TestGetContractQuotes:

    @pytest.mark.asyncio
    async def test_empty_contracts(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/market/quotes?contracts=")
        assert resp.status_code == 200
        assert resp.json()["quotes"] == {}

    @pytest.mark.asyncio
    async def test_with_contracts(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/market/quotes?contracts=O:AAPL260320C00185000,O:MSFT260320C00400000"
            )
        assert resp.status_code == 200
