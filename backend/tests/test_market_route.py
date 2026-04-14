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
    async def test_returns_quotes_during_market_hours(self, app):
        """When market is open and Polygon returns data, quotes are populated.

        Chain entries match the real Polygon v3 snapshot shape, with the OCC
        symbol at details.ticker (not at the top level).
        """
        chain_data = [
            {
                "details": {"ticker": "O:AAPL260320C00185000"},
                "last_quote": {"bid": 5.10, "ask": 5.30},
                "day": {"close": 5.15, "volume": 1200},
                "implied_volatility": 0.35,
                "greeks": {"delta": 0.55, "theta": -0.08},
                "open_interest": 3400,
            },
            {
                "details": {"ticker": "O:AAPL260320C00190000"},
                "last_quote": {"bid": 3.00, "ask": 3.20},
                "day": {"close": 3.05, "volume": 800},
                "implied_volatility": 0.32,
                "greeks": {"delta": 0.42, "theta": -0.06},
                "open_interest": 1500,
            },
        ]

        mock_polygon = MagicMock()
        mock_polygon.__aenter__ = AsyncMock(return_value=mock_polygon)
        mock_polygon.__aexit__ = AsyncMock(return_value=False)
        mock_polygon.get_options_chain_minimal = AsyncMock(return_value=chain_data)

        with patch("app.api.routes.market.PolygonClient", return_value=mock_polygon), \
             patch("app.api.routes.market.get_market_status", return_value="open"):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/market/quotes?contracts=O:AAPL260320C00185000"
                )

        assert resp.status_code == 200
        data = resp.json()
        quote = data["quotes"]["O:AAPL260320C00185000"]
        assert quote["bid"] == 5.10
        assert quote["ask"] == 5.30
        assert quote["mid"] == pytest.approx(5.20)
        assert quote["iv"] == 0.35
        assert quote["delta"] == 0.55
        assert quote["theta"] == -0.08
        assert quote["volume"] == 1200
        assert quote["openInterest"] == 3400
        assert "updatedAt" in quote

    @pytest.mark.asyncio
    async def test_missing_contract_omitted(self, app):
        """Contracts not found in chain are silently omitted."""
        chain_data = [
            {
                "details": {"ticker": "O:AAPL260320C00185000"},
                "last_quote": {"bid": 5.10, "ask": 5.30},
                "day": {"close": 5.15, "volume": 100},
                "implied_volatility": 0.35,
                "greeks": {"delta": 0.55, "theta": -0.08},
                "open_interest": 500,
            },
        ]

        mock_polygon = MagicMock()
        mock_polygon.__aenter__ = AsyncMock(return_value=mock_polygon)
        mock_polygon.__aexit__ = AsyncMock(return_value=False)
        mock_polygon.get_options_chain_minimal = AsyncMock(return_value=chain_data)

        with patch("app.api.routes.market.PolygonClient", return_value=mock_polygon), \
             patch("app.api.routes.market.get_market_status", return_value="open"):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/market/quotes?contracts=O:AAPL260320C00185000,O:AAPL260320C00999000"
                )

        data = resp.json()
        assert "O:AAPL260320C00185000" in data["quotes"]
        assert "O:AAPL260320C00999000" not in data["quotes"]

    @pytest.mark.asyncio
    async def test_closed_market_returns_empty(self, app):
        """During closed market, returns empty quotes without calling Polygon."""
        with patch("app.api.routes.market.get_market_status", return_value="closed"):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/market/quotes?contracts=O:AAPL260320C00185000"
                )

        assert resp.status_code == 200
        assert resp.json()["quotes"] == {}

    @pytest.mark.asyncio
    async def test_groups_by_underlying_and_expiry(self, app):
        """Contracts sharing (underlying, expiry) should batch into one chain
        fetch filtered to that expiry — so the result fits on page 1."""
        mock_polygon = MagicMock()
        mock_polygon.__aenter__ = AsyncMock(return_value=mock_polygon)
        mock_polygon.__aexit__ = AsyncMock(return_value=False)
        mock_polygon.get_options_chain_minimal = AsyncMock(return_value=[
            {
                "details": {"ticker": "O:AAPL260320C00185000"},
                "last_quote": {"bid": 5.0, "ask": 5.2},
                "day": {"volume": 100},
                "implied_volatility": 0.3,
                "greeks": {"delta": 0.5, "theta": -0.05},
                "open_interest": 200,
            },
            {
                "details": {"ticker": "O:AAPL260320P00185000"},
                "last_quote": {"bid": 2.0, "ask": 2.2},
                "day": {"volume": 50},
                "implied_volatility": 0.28,
                "greeks": {"delta": -0.45, "theta": -0.04},
                "open_interest": 150,
            },
        ])

        with patch("app.api.routes.market.PolygonClient", return_value=mock_polygon), \
             patch("app.api.routes.market.get_market_status", return_value="open"):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/market/quotes?contracts=O:AAPL260320C00185000,O:AAPL260320P00185000"
                )

        # Both contracts share the same (AAPL, 2026-03-20) so only one call.
        assert mock_polygon.get_options_chain_minimal.call_count == 1
        call_kwargs = mock_polygon.get_options_chain_minimal.call_args.kwargs
        assert call_kwargs["expiration_date_gte"] == "2026-03-20"
        assert call_kwargs["expiration_date_lte"] == "2026-03-20"
        data = resp.json()
        assert len(data["quotes"]) == 2
