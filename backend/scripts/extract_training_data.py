#!/usr/bin/env python3
"""Extract training data for Policy v3.1.0 pillar refit (Phase 2).

Pulls all closed paper positions from DynamoDB, joins them with their
FeatureValueTable records by `evaluation_id`, computes outcome labels
(`is_hr`, `is_clean_hr`, `is_fast_hr`, `is_big_loss`), and time-splits
the rows into a train partition (entry_date < cut_date) and a holdout
partition (entry_date >= cut_date).

The output JSON files are consumed by `fit_pillars_v31.py` (Phase 3) and
`validate_v31_scoring.py` (Phase 7). They are intentionally simple lists of
dicts so downstream scripts have zero schema dependencies.

Reuses utilities from `feature_outcome_analysis.py`:
- DynamoDB query/pagination pattern (`collect_data`)
- FVT field name mapping (`FVT_FIELD_MAP`)
- Decimal → Python conversion

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
        python3 backend/scripts/extract_training_data.py [--limit N] [--cut-date 2026-03-12]

Outputs:
    backend/scripts/output/training_data_train.json
    backend/scripts/output/training_data_holdout.json
    backend/scripts/output/training_data_coverage.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

# Reuse FVT field mapping from feature_outcome_analysis.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_outcome_analysis import FVT_FIELD_MAP, decimal_to_python  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
TRAIN_PATH = OUTPUT_DIR / "training_data_train.json"
HOLDOUT_PATH = OUTPUT_DIR / "training_data_holdout.json"
COVERAGE_PATH = OUTPUT_DIR / "training_data_coverage.md"

# Default cut date (2026-03-12) per the source plan's Phase 0 split
DEFAULT_CUT_DATE = "2026-03-12"

# Position-level fields that we always carry forward (denormalized at entry)
POSITION_FIELDS = [
    "position_id",
    "evaluation_id",
    "entry_date",
    "underlying_ticker",
    "option_type",
    "scanner_source",
    "convergence_count",
    "dte_bucket",
    "dte_at_entry",
    "entry_price",
    "entry_delta",
    "entry_iv",
    "entry_theta",
    "entry_iv_percentile",
    "entry_iv_rv_ratio",
    "entry_theta_adjusted_edge",
    "entry_rv20",
    "entry_underlying_price",
    "entry_moneyness_pct",
    "entry_spread_pct",
    "entry_open_interest",
    "entry_volume",
    "entry_days_to_earnings",
    "entry_atr14_pct",
    "entry_rs_20d",
    "entry_feasibility_ratio",
    # Outcome fields
    "current_pnl_pct",
    "max_adverse_excursion",
    "days_held",
    "exit_reason",
    # Regime-tagged baseline scores (v3 scores on pre-v4 rows; v4 scores
    # on post-activation rows). Each row carries at most one populated trio.
    "conviction_score",
    "pillar_premium_leverage",
    "pillar_underlying_behavior",
    "pillar_setup_quality",
    "pillar_directional_conviction",
    "pillar_move_potential",
    "pillar_trade_structure",
]

# FVT-derived numeric features used by the v3.1.0 fitter
FVT_NUMERIC_FEATURES = [
    "iv_percentile",
    "iv_rv_ratio",
    "theta_pct",
    "theta_adjusted_edge",
    "adx_14",
    "rv20",
    "feasibility_ratio",
    "time_adjusted_feasibility",
    "atr14_pct",
    "rs_20d",
    "rs_5d",
    "return_5d",
    "return_20d",
    "mid",
    "spread_pct",
    "expected_move_pct",
    "required_move_pct",
    "open_interest",
    "volume",
    "oi_5d_change_pct",
    "days_to_earnings",
    "rsi_14",
    "macd_histogram",
    "plus_di",
    "minus_di",
]

# FVT-derived categorical features
FVT_CATEGORICAL_FEATURES = [
    "iv_regime",
    "ema_alignment",
    "obv_trend",
    "recent_sec_filing",
]


# ============================================================================
# DynamoDB query
# ============================================================================


def query_closed_positions(positions_table: Any) -> list[dict[str, Any]]:
    """Page through all closed paper positions."""
    log.info("Querying POS#CLOSED partition...")
    results: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq("POS#CLOSED")}
    pages = 0
    while True:
        resp = positions_table.query(**kwargs)
        results.extend(resp.get("Items", []))
        pages += 1
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    log.info(f"  loaded {len(results)} closed positions across {pages} pages")
    return results


def fetch_feature_set(fvt_table: Any, evaluation_id: str) -> dict[str, Any]:
    """Query FeatureValueTable for an evaluation and return a flat
    {feature_name: value} dict mapped through FVT_FIELD_MAP (without
    the fvt_ prefix; we keep raw feature names so downstream scripts
    can use either form).
    """
    try:
        resp = fvt_table.query(
            KeyConditionExpression=Key("PK").eq(f"EVAL#{evaluation_id}")
            & Key("SK").begins_with("FEATURE#"),
        )
    except Exception as e:
        log.debug(f"FVT query failed for {evaluation_id}: {e}")
        return {}

    out: dict[str, Any] = {}
    for item in resp.get("Items", []):
        feat_name = str(item.get("SK", "")).replace("FEATURE#", "")
        if not feat_name:
            continue
        # Only keep the feature names we know about
        if feat_name in FVT_FIELD_MAP:
            out[feat_name] = decimal_to_python(item.get("value"))
    return out


# ============================================================================
# Row construction
# ============================================================================


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_row(
    position: dict[str, Any],
    feature_set: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Combine a paper position with its FVT feature set into one training row.

    Returns None if the row lacks the minimum fields needed for training
    (conviction_score and current_pnl_pct must be populated).
    """
    p = decimal_to_python(position)

    conviction = to_float(p.get("conviction_score"))
    pnl_pct = to_float(p.get("current_pnl_pct"))
    if conviction is None or pnl_pct is None:
        return None

    row: dict[str, Any] = {}

    for field in POSITION_FIELDS:
        row[field] = p.get(field)

    # abs_entry_delta — derived from entry_delta if present (prefer real
    # delta over the cached `abs_entry_delta` field which is sometimes stale)
    raw_delta = to_float(p.get("entry_delta"))
    row["abs_entry_delta"] = abs(raw_delta) if raw_delta is not None else None

    # FVT numeric features (overrides position-level snapshots when available)
    for feat in FVT_NUMERIC_FEATURES:
        if feat in feature_set:
            row[feat] = feature_set[feat]
        else:
            row.setdefault(feat, None)

    # FVT categorical features
    for feat in FVT_CATEGORICAL_FEATURES:
        if feat in feature_set:
            row[feat] = feature_set[feat]
        else:
            row.setdefault(feat, None)

    # Make sure entry_iv lives under both names (some FVT joins overwrite it)
    if "iv" in feature_set and feature_set["iv"] is not None:
        row["iv"] = feature_set["iv"]
    else:
        row["iv"] = to_float(p.get("entry_iv"))

    # Outcome labels
    pnl_val = pnl_pct
    mae = to_float(p.get("max_adverse_excursion"))
    days_held = p.get("days_held")
    try:
        days_held_int = int(days_held) if days_held is not None else None
    except (TypeError, ValueError):
        days_held_int = None

    is_hr = pnl_val >= 100.0
    is_clean_hr = bool(
        is_hr
        and (mae is not None and mae >= -25.0)
        and (days_held_int is not None and days_held_int <= 5)
    )
    is_fast_hr = bool(is_hr and (days_held_int is not None and days_held_int <= 3))
    is_big_loss = pnl_val <= -50.0

    row["is_hr"] = bool(is_hr)
    row["is_clean_hr"] = is_clean_hr
    row["is_fast_hr"] = is_fast_hr
    row["is_big_loss"] = is_big_loss

    return row


