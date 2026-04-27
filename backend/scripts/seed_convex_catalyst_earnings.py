#!/usr/bin/env python3
"""Seed the Convex catalyst calendar with upcoming earnings.

Run before Convex Mode cutover so Stage 2A (date-known catalyst) has
something to fire on. Two source modes:

  --source earnings-cache  (default, no API key needed)
        Scan the existing ``oss-dev-earnings-cache`` table — populated
        daily by the existing earnings refresh hook — and denormalize
        every entry into a ``CatalystCalendarEntry`` row.

  --source finnhub
        Pull all upcoming earnings directly from Finnhub
        ``/calendar/earnings`` for the next N days. Requires
        ``FINNHUB_API_KEY`` env var (or FINNHUB_SECRET_ARN configured).

Macro events (FOMC/CPI/NFP) and FDA PDUFAs are NOT seeded here — they
need a separate manual / biopharmcatalyst pass and are scoped out of
the cutover-day seed per user direction.

Usage:
    AWS_REGION=us-west-1 \\
    DYNAMODB_TABLE_PREFIX=oss-dev \\
        python backend/scripts/seed_convex_catalyst_earnings.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import get_settings  # noqa: E402
from app.core.schemas import CatalystCalendarEntry, CatalystEventType  # noqa: E402
from app.db.tables import CatalystCalendarTable  # noqa: E402
from app.services.finnhub import FinnhubClient  # noqa: E402

logger = logging.getLogger("seed-convex-catalyst")


async def _seed_from_finnhub(lookforward: int, api_key: str) -> tuple[int, int, int, int]:
    today = date.today()
    to_date = today + timedelta(days=lookforward)
    logger.info(
        "Pulling upcoming earnings from Finnhub: %s → %s",
        today.isoformat(),
        to_date.isoformat(),
    )
    async with FinnhubClient(api_key) as finnhub:
        events = await finnhub.get_all_upcoming_earnings(today, to_date)
    logger.info("Received %d earnings events from Finnhub", len(events))

    written = skipped_no_symbol = skipped_invalid_date = failed = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for ev in events:
        symbol = (ev.get("symbol") or "").upper()
        ev_date_raw = ev.get("date")
        if not symbol:
            skipped_no_symbol += 1
            continue
        try:
            event_date = datetime.strptime(ev_date_raw, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            skipped_invalid_date += 1
            continue

        metadata = {
            k: ev.get(k)
            for k in ("hour", "epsEstimate", "epsActual", "revenueEstimate", "year", "quarter")
            if ev.get(k) is not None
        }

        entry = CatalystCalendarEntry(
            ticker=symbol,
            event_date=event_date.isoformat(),
            event_type=CatalystEventType.EARNINGS,
            confirmed=True,
            source="finnhub",
            metadata=metadata,
            last_updated=now_iso,
        )
        try:
            await CatalystCalendarTable.put(entry)
            written += 1
        except Exception as e:
            failed += 1
            logger.warning("Put failed for %s @ %s: %s", symbol, event_date, e)

    return written, skipped_no_symbol, skipped_invalid_date, failed


async def _seed_from_earnings_cache(lookforward: int) -> tuple[int, int, int, int]:
    """Denormalize from oss-dev-earnings-cache into oss-dev-catalyst-calendar.

    Uses raw boto3 scan so we don't depend on EarningsCache's per-ticker
    get path. Filters out entries whose earnings_date is in the past or
    further than ``lookforward`` days out.
    """
    import boto3

    settings = get_settings()
    cache_table_name = f"{settings.dynamodb_table_prefix}-earnings-cache"
    today = date.today()
    horizon = today + timedelta(days=lookforward)

    region = os.environ.get("AWS_REGION", "us-west-1")
    table = boto3.resource("dynamodb", region_name=region).Table(cache_table_name)
    items: list[dict] = []
    last_key: dict | None = None
    while True:
        kwargs = {}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    logger.info("Scanned %d rows from %s", len(items), cache_table_name)

    written = skipped_no_symbol = skipped_invalid_date = failed = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for item in items:
        symbol = (item.get("ticker") or "").upper()
        ev_date_raw = item.get("earnings_date")
        if not symbol:
            skipped_no_symbol += 1
            continue
        if not ev_date_raw:
            # Cached "no upcoming earnings" — skip silently.
            continue
        try:
            event_date = datetime.strptime(ev_date_raw, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            skipped_invalid_date += 1
            continue
        if event_date < today or event_date > horizon:
            continue

        metadata = {
            k: item.get(k)
            for k in ("earnings_time", "fetched_at")
            if item.get(k) is not None
        }
        entry = CatalystCalendarEntry(
            ticker=symbol,
            event_date=event_date.isoformat(),
            event_type=CatalystEventType.EARNINGS,
            confirmed=True,
            source="earnings-cache-denormalize",
            metadata=metadata,
            last_updated=now_iso,
        )
        try:
            await CatalystCalendarTable.put(entry)
            written += 1
        except Exception as e:
            failed += 1
            logger.warning("Put failed for %s @ %s: %s", symbol, event_date, e)

    return written, skipped_no_symbol, skipped_invalid_date, failed


async def _seed(args: argparse.Namespace) -> int:
    if args.source == "finnhub":
        api_key = args.api_key or os.environ.get("FINNHUB_API_KEY")
        if not api_key:
            settings = get_settings()
            api_key = settings.finnhub_api_key
        if not api_key:
            logger.error(
                "No Finnhub API key found — set FINNHUB_API_KEY or FINNHUB_SECRET_ARN, "
                "or use --source earnings-cache"
            )
            return 1
        written, skipped_no_symbol, skipped_invalid_date, failed = (
            await _seed_from_finnhub(args.lookforward, api_key)
        )
    else:
        written, skipped_no_symbol, skipped_invalid_date, failed = (
            await _seed_from_earnings_cache(args.lookforward)
        )

    logger.info(
        "Seed complete: wrote=%d skipped_no_symbol=%d skipped_invalid_date=%d failed=%d",
        written,
        skipped_no_symbol,
        skipped_invalid_date,
        failed,
    )
    return 0 if failed == 0 else 1


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--lookforward",
        type=int,
        default=60,
        help="Days ahead to seed (default 60)",
    )
    p.add_argument(
        "--source",
        choices=("earnings-cache", "finnhub"),
        default="earnings-cache",
        help="Where to read upcoming earnings from (default earnings-cache)",
    )
    p.add_argument("--api-key", default=None, help="Finnhub API key (overrides env)")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    sys.exit(asyncio.run(_seed(args)))


if __name__ == "__main__":
    main()
