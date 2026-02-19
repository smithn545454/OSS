"""Tests for Polygon client retry logic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.fixture
def mock_settings():
    with patch("app.services.polygon.get_settings") as mock:
        settings = MagicMock()
        settings.polygon_api_key = "test-key"
        mock.return_value = settings
        yield settings


class TestPolygonRetry:

    @pytest.mark.asyncio
    async def test_retries_on_429(self, mock_settings):
        from app.services.polygon import PolygonClient

        client = PolygonClient()

        # Build mock responses: 429, then 200
        resp_429 = MagicMock(spec=httpx.Response)
        resp_429.status_code = 429
        resp_429.raise_for_status.side_effect = httpx.HTTPStatusError(
            "rate limited", request=MagicMock(), response=resp_429
        )

        resp_200 = MagicMock(spec=httpx.Response)
        resp_200.status_code = 200
        resp_200.raise_for_status.return_value = None

        mock_http = AsyncMock(side_effect=[resp_429, resp_200])
        client._client = MagicMock()
        client._client.request = mock_http

        with patch("app.services.polygon.asyncio.sleep", new_callable=AsyncMock):
            result = await client._rate_limited_request("GET", "https://api.polygon.io/test")

        assert result == resp_200
        assert mock_http.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_404(self, mock_settings):
        from app.services.polygon import PolygonClient

        client = PolygonClient()

        resp_404 = MagicMock(spec=httpx.Response)
        resp_404.status_code = 404
        resp_404.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found", request=MagicMock(), response=resp_404
        )

        mock_http = AsyncMock(side_effect=[resp_404])
        client._client = MagicMock()
        client._client.request = mock_http

        result = await client._rate_limited_request("GET", "https://api.polygon.io/test")

        assert result is None
        assert mock_http.call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_retries_exhausted(self, mock_settings):
        from app.services.polygon import PolygonClient

        client = PolygonClient()

        resp_500 = MagicMock(spec=httpx.Response)
        resp_500.status_code = 500
        resp_500.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=resp_500
        )

        mock_http = AsyncMock(side_effect=[resp_500, resp_500, resp_500])
        client._client = MagicMock()
        client._client.request = mock_http

        with patch("app.services.polygon.asyncio.sleep", new_callable=AsyncMock):
            result = await client._rate_limited_request("GET", "https://api.polygon.io/test")

        assert result is None
        assert mock_http.call_count == 3  # Original + 2 retries
