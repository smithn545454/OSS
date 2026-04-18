#!/usr/bin/env python3
"""Audit v4 feature coverage in FeatureValueTable for paper positions.

Samples paper positions, queries their evaluations in FVT, and reports
per-feature presence % for v4-required features. Output tells us whether
Phase R2 (retroactive feature backfill) is needed before rescoring.

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/audit_v4_feature_coverage.py [--sample N]
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")

V4_REQUIRED_FEATURES = [
    "ma_150",
    "ma_200",
    "high_52w",
    "low_52w",
    "bb_width_percentile",
    "sector_rs_20d",
    "historical_move_magnitude",
    "days_to_earnings",
]

V4_OTHER_FEATURES_TO_CHECK = [
    "iv",
    "iv_percentile",
    "iv_rv_ratio",
    "rv20",
    "adx_14",
    "plus_di",
    "minus_di",
    "rs_20d",
    "obv_trend",
    "sma50",
    "required_move_pct",
    "expected_move_pct",
    "close",
]


def query_partition(table: Any, pk: str, limit: int | None = None) -> list[dict]:
    items: list[dict] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(pk)}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if limit and len(items) >= limit:
            return items[:limit]
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=500)
    args = parser.parse_args()

    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions = dynamodb.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt = dynamodb.Table(f"{TABLE_PREFIX}-feature-values")

    print(f"Querying positions (region={AWS_REGION} prefix={TABLE_PREFIX})...")
    closed = query_partition(positions, "POS#CLOSED")
    open_ = query_partition(positions, "POS#OPEN")
    all_positions = closed + open_
    print(f"  total positions: {len(all_positions)} (closed={len(closed)}, open={len(open_)})")

    random.seed(42)
    sample = random.sample(all_positions, min(args.sample, len(all_positions)))
    print(f"  sampling: {len(sample)}")

    present_count: dict[str, int] = defaultdict(int)
    sampled_with_fvt = 0
    sampled_without_fvt = 0

    all_features = V4_REQUIRED_FEATURES + V4_OTHER_FEATURES_TO_CHECK

    for idx, pos in enumerate(sample):
        if idx % 100 == 0 and idx > 0:
            print(f"  {idx}/{len(sample)}...")
        eval_id = str(pos.get("evaluation_id", ""))
        if not eval_id:
            continue
        resp = fvt.query(
            KeyConditionExpression=Key("PK").eq(f"EVAL#{eval_id}")
            & Key("SK").begins_with("FEATURE#"),
            ProjectionExpression="SK",
        )
        items = resp.get("Items", [])
        if not items:
            sampled_without_fvt += 1
            continue
        sampled_with_fvt += 1
        present_names = {str(i["SK"]).replace("FEATURE#", "") for i in items}
        for feat in all_features:
            if feat in present_names:
                present_count[feat] += 1

    print()
    print("=" * 70)
    print(f"COVERAGE AUDIT — sample size: {len(sample)}")
    print(f"  positions with ANY FVT records:  {sampled_with_fvt}")
    print(f"  positions with NO FVT records:   {sampled_without_fvt}")
    print("=" * 70)
    denom = sampled_with_fvt if sampled_with_fvt else 1
    print()
    print("V4 NEW FEATURES (Phase 1 additions):")
    for feat in V4_REQUIRED_FEATURES:
        n = present_count[feat]
        pct = 100.0 * n / denom
        flag = "✓" if pct >= 80 else ("⚠" if pct >= 40 else "✗")
        print(f"  {flag} {feat:<32} {n:>5}/{denom:<5} ({pct:5.1f}%)")
    print()
    print("V4-CONSUMED PREEXISTING FEATURES:")
    for feat in V4_OTHER_FEATURES_TO_CHECK:
        n = present_count[feat]
        pct = 100.0 * n / denom
        flag = "✓" if pct >= 80 else ("⚠" if pct >= 40 else "✗")
        print(f"  {flag} {feat:<32} {n:>5}/{denom:<5} ({pct:5.1f}%)")
    print()
    missing_critical = [
        f for f in V4_REQUIRED_FEATURES if 100.0 * present_count[f] / denom < 80
    ]
    if missing_critical:
        print(f"DECISION: R2 backfill REQUIRED — missing: {missing_critical}")
    else:
        print("DECISION: R2 backfill NOT required — proceed directly to R3.")


if __name__ == "__main__":
    main()
