"""Backtest data prefetch — pre-materializes all S3 parquets for a batch.

Downloads stock OHLCV, IV history, options chains, and market context
parquet files into a shared in-memory cache dict. This cache is injected
into HistoricalDataProvider instances so that every _read_parquet() call
within the batch is an instant cache hit.

Post-processing builds pre-computed indexes:
- Combined IV table + ticker index (eliminates 252-date iteration per ticker)
- UV volume index (pre-aggregated 20-day rolling avg volumes per contract)

Uses ThreadPoolExecutor for parallel S3 downloads.
"""

from __future__ import annotations

import io
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Optional

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Columns needed for UV volume lookback (minimal read to save memory).
# In backtest parquets, "ticker" is the underlying (e.g. "AAPL"), and
# each row is one contract. We only need ticker + volume to compute
# the per-underlying average volume that HistoricalUVScanner expects.
_UV_VOLUME_COLUMNS = ["ticker", "volume"]


def _generate_weekdays(start: date, end: date) -> list[date]:
    """Generate all weekday dates in [start, end] range (inclusive)."""
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon-Fri
            days.append(current)
        current += timedelta(days=1)
    return days


def _compute_date_ranges(
    batch_days: list[date],
    ohlcv_lookback: int = 70,
    iv_lookback: int = 260,
    uv_lookback: int = 30,
) -> tuple[list[date], list[date], list[date], list[date]]:
    """Compute the date ranges needed for OHLCV, IV, options, and UV volume data.

    Args:
        batch_days: Trading days in this batch (e.g. 5 days).
        ohlcv_lookback: Calendar days before earliest batch day for OHLCV.
        iv_lookback: Calendar days before earliest batch day for IV history.
        uv_lookback: Calendar days before earliest batch day for UV volume
                     lookback (20 trading days ~ 28 calendar days, use 30).

    Returns:
        Tuple of (ohlcv_dates, iv_dates, options_dates, uv_volume_dates).
    """
    earliest = min(batch_days)
    latest = max(batch_days)

    ohlcv_start = earliest - timedelta(days=ohlcv_lookback)
    ohlcv_dates = _generate_weekdays(ohlcv_start, latest)

    iv_start = earliest - timedelta(days=iv_lookback)
    iv_dates = _generate_weekdays(iv_start, latest)

    # Only prefetch full options for batch days (pipeline stages 1-7).
    # Exit resolution reads forward on-demand via _read_options_chain_lite().
    options_dates = list(batch_days)

    # UV scanner needs 20-day rolling volume lookback per ticker.
    # Prefetch these with column filtering (5 cols only) to avoid
    # 10,956 on-demand S3 reads per day during UV scanning.
    uv_start = earliest - timedelta(days=uv_lookback)
    uv_volume_dates = [
        d for d in _generate_weekdays(uv_start, earliest - timedelta(days=1))
        if d not in set(batch_days)  # Don't duplicate batch-day options
    ]

    return ohlcv_dates, iv_dates, options_dates, uv_volume_dates


def _generate_s3_keys(
    ohlcv_dates: list[date],
    iv_dates: list[date],
    options_dates: list[date],
) -> list[str]:
    """Generate all unique S3 keys to prefetch (full reads)."""
    keys: set[str] = set()

    for d in ohlcv_dates:
        keys.add(f"stock-ohlcv/date={d.isoformat()}/data.parquet")

    for d in iv_dates:
        keys.add(f"iv-history/date={d.isoformat()}/data.parquet")

    for d in options_dates:
        keys.add(f"options-chains/date={d.isoformat()}/data.parquet")

    # Market context: single file, always needed
    keys.add("market-context/data.parquet")

    return sorted(keys)


