#!/usr/bin/env python3
"""Deep comparative analysis of v4 vs v3 scoring on closed paper trades.

Pulls every closed position (~18.5k) with v3 + v4 scores alongside realized
P&L / MFE / MAE / exit-reason and produces:

  1. Pillar & composite score correlations vs outcomes (Pearson + Spearman)
  2. Win-rate + mean-P&L + MFE-capture decile breakdowns (v3 and v4)
  3. Lift curves: cumulative %-of-total-P&L captured by the top-K positions
  4. Grand-slam capture: what portion of 100%+ winners each tier contains
  5. Tier transition matrix: where v3 trades landed under v4
  6. Pillar-outcome heatmap per scanner
  7. Recommended TIER_1 threshold re-calibration

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/analyze_v4_vs_v3_performance.py [--out /tmp/v4_analysis.md]
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from decimal import Decimal
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


def pearson(x: list[float], y: list[float]) -> Optional[float]:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def spearman(x: list[float], y: list[float]) -> Optional[float]:
    """Spearman rank correlation."""
    n = len(x)
    if n < 3:
        return None

    def rank(values: list[float]) -> list[float]:
        indexed = sorted(enumerate(values), key=lambda t: t[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-indexed midpoint
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    return pearson(rank(x), rank(y))


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


def decile_buckets(rows: list[dict], sort_key: str, outcome_key: str) -> list[dict]:
    """Return 10 bucket summaries sorted by `sort_key`, lowest → highest."""
    rows = [r for r in rows if r.get(sort_key) is not None and r.get(outcome_key) is not None]
    rows.sort(key=lambda r: r[sort_key])
    n = len(rows)
    if n == 0:
        return []
    out: list[dict] = []
    for d in range(10):
        lo = int(n * d / 10)
        hi = int(n * (d + 1) / 10)
        bucket = rows[lo:hi]
        if not bucket:
            continue
        pnls = [r["pnl"] for r in bucket]
        mfes = [r["mfe"] for r in bucket if r.get("mfe") is not None]
        wins = sum(1 for p in pnls if p > 0)
        big_wins = sum(1 for p in pnls if p >= 100)
        stops = sum(1 for r in bucket if r.get("exit_reason") == "STOP_LOSS")
        out.append({
            "decile": d + 1,
            "n": len(bucket),
            "score_min": round(bucket[0][sort_key], 1),
            "score_max": round(bucket[-1][sort_key], 1),
            "mean_pnl": statistics.mean(pnls),
            "median_pnl": statistics.median(pnls),
            "win_rate": wins / len(bucket) * 100,
            "big_win_rate": big_wins / len(bucket) * 100,
            "stop_rate": stops / len(bucket) * 100,
            "mean_mfe": statistics.mean(mfes) if mfes else 0,
            "median_mfe": statistics.median(mfes) if mfes else 0,
        })
    return out


def print_decile_table(label: str, buckets: list[dict]) -> list[str]:
    lines = [
        "",
        f"### {label}",
        "",
        "| Decile | Score range | n | Win % | Big-win (≥100%) % | Stop % | Mean P&L % | Median P&L % | Mean MFE % | Median MFE % |",
        "|--------|-------------|---|-------|-------------------|--------|------------|---------------|------------|--------------|",
    ]
    for b in buckets:
        lines.append(
            f"| {b['decile']:>2} | {b['score_min']}–{b['score_max']} "
            f"| {b['n']:>4} | {b['win_rate']:>5.1f} | {b['big_win_rate']:>5.1f} "
            f"| {b['stop_rate']:>5.1f} | {b['mean_pnl']:>+7.1f} "
            f"| {b['median_pnl']:>+7.1f} | {b['mean_mfe']:>6.1f} "
            f"| {b['median_mfe']:>6.1f} |"
        )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/v4_analysis.md")
    args = parser.parse_args()

    print(f"Loading closed positions (region={AWS_REGION} prefix={TABLE_PREFIX})...")
    positions = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
        f"{TABLE_PREFIX}-paper-positions"
    )
    items = query_partition(positions, "POS#CLOSED")
    print(f"  loaded {len(items)} closed positions")

    # Project rows
    rows: list[dict] = []
    for p in items:
        pnl = f(p.get("current_pnl_pct"))
        if pnl is None:
            continue
        rows.append({
            "conv_v4": f(p.get("conviction_score")),
            "conv_v3": f(p.get("conviction_score_v3")),
            "dc": f(p.get("pillar_directional_conviction")),
            "mp": f(p.get("pillar_move_potential")),
            "ts": f(p.get("pillar_trade_structure")),
            "pl": f(p.get("pillar_premium_leverage")),
            "ub": f(p.get("pillar_underlying_behavior")),
            "sq": f(p.get("pillar_setup_quality")),
            "pnl": pnl,
            "mfe": f(p.get("max_favorable_excursion")),
            "mae": f(p.get("max_adverse_excursion")),
            "days_held": f(p.get("days_held")),
            "exit_reason": str(p.get("exit_reason") or ""),
            "scanner": str(p.get("scanner_source") or ""),
            "tier_at_entry": str(p.get("quality_tier_at_entry") or ""),
            "verdict_at_entry": str(p.get("verdict_at_entry") or ""),
        })
    print(f"  usable (with P&L): {len(rows)}")

    out: list[str] = []

    def w(line: str = "") -> None:
        out.append(line)
        print(line)

    # =========================================================================
    # Header
    # =========================================================================
    w("# v4 vs v3 Scoring — Deep Performance Analysis")
    w("")
    w(f"- **Closed paper trades analyzed:** {len(rows)}")
    wins_all = sum(1 for r in rows if r["pnl"] > 0)
    w(f"- **Overall win rate:** {wins_all / len(rows) * 100:.1f}%")
    w(f"- **Overall mean P&L:** {statistics.mean([r['pnl'] for r in rows]):+.2f}%")
    w(f"- **Overall median P&L:** {statistics.median([r['pnl'] for r in rows]):+.2f}%")
    big_wins = sum(1 for r in rows if r["pnl"] >= 100)
    w(f"- **Trades with ≥100% P&L:** {big_wins} ({big_wins / len(rows) * 100:.2f}%)")
    huge_wins = sum(1 for r in rows if r["pnl"] >= 200)
    w(f"- **Trades with ≥200% P&L (grand slams):** {huge_wins} ({huge_wins / len(rows) * 100:.2f}%)")
    w("")

    # =========================================================================
    # 1. Correlations
    # =========================================================================
    w("## 1. Score–Outcome Correlations")
    w("")
    w("| Score | vs P&L (Pearson) | vs P&L (Spearman) | vs MFE (Pearson) | vs MFE (Spearman) | n |")
    w("|-------|------------------|--------------------|------------------|--------------------|---|")

    def pair(key: str) -> tuple[list[float], list[float], list[float]]:
        xs, ys, ms = [], [], []
        for r in rows:
            if r.get(key) is None or r["pnl"] is None:
                continue
            xs.append(r[key])
            ys.append(r["pnl"])
            ms.append(r["mfe"] if r["mfe"] is not None else 0)
        return xs, ys, ms

    for label, key in [
        ("v4 conviction", "conv_v4"),
        ("v3 conviction", "conv_v3"),
        ("DC (v4)", "dc"),
        ("MP (v4)", "mp"),
        ("TS (v4)", "ts"),
        ("PL (v3)", "pl"),
        ("UB (v3)", "ub"),
        ("SQ (v3)", "sq"),
    ]:
        xs, ys, ms = pair(key)
        p_pnl = pearson(xs, ys)
        s_pnl = spearman(xs, ys)
        p_mfe = pearson(xs, ms)
        s_mfe = spearman(xs, ms)
        def fmt(x: Optional[float]) -> str:
            return f"{x:+.3f}" if x is not None else "—"
        w(f"| {label:<14} | {fmt(p_pnl):>16} | {fmt(s_pnl):>16} | {fmt(p_mfe):>16} | {fmt(s_mfe):>16} | {len(xs):>5} |")
    w("")
    w("**Interpretation:** A correlation of +0.10 means the score explains ~1% of outcome variance. Even small positive Spearman values are meaningful when ranking thousands of trades; negative values mean the score is anti-predictive (worse than random).")
    w("")

    # =========================================================================
    # 2. Decile breakdowns
    # =========================================================================
    w("## 2. Decile Breakdowns — Does Score Rank Predict Outcome?")
    w("")
    for label, key in [
        ("v4 conviction deciles", "conv_v4"),
        ("v3 conviction deciles", "conv_v3"),
        ("DC (v4) deciles", "dc"),
        ("MP (v4) deciles", "mp"),
        ("TS (v4) deciles", "ts"),
    ]:
        buckets = decile_buckets(rows, key, "pnl")
        for line in print_decile_table(label, buckets):
            w(line)
    w("")

    # =========================================================================
    # 3. Lift curves — cumulative P&L capture
    # =========================================================================
    w("## 3. Lift Curves — Cumulative P&L Captured by Top-K Positions")
    w("")
    w("If you could only take the top-K positions by score, how much of the total P&L would you capture?")
    w("")
    w("| Top % | v4 conviction | v3 conviction | Random baseline |")
    w("|-------|---------------|---------------|-----------------|")
    total_pnl = sum(r["pnl"] for r in rows)
    for top_pct in [5, 10, 20, 30, 40, 50]:
        k = int(len(rows) * top_pct / 100)
        def capture(key: str) -> float:
            sorted_rows = sorted(
                [r for r in rows if r.get(key) is not None],
                key=lambda r: r[key], reverse=True
            )[:k]
            return sum(r["pnl"] for r in sorted_rows) / total_pnl * 100 if total_pnl else 0
        v4_cap = capture("conv_v4")
        v3_cap = capture("conv_v3")
        w(f"| Top {top_pct:>2}% (n={k}) | {v4_cap:>+6.1f}% | {v3_cap:>+6.1f}% | {top_pct:>4}% |")
    w("")
    w("**Reading:** A random sampler would capture exactly top_pct% of total P&L. Numbers above top_pct% = score is predictive; numbers below = score is anti-predictive.")
    w("")

    # =========================================================================
    # 4. Grand-slam capture
    # =========================================================================
    w("## 4. Grand-Slam Capture — Where Do the Big Winners Live?")
    w("")
    thresholds = [50, 100, 200, 500]
    w("| P&L threshold | Total trades at threshold | v4 top-decile capture | v3 top-decile capture | Random baseline |")
    w("|---------------|---------------------------|-----------------------|------------------------|-----------------|")
    for thresh in thresholds:
        big = [r for r in rows if r["pnl"] >= thresh]
        total_big = len(big)
        if total_big == 0:
            w(f"| ≥ {thresh}% | 0 | — | — | 10% |")
            continue
        def top_decile(key: str) -> float:
            ranked = sorted(
                [r for r in rows if r.get(key) is not None],
                key=lambda r: r[key], reverse=True,
            )
            top_n = int(len(ranked) / 10)
            top_set = set(id(r) for r in ranked[:top_n])
            captured = sum(1 for r in big if id(r) in top_set)
            return captured / total_big * 100 if total_big else 0
        v4_cap = top_decile("conv_v4")
        v3_cap = top_decile("conv_v3")
        w(f"| ≥ {thresh}% | {total_big} | {v4_cap:>5.1f}% | {v3_cap:>5.1f}% | 10% |")
    w("")

    # =========================================================================
    # 5. Tier transition matrix
    # =========================================================================
    w("## 5. Tier Transition — Where Did v3 Trades Land in v4?")
    w("")

    def v3_tier(score: Optional[float]) -> str:
        if score is None:
            return "n/a"
        # v3 used different thresholds — let's infer from historical: APPROVE ≥60,
        # TIER_1 ≥75, TIER_2 ≥65, TIER_3 ≥60 (approximate from policy)
        if score >= 75: return "TIER_1"
        if score >= 65: return "TIER_2"
        if score >= 60: return "TIER_3"
        return "BELOW"

    def v4_tier(score: Optional[float]) -> str:
        if score is None:
            return "n/a"
        if score >= 92: return "TIER_1"
        if score >= 82: return "TIER_2"
        if score >= 72: return "TIER_3"
        if score >= 62: return "WATCH"
        return "REJECT"

    matrix: dict[tuple[str, str], int] = Counter()
    for r in rows:
        matrix[(v3_tier(r["conv_v3"]), v4_tier(r["conv_v4"]))] += 1
    v3_tiers = ["TIER_1", "TIER_2", "TIER_3", "BELOW"]
    v4_tiers = ["TIER_1", "TIER_2", "TIER_3", "WATCH", "REJECT"]
    w("| v3 ↓ / v4 → | " + " | ".join(v4_tiers) + " | Total |")
    w("|-------------|" + "|".join(["---"] * (len(v4_tiers) + 1)) + "|")
    for v3 in v3_tiers:
        row_total = sum(matrix[(v3, v4)] for v4 in v4_tiers)
        cells = [str(matrix[(v3, v4)]) for v4 in v4_tiers]
        w(f"| {v3} | " + " | ".join(cells) + f" | {row_total} |")
    w("")

    # =========================================================================
    # 6. Mean P&L by tier (v3 vs v4)
    # =========================================================================
    w("## 6. Mean P&L by Tier — v3 vs v4")
    w("")
    w("| Tier | v3: trades | v3: mean P&L | v3: win % | v4: trades | v4: mean P&L | v4: win % |")
    w("|------|------------|--------------|-----------|------------|--------------|-----------|")
    for tier in ["TIER_1", "TIER_2", "TIER_3"]:
        v3_rows = [r for r in rows if v3_tier(r["conv_v3"]) == tier]
        v4_rows = [r for r in rows if v4_tier(r["conv_v4"]) == tier]
        def stats(rs: list[dict]) -> str:
            if not rs:
                return "0 | — | —"
            wins = sum(1 for r in rs if r["pnl"] > 0)
            return f"{len(rs)} | {statistics.mean([r['pnl'] for r in rs]):+6.2f}% | {wins/len(rs)*100:5.1f}%"
        w(f"| {tier} | {stats(v3_rows)} | {stats(v4_rows)} |")
    # Extra v4 row: WATCH
    watch_rows = [r for r in rows if v4_tier(r["conv_v4"]) == "WATCH"]
    w(f"| WATCH (v4 only) | — | — | — | {len(watch_rows)} | {statistics.mean([r['pnl'] for r in watch_rows]) if watch_rows else 0:+6.2f}% | {sum(1 for r in watch_rows if r['pnl'] > 0) / max(len(watch_rows), 1) * 100:5.1f}% |")
    reject_rows = [r for r in rows if v4_tier(r["conv_v4"]) == "REJECT"]
    w(f"| REJECT (v4 only) | — | — | — | {len(reject_rows)} | {statistics.mean([r['pnl'] for r in reject_rows]) if reject_rows else 0:+6.2f}% | {sum(1 for r in reject_rows if r['pnl'] > 0) / max(len(reject_rows), 1) * 100:5.1f}% |")
    w("")

    # =========================================================================
    # 7. Pillar performance by scanner
    # =========================================================================
    w("## 7. Pillar Means by Scanner — Which Scanner Rewards Which Pillar?")
    w("")
    w("| Scanner | n | DC | MP | TS | v4 conv | v3 conv | mean P&L | win % |")
    w("|---------|---|----|----|----|---------|---------|----------|-------|")
    scanners = sorted(set(r["scanner"] for r in rows if r["scanner"]))
    for s in scanners:
        rs = [r for r in rows if r["scanner"] == s]
        def avg(k: str) -> str:
            vs = [r[k] for r in rs if r.get(k) is not None]
            return f"{statistics.mean(vs):5.1f}" if vs else "—"
        wins = sum(1 for r in rs if r["pnl"] > 0)
        w(f"| {s:<18} | {len(rs):>4} | {avg('dc')} | {avg('mp')} | {avg('ts')} "
          f"| {avg('conv_v4')} | {avg('conv_v3')} | {statistics.mean([r['pnl'] for r in rs]):+6.2f}% | {wins/len(rs)*100:5.1f}% |")
    w("")

    # =========================================================================
    # 8. Recommended v4 TIER_1 threshold recalibration
    # =========================================================================
    w("## 8. Recommended TIER_1 Threshold Recalibration")
    w("")
    w("Current TIER_1 threshold (≥92) catches 0 historical trades. The sharpshooter thesis implies TIER_1 should be the top ~2% of trades. Below: what each candidate threshold yields on historical data.")
    w("")
    w("| v4 threshold | Trades qualifying | % of all | Mean P&L | Win % | Big-win (≥100%) % |")
    w("|--------------|-------------------|----------|----------|-------|---------------------|")
    for thresh in [92, 88, 85, 82, 80, 78, 75]:
        rs = [r for r in rows if r["conv_v4"] is not None and r["conv_v4"] >= thresh]
        if not rs:
            w(f"| ≥ {thresh} | 0 | 0.00% | — | — | — |")
            continue
        pnls = [r["pnl"] for r in rs]
        wins = sum(1 for p in pnls if p > 0)
        bigs = sum(1 for p in pnls if p >= 100)
        w(f"| ≥ {thresh} | {len(rs)} | {len(rs)/len(rows)*100:5.2f}% | {statistics.mean(pnls):+6.2f}% | {wins/len(rs)*100:5.1f}% | {bigs/len(rs)*100:5.2f}% |")
    w("")

    # =========================================================================
    # 9. Headline creative insight: top and bottom pillar-rank crossings
    # =========================================================================
    w("## 9. Interesting Crossings — Where v4 Disagreed Most with v3")
    w("")

    def pct_rank(rs: list[dict], key: str) -> dict[int, float]:
        vals = [(i, r.get(key)) for i, r in enumerate(rs) if r.get(key) is not None]
        vals.sort(key=lambda t: t[1])
        out = {}
        for i, (idx, _) in enumerate(vals):
            out[idx] = i / max(len(vals) - 1, 1) * 100
        return out

    v4r = pct_rank(rows, "conv_v4")
    v3r = pct_rank(rows, "conv_v3")
    deltas: list[tuple[int, float]] = []
    for i, r in enumerate(rows):
        if i in v4r and i in v3r:
            deltas.append((i, v4r[i] - v3r[i]))

    # Top 10 "v4 loved what v3 ignored"
    deltas.sort(key=lambda t: t[1], reverse=True)
    w("### Top 10: v4 promoted most vs v3 (score ranks jumped up)")
    w("")
    w("| Scanner | Tier@entry | v3 conv | v4 conv | Δ rank (pts) | P&L | Exit |")
    w("|---------|------------|---------|---------|---------------|-----|------|")
    for i, delta in deltas[:10]:
        r = rows[i]
        w(f"| {r['scanner']:<10} | {r['tier_at_entry']:<8} | {r['conv_v3']:.1f} | {r['conv_v4']:.1f} | +{delta:.1f} | {r['pnl']:+.1f}% | {r['exit_reason']} |")
    w("")

    # Bottom 10 "v4 rejected what v3 loved"
    w("### Top 10: v4 demoted most vs v3 (score ranks dropped)")
    w("")
    w("| Scanner | Tier@entry | v3 conv | v4 conv | Δ rank (pts) | P&L | Exit |")
    w("|---------|------------|---------|---------|---------------|-----|------|")
    for i, delta in deltas[-10:]:
        r = rows[i]
        w(f"| {r['scanner']:<10} | {r['tier_at_entry']:<8} | {r['conv_v3']:.1f} | {r['conv_v4']:.1f} | {delta:+.1f} | {r['pnl']:+.1f}% | {r['exit_reason']} |")
    w("")

    # Did the promotions (v4 liked, v3 didn't) pay off?
    promoted = [rows[i] for i, d in deltas if d > 20]
    demoted = [rows[i] for i, d in deltas if d < -20]
    if promoted and demoted:
        w("### Did the re-ranking pay off?")
        w("")
        w(f"- **v4-promoted cohort** (v4 rank > v3 rank by >20 pts, n={len(promoted)}): "
          f"mean P&L = {statistics.mean([r['pnl'] for r in promoted]):+.2f}%, "
          f"win rate = {sum(1 for r in promoted if r['pnl']>0)/len(promoted)*100:.1f}%")
        w(f"- **v4-demoted cohort** (v4 rank < v3 rank by >20 pts, n={len(demoted)}): "
          f"mean P&L = {statistics.mean([r['pnl'] for r in demoted]):+.2f}%, "
          f"win rate = {sum(1 for r in demoted if r['pnl']>0)/len(demoted)*100:.1f}%")
        w(f"- **Population baseline** (all {len(rows)} trades): "
          f"mean P&L = {statistics.mean([r['pnl'] for r in rows]):+.2f}%, "
          f"win rate = {wins_all/len(rows)*100:.1f}%")
        w("")
        w("If v4 is genuinely better than v3: promoted cohort beats baseline, demoted cohort underperforms baseline.")
    w("")

    # Save to file
    with open(args.out, "w") as fh:
        fh.write("\n".join(out))
    print(f"\n\nFull analysis written to: {args.out}")


if __name__ == "__main__":
    main()
