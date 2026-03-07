"""Backfill missing enrichment fields on paper trading positions.

Usage:
    cd backend
    DYNAMODB_TABLE_PREFIX=oss-dev AWS_REGION=us-west-1 python -m scripts.backfill_enrichment [--dry-run]

What it does:
1. Scan all positions (open + closed)
2. For positions missing scanner_source: look up matching evaluation
3. For positions missing option_type/strike/expiration_date: parse OCC ticker
4. Write updated fields back to DynamoDB
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# OCC ticker pattern: AAPL260315C00170000
# Groups: underlying, YYMMDD, C/P, strike (8 digits, divide by 1000)
OCC_PATTERN = re.compile(r"^(?:O:)?([A-Z]+)(\d{6})([CP])(\d{8})$")


def parse_occ_ticker(ticker: str) -> Optional[dict[str, Any]]:
    """Parse an OCC option ticker into its components.

    Args:
        ticker: Option ticker like 'O:AAPL260315C00170000' or 'TSLA260313C00380000'

    Returns:
        Dict with underlying_ticker, expiration_date, option_type, strike or None
    """
    m = OCC_PATTERN.match(ticker)
    if not m:
        return None

    underlying = m.group(1)
    date_str = m.group(2)  # YYMMDD
    opt_type = "CALL" if m.group(3) == "C" else "PUT"
    strike = int(m.group(4)) / 1000.0

    try:
        year = int(date_str[:2]) + 2000
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        expiration = f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return None

    return {
        "underlying_ticker": underlying,
        "expiration_date": expiration,
        "option_type": opt_type,
        "strike": strike,
    }


async def run(dry_run: bool = True) -> None:
    from app.db.dynamodb import get_dynamodb
    from app.db.tables import PaperPositionTable

    db = get_dynamodb()
    table = db.get_table(PaperPositionTable.TABLE)

    # --- Step 1: Scan all positions ---
    logger.info("Scanning all positions...")

    all_positions: list[dict[str, Any]] = []
    for status in ["OPEN", "CLOSED"]:
        query_kwargs: dict[str, Any] = {
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
                converted["_pk"] = f"POS#{status}"
                converted["_sk"] = converted.get("SK") or item.get("SK")
                all_positions.append(converted)
            if "LastEvaluatedKey" not in response:
                break
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    logger.info(f"Found {len(all_positions)} total positions")

    # --- Step 2: Identify positions needing backfill ---
    needs_scanner = [p for p in all_positions if not p.get("scanner_source")]
    needs_option_type = [p for p in all_positions if not p.get("option_type")]
    needs_underlying = [p for p in all_positions if not p.get("underlying_ticker")]

    logger.info(f"Missing scanner_source: {len(needs_scanner)}")
    logger.info(f"Missing option_type: {len(needs_option_type)}")
    logger.info(f"Missing underlying_ticker: {len(needs_underlying)}")

    # --- Step 3: Backfill from OCC ticker parsing ---
    ticker_updates = 0
    ticker_failures = 0

    for pos in all_positions:
        option_ticker = pos.get("option_ticker", "")
        updates: dict[str, Any] = {}

        # Parse OCC ticker for missing fields
        if not pos.get("option_type") or not pos.get("strike") or not pos.get("expiration_date"):
            parsed = parse_occ_ticker(option_ticker)
            if parsed:
                if not pos.get("option_type"):
                    updates["option_type"] = parsed["option_type"]
                if not pos.get("strike"):
                    updates["strike"] = parsed["strike"]
                if not pos.get("expiration_date"):
                    updates["expiration_date"] = parsed["expiration_date"]
                if not pos.get("underlying_ticker"):
                    updates["underlying_ticker"] = parsed["underlying_ticker"]
            else:
                ticker_failures += 1

        if updates:
            pk = pos["_pk"]
            sk = pos["_sk"]

            if dry_run:
                ticker_updates += 1
                if ticker_updates <= 5:
                    logger.info(
                        f"[DRY RUN] Would update {option_ticker}: {updates}"
                    )
            else:
                try:
                    update_expr_parts = []
                    expr_values: dict[str, Any] = {}
                    expr_names: dict[str, str] = {}

                    for key, val in updates.items():
                        safe_key = f"#f_{key}"
                        safe_val = f":v_{key}"
                        update_expr_parts.append(f"{safe_key} = {safe_val}")
                        expr_names[safe_key] = key
                        expr_values[safe_val] = val

                    table.update_item(
                        Key={"PK": pk, "SK": sk},
                        UpdateExpression="SET " + ", ".join(update_expr_parts),
                        ExpressionAttributeNames=expr_names,
                        ExpressionAttributeValues=expr_values,
                    )
                    ticker_updates += 1
                except Exception as e:
                    logger.error(f"Failed to update {option_ticker}: {e}")
                    ticker_failures += 1

    logger.info(
        f"OCC ticker backfill: {ticker_updates} updated, {ticker_failures} failures"
    )

    # --- Step 4: Backfill scanner_source from evaluations ---
    # Build lookup of evaluation_id -> scanner_source from evaluations table
    logger.info("Scanning evaluations for scanner_source backfill...")

    eval_table = db.get_table("evaluations")
    eval_scanner_map: dict[str, str] = {}
    scan_kwargs: dict[str, Any] = {}

    try:
        while True:
            response = eval_table.scan(**scan_kwargs)
            items = response.get("Items", [])
            for item in items:
                converted = db.convert_from_dynamodb(dict(item))
                eval_id = converted.get("evaluation_id")
                scanner = converted.get("scanner_source")
                if eval_id and scanner:
                    eval_scanner_map[eval_id] = scanner
            if "LastEvaluatedKey" not in response:
                break
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    except Exception as e:
        logger.warning(f"Could not scan evaluations table: {e}")

    logger.info(f"Found {len(eval_scanner_map)} evaluations with scanner_source")

    scanner_updates = 0
    scanner_not_found = 0

    for pos in needs_scanner:
        eval_id = pos.get("evaluation_id")
        if not eval_id:
            continue

        scanner = eval_scanner_map.get(eval_id)
        if not scanner:
            scanner_not_found += 1
            continue

        pk = pos["_pk"]
        sk = pos["_sk"]

        if dry_run:
            scanner_updates += 1
            if scanner_updates <= 5:
                logger.info(
                    f"[DRY RUN] Would set scanner_source={scanner} on "
                    f"{pos.get('option_ticker')}"
                )
        else:
            try:
                table.update_item(
                    Key={"PK": pk, "SK": sk},
                    UpdateExpression="SET scanner_source = :ss",
                    ExpressionAttributeValues={":ss": scanner},
                )
                scanner_updates += 1
            except Exception as e:
                logger.error(
                    f"Failed to update scanner_source for {pos.get('option_ticker')}: {e}"
                )

    logger.info(
        f"Scanner source backfill: {scanner_updates} updated, "
        f"{scanner_not_found} evals had no scanner_source"
    )

    # --- Summary ---
    logger.info("=== BACKFILL SUMMARY ===")
    logger.info(f"Total positions scanned: {len(all_positions)}")
    logger.info(f"OCC ticker fields updated: {ticker_updates}")
    logger.info(f"Scanner source updated: {scanner_updates}")
    if dry_run:
        logger.info("[DRY RUN] No changes written. Run without --dry-run to apply.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill paper position enrichment data")
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
