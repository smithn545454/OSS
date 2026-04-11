#!/usr/bin/env python3
"""Backfill technical indicators (ADX/RSI/MACD/±DI/EMA) for historical positions.

This script addresses the sparse coverage of Tier 2 technical indicators on
historical paper positions. Before running, only ~395 of 15,505 closed
positions have ADX/RSI/MACD populated in FeatureValueTable. After running,
all positions with available OHLC data will have these features.

Strategy:
1. Query all closed paper positions.
2. For each position, skip if `adx_14` is already present in
   FeatureValueTable.
3. Collect unique (ticker, entry_date) pairs that need backfill.
4. For each pair, fetch 60 days of OHLC data (need >= 29 bars for ADX).
5. Try S3 parquet first (fast, free); fall back to Polygon API for gaps.
6. Compute ADX, RSI, MACD, ±DI, EMA alignment, OBV trend via existing
   `app/services/technicals.py` functions.
7. Write results to FeatureValueTable under PK=EVAL#{eval_id}.

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/backfill_technical_indicators.py [--dry-run] [--limit N]

Runtime: ~25 minutes for 2,852 unique pairs across 15,110 positions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

# Add backend to sys.path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.data_provider import DailyBar  # noqa: E402
from app.services.technicals import (  # noqa: E402
    calculate_adx,
    calculate_ema,
    calculate_macd,
    calculate_obv_trend,
    calculate_rsi,
    classify_ema_alignment,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")
S3_BUCKET = os.environ.get("OSS_BACKTEST_BUCKET", "oss-dev-backtest-982534389101")

# Number of candidate weekdays to scan in S3 before giving up on a ticker/date
CANDIDATE_DAYS = 90
# Minimum bars needed to compute ADX(14) reliably
MIN_BARS_FOR_ADX = 29
# Polygon rate limit — requests per second
POLYGON_RATE_LIMIT = 10
# Cache key: (ticker, entry_date_str) -> list[DailyBar]
_bar_cache: dict[tuple[str, str], list[DailyBar]] = {}


# ============================================================================
# S3 OHLC reader (lightweight, standalone — no HistoricalDataProvider dep)
# ============================================================================


class S3BarsReader:
    """Reads daily OHLC bars from S3 parquet files.

    Caches read tables by date so that scanning a 60-day window for many
    tickers only reads each date's parquet once.
    """

    def __init__(self, bucket: str, region: str = "us-west-1") -> None:
        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=region)
        self._table_cache: dict[str, Any] = {}  # date -> pyarrow.Table (or None)

    def _read_date(self, date_str: str) -> Optional[Any]:
        if date_str in self._table_cache:
            return self._table_cache[date_str]
        try:
            import io
            import pyarrow.parquet as pq

            key = f"stock-ohlcv/date={date_str}/data.parquet"
            obj = self.s3.get_object(Bucket=self.bucket, Key=key)
            buf = io.BytesIO(obj["Body"].read())
            table = pq.ParquetFile(buf).read()
            self._table_cache[date_str] = table
            return table
        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
                self._table_cache[date_str] = None
                return None
            log.warning(f"S3 read error for {date_str}: {e}")
            self._table_cache[date_str] = None
            return None

    def get_bars(self, ticker: str, end_date: date, lookback_days: int = CANDIDATE_DAYS) -> list[DailyBar]:
        """Fetch daily bars for ticker ending strictly before end_date."""
        import pyarrow.compute as pc

        cache_key = (ticker, end_date.isoformat())
        if cache_key in _bar_cache:
            return _bar_cache[cache_key]

        bars: list[DailyBar] = []
        for i in range(1, lookback_days + 1):
            d = end_date - timedelta(days=i)
            if d.weekday() >= 5:
                continue  # Skip weekends
            date_str = d.isoformat()
            table = self._read_date(date_str)
            if table is None:
                continue
            filtered = table.filter(pc.field("ticker") == ticker)
            if filtered.num_rows == 0:
                continue
            row = filtered.slice(0, 1)
            bars.append(
                DailyBar(
                    ticker=ticker,
                    date=date_str,
                    open=float(row.column("open")[0].as_py()),
                    high=float(row.column("high")[0].as_py()),
                    low=float(row.column("low")[0].as_py()),
                    close=float(row.column("close")[0].as_py()),
                    volume=int(row.column("volume")[0].as_py()),
                    vwap=(
                        float(row.column("vwap")[0].as_py())
                        if "vwap" in row.column_names
                        else None
                    ),
                )
            )
            if len(bars) >= 60:
                break

        # Bars are collected most-recent-first; reverse to chronological order
        bars.reverse()
        _bar_cache[cache_key] = bars
        return bars


# ============================================================================
# Polygon fallback
# ============================================================================


async def fetch_bars_from_polygon(
    ticker: str,
    end_date: date,
    polygon_key: str,
    lookback_days: int = CANDIDATE_DAYS,
) -> list[DailyBar]:
    """Fetch daily bars from Polygon API for one ticker."""
    from app.services.polygon import PolygonClient

    start = end_date - timedelta(days=lookback_days)
    from_str = start.isoformat()
    to_str = (end_date - timedelta(days=1)).isoformat()

    async with PolygonClient(api_key=polygon_key) as polygon:
        try:
            bars = await polygon.get_daily_bars_parsed(ticker, from_str, to_str)
            return bars
        except Exception as e:
            log.warning(f"Polygon fetch failed for {ticker} to {to_str}: {e}")
            return []


# ============================================================================
# Feature computation
# ============================================================================


def compute_features_from_bars(bars: list[DailyBar]) -> dict[str, Any]:
    """Compute all technical features from a sequence of daily bars.

    Returns a dict of feature_name -> value, with None for features that
    can't be computed from the available bars.
    """
    features: dict[str, Any] = {}

    if len(bars) < MIN_BARS_FOR_ADX:
        log.debug(f"Insufficient bars ({len(bars)}) for ADX")
        return features

    closes = [b.close for b in bars]

    # ADX, +DI, -DI
    try:
        adx_result = calculate_adx(bars, period=14)
        if adx_result:
            features["adx_14"] = adx_result.adx
            features["plus_di"] = adx_result.plus_di
            features["minus_di"] = adx_result.minus_di
    except Exception as e:
        log.debug(f"ADX error: {e}")

    # RSI
    try:
        rsi = calculate_rsi(closes, period=14)
        if rsi is not None:
            features["rsi_14"] = rsi
    except Exception as e:
        log.debug(f"RSI error: {e}")

    # MACD
    try:
        macd_result = calculate_macd(closes)
        if macd_result and macd_result.histogram is not None:
            features["macd_histogram"] = macd_result.histogram
    except Exception as e:
        log.debug(f"MACD error: {e}")

    # EMAs for alignment classification
    try:
        ema_9 = calculate_ema(closes, 9)
        ema_21 = calculate_ema(closes, 21)
        ema_50 = calculate_ema(closes, 50)
        ema_200 = calculate_ema(closes, 200)
        if ema_9 is not None:
            features["ema_9"] = ema_9
        if ema_21 is not None:
            features["ema_21"] = ema_21
        if ema_50 is not None:
            features["ema_50"] = ema_50
        if ema_200 is not None:
            features["ema_200"] = ema_200

        if ema_9 is not None or ema_21 is not None:
            alignment = classify_ema_alignment(
                price=closes[-1],
                ema9=ema_9,
                ema21=ema_21,
                ema50=ema_50,
                ema200=ema_200,
            )
            if alignment:
                features["ema_alignment"] = str(alignment)
    except Exception as e:
        log.debug(f"EMA error: {e}")

    # OBV trend — returns {"obv": int, "trend": str} or None
    try:
        obv_result = calculate_obv_trend(bars)
        if obv_result and isinstance(obv_result, dict):
            trend = obv_result.get("trend")
            if trend:
                features["obv_trend"] = str(trend)
    except Exception as e:
        log.debug(f"OBV error: {e}")

    return features


# ============================================================================
# DynamoDB helpers
# ============================================================================


def decimal_to_float(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    return v


def write_features_to_fvt(
    fvt_table: Any,
    evaluation_id: str,
    features: dict[str, Any],
    dry_run: bool = False,
) -> int:
    """Write feature values to FeatureValueTable.

    Returns the number of features written. Uses batch_writer for efficiency.
    Existing values for the same feature are overwritten.
    """
    if not features:
        return 0
    if dry_run:
        return len(features)

    written = 0
    now = datetime.now(timezone.utc).isoformat()
    with fvt_table.batch_writer() as batch:
        for feat_name, feat_value in features.items():
            if feat_value is None:
                continue
            # Convert float -> Decimal for DynamoDB
            if isinstance(feat_value, float):
                feat_value = Decimal(str(feat_value))
            item = {
                "PK": f"EVAL#{evaluation_id}",
                "SK": f"FEATURE#{feat_name}",
                "evaluation_id": evaluation_id,
                "feature_name": feat_name,
                "value": feat_value,
                "computed_at": now,
            }
            batch.put_item(Item=item)
            written += 1
    return written


def check_already_has_adx(fvt_table: Any, evaluation_id: str) -> bool:
    """Return True if this evaluation already has adx_14 in FVT."""
    try:
        resp = fvt_table.get_item(
            Key={
                "PK": f"EVAL#{evaluation_id}",
                "SK": "FEATURE#adx_14",
            }
        )
        return "Item" in resp
    except Exception:
        return False


# ============================================================================
# Main flow
# ============================================================================


async def main_async(args: argparse.Namespace) -> None:
    log.info(f"Connecting to DynamoDB region={AWS_REGION} prefix={TABLE_PREFIX}")
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions_table = dynamodb.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt_table = dynamodb.Table(f"{TABLE_PREFIX}-feature-values")

    # Query all closed positions
    log.info("Querying closed positions...")
    all_positions: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq("POS#CLOSED")}
    while True:
        resp = positions_table.query(**kwargs)
        all_positions.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    log.info(f"Loaded {len(all_positions)} closed positions")

    # Collect positions needing backfill (writes are idempotent — we skip
    # the per-position existence check to avoid ~15k DynamoDB gets).
    log.info("Identifying positions to process...")
    needs_backfill: list[tuple[str, str, str]] = []  # (eval_id, ticker, entry_date)
    skipped_missing_fields = 0

    for pos in all_positions:
        eval_id = pos.get("evaluation_id")
        ticker = pos.get("underlying_ticker")
        entry_date_str = pos.get("entry_date")
        if not (eval_id and ticker and entry_date_str):
            skipped_missing_fields += 1
            continue
        needs_backfill.append((str(eval_id), str(ticker), str(entry_date_str)))

    log.info(
        f"Backfill scope: {len(needs_backfill)} positions to process "
        f"({skipped_missing_fields} missing key fields)"
    )

    if args.limit:
        needs_backfill = needs_backfill[: args.limit]
        log.info(f"Limited to first {args.limit} positions")

    # Collect unique (ticker, entry_date) pairs
    unique_pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for eval_id, ticker, entry_date_str in needs_backfill:
        unique_pairs[(ticker, entry_date_str)].append(eval_id)

    log.info(f"Unique (ticker, date) pairs to fetch: {len(unique_pairs)}")

    # Initialize S3 reader and optional Polygon client
    s3_reader = S3BarsReader(S3_BUCKET, region=AWS_REGION)
    polygon_key = os.environ.get("POLYGON_API_KEY")
    if not polygon_key:
        log.warning(
            "POLYGON_API_KEY not set — Polygon fallback disabled. "
            "Pairs missing from S3 will be skipped."
        )

    # Process each unique pair
    processed = 0
    features_written = 0
    pairs_missing = 0
    s3_hits = 0
    polygon_hits = 0
    evals_enriched = 0
    start_time = time.time()

    for (ticker, entry_date_str), eval_ids in unique_pairs.items():
        processed += 1
        if processed % 50 == 0:
            elapsed = time.time() - start_time
            rate = processed / elapsed if elapsed else 0
            eta = (len(unique_pairs) - processed) / rate if rate > 0 else 0
            log.info(
                f"  {processed}/{len(unique_pairs)} pairs "
                f"({s3_hits} S3, {polygon_hits} Polygon, {pairs_missing} missing) "
                f"- {evals_enriched} evals enriched - "
                f"{rate:.1f}/s, ETA {eta/60:.1f} min"
            )

        try:
            entry_date_obj = date.fromisoformat(entry_date_str)
        except ValueError:
            log.warning(f"Invalid entry_date for {ticker}: {entry_date_str}")
            pairs_missing += 1
            continue

        # Try S3 first
        bars = s3_reader.get_bars(ticker, entry_date_obj)
        source = "s3" if len(bars) >= MIN_BARS_FOR_ADX else None

        # Fall back to Polygon if insufficient
        if source is None and polygon_key:
            try:
                bars = await fetch_bars_from_polygon(ticker, entry_date_obj, polygon_key)
                if len(bars) >= MIN_BARS_FOR_ADX:
                    source = "polygon"
                    # Respect rate limit
                    await asyncio.sleep(1.0 / POLYGON_RATE_LIMIT)
            except Exception as e:
                log.debug(f"Polygon fetch failed for {ticker} {entry_date_str}: {e}")

        if source is None:
            pairs_missing += 1
            continue

        if source == "s3":
            s3_hits += 1
        else:
            polygon_hits += 1

        # Compute features once per pair
        features = compute_features_from_bars(bars)
        if not features:
            continue

        # Write to all evaluations sharing this (ticker, date)
        for eval_id in eval_ids:
            n = write_features_to_fvt(
                fvt_table, eval_id, features, dry_run=args.dry_run
            )
            features_written += n
            if n > 0:
                evals_enriched += 1

    elapsed = time.time() - start_time
    log.info("=" * 60)
    log.info("BACKFILL COMPLETE")
    log.info("=" * 60)
    log.info(f"Pairs processed:     {processed}")
    log.info(f"  S3 hits:           {s3_hits}")
    log.info(f"  Polygon hits:      {polygon_hits}")
    log.info(f"  Missing (skipped): {pairs_missing}")
    log.info(f"Evaluations enriched: {evals_enriched}")
    log.info(f"Feature values written: {features_written}")
    log.info(f"Elapsed: {elapsed/60:.1f} min")
    if args.dry_run:
        log.info("(dry-run — no writes performed)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill technical indicators for historical paper positions"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without writing to DynamoDB",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of positions processed (for testing)",
    )
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
