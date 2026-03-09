"""One-time backfill: reconstruct daily equity curve from closed position history.

Usage:
    cd backend
    DYNAMODB_TABLE_PREFIX=oss-dev AWS_REGION=us-west-1 python -m scripts.backfill_daily_equity [--dry-run]

What it does:
1. Paginate through all POS#CLOSED positions
2. For each: compute dollar P&L = (exit_price - entry_price) * quantity * 100
3. Group by exit_date, aggregate dollar P&L per day
4. Write each daily aggregate to METRICS#DAILY via MetricsAggregator.write_daily_equity()

This reconstructs realized P&L only (close events). The batch updater tracks
unrealized daily changes going forward.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from decimal import Decimal
from typing import Any

# Ensure backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def run(dry_run: bool = True) -> None:
    from app.db.dynamodb import get_dynamodb
    from app.db.tables import PaperPositionTable
    from app.paper_trading.metrics_aggregator import MetricsAggregator

    db = get_dynamodb()
    table = db.get_table(PaperPositionTable.TABLE)

    # --- Step 1: Paginate through all CLOSED positions ---
    logger.info("Scanning all CLOSED positions...")
    daily_pnl: dict[str, float] = defaultdict(float)
    total_positions = 0
    skipped = 0

    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": "POS#CLOSED"},
        "ScanIndexForward": False,
    }
    while True:
        response = table.query(**query_kwargs)
        items = response.get("Items", [])

        for raw_item in items:
            item = db.convert_from_dynamodb(raw_item)
            exit_date = item.get("exit_date")
            entry_price = item.get("entry_price")
            exit_price = item.get("exit_price")
            quantity = item.get("quantity", 1)

            if not exit_date or entry_price is None or exit_price is None:
                skipped += 1
                continue

            # Convert Decimals to float
            entry_price = float(entry_price)
            exit_price = float(exit_price)
            quantity = int(quantity)

            # Dollar P&L: (exit - entry) * quantity * 100 (option multiplier)
            dollar_pnl = (exit_price - entry_price) * quantity * 100
            daily_pnl[exit_date] += dollar_pnl
            total_positions += 1

        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    logger.info(f"Processed {total_positions} closed positions, {skipped} skipped (missing data)")

    if not daily_pnl:
        logger.info("No daily P&L data to write")
        return

    # Sort by date
    sorted_dates = sorted(daily_pnl.keys())
    total_dollar_pnl = sum(daily_pnl.values())

    logger.info(f"Date range: {sorted_dates[0]} to {sorted_dates[-1]}")
    logger.info(f"Total days with closes: {len(sorted_dates)}")
    logger.info(f"Total dollar P&L: ${total_dollar_pnl:,.2f}")

    # Show top 10 days by magnitude
    by_magnitude = sorted(daily_pnl.items(), key=lambda x: abs(x[1]), reverse=True)
    logger.info("Top 10 days by magnitude:")
    for date, pnl in by_magnitude[:10]:
        logger.info(f"  {date}: ${pnl:,.2f}")

    # --- Step 2: Write daily aggregates ---
    if dry_run:
        logger.info(f"[DRY RUN] Would write {len(sorted_dates)} METRICS#DAILY rows")
        logger.info("[DRY RUN] No changes made. Run without --dry-run to apply.")
    else:
        logger.info(f"Writing {len(sorted_dates)} METRICS#DAILY rows...")
        written = 0
        for date in sorted_dates:
            pnl = daily_pnl[date]
            await MetricsAggregator.write_daily_equity(date, pnl)
            written += 1
            if written % 50 == 0:
                logger.info(f"  Written {written}/{len(sorted_dates)}")
        logger.info(f"Done: wrote {written} daily equity rows")

    # --- Summary ---
    logger.info("=== BACKFILL SUMMARY ===")
    logger.info(f"Closed positions processed: {total_positions}")
    logger.info(f"Positions skipped: {skipped}")
    logger.info(f"Days with close events: {len(sorted_dates)}")
    logger.info(f"Date range: {sorted_dates[0]} to {sorted_dates[-1]}")
    logger.info(f"Total realized P&L: ${total_dollar_pnl:,.2f}")
    logger.info(f"Starting equity: $10,000 → Final equity: ${10000 + total_dollar_pnl:,.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill daily equity curve from closed positions")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview changes without writing to DynamoDB",
    )
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
