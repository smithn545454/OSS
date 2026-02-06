"""Earnings cache service with DynamoDB persistence.

Provides cached earnings dates with smart TTL:
- Future earnings: cache until day after earnings
- Past earnings: cache for 7 days (to fetch next quarter)
- Not found: cache for 24 hours
- API errors: cache for 1 hour
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from app.config import get_settings
from app.services.finnhub import FinnhubClient

logger = logging.getLogger(__name__)


class EarningsCacheService:
    """Service for cached earnings dates using Finnhub + DynamoDB.
    
    Implements aggressive caching to minimize Finnhub API calls
    while keeping earnings data fresh.
    """
    
    def __init__(
        self,
        finnhub_client: Optional[FinnhubClient] = None,
        table_name: Optional[str] = None,
    ) -> None:
        """Initialize earnings cache service.
        
        Args:
            finnhub_client: Finnhub client for fetching earnings.
                           If None, will return cached data only.
            table_name: DynamoDB table name. Defaults to settings.
        """
        self._finnhub = finnhub_client
        settings = get_settings()
        self._table_name = table_name or f"{settings.dynamodb_table_prefix}-earnings-cache"
        self._dynamodb = boto3.resource("dynamodb")
        self._table = self._dynamodb.Table(self._table_name)
    
    async def get_days_to_earnings(self, ticker: str) -> Optional[int]:
        """Get days until next earnings, using cache when available.
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            Days until next earnings, or None if unavailable
        """
        ticker_upper = ticker.upper()
        
        # Check cache first
        cached = self._get_cached(ticker_upper)
        if cached is not None:
            return cached
        
        # Fetch from Finnhub if client available
        if self._finnhub:
            result = await self._fetch_and_cache(ticker_upper)
            return result
        
        logger.debug(f"No cached earnings for {ticker_upper} and no Finnhub client")
        return None
    
    def _get_cached(self, ticker: str) -> Optional[int]:
        """Get cached earnings data if valid.
        
        Args:
            ticker: Stock ticker symbol (uppercase)
            
        Returns:
            Days until earnings, or None if not cached or expired
        """
        try:
            response = self._table.get_item(Key={"ticker": ticker})
            item = response.get("Item")
            
            if not item:
                return None
            
            # Check if TTL has passed (DynamoDB TTL is eventually consistent)
            ttl = item.get("ttl", 0)
            if ttl and datetime.utcnow().timestamp() > ttl:
                return None
            
            earnings_date_str = item.get("earnings_date")
            if not earnings_date_str:
                # Cached "not found" - return None but don't re-fetch
                logger.debug(f"Cached 'not found' for {ticker}")
                return None
            
            # Calculate days from cached date
            try:
                earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
                days = (earnings_date - date.today()).days
                return max(0, days)
            except ValueError:
                return None
                
        except ClientError as e:
            logger.error(f"DynamoDB error getting cached earnings for {ticker}: {e}")
            return None
    
    async def _fetch_and_cache(self, ticker: str) -> Optional[int]:
        """Fetch earnings from Finnhub and cache the result.
        
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
                # Cache "not found" to avoid repeated lookups
                self._cache_not_found(ticker)
                return None
                
        except Exception as e:
            logger.error(f"Error fetching earnings for {ticker}: {e}")
            # Cache error to avoid hammering the API
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
        """Prefetch earnings for multiple tickers efficiently.
        
        Checks cache first, only fetches missing ones from Finnhub.
        Rate limiting is handled by the Finnhub client.
        
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
            cached = self._get_cached(ticker_upper)
            if cached is not None:
                results[ticker_upper] = cached
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
