"""Watchlist management for scanner tickers.

Provides utilities for managing and validating the ticker watchlist
used by the scanner system.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.schemas import PolicyConfig, TickerUniverse, WatchlistConfig

logger = logging.getLogger(__name__)

# Default watchlist of high-liquidity optionable tickers
# This can be overridden in the policy configuration
DEFAULT_WATCHLIST: list[str] = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA",
    # Semiconductors
    "AMD", "INTC", "AVGO", "QCOM", "MU", "AMAT", "LRCX", "KLAC",
    # Software/Cloud
    "CRM", "ADBE", "NOW", "SNOW", "PANW", "CRWD", "ZS", "DDOG",
    # Financials
    "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW",
    # Healthcare/Pharma
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY", "BMY", "AMGN",
    # Consumer
    "DIS", "NFLX", "SBUX", "NKE", "MCD", "HD", "LOW", "TGT",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "PSX", "VLO",
    # Industrials
    "BA", "CAT", "GE", "HON", "UPS", "LMT", "RTX", "DE",
    # ETFs (high options volume)
    "SPY", "QQQ", "IWM", "EEM", "XLF", "XLE", "XLK", "GLD",
    # Popular retail/meme stocks
    "GME", "AMC", "PLTR", "SOFI", "RIVN", "LCID",
]


class WatchlistManager:
    """Manager for scanner watchlist operations."""

    def __init__(
        self,
        config: Optional[WatchlistConfig] = None,
        sp500_tickers: Optional[list[str]] = None,
    ) -> None:
        """Initialize watchlist manager.

        Args:
            config: Optional watchlist configuration. If not provided,
                   uses defaults.
            sp500_tickers: Optional S&P 500 tickers loaded from DynamoDB.
        """
        self._config = config or WatchlistConfig()
        self._sp500_tickers = sp500_tickers

    @property
    def tickers(self) -> list[str]:
        """Get the list of tickers to scan.

        Resolution order:
        1. Policy override (config.tickers) — "config over code"
        2. S&P 500 from DynamoDB — loaded via from_policy_async()
        3. DEFAULT_WATCHLIST — hardcoded 78-ticker fallback
        """
        if self._config.tickers:
            return self._config.tickers
        if self._sp500_tickers:
            return self._sp500_tickers
        return DEFAULT_WATCHLIST

    @property
    def batch_size(self) -> int:
        """Get the batch size for processing."""
        return self._config.batch_size

    @property
    def max_concurrent(self) -> int:
        """Get the maximum concurrent requests."""
        return self._config.max_concurrent_requests

    def get_batches(self) -> list[list[str]]:
        """Split tickers into batches for processing.

        Returns:
            List of ticker batches
        """
        tickers = self.tickers
        batch_size = self.batch_size
        return [
            tickers[i : i + batch_size]
            for i in range(0, len(tickers), batch_size)
        ]

    def validate_tickers(self) -> tuple[list[str], list[str]]:
        """Validate ticker symbols.

        Returns:
            Tuple of (valid_tickers, invalid_tickers)
        """
        valid: list[str] = []
        invalid: list[str] = []

        for ticker in self.tickers:
            # Basic validation: alphanumeric, 1-5 chars
            if (
                ticker
                and ticker.isalpha()
                and 1 <= len(ticker) <= 5
                and ticker.isupper()
            ):
                valid.append(ticker)
            else:
                invalid.append(ticker)
                logger.warning(f"Invalid ticker symbol: {ticker}")

        return valid, invalid

    @classmethod
    def from_policy(cls, policy_config: PolicyConfig) -> "WatchlistManager":
        """Create manager from policy configuration.

        Args:
            policy_config: The policy configuration

        Returns:
            WatchlistManager instance
        """
        return cls(config=policy_config.watchlist)

    @classmethod
    async def from_policy_async(cls, policy_config: PolicyConfig) -> "WatchlistManager":
        """Create manager from policy configuration, loading tickers from DynamoDB.

        Loads tickers based on the configured universe tier (sp500, russell1000).
        Falls back to DEFAULT_WATCHLIST if DynamoDB read fails.

        Args:
            policy_config: The policy configuration

        Returns:
            WatchlistManager instance with universe tickers if available
        """
        from app.db.tables import SP500TickerTable

        universe = policy_config.watchlist.universe
        db_tickers: Optional[list[str]] = None
        try:
            if universe == TickerUniverse.SP500:
                db_tickers = await SP500TickerTable.get_active_tickers()
            else:
                db_tickers = await SP500TickerTable.get_tickers_by_universe(universe.value)
            if not db_tickers:
                db_tickers = None
            else:
                logger.info(
                    f"Loaded {len(db_tickers)} tickers for universe={universe.value} from DynamoDB"
                )
        except Exception as e:
            logger.warning(f"Failed to load {universe.value} tickers, using fallback: {e}")
        return cls(config=policy_config.watchlist, sp500_tickers=db_tickers)


def get_default_watchlist() -> list[str]:
    """Get the default watchlist.

    Returns:
        List of default ticker symbols
    """
    return DEFAULT_WATCHLIST.copy()


def merge_watchlists(*watchlists: list[str]) -> list[str]:
    """Merge multiple watchlists, removing duplicates.

    Args:
        *watchlists: Variable number of ticker lists

    Returns:
        Merged list of unique tickers
    """
    seen: set[str] = set()
    result: list[str] = []

    for watchlist in watchlists:
        for ticker in watchlist:
            upper_ticker = ticker.upper()
            if upper_ticker not in seen:
                seen.add(upper_ticker)
                result.append(upper_ticker)

    return result
