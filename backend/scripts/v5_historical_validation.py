#!/usr/bin/env python3
"""v5 Historical Validation + Extended Archetype Discovery

Answers four questions on the full closed paper-position dataset:

  1. MONOTONICITY — If we scored every closed position under the proposed
     v5 conviction formula (= Wilson lower-bound × fit × regime × 100),
     does higher conviction mean higher HR200 probability?

  2. HR COVERAGE — What fraction of the 201 historical HR200 trades would
     have fallen in the top decile of v5 conviction? In the top quartile?
     How many would have scored zero (no archetype match)?

  3. MISSED HOME RUNS — For HR200 trades that score zero under v5, what
     features do they share? Is there a coherent missing archetype to add?

  4. EXTENDED ARCHETYPE DISCOVERY — Using relaxed thresholds, can we find
     more than the 6 discovered archetypes? Rank candidates by stability
     (Wilson lower / point estimate) and economic significance (lift × n).

Usage:
  AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
  python3 /tmp/v5_historical_validation.py
"""

from __future__ import annotations

import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Optional

import boto3
from boto3.dynamodb.conditions import Key

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")

OPT_TICKER_RE = re.compile(r"^(?:O:)?([A-Z]+)(\d{6})([CP])(\d+)$")

HR_100 = 100.0
HR_200 = 200.0
HR_500 = 500.0

FVT_FEATURES = [
    "iv_percentile", "iv_rv_ratio", "rs_20d", "atr14_pct",
    "adx_14", "plus_di", "minus_di", "rv20",
    "sector_rs_20d", "bb_width_percentile", "historical_move_magnitude",
]

OUTPUT_PATH = Path("/tmp/v5_analysis_report.md")


# ============================================================================
# Helpers
# ============================================================================

