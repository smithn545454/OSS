#!/usr/bin/env python3
"""Rollback: restore paper position scores from a backup JSON.

Companion to rescore_all_positions.py — reads the backup JSON file
written during rescore and restores the old v2 pillar fields back
onto each position, removing the v3 fields.

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/restore_position_scores.py \\
        --backup scripts/output/position_scores_backup_<timestamp>.json \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")


def to_dynamo_value(v: Any) -> dict[str, Any]:
    if v is None:
        return {"NULL": True}
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, (int, float, Decimal)):
        return {"N": str(v)}
    return {"S": str(v)}


def restore_position(
    dynamodb_client: Any,
    table_name: str,
    record: dict[str, Any],
    dry_run: bool = False,
) -> None:
    """Restore one position's old scores, removing the new ones."""
    pk = f"POS#{record['status']}"
    sk = f"{record['entry_date']}#{record['position_id']}"

    old_dir = record.get("old_pillar_directional")
    old_vol = record.get("old_pillar_volatility")
    old_str = record.get("old_pillar_structure")
    old_conv = record.get("old_conviction_score")

    # Build SET expression only for fields that had values
    set_parts = []
    expr_attr_names: dict[str, str] = {
        "#pd": "pillar_directional",
        "#pv": "pillar_volatility",
        "#ps": "pillar_structure",
        "#cs": "conviction_score",
        "#lu": "last_updated",
        "#pl": "pillar_premium_leverage",
        "#ub": "pillar_underlying_behavior",
        "#sq": "pillar_setup_quality",
    }
    expr_attr_values: dict[str, dict[str, Any]] = {}

    if old_dir is not None:
        set_parts.append("#pd = :pd")
        expr_attr_values[":pd"] = to_dynamo_value(old_dir)
    if old_vol is not None:
        set_parts.append("#pv = :pv")
        expr_attr_values[":pv"] = to_dynamo_value(old_vol)
    if old_str is not None:
        set_parts.append("#ps = :ps")
        expr_attr_values[":ps"] = to_dynamo_value(old_str)
    if old_conv is not None:
        set_parts.append("#cs = :cs")
        expr_attr_values[":cs"] = to_dynamo_value(old_conv)

    set_parts.append("#lu = :lu")
    expr_attr_values[":lu"] = {"S": datetime.now(timezone.utc).isoformat()}

    expression = "SET " + ", ".join(set_parts) + " REMOVE #pl, #ub, #sq"

    if dry_run:
        return

    dynamodb_client.update_item(
        TableName=table_name,
        Key={
            "PK": {"S": pk},
            "SK": {"S": sk},
        },
        UpdateExpression=expression,
        ExpressionAttributeNames=expr_attr_names,
        ExpressionAttributeValues=expr_attr_values,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rollback paper position scores from a backup JSON file"
    )
    parser.add_argument(
        "--backup",
        type=str,
        required=True,
        help="Path to the backup JSON file produced by rescore_all_positions.py",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without writing to DynamoDB",
    )
    args = parser.parse_args()

    backup_path = Path(args.backup)
    if not backup_path.exists():
        log.error(f"Backup file not found: {backup_path}")
        sys.exit(1)

    with open(backup_path) as f:
        records = json.load(f)
    log.info(f"Loaded {len(records)} backup records from {backup_path}")

    dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)
    table_name = f"{TABLE_PREFIX}-paper-positions"
    log.info(f"Restoring to table {table_name}")

    restored = 0
    errors = 0
    for idx, record in enumerate(records):
        if idx % 500 == 0 and idx > 0:
            log.info(f"  {idx}/{len(records)} (restored={restored}, errors={errors})")
        try:
            restore_position(dynamodb_client, table_name, record, dry_run=args.dry_run)
            restored += 1
        except Exception as e:
            errors += 1
            if errors < 10:
                log.warning(f"Restore error at {idx}: {e}")

    log.info("=" * 60)
    log.info("RESTORE COMPLETE")
    log.info("=" * 60)
    log.info(f"Restored: {restored}")
    log.info(f"Errors:   {errors}")
    if args.dry_run:
        log.info("(dry-run — no writes performed)")


if __name__ == "__main__":
    main()
