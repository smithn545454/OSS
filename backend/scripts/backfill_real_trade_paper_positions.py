"""One-time backfill: ensure every open RealTrade has a matching PaperPosition
with thesis TP/SL thresholds applied.

The Active Trades dashboard joins every open RealTrade to its paper position
via evaluation_id. RealTrades without one show "thesis pending" forever.
This script handles two cases:

1. **Orphan synth** — create a PaperPosition from the RealTrade's evaluation
   snapshot when none exists (e.g. REJECT verdicts, pre-enrollment tracks).
2. **Thesis top-off** — when a paper position exists but lacks
   thesis_tp1_pct/thesis_sl_pct/thesis_time_exit_dte, copy them from
   TradeThesisTable. This fixes the narrow case where a paper position was
   synthesized before thesis data had been copied onto it.

Usage:
    cd backend
    DYNAMODB_TABLE_PREFIX=oss-dev AWS_REGION=us-west-1 \
        python -m scripts.backfill_real_trade_paper_positions [--dry-run]

Always run --dry-run first to see what will change.
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
    from app.db.tables import (
        PaperPositionTable,
        RealTradeTable,
        TradeThesisTable,
    )
    from app.paper_trading.position_manager import (
        ensure_paper_position_for_real_trade,
    )
    from app.services.polygon import PolygonClient

    all_trades = await RealTradeTable.list_open(limit=500)
    logger.info("Scanning %d open RealTrades...", len(all_trades))

    orphans: list[dict[str, Any]] = []
    thesis_missing: list[tuple[dict[str, Any], Any]] = []  # (paper_position, thesis)
    ok = 0

    for trade in all_trades:
        snapshot = trade.get("snapshot") or {}
        eval_id = snapshot.get("evaluation_id") if isinstance(snapshot, dict) else None
        if not eval_id:
            logger.warning(
                "Trade %s has no evaluation_id — skipping",
                trade.get("trade_id"),
            )
            continue

        paper = await PaperPositionTable.get_by_evaluation_id(eval_id)
        if paper is None:
            orphans.append(trade)
            continue

        # Paper exists. Does it have thesis thresholds?
        if paper.thesis_tp1_pct is None and paper.thesis_sl_pct is None:
            thesis = await TradeThesisTable.get_by_evaluation_id(eval_id)
            if thesis is not None and thesis.exit_plan is not None:
                thesis_missing.append((paper, thesis))
                continue
        ok += 1

    logger.info(
        "Results: %d ok, %d orphans (no paper), %d missing thesis (paper exists)",
        ok, len(orphans), len(thesis_missing),
    )

    for t in orphans:
        snap = t.get("snapshot") or {}
        logger.info(
            "  orphan: trade_id=%s ticker=%s option_ticker=%s",
            t.get("trade_id"),
            snap.get("underlying_ticker"),
            snap.get("option_ticker"),
        )
    for paper, _ in thesis_missing:
        logger.info(
            "  thesis-missing: position_id=%s option_ticker=%s",
            paper.position_id, paper.option_ticker,
        )

    if not orphans and not thesis_missing:
        logger.info("Nothing to do.")
        return

    if dry_run:
        logger.info("--dry-run: no writes performed. Re-run without --dry-run to apply.")
        return

    # 1. Create missing paper positions (with thesis applied).
    if orphans:
        logger.info("Creating %d paper positions...", len(orphans))
        created = 0
        failed = 0
        async with PolygonClient() as client:
            for t in orphans:
                try:
                    position = await ensure_paper_position_for_real_trade(
                        t, polygon_client=client
                    )
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
        logger.info("Synth: %d created, %d failed", created, failed)

    # 2. Top off thesis thresholds on existing positions.
    if thesis_missing:
        logger.info("Applying thesis thresholds to %d positions...", len(thesis_missing))
        applied = 0
        failed = 0
        for paper, thesis in thesis_missing:
            try:
                updates: dict[str, Any] = {}
                exit_plan = thesis.exit_plan
                if exit_plan.take_profits:
                    updates["thesis_tp1_pct"] = exit_plan.take_profits[0].option_pnl_pct
                if exit_plan.stop_loss_level:
                    updates["thesis_sl_pct"] = abs(
                        exit_plan.stop_loss_level.option_pnl_pct
                    )
                if exit_plan.time_exit_level:
                    updates["thesis_time_exit_dte"] = (
                        exit_plan.time_exit_level.dte_threshold
                    )
                if updates:
                    await PaperPositionTable.update(paper, updates)
                    applied += 1
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "Failed to apply thesis to position %s: %s",
                    paper.position_id, e,
                )
                failed += 1
        logger.info("Thesis top-off: %d applied, %d failed", applied, failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
