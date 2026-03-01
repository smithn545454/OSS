"""Tests for backtest prefetch module."""

from __future__ import annotations

import io
from datetime import date
from unittest.mock import MagicMock

import pytest

pa = pytest.importorskip("pyarrow", reason="pyarrow not installed")
pq = pytest.importorskip("pyarrow.parquet", reason="pyarrow.parquet not installed")

from app.backtest.prefetch import (
    _compute_date_ranges,
    _download_one,
    _estimate_cache_size_mb,
    _generate_s3_keys,
    _generate_weekdays,
    prefetch_batch_data,
)


def _make_parquet_bytes() -> bytes:
    """Create minimal valid parquet bytes for testing."""
    table = pa.table({"x": pa.array([1, 2, 3])})
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


# ============================================================================
# _generate_weekdays
# ============================================================================


class TestGenerateWeekdays:
    def test_one_week(self):
        days = _generate_weekdays(date(2024, 1, 8), date(2024, 1, 12))
        assert len(days) == 5  # Mon-Fri
        assert all(d.weekday() < 5 for d in days)

    def test_skips_weekends(self):
        # Jan 6 (Sat) through Jan 14 (Sun) — only Mon-Fri included
        days = _generate_weekdays(date(2024, 1, 6), date(2024, 1, 14))
        assert len(days) == 5
        assert days[0] == date(2024, 1, 8)  # Monday
        assert days[-1] == date(2024, 1, 12)  # Friday

    def test_single_weekday(self):
        days = _generate_weekdays(date(2024, 1, 8), date(2024, 1, 8))
        assert len(days) == 1

    def test_single_weekend(self):
        days = _generate_weekdays(date(2024, 1, 6), date(2024, 1, 6))
        assert len(days) == 0

    def test_empty_range(self):
        days = _generate_weekdays(date(2024, 1, 12), date(2024, 1, 8))
        assert len(days) == 0


# ============================================================================
# _compute_date_ranges
# ============================================================================


class TestComputeDateRanges:
    def test_basic_ranges(self):
        batch = [date(2024, 6, 10), date(2024, 6, 14)]  # Mon-Fri
        ohlcv, iv, options, uv_vol = _compute_date_ranges(
            batch, ohlcv_lookback=7, iv_lookback=14,
        )

        # OHLCV starts 7 cal days before Jun 10, ends Jun 14
        assert ohlcv[0] == date(2024, 6, 3)  # Mon
        assert ohlcv[-1] == date(2024, 6, 14)

        # IV starts further back
        assert iv[0] < ohlcv[0]

        # Options: only batch days (no forward scan — exit resolver reads on-demand)
        assert options == batch

        # UV volume: weekdays before batch start (not including batch days)
        assert all(d < date(2024, 6, 10) for d in uv_vol)
        assert all(d.weekday() < 5 for d in uv_vol)

    def test_only_weekdays(self):
        batch = [date(2024, 1, 8)]
        ohlcv, iv, options, uv_vol = _compute_date_ranges(batch, ohlcv_lookback=7)
        assert all(d.weekday() < 5 for d in ohlcv)
        assert all(d.weekday() < 5 for d in iv)
        assert all(d.weekday() < 5 for d in options)
        assert all(d.weekday() < 5 for d in uv_vol)


# ============================================================================
# _generate_s3_keys
# ============================================================================


class TestGenerateS3Keys:
    def test_correct_prefixes(self):
        ohlcv = [date(2024, 1, 8)]
        iv = [date(2024, 1, 8)]
        options = [date(2024, 1, 8)]
        keys = _generate_s3_keys(ohlcv, iv, options)

        assert "stock-ohlcv/date=2024-01-08/data.parquet" in keys
        assert "iv-history/date=2024-01-08/data.parquet" in keys
        assert "options-chains/date=2024-01-08/data.parquet" in keys
        assert "market-context/data.parquet" in keys

    def test_deduplicates(self):
        ohlcv = [date(2024, 1, 8), date(2024, 1, 8)]
        keys = _generate_s3_keys(ohlcv, [], [])
        stock_keys = [k for k in keys if k.startswith("stock-ohlcv")]
        assert len(stock_keys) == 1

    def test_always_includes_market_context(self):
        keys = _generate_s3_keys([], [], [])
        assert "market-context/data.parquet" in keys


# ============================================================================
# _download_one
# ============================================================================


