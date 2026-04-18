#!/usr/bin/env python3
"""Audit option_type propagation across scanners for v4 direction-aware DC.

Questions answered:
  1. Which scanners produce CALL vs PUT entries, and in what proportion?
  2. Does entry_delta sign match option_type (PUT should be < 0)?
  3. For BREAKDOWN PUTs specifically, is current DC low (pre-fix) and
     does fixing rs_20d/sector_rs_20d direction-flip plausibly raise it?

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/audit_option_type_propagation.py
"""

from __future__ import annotations

import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")


def f(v: Any) -> Optional[float]:
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


# Option ticker format: O:TICKERYYMMDDC00XXXXXX (C=call, P=put)
OPT_TICKER_RE = re.compile(r"^O:[A-Z]+\d{6}([CP])\d+$")


def infer_option_type_from_ticker(opt_ticker: Optional[str]) -> Optional[str]:
    if not opt_ticker:
        return None
    m = OPT_TICKER_RE.match(opt_ticker)
    if not m:
        return None
    return "CALL" if m.group(1) == "C" else "PUT"


def main() -> None:
    print(f"Loading positions (region={AWS_REGION} prefix={TABLE_PREFIX})...")
    positions = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
        f"{TABLE_PREFIX}-paper-positions"
    )
    closed = query_partition(positions, "POS#CLOSED")
    open_ = query_partition(positions, "POS#OPEN")
    all_positions = closed + open_
    print(f"  loaded {len(all_positions)} total positions\n")

    # Build scanner → option_type distribution
    print("=" * 72)
    print("SCANNER × OPTION_TYPE DISTRIBUTION")
    print("=" * 72)
    scanner_type: dict[str, Counter] = defaultdict(Counter)
    scanner_delta_sign: dict[str, Counter] = defaultdict(Counter)
    ticker_vs_field: Counter = Counter()
    dc_by_direction: dict[tuple[str, str], list[float]] = defaultdict(list)

    for p in all_positions:
        scanner = str(p.get("scanner_source") or "(none)")
        opt_ticker = p.get("option_ticker")
        inferred = infer_option_type_from_ticker(opt_ticker)
        delta = f(p.get("entry_delta"))

        scanner_type[scanner][inferred or "(unknown)"] += 1
        if delta is not None:
            sign = "negative" if delta < 0 else ("zero" if delta == 0 else "positive")
            scanner_delta_sign[scanner][sign] += 1

        # Cross-check: inferred from ticker vs delta sign consistency
        if inferred and delta is not None and delta != 0:
            expected = "positive" if inferred == "CALL" else "negative"
            actual = "positive" if delta > 0 else "negative"
            ticker_vs_field[(inferred, "match" if expected == actual else "MISMATCH")] += 1

        # For closed, collect DC by (scanner, direction) for pre-fix baseline
        dc = f(p.get("pillar_directional_conviction"))
        if dc is not None and inferred:
            dc_by_direction[(scanner, inferred)].append(dc)

    print(f"{'Scanner':<24} {'CALL':>8} {'PUT':>8} {'unknown':>8} {'Total':>8}")
    for scanner in sorted(scanner_type.keys()):
        c = scanner_type[scanner]
        total = sum(c.values())
        print(f"{scanner:<24} {c.get('CALL', 0):>8} {c.get('PUT', 0):>8} "
              f"{c.get('(unknown)', 0):>8} {total:>8}")

    print()
    print("=" * 72)
    print("DELTA SIGN BY SCANNER (PUTs should have negative delta)")
    print("=" * 72)
    print(f"{'Scanner':<24} {'pos':>8} {'neg':>8} {'zero':>8} {'none':>8}")
    for scanner in sorted(scanner_delta_sign.keys()):
        c = scanner_delta_sign[scanner]
        none_ct = scanner_type[scanner].get("unknown", 0)
        print(f"{scanner:<24} {c.get('positive', 0):>8} {c.get('negative', 0):>8} "
              f"{c.get('zero', 0):>8} {none_ct:>8}")

    print()
    print("=" * 72)
    print("TICKER-INFERRED TYPE vs DELTA-SIGN CONSISTENCY")
    print("=" * 72)
    for (kind, result), ct in sorted(ticker_vs_field.items()):
        print(f"  {kind} + {result}: {ct}")

    print()
    print("=" * 72)
    print("CURRENT DC BY (SCANNER, OPTION_TYPE) — pre-fix baseline")
    print("=" * 72)
    print(f"{'Scanner':<24} {'dir':<6} {'n':>6} {'mean DC':>10} {'median DC':>11}")
    for (scanner, direction) in sorted(dc_by_direction.keys()):
        xs = dc_by_direction[(scanner, direction)]
        if not xs:
            continue
        print(f"{scanner:<24} {direction:<6} {len(xs):>6} "
              f"{statistics.mean(xs):>10.2f} {statistics.median(xs):>11.2f}")

    print()
    print("=" * 72)
    print("BREAKDOWN-SPECIFIC SAMPLE (5 positions) — inspect for fix impact")
    print("=" * 72)
    breakdown_puts = [p for p in all_positions
                      if str(p.get("scanner_source")) == "BREAKDOWN"
                      and infer_option_type_from_ticker(p.get("option_ticker")) == "PUT"]
    for p in breakdown_puts[:5]:
        print(f"  {p.get('underlying_ticker')} {p.get('entry_date')} "
              f"delta={f(p.get('entry_delta'))} "
              f"DC={f(p.get('pillar_directional_conviction'))} "
              f"conv={f(p.get('conviction_score'))} "
              f"pnl={f(p.get('current_pnl_pct'))}")

    print()
    print("=" * 72)
    print("SUMMARY & DECISIONS")
    print("=" * 72)
    breakdown_puts_ct = sum(1 for p in all_positions
                            if str(p.get("scanner_source")) == "BREAKDOWN"
                            and infer_option_type_from_ticker(p.get("option_ticker")) == "PUT")
    breakdown_total = sum(1 for p in all_positions if str(p.get("scanner_source")) == "BREAKDOWN")
    breakout_calls = sum(1 for p in all_positions
                         if str(p.get("scanner_source")) == "BREAKOUT"
                         and infer_option_type_from_ticker(p.get("option_ticker")) == "CALL")
    breakout_total = sum(1 for p in all_positions if str(p.get("scanner_source")) == "BREAKOUT")
    print(f"  BREAKDOWN: {breakdown_puts_ct}/{breakdown_total} are PUTs "
          f"({100*breakdown_puts_ct/max(breakdown_total,1):.1f}%)")
    print(f"  BREAKOUT: {breakout_calls}/{breakout_total} are CALLs "
          f"({100*breakout_calls/max(breakout_total,1):.1f}%)")
    mismatches = (
        ticker_vs_field.get(("CALL", "MISMATCH"), 0)
        + ticker_vs_field.get(("PUT", "MISMATCH"), 0)
    )
    print(f"  Ticker↔delta MISMATCHES: {mismatches}")
    print()
    if breakdown_puts_ct > 0 and mismatches < 10:
        print("  ✓ option_type propagation is clean — Phase B fix will apply cleanly.")
    else:
        print("  ⚠ Investigate mismatches before Phase B code change.")


if __name__ == "__main__":
    main()
