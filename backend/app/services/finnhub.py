"""Finnhub client for earnings calendar data.

Provides accurate earnings dates with built-in rate limiting
to stay well under the free tier (60 req/min).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

# Rate limiting: 30 req/min max (50% of free tier limit)
MIN_REQUEST_INTERVAL = 2.0  # seconds between requests


class FinnhubClient:
    """Client for Finnhub API with built-in rate limiting.

    Rate limited to 30 requests/minute (half the free tier limit)
    to ensure we never hit the 60 req/min ceiling.

    Supports two usage patterns:
    - Context manager: ``async with FinnhubClient(key) as client:`` (preferred, ensures cleanup)
    - Direct: ``client = FinnhubClient(key)`` (lazy-inits on first request)
    """

    def __init__(self, api_key: str) -> None:
        """Initialize Finnhub client.

        Args:
            api_key: Finnhub API key
        """
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request_time: float = 0
        self._lock = asyncio.Lock()

    async def _ensure_client(self) -> None:
        """Lazily initialize the HTTP client if not already created."""
        if not self._client:
            self._client = httpx.AsyncClient(
                base_url=FINNHUB_BASE_URL,
                timeout=30.0,
            )

    async def close(self) -> None:
        """Close the HTTP client. Safe to call multiple times."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "FinnhubClient":
        """Async context manager entry."""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _rate_limited_request(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Make a rate-limited request to Finnhub.

        Ensures at least MIN_REQUEST_INTERVAL seconds between requests.

        Args:
            endpoint: API endpoint (e.g., "/calendar/earnings")
            params: Query parameters

        Returns:
            JSON response or None on error
        """
        await self._ensure_client()

        async with self._lock:
            # Enforce rate limit
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_request_time
            if elapsed < MIN_REQUEST_INTERVAL:
                await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)

            try:
                request_params = {"token": self._api_key}
                if params:
                    request_params.update(params)

                response = await self._client.get(endpoint, params=request_params)
                self._last_request_time = asyncio.get_event_loop().time()

                if response.status_code == 429:
                    logger.warning("Finnhub rate limit hit, backing off...")
                    await asyncio.sleep(60)  # Back off for a minute
                    return None

                response.raise_for_status()
                return response.json()

            except httpx.HTTPError as e:
                logger.error(f"Finnhub API error: {e}")
                return None

    async def get_earnings_calendar(
        self,
        symbol: str,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        """Get earnings calendar for a symbol.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL")
            from_date: Start date for calendar range
            to_date: End date for calendar range

        Returns:
            List of earnings events
        """
        params: dict[str, Any] = {"symbol": symbol}

        if from_date:
            params["from"] = from_date.isoformat()
        if to_date:
            params["to"] = to_date.isoformat()

        data = await self._rate_limited_request("/calendar/earnings", params)

        if data and "earningsCalendar" in data:
            return data["earningsCalendar"]
        return []

    async def get_next_earnings_date(
        self,
        symbol: str,
    ) -> Optional[tuple[date, str]]:
        """Get the next earnings date for a symbol.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL")

        Returns:
            Tuple of (earnings_date, time_of_day) where time_of_day is
            "bmo" (before market open), "amc" (after market close), or "unknown".
            Returns None if no upcoming earnings found.
        """
        today = date.today()
        # Look ahead 90 days to catch next earnings
        from_date = today
        month_offset = today.month + 3 if today.month <= 9 else today.month - 9
        to_date = date(today.year, month_offset, today.day)

        try:
            # Adjust for month overflow
            if today.month > 9:
                to_date = date(today.year + 1, today.month - 9, min(today.day, 28))
            else:
                to_date = date(today.year, today.month + 3, min(today.day, 28))
        except ValueError:
            # Handle edge cases with date math
            if today.month > 9:
                to_date = date(today.year, 12, 28)
            else:
                to_date = date(today.year, today.month + 3, 28)

        earnings = await self.get_earnings_calendar(symbol, from_date, to_date)

        if not earnings:
            logger.debug(f"No earnings found for {symbol}")
            return None

        # Find the next upcoming earnings date
        for event in earnings:
            event_date_str = event.get("date")
            if not event_date_str:
                continue

            try:
                event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
                if event_date >= today:
                    # Get time of day: "bmo", "amc", or empty
                    hour = event.get("hour", "")
                    time_of_day = hour if hour in ("bmo", "amc") else "unknown"
                    return (event_date, time_of_day)
            except ValueError:
                continue

        return None

    async def get_days_to_earnings(self, symbol: str) -> Optional[int]:
        """Get days until next earnings announcement.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Days until next earnings, or None if unavailable
        """
        result = await self.get_next_earnings_date(symbol)
        if result:
            earnings_date, _ = result
            days = (earnings_date - date.today()).days
            return max(0, days)
        return None
