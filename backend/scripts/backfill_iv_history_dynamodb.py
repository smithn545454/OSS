#!/usr/bin/env python3
"""Backfill IV history from parquet files into DynamoDB.

Reads IV history parquet files (produced by derive_iv_history.py) and writes
them to the oss-dev-iv-history DynamoDB table. This enables IV percentile
calculations in the live pipeline immediately, without waiting 20+ days
for daily accumulation.

Prerequisites:
    1. Run derive_iv_history.py to produce IV history parquet files from
       historical options chain data.
    2. Ensure AWS credentials are configured (us-west-1 region).

Usage:
    # From local parquet files
    python scripts/backfill_iv_history_dynamodb.py \
        --input-dir /tmp/backtest/iv-history

    # From S3
    python scripts/backfill_iv_history_dynamodb.py \
        --s3-bucket oss-dev-backtest-123456789

    # Dry run (count records without writing)
    python scripts/backfill_iv_history_dynamodb.py \
        --input-dir /tmp/backtest/iv-history --dry-run

    # Limit to specific tickers
    python scripts/backfill_iv_history_dynamodb.py \
        --input-dir /tmp/backtest/iv-history --tickers AAPL,MSFT,TSLA

    # Limit to last N days
    python scripts/backfill_iv_history_dynamodb.py \
        --input-dir /tmp/backtest/iv-history --days 90
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def read_parquet_records(
    input_dir: Optional[Path] = None,
    s3_bucket: Optional[str] = None,
    min_date: Optional[str] = None,
    ticker_filter: Optional[set[str]] = None,
) -> list[dict]:
    """Read IV history records from parquet files.

    Args:
        input_dir: Local directory with date-partitioned parquet files
        s3_bucket: S3 bucket with iv-history/ prefix
        min_date: Minimum date to include (YYYY-MM-DD)
        ticker_filter: Optional set of tickers to include

    Returns:
        List of dicts with ticker, date, atm_iv keys
    """
    import pyarrow.parquet as pq

    all_records: list[dict] = []

    if input_dir:
        for date_dir in sorted(input_dir.iterdir()):
            if not date_dir.is_dir() or not date_dir.name.startswith("date="):
                continue

            trade_date = date_dir.name.split("=")[1]
            if min_date and trade_date < min_date:
                continue

            parquet_file = date_dir / "data.parquet"
            if not parquet_file.exists():
                continue

            table = pq.read_table(parquet_file)
            tickers = table.column("ticker").to_pylist()
            dates = table.column("date").to_pylist()
            ivs = table.column("atm_iv").to_pylist()

            for i in range(len(tickers)):
                ticker = tickers[i]
                if ticker_filter and ticker not in ticker_filter:
                    continue
                if ivs[i] is not None and ivs[i] > 0:
                    all_records.append({
                        "ticker": ticker,
                        "date": dates[i],
                        "atm_iv": ivs[i],
                    })

    elif s3_bucket:
        import boto3

        s3 = boto3.client("s3")
        paginator = s3.get_paginator("list_objects_v2")

        date_keys: list[tuple[str, str]] = []
        for page in paginator.paginate(
            Bucket=s3_bucket, Prefix="iv-history/", Delimiter="/"
        ):
            for cp in page.get("CommonPrefixes", []):
                part = cp["Prefix"].rstrip("/").split("/")[-1]
                if part.startswith("date="):
                    trade_date = part.split("=")[1]
                    if min_date and trade_date < min_date:
                        continue
                    date_keys.append((trade_date, f"iv-history/{part}/data.parquet"))

        logger.info(f"Found {len(date_keys)} date partitions in S3")

        for trade_date, s3_key in sorted(date_keys):
            try:
                obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
                buf = io.BytesIO(obj["Body"].read())
                table = pq.read_table(buf)

                tickers = table.column("ticker").to_pylist()
                dates = table.column("date").to_pylist()
                ivs = table.column("atm_iv").to_pylist()

                for i in range(len(tickers)):
                    ticker = tickers[i]
                    if ticker_filter and ticker not in ticker_filter:
                        continue
                    if ivs[i] is not None and ivs[i] > 0:
                        all_records.append({
                            "ticker": ticker,
                            "date": dates[i],
                            "atm_iv": ivs[i],
                        })
            except Exception as e:
                logger.warning(f"Error reading {s3_key}: {e}")

    return all_records


async def write_to_dynamodb(records: list[dict], dry_run: bool = False) -> int:
    """Write IV history records to DynamoDB.

    Args:
        records: List of dicts with ticker, date, atm_iv
        dry_run: If True, count without writing

    Returns:
        Number of records written
    """
    if dry_run:
        # Count unique tickers and date range
        tickers = set(r["ticker"] for r in records)
        dates = sorted(set(r["date"] for r in records))
        logger.info(f"DRY RUN: Would write {len(records)} records")
        logger.info(f"  Tickers: {len(tickers)}")
        logger.info(f"  Date range: {dates[0]} to {dates[-1]}" if dates else "  No dates")
        return 0

    # Import after path setup
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-1")
    from app.core.schemas import IVHistory
    from app.db.tables import IVHistoryTable

    # Build IVHistory objects
    iv_records = []
    for r in records:
        iv_records.append(IVHistory(
            ticker=r["ticker"],
            date=r["date"],
            atm_iv=r["atm_iv"],
        ))

    # Write in batches of 500 (put_batch handles 25-item DynamoDB limit internally)
    written = 0
    batch_size = 500
    for i in range(0, len(iv_records), batch_size):
        batch = iv_records[i : i + batch_size]
        try:
            await IVHistoryTable.put_batch(batch)
            written += len(batch)
            if written % 5000 == 0 or written == len(iv_records):
                logger.info(f"  Written {written}/{len(iv_records)} records")
        except Exception as e:
            logger.error(f"Error writing batch at offset {i}: {e}")

    return written


async def verify_backfill(sample_tickers: list[str]) -> None:
    """Verify backfill by checking a few tickers."""
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-1")
    from app.db.tables import IVHistoryTable

    logger.info("\nVerification:")
    for ticker in sample_tickers[:5]:
        records = await IVHistoryTable.list_by_ticker(ticker, limit=100)
        if records:
            dates = [r.date for r in records]
            logger.info(
                f"  {ticker}: {len(records)} days "
                f"({min(dates)} to {max(dates)}), "
                f"latest IV={records[0].atm_iv:.4f}"
            )
        else:
            logger.info(f"  {ticker}: No records")


async def main_async(args: argparse.Namespace) -> None:
    """Async main entry point."""
    # Parse ticker filter
    ticker_filter = None
    if args.tickers:
        ticker_filter = set(t.strip().upper() for t in args.tickers.split(","))
        logger.info(f"Filtering to {len(ticker_filter)} tickers")

    # Calculate min date
    min_date = None
    if args.days:
        min_date = (date.today() - timedelta(days=args.days)).isoformat()
        logger.info(f"Limiting to last {args.days} days (from {min_date})")

    # Read records
    start = time.time()
    logger.info("Reading IV history from parquet files...")
    records = read_parquet_records(
        input_dir=args.input_dir,
        s3_bucket=args.s3_bucket,
        min_date=min_date,
        ticker_filter=ticker_filter,
    )
    read_elapsed = time.time() - start
    logger.info(f"Read {len(records)} records in {read_elapsed:.1f}s")

    if not records:
        logger.warning("No records found. Check input path and filters.")
        return

    # Summarize
    tickers = sorted(set(r["ticker"] for r in records))
    dates = sorted(set(r["date"] for r in records))
    logger.info(f"  Unique tickers: {len(tickers)}")
    logger.info(f"  Date range: {dates[0]} to {dates[-1]}")
    logger.info(f"  Avg records/ticker: {len(records) / len(tickers):.1f}")

    # Write to DynamoDB
    write_start = time.time()
    written = await write_to_dynamodb(records, dry_run=args.dry_run)
    write_elapsed = time.time() - write_start

    if not args.dry_run:
        logger.info(f"Wrote {written} records in {write_elapsed:.1f}s")

        # Verify
        sample = tickers[:5]
        await verify_backfill(sample)

    total_elapsed = time.time() - start
    logger.info(f"\nTotal elapsed: {total_elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill IV history from parquet files into DynamoDB"
    )
    parser.add_argument("--input-dir", type=Path, help="Local iv-history directory")
    parser.add_argument("--s3-bucket", help="S3 bucket with iv-history/ prefix")
    parser.add_argument("--dry-run", action="store_true", help="Count without writing")
    parser.add_argument("--tickers", help="Comma-separated ticker filter (e.g., AAPL,MSFT)")
    parser.add_argument("--days", type=int, help="Limit to last N days")
    args = parser.parse_args()

    if not args.input_dir and not args.s3_bucket:
        parser.error("Specify --input-dir or --s3-bucket")

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