def f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_option(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    m = OPT_TICKER_RE.match(t)
    if not m:
        return None
    return "CALL" if m.group(3) == "C" else "PUT"


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


def fetch_fvt_for_eval(fvt: Any, eval_id: str) -> dict[str, float]:
    try:
        resp = fvt.query(
            KeyConditionExpression=Key("PK").eq(f"EVAL#{eval_id}")
            & Key("SK").begins_with("FEATURE#"),
        )
    except Exception:
        return {}
    out: dict[str, float] = {}
    for item in resp.get("Items", []):
        name = str(item.get("SK", "")).replace("FEATURE#", "")
        if name in FVT_FEATURES:
            v = f(item.get("value"))
            if v is not None:
                out[name] = v
    return out


def wilson_ci(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float, float]:
    """Wilson score interval for a binomial proportion.
    Returns (point_estimate, lower_bound, upper_bound) in [0, 1].
    Handles n=0 by returning (0, 0, 0)."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    # Approximate z-score for common confidence levels.
    z = {0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}.get(confidence, 1.96)
    p_hat = successes / n
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    half = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) / denom
    return (p_hat, max(0.0, center - half), min(1.0, center + half))


def spearman_rho(x: list[float], y: list[float]) -> Optional[float]:
    """Spearman rank correlation coefficient. Pure-python, no scipy."""
    n = len(x)
    if n < 10:
        return None
    # Rank with average-rank tie handling.
    def rank(values: list[float]) -> list[float]:
        indexed = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(values):
            j = i
            while j + 1 < len(values) and values[indexed[j + 1]] == values[indexed[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[indexed[k]] = avg_rank
            i = j + 1
        return ranks

    rx, ry = rank(x), rank(y)
    mean_x, mean_y = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    denom_x = math.sqrt(sum((rx[i] - mean_x) ** 2 for i in range(n)))
    denom_y = math.sqrt(sum((ry[i] - mean_y) ** 2 for i in range(n)))
    if denom_x == 0 or denom_y == 0:
        return None
    return num / (denom_x * denom_y)


# ============================================================================
# v5 ARCHETYPES (the 6 from v4.1.0 — conditions encoded here)
# ============================================================================

def _abs(x):
    return abs(x) if x is not None else None


def _rs_against(r) -> bool:
    """Contrarian RS: CALL on down-stock (rs<0.95) or PUT on up-stock (rs>1.05)."""
    rs = r.get("rs_20d")
    opt = r.get("option_type")
    if rs is None or opt is None:
        return False
    if opt == "CALL":
        return rs < 0.95
    return rs > 1.05


ARCHETYPES = {
    # A — UV Lottery Call — historical 20.2% HR200
    "UV_LOTTERY_CALL": {
        "conditions": [
            lambda r: r.get("scanner") == "UNUSUAL_VOLUME",
            lambda r: r.get("dte") is not None and 14 <= r["dte"] <= 21,
            lambda r: _abs(r.get("delta")) is not None and _abs(r["delta"]) <= 0.25,
            lambda r: r.get("option_type") == "CALL",
        ],
    },
    # B — UV Structural — historical 9.5% HR200
    "UV_STRUCTURAL": {
        "conditions": [
            lambda r: r.get("scanner") == "UNUSUAL_VOLUME",
            lambda r: r.get("dte") is not None and 14 <= r["dte"] <= 21,
            lambda r: r.get("ts") is not None and r["ts"] >= 75,
        ],
    },
    # C — UV Reversal PUT — historical 10.5% HR200
    "UV_REVERSAL_PUT": {
        "conditions": [
            lambda r: r.get("scanner") == "UNUSUAL_VOLUME",
            lambda r: r.get("option_type") == "PUT",
            lambda r: r.get("ts") is not None and r["ts"] >= 75,
            _rs_against,
        ],
    },
    # D — Cheap Compression — historical 7.5% HR200
    "CHEAP_COMPRESSION": {
        "conditions": [
            lambda r: r.get("scanner") == "CHEAP_OPTIONS",
            lambda r: r.get("adx_14") is not None and r["adx_14"] < 20,
            lambda r: r.get("conv_score") is not None and 65 <= r["conv_score"] <= 78,
            lambda r: r.get("atr14_pct") is not None and 4 <= r["atr14_pct"] <= 6,
        ],
    },
    # E — Cheap Vol Reversal — historical 8.0% HR200
    "CHEAP_VOL_REVERSAL": {
        "conditions": [
            lambda r: r.get("scanner") == "CHEAP_OPTIONS",
            lambda r: r.get("atr14_pct") is not None and r["atr14_pct"] >= 6,
            _rs_against,
            lambda r: r.get("iv_rv_ratio") is not None and 1.0 <= r["iv_rv_ratio"] <= 1.3,
        ],
    },
    # F — Cheap Ultra Call — historical 10.8% HR200 (small n=37, caution)
    "CHEAP_ULTRA_CALL": {
        "conditions": [
            lambda r: r.get("scanner") == "CHEAP_OPTIONS",
            lambda r: r.get("dte") is not None and r["dte"] < 14,
            lambda r: r.get("option_type") == "CALL",
            lambda r: r.get("iv_percentile") is not None and r["iv_percentile"] < 30,
        ],
    },
}


def match_archetype(r: dict) -> tuple[Optional[str], float]:
    """Return (archetype_id, fit_score) for the best matching archetype, or (None, 0)."""
    best: tuple[Optional[str], float] = (None, 0.0)
    for arch_id, spec in ARCHETYPES.items():
        conds = spec["conditions"]
        try:
            results = [bool(c(r)) for c in conds]
        except Exception:
            continue
        if all(results):
            # Binary all-conditions match → fit=100. (feather matching omitted for this analysis.)
            fit = 100.0
            if fit > best[1]:
                best = (arch_id, fit)
    return best


# ============================================================================
# Main
# ============================================================================

def main():
    print(f"Loading closed positions from {TABLE_PREFIX}-paper-positions...")
    dyn = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions = dyn.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt = dyn.Table(f"{TABLE_PREFIX}-feature-values")

    items = query_partition(positions, "POS#CLOSED")
    print(f"  raw items: {len(items)}")

    rows = []
    for p in items:
        mfe = f(p.get("max_favorable_excursion"))
        pnl = f(p.get("current_pnl_pct"))
        if mfe is None or pnl is None:
            continue
        opt_type = str(p.get("option_type") or "").upper() or None
        if not opt_type:
            opt_type = parse_option(p.get("option_ticker"))
        dte = f(p.get("dte_at_entry"))
        if dte is None:
            from datetime import datetime
            try:
                exp = datetime.fromisoformat(str(p.get("expiration_date"))[:10])
                ent = datetime.fromisoformat(str(p.get("entry_date"))[:10])
                dte = (exp - ent).days
            except Exception:
                pass
        rows.append({
            "ticker": str(p.get("underlying_ticker") or ""),
            "eval_id": str(p.get("evaluation_id") or ""),
            "entry_date": str(p.get("entry_date") or ""),
            "days_held": f(p.get("days_held")),
            "scanner": str(p.get("scanner_source") or "UNKNOWN"),
            "conv_score": f(p.get("conviction_score")),
            "option_type": opt_type,
            "dc": f(p.get("pillar_directional_conviction")),
            "mp": f(p.get("pillar_move_potential")),
            "ts": f(p.get("pillar_trade_structure")),
            "dte": dte,
            "delta": f(p.get("entry_delta")),
            "iv": f(p.get("entry_iv")),
            "theta_edge": f(p.get("entry_theta_adjusted_edge")),
            "earn_days": f(p.get("entry_days_to_earnings")),
            "moneyness": f(p.get("entry_moneyness_pct")),
            "mfe": mfe, "pnl": pnl,
        })

    total = len(rows)
    print(f"  usable: {total}")

    # FVT enrichment
    eval_ids = list({r["eval_id"] for r in rows if r["eval_id"]})
    print(f"  fetching FVT for {len(eval_ids)} eval_ids (parallel, 32 workers)...")
    enrich: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=32) as ex:
        futs = {ex.submit(fetch_fvt_for_eval, fvt, eid): eid for eid in eval_ids}
        done = 0
        for fut in as_completed(futs):
            enrich[futs[fut]] = fut.result()
            done += 1
            if done % 2000 == 0:
                print(f"    {done}/{len(eval_ids)}")
    print(f"  FVT complete")

    for r in rows:
        feats = enrich.get(r["eval_id"], {})
        for fname in FVT_FEATURES:
            r[fname] = feats.get(fname)

    # ========================================================================
    # STEP 1: Compute historical HR200 rates per archetype from this dataset
    # ========================================================================
    print()
    print("=" * 80)
    print("STEP 1 — Historical archetype rates (computed IN-SAMPLE on this dataset)")
    print("=" * 80)

    by_archetype: dict[Optional[str], list[dict]] = defaultdict(list)
    for r in rows:
        arch, fit = match_archetype(r)
        r["archetype_v5"] = arch
        r["fit_v5"] = fit
        by_archetype[arch].append(r)

    archetype_rates: dict[str, tuple[float, float, float, int]] = {}
    # (point, wilson_lower, wilson_upper, n)
    print(f"\n{'Archetype':<22} {'n':>6} {'HR200':>5} {'point':>7} {'w_lower':>8} {'w_upper':>8} {'meanPnL':>9} {'winRate':>7}")
    print("-" * 90)
    for arch_id in list(ARCHETYPES.keys()) + [None]:
        cohort = by_archetype.get(arch_id, [])
        n = len(cohort)
        hr200 = sum(1 for r in cohort if r["mfe"] is not None and r["mfe"] >= HR_200)
        pnl = [r["pnl"] for r in cohort if r["pnl"] is not None]
        wins = sum(1 for p in pnl if p > 0)
        point, lower, upper = wilson_ci(hr200, n)
        archetype_rates[arch_id or "NO_MATCH"] = (point, lower, upper, n)
        mean_pnl = statistics.mean(pnl) if pnl else 0.0
        win_rate = wins / len(pnl) * 100 if pnl else 0.0
        label = arch_id or "NO_MATCH"
        print(f"{label:<22} {n:>6} {hr200:>5} {point * 100:>6.2f}% "
              f"{lower * 100:>7.2f}% {upper * 100:>7.2f}% "
              f"{mean_pnl:>+8.2f}% {win_rate:>6.1f}%")

    # Baseline
    total_hr200 = sum(1 for r in rows if r["mfe"] is not None and r["mfe"] >= HR_200)
    total_hr100 = sum(1 for r in rows if r["mfe"] is not None and r["mfe"] >= HR_100)
    total_hr500 = sum(1 for r in rows if r["mfe"] is not None and r["mfe"] >= HR_500)
    baseline_rate = total_hr200 / total * 100
    print(f"\nBASELINE: n={total}, HR100={total_hr100} ({total_hr100/total*100:.2f}%), "
          f"HR200={total_hr200} ({baseline_rate:.2f}%), HR500={total_hr500} ({total_hr500/total*100:.3f}%)")

    # ========================================================================
    # STEP 2: Score every position under v5 conviction formula
    # ========================================================================
    print()
    print("=" * 80)
    print("STEP 2 — Assign v5 conviction to every position")
    print("         conviction_v5 = 100 * wilson_lower * fit * regime (regime=1.0 for analysis)")
    print("=" * 80)

    for r in rows:
        arch = r["archetype_v5"]
        fit = r["fit_v5"]
        if arch is None:
            r["conviction_v5_lower"] = 0.0
            r["conviction_v5_point"] = 0.0
        else:
            point, lower, _, _ = archetype_rates[arch]
            r["conviction_v5_lower"] = 100.0 * lower * (fit / 100.0)
            r["conviction_v5_point"] = 100.0 * point * (fit / 100.0)

    # Distribution summary
    convs = [r["conviction_v5_lower"] for r in rows]
    convs_pt = [r["conviction_v5_point"] for r in rows]
    print(f"\nv5 conviction (Wilson lower) distribution:")
    print(f"  min={min(convs):.2f}, max={max(convs):.2f}, mean={statistics.mean(convs):.2f}, median={statistics.median(convs):.2f}")
    print(f"  non-zero: {sum(1 for c in convs if c > 0)}/{len(convs)} ({sum(1 for c in convs if c > 0)/len(convs)*100:.1f}%)")
    print(f"\nv5 conviction (point estimate) distribution:")
    print(f"  min={min(convs_pt):.2f}, max={max(convs_pt):.2f}, mean={statistics.mean(convs_pt):.2f}, median={statistics.median(convs_pt):.2f}")

    # ========================================================================
    # STEP 3: Monotonicity — Spearman rho, decile HR rates
    # ========================================================================
    print()
    print("=" * 80)
    print("STEP 3 — Monotonicity: Spearman ρ and decile HR200 rates")
    print("=" * 80)

    valid_rows = [r for r in rows if r["mfe"] is not None and r["pnl"] is not None]
    # v5 LOWER bound
    x = [r["conviction_v5_lower"] for r in valid_rows]
    y_mfe = [r["mfe"] for r in valid_rows]
    y_pnl = [r["pnl"] for r in valid_rows]
    y_hr200 = [1.0 if r["mfe"] >= HR_200 else 0.0 for r in valid_rows]
    rho_mfe = spearman_rho(x, y_mfe)
    rho_pnl = spearman_rho(x, y_pnl)
    rho_hr200 = spearman_rho(x, y_hr200)
    print(f"\nv5 conviction (Wilson lower) vs outcome:")
    print(f"  Spearman ρ vs MFE%:    {rho_mfe:+.4f}" if rho_mfe else "  ρ(MFE)=n/a")
    print(f"  Spearman ρ vs P&L%:    {rho_pnl:+.4f}" if rho_pnl else "  ρ(P&L)=n/a")
    print(f"  Spearman ρ vs HR200:   {rho_hr200:+.4f}" if rho_hr200 else "  ρ(HR200)=n/a")

    # Compare to existing conviction score
    x_old = [r.get("conv_score") or 0 for r in valid_rows]
    rho_old_mfe = spearman_rho(x_old, y_mfe)
    rho_old_hr = spearman_rho(x_old, y_hr200)
    print(f"\nv4.1.0 conviction_score vs outcome (reference):")
    print(f"  Spearman ρ vs MFE%:    {rho_old_mfe:+.4f}" if rho_old_mfe else "  n/a")
    print(f"  Spearman ρ vs HR200:   {rho_old_hr:+.4f}" if rho_old_hr else "  n/a")

    # Deciles (using point estimate for clearer bands — Wilson lower clusters values)
    print(f"\n--- Decile breakdown by v5 conviction (POINT estimate — finer resolution) ---")
    sorted_rows = sorted(valid_rows, key=lambda r: r["conviction_v5_point"])
    per_dec = len(sorted_rows) // 10
    print(f"{'Decile':<7} {'range':<20} {'n':>5} {'HR100':>5} {'rate':>6} {'HR200':>5} {'rate':>6} {'mean_MFE':>9} {'mean_PnL':>9}")
    print("-" * 85)
    for dec in range(10):
        start = dec * per_dec
        end = (dec + 1) * per_dec if dec < 9 else len(sorted_rows)
        cohort = sorted_rows[start:end]
        if not cohort:
            continue
        lo = cohort[0]["conviction_v5_point"]
        hi = cohort[-1]["conviction_v5_point"]
        n = len(cohort)
        hr100 = sum(1 for r in cohort if r["mfe"] >= HR_100)
        hr200 = sum(1 for r in cohort if r["mfe"] >= HR_200)
        mean_mfe = statistics.mean(r["mfe"] for r in cohort)
        mean_pnl = statistics.mean(r["pnl"] for r in cohort)
        rng = f"[{lo:.2f}, {hi:.2f}]"
        print(f"D{dec + 1:<6} {rng:<20} {n:>5} {hr100:>5} {hr100/n*100:>5.2f}% {hr200:>5} {hr200/n*100:>5.2f}% "
              f"{mean_mfe:>+8.2f}% {mean_pnl:>+8.2f}%")

    # ========================================================================
    # STEP 4: Home run coverage
    # ========================================================================
    print()
    print("=" * 80)
    print("STEP 4 — Home run coverage under v5")
    print("=" * 80)

    hr200_rows = [r for r in valid_rows if r["mfe"] >= HR_200]
    hr200_n = len(hr200_rows)
    print(f"\nTotal HR200 trades: {hr200_n}")

    # Matched vs unmatched
    matched = [r for r in hr200_rows if r["archetype_v5"] is not None]
    unmatched = [r for r in hr200_rows if r["archetype_v5"] is None]
    print(f"  Matched an archetype:   {len(matched)} ({len(matched)/hr200_n*100:.1f}%)")
    print(f"  NO archetype match:     {len(unmatched)} ({len(unmatched)/hr200_n*100:.1f}%)")
    print(f"\nHR200 distribution across archetypes:")
    hr_by_arch = Counter(r["archetype_v5"] or "NO_MATCH" for r in hr200_rows)
    for arch, cnt in sorted(hr_by_arch.items(), key=lambda x: -x[1]):
        pct = cnt / hr200_n * 100
        arch_n = len(by_archetype.get(arch if arch != "NO_MATCH" else None, []))
        capture_rate = cnt / arch_n * 100 if arch_n else 0
        print(f"  {arch:<22}: {cnt:>3} HRs ({pct:>5.1f}% of HRs), "
              f"n={arch_n} ({capture_rate:.1f}% of archetype)")

    # Where do HRs fall in v5 conviction distribution?
    dec_map = {r["conviction_v5_point"]: i for i, r in enumerate(sorted_rows)}
    hr_ranks = [dec_map.get(r["conviction_v5_point"], -1) for r in hr200_rows]
    in_top10 = sum(1 for rank in hr_ranks if rank >= len(sorted_rows) * 0.90)
    in_top20 = sum(1 for rank in hr_ranks if rank >= len(sorted_rows) * 0.80)
    in_top25 = sum(1 for rank in hr_ranks if rank >= len(sorted_rows) * 0.75)
    in_top50 = sum(1 for rank in hr_ranks if rank >= len(sorted_rows) * 0.50)
    at_zero = sum(1 for r in hr200_rows if r["conviction_v5_point"] == 0)
    print(f"\nHR200 distribution by v5 conviction rank:")
    print(f"  In top 10%:  {in_top10}/{hr200_n} ({in_top10/hr200_n*100:.1f}%)")
    print(f"  In top 20%:  {in_top20}/{hr200_n} ({in_top20/hr200_n*100:.1f}%)")
    print(f"  In top 25%:  {in_top25}/{hr200_n} ({in_top25/hr200_n*100:.1f}%)")
    print(f"  In top 50%:  {in_top50}/{hr200_n} ({in_top50/hr200_n*100:.1f}%)")
    print(f"  At v5=0 (no match, INVISIBLE): {at_zero}/{hr200_n} ({at_zero/hr200_n*100:.1f}%)")

    # ========================================================================
    # STEP 5: Missed home runs — what do they look like?
    # ========================================================================
    print()
    print("=" * 80)
    print("STEP 5 — Anatomy of MISSED home runs (v5=0, no archetype match)")
    print("=" * 80)

    missed = unmatched
    if missed:
        # Scanner breakdown
        scan_c = Counter(r["scanner"] for r in missed)
        print(f"\nMissed HR200 by scanner:")
        for s, cnt in sorted(scan_c.items(), key=lambda x: -x[1]):
            pct = cnt / len(missed) * 100
            all_in_scanner = sum(1 for r in valid_rows if r["scanner"] == s)
            print(f"  {s:<22}: {cnt:>3} ({pct:>5.1f}%), scanner pop={all_in_scanner}")

        # DTE breakdown
        dte_c = Counter()
        for r in missed:
            d = r.get("dte")
            if d is None:
                dte_c["unknown"] += 1
            elif d < 14:
                dte_c["<14"] += 1
            elif d < 21:
                dte_c["14-21"] += 1
            elif d < 45:
                dte_c["21-45"] += 1
            else:
                dte_c["≥45"] += 1
        print(f"\nMissed HR200 by DTE:")
        for k in ["<14", "14-21", "21-45", "≥45", "unknown"]:
            if k in dte_c:
                print(f"  {k:<10}: {dte_c[k]} ({dte_c[k]/len(missed)*100:.1f}%)")

        # Option type + side
        ot_c = Counter(r["option_type"] or "unknown" for r in missed)
        print(f"\nMissed HR200 by option type:")
        for k, v in sorted(ot_c.items(), key=lambda x: -x[1]):
            print(f"  {k:<10}: {v} ({v/len(missed)*100:.1f}%)")

        # Delta
        delta_c = Counter()
        for r in missed:
            d = r.get("delta")
            if d is None:
                delta_c["unknown"] += 1
                continue
            ad = abs(d)
            if ad < 0.25:
                delta_c["<0.25"] += 1
            elif ad < 0.40:
                delta_c["0.25-0.40"] += 1
            elif ad < 0.55:
                delta_c["0.40-0.55"] += 1
            else:
                delta_c["≥0.55"] += 1
        print(f"\nMissed HR200 by |delta|:")
        for k in ["<0.25", "0.25-0.40", "0.40-0.55", "≥0.55", "unknown"]:
            if k in delta_c:
                print(f"  {k:<12}: {delta_c[k]} ({delta_c[k]/len(missed)*100:.1f}%)")

        # Scanner × side + DTE combo (top rows)
        combo_c = Counter()
        for r in missed:
            scan = r["scanner"]
            opt = r["option_type"] or "?"
            d = r.get("dte")
            if d is None:
                dte_b = "?"
            elif d < 14:
                dte_b = "<14"
            elif d < 21:
                dte_b = "14-21"
            elif d < 45:
                dte_b = "21-45"
            else:
                dte_b = "≥45"
            combo_c[(scan, opt, dte_b)] += 1
        print(f"\nTop (scanner × option_type × DTE) combos among missed:")
        print(f"  {'combo':<55} {'n':>5} {'% of missed':>12}")
        for (scan, opt, dte_b), cnt in sorted(combo_c.items(), key=lambda x: -x[1])[:15]:
            label = f"{scan} × {opt} × DTE {dte_b}"
            print(f"  {label:<55} {cnt:>5} {cnt/len(missed)*100:>11.1f}%")

    # ========================================================================
    # STEP 6: Extended archetype discovery — search wider
    # ========================================================================
    print()
    print("=" * 80)
    print("STEP 6 — Extended archetype discovery (relaxed thresholds, rank by stability)")
    print("=" * 80)

    MIN_SAMPLE = 20
    MIN_HR = 3
    MIN_LIFT = 2.0

    def _bucket_dte(r):
        d = r.get("dte")
        if d is None: return None
        if d < 14: return "ULTRA(<14)"
        if d < 21: return "SHORT(14-21)"
        if d < 45: return "MID(21-45)"
        return "LONG(≥45)"

    def _bucket_delta(r):
        d = r.get("delta")
        if d is None: return None
        ad = abs(d)
        if ad < 0.25: return "DEEP_OTM(<0.25)"
        if ad < 0.40: return "OTM(0.25-0.40)"
        if ad < 0.55: return "NEAR(0.40-0.55)"
        return "ITM(≥0.55)"

    def _bucket_ivp(r):
        v = r.get("iv_percentile")
        if v is None: return None
        if v < 30: return "IVP_LO(<30)"
        if v < 70: return "IVP_MID"
        return "IVP_HI(≥70)"

    def _bucket_ivrv(r):
        v = r.get("iv_rv_ratio")
        if v is None: return None
        if v < 1.0: return "IVRV_CHEAP(<1.0)"
        if v < 1.3: return "IVRV_FAIR"
        return "IVRV_RICH(≥1.3)"

    def _bucket_ts(r):
        v = r.get("ts")
        if v is None: return None
        if v < 60: return "TS_LO(<60)"
        if v < 75: return "TS_MID"
        return "TS_HI(≥75)"

    def _bucket_dc(r):
        v = r.get("dc")
        if v is None: return None
        if v < 40: return "DC_LO"
        if v < 60: return "DC_MID"
        if v < 75: return "DC_HIGH"
        return "DC_ELITE"

    def _bucket_mp(r):
        v = r.get("mp")
        if v is None: return None
        if v < 40: return "MP_LO"
        if v < 60: return "MP_MID"
        if v < 75: return "MP_HIGH"
        return "MP_ELITE"

    def _bucket_adx(r):
        v = r.get("adx_14")
        if v is None: return None
        if v < 20: return "ADX_LO(<20)"
        if v < 30: return "ADX_MID"
        return "ADX_HI(≥30)"

    def _bucket_atr(r):
        v = r.get("atr14_pct")
        if v is None: return None
        if v < 2: return "ATR_LO"
        if v < 4: return "ATR_MID"
        if v < 6: return "ATR_HI"
        return "ATR_VOL"

    def _bucket_rs(r):
        return "RS_AGAINST" if _rs_against(r) else "RS_WITH"

    def _bucket_moneyness(r):
        v = r.get("moneyness")
        if v is None: return None
        av = abs(v)
        if av < 2: return "ATM"
        if av < 5: return "NEAR"
        return "FAR"

    def _bucket_earn(r):
        v = r.get("earn_days")
        if v is None: return "EARN_NONE"
        if v <= 3: return "EARN_IMMINENT(≤3)"
        if v < 14: return "EARN_NEAR(3-14)"
        if v < 30: return "EARN_MID(14-30)"
        return "EARN_FAR(≥30)"

    BUCKETS = {
        "dte": _bucket_dte, "delta": _bucket_delta, "ivp": _bucket_ivp,
        "ivrv": _bucket_ivrv, "ts": _bucket_ts, "dc": _bucket_dc, "mp": _bucket_mp,
        "adx": _bucket_adx, "atr": _bucket_atr, "rs": _bucket_rs,
        "moneyness": _bucket_moneyness, "earn": _bucket_earn,
        "option_type": lambda r: r.get("option_type"),
    }

    def compute_cohort(cohort, baseline_rate):
        n = len(cohort)
        hr200 = sum(1 for r in cohort if r["mfe"] >= HR_200)
        hr100 = sum(1 for r in cohort if r["mfe"] >= HR_100)
        pnl = [r["pnl"] for r in cohort]
        wins = sum(1 for p in pnl if p > 0)
        point, lower, upper = wilson_ci(hr200, n)
        lift = (point * 100) / baseline_rate if baseline_rate > 0 else 0
        lift_lower = (lower * 100) / baseline_rate if baseline_rate > 0 else 0
        return {
            "n": n, "hr200": hr200, "hr100": hr100,
            "point": point * 100, "lower": lower * 100, "upper": upper * 100,
            "lift": lift, "lift_lower": lift_lower,
            "mean_pnl": statistics.mean(pnl) if pnl else 0,
            "win_rate": wins / len(pnl) * 100 if pnl else 0,
        }

    # Search triples and quads within each scanner
    by_scanner: dict[str, list] = defaultdict(list)
    for r in valid_rows:
        by_scanner[r["scanner"]].append(r)

    candidates = []
    for scanner, cohort in by_scanner.items():
        if len(cohort) < 200:
            continue
        scanner_hr200 = sum(1 for r in cohort if r["mfe"] >= HR_200)
        scanner_base = scanner_hr200 / len(cohort) * 100
        if scanner_base < 0.1:
            continue

        # Pre-compute all bucket values for this cohort
        features = list(BUCKETS.keys())
        bucketed = [{f: BUCKETS[f](r) for f in features} for r in cohort]

        # Pairs
        for i, f1 in enumerate(features):
            for f2 in features[i + 1:]:
                combo_buckets = defaultdict(list)
                for idx, r in enumerate(cohort):
                    b1 = bucketed[idx][f1]
                    b2 = bucketed[idx][f2]
                    if b1 is None or b2 is None:
                        continue
                    combo_buckets[(b1, b2)].append(r)
                for (b1, b2), sub in combo_buckets.items():
                    if len(sub) < MIN_SAMPLE:
                        continue
                    c = compute_cohort(sub, scanner_base)
                    if c["lift_lower"] >= MIN_LIFT and c["hr200"] >= MIN_HR:
                        candidates.append({
                            "scanner": scanner, "conditions": [f"{f1}={b1}", f"{f2}={b2}"],
                            "depth": 2, **c,
                        })

        # Triples (limit depth by pre-filtering — only combine with pairs that had some HRs)
        for i, f1 in enumerate(features):
            for j, f2 in enumerate(features[i + 1:], i + 1):
                for f3 in features[j + 1:]:
                    combo_buckets = defaultdict(list)
                    for idx, r in enumerate(cohort):
                        b1 = bucketed[idx][f1]
                        b2 = bucketed[idx][f2]
                        b3 = bucketed[idx][f3]
                        if b1 is None or b2 is None or b3 is None:
                            continue
                        combo_buckets[(b1, b2, b3)].append(r)
                    for (b1, b2, b3), sub in combo_buckets.items():
                        if len(sub) < MIN_SAMPLE:
                            continue
                        c = compute_cohort(sub, scanner_base)
                        if c["lift_lower"] >= MIN_LIFT + 0.5 and c["hr200"] >= MIN_HR:
                            candidates.append({
                                "scanner": scanner,
                                "conditions": [f"{f1}={b1}", f"{f2}={b2}", f"{f3}={b3}"],
                                "depth": 3, **c,
                            })

    # Deduplicate (same conditions in different order)
    seen = set()
    dedup = []
    for cand in candidates:
        key = (cand["scanner"], tuple(sorted(cand["conditions"])))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(cand)

    # Rank by stability score: lift_lower × log(n) × sqrt(mean_pnl + 100)
    def score(c):
        return c["lift_lower"] * math.log(max(2, c["n"])) * math.sqrt(max(1, c["mean_pnl"] + 100))

    dedup.sort(key=lambda c: -score(c))

    print(f"\nTotal candidate archetypes (lift_lower ≥ 2x scanner base, n ≥ 20, hr200 ≥ 3): {len(dedup)}")
    print(f"\nTop 30 candidates ranked by stability score:\n")
    print(f"{'#':<3} {'scanner':<22} {'conditions':<55} {'n':>5} {'HR200':>5} {'point':>6} "
          f"{'lower':>6} {'lift↓':>5} {'meanPnL':>8} {'win%':>5}")
    print("-" * 140)
    for i, c in enumerate(dedup[:30], 1):
        conds = " × ".join(c["conditions"])
        print(f"{i:<3} {c['scanner']:<22} {conds[:54]:<55} {c['n']:>5} {c['hr200']:>5} "
              f"{c['point']:>5.2f}% {c['lower']:>5.2f}% {c['lift_lower']:>4.2f}x "
              f"{c['mean_pnl']:>+7.2f}% {c['win_rate']:>4.1f}%")

    # ========================================================================
    # STEP 7: What if we ADD the top-N new archetypes? Coverage + monotonicity
    # ========================================================================
    print()
    print("=" * 80)
    print("STEP 7 — Simulation: add top-N new archetypes, recompute HR coverage")
    print("=" * 80)

    for top_n in [5, 10, 20]:
        extra = dedup[:top_n]
        # Recompute archetype_v5 for every row, falling back to extra matchers if no 6-archetype match
        def matches_extra(r, cand):
            if r["scanner"] != cand["scanner"]:
                return False
            for cond in cand["conditions"]:
                feat, bucket = cond.split("=", 1)
                actual = BUCKETS[feat](r) if feat in BUCKETS else None
                if actual != bucket:
                    return False
            return True

        for r in rows:
            if r["archetype_v5"] is not None:
                r["arch_extra"] = r["archetype_v5"]
                continue
            r["arch_extra"] = None
            for idx, cand in enumerate(extra):
                if matches_extra(r, cand):
                    r["arch_extra"] = f"EXTRA_{idx + 1}"
                    break

        # Count HR200 coverage
        hr200_matched = sum(1 for r in valid_rows if r["mfe"] >= HR_200 and r["arch_extra"] is not None)
        total_hr200 = sum(1 for r in valid_rows if r["mfe"] >= HR_200)
        print(f"  Top-{top_n:>2} extras: HR200 coverage = {hr200_matched}/{total_hr200} "
              f"({hr200_matched / total_hr200 * 100:.1f}%)")

    # ========================================================================
    # SAVE MARKDOWN REPORT
    # ========================================================================
    md = []
    md.append("# v5 Historical Validation & Archetype Discovery Report")
    md.append(f"\n**Dataset:** {total} closed paper positions")
    md.append(f"**Baseline HR200 rate:** {baseline_rate:.2f}% ({total_hr200} trades)")
    md.append(f"**Baseline HR100 rate:** {total_hr100/total*100:.2f}%")
    md.append(f"**Baseline HR500 rate:** {total_hr500/total*100:.3f}%\n")

    md.append("## Headline results")
    md.append(f"- **Monotonicity (Spearman ρ v5 Wilson lower vs MFE):** {rho_mfe:+.4f}" if rho_mfe else "- ρ(MFE): n/a")
    md.append(f"- **Monotonicity (Spearman ρ v5 Wilson lower vs HR200 binary):** {rho_hr200:+.4f}" if rho_hr200 else "- ρ(HR200): n/a")
    md.append(f"- **Reference: v4.1.0 conviction vs MFE:** {rho_old_mfe:+.4f}" if rho_old_mfe else "- n/a")
    md.append(f"- **HR200 captured by 6 existing archetypes:** {len(matched)}/{hr200_n} ({len(matched)/hr200_n*100:.1f}%)")
    md.append(f"- **HR200 with v5=0 (invisible to system):** {at_zero}/{hr200_n} ({at_zero/hr200_n*100:.1f}%)")
    md.append(f"- **HR200 in top-10% v5 conviction decile:** {in_top10}/{hr200_n} ({in_top10/hr200_n*100:.1f}%)\n")

    md.append("## v5 Archetype rates (in-sample on this dataset)")
    md.append("| Archetype | n | HR200 | Point | Wilson lower | Wilson upper | mean P&L | win rate |")
    md.append("|---|---|---|---|---|---|---|---|")
    for arch_id in list(ARCHETYPES.keys()) + [None]:
        cohort = by_archetype.get(arch_id, [])
        n = len(cohort)
        hr200 = sum(1 for r in cohort if r["mfe"] is not None and r["mfe"] >= HR_200)
        pnl = [r["pnl"] for r in cohort if r["pnl"] is not None]
        wins = sum(1 for p in pnl if p > 0)
        point, lower, upper = wilson_ci(hr200, n)
        mean_pnl = statistics.mean(pnl) if pnl else 0.0
        win_rate = wins / len(pnl) * 100 if pnl else 0.0
        label = arch_id or "NO_MATCH"
        md.append(f"| {label} | {n} | {hr200} | {point*100:.2f}% | {lower*100:.2f}% | {upper*100:.2f}% | "
                  f"{mean_pnl:+.2f}% | {win_rate:.1f}% |")

    md.append("\n## Candidate NEW archetypes (top 30 by stability)")
    md.append("Columns: Wilson lower bound of HR200 rate, lift vs scanner baseline, mean P&L.")
    md.append("| # | Scanner | Conditions | n | HR200 | Point | Lower | Lift (lower) | Mean P&L | Win% |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(dedup[:30], 1):
        conds = " × ".join(c["conditions"])
        md.append(f"| {i} | {c['scanner']} | {conds} | {c['n']} | {c['hr200']} | "
                  f"{c['point']:.2f}% | {c['lower']:.2f}% | {c['lift_lower']:.2f}x | "
                  f"{c['mean_pnl']:+.2f}% | {c['win_rate']:.1f}% |")

    md.append(f"\n## Decile breakdown (v5 conviction POINT estimate)")
    md.append("| Decile | Range | n | HR100 | HR100 rate | HR200 | HR200 rate | Mean MFE | Mean P&L |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for dec in range(10):
        start = dec * per_dec
        end = (dec + 1) * per_dec if dec < 9 else len(sorted_rows)
        cohort = sorted_rows[start:end]
        if not cohort:
            continue
        lo = cohort[0]["conviction_v5_point"]
        hi = cohort[-1]["conviction_v5_point"]
        n = len(cohort)
        hr100 = sum(1 for r in cohort if r["mfe"] >= HR_100)
        hr200 = sum(1 for r in cohort if r["mfe"] >= HR_200)
        mean_mfe = statistics.mean(r["mfe"] for r in cohort)
        mean_pnl = statistics.mean(r["pnl"] for r in cohort)
        md.append(f"| D{dec + 1} | [{lo:.2f}, {hi:.2f}] | {n} | {hr100} | {hr100/n*100:.2f}% | "
                  f"{hr200} | {hr200/n*100:.2f}% | {mean_mfe:+.2f}% | {mean_pnl:+.2f}% |")

    OUTPUT_PATH.write_text("\n".join(md))
    print(f"\n\nMarkdown report saved to: {OUTPUT_PATH}")
    print(f"Raw candidate archetypes: {len(dedup)}")

    # Also dump the candidate archetypes as JSON for reuse
    import json
    with open("/tmp/v5_candidate_archetypes.json", "w") as fp:
        json.dump(dedup, fp, indent=2, default=str)
    print(f"Candidate archetypes JSON: /tmp/v5_candidate_archetypes.json")


if __name__ == "__main__":
    main()
