"""HistoricalDataProvider — reads from S3 parquet files for backtesting.

Key design principles:
- Strict look-ahead bias prevention: ``get_daily_bars()`` uses ``< end_date``
- In-memory caching of full-day parquet reads (one S3 GET per date per dataset)
- Pre-indexed ticker lookups: O(1) dict hit + PyArrow take() vs O(N) filter
- No earnings/catalyst data (returns None/False)
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from typing import Any, Optional

import numpy as np
import pyarrow as pa
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

    # Max lite tables kept in _price_cache before LRU eviction.
    # Each lite table is ~73 MB (7 columns x 1.4M rows). Keep low to avoid
    # OOM during exit resolution. All exit tasks iterate forward dates in
    # lockstep, so LRU-5 has no thrashing.
    _PRICE_CACHE_MAX = 5

    def __init__(
        self,
        s3_bucket: str,
        s3_client: Any = None,
        as_of_date: Any = None,
        shared_cache: Optional[dict[str, Any]] = None,
    ) -> None:
        self.s3_bucket = s3_bucket
        self.as_of_date = as_of_date  # Contextual: callers track which date is being processed
        self.shared_cache = shared_cache
        if s3_client is None:
            import boto3

            s3_client = boto3.client("s3")
        self.s3 = s3_client
        # In-memory caches: {dataset/date -> pyarrow.Table}
        # If shared_cache provided, reuse it (pre-populated by prefetch)
        self._cache: dict[str, Any] = shared_cache if shared_cache is not None else {}
        self._market_context_cache: Optional[Any] = None
        # Lightweight cache for exit resolution (column-filtered options reads)
        # Uses LRU eviction to cap memory during forward-scanning
        self._price_cache: dict[str, Any] = {}
        # Per-instance ticker indexes for _price_cache tables
        self._price_ticker_indexes: dict[str, dict[str, np.ndarray]] = {}

    # ------------------------------------------------------------------
    # Ticker indexing — O(1) lookups instead of O(N) PyArrow filters
    # ------------------------------------------------------------------

    @staticmethod
    def _build_ticker_index(table: Any) -> dict[str, np.ndarray]:
        """Build {ticker: int32_row_indices} from a PyArrow table.

        Uses numpy argsort + boundary detection. O(N log N) construction,
        ~6 MB memory per 1.4M-row table (int32 indices).
        """
        if table is None or table.num_rows == 0:
            return {}
        ticker_np = table.column("ticker").to_numpy(zero_copy_only=False)
        order = np.argsort(ticker_np, kind="stable").astype(np.int32)
        sorted_tickers = ticker_np[order]
        breaks = np.nonzero(sorted_tickers[1:] != sorted_tickers[:-1])[0] + 1
        boundaries = np.concatenate([[0], breaks, [len(sorted_tickers)]])
        index: dict[str, np.ndarray] = {}
        for i in range(len(boundaries) - 1):
            index[sorted_tickers[boundaries[i]]] = order[boundaries[i] : boundaries[i + 1]]
        return index

    def _get_ticker_index(self, cache_key: str, table: Any) -> dict[str, np.ndarray]:
        """Get or build a ticker index for a cached table.

        Indexes are stored in _cache (shared across providers for the same
        batch) under ``__idx__<cache_key>`` so they're built once and reused.
        """
        idx_key = f"__idx__{cache_key}"
        existing = self._cache.get(idx_key)
        if existing is not None:
            return existing
        index = self._build_ticker_index(table)
        self._cache[idx_key] = index
        return index

    def _take_rows(self, table: Any, indices: np.ndarray) -> Any:
        """Efficient sub-table extraction using pre-computed row indices."""
        return table.take(pa.array(indices, type=pa.int32()))

    # ------------------------------------------------------------------
    # Internal S3 helpers
    # ------------------------------------------------------------------

    def _read_parquet(self, s3_key: str) -> Optional[Any]:
        """Read a parquet file from S3, returning a pyarrow Table."""
        if s3_key in self._cache:
            return self._cache[s3_key]
        try:
            obj = self.s3.get_object(Bucket=self.s3_bucket, Key=s3_key)
            buf = io.BytesIO(obj["Body"].read())
            table = pq.ParquetFile(buf).read()
            self._cache[s3_key] = table
            return table
        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
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
        LRU eviction caps at _PRICE_CACHE_MAX entries to prevent OOM
        during exit resolution forward-scanning (~60 unique dates).
        """
        s3_key = f"options-chains/date={trade_date}/data.parquet"
        if s3_key in self._price_cache:
            # Move to end (most recently used) for LRU
            self._price_cache[s3_key] = self._price_cache.pop(s3_key)
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
            # LRU eviction — remove oldest entries to stay within memory budget
            while len(self._price_cache) >= self._PRICE_CACHE_MAX:
                evicted_key = next(iter(self._price_cache))
                del self._price_cache[evicted_key]
                # Also clean up any ticker index for the evicted table
                self._price_ticker_indexes.pop(evicted_key, None)
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

            # Ticker-indexed lookup: O(1) dict hit + take()
            s3_key = f"stock-ohlcv/date={trade_date}/data.parquet"
            index = self._get_ticker_index(s3_key, table)
            row_indices = index.get(ticker)
            if row_indices is not None and len(row_indices) > 0:
                row = self._take_rows(table, row_indices[:1])
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
        date_str = as_of.isoformat()
        table = self._read_options_chain(date_str)
        if table is None:
            return []

        min_expiry = (as_of + timedelta(days=min_dte)).isoformat()
        max_expiry = (as_of + timedelta(days=max_dte)).isoformat()

        # Ticker-indexed lookup: O(1) dict hit + take() on ~280 rows
        s3_key = f"options-chains/date={date_str}/data.parquet"
        index = self._get_ticker_index(s3_key, table)
        row_indices = index.get(ticker)
        if row_indices is None or len(row_indices) == 0:
            return []

        ticker_table = self._take_rows(table, row_indices)

        # Apply DTE filter on the small per-ticker sub-table
        filtered = ticker_table.filter(
            (pc.field("expiry_date") >= min_expiry)
            & (pc.field("expiry_date") <= max_expiry)
        )

        return self._table_to_contracts(filtered, as_of)

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
        Uses ticker indexing for O(1) lookup in large parquets.
        """
        date_str = as_of.isoformat()
        table = self._read_options_chain_lite(date_str)
        if table is None:
            return None

        ot_lower = option_type.lower() if option_type else ""
        target_type = "c" if ot_lower in ("call", "c") else "p"

        has_bid = "bid" in table.column_names
        has_ask = "ask" in table.column_names
        has_last = "last_price" in table.column_names

        # Ticker-indexed lookup on lite table
        s3_key = f"options-chains/date={date_str}/data.parquet"
        if s3_key not in self._price_ticker_indexes:
            if "ticker" in table.column_names:
                self._price_ticker_indexes[s3_key] = self._build_ticker_index(table)
            else:
                self._price_ticker_indexes[s3_key] = {}
        index = self._price_ticker_indexes[s3_key]
        row_indices = index.get(ticker)
        if row_indices is None or len(row_indices) == 0:
            return None

        ticker_table = self._take_rows(table, row_indices)

        # Filter by expiry + option_type on the small per-ticker sub-table
        filtered = ticker_table.filter(
            (pc.field("expiry_date") == expiration_date)
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

    def _table_to_contracts(self, table: Any, as_of: date) -> list[dict[str, Any]]:
        """Batch-convert a PyArrow table to Polygon-compatible contract dicts.

        Uses to_pydict() for a single bulk PyArrow→Python conversion instead
        of per-element table.column(name)[idx].as_py() calls. ~50x faster
        for the UV scanner's 280-contract-per-ticker chains.
        """
        if table is None or table.num_rows == 0:
            return []

        cols = table.to_pydict()
        n = table.num_rows
        as_of_str = as_of.isoformat()

        # Pre-extract column lists (empty list if column missing)
        tickers = cols.get("ticker", [None] * n)
        strikes = cols.get("strike", [0.0] * n)
        expiries = cols.get("expiry_date", [""] * n)
        otypes = cols.get("option_type", ["c"] * n)
        bids = cols.get("bid", [0.0] * n)
        asks = cols.get("ask", [0.0] * n)
        lasts = cols.get("last_price", [0.0] * n)
        volumes = cols.get("volume", [0] * n)
        ois = cols.get("open_interest", [0] * n)
        bid_ivs = cols.get("bid_iv", [0.0] * n)
        ask_ivs = cols.get("ask_iv", [0.0] * n)
        deltas = cols.get("delta", [0.0] * n)
        gammas = cols.get("gamma", [0.0] * n)
        thetas = cols.get("theta", [0.0] * n)
        vegas = cols.get("vega", [0.0] * n)

        contracts = []
        for i in range(n):
            bid = bids[i] or 0.0
            ask = asks[i] or 0.0
            last = lasts[i] or 0.0
            ot = otypes[i] or "c"
            bid_iv = bid_ivs[i] or 0.0
            ask_iv = ask_ivs[i] or 0.0
            contracts.append({
                "details": {
                    "contract_type": "call" if ot in ("c", "C", "call") else "put",
                    "strike_price": strikes[i] or 0.0,
                    "expiration_date": str(expiries[i] or ""),
                    "ticker": tickers[i] or "",
                },
                "day": {
                    "close": last,
                    "volume": volumes[i] or 0,
                    "vwap": (bid + ask) / 2 if (bid + ask) > 0 else last,
                },
                "open_interest": ois[i] or 0,
                "implied_volatility": (bid_iv + ask_iv) / 2,
                "greeks": {
                    "delta": deltas[i] or 0.0,
                    "gamma": gammas[i] or 0.0,
                    "theta": thetas[i] or 0.0,
                    "vega": vegas[i] or 0.0,
                },
                "last_quote": {
                    "bid": bid,
                    "ask": ask,
                    "last_updated": as_of_str,
                },
            })
        return contracts

    # ------------------------------------------------------------------
    # Volatility / Liquidity history
    # ------------------------------------------------------------------

    async def get_iv_history(
        self,
        ticker: str,
        as_of: date,
        lookback_days: int = 252,
    ) -> list[IVHistoryRecord]:
        # Fast path: use pre-built combined IV table from prefetch
        combined_iv = self._cache.get("__combined_iv__")
        iv_ticker_index = self._cache.get("__iv_ticker_index__")
        if combined_iv is not None and iv_ticker_index is not None:
            row_indices = iv_ticker_index.get(ticker)
            if row_indices is None or len(row_indices) == 0:
                return []
            sub = self._take_rows(combined_iv, row_indices)
            # Filter by date < as_of and sort
            as_of_str = as_of.isoformat()
            filtered = sub.filter(pc.field("trade_date") < as_of_str)
            if filtered.num_rows == 0:
                return []
            # Sort by date and take the most recent lookback_days
            sorted_indices = pc.sort_indices(filtered, sort_keys=[("trade_date", "ascending")])
            sorted_table = filtered.take(sorted_indices)
            # Take last N rows
            start = max(0, sorted_table.num_rows - lookback_days)
            result_table = sorted_table.slice(start)
            records: list[IVHistoryRecord] = []
            dates_col = result_table.column("trade_date").to_pylist()
            iv_col = result_table.column("atm_iv").to_pylist()
            for i in range(result_table.num_rows):
                records.append(
                    IVHistoryRecord(
                        ticker=ticker,
                        date=str(dates_col[i]),
                        atm_iv=iv_col[i],
                    )
                )
            return records

        # Slow path: per-date iteration with ticker indexing
        records = []
        candidate_dates = self._trading_days_before(as_of, lookback_days)

        for trade_date in candidate_dates:
            if trade_date >= as_of.isoformat():
                continue
            table = self._read_iv_history(trade_date)
            if table is None:
                continue

            # Ticker-indexed lookup
            s3_key = f"iv-history/date={trade_date}/data.parquet"
            index = self._get_ticker_index(s3_key, table)
            row_indices = index.get(ticker)
            if row_indices is not None and len(row_indices) > 0:
                row = self._take_rows(table, row_indices[:1])
                records.append(
                    IVHistoryRecord(
                        ticker=ticker,
                        date=trade_date,
                        atm_iv=row.column("atm_iv")[0].as_py(),
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

            # Ticker-indexed lookup (contract tickers are in the same "ticker" col)
            s3_key = f"options-chains/date={trade_date}/data.parquet"
            index = self._get_ticker_index(s3_key, table)
            row_indices = index.get(contract)
            if row_indices is not None and len(row_indices) > 0:
                row = self._take_rows(table, row_indices[:1])
                has_oi = "open_interest" in row.column_names
                oi = row.column("open_interest")[0].as_py() if has_oi else 0
                has_vol = "volume" in row.column_names
                vol = row.column("volume")[0].as_py() if has_vol else None
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

    def release_options_chain(self, as_of_date: date) -> None:
        """Free the full options chain table for a specific date from shared cache.

        Called after pipeline phase completes for a day. Exit resolution uses
        the separate lite _price_cache, so the full table (~100 MB) is no
        longer needed. This prevents OOM during exit resolution.
        """
        key = f"options-chains/date={as_of_date.isoformat()}/data.parquet"
        idx_key = f"__idx__{key}"
        if key in self._cache:
            del self._cache[key]
        if idx_key in self._cache:
            del self._cache[idx_key]

    def clear_cache(self) -> None:
        """Clear all in-memory caches."""
        self._cache.clear()
        self._price_cache.clear()
        self._price_ticker_indexes.clear()
        self._market_context_cache = None
