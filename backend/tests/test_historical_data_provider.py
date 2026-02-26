"""Tests for HistoricalDataProvider.

Uses a mock S3 client with in-memory parquet files to verify all
DataProvider methods work correctly against historical data.
"""

from __future__ import annotations

import io
from datetime import date
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.core.data_provider import MarketContextData, StockSnapshot, IVHistoryRecord, OIHistoryRecord
from app.core.historical_data_provider import HistoricalDataProvider


# ============================================================================
# Fixtures: create mock S3 with in-memory parquet data
# ============================================================================


def _parquet_bytes(table: pa.Table) -> bytes:
    """Convert a pyarrow Table to parquet bytes."""
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


@pytest.fixture
def mock_s3_data():
    """Build a dict of s3_key -> parquet bytes for test data."""
    data = {}

    # Stock OHLCV: 5 trading days of AAPL + MSFT + SPY
    for day_offset, day_str in enumerate([
        "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
    ]):
        table = pa.table({
            "ticker": pa.array(["AAPL", "MSFT", "SPY"], type=pa.string()),
            "date": pa.array([day_str] * 3, type=pa.string()),
            "open": pa.array([180.0 + day_offset, 370.0 + day_offset, 470.0 + day_offset]),
            "high": pa.array([185.0 + day_offset, 375.0 + day_offset, 475.0 + day_offset]),
            "low": pa.array([178.0 + day_offset, 368.0 + day_offset, 468.0 + day_offset]),
            "close": pa.array([183.0 + day_offset, 372.0 + day_offset, 472.0 + day_offset]),
            "volume": pa.array([50_000_000, 30_000_000, 80_000_000], type=pa.int64()),
            "vwap": pa.array([182.0 + day_offset, 371.0 + day_offset, 471.0 + day_offset]),
        })
        data[f"stock-ohlcv/date={day_str}/data.parquet"] = _parquet_bytes(table)

    # Options chains for 2024-01-12
    n = 6
    options_table = pa.table({
        "ticker": pa.array(["AAPL"] * n, type=pa.string()),
        "trade_date": pa.array(["2024-01-12"] * n, type=pa.string()),
        "strike": pa.array([175.0, 180.0, 185.0, 175.0, 180.0, 185.0]),
        "expiry_date": pa.array(["2024-02-16"] * n, type=pa.string()),
        "option_type": pa.array(["c", "c", "c", "p", "p", "p"], type=pa.string()),
        "last_price": pa.array([12.0, 8.5, 5.2, 2.0, 3.5, 6.0]),
        "bid": pa.array([11.5, 8.2, 5.0, 1.8, 3.3, 5.8]),
        "ask": pa.array([12.5, 8.8, 5.4, 2.2, 3.7, 6.2]),
        "bid_iv": pa.array([0.28, 0.30, 0.32, 0.29, 0.31, 0.33]),
        "ask_iv": pa.array([0.30, 0.32, 0.34, 0.31, 0.33, 0.35]),
        "open_interest": pa.array([5000, 8000, 3000, 4000, 7000, 2000], type=pa.int64()),
        "volume": pa.array([500, 800, 300, 400, 700, 200], type=pa.int64()),
        "delta": pa.array([0.75, 0.55, 0.35, -0.25, -0.45, -0.65]),
        "gamma": pa.array([0.02, 0.03, 0.03, 0.02, 0.03, 0.03]),
        "vega": pa.array([0.20, 0.25, 0.23, 0.20, 0.25, 0.23]),
        "theta": pa.array([-0.05, -0.07, -0.06, -0.04, -0.06, -0.05]),
        "rho": pa.array([0.10, 0.08, 0.06, -0.10, -0.08, -0.06]),
    })
    data["options-chains/date=2024-01-12/data.parquet"] = _parquet_bytes(options_table)

    # IV history: 5 days for AAPL + MSFT
    for day_str in ["2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12"]:
        table = pa.table({
            "ticker": pa.array(["AAPL", "MSFT"], type=pa.string()),
            "date": pa.array([day_str, day_str], type=pa.string()),
            "atm_iv": pa.array([0.30, 0.25]),
        })
        data[f"iv-history/date={day_str}/data.parquet"] = _parquet_bytes(table)

    # Market context: consolidated file
    mc_table = pa.table({
        "date": pa.array([
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12"
        ], type=pa.string()),
        "spy_close": pa.array([472.0, 473.0, 474.0, 475.0, 476.0]),
        "spy_change_pct": pa.array([0.0, 0.2119, 0.2114, 0.2110, 0.2105]),
        "vix_close": pa.array([13.5, 13.2, 12.8, 13.0, 12.5]),
    })
    data["market-context/data.parquet"] = _parquet_bytes(mc_table)

    return data


