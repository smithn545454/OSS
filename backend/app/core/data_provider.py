"""DataProvider Protocol — abstracts data sources for pipeline stages.

The pipeline stages call DataProvider methods instead of directly using
PolygonClient / DynamoDB tables. Two implementations exist:

- LiveDataProvider  : wraps Polygon, DynamoDB (live trading)
- HistoricalDataProvider : reads from S3 parquet files (backtesting)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Protocol, runtime_checkable

# ============================================================================
# Data types returned by the provider
# ============================================================================

@dataclass
class DailyBar:
    """Single day OHLCV bar."""

    ticker: str
    date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None


@dataclass
class StockSnapshot:
    """End-of-day stock snapshot (previous close)."""

    ticker: str
    date: str  # YYYY-MM-DD
    close: float
    volume: int
    vwap: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None


@dataclass
class AggregatedOptionsVolume:
    """Aggregated options volume for an underlying."""

    ticker: str
    total_call_volume: int = 0
    total_put_volume: int = 0
    total_call_oi: int = 0
    total_put_oi: int = 0
    call_put_volume_ratio: float = 0.0
    timestamp: str = ""


@dataclass
class IVHistoryRecord:
    """Single IV history data point."""

    ticker: str
    date: str  # YYYY-MM-DD
    atm_iv: float
    atm_call_iv: Optional[float] = None
    atm_put_iv: Optional[float] = None
    rv20: Optional[float] = None
    iv_rv_ratio: Optional[float] = None


@dataclass
class OIHistoryRecord:
    """Single open interest history data point."""

    option_ticker: str
    date: str  # YYYY-MM-DD
    open_interest: int
    volume: Optional[int] = None


@dataclass
class MarketContextData:
    """Daily market context (SPY + VIX)."""

    date: str  # YYYY-MM-DD
    spy_close: float = 0.0
    spy_change_pct: float = 0.0
    vix_close: float = 0.0


# ============================================================================
# DataProvider Protocol
# ============================================================================

@runtime_checkable
class DataProvider(Protocol):
    """Unified data access protocol for pipeline stages.

    All methods are async and accept ``as_of``/``end_date`` parameters to
    support both live (today) and historical (arbitrary date) usage.
    """

    # ------------------------------------------------------------------
    # Stock data
    # ------------------------------------------------------------------

    async def get_daily_bars(
        self,
        ticker: str,
        end_date: date,
        lookback_days: int = 60,
    ) -> list[DailyBar]:
        """Daily OHLCV bars for ``[end_date - lookback_days, end_date)``."""
        ...

    async def get_daily_bars_batch(
        self,
        tickers: list[str],
        end_date: date,
        lookback_days: int = 60,
    ) -> dict[str, list[DailyBar]]:
        """Daily bars for multiple tickers."""
        ...

    async def get_stock_snapshot(
        self,
        ticker: str,
        as_of: date,
    ) -> Optional[StockSnapshot]:
        """End-of-day snapshot (previous close) for a ticker."""
        ...

    async def get_stock_snapshots_batch(
        self,
        tickers: list[str],
        as_of: date,
    ) -> dict[str, StockSnapshot]:
        """Batch stock snapshots."""
        ...

    # ------------------------------------------------------------------
    # Options data
    # ------------------------------------------------------------------

    async def get_options_chain(
        self,
        ticker: str,
        as_of: date,
        min_dte: int = 7,
        max_dte: int = 120,
    ) -> list[dict[str, Any]]:
        """Full options chain for a ticker (all strikes, all expirations)."""
        ...

    async def get_options_chain_minimal(
        self,
        ticker: str,
        as_of: date,
        min_dte: int = 7,
        max_dte: int = 90,
        strike_range_pct: float = 0.10,
    ) -> list[dict[str, Any]]:
        """Minimal options chain (filtered by strike range and DTE)."""
        ...

    async def get_aggregated_options_volume(
        self,
        ticker: str,
        as_of: date,
    ) -> Optional[AggregatedOptionsVolume]:
        """Aggregated call/put volume and OI."""
        ...

    # ------------------------------------------------------------------
    # Volatility / Liquidity history
    # ------------------------------------------------------------------

    async def get_iv_history(
        self,
        ticker: str,
        as_of: date,
        lookback_days: int = 252,
    ) -> list[IVHistoryRecord]:
        """IV history for percentile calculation."""
        ...

    async def get_oi_history(
        self,
        contract: str,
        as_of: date,
        lookback_days: int = 5,
    ) -> list[OIHistoryRecord]:
        """OI history for a specific contract."""
        ...

    # ------------------------------------------------------------------
    # Market context
    # ------------------------------------------------------------------

    async def get_market_context(
        self,
        as_of: date,
    ) -> Optional[MarketContextData]:
        """SPY + VIX daily context."""
        ...

    # ------------------------------------------------------------------
    # Catalyst (returns None/False for historical)
    # ------------------------------------------------------------------

    async def get_days_to_earnings(
        self,
        ticker: str,
        as_of: date,
    ) -> Optional[int]:
        """Days until next earnings. Returns None if unknown."""
        ...

    async def get_recent_sec_filing(
        self,
        ticker: str,
        as_of: date,
    ) -> bool:
        """Whether there's a recent SEC filing."""
        ...
