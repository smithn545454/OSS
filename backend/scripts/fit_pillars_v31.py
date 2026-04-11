#!/usr/bin/env python3
"""Drift-aware pillar fitter for Policy v3.1.0 (Phase 3).

Builds the Setup Pocket / Premium Leverage / Underlying Behavior pillars
by fitting tercile breakpoints against the training data extracted in
Phase 2. Three drift-aware amendments are baked into the methodology:

1. **Terciles, not quintiles** — fewer breakpoints reduces overfit room.
2. **Recency-weighted** — exponential decay (`half_life_days`) shifts the
   fit toward recent positions, since the April regime materially differs
   from Feb–Mar.
3. **Generalization gate** — every candidate subscore is fit on train,
   then scored on holdout; if `|train_r - holdout_r|` exceeds the gap
   threshold, the subscore is dropped from the pillar entirely. The
   surviving subscores have their weights renormalized.

The fit objective is a weighted blend of:
- Per-tercile clean-HR rate (primary; aligns with the home-run goal)
- Per-tercile mean PnL (regularizer; preserves the junk-filtering goal)

Score interpolation is piecewise linear and matches `scoring_engine.py`
exactly so train/holdout correlations measured here equal what the live
engine will produce.

Usage:
    python3 backend/scripts/fit_pillars_v31.py \\
        --train backend/scripts/output/training_data_train.json \\
        --holdout backend/scripts/output/training_data_holdout.json \\
        [--hr-weight 0.7] [--recency-halflife 30] [--gap-threshold 0.05] \\
        [--ref-date 2026-04-10]

Outputs:
    backend/scripts/output/policy_v31_fitted.json
    backend/scripts/output/pillar_v31_design_report.md
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Reuse pearson_r from feature_outcome_analysis.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_outcome_analysis import pearson_r  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DEFAULT_TRAIN = OUTPUT_DIR / "training_data_train.json"
DEFAULT_HOLDOUT = OUTPUT_DIR / "training_data_holdout.json"
FITTED_POLICY_PATH = OUTPUT_DIR / "policy_v31_fitted.json"
DESIGN_REPORT_PATH = OUTPUT_DIR / "pillar_v31_design_report.md"


# ============================================================================
# Pillar layout (per implementation plan / source spec Phase 4)
# ============================================================================


@dataclass
class SubscoreSpec:
    """One candidate subscore in a pillar layout."""

    subscore_id: str
    display_name: str
    feature_field: str  # ScoringContext attribute name
    source_column: str  # column name in training data JSON
    target_weight: float  # within-pillar weight before any rejection redistribution
    is_categorical: bool = False
    source_tier: str = "tier2"


SETUP_POCKET = [
    SubscoreSpec("dte", "DTE at Entry", "dte", "dte_at_entry", 0.30, source_tier="tier1"),
    SubscoreSpec("entry_price", "Entry Price Tier", "entry_price", "entry_price", 0.25, source_tier="tier1"),
    SubscoreSpec("scanner_source", "Scanner Source", "scanner_source", "scanner_source", 0.20, is_categorical=True, source_tier="tier1"),
    SubscoreSpec("abs_delta", "|Entry Delta|", "abs_delta", "abs_entry_delta", 0.15, source_tier="tier1"),
    SubscoreSpec("convergence_count", "Scanner Convergence", "convergence_count", "convergence_count", 0.10, source_tier="tier1"),
]

PREMIUM_LEVERAGE = [
    SubscoreSpec("iv_percentile", "IV Percentile", "iv_percentile", "iv_percentile", 0.40),
    SubscoreSpec("iv_rv_ratio", "IV/RV Ratio", "iv_rv_ratio", "iv_rv_ratio", 0.30),
    SubscoreSpec("theta_pct", "Theta Burden (%)", "theta_pct", "theta_pct", 0.30),
]

UNDERLYING_BEHAVIOR = [
    SubscoreSpec("adx_14", "ADX (14)", "adx_14", "adx_14", 0.35),
    SubscoreSpec("rv20", "Realized Vol (20d)", "rv20", "rv20", 0.25),
    SubscoreSpec("feasibility_ratio", "Feasibility Ratio", "feasibility_ratio", "feasibility_ratio", 0.15),
    SubscoreSpec("time_adjusted_feasibility", "Time-Adjusted Feasibility", "time_adjusted_feasibility", "time_adjusted_feasibility", 0.15),
    SubscoreSpec("atr14_pct", "ATR % (14)", "atr14_pct", "atr14_pct", 0.10),
]

PILLAR_LAYOUT = {
    "PREMIUM_LEVERAGE": ("Premium Leverage", PREMIUM_LEVERAGE),
    "UNDERLYING_BEHAVIOR": ("Underlying Behavior", UNDERLYING_BEHAVIOR),
    "SETUP_QUALITY": ("Setup Pocket", SETUP_POCKET),
}


# ============================================================================
# Recency weighting
# ============================================================================


def _parse_date(d: Any) -> Optional[datetime]:
    if not d:
        return None
    s = str(d)[:10]  # take YYYY-MM-DD prefix
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


def exponential_weights(
    entry_dates: list[Any],
    half_life_days: float,
    ref_date: Optional[datetime] = None,
) -> list[float]:
    """w_i = 0.5 ** (days_ago_i / half_life_days)."""
    parsed: list[Optional[datetime]] = [_parse_date(d) for d in entry_dates]
    valid = [d for d in parsed if d is not None]
    if not valid:
        return [1.0 for _ in entry_dates]
    if ref_date is None:
        ref_date = max(valid)
    weights: list[float] = []
    for d in parsed:
        if d is None:
            weights.append(0.0)
            continue
        days_ago = (ref_date - d).days
        if days_ago < 0:
            days_ago = 0
        weights.append(0.5 ** (days_ago / half_life_days))
    return weights


# ============================================================================
# Weighted statistics
# ============================================================================


def weighted_mean(values: list[float], weights: list[float]) -> float:
    total_w = math.fsum(weights)
    if total_w <= 0:
        return 0.0
    return math.fsum(v * w for v, w in zip(values, weights)) / total_w


def weighted_fraction(flags: list[bool], weights: list[float]) -> float:
    total_w = math.fsum(weights)
    if total_w <= 0:
        return 0.0
    return math.fsum(w for f, w in zip(flags, weights) if f) / total_w


def weighted_quantile(
    values: list[float],
    weights: list[float],
    q: float,
) -> float:
    """Weighted quantile via cumulative weights (inclusive method)."""
    if not values:
        return 0.0
    pairs = sorted(zip(values, weights), key=lambda p: p[0])
    cum = 0.0
    total = math.fsum(w for _, w in pairs)
    if total <= 0:
        return pairs[len(pairs) // 2][0]
    target = q * total
    for v, w in pairs:
        cum += w
        if cum >= target:
            return v
    return pairs[-1][0]


# ============================================================================
# Tercile partitioning
# ============================================================================


@dataclass
class Tercile:
    values: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    flags: list[bool] = field(default_factory=list)
    pnls: list[float] = field(default_factory=list)


def split_terciles(
    values: list[float],
    weights: list[float],
    flags: list[bool],
    pnls: list[float],
) -> tuple[float, float, list[Tercile]]:
    """Compute tercile boundaries and partition the data."""
    b1 = weighted_quantile(values, weights, 1 / 3)
    b2 = weighted_quantile(values, weights, 2 / 3)

    terciles = [Tercile(), Tercile(), Tercile()]
    for v, w, f, p in zip(values, weights, flags, pnls):
        if v < b1:
            t = terciles[0]
        elif v < b2:
            t = terciles[1]
        else:
            t = terciles[2]
        t.values.append(v)
        t.weights.append(w)
        t.flags.append(f)
        t.pnls.append(p)

    return b1, b2, terciles


# ============================================================================
# Piecewise linear interpolation (matches scoring_engine.score_numeric_subscore)
# ============================================================================


def interp_breakpoints(
    value: Optional[float],
    breakpoints: list[tuple[float, float]],
) -> Optional[float]:
    """Piecewise linear interpolation matching `score_numeric_subscore`."""
    if value is None:
        return None
    bps = sorted(breakpoints, key=lambda b: b[0])
    if len(bps) < 2:
        return None
    if value <= bps[0][0]:
        return bps[0][1]
    if value >= bps[-1][0]:
        return bps[-1][1]
    for i in range(len(bps) - 1):
        v0, s0 = bps[i]
        v1, s1 = bps[i + 1]
        if v0 <= value <= v1:
            if abs(v1 - v0) < 1e-15:
                return s0
            t = (value - v0) / (v1 - v0)
            return s0 + t * (s1 - s0)
    return 50.0


def score_categorical(
    value: Any,
    category_scores: dict[str, float],
    default: float = 50.0,
) -> Optional[float]:
    if value is None:
        return None
    key = str(value)
    return category_scores.get(key, default)


# ============================================================================
# Numeric fitter
# ============================================================================


@dataclass
class FitResult:
    """Result of fitting one subscore. None breakpoints/scores → rejected."""

    spec: SubscoreSpec
    breakpoints: Optional[list[tuple[float, float]]] = None
    category_scores: Optional[dict[str, float]] = None
    train_r: float = 0.0
    holdout_r: float = 0.0
    gap: float = 0.0
    accepted: bool = False
    rejection_reason: Optional[str] = None
    tercile_breakpoints: Optional[tuple[float, float]] = None
    tercile_clean_hr_rates: Optional[tuple[float, float, float]] = None
    tercile_mean_pnl: Optional[tuple[float, float, float]] = None
    train_n: int = 0
    holdout_n: int = 0
    raw_train_r_with_pnl: float = 0.0
    raw_holdout_r_with_pnl: float = 0.0


def _normalize_to_unit(values: list[float]) -> list[float]:
    """Min-max normalize a small list to [0, 1]; returns [0.5, 0.5, ...] if degenerate."""
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi - lo < 1e-15:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _linear_map(scores01: list[float], lo: float, hi: float) -> list[float]:
    """Map [0,1] composite scores into [lo, hi]."""
    return [lo + s * (hi - lo) for s in scores01]


def fit_numeric_subscore(
    spec: SubscoreSpec,
    train_values: list[float],
    train_weights: list[float],
    train_flags: list[bool],
    train_pnls: list[float],
    holdout_values: list[float],
    holdout_pnls: list[float],
    hr_weight: float,
    pnl_weight: float,
    gap_threshold: float,
) -> FitResult:
    result = FitResult(spec=spec, train_n=len(train_values), holdout_n=len(holdout_values))

    if len(train_values) < 60:
        result.rejection_reason = f"Too few train rows ({len(train_values)} < 60)"
        return result
    if len(holdout_values) < 30:
        result.rejection_reason = f"Too few holdout rows ({len(holdout_values)} < 30)"
        return result

    b1, b2, terciles = split_terciles(train_values, train_weights, train_flags, train_pnls)

    if any(len(t.values) < 20 for t in terciles):
        result.rejection_reason = (
            f"Tercile too small: sizes={[len(t.values) for t in terciles]}"
        )
        return result

    clean_rates = [weighted_fraction(t.flags, t.weights) for t in terciles]
    mean_pnls = [weighted_mean(t.pnls, t.weights) for t in terciles]

    norm_clean = _normalize_to_unit(clean_rates)
    norm_pnl = _normalize_to_unit(mean_pnls)
    composite = [
        hr_weight * c + pnl_weight * p for c, p in zip(norm_clean, norm_pnl)
    ]
    bp_scores = _linear_map(composite, 10.0, 90.0)

    # Build the breakpoint list. The scoring engine clamps below the lowest
    # breakpoint and above the highest, so we use the empirical min, the
    # tercile boundaries (b1, b2), and the empirical max as the four
    # breakpoint values. The three composite scores are mapped to (b1_low,
    # b1_to_b2_mid, b2_high) — i.e. the score AT each tercile midpoint.
    feat_min = min(train_values)
    feat_max = max(train_values)
    # Use tercile boundary midpoints as the breakpoint x-values so the score
    # transitions smoothly. Anchor scores at min, b1, b2, max so the curve
    # is monotonic-by-construction within each tercile.
    breakpoints = [
        (feat_min, bp_scores[0]),
        (b1, (bp_scores[0] + bp_scores[1]) / 2.0),
        (b2, (bp_scores[1] + bp_scores[2]) / 2.0),
        (feat_max, bp_scores[2]),
    ]
    # Deduplicate exactly equal values that can collapse the interpolation
    seen_x: set[float] = set()
    deduped: list[tuple[float, float]] = []
    for x, s in breakpoints:
        if x in seen_x:
            continue
        seen_x.add(x)
        deduped.append((x, s))
    if len(deduped) < 2:
        result.rejection_reason = "Degenerate breakpoint set (collapsed values)"
        return result
    breakpoints = deduped

    # Generalization gate — score train and holdout, correlate with PnL.
    train_scored = [interp_breakpoints(v, breakpoints) or 50.0 for v in train_values]
    holdout_scored = [interp_breakpoints(v, breakpoints) or 50.0 for v in holdout_values]
    train_r = pearson_r(train_scored, train_pnls)
    holdout_r = pearson_r(holdout_scored, holdout_pnls)
    gap = abs(train_r - holdout_r)

    result.breakpoints = breakpoints
    result.train_r = train_r
    result.holdout_r = holdout_r
    result.gap = gap
    result.tercile_breakpoints = (b1, b2)
    result.tercile_clean_hr_rates = (clean_rates[0], clean_rates[1], clean_rates[2])
    result.tercile_mean_pnl = (mean_pnls[0], mean_pnls[1], mean_pnls[2])
    result.raw_train_r_with_pnl = train_r
    result.raw_holdout_r_with_pnl = holdout_r

    if gap > gap_threshold:
        result.rejection_reason = (
            f"Generalization gap {gap:.3f} > {gap_threshold:.3f} "
            f"(train_r={train_r:.3f}, holdout_r={holdout_r:.3f})"
        )
        return result

    # Monotonicity check on weighted clean HR rate per tercile
    monotonic_inc = clean_rates[0] <= clean_rates[1] <= clean_rates[2]
    monotonic_dec = clean_rates[0] >= clean_rates[1] >= clean_rates[2]
    if not (monotonic_inc or monotonic_dec):
        result.rejection_reason = (
            f"Non-monotonic clean HR rates across terciles: "
            f"{[round(r, 4) for r in clean_rates]}"
        )
        return result

    result.accepted = True
    return result


# ============================================================================
# Categorical fitter
# ============================================================================


def fit_categorical_subscore(
    spec: SubscoreSpec,
    train_categories: list[str],
    train_weights: list[float],
    train_flags: list[bool],
    train_pnls: list[float],
    holdout_categories: list[str],
    holdout_pnls: list[float],
    hr_weight: float,
    pnl_weight: float,
    gap_threshold: float,
    min_category_n: int = 50,
) -> FitResult:
    result = FitResult(spec=spec, train_n=len(train_categories), holdout_n=len(holdout_categories))

    # Build per-category stats on train
    cats: dict[str, list[int]] = {}
    for idx, c in enumerate(train_categories):
        cats.setdefault(c, []).append(idx)

    if not cats:
        result.rejection_reason = "No categories present"
        return result

    cat_clean_rate: dict[str, float] = {}
    cat_mean_pnl: dict[str, float] = {}
    keep_cats: list[str] = []
    for c, idxs in cats.items():
        if len(idxs) < min_category_n:
            continue
        flags_c = [train_flags[i] for i in idxs]
        weights_c = [train_weights[i] for i in idxs]
        pnls_c = [train_pnls[i] for i in idxs]
        cat_clean_rate[c] = weighted_fraction(flags_c, weights_c)
        cat_mean_pnl[c] = weighted_mean(pnls_c, weights_c)
        keep_cats.append(c)

    if len(keep_cats) < 2:
        result.rejection_reason = (
            f"Too few categories with ≥{min_category_n} rows ({len(keep_cats)})"
        )
        return result

    # Map categories to scores in [10, 90] using HR-weighted composite
    clean_vals = [cat_clean_rate[c] for c in keep_cats]
    pnl_vals = [cat_mean_pnl[c] for c in keep_cats]
    norm_clean = _normalize_to_unit(clean_vals)
    norm_pnl = _normalize_to_unit(pnl_vals)
    composite = [hr_weight * c + pnl_weight * p for c, p in zip(norm_clean, norm_pnl)]
    mapped = _linear_map(composite, 10.0, 90.0)
    category_scores = {c: round(s, 2) for c, s in zip(keep_cats, mapped)}

    # Generalization gate
    train_scored = [
        score_categorical(c, category_scores, default=50.0) for c in train_categories
    ]
    holdout_scored = [
        score_categorical(c, category_scores, default=50.0) for c in holdout_categories
    ]
    # Pearson needs equal-length lists with no Nones
    train_pairs = [
        (s, p)
        for s, p in zip(train_scored, train_pnls)
        if s is not None and p is not None
    ]
    holdout_pairs = [
        (s, p)
        for s, p in zip(holdout_scored, holdout_pnls)
        if s is not None and p is not None
    ]
    if len(train_pairs) < 30 or len(holdout_pairs) < 30:
        result.rejection_reason = "Too few non-null categorical pairs after scoring"
        return result
    train_r = pearson_r([p[0] for p in train_pairs], [p[1] for p in train_pairs])
    holdout_r = pearson_r([p[0] for p in holdout_pairs], [p[1] for p in holdout_pairs])
    gap = abs(train_r - holdout_r)

    result.category_scores = category_scores
    result.train_r = train_r
    result.holdout_r = holdout_r
    result.gap = gap
    result.raw_train_r_with_pnl = train_r
    result.raw_holdout_r_with_pnl = holdout_r

    if gap > gap_threshold:
        result.rejection_reason = (
            f"Generalization gap {gap:.3f} > {gap_threshold:.3f} "
            f"(train_r={train_r:.3f}, holdout_r={holdout_r:.3f})"
        )
        return result

    result.accepted = True
    return result


# ============================================================================
# Per-pillar fitting orchestration
# ============================================================================


def _extract_columns(
    rows: list[dict[str, Any]],
    spec: SubscoreSpec,
) -> tuple[list[Any], list[str], list[bool], list[float]]:
    """Extract (raw_value, entry_date, clean_hr, pnl_pct) per row, dropping rows
    where the source column is None for this subscore.
    """
    raws: list[Any] = []
    dates: list[str] = []
    flags: list[bool] = []
    pnls: list[float] = []
    for row in rows:
        v = row.get(spec.source_column)
        if v is None:
            continue
        if not spec.is_categorical:
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            # Convergence count and other discrete features may legitimately be 0;
            # only skip NaN/inf
            if math.isnan(v) or math.isinf(v):
                continue
        else:
            v = str(v)
        date = row.get("entry_date") or ""
        pnl = row.get("current_pnl_pct")
        try:
            pnl = float(pnl) if pnl is not None else None
        except (TypeError, ValueError):
            pnl = None
        if pnl is None:
            continue
        raws.append(v)
        dates.append(date)
        flags.append(bool(row.get("is_clean_hr")))
        pnls.append(pnl)
    return raws, dates, flags, pnls


def fit_pillar(
    pillar_id: str,
    pillar_name: str,
    specs: list[SubscoreSpec],
    train_rows: list[dict[str, Any]],
    holdout_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[FitResult], list[FitResult]]:
    """Fit every subscore in a pillar. Returns (accepted, all_results)."""
    log.info(f"--- Fitting pillar {pillar_id} ({pillar_name}) ---")
    accepted: list[FitResult] = []
    all_results: list[FitResult] = []
    ref_date = (
        datetime.strptime(args.ref_date, "%Y-%m-%d") if args.ref_date else None
    )

    for spec in specs:
        train_raws, train_dates, train_flags, train_pnls = _extract_columns(train_rows, spec)
        holdout_raws, _, _, holdout_pnls = _extract_columns(holdout_rows, spec)

        # Recency-weight training rows
        train_weights = exponential_weights(train_dates, args.recency_halflife, ref_date)

        if spec.is_categorical:
            result = fit_categorical_subscore(
                spec=spec,
                train_categories=[str(v) for v in train_raws],
                train_weights=train_weights,
                train_flags=train_flags,
                train_pnls=train_pnls,
                holdout_categories=[str(v) for v in holdout_raws],
                holdout_pnls=holdout_pnls,
                hr_weight=args.hr_weight,
                pnl_weight=1.0 - args.hr_weight,
                gap_threshold=args.gap_threshold,
            )
        else:
            result = fit_numeric_subscore(
                spec=spec,
                train_values=[float(v) for v in train_raws],
                train_weights=train_weights,
                train_flags=train_flags,
                train_pnls=train_pnls,
                holdout_values=[float(v) for v in holdout_raws],
                holdout_pnls=holdout_pnls,
                hr_weight=args.hr_weight,
                pnl_weight=1.0 - args.hr_weight,
                gap_threshold=args.gap_threshold,
            )

        all_results.append(result)
        if result.accepted:
            accepted.append(result)
            log.info(
                f"  ACCEPT  {spec.subscore_id:<25} train_r={result.train_r:+.3f} "
                f"holdout_r={result.holdout_r:+.3f} gap={result.gap:.3f}"
            )
        else:
            log.warning(
                f"  REJECT  {spec.subscore_id:<25} {result.rejection_reason}"
            )

    return accepted, all_results


# ============================================================================
# Build PolicyConfig-shaped JSON
# ============================================================================


def build_pillar_payload(
    pillar_id: str,
    pillar_name: str,
    accepted: list[FitResult],
) -> Optional[dict[str, Any]]:
    """Build the PillarConfigV2 dict for one pillar.

    Renormalizes the surviving subscore weights so they sum to exactly 1.0.
    Returns None if no subscores were accepted (caller should treat that
    as a halt condition).
    """
    if not accepted:
        return None

    total_target = sum(r.spec.target_weight for r in accepted)
    if total_target <= 0:
        return None

    numeric: list[dict[str, Any]] = []
    categorical: list[dict[str, Any]] = []
    for r in accepted:
        weight = round(r.spec.target_weight / total_target, 4)
        if r.spec.is_categorical:
            categorical.append(
                {
                    "subscore_id": r.spec.subscore_id,
                    "display_name": r.spec.display_name,
                    "feature_field": r.spec.feature_field,
                    "weight": weight,
                    "category_scores": r.category_scores or {},
                    "default_score": 50.0,
                }
            )
        else:
            numeric.append(
                {
                    "subscore_id": r.spec.subscore_id,
                    "display_name": r.spec.display_name,
                    "feature_field": r.spec.feature_field,
                    "weight": weight,
                    "breakpoints": [
                        {"value": round(x, 6), "score": round(s, 2)}
                        for x, s in (r.breakpoints or [])
                    ],
                    "source_tier": r.spec.source_tier,
                }
            )

    # Fix any rounding drift on the weights
    all_subs = numeric + categorical
    drift = 1.0 - sum(s["weight"] for s in all_subs)
    if abs(drift) > 1e-6 and all_subs:
        biggest = max(all_subs, key=lambda s: s["weight"])
        biggest["weight"] = round(biggest["weight"] + drift, 4)

    return {
        "pillar_id": pillar_id,
        "display_name": pillar_name,
        "description": (
            "Setup Pocket — pocket-fit features (DTE, entry price, scanner, "
            "delta, convergence) that identify where home runs live."
            if pillar_id == "SETUP_QUALITY"
            else f"{pillar_name} — quality filter within pocket (Policy v3.1.0)."
        ),
        "numeric_subscores": numeric,
        "categorical_subscores": categorical,
    }


# ============================================================================
# Acceptance gate (Phase 3 acceptance criteria from the implementation plan)
# ============================================================================


def check_phase3_acceptance(
    accepted_by_pillar: dict[str, list[FitResult]],
    setup_pocket_target_weight_total: float = 1.0,
) -> tuple[bool, list[str]]:
    """Verify the four acceptance criteria. Returns (ok, list_of_failures)."""
    failures: list[str] = []

    total_accepted = sum(len(v) for v in accepted_by_pillar.values())
    if total_accepted < 6:
        failures.append(
            f"Acceptance #1: total accepted subscores {total_accepted} < 6"
        )

    setup_accepted = accepted_by_pillar.get("SETUP_QUALITY", [])
    setup_target_sum = sum(r.spec.target_weight for r in setup_accepted)
    if setup_target_sum < 0.60:
        failures.append(
            f"Acceptance #2: Setup Pocket accepted weight share "
            f"{setup_target_sum:.3f} < 0.60"
        )

    return (not failures), failures


# ============================================================================
# Markdown design report
# ============================================================================


def write_design_report(
    args: argparse.Namespace,
    train_n: int,
    holdout_n: int,
    results_by_pillar: dict[str, list[FitResult]],
    accepted_by_pillar: dict[str, list[FitResult]],
    accepted_passes: bool,
    acceptance_failures: list[str],
) -> None:
    lines: list[str] = []
    lines.append("# Policy v3.1.0 — Pillar Fitting Design Report")
    lines.append("")
    lines.append(f"- Train rows: **{train_n}**")
    lines.append(f"- Holdout rows: **{holdout_n}**")
    lines.append(f"- HR weight: {args.hr_weight}, PnL weight: {1 - args.hr_weight:.2f}")
    lines.append(f"- Recency half-life: {args.recency_halflife} days")
    lines.append(f"- Generalization gap threshold: {args.gap_threshold}")
    lines.append(f"- Reference date: {args.ref_date or '(auto: max train date)'}")
    lines.append("")
    lines.append(
        "## Acceptance: " + ("✅ PASS" if accepted_passes else "❌ FAIL")
    )
    if acceptance_failures:
        for f in acceptance_failures:
            lines.append(f"- {f}")
    lines.append("")

    for pillar_id, (pillar_name, _) in PILLAR_LAYOUT.items():
        lines.append(f"## {pillar_name} ({pillar_id})")
        lines.append("")
        results = results_by_pillar.get(pillar_id, [])
        accepted = accepted_by_pillar.get(pillar_id, [])
        accepted_total_w = sum(r.spec.target_weight for r in accepted)
        lines.append(f"Accepted subscores: {len(accepted)}/{len(results)}  "
                     f"(target weight share: {accepted_total_w:.2f})")
        lines.append("")
        lines.append("| Subscore | n_train | n_holdout | train_r | holdout_r | gap | status |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in results:
            status = "✅ ACCEPT" if r.accepted else f"❌ {r.rejection_reason or 'unknown'}"
            lines.append(
                f"| {r.spec.subscore_id} | {r.train_n} | {r.holdout_n} | "
                f"{r.train_r:+.3f} | {r.holdout_r:+.3f} | {r.gap:.3f} | {status} |"
            )
        lines.append("")

        # Per-tercile / per-category detail for accepted
        for r in accepted:
            if r.spec.is_categorical:
                lines.append(f"### {r.spec.subscore_id} (categorical)")
                lines.append("")
                lines.append("| Category | Score |")
                lines.append("|---|---|")
                for cat, score in sorted(
                    (r.category_scores or {}).items(), key=lambda x: -x[1]
                ):
                    lines.append(f"| {cat} | {score:.1f} |")
                lines.append("")
            elif r.tercile_breakpoints and r.tercile_clean_hr_rates and r.tercile_mean_pnl:
                b1, b2 = r.tercile_breakpoints
                cr = r.tercile_clean_hr_rates
                mp = r.tercile_mean_pnl
                lines.append(f"### {r.spec.subscore_id} (numeric)")
                lines.append("")
                lines.append(f"Tercile boundaries: b1={b1:.4f}, b2={b2:.4f}")
                lines.append("")
                lines.append("| Tercile | Range | Clean HR rate (weighted) | Mean PnL (weighted) |")
                lines.append("|---|---|---|---|")
                lines.append(f"| T1 | < {b1:.4f} | {cr[0] * 100:.2f}% | {mp[0]:+.2f}% |")
                lines.append(f"| T2 | {b1:.4f} – {b2:.4f} | {cr[1] * 100:.2f}% | {mp[1]:+.2f}% |")
                lines.append(f"| T3 | ≥ {b2:.4f} | {cr[2] * 100:.2f}% | {mp[2]:+.2f}% |")
                lines.append("")
                if r.breakpoints:
                    lines.append("Final breakpoints:")
                    lines.append("")
                    for x, s in r.breakpoints:
                        lines.append(f"- value={x:.4f}  score={s:.2f}")
                    lines.append("")

    DESIGN_REPORT_PATH.write_text("\n".join(lines))
    log.info(f"Design report written to {DESIGN_REPORT_PATH}")


# ============================================================================
# Main
# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit Policy v3.1.0 pillars")
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--hr-weight", type=float, default=0.7)
    parser.add_argument("--recency-halflife", type=float, default=30.0)
    parser.add_argument("--gap-threshold", type=float, default=0.05)
    parser.add_argument(
        "--ref-date",
        type=str,
        default=None,
        help="Reference date for recency weights (default: max train entry_date)",
    )
    args = parser.parse_args()

    log.info(f"Loading train from {args.train}")
    train_rows = json.loads(args.train.read_text())
    log.info(f"  {len(train_rows)} rows")
    log.info(f"Loading holdout from {args.holdout}")
    holdout_rows = json.loads(args.holdout.read_text())
    log.info(f"  {len(holdout_rows)} rows")

    results_by_pillar: dict[str, list[FitResult]] = {}
    accepted_by_pillar: dict[str, list[FitResult]] = {}

    for pillar_id, (pillar_name, specs) in PILLAR_LAYOUT.items():
        accepted, results = fit_pillar(
            pillar_id, pillar_name, specs, train_rows, holdout_rows, args
        )
        results_by_pillar[pillar_id] = results
        accepted_by_pillar[pillar_id] = accepted

    accepted_passes, acceptance_failures = check_phase3_acceptance(accepted_by_pillar)

    log.info("=" * 60)
    log.info(f"PHASE 3 ACCEPTANCE: {'PASS' if accepted_passes else 'FAIL'}")
    for f in acceptance_failures:
        log.warning(f"  - {f}")

    write_design_report(
        args, len(train_rows), len(holdout_rows),
        results_by_pillar, accepted_by_pillar, accepted_passes, acceptance_failures,
    )

    if not accepted_passes:
        log.error("Phase 3 acceptance criteria failed; not writing fitted policy JSON")
        return 2

    # Build the Policy v3.1.0 fitted-pillars JSON
    pillars_payload: dict[str, Any] = {
        "weights": {
            "premium_leverage": 0.25,
            "underlying_behavior": 0.35,
            "setup_quality": 0.40,
        },
    }
    for pillar_id, (pillar_name, _) in PILLAR_LAYOUT.items():
        key = pillar_id.lower()
        payload = build_pillar_payload(pillar_id, pillar_name, accepted_by_pillar[pillar_id])
        if payload is None:
            log.error(f"Pillar {pillar_id} has no accepted subscores — cannot build policy")
            return 3
        pillars_payload[key] = payload

    fitted = {
        "config": {"pillars": pillars_payload},
        "version": "3.1.0",
        "created_by": "fit_pillars_v31.py",
        "fit_metadata": {
            "hr_weight": args.hr_weight,
            "recency_halflife_days": args.recency_halflife,
            "gap_threshold": args.gap_threshold,
            "train_rows": len(train_rows),
            "holdout_rows": len(holdout_rows),
            "ref_date": args.ref_date,
        },
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FITTED_POLICY_PATH.write_text(json.dumps(fitted, indent=2))
    log.info(f"Fitted policy written to {FITTED_POLICY_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
