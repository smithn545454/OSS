"""Backtest data prefetch — pre-materializes all S3 parquets for a batch.

Downloads stock OHLCV, IV history, options chains, and market context
parquet files into a shared in-memory cache dict. This cache is injected
into HistoricalDataProvider instances so that every _read_parquet() call
within the batch is an instant cache hit.

Uses ThreadPoolExecutor for parallel S3 downloads.
"""

from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Optional

import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


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
) -> tuple[list[date], list[date], list[date]]:
    """Compute the date ranges needed for OHLCV, IV, and options data.

    Args:
        batch_days: Trading days in this batch (e.g. 5 days).
        ohlcv_lookback: Calendar days before earliest batch day for OHLCV
                        (60 trading days ~ 84 calendar days, use 70 buffer).
        iv_lookback: Calendar days before earliest batch day for IV history
                     (252 trading days ~ 353 calendar days, use 260 buffer).

    Returns:
        Tuple of (ohlcv_dates, iv_dates, options_dates).
        Options dates cover only the batch days (for pipeline stages 1-7).
        Exit resolution reads forward options data on-demand via
        HistoricalDataProvider._read_options_chain_lite() (column-filtered,
        ~5 MB vs ~13 MB per file).
    """
    earliest = min(batch_days)
    latest = max(batch_days)

    ohlcv_start = earliest - timedelta(days=ohlcv_lookback)
    ohlcv_dates = _generate_weekdays(ohlcv_start, latest)

    iv_start = earliest - timedelta(days=iv_lookback)
    iv_dates = _generate_weekdays(iv_start, latest)

    # Only prefetch options for batch days (pipeline stages 1-7).
    # Exit resolution forward-scans 60-130 days of options data per trade,
    # reading on-demand via _read_options_chain_lite() which column-filters
    # to ~5 MB/file and caches per provider instance.
    # Prefetching 99+ full options files (~13 MB each = ~1.3 GB) caused OOM
    # on the 3 GB Lambda.
    options_dates = list(batch_days)

    return ohlcv_dates, iv_dates, options_dates


def _generate_s3_keys(
    ohlcv_dates: list[date],
    iv_dates: list[date],
    options_dates: list[date],
) -> list[str]:
    """Generate all unique S3 keys to prefetch."""
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
) -> tuple[str, Optional[Any]]:
    """Download and parse a single parquet file from S3.

    Returns (s3_key, pyarrow_table_or_None). Returns None for missing files.
    """
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        buf = io.BytesIO(obj["Body"].read())
        table = pq.ParquetFile(buf).read()
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

    Only prefetches data needed for pipeline stages 1-7 (OHLCV lookback,
    IV lookback, options for batch days). Exit resolution forward options
    data is read on-demand by HistoricalDataProvider._read_options_chain_lite().

    Args:
        s3_bucket: S3 bucket name.
        batch_days: List of trading dates in this batch.
        s3_client: Optional boto3 S3 client (created if None).
        ohlcv_lookback: Calendar days lookback for stock OHLCV.
        iv_lookback: Calendar days lookback for IV history.
        max_workers: Max parallel S3 download threads.

    Returns:
        Dict of {s3_key: pyarrow.Table} for all successfully downloaded files.
    """
    if s3_client is None:
        import boto3

        s3_client = boto3.client("s3")

    ohlcv_dates, iv_dates, options_dates = _compute_date_ranges(
        batch_days,
        ohlcv_lookback=ohlcv_lookback,
        iv_lookback=iv_lookback,
    )

    keys = _generate_s3_keys(ohlcv_dates, iv_dates, options_dates)
    logger.info(
        f"Prefetch: {len(keys)} S3 keys for {len(batch_days)} days "
        f"(OHLCV: {len(ohlcv_dates)}, IV: {len(iv_dates)}, "
        f"Options: {len(options_dates)})"
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
        f"Prefetch complete: {downloaded} files downloaded, "
        f"{missing} missing (holidays/gaps), "
        f"cache size ~{_estimate_cache_size_mb(cache):.0f} MB"
    )

    return cache
