#!/usr/bin/env python3
"""Deep diagnosis: why does v4.0.1 predict well at ≥78 but fail at ≥80,
and where do actual home runs live in the score distribution?

This script answers three questions rigorously:

  A. WHAT CHANGES between the ≥78 cohort (117 trades, 64% win, +25% mean)
     and the ≥80 cohort (27 trades, 44% win, -5% mean)? Scanner mix,
     option direction, DTE, delta, IV, entry date, individual trade
     anatomy — we dissect the 27 outliers one by one.

  B. Where do ≥100% and ≥200% winners actually live in the score
     distribution? Score histogram of home runs. Scanner breakdown.
     How often does v4.0.1 correctly identify them ex-ante.

  C. Subscore-level predictiveness: which of the 16 v4 subscores is
     genuinely predictive of home runs vs noise? Could a simpler
     "best-of-3-pillars" or "threshold-AND" composite beat the
     weighted geometric mean?

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/home_run_diagnosis.py
"""

from __future__ import annotations

import math
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

OPT_TICKER_RE = re.compile(r"^O:([A-Z]+)(\d{6})([CP])(\d+)$")


def f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pearson(x: list[float], y: list[float]) -> Optional[float]:
    n = len(x)
    if n < 10:
        return None
    mx, my = sum(x) / n, sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def spearman(x: list[float], y: list[float]) -> Optional[float]:
    n = len(x)
    if n < 10:
        return None

    def rank(values: list[float]) -> list[float]:
        indexed = sorted(enumerate(values), key=lambda t: t[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg
            i = j + 1
        return ranks

    return pearson(rank(x), rank(y))


def parse_opt_ticker(t: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[float]]:
    """Return (option_type, expiry_yymmdd, strike_dollars) or Nones."""
    if not t:
        return None, None, None
    m = OPT_TICKER_RE.match(t)
    if not m:
        return None, None, None
    typ = "CALL" if m.group(3) == "C" else "PUT"
    strike = int(m.group(4)) / 1000.0
    return typ, m.group(2), strike


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
    print(f"Loading closed positions (region={AWS_REGION} prefix={TABLE_PREFIX})...")
    positions = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
        f"{TABLE_PREFIX}-paper-positions"
    )
    items = query_partition(positions, "POS#CLOSED")
    fvt = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
        f"{TABLE_PREFIX}-feature-values"
    )
    evals = boto3.resource("dynamodb", region_name=AWS_REGION).Table(
        f"{TABLE_PREFIX}-evaluations"
    )
    print(f"  loaded {len(items)} closed positions\n")

    rows: list[dict] = []
    for p in items:
        pnl = f(p.get("current_pnl_pct"))
        if pnl is None:
            continue
        opt_type, expiry, strike = parse_opt_ticker(p.get("option_ticker"))
        rows.append({
            "ticker": str(p.get("underlying_ticker") or ""),
            "eval_id": str(p.get("evaluation_id") or ""),
            "entry_date": str(p.get("entry_date") or ""),
            "exit_date": str(p.get("exit_date") or ""),
            "days_held": f(p.get("days_held")),
            "conv_v4": f(p.get("conviction_score")),
            "conv_v3": f(p.get("conviction_score_v3")),
            "dc": f(p.get("pillar_directional_conviction")),
            "mp": f(p.get("pillar_move_potential")),
            "ts": f(p.get("pillar_trade_structure")),
            "pnl": pnl,
            "mfe": f(p.get("max_favorable_excursion")),
            "mae": f(p.get("max_adverse_excursion")),
            "exit_reason": str(p.get("exit_reason") or ""),
            "scanner": str(p.get("scanner_source") or ""),
            "option_type": opt_type,
            "strike": strike,
            "entry_delta": f(p.get("entry_delta")),
            "entry_iv": f(p.get("entry_iv")),
            "entry_iv_percentile": f(p.get("entry_iv_percentile")),
            "entry_price": f(p.get("entry_price")),
            "entry_underlying_price": f(p.get("entry_underlying_price")),
        })
    print(f"  usable with conv_v4: {len(rows)}\n")

    # =========================================================================
    # SECTION A — THE ≥80 ANOMALY
    # =========================================================================
    print("=" * 78)
    print("SECTION A — THE ≥80 ANOMALY: why 78→80 performance collapses")
    print("=" * 78)

    cohort_78 = [r for r in rows if r["conv_v4"] is not None and r["conv_v4"] >= 78]
    cohort_80 = [r for r in rows if r["conv_v4"] is not None and r["conv_v4"] >= 80]
    cohort_82 = [r for r in rows if r["conv_v4"] is not None and r["conv_v4"] >= 82]
    cohort_78_to_80 = [r for r in rows if r["conv_v4"] is not None and 78 <= r["conv_v4"] < 80]
    cohort_80_to_82 = [r for r in rows if r["conv_v4"] is not None and 80 <= r["conv_v4"] < 82]
    cohort_75_to_78 = [r for r in rows if r["conv_v4"] is not None and 75 <= r["conv_v4"] < 78]

    def describe(label: str, cohort: list[dict]) -> None:
        if not cohort:
            print(f"\n{label}: EMPTY")
            return
        pnls = [r["pnl"] for r in cohort]
        wins = sum(1 for p in pnls if p > 0)
        bigs = sum(1 for p in pnls if p >= 100)
        stops = sum(1 for r in cohort if r["exit_reason"] == "STOP_LOSS")
        tgts = sum(1 for r in cohort if r["exit_reason"] == "PROFIT_TARGET")
        mfes = [r["mfe"] for r in cohort if r["mfe"] is not None]
        calls = sum(1 for r in cohort if r["option_type"] == "CALL")
        puts = sum(1 for r in cohort if r["option_type"] == "PUT")
        deltas = [r["entry_delta"] for r in cohort if r["entry_delta"] is not None]
        ivs = [r["entry_iv_percentile"] for r in cohort if r["entry_iv_percentile"] is not None]
        days = [r["days_held"] for r in cohort if r["days_held"] is not None]

        print(f"\n{label} (n={len(cohort)}):")
        print(f"  mean P&L: {statistics.mean(pnls):+.2f}%  median: {statistics.median(pnls):+.2f}%")
        print(f"  win rate: {wins/len(cohort)*100:.1f}%  stops: {stops/len(cohort)*100:.1f}%  "
              f"profit-targets: {tgts/len(cohort)*100:.1f}%")
        print(f"  big wins (≥100%): {bigs} ({bigs/len(cohort)*100:.1f}%)")
        if mfes:
            print(f"  mean MFE: {statistics.mean(mfes):+.1f}%  median: {statistics.median(mfes):+.1f}%")
        print(f"  CALL/PUT: {calls}/{puts}")
        if deltas:
            print(f"  abs(delta) mean: {statistics.mean([abs(d) for d in deltas]):.2f}")
        if ivs:
            print(f"  IV percentile mean: {statistics.mean(ivs):.1f}")
        if days:
            print(f"  days held mean: {statistics.mean(days):.1f}")

        # Scanner breakdown
        scanners = Counter(r["scanner"] for r in cohort)
        total = len(cohort)
        scanner_detail = ", ".join(f"{s}={c}({c/total*100:.0f}%)"
                                   for s, c in scanners.most_common())
        print(f"  scanners: {scanner_detail}")

    describe("75-78 cohort  (below TIER_1-candidate threshold)", cohort_75_to_78)
    describe("78-80 cohort  (the sweet spot — 64% win)", cohort_78_to_80)
    describe("80-82 cohort  (the anomaly — 44% win, no big wins)", cohort_80_to_82)
    describe("≥82 cohort  (tiny sample)", cohort_82)

    # The smoking gun: inspect every single ≥80 trade one by one
    print()
    print("=" * 78)
    print("EVERY SINGLE ≥80 TRADE (n=27) — individual examination")
    print("=" * 78)
    by80 = sorted(cohort_80, key=lambda r: r["conv_v4"] or 0, reverse=True)
    print(f"{'#':>3} {'ticker':<7} {'scanner':<12} {'type':<4} {'conv':>6} "
          f"{'DC':>5} {'MP':>5} {'TS':>5} {'pnl':>8} {'mfe':>7} {'exit':<14} {'days':>4}")
    for i, r in enumerate(by80, 1):
        print(f"{i:>3} {r['ticker']:<7} {r['scanner'][:12]:<12} "
              f"{r['option_type'] or '?':<4} {r['conv_v4']:>6.2f} "
              f"{r['dc'] or 0:>5.1f} {r['mp'] or 0:>5.1f} {r['ts'] or 0:>5.1f} "
              f"{r['pnl']:>+7.1f}% {r['mfe'] or 0:>+6.1f}% "
              f"{r['exit_reason'][:14]:<14} {r['days_held'] or 0:>4.0f}")

    # =========================================================================
    # SECTION B — WHERE DO HOME RUNS ACTUALLY LIVE?
    # =========================================================================
    print()
    print("=" * 78)
    print("SECTION B — HOME-RUN FINGERPRINT: where in the distribution do")
    print("            ≥100%, ≥200%, and ≥500% winners actually sit?")
    print("=" * 78)

    bigs_100 = [r for r in rows if r["pnl"] >= 100]
    bigs_200 = [r for r in rows if r["pnl"] >= 200]
    bigs_500 = [r for r in rows if r["pnl"] >= 500]
    print(f"\nTotal home runs by threshold:")
    print(f"  ≥100%: {len(bigs_100)} trades ({len(bigs_100)/len(rows)*100:.2f}% of all)")
    print(f"  ≥200%: {len(bigs_200)} trades ({len(bigs_200)/len(rows)*100:.2f}% of all)")
    print(f"  ≥500%: {len(bigs_500)} trades ({len(bigs_500)/len(rows)*100:.2f}% of all)")

    # Where in the v4.0.1 score distribution do home runs sit?
    def score_histogram(cohort: list[dict], label: str) -> None:
        print(f"\n{label} — v4.0.1 conviction distribution:")
        scores = [r["conv_v4"] for r in cohort if r["conv_v4"] is not None]
        if not scores:
            print("  (no scores)")
            return
        buckets = [
            (0, 30),
            (30, 40),
            (40, 50),
            (50, 55),
            (55, 60),
            (60, 65),
            (65, 70),
            (70, 75),
            (75, 80),
            (80, 85),
            (85, 100),
        ]
        for lo, hi in buckets:
            n = sum(1 for s in scores if lo <= s < hi)
            pct = n / len(scores) * 100 if scores else 0
            bar = "█" * int(pct / 2)
            print(f"  [{lo:>3},{hi:>3}): {n:>4} ({pct:5.1f}%)  {bar}")

    score_histogram(bigs_100, "≥100% winners (n={})".format(len(bigs_100)))
    score_histogram(bigs_200, "≥200% winners (grand slams)")
    score_histogram(bigs_500, "≥500% winners (legendary)")
    # And the baseline
    score_histogram(rows, "ALL TRADES (for reference)")

    # Scanner contribution to home runs
    print()
    print("=" * 78)
    print("SECTION B.2 — SCANNER CONTRIBUTION TO HOME RUNS")
    print("=" * 78)
    total_trades = len(rows)
    scanner_totals = Counter(r["scanner"] for r in rows)
    scanner_hrs_100 = Counter(r["scanner"] for r in bigs_100)
    scanner_hrs_200 = Counter(r["scanner"] for r in bigs_200)
    scanner_hrs_500 = Counter(r["scanner"] for r in bigs_500)

    print(f"\n{'Scanner':<22} {'trades':>8} {'% of all':>9} "
          f"{'≥100% HR':>9} {'hit rate':>9} "
          f"{'≥200% HR':>9} {'hit rate':>9} {'≥500%':>6}")
    for scanner in sorted(scanner_totals.keys()):
        st = scanner_totals[scanner]
        if st == 0:
            continue
        h100 = scanner_hrs_100[scanner]
        h200 = scanner_hrs_200[scanner]
        h500 = scanner_hrs_500[scanner]
        print(f"{scanner:<22} {st:>8} {st/total_trades*100:>8.1f}% "
              f"{h100:>9} {h100/st*100:>8.2f}% "
              f"{h200:>9} {h200/st*100:>8.2f}% {h500:>6}")

    # Where would we CATCH home runs if we just took top-K by v4.0.1?
    print()
    print("=" * 78)
    print("SECTION B.3 — HOME-RUN CATCH RATE BY CONVICTION THRESHOLD")
    print("=" * 78)
    print("\nIf we only took trades with v4.0.1 conviction ≥ threshold, how many")
    print("home runs would we capture? (Baseline: taking 10% of trades should")
    print("catch ~10% of home runs if random.)")
    thresholds = [50, 55, 60, 65, 70, 75, 78, 80]
    total_hr_100 = len(bigs_100)
    total_hr_200 = len(bigs_200)
    print(f"\n{'≥ threshold':<12} {'trades':>7} {'% of all':>9} "
          f"{'≥100% caught':>13} {'catch rate':>12} "
          f"{'≥200% caught':>13} {'catch rate':>12}")
    for t in thresholds:
        taken = [r for r in rows if r["conv_v4"] is not None and r["conv_v4"] >= t]
        n_t = len(taken)
        hr100 = sum(1 for r in taken if r["pnl"] >= 100)
        hr200 = sum(1 for r in taken if r["pnl"] >= 200)
        print(f"≥ {t:<10} {n_t:>7} {n_t/total_trades*100:>8.1f}% "
              f"{hr100:>13} {hr100/total_hr_100*100:>11.1f}% "
              f"{hr200:>13} {hr200/total_hr_200*100:>11.1f}%")

    # =========================================================================
    # SECTION C — ARE UNUSUAL_VOLUME + CHEAP_OPTIONS NOISE OR DIAMONDS?
    # =========================================================================
    print()
    print("=" * 78)
    print("SECTION C — NOISE vs DIAMONDS: UNUSUAL_VOLUME + CHEAP_OPTIONS")
    print("=" * 78)
    print("\nThese two scanners produce 87% of trades. Question: do they")
    print("contain the home runs we care about, or can we drop them?\n")

    for sc in ["UNUSUAL_VOLUME", "CHEAP_OPTIONS"]:
        scanner_trades = [r for r in rows if r["scanner"] == sc]
        sc_n = len(scanner_trades)
        if sc_n == 0:
            continue
        sc_hrs_100 = [r for r in scanner_trades if r["pnl"] >= 100]
        sc_hrs_200 = [r for r in scanner_trades if r["pnl"] >= 200]
        sc_hrs_500 = [r for r in scanner_trades if r["pnl"] >= 500]
        mean_pnl = statistics.mean([r["pnl"] for r in scanner_trades])
        wins = sum(1 for r in scanner_trades if r["pnl"] > 0)

        print(f"\n  {sc}:")
        print(f"    trades: {sc_n}  mean P&L: {mean_pnl:+.2f}%  win rate: {wins/sc_n*100:.1f}%")
        print(f"    home runs (≥100%): {len(sc_hrs_100)} ({len(sc_hrs_100)/sc_n*100:.2f}%)")
        print(f"    home runs (≥200%): {len(sc_hrs_200)} ({len(sc_hrs_200)/sc_n*100:.2f}%)")
        print(f"    home runs (≥500%): {len(sc_hrs_500)}")
        # What would v4.0.1 score do on these HRs?
        if sc_hrs_200:
            hr200_scores = [r["conv_v4"] for r in sc_hrs_200 if r["conv_v4"] is not None]
            if hr200_scores:
                print(f"    v4.0.1 score of grand slams: "
                      f"mean {statistics.mean(hr200_scores):.1f}, "
                      f"median {statistics.median(hr200_scores):.1f}, "
                      f"max {max(hr200_scores):.1f}")

    # =========================================================================
    # SECTION D — WHAT DOES A HOME-RUN TRADE LOOK LIKE AT ENTRY?
    # =========================================================================
    print()
    print("=" * 78)
    print("SECTION D — HOME-RUN ENTRY FINGERPRINT (≥200% winners)")
    print("=" * 78)

    def entry_stats(cohort: list[dict], label: str) -> None:
        print(f"\n{label} (n={len(cohort)}):")
        if not cohort:
            return
        for fld, human in [
            ("entry_delta", "entry_delta"),
            ("entry_iv", "entry_iv (decimal)"),
            ("entry_iv_percentile", "entry_iv_percentile"),
            ("days_held", "days_held"),
            ("dc", "DC pillar"),
            ("mp", "MP pillar"),
            ("ts", "TS pillar"),
            ("conv_v4", "v4 conviction"),
            ("conv_v3", "v3 conviction"),
            ("mfe", "MFE"),
        ]:
            vals = [r[fld] for r in cohort if r.get(fld) is not None]
            if not vals:
                continue
            vals = [float(v) for v in vals]
            if fld == "entry_delta":
                vals = [abs(v) for v in vals]
                human = "|entry_delta|"
            print(f"  {human:<22} n={len(vals):>4}  "
                  f"mean={statistics.mean(vals):>7.2f}  "
                  f"median={statistics.median(vals):>7.2f}  "
                  f"min={min(vals):>7.2f}  max={max(vals):>7.2f}")

        # Option type split
        calls = sum(1 for r in cohort if r["option_type"] == "CALL")
        puts = sum(1 for r in cohort if r["option_type"] == "PUT")
        print(f"  CALL/PUT: {calls}/{puts} "
              f"({calls/len(cohort)*100:.0f}%/{puts/len(cohort)*100:.0f}%)")

        # Scanner dist
        scanners = Counter(r["scanner"] for r in cohort)
        total = len(cohort)
        for s, c in scanners.most_common():
            # Compare to baseline rate
            baseline_rate = scanner_totals[s] / total_trades
            hr_rate = c / total
            lift = hr_rate / baseline_rate if baseline_rate > 0 else 0
            print(f"  {s:<22} {c:>4} ({c/total*100:>5.1f}%)  "
                  f"baseline {baseline_rate*100:>4.1f}%  lift x{lift:.2f}")

    entry_stats(bigs_200, "≥200% winners")
    entry_stats(bigs_500, "≥500% winners")
    entry_stats([r for r in rows if r["pnl"] <= -50], "≤-50% losers (for contrast)")

    # =========================================================================
    # SECTION E — ALTERNATIVE COMPOSITE FORMULAS
    # =========================================================================
    print()
    print("=" * 78)
    print("SECTION E — ALTERNATIVE COMPOSITES: is geometric mean the right")
    print("            aggregator? Could a simpler rule do better?")
    print("=" * 78)

    # Candidates:
    # 1. MAX(DC, MP, TS)  — "best pillar wins"
    # 2. MIN(DC, MP, TS)  — "weakest link dominates"
    # 3. arithmetic mean, equal weight
    # 4. threshold-AND: score=min if ALL ≥60, else 0
    # 5. threshold-AND at 50: score=min if ALL ≥50, else 0
    # 6. DC * MP (no TS)   — maybe TS is noise
    # 7. MP alone          — MP was least-biased pillar

    intact = [r for r in rows if r["dc"] is not None and r["mp"] is not None and r["ts"] is not None]
    print(f"\nBase: {len(intact)} trades with all 3 pillars present")

    def composite_stats(name: str, fn: Any) -> None:
        scores_pnl = [(fn(r), r["pnl"]) for r in intact]
        scores_pnl = [(s, p) for s, p in scores_pnl if s is not None]
        if not scores_pnl:
            print(f"  {name}: no data")
            return
        xs = [s for s, _ in scores_pnl]
        ys = [p for _, p in scores_pnl]
        p = pearson(xs, ys) or 0
        sp = spearman(xs, ys) or 0
        # Top 5%, top 10% P&L capture
        sorted_by_score = sorted(scores_pnl, key=lambda t: t[0], reverse=True)
        total_pnl = sum(ys)
        k5 = max(1, int(len(scores_pnl) * 0.05))
        k10 = max(1, int(len(scores_pnl) * 0.10))
        top5_pnl = sum(p for _, p in sorted_by_score[:k5])
        top10_pnl = sum(p for _, p in sorted_by_score[:k10])
        top5_cap = top5_pnl / total_pnl * 100 if total_pnl else 0
        top10_cap = top10_pnl / total_pnl * 100 if total_pnl else 0
        # HR catch at top 5% and 10%
        top5_hr100 = sum(1 for _, p in sorted_by_score[:k5] if p >= 100)
        top10_hr100 = sum(1 for _, p in sorted_by_score[:k10] if p >= 100)
        top5_hr200 = sum(1 for _, p in sorted_by_score[:k5] if p >= 200)
        top10_hr200 = sum(1 for _, p in sorted_by_score[:k10] if p >= 200)
        total_hr100 = sum(1 for _, p in scores_pnl if p >= 100)
        total_hr200 = sum(1 for _, p in scores_pnl if p >= 200)
        print(f"  {name:<30} Pears={p:+.3f}  Spear={sp:+.3f}  "
              f"top5%P&L={top5_cap:+5.0f}%  top10%P&L={top10_cap:+5.0f}%  "
              f"top5%-catches-HR100/200={top5_hr100}/{total_hr100},{top5_hr200}/{total_hr200}")

    print()
    print("Composite formulas (higher Pearson → better; higher P&L capture → better):")
    print()
    # Geometric mean (current v4 formula, 40/35/25 exponents)
    def geo_mean(r: dict) -> float:
        dc = max(1.0, r["dc"])
        mp = max(1.0, r["mp"])
        ts = max(1.0, r["ts"])
        return math.exp(0.40 * math.log(dc) + 0.35 * math.log(mp) + 0.25 * math.log(ts))
    composite_stats("v4.0.1 geo mean (current)", geo_mean)
    composite_stats("arithmetic mean 40/35/25",
                    lambda r: 0.40 * r["dc"] + 0.35 * r["mp"] + 0.25 * r["ts"])
    composite_stats("arithmetic equal-weight",
                    lambda r: (r["dc"] + r["mp"] + r["ts"]) / 3)
    composite_stats("MAX(DC,MP,TS)",
                    lambda r: max(r["dc"], r["mp"], r["ts"]))
    composite_stats("MIN(DC,MP,TS)",
                    lambda r: min(r["dc"], r["mp"], r["ts"]))
    composite_stats("MP alone",
                    lambda r: r["mp"])
    composite_stats("DC alone",
                    lambda r: r["dc"])
    composite_stats("TS alone",
                    lambda r: r["ts"])
    composite_stats("MP * TS (no DC)",
                    lambda r: (r["mp"] + r["ts"]) / 2)
    composite_stats("DC * MP (no TS)",
                    lambda r: (r["dc"] + r["mp"]) / 2)
    composite_stats("v3 conv (legacy reference)",
                    lambda r: r["conv_v3"] if r.get("conv_v3") is not None else 0)

    # Threshold-AND compositions
    def threshold_and(threshold: float, r: dict) -> float:
        if min(r["dc"], r["mp"], r["ts"]) < threshold:
            return 0.0
        return (r["dc"] + r["mp"] + r["ts"]) / 3

    print()
    print("Threshold-AND composites (require ALL pillars ≥ threshold):")
    for t in [40, 50, 55, 60]:
        composite_stats(f"ALL≥{t} else 0",
                        lambda r, t=t: threshold_and(t, r))

    # =========================================================================
    # SECTION F — SUBSCORE-LEVEL PREDICTIVENESS FOR HOME RUNS
    # =========================================================================
    print()
    print("=" * 78)
    print("SECTION F — SUBSCORE-LEVEL HOME-RUN PREDICTIVENESS")
    print("=" * 78)
    print("\nFor each v4 raw feature, compare values on home runs vs losers.")
    print("Pull a 2000-trade sample from FVT.\n")

    import random
    random.seed(7)
    # Stratified: all home runs + a sample of losers capped to keep total ~2000
    losers = [r for r in rows if r["pnl"] <= -50]
    loser_cap = max(0, 2000 - len(bigs_100))
    loser_sample = random.sample(losers, min(loser_cap, len(losers)))
    sample = bigs_100 + loser_sample
    print(f"  stratified sample: {len(bigs_100)} home runs + {len(loser_sample)} losers "
          f"= {len(sample)} trades")

    feats_to_check = [
        "rs_20d", "adx_14", "plus_di", "minus_di", "iv_percentile",
        "iv_rv_ratio", "bb_width_percentile", "historical_move_magnitude",
        "sector_rs_20d", "ma_150", "ma_200",
    ]

    vals: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for i, r in enumerate(sample):
        if i % 300 == 0 and i > 0:
            print(f"  loaded {i}/{len(sample)}...")
        eval_id = r["eval_id"]
        if not eval_id:
            continue
        try:
            resp = fvt.query(
                KeyConditionExpression=Key("PK").eq(f"EVAL#{eval_id}")
                & Key("SK").begins_with("FEATURE#"),
            )
        except Exception:
            continue
        for item in resp.get("Items", []):
            name = str(item.get("SK", "")).replace("FEATURE#", "")
            if name in feats_to_check:
                v = f(item.get("value"))
                if v is not None:
                    vals[name].append((v, r["pnl"]))

    print()
    print(f"{'Feature':<28} {'n':>5} {'Pear':>7} {'Spear':>7} "
          f"{'HR-mean':>9} {'non-HR-mean':>12} {'t-stat':>8}")
    for feat in feats_to_check:
        pairs = vals.get(feat, [])
        if len(pairs) < 50:
            print(f"  {feat:<28} {len(pairs):>5} (insufficient)")
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        p = pearson(xs, ys)
        sp = spearman(xs, ys)

        hr_vals = [v for v, pnl in pairs if pnl >= 100]
        non_hr_vals = [v for v, pnl in pairs if pnl < 100]
        if len(hr_vals) >= 5 and len(non_hr_vals) >= 5:
            hr_mean = statistics.mean(hr_vals)
            non_hr_mean = statistics.mean(non_hr_vals)
            # Welch's t (rough)
            hr_std = statistics.stdev(hr_vals) if len(hr_vals) > 1 else 1
            non_std = statistics.stdev(non_hr_vals) if len(non_hr_vals) > 1 else 1
            se = math.sqrt(hr_std**2/len(hr_vals) + non_std**2/len(non_hr_vals))
            t = (hr_mean - non_hr_mean) / se if se > 0 else 0
        else:
            hr_mean = non_hr_mean = t = 0
        fmt_p = f"{p:+.3f}" if p is not None else "—"
        fmt_sp = f"{sp:+.3f}" if sp is not None else "—"
        print(f"  {feat:<28} {len(pairs):>5} {fmt_p:>7} {fmt_sp:>7} "
              f"{hr_mean:>9.2f} {non_hr_mean:>11.2f} {t:>+7.2f}")

    # =========================================================================
    # SECTION G — ENTRY-DATE (REGIME) ANALYSIS
    # =========================================================================
    print()
    print("=" * 78)
    print("SECTION G — ENTRY-DATE REGIME: do v4 scores work in some")
    print("            market conditions but not others?")
    print("=" * 78)

    # Break entries into 2-week buckets and report v4 conv → P&L correlation
    # per bucket. If regime is the issue, correlations will flip sign over time.
    def week_bucket(date_str: str) -> str:
        # YYYY-MM-DD → YYYY-WW (rough)
        if not date_str or len(date_str) < 10:
            return "unknown"
        try:
            y, m, d = date_str.split("-")
            m_num = int(m)
            d_num = int(d)
            # 2-week buckets
            bucket_num = ((m_num - 1) * 4 + (d_num - 1) // 14) // 2 + 1
            return f"{y}-B{bucket_num:02d}"
        except Exception:
            return "unknown"

    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["conv_v4"] is not None and r["pnl"] is not None:
            buckets[week_bucket(r["entry_date"])].append(r)

    print(f"\n{'Bucket':<12} {'n':>5} {'mean P&L':>10} {'win %':>7} "
          f"{'conv Pear':>10} {'conv Spear':>11} {'DC Spear':>10}")
    for bucket in sorted(buckets.keys()):
        rs = buckets[bucket]
        if len(rs) < 100:
            continue
        ys = [r["pnl"] for r in rs]
        convs = [r["conv_v4"] for r in rs]
        dcs = [r["dc"] for r in rs if r["dc"] is not None]
        ys_dc = [r["pnl"] for r in rs if r["dc"] is not None]
        mean_pnl = statistics.mean(ys)
        wins = sum(1 for p in ys if p > 0)
        p = pearson(convs, ys) or 0
        sp = spearman(convs, ys) or 0
        dc_sp = spearman(dcs, ys_dc) if len(dcs) >= 20 else 0
        print(f"{bucket:<12} {len(rs):>5} {mean_pnl:>+9.2f}% {wins/len(rs)*100:>6.1f}% "
              f"{p:>+9.3f} {sp:>+10.3f} {dc_sp or 0:>+9.3f}")

    print()
    print("=" * 78)
    print("END OF DIAGNOSIS")
    print("=" * 78)


if __name__ == "__main__":
    main()
