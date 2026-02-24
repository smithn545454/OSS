#!/usr/bin/env python3
"""Ingest stock OHLCV data from Polygon grouped daily API into parquet files.

Uses Polygon grouped daily endpoint:
    GET /v2/aggs/grouped/locale/us/market/stocks/{date}
Returns all tickers in one call per date.

Output path:
    {output_dir}/stock-ohlcv/date=YYYY-MM-DD/data.parquet
    s3://{bucket}/stock-ohlcv/date=YYYY-MM-DD/data.parquet

Usage:
    python scripts/ingest_stock_ohlcv.py \\
        --start-date 2023-10-02 \\
        --end-date 2026-02-21 \\
        --output-dir /tmp/backtest \\
        --api-key YOUR_POLYGON_KEY

    python scripts/ingest_stock_ohlcv.py \\
        --start-date 2023-10-02 \\
        --end-date 2026-02-21 \\
        --s3-bucket oss-dev-backtest-123456789
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# S&P 500 tickers (top ~100 for initial ingestion + SPY)
# Full list can be loaded from a file; this is a minimal set
SP500_CORE = {"SPY"}  # Will accept all tickers from grouped API

OHLCV_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("date", pa.string()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("vwap", pa.float64()),
    ]
)


def get_trading_days(start: date, end: date) -> list[date]:
    """Generate weekday dates in [start, end]."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon-Fri
            days.append(current)
        current += timedelta(days=1)
    return days


async def fetch_grouped_daily(
    client: httpx.AsyncClient,
    api_key: str,
    trade_date: date,
) -> list[dict]:
    """Fetch grouped daily bars for all tickers on a given date."""
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{trade_date.isoformat()}"
    params = {"adjusted": "true", "apiKey": api_key}

    try:
        response = await client.get(url, params=params, timeout=30.0)
        response.raise_for_status()
        data = response.json()

        records = []
        for bar in data.get("results", []):
            ticker = bar.get("T", "")
            if not ticker:
                continue
            records.append(
                {
                    "ticker": ticker,
                    "date": trade_date.isoformat(),
                    "open": bar.get("o", 0.0),
                    "high": bar.get("h", 0.0),
                    "low": bar.get("l", 0.0),
                    "close": bar.get("c", 0.0),
                    "volume": int(bar.get("v", 0)),
                    "vwap": bar.get("vw", 0.0),
                }
            )
        return records
    except Exception as e:
        logger.warning(f"Error fetching {trade_date}: {e}")
        return []


def write_parquet_local(records: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {col: pa.array([r[col] for r in records], type=OHLCV_SCHEMA.field(col).type) for col in OHLCV_SCHEMA.names},
        schema=OHLCV_SCHEMA,
    )
    pq.write_table(table, output_path, compression="snappy")


def write_parquet_s3(records: list[dict], bucket: str, s3_key: str) -> None:
    import boto3

    table = pa.table(
        {col: pa.array([r[col] for r in records], type=OHLCV_SCHEMA.field(col).type) for col in OHLCV_SCHEMA.names},
        schema=OHLCV_SCHEMA,
    )
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    buf.seek(0)
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=s3_key, Body=buf.getvalue())


async def ingest_date_range(
    api_key: str,
    trading_days: list[date],
    output_dir: Optional[Path],
    s3_bucket: Optional[str],
    dry_run: bool = False,
) -> int:
    """Fetch and write OHLCV data for all trading days."""
    written = 0
    semaphore = asyncio.Semaphore(5)  # Concurrent requests

    async with httpx.AsyncClient() as client:
        for i, trade_date in enumerate(trading_days, 1):
            async with semaphore:
                records = await fetch_grouped_daily(client, api_key, trade_date)

            if not records:
                logger.warning(f"  [{i}/{len(trading_days)}] No data for {trade_date}")
                continue

            if dry_run:
                logger.info(f"  [{i}/{len(trading_days)}] {trade_date}: {len(records)} tickers")
                continue

            if s3_bucket:
                s3_key = f"stock-ohlcv/date={trade_date.isoformat()}/data.parquet"
                write_parquet_s3(records, s3_bucket, s3_key)
            elif output_dir:
                out_path = output_dir / "stock-ohlcv" / f"date={trade_date.isoformat()}" / "data.parquet"
                write_parquet_local(records, out_path)

            written += 1
            if written % 50 == 0:
                logger.info(f"  Written {written}/{len(trading_days)} dates ({len(records)} tickers/day)...")

    return written


def main():
    parser = argparse.ArgumentParser(description="Ingest stock OHLCV from Polygon grouped daily API")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", type=Path, help="Local output directory")
    parser.add_argument("--s3-bucket", help="S3 bucket for output")
    parser.add_argument("--api-key", help="Polygon API key (or set POLYGON_API_KEY env)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("POLYGON_API_KEY", "")
    if not api_key:
        parser.error("Polygon API key required (--api-key or POLYGON_API_KEY env)")

    if not args.dry_run and not args.output_dir and not args.s3_bucket:
        parser.error("Specify --output-dir or --s3-bucket (or --dry-run)")

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    trading_days = get_trading_days(start, end)

    logger.info(f"Date range: {start} to {end}")
    logger.info(f"Trading days: {len(trading_days)}")

    overall_start = time.time()
    written = asyncio.run(
        ingest_date_range(api_key, trading_days, args.output_dir, args.s3_bucket, args.dry_run)
    )

    elapsed = time.time() - overall_start
    logger.info(f"\n{'='*60}")
    logger.info(f"COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Trading days:  {len(trading_days)}")
    logger.info(f"Written:       {written}")
    logger.info(f"Elapsed time:  {elapsed:.1f}s")


if __name__ == "__main__":
    main()