class TestDownloadOne:
    def test_successful_download(self):
        parquet_bytes = _make_parquet_bytes()
        mock_s3 = MagicMock()
        body_mock = MagicMock()
        body_mock.read.return_value = parquet_bytes
        mock_s3.get_object.return_value = {"Body": body_mock}

        key, result = _download_one(mock_s3, "bucket", "test/key.parquet")
        assert key == "test/key.parquet"
        assert result is not None
        assert result.num_rows == 3

    def test_missing_file(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey: not found")

        key, result = _download_one(mock_s3, "bucket", "missing/key.parquet")
        assert key == "missing/key.parquet"
        assert result is None

    def test_404_error(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("404 Not Found")

        key, result = _download_one(mock_s3, "bucket", "gone.parquet")
        assert result is None


# ============================================================================
# prefetch_batch_data
# ============================================================================


class TestPrefetchBatchData:
    def test_returns_populated_cache(self):
        parquet_bytes = _make_parquet_bytes()
        mock_s3 = MagicMock()
        body_mock = MagicMock()
        body_mock.read.return_value = parquet_bytes
        mock_s3.get_object.return_value = {"Body": body_mock}

        cache = prefetch_batch_data(
            s3_bucket="test-bucket",
            batch_days=[date(2024, 1, 8)],
            s3_client=mock_s3,
            ohlcv_lookback=3,
            iv_lookback=5,
            max_workers=2,
        )

        # Should have downloaded files (parquet keys) even though they
        # don't have the expected schema for IV/UV post-processing
        assert len(cache) > 0
        assert "market-context/data.parquet" in cache

    def test_handles_all_missing(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey: not found")

        cache = prefetch_batch_data(
            s3_bucket="test-bucket",
            batch_days=[date(2024, 1, 8)],
            s3_client=mock_s3,
            ohlcv_lookback=3,
            iv_lookback=5,
            max_workers=2,
        )

        # No S3 files found, no post-processing indexes created
        assert len(cache) == 0

    def test_s3_called_with_correct_bucket(self):
        parquet_bytes = _make_parquet_bytes()
        mock_s3 = MagicMock()
        body_mock = MagicMock()
        body_mock.read.return_value = parquet_bytes
        mock_s3.get_object.return_value = {"Body": body_mock}

        prefetch_batch_data(
            s3_bucket="my-bucket",
            batch_days=[date(2024, 1, 8)],
            s3_client=mock_s3,
            ohlcv_lookback=1,
            iv_lookback=1,
            max_workers=1,
        )

        # Every call should use the correct bucket
        for call in mock_s3.get_object.call_args_list:
            assert call.kwargs.get("Bucket") or call[1].get("Bucket") == "my-bucket"


# ============================================================================
# _estimate_cache_size_mb
# ============================================================================


class TestEstimateCacheSize:
    def test_with_tables(self):
        table = pa.table({"x": pa.array(range(1000))})
        cache = {"key1": table, "key2": table}
        mb = _estimate_cache_size_mb(cache)
        assert mb > 0

    def test_empty_cache(self):
        assert _estimate_cache_size_mb({}) == 0.0


# ============================================================================
# HistoricalDataProvider shared_cache integration
# ============================================================================


class TestSharedCacheIntegration:
    def test_shared_cache_hits(self):
        from app.core.historical_data_provider import HistoricalDataProvider

        table = pa.table({
            "ticker": pa.array(["AAPL"], type=pa.string()),
            "open": pa.array([180.0]),
            "high": pa.array([185.0]),
            "low": pa.array([178.0]),
            "close": pa.array([183.0]),
            "volume": pa.array([50_000_000], type=pa.int64()),
            "vwap": pa.array([182.0]),
        })

        shared = {"stock-ohlcv/date=2024-01-08/data.parquet": table}

        mock_s3 = MagicMock()
        provider = HistoricalDataProvider(
            s3_bucket="test-bucket",
            s3_client=mock_s3,
            shared_cache=shared,
        )

        # Should hit cache, not S3
        result = provider._read_parquet("stock-ohlcv/date=2024-01-08/data.parquet")
        assert result is not None
        mock_s3.get_object.assert_not_called()

    def test_default_empty_cache(self):
        from app.core.historical_data_provider import HistoricalDataProvider

        mock_s3 = MagicMock()
        provider = HistoricalDataProvider(
            s3_bucket="test-bucket",
            s3_client=mock_s3,
        )

        assert len(provider._cache) == 0

    def test_multiple_providers_share_cache(self):
        from app.core.historical_data_provider import HistoricalDataProvider

        table = pa.table({"x": pa.array([1])})
        shared = {"key1": table}

        mock_s3 = MagicMock()
        p1 = HistoricalDataProvider(
            s3_bucket="test", s3_client=mock_s3, shared_cache=shared,
        )
        p2 = HistoricalDataProvider(
            s3_bucket="test", s3_client=mock_s3, shared_cache=shared,
        )

        # Both reference the same dict
        assert p1._cache is p2._cache

    def test_cache_miss_falls_through_to_s3(self):
        from app.core.historical_data_provider import HistoricalDataProvider

        shared = {}  # Empty shared cache

        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        provider = HistoricalDataProvider(
            s3_bucket="test-bucket",
            s3_client=mock_s3,
            shared_cache=shared,
        )

        # Cache miss should attempt S3
        result = provider._read_parquet("missing-key.parquet")
        assert result is None
        mock_s3.get_object.assert_called_once()
