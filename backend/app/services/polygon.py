"""Polygon.io client for market data.

Provides methods for fetching underlying and options data with support
for batch operations and concurrency control.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.polygon.io"


@dataclass
class AggregatedOptionsVolume:
    """Aggregated options volume data for a ticker."""

    ticker: str
    total_call_volume: int
    total_put_volume: int
    total_call_oi: int
    total_put_oi: int
    call_put_volume_ratio: float
    timestamp: str


@dataclass
class DailyBar:
    """Daily OHLCV bar data."""

    ticker: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None


class PolygonClient:
    """Client for Polygon.io API with batch support."""

    def __init__(self, max_concurrent: int = 20) -> None:
        """Initialize Polygon client.

        Args:
            max_concurrent: Maximum concurrent API requests (default 20 for optimal throughput)
        """
        settings = get_settings()
        self._api_key = settings.polygon_api_key
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def __aenter__(self) -> "PolygonClient":
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=30.0,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()

    async def _rate_limited_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Optional[httpx.Response]:
        """Make a rate-limited request with retry for transient errors.

        Retries up to 2 times on 429 (rate limit) and 5xx (server error)
        with backoff (1s, 2s). Non-transient errors (4xx) return None
        immediately.

        Args:
            method: HTTP method
            url: Request URL
            **kwargs: Additional request arguments

        Returns:
            Response or None on error
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")

        max_retries = 2
        retryable_statuses = {429, 500, 502, 503, 504}

        for attempt in range(max_retries + 1):
            async with self._semaphore:
                try:
                    response = await self._client.request(method, url, **kwargs)
                    response.raise_for_status()
                    return response
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    if status in retryable_statuses and attempt < max_retries:
                        backoff = 2**attempt  # 1s, 2s
                        logger.warning(
                            f"HTTP {status} for {url} (attempt {attempt + 1}/{max_retries + 1}), "
                            f"retrying in {backoff}s"
                        )
                        await asyncio.sleep(backoff)
                        continue
                    logger.error(f"HTTP {status} for {url}: {e}")
                    return None
                except httpx.HTTPError as e:
                    if attempt < max_retries:
                        backoff = 2**attempt
                        logger.warning(
                            f"HTTP error for {url} (attempt {attempt + 1}/{max_retries + 1}), "
                            f"retrying in {backoff}s: {e}"
                        )
                        await asyncio.sleep(backoff)
                        continue
                    logger.error(f"HTTP error for {url}: {e}")
                    return None
        return None

    async def get_ticker_details(self, ticker: str) -> Optional[dict[str, Any]]:
        """Get ticker details.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Ticker details or None if not found
        """
        response = await self._rate_limited_request(
            "GET", f"/v3/reference/tickers/{ticker}"
        )
        if response:
            data = response.json()
            return data.get("results")
        return None

    async def get_daily_bars(
        self,
        ticker: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Get daily OHLCV bars for a ticker.

        Args:
            ticker: Stock ticker symbol
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            List of daily bar data
        """
        response = await self._rate_limited_request(
            "GET",
            f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
            params={"adjusted": "true", "sort": "asc", "limit": 5000},
        )
        if response:
            data = response.json()
            return data.get("results", [])
        return []

    async def get_daily_bars_parsed(
        self,
        ticker: str,
        from_date: str,
        to_date: str,
    ) -> list[DailyBar]:
        """Get daily OHLCV bars as DailyBar objects.

        Args:
            ticker: Stock ticker symbol
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            List of DailyBar objects
        """
        raw_bars = await self.get_daily_bars(ticker, from_date, to_date)
        bars = []
        for bar in raw_bars:
            # Convert timestamp (ms) to date string
            ts = bar.get("t", 0)
            date_str = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            bars.append(
                DailyBar(
                    ticker=ticker,
                    date=date_str,
                    open=bar.get("o", 0.0),
                    high=bar.get("h", 0.0),
                    low=bar.get("l", 0.0),
                    close=bar.get("c", 0.0),
                    volume=bar.get("v", 0),
                    vwap=bar.get("vw"),
                )
            )
        return bars

    async def get_grouped_daily_bars(
        self,
        date: str,
        adjusted: bool = True,
    ) -> dict[str, DailyBar]:
        """Get daily bars for ALL US stocks on a single date using grouped endpoint.
        
        This is MUCH more efficient than per-ticker calls when scanning many tickers.
        ONE API call returns ~10,000+ tickers' daily bars.

        Args:
            date: Date to fetch (YYYY-MM-DD)
            adjusted: Whether to adjust for splits

        Returns:
            Dict mapping ticker to DailyBar for that date
        """
        response = await self._rate_limited_request(
            "GET",
            f"/v2/aggs/grouped/locale/us/market/stocks/{date}",
            params={"adjusted": str(adjusted).lower()},
        )
        
        output: dict[str, DailyBar] = {}
        if response:
            data = response.json()
            for bar in data.get("results", []):
                ticker = bar.get("T", "")
                if ticker:
                    output[ticker] = DailyBar(
                        ticker=ticker,
                        date=date,
                        open=bar.get("o", 0.0),
                        high=bar.get("h", 0.0),
                        low=bar.get("l", 0.0),
                        close=bar.get("c", 0.0),
                        volume=bar.get("v", 0),
                        vwap=bar.get("vw"),
                    )
        return output

    async def get_daily_bars_batch(
        self,
        tickers: list[str],
        days: int = 60,
        use_grouped_api: bool = True,
    ) -> dict[str, list[DailyBar]]:
        """Fetch daily bars for multiple tickers.
        
        Uses grouped API by default for efficiency (1 API call per day vs N per ticker).

        Args:
            tickers: List of ticker symbols
            days: Number of days of history to fetch
            use_grouped_api: If True, uses grouped daily endpoint (1 call/day).
                            If False, uses per-ticker endpoint (1 call/ticker).

        Returns:
            Dict mapping ticker to list of DailyBar objects
        """
        ticker_set = set(tickers)
        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        if use_grouped_api and len(tickers) > 20:
            # Use grouped API: fetch each day's data in one call
            # This is O(days) API calls instead of O(tickers) calls
            logger.info(f"Using grouped API to fetch {days} days for {len(tickers)} tickers")
            
            output: dict[str, list[DailyBar]] = {ticker: [] for ticker in tickers}
            
            # Generate list of trading days (approximate - exclude weekends)
            current = datetime.now()
            dates_to_fetch: list[str] = []
            days_fetched = 0
            while days_fetched < days:
                date_str = current.strftime("%Y-%m-%d")
                # Skip weekends
                if current.weekday() < 5:  # Mon-Fri
                    dates_to_fetch.append(date_str)
                    days_fetched += 1
                current -= timedelta(days=1)
            
            # Fetch each day's data (in parallel for speed)
            async def fetch_day(date: str) -> dict[str, DailyBar]:
                return await self.get_grouped_daily_bars(date)
            
            day_tasks = [fetch_day(date) for date in dates_to_fetch]
            day_results = await asyncio.gather(*day_tasks, return_exceptions=True)
            
            # Assemble bars per ticker
            for day_data in day_results:
                if isinstance(day_data, dict):
                    for ticker in tickers:
                        if ticker in day_data:
                            output[ticker].append(day_data[ticker])
                elif isinstance(day_data, Exception):
                    logger.error(f"Error fetching grouped daily: {day_data}")
            
            # Sort each ticker's bars by date ascending
            for ticker in output:
                output[ticker].sort(key=lambda b: b.date)
            
            return output
        else:
            # Fall back to per-ticker API for small ticker counts
            async def fetch_one(ticker: str) -> tuple[str, list[DailyBar]]:
                bars = await self.get_daily_bars_parsed(ticker, from_date, to_date)
                return ticker, bars

            tasks = [fetch_one(ticker) for ticker in tickers]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            output: dict[str, list[DailyBar]] = {}
            for result in results:
                if isinstance(result, tuple):
                    ticker, bars = result
                    output[ticker] = bars
                elif isinstance(result, Exception):
                    logger.error(f"Error in batch fetch: {result}")

            return output

    async def get_options_chain(
        self,
        underlying_ticker: str,
        expiration_date_gte: Optional[str] = None,
        expiration_date_lte: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get options chain snapshot for an underlying.

        Args:
            underlying_ticker: Underlying stock ticker
            expiration_date_gte: Minimum expiration date (YYYY-MM-DD)
            expiration_date_lte: Maximum expiration date (YYYY-MM-DD)

        Returns:
            List of option contract snapshots
        """
        logger.info(f"[PAGINATED] Starting full chain fetch for {underlying_ticker}")
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")

        params: dict[str, Any] = {"limit": 250}
        if expiration_date_gte:
            params["expiration_date.gte"] = expiration_date_gte
        if expiration_date_lte:
            params["expiration_date.lte"] = expiration_date_lte

        all_results: list[dict[str, Any]] = []
        next_url: Optional[str] = f"/v3/snapshot/options/{underlying_ticker}"

        while next_url:
            async with self._semaphore:
                try:
                    response = await self._client.get(next_url, params=params)
                    response.raise_for_status()
                    data = response.json()

                    results = data.get("results", [])
                    all_results.extend(results)

                    # Handle pagination
                    next_url = data.get("next_url")
                    params = {}  # Clear params for subsequent requests
                except httpx.HTTPError as e:
                    logger.error(
                        f"Error fetching options chain for {underlying_ticker}: {e}"
                    )
                    break

        return all_results

    async def get_options_chain_minimal(
        self,
        underlying_ticker: str,
        expiration_date_gte: Optional[str] = None,
        expiration_date_lte: Optional[str] = None,
        strike_price_gte: Optional[float] = None,
        strike_price_lte: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Fetch options chain WITHOUT pagination (first page only).

        Used for scanning where we only need aggregates or ATM contracts.
        Returns up to 1000 contracts in a single API call - no pagination.

        This is dramatically faster than get_options_chain() which paginates
        through all contracts (potentially 20+ API calls per ticker).

        Args:
            underlying_ticker: Underlying stock ticker
            expiration_date_gte: Minimum expiration date (YYYY-MM-DD)
            expiration_date_lte: Maximum expiration date (YYYY-MM-DD)
            strike_price_gte: Minimum strike price (for ATM filtering)
            strike_price_lte: Maximum strike price (for ATM filtering)

        Returns:
            List of option contract snapshots (up to 1000)
        """
        logger.info(f"[MINIMAL] Single-page fetch for {underlying_ticker}")
        params: dict[str, Any] = {"limit": 250}
        if expiration_date_gte:
            params["expiration_date.gte"] = expiration_date_gte
        if expiration_date_lte:
            params["expiration_date.lte"] = expiration_date_lte
        if strike_price_gte is not None:
            params["strike_price.gte"] = strike_price_gte
        if strike_price_lte is not None:
            params["strike_price.lte"] = strike_price_lte

        response = await self._rate_limited_request(
            "GET",
            f"/v3/snapshot/options/{underlying_ticker}",
            params=params,
        )

        if response:
            data = response.json()
            results = data.get("results", [])
            logger.info(
                f"[MINIMAL] {underlying_ticker}: status={response.status_code}, "
                f"results={len(results)} contracts, params={params}"
            )
            return results

        logger.warning(
            f"[MINIMAL] {underlying_ticker}: response=None (HTTP error swallowed), "
            f"params={params}"
        )
        return []

    async def get_options_chain_batch(
        self,
        tickers: list[str],
        expiration_date_gte: Optional[str] = None,
        expiration_date_lte: Optional[str] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch options chains for multiple tickers concurrently.

        Args:
            tickers: List of underlying ticker symbols
            expiration_date_gte: Minimum expiration date
            expiration_date_lte: Maximum expiration date

        Returns:
            Dict mapping ticker to list of option contract snapshots
        """

        async def fetch_one(
            ticker: str,
        ) -> tuple[str, list[dict[str, Any]]]:
            chain = await self.get_options_chain(
                ticker, expiration_date_gte, expiration_date_lte
            )
            return ticker, chain

        tasks = [fetch_one(ticker) for ticker in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            if isinstance(result, tuple):
                ticker, chain = result
                output[ticker] = chain
            elif isinstance(result, Exception):
                logger.error(f"Error in batch chain fetch: {result}")

        return output

    async def get_aggregated_options_volume(
        self,
        underlying_ticker: str,
        max_dte: int = 60,
    ) -> Optional[AggregatedOptionsVolume]:
        """Get aggregated options volume and OI for a ticker.

        Aggregates call/put volume and open interest from the options chain snapshot.
        Filters to near-term expirations (default 60 DTE) to reduce API calls.

        Args:
            underlying_ticker: Underlying stock ticker
            max_dte: Maximum days to expiration to include (default 60)

        Returns:
            AggregatedOptionsVolume or None if no data
        """
        # Filter to near-term expirations to reduce data volume
        # Most options volume is in 0-60 DTE range anyway
        from datetime import datetime, timedelta
        max_exp_date = (datetime.now() + timedelta(days=max_dte)).strftime("%Y-%m-%d")
        
        chain = await self.get_options_chain(
            underlying_ticker,
            expiration_date_lte=max_exp_date,
        )
        if not chain:
            return None

        total_call_volume = 0
        total_put_volume = 0
        total_call_oi = 0
        total_put_oi = 0

        for contract in chain:
            details = contract.get("details", {})
            day = contract.get("day", {})

            contract_type = details.get("contract_type", "").upper()
            volume = day.get("volume", 0) or 0
            oi = contract.get("open_interest", 0) or 0

            if contract_type == "CALL":
                total_call_volume += volume
                total_call_oi += oi
            elif contract_type == "PUT":
                total_put_volume += volume
                total_put_oi += oi

        # Calculate call/put ratio (avoid division by zero)
        if total_put_volume > 0:
            ratio = total_call_volume / total_put_volume
        else:
            ratio = float("inf") if total_call_volume > 0 else 0.0

        return AggregatedOptionsVolume(
            ticker=underlying_ticker,
            total_call_volume=total_call_volume,
            total_put_volume=total_put_volume,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            call_put_volume_ratio=ratio,
            timestamp=datetime.utcnow().isoformat(),
        )

    async def get_aggregated_options_volume_batch(
        self,
        tickers: list[str],
        max_dte: int = 60,
    ) -> dict[str, AggregatedOptionsVolume]:
        """Fetch aggregated options volume for multiple tickers.

        Args:
            tickers: List of underlying ticker symbols
            max_dte: Maximum days to expiration to include (default 60)

        Returns:
            Dict mapping ticker to AggregatedOptionsVolume
        """

        async def fetch_one(
            ticker: str,
        ) -> tuple[str, Optional[AggregatedOptionsVolume]]:
            vol = await self.get_aggregated_options_volume(ticker, max_dte=max_dte)
            return ticker, vol

        tasks = [fetch_one(ticker) for ticker in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, AggregatedOptionsVolume] = {}
        for result in results:
            if isinstance(result, tuple):
                ticker, vol = result
                if vol:
                    output[ticker] = vol
            elif isinstance(result, Exception):
                logger.error(f"Error in batch volume fetch: {result}")

        return output

    async def get_previous_close(self, ticker: str) -> Optional[dict[str, Any]]:
        """Get previous day's close for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Previous close data or None
        """
        response = await self._rate_limited_request(
            "GET", f"/v2/aggs/ticker/{ticker}/prev"
        )
        if response:
            data = response.json()
            results = data.get("results", [])
            return results[0] if results else None
        return None

    async def get_previous_close_batch(
        self,
        tickers: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Get previous close for multiple tickers.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dict mapping ticker to previous close data
        """

        async def fetch_one(ticker: str) -> tuple[str, Optional[dict[str, Any]]]:
            data = await self.get_previous_close(ticker)
            return ticker, data

        tasks = [fetch_one(ticker) for ticker in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, dict[str, Any]] = {}
        for result in results:
            if isinstance(result, tuple):
                ticker, data = result
                if data:
                    output[ticker] = data
            elif isinstance(result, Exception):
                logger.error(f"Error in batch prev close fetch: {result}")

        return output

    async def get_ticker_events(
        self,
        ticker: str,
        event_types: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Get corporate events for a ticker including earnings dates.

        Uses Polygon's Stock Financials API to find earnings/report dates.

        Args:
            ticker: Stock ticker symbol
            event_types: Optional filter for event types

        Returns:
            List of event dictionaries with dates
        """
        # Try the reference financials endpoint for earnings dates
        response = await self._rate_limited_request(
            "GET",
            f"/vX/reference/financials",
            params={
                "ticker": ticker,
                "limit": 4,  # Last 4 quarters
                "sort": "filing_date",
                "order": "desc",
            },
        )
        if response:
            data = response.json()
            return data.get("results", [])
        return []

    async def get_next_earnings_date(self, ticker: str) -> Optional[datetime]:
        """Get the next expected earnings date for a ticker.

        Estimates next earnings based on historical filing patterns.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Estimated next earnings date, or None if unavailable
        """
        try:
            financials = await self.get_ticker_events(ticker)
            if not financials or len(financials) < 2:
                return None

            # Get the most recent filing dates to estimate next earnings
            filing_dates = []
            for item in financials:
                filing_date_str = item.get("filing_date")
                if filing_date_str:
                    try:
                        filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d")
                        filing_dates.append(filing_date)
                    except ValueError:
                        continue

            if len(filing_dates) < 2:
                return None

            # Sort descending (most recent first)
            filing_dates.sort(reverse=True)

            # Estimate interval between filings (typically ~90 days for quarterly)
            most_recent = filing_dates[0]
            second_recent = filing_dates[1]
            interval = most_recent - second_recent

            # Estimate next earnings date
            estimated_next = most_recent + interval

            # Only return if it's in the future
            if estimated_next > datetime.now():
                return estimated_next

            # If estimate is in the past, add another interval
            while estimated_next <= datetime.now():
                estimated_next += interval

            return estimated_next

        except Exception as e:
            logger.debug(f"Error estimating earnings date for {ticker}: {e}")
            return None

    async def get_days_to_earnings(self, ticker: str) -> Optional[int]:
        """Get days until next estimated earnings announcement.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Days until next earnings, or None if unavailable
        """
        next_earnings = await self.get_next_earnings_date(ticker)
        if next_earnings:
            days = (next_earnings.date() - datetime.now().date()).days
            return max(0, days)
        return None
