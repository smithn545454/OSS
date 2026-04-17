#!/usr/bin/env python3
"""Backfill daily OHLCV bars into oss-dev-price-history for Pillar v4.

Fetches 280 calendar days (~200 trading days with buffer) of daily bars
for each ticker in the chosen universe(s) via Polygon, then writes
them to the ``oss-dev-price-history`` DynamoDB table with a TTL.

Why: Pillar v4 Directional Conviction subscores need 252-day history
(150/200MA, 52w high/low, BB width percentile). Without backfill the
cache starts empty and the pipeline hits Polygon per-ticker on every
run until the daily refresh warms it. Backfilling upfront is a one-shot
cost (~1,500 API calls, well within the Advanced Options plan).

Usage
-----
    # Both S&P 500 + Russell 1000 (default — matches Pillar v4 universe)
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
        python scripts/backfill_price_history.py

    # Single universe
    python scripts/backfill_price_history.py --universe sp500

    # Specific tickers (e.g. for filling a gap)
    python scripts/backfill_price_history.py --tickers AAPL,MSFT,NVDA

    # Dry run: fetch counts only, no writes
    python scripts/backfill_price_history.py --dry-run

    # Custom lookback (default 280 calendar days)
    python scripts/backfill_price_history.py --days 365
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

# Ensure region is set before any app imports; default to production region.
os.environ.setdefault("AWS_REGION", "us-west-1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def resolve_universe(
    universes: list[str], explicit_tickers: Optional[list[str]]
) -> list[str]:
    """Resolve the final ticker list.

    When --tickers is provided, that list wins. Otherwise the union of
    every requested universe from ``SP500TickerTable``.
    """
    if explicit_tickers:
        return sorted(set(t.upper() for t in explicit_tickers))

    from app.db.tables import SP500TickerTable

    seen: set[str] = set()
    for universe in universes:
        tickers = await SP500TickerTable.get_tickers_by_universe(universe)
        logger.info(f"Universe {universe}: {len(tickers)} tickers")
        seen.update(tickers)

    return sorted(seen)


async def backfill_tickers(
    tickers: list[str],
    *,
    lookback_days: int,
    dry_run: bool,
    concurrency: int,
) -> dict[str, int | list[str]]:
    """Fetch + write bars for each ticker.

    Runs with bounded concurrency; Polygon's own semaphore inside
    ``PolygonClient`` throttles at the API level.
    """
    from app.services.polygon import PolygonClient
    from app.services.price_history import PriceHistoryService

    attempted = 0
    succeeded = 0
    bars_written = 0
    failures: list[str] = []

    async with PolygonClient(max_concurrent=concurrency) as polygon:
        service = PriceHistoryService(polygon_client=polygon)
        started = time.time()

        for batch_start in range(0, len(tickers), 50):
            batch = tickers[batch_start : batch_start + 50]

            async def _do(ticker: str) -> tuple[str, int, Optional[str]]:
                try:
                    if dry_run:
                        # Fetch only, no write.
                        from datetime import timedelta

                        start_date = (
                            date.today() - timedelta(days=lookback_days)
                        ).isoformat()
                        end_date = date.today().isoformat()
                        raw = await polygon.get_daily_bars_parsed(
                            ticker, start_date, end_date
                        )
                        return ticker, len(raw), None
                    count = await service.warm_ticker(
                        ticker, lookback_days=lookback_days
                    )
                    return ticker, count, None
                except Exception as e:  # pragma: no cover — defensive
                    return ticker, 0, str(e)

            results = await asyncio.gather(*(_do(t) for t in batch))
            for ticker, count, err in results:
                attempted += 1
                if err is not None:
                    failures.append(f"{ticker}: {err}")
                elif count > 0:
                    succeeded += 1
                    bars_written += count
                else:
                    failures.append(f"{ticker}: no bars returned")

            elapsed = time.time() - started
            rate = attempted / elapsed if elapsed > 0 else 0.0
            logger.info(
                f"Progress: {attempted}/{len(tickers)} "
                f"({succeeded} OK, {bars_written} bars, "
                f"{len(failures)} failures, {rate:.1f}/s)"
            )

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": len(failures),
        "bars_written": bars_written,
        "failures": failures[:25],  # keep report compact
    }


async def main_async(args: argparse.Namespace) -> int:
    tickers = await resolve_universe(
        universes=args.universe,
        explicit_tickers=args.tickers.split(",") if args.tickers else None,
    )
    if not tickers:
        logger.error("No tickers to backfill — universe is empty")
        return 2

    logger.info(
        f"Backfilling {len(tickers)} tickers "
        f"(lookback={args.days}d, dry_run={args.dry_run})"
    )

    report = await backfill_tickers(
        tickers,
        lookback_days=args.days,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
    )

    logger.info("=" * 60)
    logger.info("BACKFILL COMPLETE")
    logger.info(f"  tickers attempted:  {report['attempted']}")
    logger.info(f"  tickers succeeded:  {report['succeeded']}")
    logger.info(f"  tickers failed:     {report['failed']}")
    logger.info(f"  bars written:       {report['bars_written']}")
    coverage = (
        report["succeeded"] / report["attempted"] * 100
        if report["attempted"]
        else 0.0
    )
    logger.info(f"  coverage:           {coverage:.1f}%")
    if report["failures"]:
        logger.warning("First 25 failures:")
        for line in report["failures"]:  # type: ignore[union-attr]
            logger.warning(f"  {line}")

    # Acceptance criterion: ≥99% coverage on production backfill.
    if not args.dry_run and coverage < 99.0 and report["attempted"] > 100:
        logger.warning(
            "Coverage below 99% target — investigate failures before "
            "relying on Pillar v4 features."
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe",
        action="append",
        choices=["sp500", "russell1000"],
        help="Universe(s) to backfill. Repeatable. Defaults to both.",
    )
    parser.add_argument(
        "--tickers",
        help="Comma-separated ticker list. Overrides --universe when set.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=280,
        help="Calendar-day lookback (default 280 for 252 trading days + buffer).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Max concurrent Polygon requests.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch from Polygon but do NOT write to DynamoDB.",
    )
    args = parser.parse_args()
    if not args.universe and not args.tickers:
        args.universe = ["sp500", "russell1000"]
    elif args.universe is None:
        args.universe = []
    return args


if __name__ == "__main__":
    args = parse_args()
    sys.exit(asyncio.run(main_async(args)))