# ============================================================================
# Coverage report
# ============================================================================


def write_coverage_report(
    train_rows: list[dict[str, Any]],
    holdout_rows: list[dict[str, Any]],
    cut_date: str,
) -> None:
    """Write a markdown report with per-feature non-null rates and class balance."""

    def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rows)
        if n == 0:
            return {"n": 0}
        is_hr = sum(1 for r in rows if r["is_hr"])
        is_clean = sum(1 for r in rows if r["is_clean_hr"])
        is_fast = sum(1 for r in rows if r["is_fast_hr"])
        is_big = sum(1 for r in rows if r["is_big_loss"])
        return {
            "n": n,
            "hr_rate": is_hr / n,
            "clean_hr_rate": is_clean / n,
            "fast_hr_rate": is_fast / n,
            "big_loss_rate": is_big / n,
        }

    def feature_coverage(rows: list[dict[str, Any]], features: list[str]) -> dict[str, float]:
        n = len(rows)
        if n == 0:
            return {f: 0.0 for f in features}
        out = {}
        for f in features:
            non_null = sum(1 for r in rows if r.get(f) is not None)
            out[f] = non_null / n
        return out

    train_stats = stats(train_rows)
    holdout_stats = stats(holdout_rows)

    pocket_features = ["dte_at_entry", "entry_price", "scanner_source", "abs_entry_delta", "convergence_count"]
    pl_features = ["iv_percentile", "iv_rv_ratio", "theta_pct"]
    ub_features = ["adx_14", "rv20", "feasibility_ratio", "time_adjusted_feasibility", "atr14_pct"]

    train_pocket_cov = feature_coverage(train_rows, pocket_features)
    train_pl_cov = feature_coverage(train_rows, pl_features)
    train_ub_cov = feature_coverage(train_rows, ub_features)
    holdout_pocket_cov = feature_coverage(holdout_rows, pocket_features)
    holdout_pl_cov = feature_coverage(holdout_rows, pl_features)
    holdout_ub_cov = feature_coverage(holdout_rows, ub_features)

    lines = [
        "# Training Data Coverage Report",
        "",
        f"- **Cut date**: {cut_date}",
        f"- **Train rows** (entry_date < {cut_date}): {train_stats['n']}",
        f"- **Holdout rows** (entry_date >= {cut_date}): {holdout_stats['n']}",
        "",
        "## Class Balance",
        "",
        "| Metric | Train | Holdout |",
        "|---|---|---|",
    ]
    for key in ["hr_rate", "clean_hr_rate", "fast_hr_rate", "big_loss_rate"]:
        t = train_stats.get(key, 0.0)
        h = holdout_stats.get(key, 0.0)
        lines.append(f"| {key} | {t * 100:.2f}% | {h * 100:.2f}% |")
    lines.append("")

    def coverage_section(title: str, features: list[str], train_cov: dict, holdout_cov: dict) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| Feature | Train coverage | Holdout coverage |")
        lines.append("|---|---|---|")
        for f in features:
            lines.append(f"| {f} | {train_cov[f] * 100:.1f}% | {holdout_cov[f] * 100:.1f}% |")
        lines.append("")

    coverage_section("Setup Pocket Pillar Features", pocket_features, train_pocket_cov, holdout_pocket_cov)
    coverage_section("Premium Leverage Pillar Features", pl_features, train_pl_cov, holdout_pl_cov)
    coverage_section("Underlying Behavior Pillar Features", ub_features, train_ub_cov, holdout_ub_cov)

    COVERAGE_PATH.write_text("\n".join(lines))
    log.info(f"Coverage report written to {COVERAGE_PATH}")


