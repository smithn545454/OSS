#!/usr/bin/env python3
"""Structural-only pillar design and backtest.

Builds a 3-pillar scoring system using ONLY structural features
(contract characteristics + signal confirmation) that work regardless
of market direction or regime. Excludes:
- option_type (PUT vs CALL bias could flip)
- scanner_source (some scanners might be regime-specific)
- iv_regime, SPY returns, RS (market environment)
- directional indicators (+DI/-DI, MACD, returns)
- verdict_at_entry, quality_tier (meta-outputs)

Then backtests the design on all 15,505 closed positions.
"""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path

from feature_outcome_analysis import (
    ANALYSIS_CACHE,
    DATA_CACHE,
    OUTPUT_DIR,
    compute_quintiles,
    generate_report,
    pearson_r,
    analytical_p_value,
    run_backtest,
    score_position,
    win_rate,
    safe_median,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

STRUCTURAL_DESIGN_CACHE = OUTPUT_DIR / "structural_pillar_design.json"
STRUCTURAL_BACKTEST_CACHE = OUTPUT_DIR / "structural_pillar_backtest.json"
STRUCTURAL_REPORT = OUTPUT_DIR / "structural_pillar_report.md"


def build_structural_design(analysis: dict) -> dict:
    """Build a 3-pillar design using only structural features."""
    tier1_lookup = {f["feature"]: f for f in analysis["tier1_numeric"]}
    tier2_lookup = {f["feature"]: f for f in analysis["tier2_numeric"]}
    cat_lookup = {f["feature"]: f for f in analysis["tier1_categorical"] + analysis["tier2_categorical"]}

    def make_numeric_subscore(feat_key: str, target_weight: float) -> dict | None:
        """Build a subscore from numeric feature quintiles."""
        feat = tier1_lookup.get(feat_key) or tier2_lookup.get(feat_key)
        if not feat or not feat["quintiles"]:
            log.warning(f"Feature {feat_key} not found or has no quintiles")
            return None

        breakpoints = []
        for q in feat["quintiles"]:
            mid_val = (q["range_low"] + q["range_high"]) / 2
            # Score 20-100 based on quintile rank, direction-adjusted
            if feat["direction"] == "lower_is_better":
                score = (6 - q["quintile"]) * 20
            else:
                score = q["quintile"] * 20
            breakpoints.append({
                "value": round(mid_val, 4),
                "score": score,
                "q_win_rate": q["win_rate"],
                "q_avg_return": q["avg_return"],
            })

        return {
            "feature": feat_key,
            "display_name": feat["display_name"],
            "weight": round(target_weight, 3),
            "direction": feat["direction"],
            "pearson_r": feat["pearson_r"],
            "cohens_d": feat["cohens_d"],
            "breakpoints": breakpoints,
            "monotonic": feat["monotonic"],
            "tier": "tier1" if not feat_key.startswith("fvt_") else "tier2",
        }

    def make_categorical_subscore(feat_key: str, target_weight: float) -> dict | None:
        """Build a subscore from categorical feature category rankings."""
        feat = cat_lookup.get(feat_key)
        if not feat:
            log.warning(f"Categorical {feat_key} not found")
            return None

        cats = [c for c in feat["categories"] if c["n"] >= 30]
        if not cats:
            return None

        # Sort by avg_return ascending, assign scores 10-90
        sorted_cats = sorted(cats, key=lambda c: c["avg_return"])
        n = len(sorted_cats)
        cat_score_map = {}
        for i, cat in enumerate(sorted_cats):
            score = 10 + (80 * i / max(n - 1, 1))
            cat_score_map[cat["category"]] = round(score, 1)

        return {
            "feature": feat_key,
            "display_name": feat_key.replace("_", " ").title(),
            "weight": round(target_weight, 3),
            "direction": "categorical",
            "pearson_r": 0,
            "cohens_d": 0,  # not meaningful for categorical
            "breakpoints": [],
            "monotonic": "categorical",
            "tier": "tier1",
            "is_categorical": True,
            "category_score_map": cat_score_map,
        }

    # =========================================================================
    # PILLAR 1: PREMIUM LEVERAGE (65% weight)
    # How cheap and asymmetric is the contract?
    # =========================================================================
    p1_subscores = []
    # Entry Delta — 77% (dominant, d=0.42)
    s = make_numeric_subscore("entry_delta", 0.77)
    if s:
        p1_subscores.append(s)
    # Entry IV — 23% (d=0.13)
    s = make_numeric_subscore("entry_iv", 0.23)
    if s:
        p1_subscores.append(s)

    pillar1 = {
        "name": "Premium Leverage",
        "description": (
            "Structural measure of how cheap and asymmetric the contract is. "
            "Far-OTM contracts with low implied volatility offer the best "
            "risk/reward profile, regardless of whether the trade is a CALL or PUT."
        ),
        "weight": 0.65,
        "importance": round(sum(abs(s["cohens_d"]) for s in p1_subscores), 4),
        "n_subscores": len(p1_subscores),
        "subscores": p1_subscores,
    }

    # =========================================================================
    # PILLAR 2: MOVE FEASIBILITY (25% weight)
    # Can the required move realistically happen within the timeframe?
    # =========================================================================
    p2_subscores = []
    # Time-Adjusted Feasibility — 50%
    s = make_numeric_subscore("fvt_time_adjusted_feasibility", 0.50)
    if s:
        p2_subscores.append(s)
    # Feasibility Ratio — 50%
    s = make_numeric_subscore("fvt_feasibility_ratio", 0.50)
    if s:
        p2_subscores.append(s)

    pillar2 = {
        "name": "Move Feasibility",
        "description": (
            "Whether the price move required to reach profitability is "
            "realistic given the option's time horizon and the underlying's "
            "expected volatility. Direction-agnostic structural measure."
        ),
        "weight": 0.25,
        "importance": round(sum(abs(s["cohens_d"]) for s in p2_subscores), 4),
        "n_subscores": len(p2_subscores),
        "subscores": p2_subscores,
    }

    # =========================================================================
    # PILLAR 3: SETUP QUALITY (10% weight)
    # System confirmation strength and DTE suitability
    # =========================================================================
    p3_subscores = []
    # DTE Bucket — 60% (categorical, A=best)
    s = make_categorical_subscore("dte_bucket", 0.60)
    if s:
        p3_subscores.append(s)
    # Scanner Convergence — 40%
    s = make_numeric_subscore("convergence_count", 0.40)
    if s:
        p3_subscores.append(s)

    pillar3 = {
        "name": "Setup Quality",
        "description": (
            "Structural setup indicators: days-to-expiration bucket suitability "
            "and how many independent scanners converged on the opportunity. "
            "Direction-agnostic meta-signal quality measures."
        ),
        "weight": 0.10,
        "importance": round(sum(abs(s["cohens_d"]) for s in p3_subscores), 4),
        "n_subscores": len(p3_subscores),
        "subscores": p3_subscores,
    }

    design = {
        "pillars": [pillar1, pillar2, pillar3],
        "description": (
            "Structural-only 3-pillar system. Excludes all features that could "
            "reflect short-term market regime (PUT vs CALL bias, scanner-specific "
            "performance, IV regime, market returns, directional indicators). "
            "Uses only features that describe the contract itself or direction-"
            "agnostic feasibility measures."
        ),
        "tier2_recommendations": [
            {
                "feature": "fvt_adx_14",
                "display_name": "ADX (14)",
                "cohens_d": -0.4087,
                "pearson_r": -0.2266,
                "recommendation": (
                    "Denormalize onto PaperPosition. ADX measures trend strength "
                    "direction-agnostically — a strong trend (high ADX) helps both "
                    "calls and puts that are positioned with the trend. Only n=395 "
                    "currently due to limited feature history, but it's the strongest "
                    "structural directional-strength signal available."
                ),
            },
        ],
        "total_features_selected": sum(p["n_subscores"] for p in [pillar1, pillar2, pillar3]),
    }

    return design


def main():
    log.info("Loading cached analysis and position data...")
    with open(ANALYSIS_CACHE) as f:
        analysis = json.load(f)
    with open(DATA_CACHE) as f:
        positions = json.load(f)

    log.info("Building structural-only pillar design...")
    design = build_structural_design(analysis)

    with open(STRUCTURAL_DESIGN_CACHE, "w") as f:
        json.dump(design, f, indent=2)
    log.info(f"Design saved to {STRUCTURAL_DESIGN_CACHE}")

    log.info("Running backtest...")
    backtest = run_backtest(positions, design)

    with open(STRUCTURAL_BACKTEST_CACHE, "w") as f:
        json.dump(backtest, f, indent=2)

    log.info("Generating report...")
    report = generate_report(analysis, design, backtest)
    with open(STRUCTURAL_REPORT, "w") as f:
        f.write(report)

    # Print summary
    print()
    print("=" * 78)
    print("STRUCTURAL-ONLY PILLAR SYSTEM — BACKTEST SUMMARY")
    print("=" * 78)
    summary = analysis["summary"]
    print(f"Dataset: {summary['n_total']:,} positions")
    print(f"Baseline: {summary['overall_win_rate']}% WR, {summary['overall_avg_return']}% avg return")
    print()

    print("PILLAR CORRELATIONS:")
    for pname, pdata in backtest["new_pillar_correlations"].items():
        print(f"  {pname:25s} r={pdata['pearson_r']:+.4f}  (n={pdata['n']:,}, p={pdata['p_value']:.4f})")
    print()

    print("OLD vs NEW COMPOSITE:")
    old = backtest["old_pillar_correlations"].get("Conviction", {}).get("pearson_r", 0)
    new = backtest["new_composite"]["pearson_r"]
    print(f"  Old Conviction Score  r={old:+.4f}")
    print(f"  New Composite Score   r={new:+.4f}  (improvement: {new - old:+.4f})")
    print()

    print("COMPOSITE QUINTILE PERFORMANCE:")
    print(f"  {'Quintile':<12} {'n':<8} {'Win Rate':<12} {'Avg Return':<12}")
    for q in backtest["composite_quintiles"]:
        print(f"  Q{q['quintile']:<11} {q['n']:<8,} {q['win_rate']:.1f}%{'':<7} {q['avg_return']:+.2f}%")
    print()

    print("DECILE ANALYSIS:")
    da = backtest["decile_analysis"]
    print(f"  Top 10%:    n={da['top_10pct']['n']}  WR={da['top_10pct']['win_rate']}%  avg={da['top_10pct']['avg_return']}%  composite={da['top_10pct']['avg_composite']}")
    print(f"  Bottom 10%: n={da['bottom_10pct']['n']}  WR={da['bottom_10pct']['win_rate']}%  avg={da['bottom_10pct']['avg_return']}%  composite={da['bottom_10pct']['avg_composite']}")
    print()

    print("SCANNER-SPECIFIC COMPARISON:")
    for scanner, data in sorted(backtest["scanner_comparison"].items()):
        print(f"  {scanner:25s} n={data['n']:>5,}  old r={data['old_conviction_r']:+.4f}  new r={data['new_composite_r']:+.4f}  ({data['improvement']:+.4f})")
    print()

    print(f"Full report: {STRUCTURAL_REPORT}")


if __name__ == "__main__":
    main()
