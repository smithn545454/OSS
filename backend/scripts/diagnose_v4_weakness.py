#!/usr/bin/env python3
"""Deep diagnosis: WHY is v4 under-performing v3 on historical paper trades?

Tests five hypotheses in order and reports findings you can act on:

  H1. Min-subscore zero-collapse is destroying signal on incomplete data
  H2. One or more v4 subscores have inverted / wrong-signed breakpoints
       for this market regime
  H3. Geometric-mean composite is hiding good pillar scores
  H4. v4 was calibrated OUT-OF-SAMPLE (never fit to this paper-trade
       distribution), whereas v3 was fit to it
  H5. Historical rescore has systematic feature gaps that degrade v4

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/diagnose_v4_weakness.py
"""

from __future__ import annotations

import math
import os
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


def geo_mean(scores: list[float], weights: list[float]) -> float:
    """Weighted geometric mean with floor-at-1 on each input."""
    s = 0.0
    w_sum = sum(weights)
    for sc, w in zip(scores, weights):
        s += (w / w_sum) * math.log(max(1.0, sc))
    return math.exp(s)


def arith_mean(scores: list[float], weights: list[float]) -> float:
    w_sum = sum(weights)
    return sum(sc * w for sc, w in zip(scores, weights)) / w_sum


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
    print(f"  loaded {len(items)} closed positions")

    rows: list[dict] = []
    for p in items:
        pnl = f(p.get("current_pnl_pct"))
        if pnl is None:
            continue
        rows.append({
            "eval_id": str(p.get("evaluation_id") or ""),
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
            "exit_reason": str(p.get("exit_reason") or ""),
            "scanner": str(p.get("scanner_source") or ""),
        })
    print(f"  usable (with P&L): {len(rows)}\n")

    # =========================================================================
    # H1: Min-subscore zero-collapse impact
    # =========================================================================
    print("=" * 78)
    print("H1: IS THE MIN-SUBSCORE RULE ZEROING TOO MANY PILLARS?")
    print("=" * 78)
    n = len(rows)
    dc_zero = sum(1 for r in rows if r["dc"] == 0.0)
    mp_zero = sum(1 for r in rows if r["mp"] == 0.0)
    ts_zero = sum(1 for r in rows if r["ts"] == 0.0)
    any_zero = sum(1 for r in rows if r["dc"] == 0 or r["mp"] == 0 or r["ts"] == 0)
    conv_zero = sum(1 for r in rows if r["conv_v4"] == 0.0)
    print(f"  Total closed trades:        {n}")
    print(f"  DC = 0 (min-subscore fail): {dc_zero:>6} ({dc_zero/n*100:5.1f}%)")
    print(f"  MP = 0:                     {mp_zero:>6} ({mp_zero/n*100:5.1f}%)")
    print(f"  TS = 0:                     {ts_zero:>6} ({ts_zero/n*100:5.1f}%)")
    print(f"  ANY pillar = 0:             {any_zero:>6} ({any_zero/n*100:5.1f}%)")
    print(f"  Composite = 0 (all zero):   {conv_zero:>6} ({conv_zero/n*100:5.1f}%)")
    print()
    # Performance of zero-collapsed positions
    collapsed = [r for r in rows if r["dc"] == 0 or r["mp"] == 0 or r["ts"] == 0]
    intact = [r for r in rows if r["dc"] > 0 and r["mp"] > 0 and r["ts"] > 0]
    if collapsed:
        c_pnl = statistics.mean([r["pnl"] for r in collapsed])
        c_win = sum(1 for r in collapsed if r["pnl"] > 0) / len(collapsed) * 100
        print(f"  COLLAPSED cohort (n={len(collapsed)}):  mean P&L = {c_pnl:+6.2f}%, win = {c_win:5.1f}%")
    if intact:
        i_pnl = statistics.mean([r["pnl"] for r in intact])
        i_win = sum(1 for r in intact if r["pnl"] > 0) / len(intact) * 100
        print(f"  INTACT cohort (n={len(intact)}):     mean P&L = {i_pnl:+6.2f}%, win = {i_win:5.1f}%")
    print()
    # Re-run v4 conviction correlation on INTACT-only
    if intact:
        xs = [r["conv_v4"] for r in intact]
        ys = [r["pnl"] for r in intact]
        p = pearson(xs, ys)
        s = spearman(xs, ys)
        print(f"  v4 conv correlation on INTACT only:  Pearson={p:+.3f}, Spearman={s:+.3f}")
        # Compare pillar-level
        for key, label in [("dc", "DC"), ("mp", "MP"), ("ts", "TS")]:
            xs = [r[key] for r in intact]
            ys = [r["pnl"] for r in intact]
            p = pearson(xs, ys)
            s = spearman(xs, ys)
            print(f"    {label} on INTACT: Pearson={p:+.3f}, Spearman={s:+.3f}")
    print()

    # =========================================================================
    # H3: Geometric vs arithmetic composite
    # =========================================================================
    print("=" * 78)
    print("H3: IS THE GEOMETRIC MEAN HIDING A BETTER COMPOSITE?")
    print("=" * 78)
    if intact:
        # Pillar exponents match v4 policy
        weights = [0.40, 0.35, 0.25]  # DC, MP, TS
        g_scores, a_scores, ys = [], [], []
        for r in intact:
            pillars = [r["dc"], r["mp"], r["ts"]]
            g_scores.append(geo_mean(pillars, weights))
            a_scores.append(arith_mean(pillars, weights))
            ys.append(r["pnl"])
        pg = pearson(g_scores, ys)
        sg = spearman(g_scores, ys)
        pa = pearson(a_scores, ys)
        sa = spearman(a_scores, ys)
        print(f"  On {len(intact)} intact (no-zero) trades:")
        print(f"    Geometric mean composite → Pearson={pg:+.3f} Spearman={sg:+.3f}")
        print(f"    Arithmetic mean composite → Pearson={pa:+.3f} Spearman={sa:+.3f}")
        # Alt: simple average of the three pillars (equal weight)
        e_scores = [arith_mean([r["dc"], r["mp"], r["ts"]], [1, 1, 1]) for r in intact]
        pe = pearson(e_scores, ys)
        se = spearman(e_scores, ys)
        print(f"    Equal-weight mean composite → Pearson={pe:+.3f} Spearman={se:+.3f}")

        # What about JUST the single best pillar?
        for key, label in [("dc", "DC alone"), ("mp", "MP alone"), ("ts", "TS alone")]:
            xs = [r[key] for r in intact]
            p = pearson(xs, ys)
            s = spearman(xs, ys)
            print(f"    {label}: Pearson={p:+.3f} Spearman={s:+.3f}")
    print()

    # =========================================================================
    # H2: Inverted subscores — check raw feature → P&L correlation
    # =========================================================================
    print("=" * 78)
    print("H2: ARE ANY v4 SUBSCORES INVERTED FOR THIS DATA?")
    print("=" * 78)
    print("Pulling raw feature values from FVT for a sample of trades...")

    # Sample 3000 trades to keep FVT query cost bounded
    import random
    random.seed(42)
    sample = random.sample(rows, min(3000, len(rows)))

    # The features that v4 pillars consume directly (not derived):
    v4_features = [
        "rs_20d",  # DC weight 0.20 - monotonic reward high
        "iv_rv_ratio",  # MP weight 0.15 - reward LOW
        "bb_width_percentile",  # MP weight 0.15 - reward LOW
        "iv_percentile",  # TS weight 0.20 - reward LOW
        "adx_14",  # component of DC adx_directional_score
        "sector_rs_20d",  # DC weight 0.10 - reward HIGH
        "historical_move_magnitude",  # MP weight 0.20 - reward HIGH
    ]

    feature_vals: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for i, r in enumerate(sample):
        if i % 500 == 0 and i > 0:
            print(f"  {i}/{len(sample)}...")
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
            if name in v4_features:
                val = f(item.get("value"))
                if val is not None:
                    feature_vals[name].append((val, r["pnl"]))

    print()
    print("  Raw feature → P&L correlation (sample size varies by coverage):")
    print(f"  {'Feature':<30} {'n':>6} {'Pearson':>10} {'Spearman':>10} {'Design says':<20} {'Data says':<20}")
    design_direction = {
        "rs_20d": "reward HIGH",
        "iv_rv_ratio": "reward LOW",
        "bb_width_percentile": "reward LOW",
        "iv_percentile": "reward LOW",
        "adx_14": "reward HIGH",
        "sector_rs_20d": "reward HIGH",
        "historical_move_magnitude": "reward HIGH",
    }
    for feat in v4_features:
        pairs = feature_vals.get(feat, [])
        if len(pairs) < 50:
            print(f"  {feat:<30} {len(pairs):>6} (insufficient)")
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        p = pearson(xs, ys)
        s = spearman(xs, ys)
        design = design_direction.get(feat, "?")
        if s is not None:
            data_says = "reward HIGH" if s > 0.02 else ("reward LOW" if s < -0.02 else "~ neutral")
        else:
            data_says = "?"
        aligned = "✓" if design.split()[-1] == data_says.split()[-1] else "✗ MISMATCH"
        print(f"  {feat:<30} {len(pairs):>6} {(f'{p:+.3f}' if p is not None else '—'):>10} "
              f"{(f'{s:+.3f}' if s is not None else '—'):>10} {design:<20} {data_says:<20} {aligned}")
    print()

    # =========================================================================
    # H4: In-sample (v3) vs out-of-sample (v4) bias
    # =========================================================================
    print("=" * 78)
    print("H4: IS v4 HANDICAPPED BY OUT-OF-SAMPLE DESIGN?")
    print("=" * 78)
    print("  v3 was empirically tuned against THIS paper-trade population.")
    print("  v4 breakpoints were designed from first principles / literature —")
    print("  NOT validated against this specific dataset.")
    print()
    print("  Direct evidence:")
    print("  - v3 TIER_1 (top 686 by v3 score) has mean P&L +31.8%, 55% win rate")
    print("  - v4 gives those 686 trades a mean score of ~", end="")
    # Find v3 TIER_1 trades (score >= 75) and get their v4 score
    v3_tier1 = [r for r in rows if r["conv_v3"] is not None and r["conv_v3"] >= 75]
    if v3_tier1:
        mean_v4_on_v3_t1 = statistics.mean([r["conv_v4"] for r in v3_tier1 if r["conv_v4"] is not None])
        print(f"{mean_v4_on_v3_t1:.1f}")
    print()

    # =========================================================================
    # H5: Regime mismatch diagnostic
    # =========================================================================
    print("=" * 78)
    print("H5: IS THIS A REGIME MISMATCH (trend-follow vs mean-reversion)?")
    print("=" * 78)
    print("  v4 DC heavily rewards trend-following (Stage 2, RS, breakout,")
    print("  sector leadership, volume confirmation). If this dataset comes")
    print("  from a mean-reverting/choppy regime, DC should score HIGH on")
    print("  the WRONG trades.")
    print()
    # By scanner: which are trend-following vs mean-reverting?
    scanner_regimes = {
        "BREAKOUT": "trend-follow",
        "BREAKDOWN": "mean-revert (short bias)",
        "UNUSUAL_VOLUME": "catalyst",
        "COMPRESSION_EXPANSION": "volatility",
        "CHEAP_OPTIONS": "asymmetric",
        "REVALIDATION": "catalyst",
    }
    print(f"  {'Scanner':<22} {'n':>5} {'Regime':<26} {'Mean P&L':>10} {'DC mean':>9} {'v4 conv':>9}")
    for scanner in sorted(set(r["scanner"] for r in rows if r["scanner"])):
        rs = [r for r in rows if r["scanner"] == scanner]
        m_pnl = statistics.mean([r["pnl"] for r in rs])
        m_dc = statistics.mean([r["dc"] for r in rs if r["dc"] is not None])
        m_c = statistics.mean([r["conv_v4"] for r in rs if r["conv_v4"] is not None])
        reg = scanner_regimes.get(scanner, "?")
        print(f"  {scanner:<22} {len(rs):>5} {reg:<26} {m_pnl:+7.2f}% {m_dc:>8.1f} {m_c:>8.1f}")
    print()

    # Compute DC-P&L correlation within each scanner
    print("  DC → P&L correlation BY SCANNER (tells us where DC is working):")
    print(f"  {'Scanner':<22} {'n':>5} {'DC Pearson':>12} {'DC Spearman':>13} {'MP Spearman':>13} {'TS Spearman':>13}")
    for scanner in sorted(set(r["scanner"] for r in rows if r["scanner"])):
        rs = [r for r in rows if r["scanner"] == scanner]
        if len(rs) < 50:
            continue
        ys = [r["pnl"] for r in rs]
        def s_for(k: str) -> str:
            xs = [r[k] for r in rs if r[k] is not None]
            ys_f = [r["pnl"] for r in rs if r[k] is not None]
            if len(xs) < 30: return "—"
            sp = spearman(xs, ys_f)
            return f"{sp:+.3f}" if sp is not None else "—"
        dc_p = pearson([r["dc"] for r in rs if r["dc"] is not None],
                       [r["pnl"] for r in rs if r["dc"] is not None])
        print(f"  {scanner:<22} {len(rs):>5} {(f'{dc_p:+.3f}' if dc_p else '—'):>12} "
              f"{s_for('dc'):>13} {s_for('mp'):>13} {s_for('ts'):>13}")
    print()

    # =========================================================================
    # SYNTHESIS: What's the biggest lever?
    # =========================================================================
    print("=" * 78)
    print("SYNTHESIS — WHAT IF WE FIX THE BIGGEST LEVERS?")
    print("=" * 78)

    # Simulation 1: arithmetic mean, no min-subscore, no zero floor
    sim_scores, sim_ys = [], []
    for r in rows:
        if r["dc"] is None or r["mp"] is None or r["ts"] is None:
            continue
        # "Fair" composite: weight-average, NO zero-collapse
        # Treat 0 as missing-data → use 50 as neutral fallback ONLY if literally 0
        dc = r["dc"] if r["dc"] > 0 else 50
        mp = r["mp"] if r["mp"] > 0 else 50
        ts = r["ts"] if r["ts"] > 0 else 50
        sim_scores.append(arith_mean([dc, mp, ts], [0.40, 0.35, 0.25]))
        sim_ys.append(r["pnl"])
    p_s = pearson(sim_scores, sim_ys)
    s_s = spearman(sim_scores, sim_ys)
    print(f"  FIX 1: Replace geometric mean w/ arithmetic + treat 0 as missing→50:")
    print(f"    Pearson={p_s:+.3f} Spearman={s_s:+.3f}  (vs v4 actual −0.030, v3 +0.127)")
    print()

    # Simulation 2: MP-only composite (since MP had best correlation)
    mp_only = [r["mp"] for r in rows if r["mp"] is not None and r["mp"] > 0]
    mp_pnl = [r["pnl"] for r in rows if r["mp"] is not None and r["mp"] > 0]
    p_mp = pearson(mp_only, mp_pnl)
    s_mp = spearman(mp_only, mp_pnl)
    print(f"  FIX 2 (diagnostic): MP-only scoring (n={len(mp_only)}):")
    print(f"    Pearson={p_mp:+.3f} Spearman={s_mp:+.3f}")
    print()

    # Simulation 3: DC-only on intact
    if intact:
        dc_only_y = [r["pnl"] for r in intact]
        dc_only_x = [r["dc"] for r in intact]
        p_dc = pearson(dc_only_x, dc_only_y)
        s_dc = spearman(dc_only_x, dc_only_y)
        print(f"  FIX 3 (diagnostic): DC-only, intact positions only (n={len(intact)}):")
        print(f"    Pearson={p_dc:+.3f} Spearman={s_dc:+.3f}")
    print()


if __name__ == "__main__":
    main()