def _download_one(
    s3_client: Any,
    bucket: str,
    key: str,
    columns: Optional[list[str]] = None,
) -> tuple[str, Optional[Any]]:
    """Download and parse a single parquet file from S3.

    Args:
        columns: If provided, only read these columns (for memory savings).

    Returns (s3_key, pyarrow_table_or_None). Returns None for missing files.
    """
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        buf = io.BytesIO(obj["Body"].read())
        pf = pq.ParquetFile(buf)
        if columns:
            available = set(pf.schema.names)
            cols = [c for c in columns if c in available]
            table = pf.read(columns=cols) if cols else pf.read()
        else:
            table = pf.read()
        return (key, table)
    except Exception as e:
        if "NoSuchKey" in str(e) or "404" in str(e):
            return (key, None)  # Expected: holidays, gaps
        logger.warning(f"Prefetch error for s3://{bucket}/{key}: {e}")
        return (key, None)


def _estimate_cache_size_mb(cache: dict[str, Any]) -> float:
    """Rough estimate of cache memory usage in MB."""
    total_bytes = 0
    for table in cache.values():
        if hasattr(table, "nbytes"):
            total_bytes += table.nbytes
    return total_bytes / (1024 * 1024)


def _build_ticker_index(table: Any) -> dict[str, np.ndarray]:
    """Build {ticker: int32_row_indices} from a PyArrow table."""
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


def _build_combined_iv_index(cache: dict[str, Any]) -> None:
    """Concatenate all IV history tables and build a ticker index.

    Stores results in cache under special keys:
    - ``__combined_iv__``: Single concatenated PyArrow table
    - ``__iv_ticker_index__``: {ticker: int32_row_indices}

    Skips tables that don't have expected columns (ticker, atm_iv).
    """
    iv_tables = []
    for key, table in cache.items():
        if (
            key.startswith("iv-history/")
            and table is not None
            and hasattr(table, "num_rows")
            and table.num_rows > 0
            and "ticker" in table.column_names
        ):
            # Add trade_date column if not present (extract from S3 key)
            if "trade_date" not in table.column_names:
                date_str = key.split("date=")[1].split("/")[0]
                date_col = pa.array([date_str] * table.num_rows, type=pa.string())
                table = table.append_column("trade_date", date_col)
                cache[key] = table  # Update cache with augmented table
            iv_tables.append(table)

    if not iv_tables:
        return

    try:
        combined = pa.concat_tables(iv_tables, promote_options="default")
        cache["__combined_iv__"] = combined
        cache["__iv_ticker_index__"] = _build_ticker_index(combined)
        logger.info(
            f"Combined IV index: {combined.num_rows} rows, "
            f"{len(cache['__iv_ticker_index__'])} tickers"
        )
    except Exception as e:
        logger.warning(f"Could not build combined IV index: {e}")


def _build_uv_volume_index(
    s3_client: Any,
    s3_bucket: str,
    uv_volume_dates: list[date],
    cache: dict[str, Any],
    max_workers: int = 20,
) -> None:
    """Pre-compute UV scanner's 20-day average volume per underlying ticker.

    In backtest mode, HistoricalUVScanner._compute_avg_volume() returns
    ``{underlying_ticker: avg_volume}`` where avg is across ALL contracts
    for that underlying across 20 days. This function pre-computes that
    same structure by downloading options files with column filtering
    (ticker + volume only) and aggregating.

    Raw UV tables are NOT kept in cache (freed after aggregation).
    """
    if not uv_volume_dates:
        return

    uv_keys = [
        f"options-chains/date={d.isoformat()}/data.parquet" for d in uv_volume_dates
    ]

    # Download with column filtering (2 cols — ~10 MB in-memory each vs ~100 MB full)
    uv_tables: list[Any] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _download_one, s3_client, s3_bucket, key, _UV_VOLUME_COLUMNS
            ): key
            for key in uv_keys
        }
        for future in as_completed(futures):
            key, table = future.result()
            if table is not None:
                uv_tables.append(table)

    logger.info(f"UV volume prefetch: {len(uv_tables)}/{len(uv_keys)} files downloaded")

    if not uv_tables:
        return

    # Aggregate: accumulate ALL contract volumes per underlying ticker
    # (matches HistoricalUVScanner._compute_avg_volume behavior)
    volume_sums: dict[str, float] = defaultdict(float)
    volume_counts: dict[str, int] = defaultdict(int)

    for table in uv_tables:
        if table.num_rows == 0:
            continue
        if "ticker" not in table.column_names:
            continue
        tickers = table.column("ticker").to_pylist()
        if "volume" in table.column_names:
            volumes = table.column("volume").to_pylist()
        else:
            volumes = [0] * table.num_rows

        for i in range(table.num_rows):
            vol = volumes[i] or 0
            if vol <= 0:
                continue
            ticker = tickers[i]
            volume_sums[ticker] += vol
            volume_counts[ticker] += 1

    # Build final index: {underlying_ticker: avg_volume_per_contract_per_day}
    uv_index: dict[str, float] = {
        ticker: volume_sums[ticker] / volume_counts[ticker]
        for ticker in volume_sums
    }

    cache["__uv_volume_index__"] = uv_index
    logger.info(
        f"UV volume index: {len(uv_index)} tickers, "
        f"from {len(uv_tables)} dates"
    )

    # Free raw UV tables (not stored in main cache)
    del uv_tables


