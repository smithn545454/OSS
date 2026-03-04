"""HistoricalDataProvider — reads from S3 parquet files for backtesting.

Key design principles:
- Strict look-ahead bias prevention: ``get_daily_bars()`` uses ``< end_date``
- In-memory caching of full-day parquet reads (one S3 GET per date per dataset)
- PyArrow push-down filtering for O(1) ticker lookups in large parquets
- No earnings/catalyst data (returns None/False)
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from typing import Any, Optional

import pyarrow.compute as pc
import pyarrow.parquet as pq

from app.core.data_provider import (
    AggregatedOptionsVolume,
    DailyBar,
    IVHistoryRecord,
    MarketContextData,
    OIHistoryRecord,
    StockSnapshot,
)

logger = logging.getLogger(__name__)


class HistoricalDataProvider:
    """DataProvider backed by S3 parquet files for backtesting."""

    def __init__(
        self,
        s3_bucket: str,
        s3_client: Any = None,
        as_of_date: Any = None,
        shared_cache: Optional[dict[str, Any]] = None,
    ) -> None:
        self.s3_bucket = s3_bucket
        self.as_of_date = as_of_date  # Contextual: callers track which date is being processed
        if s3_client is None:
            import boto3

            s3_client = boto3.client("s3")
        self.s3 = s3_client
        # In-memory caches: {dataset/date -> pyarrow.Table}
        # If shared_cache provided, reuse it (pre-populated by prefetch)
        self._cache: dict[str, Any] = shared_cache if shared_cache is not None else {}
        self._market_context_cache: Optional[Any] = None
        # Lightweight cache for exit resolution (column-filtered options reads)
        self._price_cache: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Internal S3 helpers
    # ------------------------------------------------------------------

    _MISSING = object()  # Sentinel for cached negative lookups

    def _read_parquet(self, s3_key: str) -> Optional[Any]:
        """Read a parquet file from S3, returning a pyarrow Table."""
        cached = self._cache.get(s3_key, self._MISSING)
        if cached is not self._MISSING:
            return cached  # Hit (table or None for cached 404)
        try:
            obj = self.s3.get_object(Bucket=self.s3_bucket, Key=s3_key)
            buf = io.BytesIO(obj["Body"].read())
            table = pq.ParquetFile(buf).read()
            self._cache[s3_key] = table
            return table
        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
                self._cache[s3_key] = None  # Cache negative lookups
                return None
            logger.warning(f"Error reading s3://{self.s3_bucket}/{s3_key}: {e}")
            return None

    def _read_stock_ohlcv(self, trade_date: str) -> Optional[Any]:
        return self._read_parquet(f"stock-ohlcv/date={trade_date}/data.parquet")

    def _read_options_chain(self, trade_date: str) -> Optional[Any]:
        return self._read_parquet(f"options-chains/date={trade_date}/data.parquet")

    # Columns needed for contract price lookups (exit resolution)
    _PRICE_COLUMNS = ["ticker", "strike", "expiry_date", "option_type", "bid", "ask", "last_price"]

    def _read_options_chain_lite(self, trade_date: str) -> Optional[Any]:
        """Read only price-relevant columns from options chain parquet.

        Uses a separate cache from _read_options_chain to avoid inflating
        memory when the full table is not needed (e.g. exit resolution).
        """
        s3_key = f"options-chains/date={trade_date}/data.parquet"
        if s3_key in self._price_cache:
            return self._price_cache[s3_key]
        # Also check if the full table is already cached — reuse it
        if s3_key in self._cache:
            return self._cache[s3_key]
        try:
            obj = self.s3.get_object(Bucket=self.s3_bucket, Key=s3_key)
            buf = io.BytesIO(obj["Body"].read())
            pf = pq.ParquetFile(buf)
            # Only read columns that exist in the file
            available = set(pf.schema.names)
            cols = [c for c in self._PRICE_COLUMNS if c in available]
            table = pf.read(columns=cols)
            self._price_cache[s3_key] = table
            return table
        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
                return None
            logger.warning(f"Error reading lite s3://{self.s3_bucket}/{s3_key}: {e}")
            return None

    def _read_iv_history(self, trade_date: str) -> Optional[Any]:
        return self._read_parquet(f"iv-history/date={trade_date}/data.parquet")

    def _read_market_context(self) -> Optional[Any]:
        if self._market_context_cache is not None:
            return self._market_context_cache
        table = self._read_parquet("market-context/data.parquet")
        self._market_context_cache = table
        return table

    @staticmethod
    def _trading_days_before(end_date: date, lookback_days: int) -> list[str]:
        """Generate candidate date strings for lookback.

        Not all dates will have data (weekends/holidays), so we generate
        extra candidates and let the parquet reads filter naturally.
        """
        dates = []
        # Generate ~1.5x calendar days to cover trading days
        calendar_days = int(lookback_days * 1.5) + 10
        for i in range(1, calendar_days + 1):
            d = end_date - timedelta(days=i)
            # Skip weekends
            if d.weekday() < 5:
                dates.append(d.isoformat())
        return sorted(dates)

    # ------------------------------------------------------------------
    # Stock data
    # ------------------------------------------------------------------

    async def get_daily_bars(
        self,
        ticker: str,
        end_date: date,
        lookback_days: int = 60,
    ) -> list[DailyBar]:
        """Daily OHLCV bars for ``[end_date - lookback, end_date)`` — strict < end_date."""
        candidate_dates = self._trading_days_before(end_date, lookback_days)
        bars: list[DailyBar] = []

        for trade_date in candidate_dates:
            if trade_date >= end_date.isoformat():
                continue  # Prevent look-ahead
            table = self._read_stock_ohlcv(trade_date)
            if table is None:
                continue

            # PyArrow push-down filter: O(1) vs O(n) Python loop
            filtered = table.filter(pc.field("ticker") == ticker)
            if filtered.num_rows > 0:
                row = filtered.slice(0, 1)
                bars.append(
                    DailyBar(
                        ticker=ticker,
                        date=trade_date,
                        open=row.column("open")[0].as_py(),
                        high=row.column("high")[0].as_py(),
                        low=row.column("low")[0].as_py(),
                        close=row.column("close")[0].as_py(),
                        volume=row.column("volume")[0].as_py(),
                        vwap=(
                            row.column("vwap")[0].as_py()
                            if "vwap" in row.column_names
                            else None
                        ),
                    )
                )

            if len(bars) >= lookback_days:
                break

        return bars

    async def get_daily_bars_batch(
        self,
        tickers: list[str],
        end_date: date,
        lookback_days: int = 60,
    ) -> dict[str, list[DailyBar]]:
        candidate_dates = self._trading_days_before(end_date, lookback_days)
        result: dict[str, list[DailyBar]] = {t: [] for t in tickers}

        for trade_date in candidate_dates:
            if trade_date >= end_date.isoformat():
                continue
            table = self._read_stock_ohlcv(trade_date)
            if table is None:
                continue

            # PyArrow push-down filter: batch filter for all requested tickers
            filtered = table.filter(pc.field("ticker").isin(tickers))
            if filtered.num_rows == 0:
                continue

            has_vwap = "vwap" in filtered.column_names
            filtered_tickers = filtered.column("ticker").to_pylist()
            for idx, t in enumerate(filtered_tickers):
                result[t].append(
                    DailyBar(
                        ticker=t,
                        date=trade_date,
                        open=filtered.column("open")[idx].as_py(),
                        high=filtered.column("high")[idx].as_py(),
                        low=filtered.column("low")[idx].as_py(),
                        close=filtered.column("close")[idx].as_py(),
                        volume=filtered.column("volume")[idx].as_py(),
                        vwap=(
                            filtered.column("vwap")[idx].as_py()
                            if has_vwap
                            else None
                        ),
                    )
                )

        return result

    async def get_stock_snapshot(
        self,
        ticker: str,
        as_of: date,
    ) -> Optional[StockSnapshot]:
        """Previous close: reads the most recent trading day before as_of."""
        bars = await self.get_daily_bars(ticker, as_of, lookback_days=5)
        if not bars:
            return None
        latest = bars[-1]  # Most recent
        return StockSnapshot(
            ticker=ticker,
            date=latest.date,
            close=latest.close,
            volume=latest.volume,
            vwap=latest.vwap,
            open=latest.open,
            high=latest.high,
            low=latest.low,
        )

    async def get_stock_snapshots_batch(
        self,
        tickers: list[str],
        as_of: date,
    ) -> dict[str, StockSnapshot]:
        result: dict[str, StockSnapshot] = {}
        batch = await self.get_daily_bars_batch(tickers, as_of, lookback_days=5)
        for t, bars in batch.items():
            if bars:
                latest = bars[-1]
                result[t] = StockSnapshot(
                    ticker=t,
                    date=latest.date,
                    close=latest.close,
                    volume=latest.volume,
                    vwap=latest.vwap,
                    open=latest.open,
                    high=latest.high,
                    low=latest.low,
                )
        return result

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
        """Read options chain from parquet for as_of date (EOD data)."""
        table = self._read_options_chain(as_of.isoformat())
        if table is None:
            logger.warning(f"[HDP] No options parquet for date={as_of.isoformat()}")
            return []

        min_expiry = (as_of + timedelta(days=min_dte)).isoformat()
        max_expiry = (as_of + timedelta(days=max_dte)).isoformat()

        # PyArrow push-down filter: ticker + expiry range
        filtered = table.filter(
            (pc.field("ticker") == ticker)
            & (pc.field("expiry_date") >= min_expiry)
            & (pc.field("expiry_date") <= max_expiry)
        )

        if filtered.num_rows == 0:
            logger.debug(
                f"[HDP] 0 contracts for {ticker} on {as_of} "
                f"(expiry {min_expiry}-{max_expiry}, "
                f"table rows: {table.num_rows})"
            )

        contracts: list[dict[str, Any]] = []
        for idx in range(filtered.num_rows):
            contracts.append(self._row_to_contract(filtered, idx, as_of))

        return contracts

    async def get_options_chain_batch(
        self,
        tickers: list[str],
        as_of: date,
        min_dte: int = 7,
        max_dte: int = 120,
    ) -> dict[str, list[dict[str, Any]]]:
        """Batch-read options chains for multiple tickers in a single pass.

        Much faster than calling get_options_chain() per ticker because
        it does a single PyArrow isin() filter instead of N individual
        equality filters on a ~1.5M row table.
        """
        table = self._read_options_chain(as_of.isoformat())
        if table is None:
            return {}

        min_expiry = (as_of + timedelta(days=min_dte)).isoformat()
        max_expiry = (as_of + timedelta(days=max_dte)).isoformat()

        # Single PyArrow filter: all tickers + expiry range
        filtered = table.filter(
            pc.field("ticker").isin(tickers)
            & (pc.field("expiry_date") >= min_expiry)
            & (pc.field("expiry_date") <= max_expiry)
        )

        # Group by ticker
        result: dict[str, list[dict[str, Any]]] = {}
        if filtered.num_rows > 0:
            ticker_col = filtered.column("ticker").to_pylist()
            for idx, t in enumerate(ticker_col):
                if t not in result:
                    result[t] = []
                result[t].append(
                    self._row_to_contract(filtered, idx, as_of)
                )

        logger.info(
            f"[HDP] Batch chain: {len(tickers)} tickers → "
            f"{filtered.num_rows} contracts across "
            f"{len(result)} tickers"
        )
        return result

    async def get_options_chain_minimal(
        self,
        ticker: str,
        as_of: date,
        min_dte: int = 7,
        max_dte: int = 90,
        strike_range_pct: float = 0.10,
    ) -> list[dict[str, Any]]:
        # Get stock price for strike filtering
        snapshot = await self.get_stock_snapshot(ticker, as_of)
        stock_price = snapshot.close if snapshot else None

        chain = await self.get_options_chain(ticker, as_of, min_dte, max_dte)

        if stock_price and stock_price > 0:
            lo = stock_price * (1 - strike_range_pct)
            hi = stock_price * (1 + strike_range_pct)
            chain = [c for c in chain if lo <= c.get("details", {}).get("strike_price", 0) <= hi]

        return chain

    async def get_contract_price(
        self,
        ticker: str,
        strike: float,
        expiration_date: str,
        option_type: str,
        as_of: date,
    ) -> Optional[float]:
        """Get a single contract's mid price using column-filtered reads.

        Optimized for exit resolution: reads only price-relevant columns
        (~33MB vs ~75MB per file), reducing memory during 21-day forward scans.
        Uses PyArrow push-down filters for O(1) lookup in large parquets.
        """
        table = self._read_options_chain_lite(as_of.isoformat())
        if table is None:
            return None

        ot_lower = option_type.lower() if option_type else ""
        target_type = "c" if ot_lower in ("call", "c") else "p"

        has_bid = "bid" in table.column_names
        has_ask = "ask" in table.column_names
        has_last = "last_price" in table.column_names

        # PyArrow push-down filter: ticker + expiry + option_type
        # Strike uses fuzzy match, so filter by ticker first then check strike
        filtered = table.filter(
            (pc.field("ticker") == ticker)
            & (pc.field("expiry_date") == expiration_date)
            & (
                (pc.field("option_type") == target_type)
                | (pc.field("option_type") == ("call" if target_type == "c" else "put"))
            )
        )

        if filtered.num_rows == 0:
            return None

        strikes_col = filtered.column("strike").to_pylist()
        for idx, c_strike in enumerate(strikes_col):
            if abs(float(c_strike or 0) - strike) < 0.01:
                bid = float(filtered.column("bid")[idx].as_py() or 0) if has_bid else 0.0
                ask = float(filtered.column("ask")[idx].as_py() or 0) if has_ask else 0.0
                if bid > 0 and ask > 0:
                    return (bid + ask) / 2
                last = (
                    float(filtered.column("last_price")[idx].as_py() or 0)
                    if has_last
                    else 0.0
                )
                if last > 0:
                    return last

        return None

    async def get_aggregated_options_volume(
        self,
        ticker: str,
        as_of: date,
    ) -> Optional[AggregatedOptionsVolume]:
        chain = await self.get_options_chain(ticker, as_of, min_dte=0, max_dte=60)
        if not chain:
            return None

        call_vol = put_vol = call_oi = put_oi = 0
        for c in chain:
            details = c.get("details", {})
            otype = details.get("contract_type", "").lower()
            vol = c.get("day", {}).get("volume", 0) or 0
            oi = c.get("open_interest", 0) or 0
            if otype in ("call", "c"):
                call_vol += vol
                call_oi += oi
            elif otype in ("put", "p"):
                put_vol += vol
                put_oi += oi

        ratio = call_vol / put_vol if put_vol > 0 else 0.0
        return AggregatedOptionsVolume(
            ticker=ticker,
            total_call_volume=call_vol,
            total_put_volume=put_vol,
            total_call_oi=call_oi,
            total_put_oi=put_oi,
            call_put_volume_ratio=round(ratio, 4),
            timestamp=as_of.isoformat(),
        )

    def _row_to_contract(self, table: Any, idx: int, as_of: date) -> dict[str, Any]:
        """Convert a parquet row to a Polygon-compatible contract dict."""

        def _col(name: str, default: Any = None) -> Any:
            if name in table.column_names:
                val = table.column(name)[idx].as_py()
                return val if val is not None else default
            return default

        strike = _col("strike", 0.0)
        expiry = str(_col("expiry_date", ""))
        otype = _col("option_type", "c")
        bid = _col("bid", 0.0)
        ask = _col("ask", 0.0)
        last = _col("last_price", 0.0)

        return {
            "details": {
                "contract_type": "call" if otype in ("c", "C", "call") else "put",
                "strike_price": strike,
                "expiration_date": expiry,
                "ticker": _col("ticker", ""),
            },
            "day": {
                "close": last,
                "volume": _col("volume", 0),
                "vwap": (bid + ask) / 2 if (bid + ask) > 0 else last,
            },
            "open_interest": _col("open_interest", 0),
            "implied_volatility": (_col("bid_iv", 0.0) + _col("ask_iv", 0.0)) / 2,
            "greeks": {
                "delta": _col("delta", 0.0),
                "gamma": _col("gamma", 0.0),
                "theta": _col("theta", 0.0),
                "vega": _col("vega", 0.0),
            },
            "last_quote": {
                "bid": bid,
                "ask": ask,
                "last_updated": as_of.isoformat(),
            },
        }

    # ------------------------------------------------------------------
    # Volatility / Liquidity history
    # ------------------------------------------------------------------

    async def get_iv_history(
        self,
        ticker: str,
        as_of: date,
        lookback_days: int = 252,
    ) -> list[IVHistoryRecord]:
        records: list[IVHistoryRecord] = []
        candidate_dates = self._trading_days_before(as_of, lookback_days)

        for trade_date in candidate_dates:
            if trade_date >= as_of.isoformat():
                continue
            table = self._read_iv_history(trade_date)
            if table is None:
                continue

            # PyArrow push-down filter
            filtered = table.filter(pc.field("ticker") == ticker)
            if filtered.num_rows > 0:
                records.append(
                    IVHistoryRecord(
                        ticker=ticker,
                        date=trade_date,
                        atm_iv=filtered.column("atm_iv")[0].as_py(),
                    )
                )

            if len(records) >= lookback_days:
                break

        return records

    async def get_oi_history(
        self,
        contract: str,
        as_of: date,
        lookback_days: int = 5,
    ) -> list[OIHistoryRecord]:
        """Extract OI from options-chains parquets for last N trading days."""
        records: list[OIHistoryRecord] = []
        candidate_dates = self._trading_days_before(as_of, lookback_days)

        for trade_date in candidate_dates[-lookback_days:]:
            if trade_date >= as_of.isoformat():
                continue
            table = self._read_options_chain(trade_date)
            if table is None:
                continue

            if "ticker" not in table.column_names:
                continue

            # PyArrow push-down filter
            filtered = table.filter(pc.field("ticker") == contract)
            if filtered.num_rows > 0:
                has_oi = "open_interest" in filtered.column_names
                oi = filtered.column("open_interest")[0].as_py() if has_oi else 0
                has_vol = "volume" in filtered.column_names
                vol = filtered.column("volume")[0].as_py() if has_vol else None
                records.append(
                    OIHistoryRecord(
                        option_ticker=contract,
                        date=trade_date,
                        open_interest=oi or 0,
                        volume=vol,
                    )
                )

        return records

    # ------------------------------------------------------------------
    # Market context
    # ------------------------------------------------------------------

    async def get_market_context(
        self,
        as_of: date,
    ) -> Optional[MarketContextData]:
        table = self._read_market_context()
        if table is None:
            return None

        target = as_of.isoformat()
        # PyArrow push-down filter
        filtered = table.filter(pc.field("date") == target)
        if filtered.num_rows > 0:
            return MarketContextData(
                date=target,
                spy_close=filtered.column("spy_close")[0].as_py(),
                spy_change_pct=filtered.column("spy_change_pct")[0].as_py(),
                vix_close=filtered.column("vix_close")[0].as_py(),
            )
        return None

    # ------------------------------------------------------------------
    # Catalyst (always None/False for historical)
    # ------------------------------------------------------------------

    async def get_days_to_earnings(
        self,
        ticker: str,
        as_of: date,
    ) -> Optional[int]:
        return None

    async def get_recent_sec_filing(
        self,
        ticker: str,
        as_of: date,
    ) -> bool:
        return False

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Clear all in-memory caches."""
        self._cache.clear()
        self._price_cache.clear()
        self._market_context_cache = None
