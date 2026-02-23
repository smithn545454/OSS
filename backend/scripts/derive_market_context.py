#!/usr/bin/env python3
"""Derive market context (SPY + VIX) from stock OHLCV parquet files.

Reads stock-ohlcv date partitions to extract SPY daily data, and uses
Polygon API for VIX (index ticker I:VIX). Writes a single consolidated
parquet file.

Output path:
    {output_dir}/market-context/data.parquet
    s3://{bucket}/market-context/data.parquet

Usage:
    python scripts/derive_market_context.py \\
        --input-dir /tmp/backtest/stock-ohlcv \\
        --output-dir /tmp/backtest

    python scripts/derive_market_context.py \\
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

import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Output schema
MARKET_CONTEXT_SCHEMA = pa.schema([
    ("date", pa.string()),
    ("spy_close", pa.float64()),
    ("spy_change_pct", pa.float64()),
    ("vix_close", pa.float64()),
])


def extract_spy_from_ohlcv(input_dir: Path) -> list[dict]:
    """Extract SPY daily data from stock-ohlcv parquet files.

    Returns list sorted by date, each with: {date, spy_close}
    """
    results = []

    for date_dir in sorted(input_dir.iterdir()):
        if not date_dir.is_dir() or not date_dir.name.startswith("date="):
            continue

        trade_date = date_dir.name.split("=")[1]
        parquet_file = date_dir / "data.parquet"
        if not parquet_file.exists():
            continue

        table = pq.read_table(parquet_file, columns=["ticker", "close"])
        tickers = table.column("ticker").to_pylist()
        closes = table.column("close").to_pylist()

        for ticker, close in zip(tickers, closes):
            if ticker == "SPY":
                results.append({"date": trade_date, "spy_close": close})
                break

    results.sort(key=lambda r: r["date"])
    return results


def extract_spy_from_s3(bucket: str) -> list[dict]:
    """Extract SPY data from S3 stock-ohlcv parquet files."""
    import boto3

    s3 = boto3.client("s3")
    results = []

    # List date partitions
    paginator = s3.get_paginator("list_objects_v2")
    dates = set()
    for page in paginator.paginate(Bucket=bucket, Prefix="stock-ohlcv/", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            part = cp["Prefix"].rstrip("/").split("/")[-1]
            if part.startswith("date="):
                dates.add(part.split("=")[1])

    for trade_date in sorted(dates):
        s3_key = f"stock-ohlcv/date={trade_date}/data.parquet"
        try:
            obj = s3.get_object(Bucket=bucket, Key=s3_key)
            buf = io.BytesIO(obj["Body"].read())
            table = pq.read_table(buf, columns=["ticker", "close"])
            tickers = table.column("ticker").to_pylist()
            closes = table.column("close").to_pylist()

            for ticker, close in zip(tickers, closes):
                if ticker == "SPY":
                    results.append({"date": trade_date, "spy_close": close})
                    break
        except Exception as e:
            logger.warning(f"Error reading {trade_date}: {e}")

    results.sort(key=lambda r: r["date"])
    return results


async def fetch_vix_history(
    api_key: str,
    start_date: str,
    end_date: str,
) -> dict[str, float]:
    """Fetch VIX daily closes from Polygon.

    Uses ticker I:VIX with the daily bars endpoint.

    Returns:
        Dict mapping date string to VIX close price
    """
    import httpx

    url = f"https://api.polygon.io/v2/aggs/ticker/I:VIX/range/1/day/{start_date}/{end_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": api_key}

    vix_data: dict[str, float] = {}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, timeout=30.0)
            response.raise_for_status()
            data = response.json()

            for bar in data.get("results", []):
                ts = bar.get("t", 0)
                date_str = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                vix_data[date_str] = bar.get("c", 0.0)

            logger.info(f"Fetched {len(vix_data)} VIX daily bars")
        except Exception as e:
            logger.error(f"Error fetching VIX data: {e}")

    return vix_data


def compute_market_context(
    spy_data: list[dict],
    vix_data: dict[str, float],
) -> list[dict]:
    """Compute market context from SPY + VIX data.

    Calculates spy_change_pct as day-over-day close change.
    """
    results = []
    prev_close = None

    for record in spy_data:
        trade_date = record["date"]
        spy_close = record["spy_close"]

        # Day-over-day change
        if prev_close and prev_close > 0:
            spy_change_pct = round(((spy_close - prev_close) / prev_close) * 100, 4)
        else:
            spy_change_pct = 0.0

        vix_close = vix_data.get(trade_date, 0.0)

        results.append({
            "date": trade_date,
            "spy_close": spy_close,
            "spy_change_pct": spy_change_pct,
            "vix_close": vix_close,
        })

        prev_close = spy_close

    return results


def write_market_context_local(records: list[dict], output_dir: Path) -> str:
    """Write consolidated market context parquet locally."""
    mc_dir = output_dir / "market-context"
    mc_dir.mkdir(parents=True, exist_ok=True)
    out_path = mc_dir / "data.parquet"

    table = pa.table(
        {
            "date": pa.array([r["date"] for r in records], type=pa.string()),
            "spy_close": pa.array([r["spy_close"] for r in records], type=pa.float64()),
            "spy_change_pct": pa.array([r["spy_change_pct"] for r in records], type=pa.float64()),
            "vix_close": pa.array([r["vix_close"] for r in records], type=pa.float64()),
        },
        schema=MARKET_CONTEXT_SCHEMA,
    )
    pq.write_table(table, out_path, compression="snappy")
    return str(out_path)


def write_market_context_s3(records: list[dict], bucket: str) -> str:
    """Write consolidated market context parquet to S3."""
    import boto3

    s3_key = "market-context/data.parquet"
    table = pa.table(
        {
            "date": pa.array([r["date"] for r in records], type=pa.string()),
            "spy_close": pa.array([r["spy_close"] for r in records], type=pa.float64()),
            "spy_change_pct": pa.array([r["spy_change_pct"] for r in records], type=pa.float64()),
            "vix_close": pa.array([r["vix_close"] for r in records], type=pa.float64()),
        },
        schema=MARKET_CONTEXT_SCHEMA,
    )
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    buf.seek(0)

    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=s3_key, Body=buf.getvalue())
    return f"s3://{bucket}/{s3_key}"


def main():
    parser = argparse.ArgumentParser(
        description="Derive market context (SPY/VIX) from stock OHLCV parquet"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Local directory containing stock-ohlcv/ date partitions",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Local output directory",
    )
    parser.add_argument(
        "--s3-bucket",
        help="S3 bucket (reads from and writes to S3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count SPY dates without writing",
    )
    args = parser.parse_args()

    if not args.input_dir and not args.s3_bucket:
        parser.error("Specify --input-dir or --s3-bucket")

    if not args.dry_run and not args.output_dir and not args.s3_bucket:
        parser.error("Specify --output-dir or --s3-bucket (or --dry-run)")

    overall_start = time.time()

    # Extract SPY data
    logger.info("Extracting SPY daily data from stock OHLCV...")
    if args.input_dir:
        spy_data = extract_spy_from_ohlcv(args.input_dir)
    else:
        spy_data = extract_spy_from_s3(args.s3_bucket)

    logger.info(f"Found {len(spy_data)} SPY trading days")
    if not spy_data:
        logger.error("No SPY data found!")
        sys.exit(1)

    if args.dry_run:
        logger.info(f"DRY RUN: Date range {spy_data[0]['date']} to {spy_data[-1]['date']}")
        return

    # Fetch VIX data from Polygon
    api_key = os.environ.get("POLYGON_API_KEY", "")
    if not api_key:
        try:
            from app.config import get_settings
            api_key = get_settings().polygon_api_key
        except Exception:
            logger.warning("No Polygon API key — VIX data will be 0.0")
            api_key = ""

    vix_data: dict[str, float] = {}
    if api_key:
        logger.info("Fetching VIX data from Polygon...")
        vix_data = asyncio.run(
            fetch_vix_history(
                api_key,
                spy_data[0]["date"],
                spy_data[-1]["date"],
            )
        )
    else:
        logger.warning("Skipping VIX fetch (no API key)")

    # Compute market context
    logger.info("Computing market context...")
    records = compute_market_context(spy_data, vix_data)

    # Write output
    if args.s3_bucket:
        path = write_market_context_s3(records, args.s3_bucket)
    elif args.output_dir:
        path = write_market_context_local(records, args.output_dir)

    elapsed = time.time() - overall_start
    logger.info(f"\n{'='*60}")
    logger.info(f"COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Trading days:    {len(records)}")
    logger.info(f"VIX data points: {len(vix_data)}")
    logger.info(f"Date range:      {records[0]['date']} to {records[-1]['date']}")
    logger.info(f"Written to:      {path}")
    logger.info(f"Elapsed time:    {elapsed:.1f}s")


if __name__ == "__main__":
    main()