def prefetch_batch_data(
    s3_bucket: str,
    batch_days: list[date],
    s3_client: Any = None,
    ohlcv_lookback: int = 70,
    iv_lookback: int = 260,
    max_workers: int = 20,
) -> dict[str, Any]:
    """Pre-download all parquet files needed for a batch into a shared cache.

    Uses ThreadPoolExecutor for parallel S3 downloads. The returned dict
    should be passed as ``shared_cache`` to HistoricalDataProvider instances.

    Post-processing builds pre-computed indexes:
    - Combined IV history table + ticker index (for O(1) IV lookups)
    - UV volume index (pre-aggregated 20-day avg volumes per contract)

    Args:
        s3_bucket: S3 bucket name.
        batch_days: List of trading dates in this batch.
        s3_client: Optional boto3 S3 client (created if None).
        ohlcv_lookback: Calendar days lookback for stock OHLCV.
        iv_lookback: Calendar days lookback for IV history.
        max_workers: Max parallel S3 download threads.

    Returns:
        Dict of {s3_key: pyarrow.Table} plus special index keys.
    """
    if s3_client is None:
        import boto3

        s3_client = boto3.client("s3")

    ohlcv_dates, iv_dates, options_dates, uv_volume_dates = _compute_date_ranges(
        batch_days,
        ohlcv_lookback=ohlcv_lookback,
        iv_lookback=iv_lookback,
    )

    keys = _generate_s3_keys(ohlcv_dates, iv_dates, options_dates)
    logger.info(
        f"Prefetch: {len(keys)} S3 keys for {len(batch_days)} days "
        f"(OHLCV: {len(ohlcv_dates)}, IV: {len(iv_dates)}, "
        f"Options: {len(options_dates)}, UV lookback: {len(uv_volume_dates)})"
    )

    cache: dict[str, Any] = {}
    downloaded = 0
    missing = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_download_one, s3_client, s3_bucket, key): key
            for key in keys
        }

        for future in as_completed(futures):
            key, table = future.result()
            if table is not None:
                cache[key] = table
                downloaded += 1
            else:
                missing += 1

    logger.info(
        f"Prefetch download: {downloaded} files, "
        f"{missing} missing (holidays/gaps), "
        f"cache size ~{_estimate_cache_size_mb(cache):.0f} MB"
    )

    # Post-processing: build pre-computed indexes
    _build_combined_iv_index(cache)
    _build_uv_volume_index(s3_client, s3_bucket, uv_volume_dates, cache, max_workers)

    logger.info(
        f"Prefetch complete: cache size ~{_estimate_cache_size_mb(cache):.0f} MB "
        f"(includes indexes)"
    )

    return cache
