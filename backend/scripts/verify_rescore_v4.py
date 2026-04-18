#!/usr/bin/env python3
"""Post-rescore verification for v4 rescore of paper positions.

Queries the positions table after rescore and reports:
- v4 field population coverage (should be ~100%)
- conviction_score_v3 preservation (should be ~100%)
- v3 pillar field preservation (should be untouched)
- Distribution shift v3 → v4
- Tier distribution under new thresholds
- Any positions that still lack v4 scores

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/verify_rescore_v4.py
"""

from __future__ import annotations

import os
import statistics
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")


def to_f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def query_partition(table: Any, pk: str) -> list[dict]:
    items: list[dict] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(pk)}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def main() -> None:
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions = dynamodb.Table(f"{TABLE_PREFIX}-paper-positions")

    print(f"Loading positions from {TABLE_PREFIX}-paper-positions (region={AWS_REGION})...")
    closed = query_partition(positions, "POS#CLOSED")
    open_ = query_partition(positions, "POS#OPEN")
    all_positions = closed + open_
    print(f"  total: {len(all_positions)} (closed={len(closed)} open={len(open_)})")

    # Field coverage
    v4_fields = [
        "pillar_directional_conviction",
        "pillar_move_potential",
        "pillar_trade_structure",
    ]
    v3_fields = [
        "pillar_premium_leverage",
        "pillar_underlying_behavior",
        "pillar_setup_quality",
    ]
    coverage_v4 = {f: 0 for f in v4_fields}
    coverage_v3 = {f: 0 for f in v3_fields}
    coverage_v3_backup = 0
    coverage_regime = Counter()
    coverage_rescored_at = 0

    v3_conv: list[float] = []
    v4_conv: list[float] = []
    v4_dc: list[float] = []
    v4_mp: list[float] = []
    v4_ts: list[float] = []

    missing_v4_positions: list[dict[str, Any]] = []

    for p in all_positions:
        for f in v4_fields:
            if p.get(f) is not None:
                coverage_v4[f] += 1
        for f in v3_fields:
            if p.get(f) is not None:
                coverage_v3[f] += 1
        if p.get("conviction_score_v3") is not None:
            coverage_v3_backup += 1
        if p.get("rescored_v4_at") is not None:
            coverage_rescored_at += 1
        coverage_regime[str(p.get("scoring_regime") or "(unset)")] += 1

        cv3 = to_f(p.get("conviction_score_v3"))
        cv4 = to_f(p.get("conviction_score"))
        if cv3 is not None:
            v3_conv.append(cv3)
        if cv4 is not None and p.get("scoring_regime") == "v4":
            v4_conv.append(cv4)
        dc = to_f(p.get("pillar_directional_conviction"))
        mp = to_f(p.get("pillar_move_potential"))
        ts = to_f(p.get("pillar_trade_structure"))
        if dc is not None: v4_dc.append(dc)
        if mp is not None: v4_mp.append(mp)
        if ts is not None: v4_ts.append(ts)

        if not all(p.get(f) is not None for f in v4_fields):
            missing_v4_positions.append({
                "ticker": p.get("underlying_ticker"),
                "entry_date": p.get("entry_date"),
                "position_id": p.get("position_id"),
            })

    n = len(all_positions)
    print()
    print("=" * 70)
    print("v4 FIELD COVERAGE (should be ~100%)")
    print("=" * 70)
    for f in v4_fields:
        pct = 100.0 * coverage_v4[f] / n if n else 0
        print(f"  {f:<38} {coverage_v4[f]}/{n}  ({pct:.1f}%)")
    pct = 100.0 * coverage_v3_backup / n if n else 0
    print(f"  conviction_score_v3 (backup)           {coverage_v3_backup}/{n}  ({pct:.1f}%)")
    pct = 100.0 * coverage_rescored_at / n if n else 0
    print(f"  rescored_v4_at                         {coverage_rescored_at}/{n}  ({pct:.1f}%)")

    print()
    print("v3 FIELD PRESERVATION (should be unchanged at 100%)")
    for f in v3_fields:
        pct = 100.0 * coverage_v3[f] / n if n else 0
        print(f"  {f:<38} {coverage_v3[f]}/{n}  ({pct:.1f}%)")

    print()
    print("scoring_regime distribution:")
    for k, cnt in coverage_regime.most_common():
        print(f"  {k}: {cnt}")

    print()
    print("=" * 70)
    print("SCORE DISTRIBUTION — v3 vs v4")
    print("=" * 70)
    def stats(label: str, xs: list[float]) -> None:
        if not xs:
            print(f"  {label}: (no data)")
            return
        print(
            f"  {label}: n={len(xs)} mean={statistics.mean(xs):.2f} "
            f"median={statistics.median(xs):.2f} "
            f"stdev={statistics.stdev(xs) if len(xs) > 1 else 0:.2f} "
            f"min={min(xs):.2f} max={max(xs):.2f}"
        )
    stats("v3 conviction (backup)", v3_conv)
    stats("v4 conviction          ", v4_conv)
    stats("v4 DC pillar           ", v4_dc)
    stats("v4 MP pillar           ", v4_mp)
    stats("v4 TS pillar           ", v4_ts)

    print()
    print("TIER DISTRIBUTION (v4 thresholds 92/82/72/62)")
    if v4_conv:
        t1 = sum(1 for s in v4_conv if s >= 92)
        t2 = sum(1 for s in v4_conv if 82 <= s < 92)
        t3 = sum(1 for s in v4_conv if 72 <= s < 82)
        w = sum(1 for s in v4_conv if 62 <= s < 72)
        r = sum(1 for s in v4_conv if s < 62)
        total = len(v4_conv)
        print(f"  TIER_1 (>=92):   {t1:>6}  ({100*t1/total:.1f}%)")
        print(f"  TIER_2 (82-91):  {t2:>6}  ({100*t2/total:.1f}%)")
        print(f"  TIER_3 (72-81):  {t3:>6}  ({100*t3/total:.1f}%)")
        print(f"  WATCH  (62-71):  {w:>6}  ({100*w/total:.1f}%)")
        print(f"  REJECT (<62):    {r:>6}  ({100*r/total:.1f}%)")

    if missing_v4_positions:
        print()
        print(f"POSITIONS MISSING v4 SCORES: {len(missing_v4_positions)}")
        for mp in missing_v4_positions[:10]:
            print(f"  {mp}")
        if len(missing_v4_positions) > 10:
            print(f"  ... and {len(missing_v4_positions)-10} more")


if __name__ == "__main__":
    main()
