#!/usr/bin/env python3
"""Comprehensive feature-outcome analysis and pillar redesign.

Pulls all closed paper trading positions, enriches with FeatureValueTable data,
runs comprehensive statistical analysis, proposes new pillar system, and backtests it.

Usage:
    python scripts/feature_outcome_analysis.py --phase all
    python scripts/feature_outcome_analysis.py --phase data
    python scripts/feature_outcome_analysis.py --phase analyze
    python scripts/feature_outcome_analysis.py --phase design
    python scripts/feature_outcome_analysis.py --phase backtest

All stats computed with Python standard library (math, statistics). No numpy/scipy/pandas.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"
DATA_CACHE = OUTPUT_DIR / "feature_analysis_data.json"
ANALYSIS_CACHE = OUTPUT_DIR / "feature_analysis_results.json"
DESIGN_CACHE = OUTPUT_DIR / "feature_analysis_design.json"
REPORT_PATH = OUTPUT_DIR / "feature_analysis_report.md"

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")

# How many days back to look for FeatureValueTable data
FVT_LOOKBACK_DAYS = 90

# Minimum sample sizes
MIN_SAMPLE = 20
MIN_QUINTILE_SAMPLE = 10

# ---------------------------------------------------------------------------
# Tier 1 features: denormalized on PaperPosition (available for all positions)
# ---------------------------------------------------------------------------
TIER1_NUMERIC_FEATURES = [
    ("abs_entry_delta", "|Entry Delta|"),
    ("entry_delta", "Entry Delta (signed)"),
    ("entry_iv", "Entry IV"),
    ("entry_theta", "Entry Theta"),
    ("entry_iv_percentile", "IV Percentile"),
    ("entry_iv_rv_ratio", "IV/RV Ratio"),
    ("entry_theta_adjusted_edge", "Theta-Adj Edge"),
    ("entry_rv20", "Realized Vol 20d"),
    ("entry_underlying_price", "Underlying Price"),
    ("entry_moneyness_pct", "Moneyness %"),
    ("entry_spread_pct", "Spread %"),
    ("entry_open_interest", "Open Interest"),
    ("entry_volume", "Volume"),
    ("entry_days_to_earnings", "Days to Earnings"),
    ("entry_atr14_pct", "ATR % (14d)"),
    ("entry_rs_20d", "Rel Strength 20d"),
    ("entry_feasibility_ratio", "Feasibility Ratio"),
    ("pillar_directional", "Pillar: Directional"),
    ("pillar_volatility", "Pillar: Volatility"),
    ("pillar_structure", "Pillar: Structure"),
    ("conviction_score", "Conviction Score"),
    ("convergence_count", "Scanner Convergence"),
    ("gate_margin", "Gate Margin"),
    ("theta_adj_ev", "Theta-Adj EV"),
    ("dte_at_entry", "DTE at Entry"),
    ("entry_price", "Entry Price"),
    ("days_held", "Days Held"),
]

TIER1_CATEGORICAL_FEATURES = [
    "scanner_source",
    "option_type",
    "dte_bucket",
    "verdict_at_entry",
    "quality_tier_at_entry",
]

# ---------------------------------------------------------------------------
# Tier 2 features: from FeatureValueTable (recent positions only)
# ---------------------------------------------------------------------------
TIER2_NUMERIC_FEATURES = [
    # Technical indicators
    ("fvt_rsi_14", "RSI (14)"),
    ("fvt_adx_14", "ADX (14)"),
    ("fvt_plus_di", "+DI"),
    ("fvt_minus_di", "-DI"),
    ("fvt_macd_histogram", "MACD Histogram"),
    # Returns and RS
    ("fvt_return_5d", "Return 5d"),
    ("fvt_return_20d", "Return 20d"),
    ("fvt_spy_return_5d", "SPY Return 5d"),
    ("fvt_spy_return_20d", "SPY Return 20d"),
    ("fvt_rs_5d", "Rel Strength 5d"),
    ("fvt_rs_20d", "Rel Strength 20d"),
    # Volatility
    ("fvt_rv20", "Realized Vol 20d"),
    ("fvt_iv", "FVT Implied Vol"),
    ("fvt_iv_rv_ratio", "IV/RV Ratio"),
    ("fvt_iv_percentile", "IV Percentile"),
    ("fvt_iv_10d_change", "IV 10d Change"),
    # Contract
    ("fvt_theta_pct", "Theta %"),
    ("fvt_theta_adjusted_edge", "Theta-Adjusted Edge"),
    ("fvt_required_move_pct", "Required Move %"),
    ("fvt_expected_move_pct", "Expected Move %"),
    ("fvt_feasibility_ratio", "Feasibility Ratio"),
    ("fvt_time_adjusted_feasibility", "Time-Adj Feasibility"),
    # Liquidity
    ("fvt_open_interest", "Open Interest"),
    ("fvt_volume", "Volume"),
    ("fvt_oi_5d_change_pct", "OI 5d Change %"),
    ("fvt_spread_pct", "Spread %"),
    ("fvt_mid", "Option Mid Price"),
    # Underlying technicals
    ("fvt_close", "Underlying Close"),
    ("fvt_atr14_pct", "ATR % (14)"),
    # Catalyst
    ("fvt_days_to_earnings", "Days to Earnings"),
]

TIER2_CATEGORICAL_FEATURES = [
    "fvt_ema_alignment",
    "fvt_obv_trend",
    "fvt_iv_regime",
]

# FVT field name -> position dict key
FVT_FIELD_MAP = {
    # Technical indicators
    "rsi_14": "fvt_rsi_14",
    "adx_14": "fvt_adx_14",
    "plus_di": "fvt_plus_di",
    "minus_di": "fvt_minus_di",
    "macd_histogram": "fvt_macd_histogram",
    # Returns and relative strength
    "return_5d": "fvt_return_5d",
    "return_20d": "fvt_return_20d",
    "spy_return_5d": "fvt_spy_return_5d",
    "spy_return_20d": "fvt_spy_return_20d",
    "rs_5d": "fvt_rs_5d",
    "rs_20d": "fvt_rs_20d",
    # Volatility
    "rv20": "fvt_rv20",
    "iv": "fvt_iv",
    "iv_rv_ratio": "fvt_iv_rv_ratio",
    "iv_percentile": "fvt_iv_percentile",
    "iv_10d_change": "fvt_iv_10d_change",
    "iv_regime": "fvt_iv_regime",
    # Contract characteristics
    "theta_pct": "fvt_theta_pct",
    "theta_adjusted_edge": "fvt_theta_adjusted_edge",
    "breakeven_price": "fvt_breakeven_price",
    "required_move_pct": "fvt_required_move_pct",
    "expected_move_pct": "fvt_expected_move_pct",
    "feasibility_ratio": "fvt_feasibility_ratio",
    "time_adjusted_feasibility": "fvt_time_adjusted_feasibility",
    # Liquidity
    "open_interest": "fvt_open_interest",
    "volume": "fvt_volume",
    "oi_5d_change_pct": "fvt_oi_5d_change_pct",
    "spread_pct": "fvt_spread_pct",
    "mid": "fvt_mid",
    # Underlying technicals
    "close": "fvt_close",
    "atr14": "fvt_atr14",
    "atr14_pct": "fvt_atr14_pct",
    "sma20": "fvt_sma20",
    "sma50": "fvt_sma50",
    "ema_9": "fvt_ema_9",
    "ema_21": "fvt_ema_21",
    "ema_50": "fvt_ema_50",
    "ema_200": "fvt_ema_200",
    "ema_alignment": "fvt_ema_alignment",
    "obv_trend": "fvt_obv_trend",
    "trend_aligned_bullish": "fvt_trend_aligned_bullish",
    "trend_aligned_bearish": "fvt_trend_aligned_bearish",
    # Catalyst
    "days_to_earnings": "fvt_days_to_earnings",
    "recent_sec_filing": "fvt_recent_sec_filing",
}


# ============================================================================
# Statistical utilities (pure Python, no external deps)
# ============================================================================


def pearson_r(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    x_mean = math.fsum(xs) / n
    y_mean = math.fsum(ys) / n
    num = math.fsum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den_x = math.fsum((x - x_mean) ** 2 for x in xs)
    den_y = math.fsum((y - y_mean) ** 2 for y in ys)
    denom = math.sqrt(den_x * den_y)
    if denom < 1e-15:
        return 0.0
    return num / denom


def analytical_p_value(observed_r: float, n: int) -> float:
    if n < 3:
        return 1.0
    r_sq = observed_r**2
    if r_sq >= 1.0:
        return 0.0
    t = abs(observed_r) * math.sqrt(n - 2) / math.sqrt(1.0 - r_sq)
    p = math.erfc(t / math.sqrt(2.0))
    return min(max(p, 0.0), 1.0)


def cohens_d(group_a: list[float], group_b: list[float]) -> float:
    if len(group_a) < 2 or len(group_b) < 2:
        return 0.0
    mean_a = statistics.mean(group_a)
    mean_b = statistics.mean(group_b)
    var_a = statistics.variance(group_a)
    var_b = statistics.variance(group_b)
    pooled_var = ((len(group_a) - 1) * var_a + (len(group_b) - 1) * var_b) / (
        len(group_a) + len(group_b) - 2
    )
    if pooled_var < 1e-15:
        return 0.0
    return (mean_a - mean_b) / math.sqrt(pooled_var)


def safe_mean(vals: list[float]) -> Optional[float]:
    return statistics.mean(vals) if vals else None


def safe_median(vals: list[float]) -> float:
    return statistics.median(vals) if vals else 0.0


def win_rate(pnls: list[float]) -> float:
    if not pnls:
        return 0.0
    return (sum(1 for p in pnls if p > 0) / len(pnls)) * 100


def compute_quintiles(
    pairs: list[tuple[float, float]],
) -> list[dict]:
    if len(pairs) < MIN_QUINTILE_SAMPLE * 5:
        return []
    sorted_pairs = sorted(pairs, key=lambda p: p[0])
    n = len(sorted_pairs)
    bucket_size = n // 5
    quintiles = []
    for q in range(5):
        start = q * bucket_size
        end = start + bucket_size if q < 4 else n
        bucket = sorted_pairs[start:end]
        if not bucket:
            continue
        feat_vals = [p[0] for p in bucket]
        pnls = [p[1] for p in bucket]
        quintiles.append(
            {
                "quintile": q + 1,
                "range_low": round(min(feat_vals), 4),
                "range_high": round(max(feat_vals), 4),
                "n": len(bucket),
                "win_rate": round(win_rate(pnls), 1),
                "avg_return": round(statistics.mean(pnls), 2),
                "median_return": round(safe_median(pnls), 2),
            }
        )
    return quintiles


def is_monotonic(values: list[float], threshold: int = 4) -> str:
    """Check if a sequence is monotonically increasing or decreasing.
    Returns 'increasing', 'decreasing', or 'none'.
    threshold = minimum consecutive steps in one direction (out of 4 total).
    """
    if len(values) < 3:
        return "none"
    inc = sum(1 for i in range(1, len(values)) if values[i] > values[i - 1])
    dec = sum(1 for i in range(1, len(values)) if values[i] < values[i - 1])
    if inc >= threshold:
        return "increasing"
    if dec >= threshold:
        return "decreasing"
    return "none"


def chi_squared_p(observed: list[int], expected: list[float]) -> float:
    """Pure Python chi-squared goodness-of-fit p-value.
    Uses Wilson-Hilferty normal approximation for df > 5.
    """
    df = len(observed) - 1
    if df < 1:
        return 1.0
    chi2 = sum(
        (o - e) ** 2 / e for o, e in zip(observed, expected) if e > 0
    )
    if df <= 5:
        # Simple approximation for small df
        z = math.sqrt(2 * chi2) - math.sqrt(2 * df - 1)
        return min(max(math.erfc(z / math.sqrt(2)) / 2, 0.0), 1.0)
    # Wilson-Hilferty approximation
    k = df
    z = ((chi2 / k) ** (1 / 3) - (1 - 2 / (9 * k))) / math.sqrt(2 / (9 * k))
    p = math.erfc(z / math.sqrt(2)) / 2
    return min(max(p, 0.0), 1.0)


# ============================================================================
# Decimal/DynamoDB JSON helper
# ============================================================================


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            f = float(obj)
            if f == int(f):
                return int(f)
            return f
        return super().default(obj)


def decimal_to_python(obj: Any) -> Any:
    """Recursively convert Decimal values from DynamoDB to Python floats/ints."""
    if isinstance(obj, Decimal):
        f = float(obj)
        return int(f) if f == int(f) else f
    if isinstance(obj, dict):
        return {k: decimal_to_python(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decimal_to_python(i) for i in obj]
    return obj


# ============================================================================
# Phase 1: Data Collection
# ============================================================================


def collect_data() -> list[dict]:
    """Pull all closed positions from DynamoDB, enrich recent ones with FVT."""
    log.info("Phase 1: Collecting data from DynamoDB...")

    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions_table = dynamodb.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt_table = dynamodb.Table(f"{TABLE_PREFIX}-feature-values")

    # Step 1: Pull all closed positions
    log.info("Querying closed positions...")
    all_positions = []
    kwargs = {
        "KeyConditionExpression": Key("PK").eq("POS#CLOSED"),
    }
    while True:
        resp = positions_table.query(**kwargs)
        items = resp.get("Items", [])
        all_positions.extend(items)
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    log.info(f"Loaded {len(all_positions)} closed positions")

    # Convert Decimals and flatten
    positions = []
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=FVT_LOOKBACK_DAYS)).strftime(
        "%Y-%m-%d"
    )
    fvt_eligible_ids = []

    for item in all_positions:
        pos = decimal_to_python(item)
        # Remove DynamoDB keys
        pos.pop("PK", None)
        pos.pop("SK", None)
        pos.pop("GSI1PK", None)
        pos.pop("GSI1SK", None)
        pos.pop("GSI2PK", None)
        pos.pop("GSI2SK", None)
        pos["has_fvt_features"] = False
        positions.append(pos)

        entry_date = pos.get("entry_date", "")
        eval_id = pos.get("evaluation_id", "")
        if entry_date >= cutoff_date and eval_id:
            fvt_eligible_ids.append((len(positions) - 1, eval_id))

    log.info(
        f"FVT-eligible positions (last {FVT_LOOKBACK_DAYS} days): {len(fvt_eligible_ids)}"
    )

    # Step 2: Enrich with FeatureValueTable
    enriched = 0
    total = len(fvt_eligible_ids)
    for batch_idx, (pos_idx, eval_id) in enumerate(fvt_eligible_ids):
        if batch_idx % 200 == 0 and batch_idx > 0:
            log.info(f"  FVT enrichment: {batch_idx}/{total} ({enriched} enriched)")
            time.sleep(0.1)  # Small throttle

        try:
            resp = fvt_table.query(
                KeyConditionExpression=Key("PK").eq(f"EVAL#{eval_id}")
                & Key("SK").begins_with("FEATURE#"),
            )
            features = resp.get("Items", [])
            if features:
                for feat in features:
                    feat_name = feat.get("SK", "").replace("FEATURE#", "")
                    mapped_key = FVT_FIELD_MAP.get(feat_name)
                    if mapped_key:
                        val = decimal_to_python(feat.get("value"))
                        positions[pos_idx][mapped_key] = val
                positions[pos_idx]["has_fvt_features"] = True
                enriched += 1
        except Exception as e:
            if batch_idx < 3:
                log.warning(f"FVT query failed for {eval_id}: {e}")
            continue

    log.info(f"FVT enrichment complete: {enriched}/{total} positions enriched")

    # Save cache
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_CACHE, "w") as f:
        json.dump(positions, f, cls=DecimalEncoder)
    log.info(f"Data cached to {DATA_CACHE} ({len(positions)} positions)")

    return positions


# ============================================================================
# Phase 2: Statistical Analysis
# ============================================================================


def extract_numeric_pairs(
    positions: list[dict], feature_key: str, outcome_key: str = "current_pnl_pct"
) -> list[tuple[float, float]]:
    """Extract (feature, outcome) pairs, skipping None/missing."""
    pairs = []
    for pos in positions:
        fv = pos.get(feature_key)
        ov = pos.get(outcome_key)
        if fv is not None and ov is not None:
            try:
                pairs.append((float(fv), float(ov)))
            except (TypeError, ValueError):
                continue
    return pairs


def analyze_numeric_feature(
    positions: list[dict],
    feature_key: str,
    display_name: str,
    outcome_key: str = "current_pnl_pct",
) -> Optional[dict]:
    """Full statistical analysis for one numeric feature."""
    pairs = extract_numeric_pairs(positions, feature_key, outcome_key)
    if len(pairs) < MIN_SAMPLE:
        return None

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]

    r = pearson_r(xs, ys)
    p_val = analytical_p_value(r, len(xs))

    # Winners vs losers
    win_vals = [x for x, y in pairs if y > 0]
    loss_vals = [x for x, y in pairs if y <= 0]
    effect = cohens_d(win_vals, loss_vals) if len(win_vals) >= 2 and len(loss_vals) >= 2 else 0.0

    # Direction
    if r > 0.03:
        direction = "higher_is_better"
    elif r < -0.03:
        direction = "lower_is_better"
    else:
        direction = "unclear"

    quintiles = compute_quintiles(pairs)

    # Check monotonicity of quintile avg returns
    q_returns = [q["avg_return"] for q in quintiles]
    mono = is_monotonic(q_returns)

    return {
        "feature": feature_key,
        "display_name": display_name,
        "n": len(pairs),
        "pearson_r": round(r, 4),
        "p_value": round(p_val, 6),
        "is_significant": p_val < 0.05,
        "cohens_d": round(effect, 4),
        "abs_cohens_d": round(abs(effect), 4),
        "direction": direction,
        "win_mean": round(safe_mean(win_vals), 4) if win_vals else None,
        "loss_mean": round(safe_mean(loss_vals), 4) if loss_vals else None,
        "quintiles": quintiles,
        "monotonic": mono,
        "quintile_spread": round(q_returns[-1] - q_returns[0], 2) if len(q_returns) >= 2 else 0,
    }


def analyze_categorical_feature(
    positions: list[dict],
    feature_key: str,
    outcome_key: str = "current_pnl_pct",
) -> Optional[dict]:
    """Analyze a categorical feature: win rate + avg return per category."""
    groups: dict[str, list[float]] = defaultdict(list)
    for pos in positions:
        cat = pos.get(feature_key)
        ov = pos.get(outcome_key)
        if cat is not None and ov is not None:
            try:
                groups[str(cat)].append(float(ov))
            except (TypeError, ValueError):
                continue

    if len(groups) < 2:
        return None

    total_n = sum(len(v) for v in groups.values())
    if total_n < MIN_SAMPLE:
        return None

    overall_wr = sum(1 for vals in groups.values() for v in vals if v > 0) / total_n * 100

    categories = []
    for cat, pnls in sorted(groups.items(), key=lambda x: -len(x[1])):
        categories.append(
            {
                "category": cat,
                "n": len(pnls),
                "win_rate": round(win_rate(pnls), 1),
                "avg_return": round(statistics.mean(pnls), 2),
                "median_return": round(safe_median(pnls), 2),
            }
        )

    # Chi-squared: test if win rates differ across categories
    observed_wins = [sum(1 for v in groups[cat] if v > 0) for cat in groups]
    expected_wins = [len(groups[cat]) * overall_wr / 100 for cat in groups]
    p_val = chi_squared_p(observed_wins, expected_wins)

    return {
        "feature": feature_key,
        "n": total_n,
        "n_categories": len(groups),
        "chi_squared_p": round(p_val, 6),
        "is_significant": p_val < 0.05,
        "overall_win_rate": round(overall_wr, 1),
        "categories": categories,
    }


def analyze_interactions(
    positions: list[dict],
    top_features: list[dict],
    outcome_key: str = "current_pnl_pct",
    max_features: int = 15,
) -> list[dict]:
    """Test pairs of top features for synergistic effects."""
    sig = [f for f in top_features if f["is_significant"]][:max_features]
    if len(sig) < 2:
        return []

    # Precompute medians and value maps
    feature_data: dict[str, tuple[float, str, dict[int, float]]] = {}
    for feat in sig:
        pairs = extract_numeric_pairs(positions, feat["feature"], outcome_key)
        if len(pairs) < MIN_SAMPLE:
            continue
        vals = [p[0] for p in pairs]
        med = safe_median(vals)
        val_map = {}
        for idx, pos in enumerate(positions):
            fv = pos.get(feat["feature"])
            if fv is not None:
                try:
                    val_map[idx] = float(fv)
                except (TypeError, ValueError):
                    continue
        feature_data[feat["feature"]] = (med, feat["direction"], val_map)

    results = []
    feat_names = [f["feature"] for f in sig if f["feature"] in feature_data]
    display_map = {f["feature"]: f["display_name"] for f in sig}

    for i, fa in enumerate(feat_names):
        med_a, dir_a, vals_a = feature_data[fa]
        a_lower = dir_a == "lower_is_better"
        for fb in feat_names[i + 1 :]:
            med_b, dir_b, vals_b = feature_data[fb]
            b_lower = dir_b == "lower_is_better"

            both_fav, a_only, b_only, both_unfav = [], [], [], []

            for idx, pos in enumerate(positions):
                ov = pos.get(outcome_key)
                if idx not in vals_a or idx not in vals_b or ov is None:
                    continue
                pnl = float(ov)
                a_fav = vals_a[idx] < med_a if a_lower else vals_a[idx] >= med_a
                b_fav = vals_b[idx] < med_b if b_lower else vals_b[idx] >= med_b

                if a_fav and b_fav:
                    both_fav.append(pnl)
                elif a_fav:
                    a_only.append(pnl)
                elif b_fav:
                    b_only.append(pnl)
                else:
                    both_unfav.append(pnl)

            if len(both_fav) < 10 or len(a_only) < 10 or len(b_only) < 10:
                continue

            bf_wr = win_rate(both_fav)
            ao_wr = win_rate(a_only)
            bo_wr = win_rate(b_only)
            bu_wr = win_rate(both_unfav) if both_unfav else 0.0

            lift = bf_wr - max(ao_wr, bo_wr)

            results.append(
                {
                    "feature_a": fa,
                    "feature_a_display": display_map.get(fa, fa),
                    "feature_b": fb,
                    "feature_b_display": display_map.get(fb, fb),
                    "both_favorable_wr": round(bf_wr, 1),
                    "both_favorable_avg_return": round(statistics.mean(both_fav), 2),
                    "both_favorable_n": len(both_fav),
                    "a_only_wr": round(ao_wr, 1),
                    "a_only_n": len(a_only),
                    "b_only_wr": round(bo_wr, 1),
                    "b_only_n": len(b_only),
                    "both_unfavorable_wr": round(bu_wr, 1),
                    "both_unfavorable_n": len(both_unfav),
                    "synergy_lift": round(lift, 1),
                    "both_favorable_avg": round(statistics.mean(both_fav), 2),
                    "both_unfavorable_avg": round(
                        statistics.mean(both_unfav), 2
                    ) if both_unfav else 0,
                }
            )

    results.sort(key=lambda x: x["synergy_lift"], reverse=True)
    return results


def analyze_scanner_segments(
    positions: list[dict],
    all_features: list[dict],
    outcome_key: str = "current_pnl_pct",
) -> dict:
    """Per-scanner correlation analysis."""
    scanners = defaultdict(list)
    for pos in positions:
        sc = pos.get("scanner_source")
        if sc:
            scanners[str(sc)].append(pos)

    results = {}
    for scanner, scanner_positions in sorted(scanners.items()):
        if len(scanner_positions) < MIN_SAMPLE:
            continue

        pnls = [float(p.get("current_pnl_pct", 0)) for p in scanner_positions if p.get("current_pnl_pct") is not None]
        scanner_wr = win_rate(pnls) if pnls else 0
        scanner_avg = statistics.mean(pnls) if pnls else 0

        # Analyze top features for this scanner
        feat_results = []
        for feat in all_features:
            if not feat["is_significant"]:
                continue
            analysis = analyze_numeric_feature(
                scanner_positions, feat["feature"], feat["display_name"], outcome_key
            )
            if analysis and analysis["is_significant"]:
                feat_results.append(analysis)

        feat_results.sort(key=lambda x: x["abs_cohens_d"], reverse=True)

        results[scanner] = {
            "n": len(scanner_positions),
            "win_rate": round(scanner_wr, 1),
            "avg_return": round(scanner_avg, 2),
            "top_features": feat_results[:10],
        }

    return results


def analyze_temporal_stability(
    positions: list[dict],
    top_features: list[dict],
    window_days: int = 60,
    step_days: int = 14,
    outcome_key: str = "current_pnl_pct",
) -> dict:
    """Rolling window analysis of feature correlations over time."""
    # Sort positions by entry_date
    dated_positions = [(pos.get("entry_date", ""), pos) for pos in positions if pos.get("entry_date")]
    dated_positions.sort(key=lambda x: x[0])
    if not dated_positions:
        return {}

    first_date = dated_positions[0][0]
    last_date = dated_positions[-1][0]

    sig_features = [f for f in top_features if f["is_significant"]][:10]

    windows = []
    start = first_date
    while start <= last_date:
        end_date = (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=window_days)).strftime(
            "%Y-%m-%d"
        )
        window_positions = [pos for d, pos in dated_positions if start <= d < end_date]

        if len(window_positions) >= MIN_SAMPLE:
            feature_rs = {}
            for feat in sig_features:
                pairs = extract_numeric_pairs(window_positions, feat["feature"], outcome_key)
                if len(pairs) >= MIN_SAMPLE:
                    r = pearson_r([p[0] for p in pairs], [p[1] for p in pairs])
                    feature_rs[feat["feature"]] = round(r, 4)

            windows.append(
                {
                    "start": start,
                    "end": end_date,
                    "n": len(window_positions),
                    "correlations": feature_rs,
                }
            )

        start = (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=step_days)).strftime(
            "%Y-%m-%d"
        )

    # Compute stability scores: how often does the correlation maintain its sign?
    stability = {}
    for feat in sig_features:
        rs = [w["correlations"].get(feat["feature"]) for w in windows if feat["feature"] in w.get("correlations", {})]
        if len(rs) < 3:
            continue
        # Stability = fraction of windows with same sign as overall
        overall_sign = 1 if feat["pearson_r"] >= 0 else -1
        same_sign = sum(1 for r in rs if (r >= 0) == (overall_sign >= 0))
        stability[feat["feature"]] = {
            "display_name": feat["display_name"],
            "n_windows": len(rs),
            "same_sign_pct": round(same_sign / len(rs) * 100, 1),
            "r_range": [round(min(rs), 4), round(max(rs), 4)],
            "r_mean": round(statistics.mean(rs), 4),
        }

    return {"windows": windows, "stability": stability}


def run_analysis(positions: list[dict]) -> dict:
    """Run comprehensive statistical analysis."""
    log.info("Phase 2: Running analysis...")

    n_total = len(positions)
    fvt_positions = [p for p in positions if p.get("has_fvt_features")]
    n_fvt = len(fvt_positions)

    pnls = [float(p["current_pnl_pct"]) for p in positions if p.get("current_pnl_pct") is not None]
    overall_wr = win_rate(pnls)
    overall_avg = statistics.mean(pnls) if pnls else 0

    log.info(f"Dataset: {n_total} positions, {n_fvt} with FVT features")
    log.info(f"Overall: {overall_wr:.1f}% win rate, {overall_avg:.2f}% avg return")

    # --- 2A: Numeric feature correlations ---
    log.info("2A: Numeric feature correlations (Tier 1)...")
    tier1_results = []
    for feat_key, display in TIER1_NUMERIC_FEATURES:
        result = analyze_numeric_feature(positions, feat_key, display)
        if result:
            tier1_results.append(result)
    tier1_results.sort(key=lambda x: x["abs_cohens_d"], reverse=True)

    log.info("2A: Numeric feature correlations (Tier 2)...")
    tier2_results = []
    for feat_key, display in TIER2_NUMERIC_FEATURES:
        result = analyze_numeric_feature(fvt_positions, feat_key, display)
        if result:
            tier2_results.append(result)
    tier2_results.sort(key=lambda x: x["abs_cohens_d"], reverse=True)

    # --- 2A: Categorical features ---
    log.info("2A: Categorical features...")
    cat_results_t1 = []
    for feat_key in TIER1_CATEGORICAL_FEATURES:
        result = analyze_categorical_feature(positions, feat_key)
        if result:
            cat_results_t1.append(result)

    cat_results_t2 = []
    for feat_key in TIER2_CATEGORICAL_FEATURES:
        result = analyze_categorical_feature(fvt_positions, feat_key)
        if result:
            cat_results_t2.append(result)

    # --- 2B: Already done in numeric analysis (quintiles computed per feature) ---

    # --- 2C: Interaction analysis ---
    log.info("2C: Interaction analysis...")
    all_significant = sorted(
        [f for f in tier1_results + tier2_results if f["is_significant"]],
        key=lambda x: x["abs_cohens_d"],
        reverse=True,
    )
    # For interaction analysis, use the full dataset with tier1 features
    interactions = analyze_interactions(positions, tier1_results)

    # --- 2D: Scanner segments ---
    log.info("2D: Scanner segment analysis...")
    scanner_segments = analyze_scanner_segments(positions, tier1_results)

    # --- 2E: Temporal stability ---
    log.info("2E: Temporal stability analysis...")
    stability = analyze_temporal_stability(positions, tier1_results)

    results = {
        "summary": {
            "n_total": n_total,
            "n_fvt": n_fvt,
            "overall_win_rate": round(overall_wr, 1),
            "overall_avg_return": round(overall_avg, 2),
        },
        "tier1_numeric": tier1_results,
        "tier2_numeric": tier2_results,
        "tier1_categorical": cat_results_t1,
        "tier2_categorical": cat_results_t2,
        "interactions": interactions,
        "scanner_segments": scanner_segments,
        "temporal_stability": stability,
    }

    with open(ANALYSIS_CACHE, "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"Analysis cached to {ANALYSIS_CACHE}")

    return results


# ============================================================================
# Phase 3: Pillar Design
# ============================================================================


def design_pillars(analysis: dict) -> dict:
    """Algorithmically design new pillar system from analysis results."""
    log.info("Phase 3: Designing new pillar system...")

    tier1 = analysis["tier1_numeric"]
    tier2 = analysis["tier2_numeric"]

    # Step 1: Feature selection — significant with any meaningful effect
    # Use a very low threshold since options trading features have small effects
    MIN_EFFECT = 0.02
    selected = []
    for feat in tier1:
        if feat["is_significant"] and feat["abs_cohens_d"] >= MIN_EFFECT:
            selected.append(feat)

    # Also consider tier2 features (need higher threshold due to smaller samples)
    tier2_selected = []
    for feat in tier2:
        if feat["is_significant"] and feat["abs_cohens_d"] >= MIN_EFFECT:
            tier2_selected.append(feat)

    # Add categorical features as pseudo-numeric entries based on their effect
    for cat in analysis["tier1_categorical"] + analysis["tier2_categorical"]:
        if not cat["is_significant"]:
            continue
        # Compute effect size as max category WR spread
        wrs = [c["win_rate"] for c in cat["categories"] if c["n"] >= 30]
        avgs = [c["avg_return"] for c in cat["categories"] if c["n"] >= 30]
        if len(wrs) >= 2:
            wr_spread = max(wrs) - min(wrs)
            avg_spread = max(avgs) - min(avgs)
            # Create a pseudo effect size from the WR spread
            pseudo_d = wr_spread / 50  # normalize: 50pp spread -> d=1.0
            if pseudo_d >= MIN_EFFECT:
                selected.append({
                    "feature": cat["feature"],
                    "display_name": cat["feature"].replace("_", " ").title(),
                    "n": cat["n"],
                    "pearson_r": 0,  # not applicable for categorical
                    "p_value": cat["chi_squared_p"],
                    "is_significant": True,
                    "cohens_d": pseudo_d,
                    "abs_cohens_d": pseudo_d,
                    "direction": "categorical",
                    "win_mean": None,
                    "loss_mean": None,
                    "quintiles": [],
                    "monotonic": "categorical",
                    "quintile_spread": avg_spread,
                    "is_categorical": True,
                    "categories": cat["categories"],
                })

    log.info(f"Selected features: {len(selected)} (incl. categorical), {len(tier2_selected)} Tier 2")

    # Remove meta-features (pillar scores, conviction score) and outcome features
    meta_features = {
        "pillar_directional", "pillar_volatility", "pillar_structure",
        "conviction_score", "days_held",
    }
    selected = [f for f in selected if f["feature"] not in meta_features]

    # Step 2: Semantic pillar grouping
    # Group features into logical pillars. Weights within and between pillars
    # are derived from empirical effect sizes (|Cohen's d|).
    with open(DATA_CACHE) as f:
        positions = json.load(f)

    # Build a lookup for quick access — include both tier1 selected and tier2 selected
    feat_lookup = {f["feature"]: f for f in selected}
    for f in tier2_selected:
        if f["feature"] not in feat_lookup:
            feat_lookup[f["feature"]] = f

    # Define semantic pillar groups
    PILLAR_GROUPS = {
        "Contract Quality": {
            "features": [
                "entry_delta", "option_type", "entry_iv", "entry_price",
                "dte_at_entry", "entry_spread_pct", "entry_moneyness_pct",
                "dte_bucket",
            ],
            "description": "Contract characteristics that predict outcomes",
        },
        "Signal Quality": {
            "features": [
                "scanner_source", "verdict_at_entry", "convergence_count",
                "gate_margin",
            ],
            "description": "Signal source and system conviction indicators",
        },
        "Market Context": {
            "features": [
                "fvt_iv_regime", "fvt_feasibility_ratio",
                "fvt_time_adjusted_feasibility", "entry_feasibility_ratio",
                "fvt_return_5d", "fvt_spy_return_5d", "fvt_rs_5d",
                "fvt_iv_10d_change",
            ],
            "description": "Market environment and move feasibility at entry",
        },
        "Volatility Edge": {
            "features": [
                "entry_iv_percentile", "entry_iv_rv_ratio",
                "entry_theta_adjusted_edge", "entry_rv20", "theta_adj_ev",
            ],
            "description": "Whether implied vol is cheap relative to realized",
        },
    }

    pillars = []
    total_importance = 0

    for pillar_name, group_def in PILLAR_GROUPS.items():
        cluster = []
        for feat_key in group_def["features"]:
            if feat_key in feat_lookup:
                cluster.append(feat_lookup[feat_key])

        if not cluster:
            continue

        importance = sum(f["abs_cohens_d"] for f in cluster)
        total_importance += importance

        pillars.append({
            "_cluster": cluster,
            "_importance": importance,
            "name": pillar_name,
            "description": group_def["description"],
        })

    # Compute pillar weights proportional to total feature importance
    for pillar in pillars:
        importance = pillar["_importance"]
        cluster = pillar["_cluster"]
        pillar_weight = importance / total_importance if total_importance > 0 else 0

        # Subscore weights within pillar (proportional to |Cohen's d|)
        subscores = []
        cluster_importance = sum(f["abs_cohens_d"] for f in cluster)
        for feat in sorted(cluster, key=lambda f: f["abs_cohens_d"], reverse=True):
            sub_weight = feat["abs_cohens_d"] / cluster_importance if cluster_importance > 0 else 0

            if feat.get("is_categorical"):
                # Build category -> score map based on avg return ranking
                cats = [c for c in feat.get("categories", []) if c["n"] >= 20]
                if cats:
                    sorted_cats = sorted(cats, key=lambda c: c["avg_return"])
                    n_cats = len(sorted_cats)
                    cat_score_map = {}
                    for ci, cat in enumerate(sorted_cats):
                        # Spread scores from 10 to 90 across categories
                        score = 10 + (80 * ci / max(n_cats - 1, 1))
                        cat_score_map[cat["category"]] = round(score, 1)

                    subscores.append({
                        "feature": feat["feature"],
                        "display_name": feat["display_name"],
                        "weight": round(sub_weight, 3),
                        "direction": "categorical",
                        "pearson_r": 0,
                        "cohens_d": feat["cohens_d"],
                        "breakpoints": [],
                        "monotonic": "categorical",
                        "tier": "tier1",
                        "is_categorical": True,
                        "category_score_map": cat_score_map,
                    })
                continue

            # Build breakpoint map from quintiles
            breakpoints = []
            if feat["quintiles"]:
                for q in feat["quintiles"]:
                    # Map quintile midpoint to score
                    mid_val = (q["range_low"] + q["range_high"]) / 2
                    # Q1 = worst performing (or best, depending on direction)
                    score = q["quintile"] * 20  # 20, 40, 60, 80, 100
                    if feat["direction"] == "lower_is_better":
                        score = (6 - q["quintile"]) * 20  # 100, 80, 60, 40, 20
                    breakpoints.append({
                        "value": round(mid_val, 4),
                        "score": score,
                        "q_win_rate": q["win_rate"],
                        "q_avg_return": q["avg_return"],
                    })

            subscores.append({
                "feature": feat["feature"],
                "display_name": feat["display_name"],
                "weight": round(sub_weight, 3),
                "direction": feat["direction"],
                "pearson_r": feat["pearson_r"],
                "cohens_d": feat["cohens_d"],
                "breakpoints": breakpoints,
                "monotonic": feat["monotonic"],
                "tier": "tier1" if not feat["feature"].startswith("fvt_") else "tier2",
            })

        pillar["weight"] = round(pillar_weight, 3)
        pillar["importance"] = round(importance, 4)
        pillar["n_subscores"] = len(subscores)
        pillar["subscores"] = subscores
        # Clean up temp keys
        pillar.pop("_cluster", None)
        pillar.pop("_importance", None)

    # Sort pillars by weight descending
    pillars.sort(key=lambda p: p["weight"], reverse=True)

    # Limit to top 4 pillars, redistribute weight
    if len(pillars) > 4:
        # Merge smallest pillars into "Other"
        main_pillars = pillars[:4]
        total_w = sum(p["weight"] for p in main_pillars)
        for p in main_pillars:
            p["weight"] = round(p["weight"] / total_w, 3)
        pillars = main_pillars

    # Also include tier2 recommendations
    tier2_recommendations = []
    for feat in tier2_selected:
        tier2_recommendations.append({
            "feature": feat["feature"],
            "display_name": feat["display_name"],
            "cohens_d": feat["cohens_d"],
            "pearson_r": feat["pearson_r"],
            "recommendation": "Denormalize onto PaperPosition for full-history analysis",
        })

    design = {
        "pillars": pillars,
        "tier2_recommendations": tier2_recommendations,
        "total_features_selected": len(selected),
        "total_tier2_promising": len(tier2_selected),
    }

    with open(DESIGN_CACHE, "w") as f:
        json.dump(design, f, indent=2)
    log.info(f"Pillar design cached to {DESIGN_CACHE}")

    return design


# ============================================================================
# Phase 4: Backtest
# ============================================================================


def score_position(pos: dict, design: dict) -> dict:
    """Score a single position using the new pillar system."""
    pillar_scores = {}
    pillar_weights = {}
    available_weight = 0

    for pillar in design["pillars"]:
        subscore_total = 0
        subscore_weight_total = 0

        for sub in pillar["subscores"]:
            # Handle categorical features
            if sub.get("is_categorical"):
                cat_map = sub.get("category_score_map", {})
                fval = pos.get(sub["feature"])
                if fval is None:
                    continue
                score = cat_map.get(str(fval), 50)  # default 50 for unknown categories
                score = max(0, min(100, score))
                subscore_total += score * sub["weight"]
                subscore_weight_total += sub["weight"]
                continue

            fval = pos.get(sub["feature"])
            if fval is None:
                continue
            try:
                fval = float(fval)
            except (TypeError, ValueError):
                continue

            # Score using breakpoints (linear interpolation)
            bps = sub["breakpoints"]
            if not bps:
                continue

            # Sort breakpoints by value
            sorted_bps = sorted(bps, key=lambda b: b["value"])
            score = 50  # default

            if fval <= sorted_bps[0]["value"]:
                score = sorted_bps[0]["score"]
            elif fval >= sorted_bps[-1]["value"]:
                score = sorted_bps[-1]["score"]
            else:
                for j in range(len(sorted_bps) - 1):
                    if sorted_bps[j]["value"] <= fval <= sorted_bps[j + 1]["value"]:
                        v0, s0 = sorted_bps[j]["value"], sorted_bps[j]["score"]
                        v1, s1 = sorted_bps[j + 1]["value"], sorted_bps[j + 1]["score"]
                        if abs(v1 - v0) < 1e-15:
                            score = s0
                        else:
                            t = (fval - v0) / (v1 - v0)
                            score = s0 + t * (s1 - s0)
                        break

            score = max(0, min(100, score))
            subscore_total += score * sub["weight"]
            subscore_weight_total += sub["weight"]

        if subscore_weight_total > 0:
            pillar_score = subscore_total / subscore_weight_total
            pillar_scores[pillar["name"]] = round(pillar_score, 2)
            pillar_weights[pillar["name"]] = pillar["weight"]
            available_weight += pillar["weight"]

    # Composite score (weighted average with weight redistribution)
    composite = 0
    if available_weight > 0:
        for name, score in pillar_scores.items():
            composite += score * (pillar_weights[name] / available_weight)

    return {
        "pillar_scores": pillar_scores,
        "composite": round(composite, 2),
    }


def run_backtest(positions: list[dict], design: dict) -> dict:
    """Backtest the new pillar system on all positions."""
    log.info("Phase 4: Backtesting new pillar system...")

    scored = []
    for pos in positions:
        result = score_position(pos, design)
        result["current_pnl_pct"] = pos.get("current_pnl_pct")
        result["scanner_source"] = pos.get("scanner_source")
        # Preserve old scores for comparison
        result["old_pillar_directional"] = pos.get("pillar_directional")
        result["old_pillar_volatility"] = pos.get("pillar_volatility")
        result["old_pillar_structure"] = pos.get("pillar_structure")
        result["old_conviction_score"] = pos.get("conviction_score")
        scored.append(result)

    # Filter to positions with valid outcome
    valid = [s for s in scored if s["current_pnl_pct"] is not None]
    log.info(f"Scored {len(valid)} positions")

    # Correlation of new composite with outcome
    composites = [s["composite"] for s in valid]
    pnls = [float(s["current_pnl_pct"]) for s in valid]
    new_composite_r = pearson_r(composites, pnls)
    new_composite_p = analytical_p_value(new_composite_r, len(composites))

    # Correlations per new pillar
    new_pillar_correlations = {}
    for pillar in design["pillars"]:
        pname = pillar["name"]
        pairs = [
            (s["pillar_scores"].get(pname), float(s["current_pnl_pct"]))
            for s in valid
            if pname in s["pillar_scores"] and s["current_pnl_pct"] is not None
        ]
        if len(pairs) >= MIN_SAMPLE:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            r = pearson_r(xs, ys)
            new_pillar_correlations[pname] = {
                "pearson_r": round(r, 4),
                "p_value": round(analytical_p_value(r, len(xs)), 6),
                "n": len(pairs),
            }

    # Old pillar correlations
    old_pillar_correlations = {}
    for old_name, old_key in [
        ("Directional", "old_pillar_directional"),
        ("Volatility", "old_pillar_volatility"),
        ("Structure", "old_pillar_structure"),
        ("Conviction", "old_conviction_score"),
    ]:
        pairs = [
            (float(s[old_key]), float(s["current_pnl_pct"]))
            for s in valid
            if s.get(old_key) is not None and s["current_pnl_pct"] is not None
        ]
        if len(pairs) >= MIN_SAMPLE:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            r = pearson_r(xs, ys)
            old_pillar_correlations[old_name] = {
                "pearson_r": round(r, 4),
                "p_value": round(analytical_p_value(r, len(xs)), 6),
                "n": len(pairs),
            }

    # Quintile analysis of new composite
    composite_pairs = [(s["composite"], float(s["current_pnl_pct"])) for s in valid]
    composite_quintiles = compute_quintiles(composite_pairs)

    # Per-pillar quintile analysis
    pillar_quintiles = {}
    for pillar in design["pillars"]:
        pname = pillar["name"]
        pairs = [
            (s["pillar_scores"].get(pname, 0), float(s["current_pnl_pct"]))
            for s in valid
            if pname in s["pillar_scores"] and s["current_pnl_pct"] is not None
        ]
        pq = compute_quintiles(pairs)
        if pq:
            pillar_quintiles[pname] = pq

    # Top/bottom decile analysis
    sorted_by_composite = sorted(valid, key=lambda s: s["composite"])
    n = len(sorted_by_composite)
    decile_size = n // 10

    bottom_decile = sorted_by_composite[:decile_size]
    top_decile = sorted_by_composite[-decile_size:]

    bottom_pnls = [float(s["current_pnl_pct"]) for s in bottom_decile]
    top_pnls = [float(s["current_pnl_pct"]) for s in top_decile]

    decile_analysis = {
        "top_10pct": {
            "n": len(top_decile),
            "win_rate": round(win_rate(top_pnls), 1),
            "avg_return": round(statistics.mean(top_pnls), 2),
            "median_return": round(safe_median(top_pnls), 2),
            "avg_composite": round(statistics.mean([s["composite"] for s in top_decile]), 1),
        },
        "bottom_10pct": {
            "n": len(bottom_decile),
            "win_rate": round(win_rate(bottom_pnls), 1),
            "avg_return": round(statistics.mean(bottom_pnls), 2),
            "median_return": round(safe_median(bottom_pnls), 2),
            "avg_composite": round(statistics.mean([s["composite"] for s in bottom_decile]), 1),
        },
    }

    # Scanner-specific comparison
    scanner_comparison = {}
    scanner_groups = defaultdict(list)
    for s in valid:
        sc = s.get("scanner_source")
        if sc:
            scanner_groups[sc].append(s)

    for scanner, group in scanner_groups.items():
        if len(group) < MIN_SAMPLE:
            continue
        new_pairs = [(s["composite"], float(s["current_pnl_pct"])) for s in group]
        old_pairs = [
            (float(s["old_conviction_score"]), float(s["current_pnl_pct"]))
            for s in group
            if s.get("old_conviction_score") is not None
        ]

        new_r = pearson_r([p[0] for p in new_pairs], [p[1] for p in new_pairs])
        old_r = (
            pearson_r([p[0] for p in old_pairs], [p[1] for p in old_pairs])
            if len(old_pairs) >= MIN_SAMPLE
            else 0
        )

        scanner_comparison[scanner] = {
            "n": len(group),
            "new_composite_r": round(new_r, 4),
            "old_conviction_r": round(old_r, 4),
            "improvement": round(abs(new_r) - abs(old_r), 4),
        }

    backtest = {
        "new_composite": {
            "pearson_r": round(new_composite_r, 4),
            "p_value": round(new_composite_p, 6),
            "n": len(valid),
        },
        "new_pillar_correlations": new_pillar_correlations,
        "old_pillar_correlations": old_pillar_correlations,
        "composite_quintiles": composite_quintiles,
        "pillar_quintiles": pillar_quintiles,
        "decile_analysis": decile_analysis,
        "scanner_comparison": scanner_comparison,
    }

    return backtest


# ============================================================================
# Report Generation
# ============================================================================


def generate_report(analysis: dict, design: dict, backtest: dict) -> str:
    """Generate comprehensive markdown report."""
    lines = []

    def add(text: str = ""):
        lines.append(text)

    summary = analysis["summary"]
    nc = backtest["new_composite"]

    add("# Feature-Outcome Analysis & Pillar Redesign Report")
    add(f"\nGenerated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    add(f"Dataset: {summary['n_total']:,} closed positions, {summary['n_fvt']:,} with full feature data")
    add()

    # Executive Summary
    add("## Executive Summary")
    add()
    add(f"- **Overall**: {summary['overall_win_rate']}% win rate, {summary['overall_avg_return']}% avg return")

    old = backtest["old_pillar_correlations"]
    add(f"- **Old pillar correlations**: "
        f"Dir r={old.get('Directional', {}).get('pearson_r', 'N/A')}, "
        f"Vol r={old.get('Volatility', {}).get('pearson_r', 'N/A')}, "
        f"Str r={old.get('Structure', {}).get('pearson_r', 'N/A')}, "
        f"Conviction r={old.get('Conviction', {}).get('pearson_r', 'N/A')}")

    add(f"- **New composite correlation**: r={nc['pearson_r']} (p={nc['p_value']})")

    if backtest["composite_quintiles"]:
        q1 = backtest["composite_quintiles"][0]
        q5 = backtest["composite_quintiles"][-1]
        add(f"- **New composite Q1 (lowest scores)**: {q1['win_rate']}% WR, {q1['avg_return']}% avg return")
        add(f"- **New composite Q5 (highest scores)**: {q5['win_rate']}% WR, {q5['avg_return']}% avg return")
        add(f"- **Quintile spread**: {round(q5['avg_return'] - q1['avg_return'], 2)} pp")

    da = backtest["decile_analysis"]
    add(f"- **Top 10% by new score**: {da['top_10pct']['win_rate']}% WR, {da['top_10pct']['avg_return']}% avg return")
    add(f"- **Bottom 10% by new score**: {da['bottom_10pct']['win_rate']}% WR, {da['bottom_10pct']['avg_return']}% avg return")

    # Count significant features
    sig_t1 = [f for f in analysis["tier1_numeric"] if f["is_significant"]]
    sig_t2 = [f for f in analysis["tier2_numeric"] if f["is_significant"]]
    add(f"- **Significant predictors**: {len(sig_t1)} Tier 1, {len(sig_t2)} Tier 2")
    add()

    # ===== Section 1: Feature Correlation Rankings =====
    add("---")
    add("## 1. Feature Correlation Rankings")
    add()

    add("### 1a. Tier 1 — All Positions (Numeric)")
    add()
    add("| Rank | Feature | n | r | p-value | Cohen's d | Direction | Monotonic | Q Spread |")
    add("|------|---------|---|---|---------|-----------|-----------|-----------|----------|")

    for i, f in enumerate(analysis["tier1_numeric"]):
        sig = "**" if f["is_significant"] else ""
        add(
            f"| {i+1} | {sig}{f['display_name']}{sig} | {f['n']:,} | {f['pearson_r']:.4f} | "
            f"{f['p_value']:.4f} | {f['cohens_d']:.4f} | {f['direction']} | "
            f"{f['monotonic']} | {f['quintile_spread']:.1f}pp |"
        )

    add()
    add("### 1b. Tier 2 — Recent Positions with Full Features (Numeric)")
    add()
    if analysis["tier2_numeric"]:
        add("| Rank | Feature | n | r | p-value | Cohen's d | Direction | Monotonic | Q Spread |")
        add("|------|---------|---|---|---------|-----------|-----------|-----------|----------|")
        for i, f in enumerate(analysis["tier2_numeric"]):
            sig = "**" if f["is_significant"] else ""
            add(
                f"| {i+1} | {sig}{f['display_name']}{sig} | {f['n']:,} | {f['pearson_r']:.4f} | "
                f"{f['p_value']:.4f} | {f['cohens_d']:.4f} | {f['direction']} | "
                f"{f['monotonic']} | {f['quintile_spread']:.1f}pp |"
            )
    else:
        add("*No Tier 2 features available (no recent positions with FVT data)*")

    add()

    # ===== Section 2: Quintile Analysis =====
    add("---")
    add("## 2. Quintile Analysis (Significant Features)")
    add()

    sig_features = [f for f in analysis["tier1_numeric"] if f["is_significant"] and f["quintiles"]]
    for feat in sig_features[:15]:  # Top 15
        add(f"### {feat['display_name']} (r={feat['pearson_r']:.4f}, d={feat['cohens_d']:.4f}, {feat['monotonic']})")
        add()
        add("| Quintile | Range | n | Win Rate | Avg Return | Median Return |")
        add("|----------|-------|---|----------|------------|---------------|")
        for q in feat["quintiles"]:
            add(
                f"| Q{q['quintile']} | {q['range_low']:.4f} — {q['range_high']:.4f} | "
                f"{q['n']} | {q['win_rate']:.1f}% | {q['avg_return']:.2f}% | {q['median_return']:.2f}% |"
            )
        add()

    # Tier 2 quintiles
    sig_t2_feats = [f for f in analysis["tier2_numeric"] if f["is_significant"] and f["quintiles"]]
    if sig_t2_feats:
        add("### Tier 2 Quintiles (Recent Positions)")
        add()
        for feat in sig_t2_feats[:10]:
            add(f"### {feat['display_name']} (r={feat['pearson_r']:.4f}, d={feat['cohens_d']:.4f}, {feat['monotonic']})")
            add()
            add("| Quintile | Range | n | Win Rate | Avg Return | Median Return |")
            add("|----------|-------|---|----------|------------|---------------|")
            for q in feat["quintiles"]:
                add(
                    f"| Q{q['quintile']} | {q['range_low']:.4f} — {q['range_high']:.4f} | "
                    f"{q['n']} | {q['win_rate']:.1f}% | {q['avg_return']:.2f}% | {q['median_return']:.2f}% |"
                )
            add()

    # ===== Section 3: Categorical Feature Breakdown =====
    add("---")
    add("## 3. Categorical Feature Breakdown")
    add()

    for cat in analysis["tier1_categorical"] + analysis["tier2_categorical"]:
        sig = " *" if cat["is_significant"] else ""
        add(f"### {cat['feature']}{sig}")
        add(f"Chi-squared p={cat['chi_squared_p']:.4f}, n={cat['n']:,}")
        add()
        add("| Category | n | Win Rate | Avg Return | Median Return |")
        add("|----------|---|----------|------------|---------------|")
        for c in cat["categories"]:
            add(
                f"| {c['category']} | {c['n']:,} | {c['win_rate']:.1f}% | "
                f"{c['avg_return']:.2f}% | {c['median_return']:.2f}% |"
            )
        add()

    # ===== Section 4: Feature Interactions =====
    add("---")
    add("## 4. Feature Interactions (Top 20 Pairs)")
    add()

    if analysis["interactions"]:
        add("| Feature A | Feature B | Both Fav WR | Both Fav Avg | A Only WR | B Only WR | Both Unfav WR | Synergy Lift |")
        add("|-----------|-----------|-------------|--------------|-----------|-----------|---------------|--------------|")
        for pair in analysis["interactions"][:20]:
            add(
                f"| {pair['feature_a_display']} | {pair['feature_b_display']} | "
                f"{pair['both_favorable_wr']:.1f}% | {pair['both_favorable_avg_return']:.2f}% | "
                f"{pair['a_only_wr']:.1f}% | {pair['b_only_wr']:.1f}% | "
                f"{pair['both_unfavorable_wr']:.1f}% | {pair['synergy_lift']:+.1f}pp |"
            )
    else:
        add("*Insufficient data for interaction analysis*")
    add()

    # ===== Section 5: Scanner Segment Analysis =====
    add("---")
    add("## 5. Scanner Segment Analysis")
    add()

    for scanner, data in analysis["scanner_segments"].items():
        add(f"### {scanner} (n={data['n']:,}, WR={data['win_rate']:.1f}%, Avg={data['avg_return']:.2f}%)")
        add()
        if data["top_features"]:
            add("| Feature | r | Cohen's d | Direction |")
            add("|---------|---|-----------|-----------|")
            for feat in data["top_features"][:5]:
                add(f"| {feat['display_name']} | {feat['pearson_r']:.4f} | {feat['cohens_d']:.4f} | {feat['direction']} |")
        add()

    # ===== Section 6: Temporal Stability =====
    add("---")
    add("## 6. Temporal Stability")
    add()

    stability = analysis.get("temporal_stability", {}).get("stability", {})
    if stability:
        add("| Feature | Windows | Same-Sign % | r Range | r Mean |")
        add("|---------|---------|-------------|---------|--------|")
        for feat_key, data in sorted(stability.items(), key=lambda x: -x[1]["same_sign_pct"]):
            add(
                f"| {data['display_name']} | {data['n_windows']} | "
                f"{data['same_sign_pct']:.1f}% | [{data['r_range'][0]:.4f}, {data['r_range'][1]:.4f}] | "
                f"{data['r_mean']:.4f} |"
            )
    add()

    # ===== Section 7: Proposed New Pillar System =====
    add("---")
    add("## 7. Proposed New Pillar System")
    add()

    for pillar in design["pillars"]:
        add(f"### {pillar['name']} (Weight: {pillar['weight']:.1%})")
        add()
        add(f"Importance: {pillar['importance']:.4f}, Subscores: {pillar['n_subscores']}")
        add()
        add("| Subscore | Feature | Weight | r | Cohen's d | Direction | Monotonic | Tier |")
        add("|----------|---------|--------|---|-----------|-----------|-----------|------|")
        for sub in pillar["subscores"]:
            add(
                f"| {sub['display_name']} | `{sub['feature']}` | {sub['weight']:.1%} | "
                f"{sub['pearson_r']:.4f} | {sub['cohens_d']:.4f} | {sub['direction']} | "
                f"{sub['monotonic']} | {sub['tier']} |"
            )
        add()

        # Breakpoints for top subscores
        for sub in pillar["subscores"][:3]:
            if sub["breakpoints"]:
                add(f"**{sub['display_name']} breakpoints:**")
                add()
                add("| Value | Score | Quintile WR | Quintile Avg Return |")
                add("|-------|-------|-------------|---------------------|")
                for bp in sub["breakpoints"]:
                    add(f"| {bp['value']:.4f} | {bp['score']} | {bp['q_win_rate']:.1f}% | {bp['q_avg_return']:.2f}% |")
                add()

    add("### Composite Score Formula")
    add()
    add("```")
    parts = [f"{p['weight']:.2f} x {p['name']}" for p in design["pillars"]]
    add(f"composite = {' + '.join(parts)}")
    add("```")
    add()

    if design["tier2_recommendations"]:
        add("### Tier 2 Feature Recommendations (Denormalize for Full History)")
        add()
        add("| Feature | Cohen's d | r | Recommendation |")
        add("|---------|-----------|---|----------------|")
        for rec in design["tier2_recommendations"]:
            add(f"| {rec['display_name']} | {rec['cohens_d']:.4f} | {rec['pearson_r']:.4f} | {rec['recommendation']} |")
        add()

    # ===== Section 8: Backtest Comparison =====
    add("---")
    add("## 8. Backtest Comparison")
    add()

    add("### Old vs New Pillar Correlations")
    add()
    add("| Metric | Old r | New r | Improvement |")
    add("|--------|-------|-------|-------------|")

    old_corrs = backtest["old_pillar_correlations"]
    new_corrs = backtest["new_pillar_correlations"]

    for old_name, old_data in old_corrs.items():
        add(f"| Old {old_name} | {old_data['pearson_r']:.4f} | — | — |")

    for new_name, new_data in new_corrs.items():
        add(f"| New {new_name} | — | {new_data['pearson_r']:.4f} | — |")

    old_conv_r = old_corrs.get("Conviction", {}).get("pearson_r", 0)
    add(f"| **Old Conviction (composite)** | **{old_conv_r}** | — | — |")
    add(f"| **New Composite** | — | **{nc['pearson_r']}** | **{abs(nc['pearson_r']) - abs(old_conv_r):+.4f}** |")
    add()

    add("### New Composite Quintile Performance")
    add()
    add("| Quintile | Range | n | Win Rate | Avg Return | Median Return |")
    add("|----------|-------|---|----------|------------|---------------|")
    for q in backtest["composite_quintiles"]:
        add(
            f"| Q{q['quintile']} | {q['range_low']:.1f} — {q['range_high']:.1f} | "
            f"{q['n']} | {q['win_rate']:.1f}% | {q['avg_return']:.2f}% | {q['median_return']:.2f}% |"
        )
    add()

    # Per-pillar quintiles
    for pname, pq in backtest.get("pillar_quintiles", {}).items():
        add(f"### {pname} Quintile Performance")
        add()
        add("| Quintile | Range | n | Win Rate | Avg Return |")
        add("|----------|-------|---|----------|------------|")
        for q in pq:
            add(f"| Q{q['quintile']} | {q['range_low']:.1f} — {q['range_high']:.1f} | {q['n']} | {q['win_rate']:.1f}% | {q['avg_return']:.2f}% |")
        add()

    add("### Top/Bottom Decile Analysis")
    add()
    add("| Segment | n | Win Rate | Avg Return | Median Return | Avg Composite |")
    add("|---------|---|----------|------------|---------------|---------------|")
    for seg_name, seg_data in da.items():
        add(
            f"| {seg_name} | {seg_data['n']} | {seg_data['win_rate']:.1f}% | "
            f"{seg_data['avg_return']:.2f}% | {seg_data['median_return']:.2f}% | "
            f"{seg_data['avg_composite']:.1f} |"
        )
    add()

    add("### Scanner-Specific Comparison")
    add()
    if backtest["scanner_comparison"]:
        add("| Scanner | n | Old Conviction r | New Composite r | Improvement |")
        add("|---------|---|------------------|-----------------|-------------|")
        for scanner, data in sorted(backtest["scanner_comparison"].items()):
            add(
                f"| {scanner} | {data['n']:,} | {data['old_conviction_r']:.4f} | "
                f"{data['new_composite_r']:.4f} | {data['improvement']:+.4f} |"
            )
    add()

    return "\n".join(lines)


# ============================================================================
# Main
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="Feature-outcome analysis and pillar redesign")
    parser.add_argument(
        "--phase",
        choices=["data", "analyze", "design", "backtest", "all"],
        default="all",
        help="Which phase to run",
    )
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    phases = (
        ["data", "analyze", "design", "backtest"]
        if args.phase == "all"
        else [args.phase]
    )

    positions = None
    analysis = None
    design = None
    backtest_results = None

    for phase in phases:
        if phase == "data":
            positions = collect_data()

        elif phase == "analyze":
            if positions is None:
                if DATA_CACHE.exists():
                    log.info("Loading cached position data...")
                    with open(DATA_CACHE) as f:
                        positions = json.load(f)
                else:
                    log.error("No data cache found. Run --phase data first.")
                    sys.exit(1)
            analysis = run_analysis(positions)

        elif phase == "design":
            if analysis is None:
                if ANALYSIS_CACHE.exists():
                    log.info("Loading cached analysis...")
                    with open(ANALYSIS_CACHE) as f:
                        analysis = json.load(f)
                else:
                    log.error("No analysis cache found. Run --phase analyze first.")
                    sys.exit(1)
            design = design_pillars(analysis)

        elif phase == "backtest":
            if positions is None:
                if DATA_CACHE.exists():
                    log.info("Loading cached position data...")
                    with open(DATA_CACHE) as f:
                        positions = json.load(f)
                else:
                    log.error("No data cache found. Run --phase data first.")
                    sys.exit(1)
            if design is None:
                if DESIGN_CACHE.exists():
                    log.info("Loading cached design...")
                    with open(DESIGN_CACHE) as f:
                        design = json.load(f)
                else:
                    log.error("No design cache found. Run --phase design first.")
                    sys.exit(1)
            backtest_results = run_backtest(positions, design)

    # Generate report if we have all pieces
    if analysis and design and backtest_results:
        log.info("Generating report...")
        report = generate_report(analysis, design, backtest_results)
        with open(REPORT_PATH, "w") as f:
            f.write(report)
        log.info(f"Report written to {REPORT_PATH}")

        # Also save backtest results
        backtest_path = OUTPUT_DIR / "feature_analysis_backtest.json"
        with open(backtest_path, "w") as f:
            json.dump(backtest_results, f, indent=2)
        log.info(f"Backtest results saved to {backtest_path}")
    elif analysis:
        log.info("Partial run — report not generated (need all phases)")


if __name__ == "__main__":
    main()
