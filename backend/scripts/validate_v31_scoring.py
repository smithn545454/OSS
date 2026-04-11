#!/usr/bin/env python3
"""Validate Policy v3.1.0 against the historical paper-trade corpus (Phase 7).

Pulls every closed paper position with both v3.0.0 (`conviction_score`) and
v3.1.0 (`v31_conviction_score`) fields populated, then computes the Group A
PRESERVE checks, the Group B ACHIEVE checks, the diagnostic monotonicity
check, and the bucket / scanner-conditioned analysis. Emits a markdown
report ending with an explicit ACTIVATE | ITERATE | ROLLBACK recommendation.

Reads from DynamoDB directly (no schema dependencies). Reuses `pearson_r`
from `feature_outcome_analysis.py`.

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
        python3 backend/scripts/validate_v31_scoring.py [--cut-date 2026-03-12]
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# Reuse pearson_r and decimal_to_python
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_outcome_analysis import decimal_to_python, pearson_r  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
REPORT_PATH = OUTPUT_DIR / "policy_v31_validation_report.md"

DEFAULT_CUT_DATE = "2026-03-12"


# ============================================================================
# Group A / B threshold tables (from spec)
# ============================================================================


GROUP_A_THRESHOLDS = {
    "A1_full_min": 0.10,
    "A1_holdout_min": 0.02,
    "A2_full_min": 25.0,
    "A2_holdout_min": 5.0,
    "A3_full_min": 150_000.0,
    "A3_holdout_min": 100_000.0,
    "A4_full_min_pct": 8.0,
    "A4_holdout_min_pct": 6.0,
}

GROUP_B_THRESHOLDS = {
    "B1_full_min": 0.08,
    "B1_holdout_min": 0.05,
    "B2_full_min_pct": 15.0,
    "B2_holdout_min_pct": 13.0,
    "B3_full_min_pct": 10.0,
    "B3_holdout_min_pct": 9.0,
    "B4_full_ratio_min": 1.3,
    "B4_holdout_ratio_min": 1.2,
}


# ============================================================================
# Data load
# ============================================================================


def load_positions() -> list[dict[str, Any]]:
    log.info(f"Connecting to DynamoDB region={AWS_REGION} prefix={TABLE_PREFIX}")
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = dynamodb.Table(f"{TABLE_PREFIX}-paper-positions")

    log.info("Querying POS#CLOSED partition...")
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq("POS#CLOSED")}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    log.info(f"  loaded {len(items)} closed positions")

    rows = []
    for it in items:
        p = decimal_to_python(it)
        rows.append(p)
    return rows


def filter_paired(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only positions with both v3.0.0 and v3.1.0 conviction populated."""
    out = []
    for r in rows:
        v30 = r.get("conviction_score")
        v31 = r.get("v31_conviction_score")
        pnl = r.get("current_pnl_pct")
        if v30 is None or v31 is None or pnl is None:
            continue
        try:
            r["_v30"] = float(v30)
            r["_v31"] = float(v31)
            r["_pnl"] = float(pnl)
        except (TypeError, ValueError):
            continue
        out.append(r)
    return out


