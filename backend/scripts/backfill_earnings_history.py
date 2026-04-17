#!/usr/bin/env python3
"""Backfill historical earnings events + 1-day post-event moves.

For each ticker, fetches the last 18 months of earnings events from
Finnhub and the next 90 days of scheduled events. For past events,
joins with price history to compute the close-to-close 1-day move
(bmo vs amc aware) and persists to ``oss-dev-earnings-history``.

Why: Pillar v4 Move Potential scores the average absolute 1-day move
across a ticker's last 4 earnings. The pipeline reads this as a
pre-computed feature — no Finnhub call in the hot path.

Rate limits: Finnhub free tier is 60 req/min; client self-throttles
at 30 req/min. One request per ticker → ~50 minutes for 1,500 tickers.

Usage
-----
    # Full backfill (S&P 500 + Russell 1000)
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev FINNHUB_API_KEY=... \\
        python scripts/backfill_earnings_history.py

    # Specific tickers (useful to fill gaps or verify one)
    python scripts/backfill_earnings_history.py --tickers AAPL,MSFT,TSLA

    # Dry run — fetch from Finnhub but do NOT write to DynamoDB
    python scripts/backfill_earnings_history.py --dry-run --tickers AAPL
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

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
    if explicit_tickers:
        return sorted(set(t.upper() for t in explicit_tickers))
    from app.db.tables import SP500TickerTable

    seen: set[str] = set()
    for universe in universes:
        tickers = await SP500TickerTable.get_tickers_by_universe(universe)
        logger.info(f"Universe {universe}: {len(tickers)} tickers")
        seen.update(tickers)
    return sorted(seen)


async def backfill(
    tickers: list[str], *, dry_run: bool
) -> dict[str, int | list[str]]:
    """Refresh one ticker at a time (Finnhub rate limit prefers serial).

    Uses the client's own lock to enforce 30 req/min cap.
    """
    from app.config import get_settings
    from app.services.earnings_calendar import EarningsCalendarService
    from app.services.finnhub import FinnhubClient
    from app.services.price_history import PriceHistoryService

    settings = get_settings()
    api_key = os.environ.get("FINNHUB_API_KEY") or getattr(
        settings, "finnhub_api_key", None
    )
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY not set")

    attempted = 0
    succeeded = 0
    events_written = 0
    moves_computed = 0
    failures: list[str] = []

    async with FinnhubClient(api_key) as finnhub:
        price_svc = PriceHistoryService()  # read-only against DynamoDB
        if dry_run:
            # For dry-run, skip writes by not passing the service that
            # writes to DynamoDB. We'll still invoke Finnhub so operators
            # can verify connectivity + coverage before committing.
            service = EarningsCalendarService(
                finnhub_client=finnhub, price_history_service=price_svc
            )
        else:
            service = EarningsCalendarService(
                finnhub_client=finnhub, price_history_service=price_svc
            )

        started = time.time()
        for i, ticker in enumerate(tickers, 1):
            attempted += 1
            try:
                if dry_run:
                    raw = await finnhub.get_earnings_calendar(
                        symbol=ticker, from_date=None, to_date=None
                    )
                    logger.info(
                        f"[{i}/{len(tickers)}] {ticker}: {len(raw)} events "
                        f"(dry-run, not written)"
                    )
                    succeeded += 1
                    continue

                report = await service.refresh_ticker(ticker)
                if report.tickers_succeeded:
                    succeeded += 1
                    events_written += report.events_written
                    moves_computed += report.moves_computed
                else:
                    failures.extend(report.failures)
                if i % 25 == 0 or i == len(tickers):
                    elapsed = time.time() - started
                    rate = i / elapsed if elapsed > 0 else 0.0
                    logger.info(
                        f"[{i}/{len(tickers)}] {succeeded} OK, "
                        f"{events_written} events, "
                        f"{moves_computed} moves, "
                        f"{len(failures)} fail "
                        f"({rate:.2f}/s)"
                    )
            except Exception as e:  # pragma: no cover — defensive
                failures.append(f"{ticker}: {e}")
                logger.exception(f"[{i}/{len(tickers)}] {ticker} raised")

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": len(failures),
        "events_written": events_written,
        "moves_computed": moves_computed,
        "failures": failures[:25],
    }


async def main_async(args: argparse.Namespace) -> int:
    tickers = await resolve_universe(
        universes=args.universe,
        explicit_tickers=args.tickers.split(",") if args.tickers else None,
    )
    if not tickers:
        logger.error("No tickers to backfill")
        return 2

    logger.info(
        f"Backfilling earnings history for {len(tickers)} tickers "
        f"(dry_run={args.dry_run})"
    )

    report = await backfill(tickers, dry_run=args.dry_run)

    logger.info("=" * 60)
    logger.info("EARNINGS BACKFILL COMPLETE")
    logger.info(f"  tickers attempted: {report['attempted']}")
    logger.info(f"  tickers succeeded: {report['succeeded']}")
    logger.info(f"  tickers failed:    {report['failed']}")
    logger.info(f"  events written:    {report['events_written']}")
    logger.info(f"  moves computed:    {report['moves_computed']}")
    coverage = (
        report["succeeded"] / report["attempted"] * 100
        if report["attempted"]
        else 0.0
    )
    logger.info(f"  ticker coverage:   {coverage:.1f}%")
    if report["failures"]:
        logger.warning("First 25 failures:")
        for line in report["failures"]:  # type: ignore[union-attr]
            logger.warning(f"  {line}")

    if not args.dry_run and coverage < 90.0 and report["attempted"] > 100:
        logger.warning(
            "Earnings coverage below 90% target — some tickers may "
            "have no catalyst data in Move Potential scores."
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
        "--dry-run",
        action="store_true",
        help="Fetch from Finnhub but do NOT write to DynamoDB.",
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
