#!/usr/bin/env python3
"""Finalized 3-pillar structural design.

Incorporates ALL significant structural features discovered in the analysis,
with return-based quintile scoring (non-monotonic aware).

Pillars:
1. Premium Leverage — how cheap and asymmetric the contract is
2. Underlying Behavior — the stock's volatility profile and move feasibility
3. Setup Quality — meta-indicators of a high-quality entry
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from feature_outcome_analysis import (
    ANALYSIS_CACHE,
    DATA_CACHE,
    OUTPUT_DIR,
    generate_report,
    run_backtest,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

FINAL_DESIGN_CACHE = OUTPUT_DIR / "final_pillar_design.json"
FINAL_BACKTEST_CACHE = OUTPUT_DIR / "final_pillar_backtest.json"
FINAL_REPORT = OUTPUT_DIR / "final_pillar_report.md"


def make_return_based_subscore(feat: dict, target_weight: float) -> dict | None:
    """Build a subscore where breakpoint scores come from the empirical avg return
    of each quintile, not from the quintile's rank order. This handles
    non-monotonic features correctly.
    """
    if not feat.get("quintiles"):
        return None

    quintiles = feat["quintiles"]
    returns = [q["avg_return"] for q in quintiles]
    min_ret, max_ret = min(returns), max(returns)
    span = max_ret - min_ret if max_ret > min_ret else 1.0

    breakpoints = []
    for q in quintiles:
        mid_val = (q["range_low"] + q["range_high"]) / 2
        # Map avg return to 10-90 score range
        normalized = (q["avg_return"] - min_ret) / span
        score = round(10 + 80 * normalized, 1)
        breakpoints.append({
            "value": round(mid_val, 4),
            "score": score,
            "q_win_rate": q["win_rate"],
            "q_avg_return": q["avg_return"],
        })

    return {
        "feature": feat["feature"],
        "display_name": feat["display_name"],
        "weight": round(target_weight, 4),
        "direction": feat["direction"],
        "pearson_r": feat["pearson_r"],
        "cohens_d": feat["cohens_d"],
        "breakpoints": breakpoints,
        "monotonic": feat["monotonic"],
        "tier": "tier1" if not feat["feature"].startswith("fvt_") else "tier2",
    }


def make_categorical_subscore(feat: dict, target_weight: float) -> dict | None:
    """Build a categorical subscore from avg return ranking."""
    cats = [c for c in feat.get("categories", []) if c["n"] >= 30]
    if not cats:
        return None

    returns = [c["avg_return"] for c in cats]
    min_ret, max_ret = min(returns), max(returns)
    span = max_ret - min_ret if max_ret > min_ret else 1.0

    cat_score_map = {}
    for cat in cats:
        normalized = (cat["avg_return"] - min_ret) / span
        score = round(10 + 80 * normalized, 1)
        cat_score_map[cat["category"]] = score

    return {
        "feature": feat["feature"],
        "display_name": feat["feature"].replace("_", " ").title(),
        "weight": round(target_weight, 4),
        "direction": "categorical",
        "pearson_r": 0,
        "cohens_d": 0,
        "breakpoints": [],
        "monotonic": "categorical",
        "tier": "tier1",
        "is_categorical": True,
        "category_score_map": cat_score_map,
    }


def build_final_design(analysis: dict) -> dict:
    """Build the finalized 3-pillar design from analysis data."""
    tier1 = {f["feature"]: f for f in analysis["tier1_numeric"]}
    tier2 = {f["feature"]: f for f in analysis["tier2_numeric"]}
    cats = {
        f["feature"]: f
        for f in analysis["tier1_categorical"] + analysis["tier2_categorical"]
    }

    # =========================================================================
    # Feature selection (structural only, significant, |d| >= 0.03)
    # =========================================================================
    pillar1_features = [
        ("abs_entry_delta", tier1),       # |delta|, direction-agnostic
        ("fvt_iv", tier2),                # d=-0.22, raw IV (100% coverage)
        ("fvt_iv_percentile", tier2),     # d=-0.06, historical IV rank
        ("fvt_iv_rv_ratio", tier2),       # d=-0.03, IV vs RV fairness
    ]
    pillar2_features = [
        ("fvt_rv20", tier2),                        # d=-0.19, realized vol
        ("fvt_atr14_pct", tier2),                   # d=-0.10, average true range
        ("fvt_time_adjusted_feasibility", tier2),   # d=+0.10, move feasibility
        ("fvt_feasibility_ratio", tier2),           # d=+0.10, required/expected
        ("fvt_adx_14", tier2),                      # d=-0.41, trend strength (n=395)
    ]
    pillar3_features_numeric = [
        ("fvt_volume", tier2),            # d=-0.12, contrarian liquidity
        ("fvt_open_interest", tier2),     # d=-0.08, contrarian OI
        ("convergence_count", tier1),     # d=+0.05, scanner convergence
    ]
    pillar3_features_cat = [
        ("dte_bucket", cats),             # A=best, D=worst
    ]

    def _build(feature_list):
        """Build subscores for a list of (feature_key, source_dict)."""
        collected = []
        for key, source in feature_list:
            feat = source.get(key)
            if not feat:
                log.warning(f"  Missing: {key}")
                continue
            if not feat.get("is_significant"):
                log.warning(f"  Not significant: {key} (p={feat['p_value']:.4f})")
                continue
            collected.append(feat)
        return collected

    log.info("Collecting Pillar 1 features...")
    p1 = _build(pillar1_features)
    log.info("Collecting Pillar 2 features...")
    p2 = _build(pillar2_features)
    log.info("Collecting Pillar 3 features...")
    p3_num = _build(pillar3_features_numeric)
    p3_cat = []
    for key, source in pillar3_features_cat:
        c = source.get(key)
        if c and c.get("is_significant"):
            p3_cat.append(c)

    # =========================================================================
    # Compute within-pillar weights proportional to |Cohen's d|
    # =========================================================================
    def _compute_weights(numeric_feats, cat_feats=None):
        cat_feats = cat_feats or []
        total = sum(abs(f["cohens_d"]) for f in numeric_feats)
        # For categorical features, compute pseudo-d from avg return spread
        cat_pseudo_ds = []
        for c in cat_feats:
            returns = [cat["avg_return"] for cat in c["categories"] if cat["n"] >= 30]
            if len(returns) >= 2:
                spread = max(returns) - min(returns)
                # Normalize: 30pp spread ~= d=0.15 (modest-to-moderate effect)
                pseudo = spread / 200
                cat_pseudo_ds.append((c, pseudo))
                total += pseudo
            else:
                cat_pseudo_ds.append((c, 0))

        if total == 0:
            return {}, 0

        weights = {}
        for f in numeric_feats:
            weights[f["feature"]] = abs(f["cohens_d"]) / total
        for c, pseudo in cat_pseudo_ds:
            weights[c["feature"]] = pseudo / total
        return weights, total

    # Pillar 1
    p1_weights, p1_importance = _compute_weights(p1)
    p1_subscores = []
    for feat in sorted(p1, key=lambda f: -abs(f["cohens_d"])):
        s = make_return_based_subscore(feat, p1_weights[feat["feature"]])
        if s:
            p1_subscores.append(s)

    # Pillar 2
    p2_weights, p2_importance = _compute_weights(p2)
    p2_subscores = []
    for feat in sorted(p2, key=lambda f: -abs(f["cohens_d"])):
        s = make_return_based_subscore(feat, p2_weights[feat["feature"]])
        if s:
            p2_subscores.append(s)

    # Pillar 3
    p3_weights, p3_importance = _compute_weights(p3_num, p3_cat)
    p3_subscores = []
    # Numeric first
    for feat in sorted(p3_num, key=lambda f: -abs(f["cohens_d"])):
        s = make_return_based_subscore(feat, p3_weights[feat["feature"]])
        if s:
            p3_subscores.append(s)
    # Then categorical
    for c in p3_cat:
        s = make_categorical_subscore(c, p3_weights[c["feature"]])
        if s:
            p3_subscores.append(s)

    # =========================================================================
    # Pillar-level weights proportional to total within-pillar importance
    # =========================================================================
    total_importance = p1_importance + p2_importance + p3_importance

    pillar1 = {
        "name": "Premium Leverage",
        "description": (
            "How cheap and asymmetric the contract is. Far-OTM (low |delta|) "
            "contracts with low implied volatility offer the best risk/reward "
            "profile. Direction-agnostic: works for both calls and puts."
        ),
        "weight": round(p1_importance / total_importance, 4),
        "importance": round(p1_importance, 4),
        "n_subscores": len(p1_subscores),
        "subscores": p1_subscores,
    }

    pillar2 = {
        "name": "Underlying Behavior",
        "description": (
            "How the underlying stock is behaving and whether the required "
            "move is realistic. Captures volatility context, trend strength "
            "(ADX), and feasibility of reaching profit targets within DTE. "
            "Direction-agnostic structural measures."
        ),
        "weight": round(p2_importance / total_importance, 4),
        "importance": round(p2_importance, 4),
        "n_subscores": len(p2_subscores),
        "subscores": p2_subscores,
    }

    pillar3 = {
        "name": "Setup Quality",
        "description": (
            "Meta-indicators of a high-quality entry: scanner convergence, "
            "DTE bucket suitability, and contrarian liquidity (lower volume / "
            "open interest has historically predicted better outcomes within "
            "the already-filtered opportunity set)."
        ),
        "weight": round(p3_importance / total_importance, 4),
        "importance": round(p3_importance, 4),
        "n_subscores": len(p3_subscores),
        "subscores": p3_subscores,
    }

    design = {
        "pillars": [pillar1, pillar2, pillar3],
        "description": (
            "Finalized structural-only 3-pillar scoring system. Uses ALL "
            "significant structural features (p<0.05) available from the "
            "FeatureValueTable. Excludes features that could reflect short-"
            "term market regime: option_type (PUT/CALL bias), scanner_source, "
            "iv_regime, SPY returns, relative strength, MACD, +DI/-DI, 5d/20d "
            "returns. All subscores use empirical avg-return-based quintile "
            "scoring to handle non-monotonic relationships correctly."
        ),
        "tier2_recommendations": [],
        "total_features_selected": (
            len(p1_subscores) + len(p2_subscores) + len(p3_subscores)
        ),
    }

    return design


def main():
    log.info("Loading cached analysis and position data...")
    with open(ANALYSIS_CACHE) as f:
        analysis = json.load(f)
    with open(DATA_CACHE) as f:
        positions = json.load(f)

    log.info("Building finalized pillar design...")
    design = build_final_design(analysis)

    with open(FINAL_DESIGN_CACHE, "w") as f:
        json.dump(design, f, indent=2)
    log.info(f"Design saved to {FINAL_DESIGN_CACHE}")

    log.info("Running backtest...")
    backtest = run_backtest(positions, design)

    with open(FINAL_BACKTEST_CACHE, "w") as f:
        json.dump(backtest, f, indent=2)

    log.info("Generating report...")
    report = generate_report(analysis, design, backtest)
    with open(FINAL_REPORT, "w") as f:
        f.write(report)

    # Print summary
    print()
    print("=" * 80)
    print("FINALIZED 3-PILLAR DESIGN — BACKTEST SUMMARY")
    print("=" * 80)
    summary = analysis["summary"]
    print(f"Dataset: {summary['n_total']:,} positions")
    print(f"Baseline: {summary['overall_win_rate']}% WR, {summary['overall_avg_return']}% avg return")
    print()

    print("PILLAR STRUCTURE:")
    for p in design["pillars"]:
        print(f"\n  === {p['name']} (weight: {p['weight']*100:.1f}%) ===")
        for s in p["subscores"]:
            cat = " [CAT]" if s.get("is_categorical") else ""
            tier = f" [{s['tier']}]"
            print(f"    {s['weight']*100:5.1f}%  d={s['cohens_d']:+.4f}  {s['display_name']}{cat}{tier}")
    print()

    print("BACKTEST RESULTS:")
    print()
    print("Pillar correlations with outcomes:")
    for pname, pdata in backtest["new_pillar_correlations"].items():
        print(f"  {pname:25s} r={pdata['pearson_r']:+.4f}  (n={pdata['n']:,}, p={pdata['p_value']:.4f})")
    print()

    old = backtest["old_pillar_correlations"].get("Conviction", {}).get("pearson_r", 0)
    new = backtest["new_composite"]["pearson_r"]
    print(f"Old Conviction Score:  r={old:+.4f}")
    print(f"New Composite Score:   r={new:+.4f}  (improvement: {new - old:+.4f})")
    print()

    print("New Composite Quintile Performance:")
    for q in backtest["composite_quintiles"]:
        print(f"  Q{q['quintile']}: n={q['n']:,}  WR={q['win_rate']:5.1f}%  avg={q['avg_return']:+7.2f}%  median={q['median_return']:+7.2f}%")
    print()

    print("Decile Analysis:")
    da = backtest["decile_analysis"]
    print(f"  Top 10%:    n={da['top_10pct']['n']}  WR={da['top_10pct']['win_rate']}%  avg={da['top_10pct']['avg_return']}%  composite={da['top_10pct']['avg_composite']}")
    print(f"  Bottom 10%: n={da['bottom_10pct']['n']}  WR={da['bottom_10pct']['win_rate']}%  avg={da['bottom_10pct']['avg_return']}%  composite={da['bottom_10pct']['avg_composite']}")
    print()

    print("Scanner-Specific Comparison:")
    for scanner, d in sorted(backtest["scanner_comparison"].items()):
        print(f"  {scanner:22s} n={d['n']:>5,}  old r={d['old_conviction_r']:+.4f}  new r={d['new_composite_r']:+.4f}")
    print()

    print(f"Full report: {FINAL_REPORT}")


if __name__ == "__main__":
    main()
