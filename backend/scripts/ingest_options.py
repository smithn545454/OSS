#!/usr/bin/env python3
"""Ingest historical options chain CSVs into date-partitioned parquet files.

Reads from ``Historic Data/{YEAR}_q{Q}_option_chain_{hash}/`` directories.
Each CSV file contains option chain data for one or more tickers.

Output path:
    {output_dir}/options-chains/date=YYYY-MM-DD/data.parquet
    s3://{bucket}/options-chains/date=YYYY-MM-DD/data.parquet

CSV format (16 columns, no header):
    underlying, trade_date, strike, expiry_date, option_type,
    last_price, bid, ask, bid_iv, ask_iv, open_interest, volume,
    delta, gamma, vega, theta

Usage:
    python scripts/ingest_options.py \\
        --input-dir "/path/to/Historic Data" \\
        --output-dir /tmp/backtest

    python scripts/ingest_options.py \\
        --input-dir "/path/to/Historic Data" \\
        --s3-bucket oss-dev-backtest-123456789
"""

from __future__ import annotations

import argparse
import csv
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

# CSV column indices (no header row)
COL_TICKER = 0
COL_TRADE_DATE = 1
COL_STRIKE = 2
COL_EXPIRY_DATE = 3
COL_OPTION_TYPE = 4
COL_LAST_PRICE = 5
COL_BID = 6
COL_ASK = 7
COL_BID_IV = 8
COL_ASK_IV = 9
COL_OI = 10
COL_VOLUME = 11
COL_DELTA = 12
COL_GAMMA = 13
COL_VEGA = 14
COL_THETA = 15

# Output parquet schema
OPTIONS_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("trade_date", pa.string()),
        ("strike", pa.float64()),
        ("expiry_date", pa.string()),
        ("option_type", pa.string()),
        ("last_price", pa.float64()),
        ("bid", pa.float64()),
        ("ask", pa.float64()),
        ("bid_iv", pa.float64()),
        ("ask_iv", pa.float64()),
        ("open_interest", pa.int64()),
        ("volume", pa.int64()),
        ("delta", pa.float64()),
        ("gamma", pa.float64()),
        ("vega", pa.float64()),
        ("theta", pa.float64()),
    ]
)


def safe_float(val: str, default: float = 0.0) -> float:
    try:
        v = float(val.strip()) if val.strip() else default
        return v if v == v else default  # NaN check
    except (ValueError, TypeError):
        return default


def safe_int(val: str, default: int = 0) -> int:
    try:
        return int(float(val.strip())) if val.strip() else default
    except (ValueError, TypeError):
        return default


def parse_csv_file(filepath: Path) -> dict[str, list[dict]]:
    """Parse a single CSV file, grouping rows by trade date.

    Returns:
        Dict mapping date string to list of row dicts.
    """
    by_date: dict[str, list[dict]] = defaultdict(list)
    errors = 0

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row_num, row in enumerate(reader, 1):
            if len(row) < 16:
                errors += 1
                continue
            try:
                trade_date = row[COL_TRADE_DATE].strip()
                if not trade_date or len(trade_date) < 8:
                    continue

                by_date[trade_date].append(
                    {
                        "ticker": row[COL_TICKER].strip().upper(),
                        "trade_date": trade_date,
                        "strike": safe_float(row[COL_STRIKE]),
                        "expiry_date": row[COL_EXPIRY_DATE].strip(),
                        "option_type": row[COL_OPTION_TYPE].strip().lower(),
                        "last_price": safe_float(row[COL_LAST_PRICE]),
                        "bid": safe_float(row[COL_BID]),
                        "ask": safe_float(row[COL_ASK]),
                        "bid_iv": safe_float(row[COL_BID_IV]),
                        "ask_iv": safe_float(row[COL_ASK_IV]),
                        "open_interest": safe_int(row[COL_OI]),
                        "volume": safe_int(row[COL_VOLUME]),
                        "delta": safe_float(row[COL_DELTA]),
                        "gamma": safe_float(row[COL_GAMMA]),
                        "vega": safe_float(row[COL_VEGA]),
                        "theta": safe_float(row[COL_THETA]),
                    }
                )
            except Exception:
                errors += 1
                continue

    if errors > 0:
        logger.warning(f"  {errors} parse errors in {filepath.name}")

    return dict(by_date)


