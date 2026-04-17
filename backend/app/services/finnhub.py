"""Finnhub client for earnings calendar data.

Provides accurate earnings dates with built-in rate limiting
to stay well under the free tier (60 req/min).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
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

    async def get_company_news(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
    ) -> list[dict[str, Any]]:
        """Get company news articles for a symbol.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL")
            from_date: Start date for news range
            to_date: End date for news range

        Returns:
            List of news dicts with headline, summary, source, datetime, url
        """
        data = await self._rate_limited_request(
            "/company-news",
            {
                "symbol": symbol,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
            },
        )
        if isinstance(data, list):
            return data
        return []

    async def get_earnings_calendar(
        self,
        symbol: Optional[str] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        """Get earnings calendar, optionally filtered by symbol.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL"). If None, returns
                    all earnings in the date range.
            from_date: Start date for calendar range
            to_date: End date for calendar range

        Returns:
            List of earnings events
        """
        params: dict[str, Any] = {}

        if symbol:
            params["symbol"] = symbol
        if from_date:
            params["from"] = from_date.isoformat()
        if to_date:
            params["to"] = to_date.isoformat()

        data = await self._rate_limited_request("/calendar/earnings", params)

        if data and "earningsCalendar" in data:
            return data["earningsCalendar"]
        return []

    async def get_all_upcoming_earnings(
        self,
        from_date: date,
        to_date: date,
    ) -> list[dict[str, Any]]:
        """Get all earnings announcements in a date range (bulk fetch).

        One API call returns ALL companies reporting in the range.
        No symbol filter — much more efficient than per-ticker queries.

        Args:
            from_date: Start of date range
            to_date: End of date range

        Returns:
            List of earnings events with 'symbol', 'date', 'hour' fields
        """
        return await self.get_earnings_calendar(
            symbol=None, from_date=from_date, to_date=to_date,
        )

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
        to_date = today + timedelta(days=90)

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

    async def get_company_profile(
        self, symbol: str
    ) -> Optional[dict[str, Any]]:
        """Fetch the `/stock/profile2` snapshot for a ticker.

        Used by the Pillar v4 sector backfill to populate the GICS
        sector on SP500TickerTable. Returns None on error or empty
        response.

        Returns a dict with keys including:
            - finnhubIndustry (canonical sector, e.g. "Technology")
            - gicsSubIndustry, gicsIndustry, gicsIndustryGroup (optional)
            - ticker, name, marketCapitalization, ipo, exchange, ...
        """
        data = await self._rate_limited_request(
            "/stock/profile2", {"symbol": symbol}
        )
        if not data:
            return None
        # Finnhub returns an empty dict {} for unknown tickers.
        if not data.get("ticker") and not data.get("finnhubIndustry"):
            return None
        return data

    async def get_earnings_history(
        self, symbol: str
    ) -> list[dict[str, Any]]:
        """Fetch last ~4 quarters of EPS from /stock/earnings.

        Unlike /calendar/earnings (free-tier returns only the next
        upcoming event), /stock/earnings returns past quarters. Each
        item carries the quarter-end ``period`` (YYYY-MM-DD) but NOT
        the announcement date — callers resolve the announcement day
        separately (typically via a volume-spike scan of price history
        in the 2-6 week window after quarter-end).

        Returns a list of dicts with keys: ``actual``, ``estimate``,
        ``period``, ``quarter``, ``surprise``, ``surprisePercent``,
        ``symbol``, ``year``. Most recent quarter first.
        """
        data = await self._rate_limited_request(
            "/stock/earnings", {"symbol": symbol}
        )
        if isinstance(data, list):
            return data
        return []