@pytest.fixture
def mock_s3_client(mock_s3_data):
    """Create a mock S3 client that returns parquet data from in-memory store."""
    client = MagicMock()

    def mock_get_object(Bucket, Key):
        if Key in mock_s3_data:
            body = MagicMock()
            body.read.return_value = mock_s3_data[Key]
            return {"Body": body}
        # Simulate NoSuchKey error
        from botocore.exceptions import ClientError
        raise ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
            "GetObject",
        )

    client.get_object = MagicMock(side_effect=mock_get_object)
    return client


@pytest.fixture
def provider(mock_s3_client):
    """HistoricalDataProvider with mocked S3."""
    return HistoricalDataProvider(s3_bucket="test-bucket", s3_client=mock_s3_client)


# ============================================================================
# Tests
# ============================================================================


class TestHistoricalDailyBars:
    """Tests for stock OHLCV data access."""

    async def test_get_daily_bars_single_ticker(self, provider):
        """get_daily_bars returns bars for AAPL."""
        bars = await provider.get_daily_bars("AAPL", date(2024, 1, 13), lookback_days=10)
        assert len(bars) >= 4
        assert all(b.ticker == "AAPL" for b in bars)
        # Verify strict < end_date
        assert all(b.date < "2024-01-13" for b in bars)

    async def test_get_daily_bars_batch(self, provider):
        """get_daily_bars_batch returns bars for multiple tickers."""
        result = await provider.get_daily_bars_batch(
            ["AAPL", "MSFT"], date(2024, 1, 13), lookback_days=10
        )
        assert "AAPL" in result
        assert "MSFT" in result
        assert len(result["AAPL"]) >= 4
        assert len(result["MSFT"]) >= 4

    async def test_get_daily_bars_lookback_prevention(self, provider):
        """Bars don't include the end_date (look-ahead prevention)."""
        bars = await provider.get_daily_bars("AAPL", date(2024, 1, 12), lookback_days=10)
        # Should not include Jan 12 itself
        assert all(b.date < "2024-01-12" for b in bars)


class TestHistoricalSnapshots:
    """Tests for stock snapshot access."""

    async def test_get_stock_snapshot(self, provider):
        """get_stock_snapshot returns most recent close."""
        snap = await provider.get_stock_snapshot("AAPL", date(2024, 1, 13))
        assert snap is not None
        assert isinstance(snap, StockSnapshot)
        assert snap.ticker == "AAPL"
        assert snap.close > 0

    async def test_get_stock_snapshot_missing_ticker(self, provider):
        """get_stock_snapshot returns None for unknown ticker."""
        snap = await provider.get_stock_snapshot("ZZZZ", date(2024, 1, 12))
        assert snap is None

    async def test_get_stock_snapshots_batch(self, provider):
        """get_stock_snapshots_batch returns snapshots for multiple tickers."""
        result = await provider.get_stock_snapshots_batch(
            ["AAPL", "MSFT"], date(2024, 1, 13)
        )
        assert len(result) == 2
        assert "AAPL" in result
        assert "MSFT" in result