def write_parquet_local(records: list[dict], output_path: Path) -> None:
    """Write records to a local parquet file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {col: pa.array([r[col] for r in records], type=OPTIONS_SCHEMA.field(col).type) for col in OPTIONS_SCHEMA.names},
        schema=OPTIONS_SCHEMA,
    )
    pq.write_table(table, output_path, compression="snappy")


def write_parquet_s3(records: list[dict], bucket: str, s3_key: str) -> None:
    """Write records to S3 as parquet."""
    import boto3

    table = pa.table(
        {col: pa.array([r[col] for r in records], type=OPTIONS_SCHEMA.field(col).type) for col in OPTIONS_SCHEMA.names},
        schema=OPTIONS_SCHEMA,
    )
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    buf.seek(0)
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=s3_key, Body=buf.getvalue())


def discover_csv_files(input_dir: Path) -> list[Path]:
    """Find all CSV files in the input directory tree."""
    files = []
    for root, _dirs, filenames in os.walk(input_dir):
        for fn in filenames:
            if fn.endswith(".csv"):
                files.append(Path(root) / fn)
    return sorted(files)


def main():
    parser = argparse.ArgumentParser(
        description="Ingest historical options chain CSVs into parquet"
    )
    parser.add_argument("--input-dir", type=Path, required=True, help="Root directory with CSV files")
    parser.add_argument("--output-dir", type=Path, help="Local output directory")
    parser.add_argument("--s3-bucket", help="S3 bucket for output")
    parser.add_argument("--dry-run", action="store_true", help="Count records without writing")
    args = parser.parse_args()

    if not args.dry_run and not args.output_dir and not args.s3_bucket:
        parser.error("Specify --output-dir or --s3-bucket (or --dry-run)")

    csv_files = discover_csv_files(args.input_dir)
    logger.info(f"Found {len(csv_files)} CSV files")

    if not csv_files:
        logger.error("No CSV files found!")
        sys.exit(1)

    overall_start = time.time()
    all_by_date: dict[str, list[dict]] = defaultdict(list)
    total_rows = 0

    for i, csv_file in enumerate(csv_files, 1):
        logger.info(f"[{i}/{len(csv_files)}] Parsing {csv_file.name}...")
        by_date = parse_csv_file(csv_file)
        for dt, rows in by_date.items():
            all_by_date[dt].extend(rows)
            total_rows += len(rows)

    dates = sorted(all_by_date.keys())
    logger.info(f"Parsed {total_rows:,} rows across {len(dates)} dates")
    logger.info(f"Date range: {dates[0]} to {dates[-1]}")

    if args.dry_run:
        logger.info("DRY RUN complete")
        return

    written = 0
    for dt in dates:
        records = all_by_date[dt]
        if args.s3_bucket:
            s3_key = f"options-chains/date={dt}/data.parquet"
            write_parquet_s3(records, args.s3_bucket, s3_key)
        elif args.output_dir:
            out_path = args.output_dir / "options-chains" / f"date={dt}" / "data.parquet"
            write_parquet_local(records, out_path)
        written += 1

        if written % 50 == 0:
            logger.info(f"  Written {written}/{len(dates)} dates...")

    elapsed = time.time() - overall_start
    logger.info(f"\n{'='*60}")
    logger.info(f"COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"CSV files:      {len(csv_files)}")
    logger.info(f"Total rows:     {total_rows:,}")
    logger.info(f"Trading dates:  {len(dates)}")
    logger.info(f"Date range:     {dates[0]} to {dates[-1]}")
    logger.info(f"Elapsed time:   {elapsed:.1f}s")


if __name__ == "__main__":
    main()
