#!/usr/bin/env python3
"""Derive IV history from options chain parquet files.

For each date, loads the options parquet and for each ticker finds ATM
contracts (delta ~0.50, DTE 30-45) to compute daily ATM IV.

Output path:
    {output_dir}/iv-history/date=YYYY-MM-DD/data.parquet
    s3://{bucket}/iv-history/date=YYYY-MM-DD/data.parquet

Usage:
    python scripts/derive_iv_history.py \\
        --input-dir /tmp/backtest/options-chains \\
        --output-dir /tmp/backtest

    python scripts/derive_iv_history.py \\
        --s3-bucket oss-dev-backtest-123456789
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
from collections import defaultdict
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

IV_HISTORY_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("date", pa.string()),
        ("atm_iv", pa.float64()),
    ]
)


def compute_atm_iv_for_date(table: pa.Table, trade_date: str) -> list[dict]:
    """Extract per-ticker ATM IV from an options parquet table.

    ATM selection: contracts with |delta| between 0.35-0.65, DTE 20-60.
    Average bid_iv + ask_iv for selected contracts.
    """
    tickers = table.column("ticker").to_pylist()
    deltas = table.column("delta").to_pylist()
    bid_ivs = table.column("bid_iv").to_pylist()
    ask_ivs = table.column("ask_iv").to_pylist()
    expiry_dates = table.column("expiry_date").to_pylist()

    # Group by ticker
    ticker_ivs: dict[str, list[float]] = defaultdict(list)

    for i in range(len(tickers)):
        ticker = tickers[i]
        delta = deltas[i] if deltas[i] is not None else 0.0
        bid_iv = bid_ivs[i] if bid_ivs[i] is not None else 0.0
        ask_iv = ask_ivs[i] if ask_ivs[i] is not None else 0.0

        # ATM filter: |delta| between 0.35-0.65
        abs_delta = abs(delta)
        if abs_delta < 0.35 or abs_delta > 0.65:
            continue

        # DTE filter: 20-60 days
        expiry = str(expiry_dates[i]) if expiry_dates[i] else ""
        if not expiry or expiry <= trade_date:
            continue
        # Rough DTE calculation
        try:
            from datetime import date as dt

            exp_d = dt.fromisoformat(expiry)
            td_d = dt.fromisoformat(trade_date)
            dte = (exp_d - td_d).days
            if dte < 20 or dte > 60:
                continue
        except (ValueError, TypeError):
            continue

        # Average bid/ask IV
        mid_iv = (bid_iv + ask_iv) / 2 if (bid_iv + ask_iv) > 0 else bid_iv or ask_iv
        if mid_iv > 0:
            ticker_ivs[ticker].append(mid_iv)

    # Compute average IV per ticker
    results = []
    for ticker, ivs in ticker_ivs.items():
        if ivs:
            avg_iv = sum(ivs) / len(ivs)
            results.append(
                {
                    "ticker": ticker,
                    "date": trade_date,
                    "atm_iv": round(avg_iv, 6),
                }
            )

    return results


def process_local(input_dir: Path, output_dir: Optional[Path], dry_run: bool) -> int:
    """Process local parquet files."""
    dates_processed = 0

    for date_dir in sorted(input_dir.iterdir()):
        if not date_dir.is_dir() or not date_dir.name.startswith("date="):
            continue

        trade_date = date_dir.name.split("=")[1]
        parquet_file = date_dir / "data.parquet"
        if not parquet_file.exists():
            continue

        table = pq.ParquetFile(parquet_file).read()
        records = compute_atm_iv_for_date(table, trade_date)

        if dry_run:
            logger.info(f"  {trade_date}: {len(records)} tickers with ATM IV")
            dates_processed += 1
            continue

        if records and output_dir:
            out_path = output_dir / "iv-history" / f"date={trade_date}" / "data.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_table = pa.table(
                {
                    "ticker": pa.array([r["ticker"] for r in records], type=pa.string()),
                    "date": pa.array([r["date"] for r in records], type=pa.string()),
                    "atm_iv": pa.array([r["atm_iv"] for r in records], type=pa.float64()),
                },
                schema=IV_HISTORY_SCHEMA,
            )
            pq.write_table(out_table, out_path, compression="snappy")

        dates_processed += 1
        if dates_processed % 50 == 0:
            logger.info(f"  Processed {dates_processed} dates...")

    return dates_processed


def process_s3(bucket: str, dry_run: bool) -> int:
    """Process S3 parquet files with parallel workers."""
    import boto3
    from concurrent.futures import ThreadPoolExecutor, as_completed

    s3 = boto3.client("s3")

    # List date partitions
    paginator = s3.get_paginator("list_objects_v2")
    dates = set()
    for page in paginator.paginate(Bucket=bucket, Prefix="options-chains/", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            part = cp["Prefix"].rstrip("/").split("/")[-1]
            if part.startswith("date="):
                dates.add(part.split("=")[1])

    sorted_dates = sorted(dates)
    logger.info(f"  Found {len(sorted_dates)} dates to process")

    def process_one_date(trade_date: str) -> tuple[str, int]:
        s3_key = f"options-chains/date={trade_date}/data.parquet"
        # Each thread needs its own S3 client
        thread_s3 = boto3.client("s3")
        obj = thread_s3.get_object(Bucket=bucket, Key=s3_key)
        buf = io.BytesIO(obj["Body"].read())
        table = pq.ParquetFile(buf).read()
        records = compute_atm_iv_for_date(table, trade_date)

        if not dry_run and records:
            out_table = pa.table(
                {
                    "ticker": pa.array([r["ticker"] for r in records], type=pa.string()),
                    "date": pa.array([r["date"] for r in records], type=pa.string()),
                    "atm_iv": pa.array([r["atm_iv"] for r in records], type=pa.float64()),
                },
                schema=IV_HISTORY_SCHEMA,
            )
            out_buf = io.BytesIO()
            pq.write_table(out_table, out_buf, compression="snappy")
            thread_s3.put_object(
                Bucket=bucket,
                Key=f"iv-history/date={trade_date}/data.parquet",
                Body=out_buf.getvalue(),
            )

        return trade_date, len(records)

    dates_processed = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_one_date, d): d for d in sorted_dates}
        for future in as_completed(futures):
            try:
                td, count = future.result()
                dates_processed += 1
                if dates_processed % 50 == 0 or dates_processed == len(sorted_dates):
                    elapsed = time.time() - start
                    logger.info(
                        f"  [{dates_processed}/{len(sorted_dates)}] "
                        f"{elapsed:.0f}s elapsed"
                    )
            except Exception as e:
                td = futures[future]
                logger.warning(f"Error processing {td}: {e}")

    return dates_processed


def main():
    parser = argparse.ArgumentParser(description="Derive IV history from options parquet files")
    parser.add_argument("--input-dir", type=Path, help="Local options-chains directory")
    parser.add_argument("--output-dir", type=Path, help="Local output directory")
    parser.add_argument("--s3-bucket", help="S3 bucket (reads options-chains, writes iv-history)")
    parser.add_argument("--dry-run", action="store_true", help="Count without writing")
    args = parser.parse_args()

    if not args.input_dir and not args.s3_bucket:
        parser.error("Specify --input-dir or --s3-bucket")

    overall_start = time.time()

    if args.s3_bucket:
        logger.info(f"Processing from S3: {args.s3_bucket}")
        dates_processed = process_s3(args.s3_bucket, args.dry_run)
    else:
        logger.info(f"Processing from local: {args.input_dir}")
        dates_processed = process_local(args.input_dir, args.output_dir, args.dry_run)

    elapsed = time.time() - overall_start
    logger.info(f"\n{'='*60}")
    logger.info(f"COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Dates processed: {dates_processed}")
    logger.info(f"Elapsed time:    {elapsed:.1f}s")


if __name__ == "__main__":
    main()
