"""One-time backfill script: dedup open positions and enrich with evaluation data.

Usage:
    cd backend
    DYNAMODB_TABLE_PREFIX=oss-dev AWS_REGION=us-west-1 python -m scripts.backfill_positions [--dry-run]

What it does:
1. Paginate through all OPEN positions
2. Group by option_ticker — keep the newest, delete older duplicates
3. For retained positions missing enrichment fields, query the evaluation
   by evaluation_id and write enrichment data back to the position
4. Process deletes in batches of 25 (DynamoDB BatchWrite limit)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from typing import Any

# Ensure backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def run(dry_run: bool = True) -> None:
    from app.db.dynamodb import get_dynamodb
    from app.db.tables import EvaluationTable, PaperPositionTable

    db = get_dynamodb()
    table = db.get_table(PaperPositionTable.TABLE)

    # --- Step 1: Collect all OPEN positions via paginated query ---
    logger.info("Scanning all OPEN positions...")
    all_open: list[dict[str, Any]] = []
    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": "POS#OPEN"},
        "ScanIndexForward": False,
    }
    while True:
        response = table.query(**query_kwargs)
        items = response.get("Items", [])
        all_open.extend(db.convert_from_dynamodb(item) for item in items)
        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    logger.info(f"Found {len(all_open)} open positions")

    # --- Step 2: Group by option_ticker, identify duplicates ---
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pos in all_open:
        by_ticker[pos.get("option_ticker", "UNKNOWN")].append(pos)

    duplicates_to_delete: list[dict[str, str]] = []  # list of {PK, SK}
    positions_to_keep: list[dict[str, Any]] = []

    for ticker, group in by_ticker.items():
        if len(group) == 1:
            positions_to_keep.append(group[0])
            continue

        # Sort by entry_date descending — keep newest
        group.sort(key=lambda p: p.get("entry_date", ""), reverse=True)
        positions_to_keep.append(group[0])

        for dup in group[1:]:
            duplicates_to_delete.append({
                "PK": f"POS#OPEN",
                "SK": f"{dup['entry_date']}#{dup['position_id']}",
            })

    logger.info(
        f"Dedup results: {len(positions_to_keep)} to keep, "
        f"{len(duplicates_to_delete)} duplicates to delete "
        f"({len(by_ticker)} unique option_tickers)"
    )

    # --- Step 3: Delete duplicates in batches of 25 ---
    if duplicates_to_delete:
        if dry_run:
            logger.info("[DRY RUN] Would delete %d duplicate positions", len(duplicates_to_delete))
        else:
            table_name = db.get_table_name(PaperPositionTable.TABLE)
            dynamo_resource = db._resource
            deleted = 0
            for i in range(0, len(duplicates_to_delete), 25):
                batch = duplicates_to_delete[i : i + 25]
                with table.batch_writer() as writer:
                    for key in batch:
                        writer.delete_item(Key=key)
                deleted += len(batch)
                logger.info(f"Deleted {deleted}/{len(duplicates_to_delete)} duplicates")

    # --- Step 4: Enrich positions missing enrichment fields ---
    enrichment_fields = [
        "underlying_ticker", "scanner_source", "convergence_count",
        "conviction_score", "pillar_directional", "pillar_volatility",
        "pillar_structure", "strike", "option_type", "expiration_date",
        "dte_at_entry", "dte_bucket", "entry_delta", "entry_iv",
        "entry_theta", "gate_margin", "theta_adj_ev",
    ]

    needs_enrichment = [
        pos for pos in positions_to_keep
        if not pos.get("underlying_ticker")
    ]
    logger.info(f"{len(needs_enrichment)} positions need enrichment from evaluations")

    enriched_count = 0
    not_found_count = 0

    for pos in needs_enrichment:
        eval_id = pos.get("evaluation_id")
        if not eval_id:
            continue

        # Try to find the evaluation
        # We need the ticker to query — extract from option_ticker
        option_ticker = pos.get("option_ticker", "")
        from app.paper_trading.position_manager import extract_underlying_from_option_ticker
        underlying = extract_underlying_from_option_ticker(option_ticker)

        eval_item = await EvaluationTable.get_by_id(underlying, eval_id)
        if not eval_item:
            not_found_count += 1
            continue

        decision = eval_item.get("decision", {})

        # Build updates from evaluation data
        updates: dict[str, Any] = {}
        if eval_item.get("underlying_ticker"):
            updates["underlying_ticker"] = eval_item["underlying_ticker"]
        if eval_item.get("strike"):
            updates["strike"] = eval_item["strike"]
        if eval_item.get("option_type"):
            updates["option_type"] = eval_item["option_type"]
        if eval_item.get("expiration_date"):
            updates["expiration_date"] = eval_item["expiration_date"]
        if eval_item.get("dte") is not None:
            updates["dte_at_entry"] = eval_item["dte"]
        if eval_item.get("dte_bucket"):
            updates["dte_bucket"] = eval_item["dte_bucket"]
        if eval_item.get("delta") is not None:
            updates["entry_delta"] = eval_item["delta"]
        if eval_item.get("iv") is not None:
            updates["entry_iv"] = eval_item["iv"]
        if eval_item.get("theta") is not None:
            updates["entry_theta"] = eval_item["theta"]

        # From decision
        if decision.get("final_score") is not None:
            updates["conviction_score"] = decision["final_score"]
        if decision.get("directional_score") is not None:
            updates["pillar_directional"] = decision["directional_score"]
        if decision.get("volatility_score") is not None:
            updates["pillar_volatility"] = decision["volatility_score"]
        if decision.get("structure_score") is not None:
            updates["pillar_structure"] = decision["structure_score"]

        # Scanner source from evaluation
        if eval_item.get("scanner_source"):
            updates["scanner_source"] = eval_item["scanner_source"]

        # Write GSI2 keys
        entry_date = pos.get("entry_date", "")
        updates["GSI2PK"] = f"OPT#{option_ticker}"
        updates["GSI2SK"] = f"OPEN#{entry_date}"

        if not updates:
            continue

        if dry_run:
            logger.info(
                f"[DRY RUN] Would enrich {option_ticker}: {list(updates.keys())}"
            )
        else:
            pk = "POS#OPEN"
            sk = f"{entry_date}#{pos['position_id']}"
            await db.update_item(PaperPositionTable.TABLE, pk, sk, updates)

        enriched_count += 1

    logger.info(
        f"Enrichment complete: {enriched_count} enriched, "
        f"{not_found_count} evaluations not found"
    )

    # --- Summary ---
    logger.info("=== BACKFILL SUMMARY ===")
    logger.info(f"Total open positions scanned: {len(all_open)}")
    logger.info(f"Unique option tickers: {len(by_ticker)}")
    logger.info(f"Duplicates {'would be ' if dry_run else ''}deleted: {len(duplicates_to_delete)}")
    logger.info(f"Positions enriched: {enriched_count}")
    logger.info(f"Evaluations not found: {not_found_count}")
    if dry_run:
        logger.info("*** DRY RUN — no changes were made. Run without --dry-run to apply. ***")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill paper positions")
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