class TestHistoricalOptionsChain:
    """Tests for options chain access."""

    async def test_get_options_chain(self, provider):
        """get_options_chain returns contracts in Polygon format."""
        chain = await provider.get_options_chain("AAPL", date(2024, 1, 12))
        assert len(chain) > 0

        # Verify Polygon-compatible structure
        contract = chain[0]
        assert "details" in contract
        assert "greeks" in contract
        assert "day" in contract
        assert "open_interest" in contract
        assert "last_quote" in contract
        assert contract["details"]["contract_type"] in ("call", "put")

    async def test_get_options_chain_dte_filter(self, provider):
        """Options outside DTE range are filtered out."""
        # All test options expire 2024-02-16, which is 35 DTE from 2024-01-12
        chain = await provider.get_options_chain(
            "AAPL", date(2024, 1, 12), min_dte=30, max_dte=45
        )
        assert len(chain) > 0

        # min_dte=90 should exclude them
        chain_empty = await provider.get_options_chain(
            "AAPL", date(2024, 1, 12), min_dte=90, max_dte=120
        )
        assert len(chain_empty) == 0

    async def test_get_aggregated_options_volume(self, provider):
        """get_aggregated_options_volume computes from chain data."""
        vol = await provider.get_aggregated_options_volume("AAPL", date(2024, 1, 12))
        assert vol is not None
        assert vol.total_call_volume > 0
        assert vol.total_put_volume > 0


class TestHistoricalIVHistory:
    """Tests for IV history access."""

    async def test_get_iv_history(self, provider):
        """get_iv_history returns IVHistoryRecord objects."""
        records = await provider.get_iv_history("AAPL", date(2024, 1, 13), lookback_days=10)
        assert len(records) >= 4
        assert all(isinstance(r, IVHistoryRecord) for r in records)
        assert all(r.ticker == "AAPL" for r in records)
        assert all(r.atm_iv > 0 for r in records)
        # Strict < as_of
        assert all(r.date < "2024-01-13" for r in records)


class TestHistoricalMarketContext:
    """Tests for market context access."""

    async def test_get_market_context(self, provider):
        """get_market_context returns SPY/VIX data."""
        ctx = await provider.get_market_context(date(2024, 1, 12))
        assert ctx is not None
        assert isinstance(ctx, MarketContextData)
        assert ctx.spy_close == 476.0
        assert ctx.vix_close == 12.5

    async def test_get_market_context_missing_date(self, provider):
        """get_market_context returns None for unknown date."""
        ctx = await provider.get_market_context(date(2023, 1, 1))
        assert ctx is None


class TestHistoricalCatalyst:
    """Tests for catalyst methods (always return None/False)."""

    async def test_earnings_returns_none(self, provider):
        """get_days_to_earnings always returns None for historical."""
        result = await provider.get_days_to_earnings("AAPL", date(2024, 1, 12))
        assert result is None

    async def test_sec_filing_returns_false(self, provider):
        """get_recent_sec_filing always returns False for historical."""
        result = await provider.get_recent_sec_filing("AAPL", date(2024, 1, 12))
        assert result is False


class TestHistoricalCacheManagement:
    """Tests for cache management."""

    async def test_clear_cache(self, provider):
        """clear_cache empties all caches."""
        # Warm up cache
        await provider.get_daily_bars("AAPL", date(2024, 1, 13), lookback_days=10)
        assert len(provider._cache) > 0

        provider.clear_cache()
        assert len(provider._cache) == 0
        assert provider._market_context_cache is None


class TestHistoricalProviderInit:
    """Tests for provider initialization."""

    def test_s3_mode(self, mock_s3_client):
        """S3 mode initializes correctly."""
        p = HistoricalDataProvider(s3_bucket="test-bucket", s3_client=mock_s3_client)
        assert p.s3_bucket == "test-bucket"
