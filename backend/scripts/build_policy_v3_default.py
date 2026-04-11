#!/usr/bin/env python3
"""Build default Policy v3.0.0 JSON from final_pillar_design.json.

This converts the analytically-derived pillar design (weights, breakpoints)
into a PolicyConfig-compatible JSON document that can be POSTed to the
/api/policies endpoint to seed the new scoring system.

The output structure matches PolicyConfig (with PillarConfig using
PillarConfigV2 sub-configs). Feature field names from the analysis script
are mapped to FeatureSet attribute names (stripping the `fvt_` prefix).
"""

from __future__ import annotations

import json
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"
DESIGN_PATH = OUTPUT_DIR / "final_pillar_design.json"
POLICY_PATH = OUTPUT_DIR / "policy_v3_default.json"


# Analysis-script feature key -> FeatureSet attribute name
# (Analysis uses `fvt_` prefix; FeatureSet / Evaluation attributes do not)
FEATURE_MAP = {
    "abs_entry_delta": "abs_delta",
    "entry_delta": "entry_delta",
    "fvt_iv": "iv",
    "fvt_iv_percentile": "iv_percentile",
    "fvt_iv_rv_ratio": "iv_rv_ratio",
    "fvt_adx_14": "adx_14",
    "fvt_rv20": "rv20",
    "fvt_feasibility_ratio": "feasibility_ratio",
    "fvt_time_adjusted_feasibility": "time_adjusted_feasibility",
    "fvt_atr14_pct": "atr14_pct",
    "fvt_volume": "volume",
    "fvt_open_interest": "open_interest",
    "convergence_count": "convergence_count",
    "dte_bucket": "dte_bucket",
}

# Human-readable display names
DISPLAY_NAMES = {
    "abs_delta": "|Entry Delta|",
    "entry_delta": "Entry Delta",
    "iv": "Implied Volatility",
    "iv_percentile": "IV Percentile",
    "iv_rv_ratio": "IV/RV Ratio",
    "adx_14": "ADX (14)",
    "rv20": "Realized Volatility (20d)",
    "feasibility_ratio": "Feasibility Ratio",
    "time_adjusted_feasibility": "Time-Adjusted Feasibility",
    "atr14_pct": "ATR % (14)",
    "volume": "Volume",
    "open_interest": "Open Interest",
    "convergence_count": "Scanner Convergence",
    "dte_bucket": "DTE Bucket",
}


def convert_subscore(src: dict) -> tuple[dict, bool]:
    """Convert one analysis subscore to PolicyConfig format.

    Returns (subscore_dict, is_categorical).
    """
    src_key = src["feature"]
    feature_field = FEATURE_MAP.get(src_key, src_key)
    display_name = DISPLAY_NAMES.get(feature_field, src_key)
    is_cat = bool(src.get("is_categorical"))
    source_tier = "tier2" if src_key.startswith("fvt_") else "tier1"

    if is_cat:
        return (
            {
                "subscore_id": feature_field,
                "display_name": display_name,
                "feature_field": feature_field,
                "weight": round(float(src["weight"]), 4),
                "category_scores": {
                    k: round(float(v), 2)
                    for k, v in src.get("category_score_map", {}).items()
                },
                "default_score": 50.0,
            },
            True,
        )

    breakpoints = [
        {"value": round(float(bp["value"]), 6), "score": round(float(bp["score"]), 2)}
        for bp in src.get("breakpoints", [])
    ]
    # Sort breakpoints by value for determinism
    breakpoints.sort(key=lambda b: b["value"])

    return (
        {
            "subscore_id": feature_field,
            "display_name": display_name,
            "feature_field": feature_field,
            "weight": round(float(src["weight"]), 4),
            "breakpoints": breakpoints,
            "source_tier": source_tier,
        },
        False,
    )


def normalize_subscores(subscores: list[dict]) -> None:
    """Ensure weights within a list sum to exactly 1.0 (handles rounding)."""
    total = sum(s["weight"] for s in subscores)
    if abs(total - 1.0) < 1e-6:
        return
    scale = 1.0 / total
    for s in subscores:
        s["weight"] = round(s["weight"] * scale, 4)
    # Fix any residual drift by adjusting the largest-weight subscore
    total = sum(s["weight"] for s in subscores)
    if abs(total - 1.0) > 1e-6:
        drift = 1.0 - total
        biggest = max(subscores, key=lambda s: s["weight"])
        biggest["weight"] = round(biggest["weight"] + drift, 4)


