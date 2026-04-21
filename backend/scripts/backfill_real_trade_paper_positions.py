"""One-time backfill: synth PaperPositions for any orphan RealTrades.

The Active Trades dashboard joins every open RealTrade to its matching
PaperPosition via evaluation_id. RealTrades that were tracked from REJECT
verdicts (or from evaluations that predated the paper-trading enrollment
path) don't have a paper position and would show as "thesis pending" in
the dashboard. This script detects those and creates a paper position
from the RealTrade's stored evaluation snapshot.

Usage:
    cd backend
    DYNAMODB_TABLE_PREFIX=oss-dev AWS_REGION=us-west-1 \
        python -m scripts.backfill_real_trade_paper_positions [--dry-run]

Always run --dry-run first to see the orphan count and identifiers.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def run(dry_run: bool = True) -> None:
    from app.db.tables import PaperPositionTable, RealTradeTable
    from app.paper_trading.position_manager import (
        ensure_paper_position_for_real_trade,
    )
    from app.services.polygon import PolygonClient

    # Only backfill OPEN RealTrades — the Active Trades dashboard is what
    # drove this invariant. Closed trades have their exit data on the
    # RealTrade itself and don't need a parallel paper position.
    all_trades = await RealTradeTable.list_open(limit=500)
    logger.info("Scanning %d open RealTrades for orphans...", len(all_trades))

    orphans: list[dict[str, Any]] = []
    for trade in all_trades:
        snapshot = trade.get("snapshot") or {}
        eval_id = snapshot.get("evaluation_id") if isinstance(snapshot, dict) else None
        if not eval_id:
            logger.warning(
                "Trade %s has no evaluation_id — cannot backfill",
                trade.get("trade_id"),
            )
            continue

        paper = await PaperPositionTable.get_by_evaluation_id(eval_id)
        if paper is None:
            orphans.append(trade)

    logger.info("Found %d orphan RealTrades (no matching PaperPosition)", len(orphans))

    if not orphans:
        logger.info("Nothing to backfill. Exiting.")
        return

    for t in orphans:
        snap = t.get("snapshot") or {}
        logger.info(
            "  orphan: trade_id=%s ticker=%s option_ticker=%s verdict=%s tracked_at=%s",
            t.get("trade_id"),
            snap.get("underlying_ticker"),
            snap.get("option_ticker"),
            snap.get("verdict"),
            t.get("tracked_at"),
        )

    if dry_run:
        logger.info("--dry-run: no writes performed. Re-run without --dry-run to backfill.")
        return

    logger.info("Creating %d paper positions...", len(orphans))
    created = 0
    failed = 0
    async with PolygonClient() as client:
        for t in orphans:
            try:
                position = await ensure_paper_position_for_real_trade(t, polygon_client=client)
                if position:
                    created += 1
                else:
                    failed += 1
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "Failed to synth paper position for trade %s: %s",
                    t.get("trade_id"), e,
                )
                failed += 1

    logger.info("Backfill complete: %d created, %d failed", created, failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report orphans without writing")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
