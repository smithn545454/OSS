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


_OPTIONAL_COLUMNS = ("iv_30d", "iv_60d", "iv_25d_put", "iv_25d_call")


def _table_to_records(
    table, ticker_filter: Optional[set[str]]
) -> list[dict]:
    """Convert a PyArrow IV-history table into records, including new columns
    when present. Old parquet files that only have ``atm_iv`` still work.
    """
    column_names = set(table.schema.names)
    tickers = table.column("ticker").to_pylist()
    dates = table.column("date").to_pylist()
    atm_ivs = table.column("atm_iv").to_pylist()

    optional_lists: dict[str, list] = {}
    for col in _OPTIONAL_COLUMNS:
        if col in column_names:
            optional_lists[col] = table.column(col).to_pylist()

    records: list[dict] = []
    for i in range(len(tickers)):
        ticker = tickers[i]
        if ticker_filter and ticker not in ticker_filter:
            continue
        atm_iv = atm_ivs[i]
        if atm_iv is None or atm_iv <= 0:
            # ATM IV is the foundational column — skip rows missing it.
            continue
        rec = {
            "ticker": ticker,
            "date": dates[i],
            "atm_iv": atm_iv,
        }
        for col, values in optional_lists.items():
            v = values[i]
            if v is not None and v > 0:
                rec[col] = v
        records.append(rec)
    return records


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
        List of dicts with ticker, date, atm_iv keys plus optional
        iv_30d / iv_60d / iv_25d_put / iv_25d_call when those columns
        are present in the parquet files (Convex Mode multi-tenor +
        skew backfill).
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
            all_records.extend(_table_to_records(table, ticker_filter))

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
                all_records.extend(_table_to_records(table, ticker_filter))
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
        # Count unique tickers, date range, and per-column coverage.
        tickers = set(r["ticker"] for r in records)
        dates = sorted(set(r["date"] for r in records))
        logger.info(f"DRY RUN: Would write {len(records)} records")
        logger.info(f"  Tickers: {len(tickers)}")
        logger.info(f"  Date range: {dates[0]} to {dates[-1]}" if dates else "  No dates")
        if records:
            for col in ("atm_iv", "iv_30d", "iv_60d", "iv_25d_put", "iv_25d_call"):
                count = sum(1 for r in records if r.get(col) is not None)
                pct = (count / len(records)) * 100
                logger.info(f"  {col:14s}: {pct:5.1f}%  ({count} / {len(records)})")
        return 0

    # Import after path setup
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-1")
    from app.core.schemas import IVHistory
    from app.db.tables import IVHistoryTable

    # Build IVHistory objects, populating multi-tenor + skew fields when
    # available in the source parquet (Convex Mode Phase 0.5 backfill).
    iv_records = []
    for r in records:
        iv_records.append(IVHistory(
            ticker=r["ticker"],
            date=r["date"],
            atm_iv=r["atm_iv"],
            iv_30d=r.get("iv_30d"),
            iv_60d=r.get("iv_60d"),
            iv_25d_put=r.get("iv_25d_put"),
            iv_25d_call=r.get("iv_25d_call"),
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
            latest = records[0]
            extra = []
            if latest.iv_30d is not None:
                extra.append(f"iv_30d={latest.iv_30d:.4f}")
            if latest.iv_60d is not None:
                extra.append(f"iv_60d={latest.iv_60d:.4f}")
            if latest.iv_25d_put is not None and latest.iv_25d_call is not None:
                extra.append(
                    f"skew={latest.iv_25d_put:.4f}/{latest.iv_25d_call:.4f}"
                )
            extras_str = (" " + " ".join(extra)) if extra else ""
            logger.info(
                f"  {ticker}: {len(records)} days "
                f"({min(dates)} to {max(dates)}), "
                f"latest atm={latest.atm_iv:.4f}{extras_str}"
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