def split_by_date(
    rows: list[dict[str, Any]], cut_date: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train, holdout = [], []
    for r in rows:
        d = (r.get("entry_date") or "")[:10]
        if d >= cut_date:
            holdout.append(r)
        else:
            train.append(r)
    return train, holdout


# ============================================================================
# Outcome flags
# ============================================================================


def annotate_outcomes(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        pnl = r["_pnl"]
        mae = r.get("max_adverse_excursion")
        days = r.get("days_held")
        try:
            mae_f = float(mae) if mae is not None else None
        except (TypeError, ValueError):
            mae_f = None
        try:
            days_i = int(days) if days is not None else None
        except (TypeError, ValueError):
            days_i = None
        is_hr = pnl >= 100.0
        r["is_hr"] = is_hr
        r["is_clean_hr"] = bool(
            is_hr
            and (mae_f is not None and mae_f >= -25.0)
            and (days_i is not None and days_i <= 5)
        )
        r["is_big_loss"] = pnl <= -50.0


# ============================================================================
# Decile helpers
# ============================================================================


def deciles_by(rows: list[dict[str, Any]], conv_field: str) -> list[list[dict[str, Any]]]:
    sorted_rows = sorted(rows, key=lambda r: r[conv_field])
    n = len(sorted_rows)
    if n == 0:
        return [[] for _ in range(10)]
    out: list[list[dict[str, Any]]] = []
    bucket = n // 10
    for i in range(10):
        start = i * bucket
        end = start + bucket if i < 9 else n
        out.append(sorted_rows[start:end])
    return out


def top_n_pct(rows: list[dict[str, Any]], conv_field: str, pct: float) -> list[dict[str, Any]]:
    sorted_rows = sorted(rows, key=lambda r: -r[conv_field])
    k = max(1, int(len(sorted_rows) * pct / 100))
    return sorted_rows[:k]


def bottom_n_pct(rows: list[dict[str, Any]], conv_field: str, pct: float) -> list[dict[str, Any]]:
    sorted_rows = sorted(rows, key=lambda r: r[conv_field])
    k = max(1, int(len(sorted_rows) * pct / 100))
    return sorted_rows[:k]


# ============================================================================
# Group A / B metric computation
# ============================================================================


def compute_group_a(
    rows: list[dict[str, Any]], conv_field: str = "_v31"
) -> dict[str, float]:
    if not rows:
        return {}

    convs = [r[conv_field] for r in rows]
    pnls = [r["_pnl"] for r in rows]
    a1 = pearson_r(convs, pnls)

    deciles = deciles_by(rows, conv_field)
    d1_pnl = statistics.mean([r["_pnl"] for r in deciles[0]]) if deciles[0] else 0.0
    d10_pnl = statistics.mean([r["_pnl"] for r in deciles[-1]]) if deciles[-1] else 0.0
    a2 = d10_pnl - d1_pnl

    # A3: total $PnL of conviction >= 60 subset (use realized contract pnl
    # estimate: pnl_pct * entry_price * 100). Many positions don't have a
    # `dollar_pnl` populated; reconstruct from pnl_pct * cost basis.
    def dollar_pnl(r: dict[str, Any]) -> float:
        d = r.get("dollar_pnl")
        if d is not None:
            try:
                return float(d)
            except (TypeError, ValueError):
                pass
        try:
            entry = float(r.get("entry_price") or 0.0)
            qty = int(r.get("quantity") or 1)
        except (TypeError, ValueError):
            return 0.0
        return r["_pnl"] / 100.0 * entry * qty * 100.0  # 100 shares per contract

    filtered = [r for r in rows if r[conv_field] >= 60.0]
    a3 = sum(dollar_pnl(r) for r in filtered)

    # A4: junk capture @ bottom 10% — fraction of all big_loss positions
    # that land in the bottom decile by conviction.
    big_losses_total = sum(1 for r in rows if r["is_big_loss"])
    bot10 = bottom_n_pct(rows, conv_field, 10)
    big_losses_bot = sum(1 for r in bot10 if r["is_big_loss"])
    a4 = (big_losses_bot / big_losses_total * 100.0) if big_losses_total > 0 else 0.0

    return {
        "A1_corr": a1,
        "A2_d10_minus_d1_pnl": a2,
        "A3_filter_total_dollar_pnl": a3,
        "A4_junk_capture_bot10_pct": a4,
    }


def compute_group_b(
    rows: list[dict[str, Any]], conv_field: str = "_v31"
) -> dict[str, float]:
    if not rows:
        return {}

    convs = [r[conv_field] for r in rows]
    clean_flags = [1.0 if r["is_clean_hr"] else 0.0 for r in rows]
    b1 = pearson_r(convs, clean_flags)

    total_clean = sum(1 for r in rows if r["is_clean_hr"])

    top10 = top_n_pct(rows, conv_field, 10)
    top10_clean = sum(1 for r in top10 if r["is_clean_hr"])
    b2 = (top10_clean / total_clean * 100.0) if total_clean > 0 else 0.0

    top5 = top_n_pct(rows, conv_field, 5)
    top5_clean = sum(1 for r in top5 if r["is_clean_hr"])
    b3 = (top5_clean / total_clean * 100.0) if total_clean > 0 else 0.0

    deciles = deciles_by(rows, conv_field)
    d1_clean = (
        sum(1 for r in deciles[0] if r["is_clean_hr"]) / len(deciles[0])
        if deciles[0]
        else 0.0
    )
    d10_clean = (
        sum(1 for r in deciles[-1] if r["is_clean_hr"]) / len(deciles[-1])
        if deciles[-1]
        else 0.0
    )
    b4_ratio = (d10_clean / d1_clean) if d1_clean > 0 else float("inf") if d10_clean > 0 else 0.0

    return {
        "B1_corr_clean_hr": b1,
        "B2_top10_clean_capture_pct": b2,
        "B3_top5_clean_capture_pct": b3,
        "B4_d10_clean_rate": d10_clean,
        "B4_d1_clean_rate": d1_clean,
        "B4_ratio": b4_ratio,
    }


def per_decile_clean_rate(rows: list[dict[str, Any]], conv_field: str) -> list[float]:
    deciles = deciles_by(rows, conv_field)
    return [
        (sum(1 for r in d if r["is_clean_hr"]) / len(d) * 100.0) if d else 0.0
        for d in deciles
    ]


# ============================================================================
# Bucket analysis (Section 7.3)
# ============================================================================


def bucket_means(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    buckets = {
        "clean_hr": [r for r in rows if r["is_clean_hr"]],
        "big_loss": [r for r in rows if r["is_big_loss"]],
        "neither": [r for r in rows if not r["is_clean_hr"] and not r["is_big_loss"]],
    }
    out: dict[str, dict[str, float]] = {}
    for name, bucket in buckets.items():
        if not bucket:
            out[name] = {"n": 0, "v30": 0.0, "v31": 0.0, "delta": 0.0}
            continue
        v30 = statistics.mean([r["_v30"] for r in bucket])
        v31 = statistics.mean([r["_v31"] for r in bucket])
        out[name] = {
            "n": len(bucket),
            "v30": v30,
            "v31": v31,
            "delta": v31 - v30,
        }
    return out


# ============================================================================
# Scanner-conditioned analysis (Section 7.4)
# ============================================================================


def scanner_clean_capture(rows: list[dict[str, Any]], conv_field: str) -> dict[str, dict[str, float]]:
    by_scanner: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_scanner[str(r.get("scanner_source") or "UNKNOWN")].append(r)
    out: dict[str, dict[str, float]] = {}
    for s, bucket in by_scanner.items():
        n = len(bucket)
        n_clean = sum(1 for r in bucket if r["is_clean_hr"])
        if n_clean == 0:
            out[s] = {"n": n, "n_clean": 0, "top10_capture_pct": 0.0}
            continue
        top10 = top_n_pct(bucket, conv_field, 10)
        cap = sum(1 for r in top10 if r["is_clean_hr"])
        out[s] = {
            "n": n,
            "n_clean": n_clean,
            "top10_capture_pct": cap / n_clean * 100.0,
        }
    return out


def top_20_spot_check(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_rows = sorted(rows, key=lambda r: -r["_v31"])
    out = []
    for r in sorted_rows[:20]:
        out.append(
            {
                "ticker": r.get("underlying_ticker"),
                "scanner": r.get("scanner_source"),
                "entry_date": (r.get("entry_date") or "")[:10],
                "entry_price": r.get("entry_price"),
                "dte": r.get("dte_at_entry"),
                "v31_conviction": r["_v31"],
                "v30_conviction": r["_v30"],
                "pnl_pct": r["_pnl"],
                "is_clean_hr": r["is_clean_hr"],
                "is_big_loss": r["is_big_loss"],
            }
        )
    return out


# ============================================================================
# Decision rules
# ============================================================================


def decide(group_a_full, group_a_h, group_b_full, group_b_h) -> tuple[str, list[str]]:
    failures: list[str] = []

    # Group A
    a_thr = GROUP_A_THRESHOLDS
    if group_a_full["A1_corr"] < a_thr["A1_full_min"]:
        failures.append(
            f"A1 (full): r(conv,pnl)={group_a_full['A1_corr']:.3f} < {a_thr['A1_full_min']}"
        )
    if group_a_h["A1_corr"] < a_thr["A1_holdout_min"]:
        failures.append(
            f"A1 (holdout): r(conv,pnl)={group_a_h['A1_corr']:.3f} < {a_thr['A1_holdout_min']}"
        )
    if group_a_full["A2_d10_minus_d1_pnl"] < a_thr["A2_full_min"]:
        failures.append(
            f"A2 (full): D10-D1 pnl={group_a_full['A2_d10_minus_d1_pnl']:.1f} < {a_thr['A2_full_min']}"
        )
    if group_a_h["A2_d10_minus_d1_pnl"] < a_thr["A2_holdout_min"]:
        failures.append(
            f"A2 (holdout): D10-D1 pnl={group_a_h['A2_d10_minus_d1_pnl']:.1f} < {a_thr['A2_holdout_min']}"
        )
    if group_a_full["A3_filter_total_dollar_pnl"] < a_thr["A3_full_min"]:
        failures.append(
            f"A3 (full): filter $PnL={group_a_full['A3_filter_total_dollar_pnl']:.0f} < {a_thr['A3_full_min']:.0f}"
        )
    if group_a_h["A3_filter_total_dollar_pnl"] < a_thr["A3_holdout_min"]:
        failures.append(
            f"A3 (holdout): filter $PnL={group_a_h['A3_filter_total_dollar_pnl']:.0f} < {a_thr['A3_holdout_min']:.0f}"
        )
    if group_a_full["A4_junk_capture_bot10_pct"] < a_thr["A4_full_min_pct"]:
        failures.append(
            f"A4 (full): junk capture={group_a_full['A4_junk_capture_bot10_pct']:.1f}% < {a_thr['A4_full_min_pct']}%"
        )
    if group_a_h["A4_junk_capture_bot10_pct"] < a_thr["A4_holdout_min_pct"]:
        failures.append(
            f"A4 (holdout): junk capture={group_a_h['A4_junk_capture_bot10_pct']:.1f}% < {a_thr['A4_holdout_min_pct']}%"
        )

    a_failures = [f for f in failures if f.startswith("A")]
    if a_failures:
        return "ROLLBACK", failures

    # Group B
    b_thr = GROUP_B_THRESHOLDS
    b_failures: list[str] = []
    if group_b_full["B1_corr_clean_hr"] < b_thr["B1_full_min"]:
        b_failures.append(
            f"B1 (full): r(conv,clean_hr)={group_b_full['B1_corr_clean_hr']:.3f} < {b_thr['B1_full_min']}"
        )
    if group_b_h["B1_corr_clean_hr"] < b_thr["B1_holdout_min"]:
        b_failures.append(
            f"B1 (holdout): r(conv,clean_hr)={group_b_h['B1_corr_clean_hr']:.3f} < {b_thr['B1_holdout_min']}"
        )
    if group_b_full["B2_top10_clean_capture_pct"] < b_thr["B2_full_min_pct"]:
        b_failures.append(
            f"B2 (full): top10 capture={group_b_full['B2_top10_clean_capture_pct']:.1f}% < {b_thr['B2_full_min_pct']}%"
        )
    if group_b_h["B2_top10_clean_capture_pct"] < b_thr["B2_holdout_min_pct"]:
        b_failures.append(
            f"B2 (holdout): top10 capture={group_b_h['B2_top10_clean_capture_pct']:.1f}% < {b_thr['B2_holdout_min_pct']}%"
        )
    if group_b_full["B3_top5_clean_capture_pct"] < b_thr["B3_full_min_pct"]:
        b_failures.append(
            f"B3 (full): top5 capture={group_b_full['B3_top5_clean_capture_pct']:.1f}% < {b_thr['B3_full_min_pct']}%"
        )
    if group_b_h["B3_top5_clean_capture_pct"] < b_thr["B3_holdout_min_pct"]:
        b_failures.append(
            f"B3 (holdout): top5 capture={group_b_h['B3_top5_clean_capture_pct']:.1f}% < {b_thr['B3_holdout_min_pct']}%"
        )
    if group_b_full["B4_ratio"] < b_thr["B4_full_ratio_min"]:
        b_failures.append(
            f"B4 (full): D10/D1 clean ratio={group_b_full['B4_ratio']:.2f} < {b_thr['B4_full_ratio_min']}"
        )
    if group_b_h["B4_ratio"] < b_thr["B4_holdout_ratio_min"]:
        b_failures.append(
            f"B4 (holdout): D10/D1 clean ratio={group_b_h['B4_ratio']:.2f} < {b_thr['B4_holdout_ratio_min']}"
        )

    failures.extend(b_failures)
    if b_failures:
        return "ITERATE", failures

    return "ACTIVATE", failures


# ============================================================================
# Markdown report
# ============================================================================


def write_report(
    full_rows: list[dict[str, Any]],
    train_rows: list[dict[str, Any]],
    holdout_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    a_full = compute_group_a(full_rows, "_v31")
    a_h = compute_group_a(holdout_rows, "_v31")
    a_full_v30 = compute_group_a(full_rows, "_v30")
    a_h_v30 = compute_group_a(holdout_rows, "_v30")

    b_full = compute_group_b(full_rows, "_v31")
    b_h = compute_group_b(holdout_rows, "_v31")
    b_full_v30 = compute_group_b(full_rows, "_v30")
    b_h_v30 = compute_group_b(holdout_rows, "_v30")

    decision, failures = decide(a_full, a_h, b_full, b_h)

    decile_v31_full = per_decile_clean_rate(full_rows, "_v31")
    decile_v31_h = per_decile_clean_rate(holdout_rows, "_v31")
    decile_v30_full = per_decile_clean_rate(full_rows, "_v30")

    bucket_full = bucket_means(full_rows)
    bucket_h = bucket_means(holdout_rows)

    scanner_full = scanner_clean_capture(full_rows, "_v31")
    top_20 = top_20_spot_check(full_rows)

    lines: list[str] = []
    lines.append("# Policy v3.1.0 — Validation Report")
    lines.append("")
    lines.append(f"- **Cut date**: {args.cut_date}")
    lines.append(f"- **Full N**: {len(full_rows)}")
    lines.append(f"- **Train N** (entry_date < {args.cut_date}): {len(train_rows)}")
    lines.append(f"- **Holdout N** (entry_date ≥ {args.cut_date}): {len(holdout_rows)}")
    lines.append("")
    lines.append("## Group A — PRESERVE (higher conv → more profitable trades)")
    lines.append("")
    lines.append("| Metric | v3.0.0 full | v3.0.0 hold | v3.1.0 full | v3.1.0 hold | Min full | Min hold |")
    lines.append("|---|---|---|---|---|---|---|")
    lines.append(
        f"| A1: r(conv, pnl%) | {a_full_v30['A1_corr']:+.3f} | "
        f"{a_h_v30['A1_corr']:+.3f} | {a_full['A1_corr']:+.3f} | "
        f"{a_h['A1_corr']:+.3f} | {GROUP_A_THRESHOLDS['A1_full_min']:.2f} | {GROUP_A_THRESHOLDS['A1_holdout_min']:.2f} |"
    )
    lines.append(
        f"| A2: D10−D1 pnl pts | {a_full_v30['A2_d10_minus_d1_pnl']:+.1f} | "
        f"{a_h_v30['A2_d10_minus_d1_pnl']:+.1f} | {a_full['A2_d10_minus_d1_pnl']:+.1f} | "
        f"{a_h['A2_d10_minus_d1_pnl']:+.1f} | {GROUP_A_THRESHOLDS['A2_full_min']:.0f} | {GROUP_A_THRESHOLDS['A2_holdout_min']:.0f} |"
    )
    lines.append(
        f"| A3: filter $PnL (conv ≥60) | ${a_full_v30['A3_filter_total_dollar_pnl']:,.0f} | "
        f"${a_h_v30['A3_filter_total_dollar_pnl']:,.0f} | "
        f"${a_full['A3_filter_total_dollar_pnl']:,.0f} | "
        f"${a_h['A3_filter_total_dollar_pnl']:,.0f} | "
        f"${GROUP_A_THRESHOLDS['A3_full_min']:,.0f} | ${GROUP_A_THRESHOLDS['A3_holdout_min']:,.0f} |"
    )
    lines.append(
        f"| A4: junk capture @ bot10 | {a_full_v30['A4_junk_capture_bot10_pct']:.1f}% | "
        f"{a_h_v30['A4_junk_capture_bot10_pct']:.1f}% | {a_full['A4_junk_capture_bot10_pct']:.1f}% | "
        f"{a_h['A4_junk_capture_bot10_pct']:.1f}% | {GROUP_A_THRESHOLDS['A4_full_min_pct']:.0f}% | "
        f"{GROUP_A_THRESHOLDS['A4_holdout_min_pct']:.0f}% |"
    )
    lines.append("")

    lines.append("## Group B — ACHIEVE (top conv = clean home runs)")
    lines.append("")
    lines.append("| Metric | v3.0.0 full | v3.0.0 hold | v3.1.0 full | v3.1.0 hold | Min full | Min hold |")
    lines.append("|---|---|---|---|---|---|---|")
    lines.append(
        f"| B1: r(conv, clean_hr) | {b_full_v30['B1_corr_clean_hr']:+.3f} | "
        f"{b_h_v30['B1_corr_clean_hr']:+.3f} | {b_full['B1_corr_clean_hr']:+.3f} | "
        f"{b_h['B1_corr_clean_hr']:+.3f} | {GROUP_B_THRESHOLDS['B1_full_min']:.2f} | "
        f"{GROUP_B_THRESHOLDS['B1_holdout_min']:.2f} |"
    )
    lines.append(
        f"| B2: top10 clean capture | {b_full_v30['B2_top10_clean_capture_pct']:.1f}% | "
        f"{b_h_v30['B2_top10_clean_capture_pct']:.1f}% | "
        f"{b_full['B2_top10_clean_capture_pct']:.1f}% | "
        f"{b_h['B2_top10_clean_capture_pct']:.1f}% | "
        f"{GROUP_B_THRESHOLDS['B2_full_min_pct']:.0f}% | "
        f"{GROUP_B_THRESHOLDS['B2_holdout_min_pct']:.0f}% |"
    )
    lines.append(
        f"| B3: top5 clean capture | {b_full_v30['B3_top5_clean_capture_pct']:.1f}% | "
        f"{b_h_v30['B3_top5_clean_capture_pct']:.1f}% | "
        f"{b_full['B3_top5_clean_capture_pct']:.1f}% | "
        f"{b_h['B3_top5_clean_capture_pct']:.1f}% | "
        f"{GROUP_B_THRESHOLDS['B3_full_min_pct']:.0f}% | "
        f"{GROUP_B_THRESHOLDS['B3_holdout_min_pct']:.0f}% |"
    )
    lines.append(
        f"| B4: D10/D1 clean ratio | {b_full_v30['B4_ratio']:.2f} | "
        f"{b_h_v30['B4_ratio']:.2f} | {b_full['B4_ratio']:.2f} | "
        f"{b_h['B4_ratio']:.2f} | {GROUP_B_THRESHOLDS['B4_full_ratio_min']:.2f} | "
        f"{GROUP_B_THRESHOLDS['B4_holdout_ratio_min']:.2f} |"
    )
    lines.append("")

    lines.append("## Diagnostic — Per-Decile Clean HR Rate")
    lines.append("")
    lines.append("| Decile | v3.0.0 full | v3.1.0 full | v3.1.0 holdout |")
    lines.append("|---|---|---|---|")
    for i in range(10):
        lines.append(
            f"| D{i + 1} | {decile_v30_full[i]:.2f}% | "
            f"{decile_v31_full[i]:.2f}% | {decile_v31_h[i]:.2f}% |"
        )
    lines.append("")

    lines.append("## Bucket Analysis (Section 7.3)")
    lines.append("")
    lines.append("### Full")
    lines.append("")
    lines.append("| Bucket | n | v3.0.0 mean conv | v3.1.0 mean conv | Δ |")
    lines.append("|---|---|---|---|---|")
    for name in ("clean_hr", "big_loss", "neither"):
        b = bucket_full[name]
        lines.append(
            f"| {name} | {b['n']} | {b['v30']:.2f} | {b['v31']:.2f} | {b['delta']:+.2f} |"
        )
    lines.append("")
    lines.append("### Holdout")
    lines.append("")
    lines.append("| Bucket | n | v3.0.0 mean conv | v3.1.0 mean conv | Δ |")
    lines.append("|---|---|---|---|---|")
    for name in ("clean_hr", "big_loss", "neither"):
        b = bucket_h[name]
        lines.append(
            f"| {name} | {b['n']} | {b['v30']:.2f} | {b['v31']:.2f} | {b['delta']:+.2f} |"
        )
    lines.append("")

    lines.append("## Scanner-Conditioned (Full, top-10 clean capture under v3.1.0)")
    lines.append("")
    lines.append("| Scanner | n | n_clean | top10 capture |")
    lines.append("|---|---|---|---|")
    for s, m in sorted(scanner_full.items(), key=lambda x: -x[1]["n"]):
        lines.append(
            f"| {s} | {m['n']} | {m['n_clean']} | {m['top10_capture_pct']:.1f}% |"
        )
    lines.append("")

    lines.append("## Top 20 by v3.1.0 Conviction (Spot Check)")
    lines.append("")
    lines.append("| # | Ticker | Scanner | Entry Date | Entry $ | DTE | v31 | v30 | PnL% | Clean HR | Big Loss |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(top_20, 1):
        lines.append(
            f"| {i} | {r['ticker']} | {r['scanner']} | {r['entry_date']} | "
            f"{r['entry_price']} | {r['dte']} | {r['v31_conviction']:.1f} | "
            f"{r['v30_conviction']:.1f} | {r['pnl_pct']:+.1f} | "
            f"{'✅' if r['is_clean_hr'] else ''} | {'❌' if r['is_big_loss'] else ''} |"
        )
    lines.append("")

    if failures:
        lines.append("## Failures")
        lines.append("")
        for f in failures:
            lines.append(f"- {f}")
        lines.append("")

    lines.append(f"## RECOMMENDATION: {decision}")
    lines.append("")
    if decision == "ACTIVATE":
        lines.append("All Group A and Group B criteria met on both splits. Safe to activate v3.1.0.")
    elif decision == "ITERATE":
        lines.append("Group A preserved but Group B failed. Re-fit Phase 3 with adjusted parameters.")
    elif decision == "ROLLBACK":
        lines.append("Group A regressed — junk filtering capability lost. Roll back to v3.0.0.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))
    log.info(f"Validation report written to {REPORT_PATH}")
    log.info(f"RECOMMENDATION: {decision}")


# ============================================================================
# Main
# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Policy v3.1.0 against historical paper trades")
    parser.add_argument(
        "--cut-date",
        type=str,
        default=DEFAULT_CUT_DATE,
        help=f"Train/holdout boundary entry_date (default: {DEFAULT_CUT_DATE})",
    )
    args = parser.parse_args()

    rows = load_positions()
    paired = filter_paired(rows)
    log.info(f"  paired rows (have both v3.0.0 and v3.1.0 conviction): {len(paired)}")
    if not paired:
        log.error("No paired rows; have you run rescore_positions_v31.py?")
        return 1
    annotate_outcomes(paired)
    train, holdout = split_by_date(paired, args.cut_date)
    log.info(f"  train: {len(train)}  holdout: {len(holdout)}")

    write_report(paired, train, holdout, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
