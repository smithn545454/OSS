#!/usr/bin/env python3
"""Ingest historical options chain CSVs into date-partitioned parquet files on S3.

Uses pandas for fast CSV reading (~600K rows/s vs 65K with Python csv module).
Processes batches of tickers at a time, groups by date, writes parquet, uploads.

Source layout:
    Historic Data/{YEAR}_q{Q}_option_chain_{hash}/{TICKER}_{YEAR}_q{Q}_option_chain.txt

Output:
    s3://{bucket}/options-chains/date=YYYY-MM-DD/data.parquet

Usage:
    python scripts/ingest_options.py \\
        --input-dir "/path/to/Historic Data" \\
        --s3-bucket oss-dev-backtest-982534389101 \\
        --all-quarters

    python scripts/ingest_options.py \\
        --input-dir "/path/to/Historic Data" \\
        --s3-bucket oss-dev-backtest-982534389101 \\
        --quarter 2024_q1
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# CSV columns (no header in source files, ticker comes from filename)
CSV_COLUMNS = [
    "trade_date", "strike", "expiry_date", "option_type",
    "last_price", "bid", "ask", "bid_iv", "ask_iv",
    "open_interest", "volume", "delta", "gamma", "vega", "theta", "rho",
]

# Parquet schema (rho excluded)
PARQUET_COLUMNS = [
    "ticker", "trade_date", "strike", "expiry_date", "option_type",
    "last_price", "bid", "ask", "bid_iv", "ask_iv",
    "open_interest", "volume", "delta", "gamma", "vega", "theta",
]

OPTIONS_SCHEMA = pa.schema([
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
])

# How many ticker files to read at once before flushing to per-date accumulators.
# ~100 tickers * ~15K rows avg = ~1.5M rows per batch, ~200 MB RAM.
BATCH_SIZE = 100


def extract_ticker(filepath: Path) -> str:
    """AAPL_2024_q1_option_chain.txt -> AAPL"""
    match = re.match(r"^([A-Z]+)_\d{4}_q\d", filepath.stem, re.IGNORECASE)
    return match.group(1).upper() if match else filepath.stem.split("_")[0].upper()


def discover_quarter_dirs(input_dir: Path) -> list[tuple[str, Path]]:
    quarters = []
    for entry in input_dir.iterdir():
        if entry.is_dir():
            match = re.match(r"^(\d{4}_q\d)_option_chain", entry.name)
            if match:
                quarters.append((match.group(1), entry))
    return sorted(quarters)


def discover_data_files(
    quarter_dir: Path, ticker_filter: Optional[set[str]] = None
) -> list[Path]:
    files = []
    for fn in os.listdir(quarter_dir):
        if not (fn.endswith(".csv") or fn.endswith(".txt")):
            continue
        if fn.startswith("."):
            continue
        fp = quarter_dir / fn
        if not fp.is_file():
            continue
        if ticker_filter and extract_ticker(fp) not in ticker_filter:
            continue
        files.append(fp)
    return sorted(files)


def read_ticker_csv(filepath: Path) -> pd.DataFrame:
    """Read a single ticker CSV with pandas (C engine, ~600K rows/s)."""
    ticker = extract_ticker(filepath)
    try:
        df = pd.read_csv(
            filepath,
            header=None,
            names=CSV_COLUMNS,
            dtype={
                "trade_date": str,
                "expiry_date": str,
                "option_type": str,
            },
            on_bad_lines="skip",
            engine="c",
        )
    except Exception as e:
        logger.warning(f"  Failed to read {filepath.name}: {e}")
        return pd.DataFrame(columns=["ticker"] + CSV_COLUMNS)

    df.insert(0, "ticker", ticker)
    # Normalize option_type to lowercase
    df["option_type"] = df["option_type"].str.strip().str.lower()
    # Drop rho column (not in parquet schema)
    df.drop(columns=["rho"], errors="ignore", inplace=True)
    # Coerce numeric columns (handles NaN gracefully)
    for col in ["strike", "last_price", "bid", "ask", "bid_iv", "ask_iv",
                 "delta", "gamma", "vega", "theta"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    for col in ["open_interest", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    return df


def df_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    """Convert a DataFrame to snappy-compressed parquet bytes."""
    # Ensure column order matches schema
    df = df[PARQUET_COLUMNS]
    table = pa.Table.from_pandas(df, schema=OPTIONS_SCHEMA, preserve_index=False)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def upload_to_s3(s3_client, bucket: str, key: str, data: bytes) -> None:
    s3_client.put_object(Bucket=bucket, Key=key, Body=data)


def process_quarter(
    quarter_label: str,
    quarter_dir: Path,
    s3_bucket: Optional[str],
    output_dir: Optional[Path],
    ticker_filter: Optional[set[str]],
    dry_run: bool,
) -> tuple[int, int, int]:
    """Process one quarter: read CSVs in batches, group by date, upload parquet."""
    data_files = discover_data_files(quarter_dir, ticker_filter)
    if not data_files:
        logger.warning(f"  No data files in {quarter_dir}")
        return 0, 0, 0

    logger.info(f"  Found {len(data_files)} ticker files")
    q_start = time.time()

    # Accumulate per-date DataFrames across batches
    date_frames: dict[str, list[pd.DataFrame]] = {}
    total_rows = 0
    files_done = 0

    # Process in batches of BATCH_SIZE ticker files
    for batch_start in range(0, len(data_files), BATCH_SIZE):
        batch_files = data_files[batch_start:batch_start + BATCH_SIZE]
        batch_dfs = []

        for fp in batch_files:
            df = read_ticker_csv(fp)
            if len(df) > 0:
                batch_dfs.append(df)

        files_done += len(batch_files)

        if not batch_dfs:
            continue

        # Concatenate batch and group by trade_date
        batch_df = pd.concat(batch_dfs, ignore_index=True)
        total_rows += len(batch_df)

        for trade_date, group_df in batch_df.groupby("trade_date"):
            td = str(trade_date)
            if td not in date_frames:
                date_frames[td] = []
            date_frames[td].append(group_df)

        # Free memory
        del batch_df, batch_dfs

        elapsed = time.time() - q_start
        rate = total_rows / elapsed if elapsed > 0 else 0
        logger.info(
            f"  [{files_done}/{len(data_files)}] "
            f"{total_rows:,} rows, {rate:,.0f} rows/s"
        )

    if dry_run:
        dates_count = len(date_frames)
        logger.info(f"  [dry-run] {dates_count} dates, {total_rows:,} rows")
        return files_done, dates_count, total_rows

    # Phase 2: Convert accumulated date groups to parquet and upload
    logger.info(f"  Uploading {len(date_frames)} dates to S3...")
    upload_start = time.time()

    import boto3
    s3_client = boto3.client("s3")

    dates_written = 0
    rows_written = 0

    def upload_date(trade_date: str, frames: list[pd.DataFrame]) -> tuple[str, int]:
        combined = pd.concat(frames, ignore_index=True)
        parquet_bytes = df_to_parquet_bytes(combined)
        row_count = len(combined)

        if s3_bucket:
            upload_to_s3(s3_client, s3_bucket,
                         f"options-chains/date={trade_date}/data.parquet",
                         parquet_bytes)
        elif output_dir:
            out = output_dir / "options-chains" / f"date={trade_date}" / "data.parquet"
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "wb") as f:
                f.write(parquet_bytes)

        return trade_date, row_count

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(upload_date, td, frames): td
            for td, frames in sorted(date_frames.items())
        }
        for future in as_completed(futures):
            try:
                td, rc = future.result()
                dates_written += 1
                rows_written += rc
                if dates_written % 20 == 0 or dates_written == len(date_frames):
                    elapsed = time.time() - upload_start
                    logger.info(
                        f"  Uploaded {dates_written}/{len(date_frames)} dates "
                        f"({rows_written:,} rows, {elapsed:.0f}s)"
                    )
            except Exception as e:
                td = futures[future]
                logger.error(f"  Failed date {td}: {e}")

    # Free accumulated frames
    date_frames.clear()

    total_elapsed = time.time() - q_start
    logger.info(
        f"  Quarter done: {files_done} files, {dates_written} dates, "
        f"{rows_written:,} rows in {total_elapsed:.0f}s"
    )
    return files_done, dates_written, rows_written


def main():
    parser = argparse.ArgumentParser(
        description="Ingest historical options CSVs to date-partitioned parquet"
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--s3-bucket")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tickers", help="Comma-separated ticker filter")
    parser.add_argument("--quarter", help="Single quarter (e.g. 2024_q1)")
    parser.add_argument("--all-quarters", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and not args.output_dir and not args.s3_bucket:
        parser.error("Specify --output-dir or --s3-bucket (or --dry-run)")
    if not args.quarter and not args.all_quarters:
        parser.error("Specify --quarter or --all-quarters")

    ticker_filter = None
    if args.tickers:
        ticker_filter = {t.strip().upper() for t in args.tickers.split(",")}
        logger.info(f"Filtering to {len(ticker_filter)} tickers")

    all_quarters = discover_quarter_dirs(args.input_dir)
    if not all_quarters:
        logger.error(f"No quarter dirs in {args.input_dir}")
        sys.exit(1)

    logger.info(f"Available: {', '.join(q[0] for q in all_quarters)}")

    if args.quarter:
        quarters = [(q, p) for q, p in all_quarters if q == args.quarter]
        if not quarters:
            logger.error(f"Quarter {args.quarter} not found")
            sys.exit(1)
    else:
        quarters = all_quarters

    overall_start = time.time()
    g_files = g_dates = g_rows = 0

    for qi, (ql, qd) in enumerate(quarters, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"QUARTER {qi}/{len(quarters)}: {ql}")
        logger.info(f"{'='*60}")

        files, dates, rows = process_quarter(
            ql, qd, args.s3_bucket, args.output_dir, ticker_filter, args.dry_run
        )
        g_files += files
        g_dates += dates
        g_rows += rows

    elapsed = time.time() - overall_start
    logger.info(f"\n{'='*60}")
    logger.info("COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Quarters:      {len(quarters)}")
    logger.info(f"Ticker files:  {g_files:,}")
    logger.info(f"Trading dates: {g_dates}")
    logger.info(f"Total rows:    {g_rows:,}")
    logger.info(f"Elapsed:       {elapsed:.0f}s ({elapsed/60:.1f}m)")


if __name__ == "__main__":
    main()
