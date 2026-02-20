"""One-time script to rebuild pre-aggregated metrics from all positions.

Usage:
    cd backend
    DYNAMODB_TABLE_PREFIX=oss-dev AWS_REGION=us-west-1 python -m scripts.rebuild_metrics [--dry-run]

What it does:
1. Delete all existing METRICS# records
2. Scan all OPEN and CLOSED positions
3. Recompute METRICS#GLOBAL, METRICS#SCANNER, METRICS#VERDICT, METRICS#TIER
4. Write daily equity points from closed positions (METRICS#DAILY)
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _enum_val(v: Any) -> str:
    """Extract .value from an enum, or return string as-is."""
    return str(v.value if hasattr(v, "value") else v)


async def run(dry_run: bool = True) -> None:
    from app.db.dynamodb import get_dynamodb
    from app.db.tables import PaperPositionTable

    db = get_dynamodb()
    table = db.get_table(PaperPositionTable.TABLE)

    # --- Step 1: Delete existing metric records ---
    logger.info("Cleaning existing metric records...")
    metric_prefixes = [
        "METRICS#GLOBAL", "METRICS#SCANNER", "METRICS#VERDICT",
        "METRICS#TIER", "METRICS#DAILY",
    ]

    deleted_count = 0
    for prefix in metric_prefixes:
        query_kwargs: dict[str, Any] = {
            "KeyConditionExpression": "PK = :pk",
            "ExpressionAttributeValues": {":pk": prefix},
            "ProjectionExpression": "PK, SK",
        }
        while True:
            response = table.query(**query_kwargs)
            items = response.get("Items", [])
            if not items:
                break
            if dry_run:
                deleted_count += len(items)
                logger.info(f"[DRY RUN] Would delete {len(items)} records from {prefix}")
            else:
                with table.batch_writer() as writer:
                    for item in items:
                        writer.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
                deleted_count += len(items)
            if "LastEvaluatedKey" not in response:
                break
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    logger.info(f"{'Would delete' if dry_run else 'Deleted'} {deleted_count} old metric records")

    # --- Step 2: Scan all positions ---
    logger.info("Scanning all positions...")

    all_positions: list[dict[str, Any]] = []
    for status in ["OPEN", "CLOSED"]:
        query_kwargs = {
            "KeyConditionExpression": "PK = :pk",
            "ExpressionAttributeValues": {":pk": f"POS#{status}"},
            "ScanIndexForward": False,
        }
        while True:
            response = table.query(**query_kwargs)
            items = response.get("Items", [])
            for item in items:
                converted = db.convert_from_dynamodb(dict(item))
                converted["_status"] = status
                all_positions.append(converted)
            if "LastEvaluatedKey" not in response:
                break
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    open_positions = [p for p in all_positions if p["_status"] == "OPEN"]
    closed_positions = [p for p in all_positions if p["_status"] == "CLOSED"]
    logger.info(f"Found {len(open_positions)} open, {len(closed_positions)} closed positions")

    # --- Step 3: Compute metrics ---

    # Global summary
    global_metrics = {
        "open_count": len(open_positions),
        "closed_count": len(closed_positions),
        "total_count": len(all_positions),
        "win_count": 0,
        "loss_count": 0,
        "total_pnl": Decimal("0"),
        "sum_returns": Decimal("0"),
    }

    for pos in closed_positions:
        pnl = pos.get("current_pnl_pct", 0) or 0
        pnl_dec = Decimal(str(pnl))
        global_metrics["sum_returns"] += pnl_dec
        global_metrics["total_pnl"] += pnl_dec
        if pnl > 0:
            global_metrics["win_count"] += 1
        elif pnl < 0:
            global_metrics["loss_count"] += 1

    # Add running P&L from open positions
    for pos in open_positions:
        pnl = pos.get("current_pnl_pct", 0) or 0
        global_metrics["total_pnl"] += Decimal(str(pnl))

    # Compute derived values
    closed_total = global_metrics["closed_count"]
    win_rate = (
        float(global_metrics["win_count"] / closed_total * 100)
        if closed_total > 0
        else 0.0
    )
    avg_return = (
        float(global_metrics["sum_returns"] / Decimal(str(closed_total)))
        if closed_total > 0
        else 0.0
    )

    global_metrics["win_rate"] = Decimal(str(round(win_rate, 2)))
    global_metrics["avg_return"] = Decimal(str(round(avg_return, 4)))

    from datetime import datetime, timezone
    global_metrics["last_updated"] = datetime.now(timezone.utc).isoformat()

    logger.info(
        f"Global: open={global_metrics['open_count']}, "
        f"closed={global_metrics['closed_count']}, "
        f"win_rate={win_rate:.1f}%, avg_return={avg_return:.2f}%"
    )

    # Per-scanner metrics
    scanner_metrics: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "closed_count": 0, "win_count": 0, "loss_count": 0, "total_pnl": Decimal("0")}
    )
    for pos in all_positions:
        scanner = pos.get("scanner_source") or "UNKNOWN"
        scanner_metrics[scanner]["count"] += 1
    for pos in closed_positions:
        scanner = pos.get("scanner_source") or "UNKNOWN"
        pnl = pos.get("current_pnl_pct", 0) or 0
        scanner_metrics[scanner]["closed_count"] += 1
        scanner_metrics[scanner]["total_pnl"] += Decimal(str(pnl))
        if pnl > 0:
            scanner_metrics[scanner]["win_count"] += 1
        elif pnl < 0:
            scanner_metrics[scanner]["loss_count"] += 1

    for scanner, m in scanner_metrics.items():
        logger.info(f"Scanner {scanner}: count={m['count']}, closed={m['closed_count']}")

    # Per-verdict metrics
    verdict_metrics: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "closed_count": 0, "win_count": 0, "loss_count": 0, "total_pnl": Decimal("0")}
    )
    for pos in all_positions:
        verdict = _enum_val(pos.get("verdict_at_entry", "UNKNOWN"))
        verdict_metrics[verdict]["count"] += 1
    for pos in closed_positions:
        verdict = _enum_val(pos.get("verdict_at_entry", "UNKNOWN"))
        pnl = pos.get("current_pnl_pct", 0) or 0
        verdict_metrics[verdict]["closed_count"] += 1
        verdict_metrics[verdict]["total_pnl"] += Decimal(str(pnl))
        if pnl > 0:
            verdict_metrics[verdict]["win_count"] += 1
        elif pnl < 0:
            verdict_metrics[verdict]["loss_count"] += 1

    for verdict, m in verdict_metrics.items():
        logger.info(f"Verdict {verdict}: count={m['count']}, closed={m['closed_count']}")

    # Per-tier metrics
    tier_metrics: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "closed_count": 0, "win_count": 0, "loss_count": 0, "total_pnl": Decimal("0")}
    )
    for pos in all_positions:
        tier = _enum_val(pos.get("quality_tier_at_entry") or "NONE")
        tier_metrics[tier]["count"] += 1
    for pos in closed_positions:
        tier = _enum_val(pos.get("quality_tier_at_entry") or "NONE")
        pnl = pos.get("current_pnl_pct", 0) or 0
        tier_metrics[tier]["closed_count"] += 1
        tier_metrics[tier]["total_pnl"] += Decimal(str(pnl))
        if pnl > 0:
            tier_metrics[tier]["win_count"] += 1
        elif pnl < 0:
            tier_metrics[tier]["loss_count"] += 1

    # Daily equity points from closed positions (grouped by exit_date)
    daily_equity: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for pos in closed_positions:
        exit_date = pos.get("exit_date")
        pnl = pos.get("current_pnl_pct", 0) or 0
        if exit_date:
            daily_equity[exit_date] += Decimal(str(pnl))

    logger.info(f"Daily equity points: {len(daily_equity)} days")

    # --- Step 4: Write metrics ---
    if dry_run:
        logger.info("[DRY RUN] Would write all metric records. Run without --dry-run to apply.")
        return

    # Write METRICS#GLOBAL/SUMMARY
    global_item = {
        "PK": "METRICS#GLOBAL",
        "SK": "SUMMARY",
        **global_metrics,
    }
    await db.put_item(PaperPositionTable.TABLE, global_item)
    logger.info("Wrote METRICS#GLOBAL/SUMMARY")

    # Write METRICS#SCANNER/*
    for scanner, m in scanner_metrics.items():
        item = {"PK": "METRICS#SCANNER", "SK": scanner, **m}
        await db.put_item(PaperPositionTable.TABLE, item)
    logger.info(f"Wrote {len(scanner_metrics)} scanner metric records")

    # Write METRICS#VERDICT/*
    for verdict, m in verdict_metrics.items():
        item = {"PK": "METRICS#VERDICT", "SK": verdict, **m}
        await db.put_item(PaperPositionTable.TABLE, item)
    logger.info(f"Wrote {len(verdict_metrics)} verdict metric records")

    # Write METRICS#TIER/*
    for tier, m in tier_metrics.items():
        item = {"PK": "METRICS#TIER", "SK": tier, **m}
        await db.put_item(PaperPositionTable.TABLE, item)
    logger.info(f"Wrote {len(tier_metrics)} tier metric records")

    # Write METRICS#DAILY/*
    for date, pnl in sorted(daily_equity.items()):
        item = {
            "PK": "METRICS#DAILY",
            "SK": date,
            "daily_pnl": pnl,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.put_item(PaperPositionTable.TABLE, item)
    logger.info(f"Wrote {len(daily_equity)} daily equity records")

    logger.info("=== REBUILD COMPLETE ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild paper trading metrics")
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
