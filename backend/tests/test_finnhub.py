"""Tests for FinnhubClient.

Tests lazy initialization, async context manager lifecycle,
rate limiting, and earnings calendar parsing.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.finnhub import FinnhubClient, FINNHUB_BASE_URL, MIN_REQUEST_INTERVAL


# ---------------------------------------------------------------------------
# Lifecycle / initialization tests
# ---------------------------------------------------------------------------


class TestFinnhubClientLifecycle:
    """Tests for client initialization and cleanup."""

    def test_init_sets_api_key(self):
        """Constructor stores API key and leaves client as None."""
        client = FinnhubClient(api_key="test-key")
        assert client._api_key == "test-key"
        assert client._client is None

    async def test_ensure_client_creates_httpx_client(self):
        """_ensure_client creates httpx.AsyncClient on first call."""
        client = FinnhubClient(api_key="test-key")
        assert client._client is None

        await client._ensure_client()

        assert client._client is not None
        assert isinstance(client._client, httpx.AsyncClient)
        await client.close()

    async def test_ensure_client_is_idempotent(self):
        """_ensure_client does not recreate client on subsequent calls."""
        client = FinnhubClient(api_key="test-key")

        await client._ensure_client()
        first_client = client._client

        await client._ensure_client()
        assert client._client is first_client
        await client.close()

    async def test_close_clears_client(self):
        """close() shuts down the httpx client and sets it to None."""
        client = FinnhubClient(api_key="test-key")
        await client._ensure_client()
        assert client._client is not None

        await client.close()
        assert client._client is None

    async def test_close_is_safe_when_not_initialized(self):
        """close() is a no-op when client was never initialized."""
        client = FinnhubClient(api_key="test-key")
        await client.close()  # Should not raise

    async def test_context_manager_initializes_and_cleans_up(self):
        """async with creates client on enter and closes on exit."""
        async with FinnhubClient(api_key="test-key") as client:
            assert client._client is not None

        assert client._client is None

    async def test_lazy_init_without_context_manager(self):
        """Client works without async with (lazy initialization)."""
        client = FinnhubClient(api_key="test-key")

        # Mock the httpx client to avoid real HTTP calls
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"earningsCalendar": []}
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.finnhub.httpx.AsyncClient", return_value=mock_httpx_client):
            # This should work without async with
            result = await client.get_earnings_calendar("AAPL")

        assert result == []
        await client.close()


# ---------------------------------------------------------------------------
# Rate-limited request tests
# ---------------------------------------------------------------------------


class TestRateLimitedRequest:
    """Tests for the rate-limited request mechanism."""

    async def test_passes_api_key_as_token_param(self):
        """API key is passed as 'token' query parameter."""
        async with FinnhubClient(api_key="my-secret-key") as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": "test"}
            mock_response.raise_for_status = MagicMock()
            client._client.get = AsyncMock(return_value=mock_response)

            await client._rate_limited_request("/test")

            call_args = client._client.get.call_args
            assert call_args[1]["params"]["token"] == "my-secret-key"

    async def test_merges_custom_params(self):
        """Custom params are merged with the token."""
        async with FinnhubClient(api_key="key") as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.raise_for_status = MagicMock()
            client._client.get = AsyncMock(return_value=mock_response)

            await client._rate_limited_request("/test", params={"symbol": "AAPL"})

            call_args = client._client.get.call_args
            assert call_args[1]["params"]["symbol"] == "AAPL"
            assert call_args[1]["params"]["token"] == "key"

    async def test_returns_none_on_429(self):
        """Returns None and logs warning on rate limit (429)."""
        async with FinnhubClient(api_key="key") as client:
            mock_response = MagicMock()
            mock_response.status_code = 429
            client._client.get = AsyncMock(return_value=mock_response)

            with patch("app.services.finnhub.asyncio.sleep", new_callable=AsyncMock):
                result = await client._rate_limited_request("/test")

            assert result is None

    async def test_returns_none_on_http_error(self):
        """Returns None on HTTP errors."""
        async with FinnhubClient(api_key="key") as client:
            client._client.get = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "Server Error",
                    request=MagicMock(),
                    response=MagicMock(status_code=500),
                )
            )

            result = await client._rate_limited_request("/test")

            assert result is None


# ---------------------------------------------------------------------------
# Earnings calendar tests
# ---------------------------------------------------------------------------


class TestEarningsCalendar:
    """Tests for earnings calendar methods."""

    async def test_get_earnings_calendar_returns_list(self):
        """get_earnings_calendar extracts earningsCalendar from response."""
        async with FinnhubClient(api_key="key") as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "earningsCalendar": [
                    {"date": "2026-03-15", "symbol": "AAPL", "hour": "amc"},
                ]
            }
            mock_response.raise_for_status = MagicMock()
            client._client.get = AsyncMock(return_value=mock_response)

            result = await client.get_earnings_calendar("AAPL")

        assert len(result) == 1
        assert result[0]["date"] == "2026-03-15"

    async def test_get_earnings_calendar_returns_empty_on_no_data(self):
        """get_earnings_calendar returns [] when no earningsCalendar key."""
        async with FinnhubClient(api_key="key") as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.raise_for_status = MagicMock()
            client._client.get = AsyncMock(return_value=mock_response)

            result = await client.get_earnings_calendar("AAPL")

        assert result == []

    async def test_get_earnings_calendar_passes_date_params(self):
        """get_earnings_calendar passes from/to date parameters."""
        async with FinnhubClient(api_key="key") as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"earningsCalendar": []}
            mock_response.raise_for_status = MagicMock()
            client._client.get = AsyncMock(return_value=mock_response)

            await client.get_earnings_calendar(
                "AAPL",
                from_date=date(2026, 1, 1),
                to_date=date(2026, 3, 31),
            )

            call_args = client._client.get.call_args
            params = call_args[1]["params"]
            assert params["from"] == "2026-01-01"
            assert params["to"] == "2026-03-31"

    async def test_get_next_earnings_date_returns_tuple(self):
        """get_next_earnings_date returns (date, time_of_day) tuple."""
        async with FinnhubClient(api_key="key") as client:
            today = date.today()
            future_date = date(today.year, today.month, min(today.day + 10, 28))
            if future_date <= today:
                future_date = date(today.year + 1, 1, 15)

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "earningsCalendar": [
                    {"date": future_date.isoformat(), "symbol": "AAPL", "hour": "amc"},
                ]
            }
            mock_response.raise_for_status = MagicMock()
            client._client.get = AsyncMock(return_value=mock_response)

            result = await client.get_next_earnings_date("AAPL")

        assert result is not None
        earnings_date, time_of_day = result
        assert earnings_date == future_date
        assert time_of_day == "amc"

    async def test_get_next_earnings_date_returns_none_when_empty(self):
        """get_next_earnings_date returns None when no earnings found."""
        async with FinnhubClient(api_key="key") as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"earningsCalendar": []}
            mock_response.raise_for_status = MagicMock()
            client._client.get = AsyncMock(return_value=mock_response)

            result = await client.get_next_earnings_date("AAPL")

        assert result is None

    async def test_get_days_to_earnings_returns_int(self):
        """get_days_to_earnings returns integer days count."""
        async with FinnhubClient(api_key="key") as client:
            today = date.today()
            future_date = date(today.year, today.month, min(today.day + 15, 28))
            if future_date <= today:
                future_date = date(today.year + 1, 1, 20)

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "earningsCalendar": [
                    {"date": future_date.isoformat(), "symbol": "AAPL", "hour": "bmo"},
                ]
            }
            mock_response.raise_for_status = MagicMock()
            client._client.get = AsyncMock(return_value=mock_response)

            result = await client.get_days_to_earnings("AAPL")

        assert result is not None
        assert isinstance(result, int)
        assert result >= 0

    async def test_get_days_to_earnings_returns_none_when_unavailable(self):
        """get_days_to_earnings returns None when no earnings found."""
        async with FinnhubClient(api_key="key") as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"earningsCalendar": []}
            mock_response.raise_for_status = MagicMock()
            client._client.get = AsyncMock(return_value=mock_response)

            result = await client.get_days_to_earnings("AAPL")

        assert result is None