# ============================================================================
# Main flow
# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract training data for Policy v3.1.0 pillar refit"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of positions processed (for smoke testing)",
    )
    parser.add_argument(
        "--cut-date",
        type=str,
        default=DEFAULT_CUT_DATE,
        help=f"Train/holdout boundary entry_date (default: {DEFAULT_CUT_DATE})",
    )
    args = parser.parse_args()

    log.info(f"Connecting to DynamoDB region={AWS_REGION} prefix={TABLE_PREFIX}")
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions_table = dynamodb.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt_table = dynamodb.Table(f"{TABLE_PREFIX}-feature-values")

    closed_positions = query_closed_positions(positions_table)
    if args.limit:
        closed_positions = closed_positions[: args.limit]
        log.info(f"Limited to {args.limit} positions")

    train_rows: list[dict[str, Any]] = []
    holdout_rows: list[dict[str, Any]] = []
    skipped_no_outcome = 0
    fvt_hits = 0
    start = time.time()

    for idx, pos in enumerate(closed_positions):
        if idx % 500 == 0 and idx > 0:
            elapsed = time.time() - start
            rate = idx / elapsed if elapsed > 0 else 0
            eta = (len(closed_positions) - idx) / rate if rate > 0 else 0
            log.info(
                f"  {idx}/{len(closed_positions)} "
                f"(train={len(train_rows)} holdout={len(holdout_rows)} fvt_hits={fvt_hits}) "
                f"{rate:.1f}/s ETA {eta / 60:.1f} min"
            )
            time.sleep(0.05)  # mild throttle

        eval_id = decimal_to_python(pos.get("evaluation_id")) or ""
        feature_set = fetch_feature_set(fvt_table, eval_id) if eval_id else {}
        if feature_set:
            fvt_hits += 1

        row = build_row(pos, feature_set)
        if row is None:
            skipped_no_outcome += 1
            continue

        entry_date = row.get("entry_date") or ""
        if entry_date >= args.cut_date:
            holdout_rows.append(row)
        else:
            train_rows.append(row)

    elapsed = time.time() - start
    log.info("=" * 60)
    log.info("EXTRACTION COMPLETE")
    log.info("=" * 60)
    log.info(f"Closed positions scanned:    {len(closed_positions)}")
    log.info(f"Skipped (no outcome data):   {skipped_no_outcome}")
    log.info(f"FVT enrichment hits:         {fvt_hits}")
    log.info(f"Train rows  (< {args.cut_date}): {len(train_rows)}")
    log.info(f"Holdout rows (>= {args.cut_date}): {len(holdout_rows)}")
    log.info(f"Elapsed: {elapsed / 60:.1f} min")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def _json_default(o: Any) -> Any:
        if isinstance(o, Decimal):
            f = float(o)
            return int(f) if f == int(f) else f
        raise TypeError(f"Unsupported type {type(o).__name__}")

    with open(TRAIN_PATH, "w") as f:
        json.dump(train_rows, f, default=_json_default)
    log.info(f"Wrote {TRAIN_PATH} ({len(train_rows)} rows)")
    with open(HOLDOUT_PATH, "w") as f:
        json.dump(holdout_rows, f, default=_json_default)
    log.info(f"Wrote {HOLDOUT_PATH} ({len(holdout_rows)} rows)")

    write_coverage_report(train_rows, holdout_rows, args.cut_date)

    return 0


if __name__ == "__main__":
    sys.exit(main())
