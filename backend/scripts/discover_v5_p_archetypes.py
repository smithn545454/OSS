#!/usr/bin/env python3
"""Discover profitability-targeted archetypes (v5 P conviction candidates).

Mirrors v5_historical_validation.py's mining logic but optimizes for
*profit* (Wilson lower of win rate × normalized mean P&L) instead of
*home runs* (Wilson lower of HR200 rate). The goal is to surface
"grinder" patterns — high win rate + positive mean P&L but rare HR200 —
that the HR archetypes systematically miss.

Filters:
  - n >= 30 (statistical power)
  - mean_pnl_pct > +5% (must be meaningfully profitable)
  - Wilson lower(win_rate) >= scanner_baseline_win_lower * 1.3 (1.3x lift)

The script also reports: how many of the candidates would be NEW
(no overlap with the 12 HR archetypes), and how many of the closed
profitable trades they collectively cover.

Usage:
  AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
  python3 /tmp/discover_p_archetypes.py
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import boto3
from boto3.dynamodb.conditions import Key

# Make the backend app importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.calibration.archetype_rates import normalize_pnl_pct
from app.calibration.wilson import wilson_ci

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")
OPT_TICKER_RE = re.compile(r"^(?:O:)?([A-Z]+)(\d{6})([CP])(\d+)$")

FVT_FEATURES = [
    "iv_percentile", "iv_rv_ratio", "rs_20d", "atr14_pct",
    "adx_14", "plus_di", "minus_di",
]

MIN_N = 30
MIN_HR_OR_PROFIT = 10  # Need 10+ profitable trades for stable estimate
MIN_WIN_LIFT_LOWER = 1.3
MIN_MEAN_PNL = 5.0


# ============================================================================
# Helpers
# ============================================================================

def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_option_type(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    m = OPT_TICKER_RE.match(t)
    if not m:
        return None
    return "CALL" if m.group(3) == "C" else "PUT"


def _query_partition(table: Any, pk: str) -> list[dict]:
    items: list[dict] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(pk)}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def _fetch_fvt(fvt: Any, eval_id: str) -> dict[str, float]:
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
            v = _f(item.get("value"))
            if v is not None:
                out[name] = v
    return out


# ============================================================================
# Feature buckets (smaller set than v5_historical_validation.py — we want
# coarser buckets for grinder patterns to surface)
# ============================================================================

def _bucket(v, breaks, labels):
    """Assign a value to one of len(labels) buckets from ascending `breaks`.

    Returns None if v is None. Otherwise returns labels[i] where
    breaks[i-1] <= v < breaks[i] (with implicit -inf and +inf endpoints).
    """
    if v is None:
        return None
    for i, br in enumerate(breaks):
        if v < br:
            return labels[i]
    return labels[-1]


def b_dte(r):
    return _bucket(
        r.get("dte"),
        [14, 21, 45],
        ["ULTRA(<14)", "SHORT(14-21)", "MID(21-45)", "LONG(>=45)"],
    )


def b_delta(r):
    d = r.get("delta")
    if d is None:
        return None
    return _bucket(
        abs(d),
        [0.25, 0.40, 0.55],
        ["DEEP_OTM(<0.25)", "OTM(0.25-0.40)", "NEAR(0.40-0.55)", "ITM(>=0.55)"],
    )


def b_ivp(r):
    return _bucket(r.get("iv_percentile"), [30, 70], ["IVP_LO", "IVP_MID", "IVP_HI"])


def b_ivrv(r):
    return _bucket(
        r.get("iv_rv_ratio"), [1.0, 1.3],
        ["IVRV_CHEAP", "IVRV_FAIR", "IVRV_RICH"],
    )


def b_dc(r):
    return _bucket(
        r.get("dc_score"), [40, 60, 75],
        ["DC_LO", "DC_MID", "DC_HIGH", "DC_ELITE"],
    )


def b_mp(r):
    return _bucket(
        r.get("mp_score"), [40, 60, 75],
        ["MP_LO", "MP_MID", "MP_HIGH", "MP_ELITE"],
    )


def b_ts(r):
    return _bucket(r.get("ts_score"), [60, 75], ["TS_LO", "TS_MID", "TS_HI"])


def b_adx(r):
    return _bucket(r.get("adx_14"), [20, 30], ["ADX_LO", "ADX_MID", "ADX_HI"])


def b_atr(r):
    return _bucket(
        r.get("atr14_pct"), [2, 4, 6],
        ["ATR_LO", "ATR_MID", "ATR_HI", "ATR_VOL"],
    )


def b_option_type(r):
    return r.get("option_type")

def b_rs_dir(r):
    rs = r.get("rs_20d")
    opt = r.get("option_type")
    if rs is None or opt is None:
        return None
    if opt == "CALL":
        return "RS_AGAINST" if rs < 0.95 else "RS_WITH"
    return "RS_AGAINST" if rs > 1.05 else "RS_WITH"

BUCKETS: dict[str, Callable[[dict], Optional[str]]] = {
    "dte": b_dte, "delta": b_delta, "ivp": b_ivp, "ivrv": b_ivrv,
    "dc": b_dc, "mp": b_mp, "ts": b_ts, "adx": b_adx, "atr": b_atr,
    "option_type": b_option_type, "rs": b_rs_dir,
}


# ============================================================================
# Cohort scoring
# ============================================================================

def cohort_stats(cohort: list[dict], scanner_baseline: dict) -> dict:
    """Compute n, win-rate Wilson bounds, mean P&L, P-conviction estimate."""
    n = len(cohort)
    if n == 0:
        return {"n": 0, "skip": True}

    wins = sum(1 for r in cohort if (r.get("current_pnl_pct") or 0) > 0)
    pnls = [r["current_pnl_pct"] for r in cohort if r.get("current_pnl_pct") is not None]
    mean_pnl = statistics.mean(pnls) if pnls else 0.0
    median_pnl = statistics.median(pnls) if pnls else 0.0
    hr200 = sum(1 for r in cohort if (r.get("max_favorable_excursion") or 0) >= 200)

    win_point, win_lower, win_upper = wilson_ci(wins, n)
    hr200_point, hr200_lower, _ = wilson_ci(hr200, n)
    base_win_lower = scanner_baseline.get("win_lower", 0.0)
    win_lift = win_lower / base_win_lower if base_win_lower > 0 else 0.0

    pnl_norm = normalize_pnl_pct(mean_pnl)
    p_conviction_est = 100 * win_lower * pnl_norm  # × fit=1 × regime=1

    return {
        "n": n,
        "wins": wins,
        "win_point": win_point,
        "win_lower": win_lower,
        "win_upper": win_upper,
        "win_lift": win_lift,
        "mean_pnl": mean_pnl,
        "median_pnl": median_pnl,
        "hr200": hr200,
        "hr200_lower": hr200_lower,
        "p_conviction_est": p_conviction_est,
        "skip": False,
    }


def passes_filters(stats: dict, scanner_baseline: dict) -> bool:
    if stats.get("skip"):
        return False
    if stats["n"] < MIN_N:
        return False
    if stats["wins"] < MIN_HR_OR_PROFIT:
        return False
    if stats["mean_pnl"] < MIN_MEAN_PNL:
        return False
    if stats["win_lift"] < MIN_WIN_LIFT_LOWER:
        return False
    return True


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    print(f"Loading closed positions from {TABLE_PREFIX}-paper-positions...")
    dyn = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions = dyn.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt = dyn.Table(f"{TABLE_PREFIX}-feature-values")

    items = _query_partition(positions, "POS#CLOSED")
    print(f"  raw items: {len(items)}")

    rows = []
    for p in items:
        mfe = _f(p.get("max_favorable_excursion"))
        pnl = _f(p.get("current_pnl_pct"))
        if mfe is None or pnl is None:
            continue
        opt_type = str(p.get("option_type") or "").upper() or None
        if not opt_type:
            opt_type = _parse_option_type(p.get("option_ticker"))
        dte = _f(p.get("dte_at_entry"))
        if dte is None:
            try:
                exp = datetime.fromisoformat(str(p.get("expiration_date"))[:10])
                ent = datetime.fromisoformat(str(p.get("entry_date"))[:10])
                dte = (exp - ent).days
            except Exception:
                pass
        rows.append({
            "evaluation_id": str(p.get("evaluation_id") or ""),
            "scanner_source": str(p.get("scanner_source") or "UNKNOWN"),
            "option_type": opt_type,
            "dte": dte,
            "delta": _f(p.get("entry_delta")),
            "iv_percentile": _f(p.get("entry_iv_percentile")),
            "iv_rv_ratio": _f(p.get("entry_iv_rv_ratio")),
            "dc_score": _f(p.get("pillar_directional_conviction")),
            "mp_score": _f(p.get("pillar_move_potential")),
            "ts_score": _f(p.get("pillar_trade_structure")),
            "max_favorable_excursion": mfe,
            "current_pnl_pct": pnl,
        })
    print(f"  usable: {len(rows)}")

    # FVT enrichment
    eval_ids = list({r["evaluation_id"] for r in rows if r["evaluation_id"]})
    print(f"  fetching FVT for {len(eval_ids)} eval_ids...")
    enrich: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=32) as ex:
        futs = {ex.submit(_fetch_fvt, fvt, eid): eid for eid in eval_ids}
        done = 0
        for fut in as_completed(futs):
            enrich[futs[fut]] = fut.result()
            done += 1
            if done % 2000 == 0:
                print(f"    {done}/{len(eval_ids)}")
    for r in rows:
        feats = enrich.get(r["evaluation_id"], {})
        for fname in FVT_FEATURES:
            if r.get(fname) is None and fname in feats:
                r[fname] = feats[fname]
    print("  FVT complete")

    # Per-scanner baselines
    by_scanner: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_scanner[r["scanner_source"]].append(r)

    print()
    print("=" * 100)
    print("SCANNER BASELINES (win-rate Wilson lower, mean P&L, P-conviction est)")
    print("=" * 100)
    header = (
        f"{'Scanner':<24} {'n':>6} {'wins':>5} {'win%':>6} "
        f"{'win_lower':>10} {'mean_pnl':>9} {'p_conv_est':>11}"
    )
    print(header)  # noqa
    print("-" * 100)

    scanner_baselines: dict[str, dict] = {}
    for scanner, cohort in sorted(by_scanner.items(), key=lambda x: -len(x[1])):
        n = len(cohort)
        wins = sum(1 for r in cohort if (r.get("current_pnl_pct") or 0) > 0)
        pnls = [r["current_pnl_pct"] for r in cohort if r.get("current_pnl_pct") is not None]
        mean_pnl = statistics.mean(pnls) if pnls else 0.0
        win_point, win_lower, win_upper = wilson_ci(wins, n)
        scanner_baselines[scanner] = {
            "win_point": win_point, "win_lower": win_lower,
            "mean_pnl": mean_pnl, "n": n,
        }
        pnl_norm = normalize_pnl_pct(mean_pnl)
        p_conv_est = 100 * win_lower * pnl_norm
        print(f"{scanner:<24} {n:>6} {wins:>5} {win_point*100:>5.1f}% {win_lower*100:>8.2f}% "
              f"{mean_pnl:>+8.2f}% {p_conv_est:>10.1f}")

    # Whole-scanner candidates first (the simplest possible archetypes)
    print()
    print("=" * 100)
    print("WHOLE-SCANNER P-CANDIDATES (just `scanner_source = X`)")
    print("=" * 100)

    header_ws = (
        f"{'Scanner':<24} {'n':>6} {'win_lower':>10} {'mean_pnl':>9} "
        f"{'p_conv_est':>11} {'profitable?':>12}"
    )
    print(header_ws)
    print("-" * 100)
    for scanner, base in scanner_baselines.items():
        if base["n"] < MIN_N:
            continue
        is_profitable = base["mean_pnl"] > MIN_MEAN_PNL
        pnl_norm = normalize_pnl_pct(base["mean_pnl"])
        p_conv = 100 * base["win_lower"] * pnl_norm
        marker = "*" if is_profitable else " "
        print(f"{scanner:<24}{marker} {base['n']:>5} {base['win_lower']*100:>8.2f}% "
              f"{base['mean_pnl']:>+8.2f}% {p_conv:>10.1f}  "
              f"{'YES' if is_profitable else 'NO':>12}")

    # Within-scanner mining for refinements (only on profitable scanners)
    print()
    print("=" * 100)
    banner_within = (
        f"WITHIN-SCANNER MINING — pairs and triples "
        f"(n>={MIN_N}, mean_pnl>{MIN_MEAN_PNL}%, win_lift>={MIN_WIN_LIFT_LOWER}x)"
    )
    print(banner_within)
    print("=" * 100)

    candidates: list[dict] = []
    for scanner, cohort in by_scanner.items():
        if len(cohort) < 50:
            continue
        base = scanner_baselines[scanner]
        feats = list(BUCKETS.keys())

        # Singles
        for f in feats:
            buckets: dict[str, list[dict]] = defaultdict(list)
            for r in cohort:
                b = BUCKETS[f](r)
                if b is not None:
                    buckets[b].append(r)
            for bname, sub in buckets.items():
                stats = cohort_stats(sub, base)
                if passes_filters(stats, base):
                    candidates.append({
                        "scanner": scanner,
                        "depth": 1,
                        "conditions": [f"{f}={bname}"],
                        **{k: v for k, v in stats.items() if k != "skip"},
                    })

        # Pairs
        for i, f1 in enumerate(feats):
            for f2 in feats[i+1:]:
                buckets2: dict[tuple, list[dict]] = defaultdict(list)
                for r in cohort:
                    b1 = BUCKETS[f1](r)
                    b2 = BUCKETS[f2](r)
                    if b1 is None or b2 is None:
                        continue
                    buckets2[(b1, b2)].append(r)
                for (b1, b2), sub in buckets2.items():
                    stats = cohort_stats(sub, base)
                    if passes_filters(stats, base):
                        candidates.append({
                            "scanner": scanner,
                            "depth": 2,
                            "conditions": [f"{f1}={b1}", f"{f2}={b2}"],
                            **{k: v for k, v in stats.items() if k != "skip"},
                        })

        # Triples — limit to profitable scanners with enough data
        if len(cohort) < 200 or base["mean_pnl"] < 0:
            continue
        for i, f1 in enumerate(feats):
            for j, f2 in enumerate(feats[i+1:], i+1):
                for f3 in feats[j+1:]:
                    buckets3: dict[tuple, list[dict]] = defaultdict(list)
                    for r in cohort:
                        b1 = BUCKETS[f1](r)
                        b2 = BUCKETS[f2](r)
                        b3 = BUCKETS[f3](r)
                        if b1 is None or b2 is None or b3 is None:
                            continue
                        buckets3[(b1, b2, b3)].append(r)
                    for (b1, b2, b3), sub in buckets3.items():
                        stats = cohort_stats(sub, base)
                        if passes_filters(stats, base):
                            candidates.append({
                                "scanner": scanner,
                                "depth": 3,
                                "conditions": [f"{f1}={b1}", f"{f2}={b2}", f"{f3}={b3}"],
                                **{k: v for k, v in stats.items() if k != "skip"},
                            })

    # Deduplicate
    seen = set()
    dedup = []
    for c in candidates:
        key = (c["scanner"], tuple(sorted(c["conditions"])))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)

    # Rank by P-conviction estimate × log(n) (so we favor stable + high-conv)
    def score(c):
        return c["p_conviction_est"] * math.log(max(2, c["n"]))

    dedup.sort(key=lambda c: -score(c))

    print(f"\nTotal candidates passing filters: {len(dedup)}")
    print("\nTop 30 by P-conviction × log(n) score:\n")
    print(f"{'#':<3} {'depth':>5} {'scanner':<24} {'conditions':<55} {'n':>5} "
          f"{'win_lower':>9} {'mean_pnl':>9} {'p_conv':>7} {'HR200':>5}")
    print("-" * 140)
    for i, c in enumerate(dedup[:30], 1):
        conds = " × ".join(c["conditions"])
        print(f"{i:<3} {c['depth']:>5} {c['scanner']:<24} {conds[:54]:<55} {c['n']:>5} "
              f"{c['win_lower']*100:>7.2f}% {c['mean_pnl']:>+8.2f}% "
              f"{c['p_conviction_est']:>6.1f} {c['hr200']:>5}")

    # Save JSON
    out_path = "/tmp/v5_p_archetype_candidates.json"
    with open(out_path, "w") as fp:
        json.dump(dedup, fp, indent=2, default=str)
    print(f"\nWrote candidates to: {out_path}")
    print(f"Total candidates: {len(dedup)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
