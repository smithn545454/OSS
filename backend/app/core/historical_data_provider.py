"""HistoricalDataProvider — reads from S3 parquet files for backtesting.

Key design principles:
- Strict look-ahead bias prevention: ``get_daily_bars()`` uses ``< end_date``
- In-memory caching of full-day parquet reads (one S3 GET per date per dataset)
- No earnings/catalyst data (returns None/False)
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from typing import Any, Optional

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

    def __init__(self, s3_bucket: str, s3_client: Any = None) -> None:
        self.s3_bucket = s3_bucket
        if s3_client is None:
            import boto3

            s3_client = boto3.client("s3")
        self.s3 = s3_client
        # In-memory caches: {dataset/date -> pyarrow.Table}
        self._cache: dict[str, Any] = {}
        self._market_context_cache: Optional[Any] = None

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

            tickers = table.column("ticker").to_pylist()
            for idx, t in enumerate(tickers):
                if t == ticker:
                    bars.append(
                        DailyBar(
                            ticker=ticker,
                            date=trade_date,
                            open=table.column("open")[idx].as_py(),
                            high=table.column("high")[idx].as_py(),
                            low=table.column("low")[idx].as_py(),
                            close=table.column("close")[idx].as_py(),
                            volume=table.column("volume")[idx].as_py(),
                            vwap=(
                                table.column("vwap")[idx].as_py()
                                if "vwap" in table.column_names
                                else None
                            ),
                        )
                    )
                    break

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
        ticker_set = set(tickers)

        for trade_date in candidate_dates:
            if trade_date >= end_date.isoformat():
                continue
            table = self._read_stock_ohlcv(trade_date)
            if table is None:
                continue

            all_tickers = table.column("ticker").to_pylist()
            for idx, t in enumerate(all_tickers):
                if t in ticker_set:
                    result[t].append(
                        DailyBar(
                            ticker=t,
                            date=trade_date,
                            open=table.column("open")[idx].as_py(),
                            high=table.column("high")[idx].as_py(),
                            low=table.column("low")[idx].as_py(),
                            close=table.column("close")[idx].as_py(),
                            volume=table.column("volume")[idx].as_py(),
                            vwap=(
                                table.column("vwap")[idx].as_py()
                                if "vwap" in table.column_names
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
            return []

        min_expiry = (as_of + timedelta(days=min_dte)).isoformat()
        max_expiry = (as_of + timedelta(days=max_dte)).isoformat()

        contracts: list[dict[str, Any]] = []
        tickers_col = table.column("ticker").to_pylist()
        for idx, t in enumerate(tickers_col):
            if t != ticker:
                continue
            expiry = str(table.column("expiry_date")[idx].as_py())
            if expiry < min_expiry or expiry > max_expiry:
                continue
            contracts.append(self._row_to_contract(table, idx, as_of))

        return contracts

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

            tickers_col = table.column("ticker").to_pylist()
            for idx, t in enumerate(tickers_col):
                if t == ticker:
                    records.append(
                        IVHistoryRecord(
                            ticker=ticker,
                            date=trade_date,
                            atm_iv=table.column("atm_iv")[idx].as_py(),
                        )
                    )
                    break

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

            # Options parquet has ticker column — search for the contract
            # Contract format varies; check if any column matches
            if "ticker" in table.column_names:
                tickers_col = table.column("ticker").to_pylist()
                for idx, t in enumerate(tickers_col):
                    if t == contract:
                        has_oi = "open_interest" in table.column_names
                        oi = table.column("open_interest")[idx].as_py() if has_oi else 0
                        has_vol = "volume" in table.column_names
                        vol = table.column("volume")[idx].as_py() if has_vol else None
                        records.append(
                            OIHistoryRecord(
                                option_ticker=contract,
                                date=trade_date,
                                open_interest=oi or 0,
                                volume=vol,
                            )
                        )
                        break

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

        dates = table.column("date").to_pylist()
        target = as_of.isoformat()
        for idx, d in enumerate(dates):
            if str(d) == target:
                return MarketContextData(
                    date=target,
                    spy_close=table.column("spy_close")[idx].as_py(),
                    spy_change_pct=table.column("spy_change_pct")[idx].as_py(),
                    vix_close=table.column("vix_close")[idx].as_py(),
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
        self._market_context_cache = None
