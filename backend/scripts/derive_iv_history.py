#!/usr/bin/env python3
"""Derive IV history from options chain parquet files.

For each date, loads the options parquet and for each ticker emits the
multi-tenor + 25Δ skew IV metrics consumed by Convex Mode Stage 3
(Volatility Mispricing). The extraction logic lives in
``app.convex.iv_extraction`` so it stays pure-function and unit-testable.

Output schema (extended for Convex Mode):
    - ticker, date
    - atm_iv             — legacy field (back-compat with old readers)
    - iv_30d             — front-month tenor (~30 DTE ATM)
    - iv_60d             — 60-day tenor for term structure
    - iv_25d_put         — 25-delta put for skew
    - iv_25d_call        — 25-delta call for skew

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
import sys
import time
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

# Add backend to path so app.convex.iv_extraction imports cleanly when run
# as a standalone script.
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.convex.iv_extraction import (  # noqa: E402
    ContractRow,
    IVMetrics,
    extract_iv_metrics,
    summarise_completeness,
)

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
        ("iv_30d", pa.float64()),
        ("iv_60d", pa.float64()),
        ("iv_25d_put", pa.float64()),
        ("iv_25d_call", pa.float64()),
    ]
)


def _table_to_contract_rows(table: pa.Table) -> list[ContractRow]:
    """Convert a PyArrow options-chains table into ContractRow objects."""
    tickers = table.column("ticker").to_pylist()
    deltas = table.column("delta").to_pylist()
    bid_ivs = table.column("bid_iv").to_pylist()
    ask_ivs = table.column("ask_iv").to_pylist()
    expiry_dates = table.column("expiry_date").to_pylist()

    rows: list[ContractRow] = []
    for i in range(len(tickers)):
        rows.append(
            ContractRow(
                ticker=tickers[i],
                expiry_date=str(expiry_dates[i]) if expiry_dates[i] else "",
                delta=deltas[i],
                bid_iv=bid_ivs[i],
                ask_iv=ask_ivs[i],
            )
        )
    return rows


def compute_iv_metrics_for_date(table: pa.Table, trade_date: str) -> list[IVMetrics]:
    """Extract per-ticker multi-tenor + skew IV metrics from an options table.

    Delegates to ``app.convex.iv_extraction.extract_iv_metrics`` so the
    selection logic stays pure-function and unit-testable.
    """
    rows = _table_to_contract_rows(table)
    return extract_iv_metrics(rows, trade_date)


def _metrics_to_arrow(metrics: list[IVMetrics]) -> pa.Table:
    """Convert IVMetrics records to a PyArrow table matching IV_HISTORY_SCHEMA."""
    return pa.table(
        {
            "ticker": pa.array([m.ticker for m in metrics], type=pa.string()),
            "date": pa.array([m.date for m in metrics], type=pa.string()),
            "atm_iv": pa.array([m.atm_iv for m in metrics], type=pa.float64()),
            "iv_30d": pa.array([m.iv_30d for m in metrics], type=pa.float64()),
            "iv_60d": pa.array([m.iv_60d for m in metrics], type=pa.float64()),
            "iv_25d_put": pa.array([m.iv_25d_put for m in metrics], type=pa.float64()),
            "iv_25d_call": pa.array(
                [m.iv_25d_call for m in metrics], type=pa.float64()
            ),
        },
        schema=IV_HISTORY_SCHEMA,
    )


def process_local(input_dir: Path, output_dir: Optional[Path], dry_run: bool) -> int:
    """Process local parquet files."""
    dates_processed = 0
    all_metrics: list[IVMetrics] = []

    for date_dir in sorted(input_dir.iterdir()):
        if not date_dir.is_dir() or not date_dir.name.startswith("date="):
            continue

        trade_date = date_dir.name.split("=")[1]
        parquet_file = date_dir / "data.parquet"
        if not parquet_file.exists():
            continue

        table = pq.ParquetFile(parquet_file).read()
        metrics = compute_iv_metrics_for_date(table, trade_date)

        if dry_run:
            logger.info(f"  {trade_date}: {len(metrics)} tickers with IV metrics")
            all_metrics.extend(metrics)
            dates_processed += 1
            continue

        if metrics and output_dir:
            out_path = output_dir / "iv-history" / f"date={trade_date}" / "data.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(_metrics_to_arrow(metrics), out_path, compression="snappy")

        all_metrics.extend(metrics)
        dates_processed += 1
        if dates_processed % 50 == 0:
            logger.info(f"  Processed {dates_processed} dates...")

    # Coverage summary so the operator can see column-by-column completeness
    # before kicking off the DynamoDB backfill.
    if all_metrics:
        report = summarise_completeness(all_metrics)
        coverage = report.coverage_pct()
        logger.info("Coverage across all processed dates:")
        for col, pct in coverage.items():
            logger.info(f"  {col:14s}: {pct:5.1f}%  ({_count(report, col)} / {report.total_rows})")

    return dates_processed


def _count(report, col: str) -> int:
    """Look up the count attribute on CompletenessReport for a column name."""
    return getattr(report, f"rows_with_{col}")


def process_s3(bucket: str, dry_run: bool) -> int:
    """Process S3 parquet files with parallel workers."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import boto3

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
        metrics = compute_iv_metrics_for_date(table, trade_date)

        if not dry_run and metrics:
            out_buf = io.BytesIO()
            pq.write_table(_metrics_to_arrow(metrics), out_buf, compression="snappy")
            thread_s3.put_object(
                Bucket=bucket,
                Key=f"iv-history/date={trade_date}/data.parquet",
                Body=out_buf.getvalue(),
            )

        return trade_date, len(metrics)

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
    logger.info("COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Dates processed: {dates_processed}")
    logger.info(f"Elapsed time:    {elapsed:.1f}s")


if __name__ == "__main__":
    main()
