"""Earnings cache service with DynamoDB persistence.

Provides cached earnings dates with smart TTL:
- Future earnings: cache until day after earnings
- Not found: cache for 24 hours
- API errors: cache for 1 hour

The cache is populated by a daily bulk refresh (one Finnhub API call)
and read by pipeline runs without any API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from app.config import get_settings
from app.services.finnhub import FinnhubClient

logger = logging.getLogger(__name__)


@dataclass
class CacheResult:
    """Result of a cache lookup, distinguishing miss from cached-not-found."""

    hit: bool
    """True if found in cache (even if no upcoming earnings)."""

    days_to_earnings: Optional[int] = None
    """Days until earnings. None when hit=True means 'no upcoming earnings known'."""


class EarningsCacheService:
    """Service for cached earnings dates using Finnhub + DynamoDB.

    Two modes of operation:
    - With FinnhubClient: can refresh cache via bulk API call (daily refresh handler)
    - Without FinnhubClient: read-only from DynamoDB cache (pipeline runs)
    """

    def __init__(
        self,
        finnhub_client: Optional[FinnhubClient] = None,
        table_name: Optional[str] = None,
    ) -> None:
        """Initialize earnings cache service.

        Args:
            finnhub_client: Finnhub client for bulk refresh.
                           If None, operates in read-only mode.
            table_name: DynamoDB table name. Defaults to settings.
        """
        self._finnhub = finnhub_client
        settings = get_settings()
        self._table_name = table_name or f"{settings.dynamodb_table_prefix}-earnings-cache"
        self._dynamodb = boto3.resource("dynamodb")
        self._table = self._dynamodb.Table(self._table_name)

    async def get_days_to_earnings(self, ticker: str) -> Optional[int]:
        """Get days until next earnings from cache.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Days until next earnings, or None if no upcoming earnings known
        """
        ticker_upper = ticker.upper()

        result = self._get_cached(ticker_upper)
        if result.hit:
            return result.days_to_earnings

        # Cache miss — fetch per-ticker if client available (legacy fallback)
        if self._finnhub:
            return await self._fetch_and_cache(ticker_upper)

        logger.debug(f"No cached earnings for {ticker_upper} and no Finnhub client")
        return None

    def _get_cached(self, ticker: str) -> CacheResult:
        """Get cached earnings data if valid.

        Args:
            ticker: Stock ticker symbol (uppercase)

        Returns:
            CacheResult with hit=True if found (even for 'not found' entries),
            hit=False if not in cache or expired
        """
        try:
            response = self._table.get_item(Key={"ticker": ticker})
            item = response.get("Item")

            if not item:
                return CacheResult(hit=False)

            # Check if TTL has passed (DynamoDB TTL is eventually consistent)
            ttl = item.get("ttl", 0)
            if ttl and datetime.utcnow().timestamp() > ttl:
                return CacheResult(hit=False)

            earnings_date_str = item.get("earnings_date")
            if not earnings_date_str:
                # Cached "not found" — no upcoming earnings for this ticker
                logger.debug(f"Cached 'not found' for {ticker}")
                return CacheResult(hit=True, days_to_earnings=None)

            # Calculate days from cached date
            try:
                earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
                days = (earnings_date - date.today()).days
                return CacheResult(hit=True, days_to_earnings=max(0, days))
            except ValueError:
                return CacheResult(hit=False)

        except ClientError as e:
            logger.error(f"DynamoDB error getting cached earnings for {ticker}: {e}")
            return CacheResult(hit=False)

    async def refresh_all(self, lookforward_days: int = 10) -> dict[str, Any]:
        """Bulk refresh all upcoming earnings from Finnhub.

        Makes ONE Finnhub API call to get all earnings in the next
        `lookforward_days` days, then writes them all to DynamoDB.

        Args:
            lookforward_days: How many days ahead to look (default 10)

        Returns:
            Summary dict with counts

        Raises:
            RuntimeError: If no FinnhubClient is configured
        """
        if not self._finnhub:
            raise RuntimeError("refresh_all requires a FinnhubClient")

        today = date.today()
        to_date = today + timedelta(days=lookforward_days)

        logger.info(f"Bulk fetching earnings from {today} to {to_date}")

        events = await self._finnhub.get_all_upcoming_earnings(today, to_date)

        logger.info(f"Received {len(events)} earnings events from Finnhub")

        cached_count = 0
        error_count = 0

        for event in events:
            symbol = event.get("symbol")
            date_str = event.get("date")
            hour = event.get("hour", "")

            if not symbol or not date_str:
                continue

            try:
                earnings_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                time_of_day = hour if hour in ("bmo", "amc") else "unknown"
                self._cache_earnings(symbol.upper(), earnings_date, time_of_day)
                cached_count += 1
            except (ValueError, ClientError) as e:
                logger.warning(f"Error caching earnings for {symbol}: {e}")
                error_count += 1

        logger.info(
            f"Earnings refresh complete: {cached_count} cached, "
            f"{error_count} errors"
        )

        return {
            "events_received": len(events),
            "cached": cached_count,
            "errors": error_count,
            "date_range": f"{today} to {to_date}",
        }

    async def _fetch_and_cache(self, ticker: str) -> Optional[int]:
        """Fetch earnings from Finnhub per-ticker and cache the result.

        This is a legacy fallback — prefer refresh_all() for bulk updates.

        Args:
            ticker: Stock ticker symbol (uppercase)

        Returns:
            Days until earnings, or None if not found
        """
        if not self._finnhub:
            return None

        try:
            result = await self._finnhub.get_next_earnings_date(ticker)

            if result:
                earnings_date, time_of_day = result
                self._cache_earnings(ticker, earnings_date, time_of_day)
                days = (earnings_date - date.today()).days
                return max(0, days)
            else:
                self._cache_not_found(ticker)
                return None

        except Exception as e:
            logger.error(f"Error fetching earnings for {ticker}: {e}")
            self._cache_error(ticker)
            return None

    def _cache_earnings(
        self,
        ticker: str,
        earnings_date: date,
        time_of_day: str,
    ) -> None:
        """Cache an earnings date with appropriate TTL.

        Args:
            ticker: Stock ticker symbol
            earnings_date: The earnings date
            time_of_day: "bmo", "amc", or "unknown"
        """
        # TTL: day after earnings (so we re-fetch for next quarter)
        ttl_date = earnings_date + timedelta(days=1)
        ttl = int(datetime.combine(ttl_date, datetime.min.time()).timestamp())

        try:
            self._table.put_item(Item={
                "ticker": ticker,
                "earnings_date": earnings_date.isoformat(),
                "earnings_time": time_of_day,
                "fetched_at": datetime.utcnow().isoformat(),
                "ttl": ttl,
            })
            logger.debug(f"Cached earnings for {ticker}: {earnings_date} ({time_of_day})")
        except ClientError as e:
            logger.error(f"Failed to cache earnings for {ticker}: {e}")

    def _cache_not_found(self, ticker: str) -> None:
        """Cache a 'not found' result to avoid repeated lookups.

        TTL: 24 hours
        """
        ttl = int((datetime.utcnow() + timedelta(hours=24)).timestamp())

        try:
            self._table.put_item(Item={
                "ticker": ticker,
                "earnings_date": None,
                "earnings_time": None,
                "fetched_at": datetime.utcnow().isoformat(),
                "not_found": True,
                "ttl": ttl,
            })
            logger.debug(f"Cached 'not found' for {ticker}")
        except ClientError as e:
            logger.error(f"Failed to cache 'not found' for {ticker}: {e}")

    def _cache_error(self, ticker: str) -> None:
        """Cache an error result to avoid hammering the API.

        TTL: 1 hour
        """
        ttl = int((datetime.utcnow() + timedelta(hours=1)).timestamp())

        try:
            self._table.put_item(Item={
                "ticker": ticker,
                "earnings_date": None,
                "earnings_time": None,
                "fetched_at": datetime.utcnow().isoformat(),
                "error": True,
                "ttl": ttl,
            })
            logger.debug(f"Cached error for {ticker}")
        except ClientError as e:
            logger.error(f"Failed to cache error for {ticker}: {e}")

    async def prefetch_batch(self, tickers: list[str]) -> dict[str, Optional[int]]:
        """Prefetch earnings for multiple tickers from cache.

        Checks cache first, only fetches missing ones from Finnhub
        if a client is available.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dict mapping ticker to days until earnings (or None)
        """
        results: dict[str, Optional[int]] = {}
        to_fetch: list[str] = []

        # Check cache for all tickers
        for ticker in tickers:
            ticker_upper = ticker.upper()
            result = self._get_cached(ticker_upper)
            if result.hit:
                results[ticker_upper] = result.days_to_earnings
            else:
                to_fetch.append(ticker_upper)

        logger.info(
            f"Earnings prefetch: {len(results)} cached, {len(to_fetch)} to fetch"
        )

        # Fetch missing ones (rate limiting handled by Finnhub client)
        if self._finnhub and to_fetch:
            for ticker in to_fetch:
                days = await self._fetch_and_cache(ticker)
                results[ticker] = days

        return results

    def invalidate(self, ticker: str) -> None:
        """Invalidate cached earnings for a ticker.

        Useful when you know earnings have been announced.

        Args:
            ticker: Stock ticker symbol
        """
        try:
            self._table.delete_item(Key={"ticker": ticker.upper()})
            logger.debug(f"Invalidated cache for {ticker}")
        except ClientError as e:
            logger.error(f"Failed to invalidate cache for {ticker}: {e}")
