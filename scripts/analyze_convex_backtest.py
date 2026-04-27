#!/usr/bin/env python3
"""Inspect a Convex Phase 8 backtest JSON against §11 acceptance gates.

Usage:
    python scripts/analyze_convex_backtest.py \\
        baselines/2026-04-27-convex-phase8-backtest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# §11 acceptance gates (mirror app.convex.backtest.ValidationReport.passes_acceptance)
MIN_HIT_RATE = 30.0
MIN_RATIO = 3.0
MIN_TRADES = 50
MAX_CONSEC_LOSSES = 6


def _row(label: str, value, threshold, op: str) -> tuple[str, bool]:
    """Format a gate row and report PASS/FAIL."""
    if value is None:
        passed = False
        check = "FAIL (no data)"
    elif op == ">=":
        passed = value >= threshold
        check = "PASS" if passed else f"FAIL (need ≥ {threshold})"
    elif op == ">":
        passed = value > threshold
        check = "PASS" if passed else f"FAIL (need > {threshold})"
    elif op == "<=":
        passed = value <= threshold
        check = "PASS" if passed else f"FAIL (need ≤ {threshold})"
    else:
        passed = False
        check = "FAIL (unknown op)"
    return f"  {label:35} {str(value):>10}   {check}", passed


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("path", help="Path to backtest JSON")
    args = p.parse_args()

    data = json.loads(Path(args.path).read_text())
    report = data["validation_report"]
    meta = data.get("metadata", {})

    print(f"Backtest: {meta.get('start_date')} → {meta.get('end_date')}")
    print(f"Universe: {meta.get('universe_size')} tickers ({meta.get('universe_source')})")
    elapsed = meta.get("elapsed_seconds")
    if elapsed:
        print(f"Elapsed:  {elapsed:.1f}s")
    print()
    print("=== §11 Acceptance Gates ===")

    rows = []
    rows.append(_row("Total trades", report["total_trades"], MIN_TRADES, ">="))
    rows.append(_row("Hit rate %", report["hit_rate_pct"], MIN_HIT_RATE, ">="))
    rows.append(_row("Winner/loser ratio", report["winner_loser_ratio"], MIN_RATIO, ">="))
    rows.append(_row("Expectancy %", report["expectancy_pct"], 0, ">"))
    rows.append(
        _row("Max consecutive losses", report["max_consecutive_losses"], MAX_CONSEC_LOSSES, "<=")
    )

    tiers = report.get("tier_breakdown", {})
    a = tiers.get("A", {})
    c = tiers.get("C", {})
    if a.get("trades") and c.get("trades"):
        a_exp = a["expectancy_pct"]
        c_exp = c["expectancy_pct"]
        diff = a_exp - c_exp
        rows.append(_row("Tier A − Tier C expectancy %", round(diff, 2), 0, ">"))

    smart = report.get("smart_money_breakdown", {})
    sc = smart.get("confirmed", {})
    snc = smart.get("not_confirmed", {})
    if sc.get("trades") and snc.get("trades"):
        diff = sc["expectancy_pct"] - snc["expectancy_pct"]
        rows.append(_row("Smart Money lift %", round(diff, 2), 0, ">"))

    all_pass = True
    for line, ok in rows:
        print(line)
        all_pass = all_pass and ok

    print()
    print("=== Tier Breakdown ===")
    for t in ("A", "B", "C"):
        s = tiers.get(t, {})
        if s.get("trades"):
            print(
                f"  Tier {t}: trades={s['trades']:>4} "
                f"hit_rate={s['hit_rate_pct']:>5.1f}% "
                f"avg_pnl={s['avg_pnl_pct']:>+6.2f}% "
                f"expectancy={s['expectancy_pct']:>+6.2f}%"
            )
        else:
            print(f"  Tier {t}: no trades")

    print()
    print("=== Smart Money Cohort ===")
    for k, label in [("confirmed", "Confirmed"), ("not_confirmed", "Not confirmed")]:
        s = smart.get(k, {})
        print(
            f"  {label:14}: trades={s.get('trades', 0):>4} "
            f"hit_rate={s.get('hit_rate_pct', 0):>5.1f}% "
            f"expectancy={s.get('expectancy_pct', 0):>+6.2f}%"
        )

    print()
    print("=== Verdict ===")
    print("  passes_acceptance:", report.get("passes_acceptance"))
    print("  all gates:", "PASS" if all_pass else "FAIL")

    return 0 if (report.get("passes_acceptance") and all_pass) else 1


if __name__ == "__main__":
    sys.exit(main())