def build_pillar(src: dict, pillar_id: str) -> dict:
    """Convert one analysis pillar to PillarConfigV2 format."""
    numeric = []
    categorical = []
    for sub in src["subscores"]:
        converted, is_cat = convert_subscore(sub)
        if is_cat:
            categorical.append(converted)
        else:
            numeric.append(converted)

    # Normalize within-pillar weights across both lists together
    all_subs = numeric + categorical
    normalize_subscores(all_subs)

    return {
        "pillar_id": pillar_id,
        "display_name": src["name"],
        "description": src["description"],
        "numeric_subscores": numeric,
        "categorical_subscores": categorical,
    }


def build_policy_config(design: dict) -> dict:
    """Build the full PolicyConfig dict."""
    pillar_map = {
        "Premium Leverage": "PREMIUM_LEVERAGE",
        "Underlying Behavior": "UNDERLYING_BEHAVIOR",
        "Setup Quality": "SETUP_QUALITY",
    }
    key_map = {
        "Premium Leverage": "premium_leverage",
        "Underlying Behavior": "underlying_behavior",
        "Setup Quality": "setup_quality",
    }

    pillars_data = {"weights": {}}
    for src_pillar in design["pillars"]:
        name = src_pillar["name"]
        pillar_id = pillar_map[name]
        key = key_map[name]
        pillars_data[key] = build_pillar(src_pillar, pillar_id)
        pillars_data["weights"][key] = round(float(src_pillar["weight"]), 4)

    # Normalize pillar weights to sum to exactly 1.0
    total_w = sum(pillars_data["weights"].values())
    if abs(total_w - 1.0) > 1e-6:
        drift = 1.0 - total_w
        biggest_key = max(pillars_data["weights"], key=lambda k: pillars_data["weights"][k])
        pillars_data["weights"][biggest_key] = round(
            pillars_data["weights"][biggest_key] + drift, 4
        )

    # Build minimal PolicyConfig wrapper. All other sections use defaults.
    policy_config = {
        "pillars": pillars_data,
    }
    return policy_config


def main():
    with open(DESIGN_PATH) as f:
        design = json.load(f)

    policy_config = build_policy_config(design)

    # Wrap in the policy creation payload format expected by the API
    payload = {
        "config": policy_config,
        "created_by": "feature_outcome_analysis/build_policy_v3_default.py",
        "version": "3.0.0",
        "changelog_entry": (
            "Initial seed of Policy v3.0.0 with empirically-derived pillar "
            "system (Premium Leverage, Underlying Behavior, Setup Quality). "
            "Breakpoints from 15,505-position backtest analysis; composite "
            "correlation with outcomes r=+0.232 (vs r=+0.003 on v2.x). "
            "See scripts/output/feature_analysis_report.md for full details."
        ),
    }

    with open(POLICY_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {POLICY_PATH}")

    # Print summary
    pillars = policy_config["pillars"]
    print()
    print("Pillar weights:")
    for name, w in pillars["weights"].items():
        print(f"  {name}: {w}")
    print()
    for key in ["premium_leverage", "underlying_behavior", "setup_quality"]:
        p = pillars[key]
        print(f"{p['display_name']} ({p['pillar_id']}):")
        for s in p["numeric_subscores"]:
            print(f"  NUM  {s['weight']:.4f}  {s['display_name']} ({s['feature_field']})  "
                  f"{len(s['breakpoints'])} breakpoints  [{s['source_tier']}]")
        for s in p["categorical_subscores"]:
            print(f"  CAT  {s['weight']:.4f}  {s['display_name']} ({s['feature_field']})  "
                  f"{len(s['category_scores'])} categories")
        total = sum(s["weight"] for s in p["numeric_subscores"]) + sum(
            s["weight"] for s in p["categorical_subscores"]
        )
        print(f"  ----  total within-pillar weight: {total:.4f}")
        print()


if __name__ == "__main__":
    main()
