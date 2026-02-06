"""Catalyst data service for earnings and SEC filing data.

Provides data sources for Category F features:
- Finnhub for accurate earnings calendar data (via EarningsCacheService)
- SEC EDGAR API for 8-K/10-Q/10-K filing detection
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.services.earnings_cache import EarningsCacheService

logger = logging.getLogger(__name__)

# SEC EDGAR endpoints
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# SEC requires a descriptive User-Agent
SEC_USER_AGENT = "OSS-Scanner/1.0 (options-opportunity-scanner)"

# Filing types to check for recent_sec_filing
RELEVANT_FILING_TYPES = {"8-K", "10-Q", "10-K", "8-K/A", "10-Q/A", "10-K/A"}

# 10 trading days ≈ 14 calendar days
TRADING_DAYS_LOOKBACK = 10
CALENDAR_DAYS_LOOKBACK = 14


class CatalystDataService:
    """Service for fetching earnings dates and SEC filing data.
    
    Uses Finnhub for earnings calendar (via EarningsCacheService) and 
    SEC EDGAR for filing history.
    """
    
    def __init__(self, earnings_cache: Optional["EarningsCacheService"] = None) -> None:
        """Initialize the catalyst data service.
        
        Args:
            earnings_cache: Optional EarningsCacheService for earnings data.
                           If not provided, earnings lookup will return None.
        """
        # Cache for SEC filings (in-memory, per-request)
        self._filings_cache: dict[str, bool] = {}  # ticker -> recent_filing
        
        # CIK mapping cache (ticker -> CIK string)
        self._cik_cache: dict[str, str] = {}
        self._cik_loaded = False
        
        # HTTP client for SEC requests
        self._client: Optional[httpx.AsyncClient] = None
        
        # Earnings cache service (Finnhub + DynamoDB)
        self._earnings_cache = earnings_cache
    
    async def __aenter__(self) -> "CatalystDataService":
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": SEC_USER_AGENT},
        )
        return self
    
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()
    
    async def get_days_to_earnings(self, ticker: str) -> Optional[int]:
        """Get days until next earnings announcement.
        
        Uses Finnhub via EarningsCacheService for accurate earnings dates.
        
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL")
            
        Returns:
            Days until next earnings, or None if unavailable
        """
        if not self._earnings_cache:
            logger.debug("No earnings cache configured, earnings data unavailable")
            return None
        
        try:
            return await self._earnings_cache.get_days_to_earnings(ticker)
        except Exception as e:
            logger.debug(f"Error fetching earnings for {ticker}: {e}")
            return None
    
    async def get_recent_sec_filing(self, ticker: str) -> bool:
        """Check if there was an 8-K, 10-Q, or 10-K filing in last 10 trading days.
        
        Uses SEC EDGAR API to fetch filing history.
        
        Args:
            ticker: Stock ticker symbol (e.g., "AAPL")
            
        Returns:
            True if a relevant filing was found, False otherwise
        """
        ticker_upper = ticker.upper()
        
        # Check cache first
        if ticker_upper in self._filings_cache:
            return self._filings_cache[ticker_upper]
        
        has_filing = await self._check_recent_filing(ticker_upper)
        self._filings_cache[ticker_upper] = has_filing
        return has_filing
    
    async def _check_recent_filing(self, ticker: str) -> bool:
        """Check SEC EDGAR for recent filings.
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            True if a relevant filing was found in the lookback period
        """
        try:
            # Load CIK mapping if not already loaded
            if not self._cik_loaded:
                await self._load_cik_mapping()
            
            # Look up CIK for ticker
            cik = self._cik_cache.get(ticker)
            if not cik:
                logger.debug(f"No CIK found for {ticker}")
                return False
            
            # Fetch submissions from SEC
            filings = await self._fetch_sec_submissions(cik)
            if not filings:
                return False
            
            # Check for relevant filings in lookback period
            cutoff_date = datetime.now().date() - timedelta(days=CALENDAR_DAYS_LOOKBACK)
            
            for filing in filings:
                form_type = filing.get("form", "")
                filing_date_str = filing.get("filingDate", "")
                
                if form_type not in RELEVANT_FILING_TYPES:
                    continue
                
                try:
                    filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d").date()
                    if filing_date >= cutoff_date:
                        logger.debug(f"Found recent {form_type} for {ticker} on {filing_date}")
                        return True
                except ValueError:
                    continue
            
            return False
            
        except Exception as e:
            logger.debug(f"Error checking SEC filings for {ticker}: {e}")
            return False
    
    async def _load_cik_mapping(self) -> None:
        """Load ticker to CIK mapping from SEC."""
        if self._cik_loaded:
            return
        
        try:
            if not self._client:
                self._client = httpx.AsyncClient(
                    timeout=30.0,
                    headers={"User-Agent": SEC_USER_AGENT},
                )
            
            response = await self._client.get(SEC_COMPANY_TICKERS_URL)
            response.raise_for_status()
            
            data = response.json()
            
            # Format: {"0": {"cik_str": "320193", "ticker": "AAPL", "title": "..."}, ...}
            for entry in data.values():
                ticker = entry.get("ticker", "").upper()
                cik_str = str(entry.get("cik_str", ""))
                if ticker and cik_str:
                    # CIK needs to be zero-padded to 10 digits
                    self._cik_cache[ticker] = cik_str.zfill(10)
            
            self._cik_loaded = True
            logger.info(f"Loaded CIK mapping for {len(self._cik_cache)} tickers")
            
        except Exception as e:
            logger.warning(f"Failed to load CIK mapping: {e}")
            self._cik_loaded = True  # Mark as loaded to avoid retrying
    
    async def _fetch_sec_submissions(self, cik: str) -> list[dict]:
        """Fetch recent submissions for a company from SEC EDGAR.
        
        Args:
            cik: 10-digit CIK number (zero-padded)
            
        Returns:
            List of filing dictionaries with 'form' and 'filingDate' keys
        """
        try:
            if not self._client:
                self._client = httpx.AsyncClient(
                    timeout=30.0,
                    headers={"User-Agent": SEC_USER_AGENT},
                )
            
            url = SEC_SUBMISSIONS_URL.format(cik=cik)
            response = await self._client.get(url)
            response.raise_for_status()
            
            data = response.json()
            
            # Recent filings are in data["filings"]["recent"]
            filings_data = data.get("filings", {}).get("recent", {})
            
            forms = filings_data.get("form", [])
            dates = filings_data.get("filingDate", [])
            
            # Combine into list of dicts
            filings = []
            for form, date in zip(forms, dates):
                filings.append({"form": form, "filingDate": date})
            
            return filings
            
        except Exception as e:
            logger.debug(f"Error fetching SEC submissions for CIK {cik}: {e}")
            return []
    
    async def prefetch_batch(self, tickers: list[str]) -> None:
        """Prefetch catalyst data for multiple tickers efficiently.
        
        Loads all data into cache for fast subsequent lookups.
        
        Args:
            tickers: List of ticker symbols to prefetch
        """
        if not tickers:
            return
        
        logger.info(f"Prefetching catalyst data for {len(tickers)} tickers")
        
        # Load CIK mapping first
        await self._load_cik_mapping()
        
        # Prefetch earnings using the cache service (handles its own rate limiting)
        if self._earnings_cache:
            await self._earnings_cache.prefetch_batch(tickers)
        
        # Fetch filings in parallel with rate limiting for SEC
        async def fetch_ticker_data(ticker: str) -> None:
            """Fetch SEC filing data for a single ticker."""
            ticker_upper = ticker.upper()
            
            # Small delay to respect SEC rate limits
            await asyncio.sleep(0.1)
            
            # Fetch filings (SEC EDGAR)
            if ticker_upper not in self._filings_cache:
                has_filing = await self._check_recent_filing(ticker_upper)
                self._filings_cache[ticker_upper] = has_filing
        
        # Process tickers with limited concurrency
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent requests
        
        async def limited_fetch(ticker: str) -> None:
            async with semaphore:
                await fetch_ticker_data(ticker)
        
        # Run all fetches
        tasks = [limited_fetch(t) for t in tickers]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info(
            f"Prefetched catalyst data: "
            f"{len(self._filings_cache)} filings cached"
        )
    
    def clear_cache(self) -> None:
        """Clear in-memory cached data (filings only).
        
        Note: Earnings are cached in DynamoDB via EarningsCacheService.
        """
        self._filings_cache.clear()
        # Don't clear CIK cache - it's static data
