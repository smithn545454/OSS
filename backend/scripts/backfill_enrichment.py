"""Backfill missing enrichment fields on paper trading positions.

Usage:
    cd backend
    DYNAMODB_TABLE_PREFIX=oss-dev AWS_REGION=us-west-1 python -m scripts.backfill_enrichment [--dry-run]

What it does:
1. Scan all positions (open + closed)
2. Normalize scanner_source values (strip _SCANNER suffix from UV Lambda)
3. For positions missing scanner_source: look up matching evaluation, then opportunity
4. For positions missing option_type/strike/expiration_date: parse OCC ticker
5. Write updated fields back to DynamoDB
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from collections import defaultdict
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


def _apply_scanner_update(table: Any, pos: dict[str, Any], scanner: str, dry_run: bool) -> None:
    """Apply a scanner_source update to a position."""
    pk = pos["_pk"]
    sk = pos["_sk"]
    if dry_run:
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
        except Exception as e:
            logger.error(
                f"Failed to update scanner_source for {pos.get('option_ticker')}: {e}"
            )


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

    # --- Step 2: Normalize _SCANNER suffix on existing positions ---
    needs_normalize = [
        p for p in all_positions
        if p.get("scanner_source") and p["scanner_source"].endswith("_SCANNER")
    ]
    logger.info(f"Positions with _SCANNER suffix to normalize: {len(needs_normalize)}")

    normalize_updates = 0
    for pos in needs_normalize:
        raw = pos["scanner_source"]
        normalized = raw[: -len("_SCANNER")]
        pk = pos["_pk"]
        sk = pos["_sk"]

        if dry_run:
            normalize_updates += 1
            if normalize_updates <= 5:
                logger.info(
                    f"[DRY RUN] Would normalize {raw} -> {normalized} on "
                    f"{pos.get('option_ticker')}"
                )
        else:
            try:
                table.update_item(
                    Key={"PK": pk, "SK": sk},
                    UpdateExpression="SET scanner_source = :ss",
                    ExpressionAttributeValues={":ss": normalized},
                )
                normalize_updates += 1
            except Exception as e:
                logger.error(
                    f"Failed to normalize scanner_source for {pos.get('option_ticker')}: {e}"
                )
        # Update in-memory value so subsequent steps see the normalized value
        pos["scanner_source"] = normalized

    logger.info(f"Scanner normalization: {normalize_updates} updated")

    # --- Step 3: Identify positions still needing backfill ---
    needs_scanner = [p for p in all_positions if not p.get("scanner_source")]
    needs_option_type = [p for p in all_positions if not p.get("option_type")]
    needs_underlying = [p for p in all_positions if not p.get("underlying_ticker")]

    logger.info(f"Missing scanner_source: {len(needs_scanner)}")
    logger.info(f"Missing option_type: {len(needs_option_type)}")
    logger.info(f"Missing underlying_ticker: {len(needs_underlying)}")

    # --- Step 4: Backfill from OCC ticker parsing ---
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

            # Update in-memory dict so Step 5 can use the parsed values
            for key, val in updates.items():
                pos[key] = val

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

    # --- Step 5: Backfill scanner_source via batched evaluation + opportunity lookups ---
    # Group by ticker to minimize DynamoDB queries, and paginate fully to avoid missing data.
    logger.info(
        f"Resolving scanner_source for {len(needs_scanner)} positions "
        "via batched evaluation/opportunity queries..."
    )

    scanner_updates = 0
    scanner_not_found = 0
    unknown_updates = 0

    if needs_scanner:
        eval_table = db.get_table("evaluations")
        opp_table = db.get_table("opportunities")

        # Step 5a: Group positions by underlying_ticker
        positions_by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
        no_eval_id: list[dict[str, Any]] = []
        for pos in needs_scanner:
            eval_id = pos.get("evaluation_id")
            ticker = pos.get("underlying_ticker")
            if not eval_id or not ticker:
                no_eval_id.append(pos)
                continue
            positions_by_ticker[ticker].append(pos)

        logger.info(
            f"Grouped into {len(positions_by_ticker)} unique tickers "
            f"({len(no_eval_id)} positions have no evaluation_id/ticker)"
        )

        # Step 5b: For each ticker, build evaluation index with FULL pagination
        needs_opp_fallback: list[tuple[dict[str, Any], str, str]] = []

        for ticker_idx, (ticker, positions) in enumerate(positions_by_ticker.items()):
            if ticker_idx > 0 and ticker_idx % 20 == 0:
                logger.info(
                    f"  Ticker progress: {ticker_idx}/{len(positions_by_ticker)}, "
                    f"{scanner_updates} resolved so far"
                )

            # Collect all eval_ids we need for this ticker
            needed_eval_ids = {p["evaluation_id"] for p in positions}

            # Query ALL evaluations for this ticker (paginated)
            eval_index: dict[str, dict[str, Any]] = {}
            query_kwargs: dict[str, Any] = {
                "KeyConditionExpression": "PK = :pk",
                "ExpressionAttributeValues": {":pk": f"EVAL#{ticker}"},
                "ProjectionExpression": "SK, scanner_source, opportunity_id",
                "ScanIndexForward": False,
            }

            try:
                while True:
                    response = eval_table.query(**query_kwargs)
                    for eval_item in response.get("Items", []):
                        eval_sk = eval_item.get("SK", "")
                        if isinstance(eval_sk, dict):
                            eval_sk = eval_sk.get("S", "")
                        eval_sk = str(eval_sk)
                        # SK format: "{timestamp}#{evaluation_id}"
                        for eid in needed_eval_ids:
                            if eval_sk.endswith(eid):
                                conv = db.convert_from_dynamodb(dict(eval_item))
                                eval_index[eid] = {
                                    "scanner_source": conv.get("scanner_source"),
                                    "opportunity_id": conv.get("opportunity_id"),
                                }
                                break

                    # Early exit: if we found all needed eval_ids, stop paginating
                    if needed_eval_ids.issubset(eval_index.keys()):
                        break
                    if "LastEvaluatedKey" not in response:
                        break
                    query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            except Exception as e:
                logger.warning(f"Eval query failed for ticker {ticker}: {e}")

            # Step 5c: Resolve positions from eval index
            for pos in positions:
                eid = pos["evaluation_id"]
                eval_data = eval_index.get(eid)

                if not eval_data:
                    scanner_not_found += 1
                    continue

                scanner = eval_data.get("scanner_source")
                if scanner and scanner.endswith("_SCANNER"):
                    scanner = scanner[: -len("_SCANNER")]

                if scanner:
                    _apply_scanner_update(table, pos, scanner, dry_run)
                    scanner_updates += 1
                else:
                    # Need opportunity fallback
                    opp_id = eval_data.get("opportunity_id")
                    if opp_id:
                        needs_opp_fallback.append((pos, ticker, opp_id))
                    else:
                        scanner_not_found += 1

        logger.info(
            f"After evaluation lookup: {scanner_updates} resolved, "
            f"{len(needs_opp_fallback)} need opportunity fallback, "
            f"{scanner_not_found} not found"
        )

        # Step 5d: Opportunity fallback -- group by ticker again
        if needs_opp_fallback:
            opp_positions_by_ticker: dict[str, list[tuple[dict[str, Any], str]]] = (
                defaultdict(list)
            )
            for pos, ticker, opp_id in needs_opp_fallback:
                opp_positions_by_ticker[ticker].append((pos, opp_id))

            for ticker, pos_opp_pairs in opp_positions_by_ticker.items():
                needed_opp_ids = {opp_id for _, opp_id in pos_opp_pairs}

                # Query ALL opportunities for this ticker (paginated)
                opp_index: dict[str, str] = {}  # opp_id -> scanner_type
                query_kwargs = {
                    "KeyConditionExpression": "PK = :pk",
                    "ExpressionAttributeValues": {":pk": f"OPP#{ticker}"},
                    "ProjectionExpression": "SK, opportunity_id, scanner_triggers",
                    "ScanIndexForward": False,
                }

                try:
                    while True:
                        response = opp_table.query(**query_kwargs)
                        for opp_item in response.get("Items", []):
                            opp_conv = db.convert_from_dynamodb(dict(opp_item))
                            oid = opp_conv.get("opportunity_id")
                            if oid and oid in needed_opp_ids:
                                triggers = opp_conv.get("scanner_triggers", [])
                                if triggers:
                                    st = triggers[0]
                                    scanner_type = None
                                    if isinstance(st, dict):
                                        scanner_type = st.get("scanner_type")
                                    elif hasattr(st, "scanner_type"):
                                        scanner_type = st.scanner_type
                                    if scanner_type and hasattr(scanner_type, "value"):
                                        scanner_type = scanner_type.value
                                    if scanner_type and scanner_type.endswith("_SCANNER"):
                                        scanner_type = scanner_type[: -len("_SCANNER")]
                                    if scanner_type:
                                        opp_index[oid] = scanner_type

                        if needed_opp_ids.issubset(opp_index.keys()):
                            break
                        if "LastEvaluatedKey" not in response:
                            break
                        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                except Exception as e:
                    logger.warning(f"Opp query failed for ticker {ticker}: {e}")

                # Resolve from opportunity index
                for pos, opp_id in pos_opp_pairs:
                    scanner = opp_index.get(opp_id)
                    if scanner:
                        _apply_scanner_update(table, pos, scanner, dry_run)
                        scanner_updates += 1
                    else:
                        scanner_not_found += 1

        # Step 5e: Handle positions with no eval_id/ticker -- mark as UNKNOWN
        for pos in no_eval_id:
            _apply_scanner_update(table, pos, "UNKNOWN", dry_run)
            unknown_updates += 1

    logger.info(
        f"Scanner source backfill: {scanner_updates} resolved, "
        f"{unknown_updates} marked UNKNOWN (no eval_id/ticker), "
        f"{scanner_not_found} could not be resolved"
    )

    # --- Summary ---
    logger.info("=== BACKFILL SUMMARY ===")
    logger.info(f"Total positions scanned: {len(all_positions)}")
    logger.info(f"Scanner normalization (_SCANNER suffix): {normalize_updates}")
    logger.info(f"OCC ticker fields updated: {ticker_updates}")
    logger.info(f"Scanner source resolved: {scanner_updates}")
    logger.info(f"Scanner source marked UNKNOWN: {unknown_updates}")
    logger.info(f"Scanner source unresolvable: {scanner_not_found}")
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
