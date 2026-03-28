"""Feature importance statistical engine.

Pure computation module — no database imports, no side effects.
Takes lists of PaperPosition objects and returns analysis results.

All statistics computed with Python standard library (math, statistics).
No numpy/scipy/pandas required. Handles 10,000+ positions in <10 seconds.
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from app.calibration.feature_importance_models import (
    FeatureImportanceResult,
    FeaturePairStats,
    FeatureStats,
    QuintileStats,
    TemporalWindow,
    WeightComparison,
)
from app.core.schemas import PaperPosition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature Registry
# ---------------------------------------------------------------------------
# (field_name, display_name, pillar, subscore_name, current_weight, higher_is_better)
# subscore_name and current_weight link to PolicyConfig pillar weights.
# higher_is_better determines direction interpretation for the feature.

FeatureRegistryEntry = tuple[
    str, str, Optional[str], Optional[str], Optional[float], Optional[bool]
]

FEATURE_REGISTRY: list[FeatureRegistryEntry] = [
    ("entry_iv_percentile", "IV Percentile", "volatility", "iv_percentile", 0.25, False),
    ("entry_iv_rv_ratio", "IV/RV Ratio", "volatility", "iv_vs_rv", 0.35, False),
    (
        "entry_theta_adjusted_edge",
        "Theta-Adjusted Edge",
        "volatility",
        "theta_adjusted_edge",
        0.20,
        True,
    ),
    ("entry_rs_20d", "Relative Strength (20d)", "directional", "relative_strength", 0.15, True),
    ("entry_atr14_pct", "ATR % (14d)", "directional", None, None, None),
    ("entry_feasibility_ratio", "Feasibility Ratio", "volatility", None, None, False),
    ("entry_moneyness_pct", "Moneyness %", "structure", None, None, None),
    ("entry_spread_pct", "Spread %", "structure", "spread", 0.30, False),
    ("entry_open_interest", "Open Interest", "structure", "open_interest", 0.25, True),
    ("entry_volume", "Volume", "structure", "volume", 0.20, True),
    ("entry_iv", "Entry IV", "volatility", None, None, None),
    ("entry_delta", "Entry Delta", "directional", None, None, None),
    ("dte_at_entry", "DTE at Entry", "structure", "theta_burden", 0.15, True),
    ("gate_margin", "Gate Margin", None, None, None, True),
    ("conviction_score", "Conviction Score", None, None, None, True),
    ("convergence_count", "Scanner Convergence", None, None, None, True),
    ("pillar_directional", "Directional Score", "directional", None, None, True),
    ("pillar_volatility", "Volatility Score", "volatility", None, None, True),
    ("pillar_structure", "Structure Score", "structure", None, None, True),
    ("entry_underlying_price", "Underlying Price", None, None, None, None),
]

# Min sample size for reliable statistics
MIN_SAMPLE = 20
MIN_QUINTILE_SAMPLE = 10


# ---------------------------------------------------------------------------
# Core statistical functions (pure Python)
# ---------------------------------------------------------------------------


def _pearson_r(xs: list[float], ys: list[float]) -> float:
    """Compute Pearson correlation coefficient. Returns 0.0 if degenerate."""
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


def _analytical_p_value(observed_r: float, n: int) -> float:
    """Compute two-tailed p-value for Pearson r via t-distribution.

    Uses the t-statistic: t = r × sqrt(n-2) / sqrt(1-r²)
    and approximates the CDF via math.erf (normal approximation,
    accurate for n > 30). O(1) per call — no shuffling needed.
    """
    if n < 3:
        return 1.0
    r_sq = observed_r ** 2
    if r_sq >= 1.0:
        return 0.0  # perfect correlation
    t = abs(observed_r) * math.sqrt(n - 2) / math.sqrt(1.0 - r_sq)
    # Two-tailed p via normal CDF approximation (accurate for df > 30)
    p = math.erfc(t / math.sqrt(2.0))
    return min(max(p, 0.0), 1.0)


def _cohens_d(group_a: list[float], group_b: list[float]) -> float:
    """Compute Cohen's d effect size between two groups."""
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


def _safe_mean(vals: list[float]) -> Optional[float]:
    return statistics.mean(vals) if vals else None


def _safe_median(vals: list[float]) -> float:
    return statistics.median(vals) if vals else 0.0


def _win_rate(pnls: list[float]) -> float:
    if not pnls:
        return 0.0
    wins = sum(1 for p in pnls if p > 0)
    return (wins / len(pnls)) * 100


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def _extract_pairs(
    positions: list[PaperPosition],
    feature_field: str,
    outcome_field: str,
) -> list[tuple[float, float]]:
    """Extract (feature_value, outcome) pairs, skipping Nones."""
    pairs: list[tuple[float, float]] = []
    for pos in positions:
        feat_val = getattr(pos, feature_field, None)
        out_val = getattr(pos, outcome_field, None)
        if feat_val is not None and out_val is not None:
            try:
                pairs.append((float(feat_val), float(out_val)))
            except (TypeError, ValueError):
                continue
    return pairs


# ---------------------------------------------------------------------------
# Computation 1: Per-feature stats + quintiles
# ---------------------------------------------------------------------------


def _compute_quintiles(pairs: list[tuple[float, float]]) -> list[QuintileStats]:
    """Split trades into 5 equal buckets by feature value."""
    if len(pairs) < MIN_QUINTILE_SAMPLE * 5:
        return []

    sorted_pairs = sorted(pairs, key=lambda p: p[0])
    n = len(sorted_pairs)
    bucket_size = n // 5
    quintiles: list[QuintileStats] = []

    for q in range(5):
        start = q * bucket_size
        end = start + bucket_size if q < 4 else n  # last bucket gets remainder
        bucket = sorted_pairs[start:end]
        if not bucket:
            continue

        feat_vals = [p[0] for p in bucket]
        pnls = [p[1] for p in bucket]

        quintiles.append(
            QuintileStats(
                quintile=q + 1,
                range_low=min(feat_vals),
                range_high=max(feat_vals),
                n=len(bucket),
                win_rate=_win_rate(pnls),
                avg_return=statistics.mean(pnls),
                median_return=_safe_median(pnls),
            )
        )

    return quintiles


def compute_feature_stats(
    positions: list[PaperPosition],
    outcome_field: str = "current_pnl_pct",
) -> list[FeatureStats]:
    """Compute correlation, significance, win/loss split, and quintiles per feature."""
    results: list[FeatureStats] = []

    for field_name, display_name, pillar, _, _, higher_hint in FEATURE_REGISTRY:
        pairs = _extract_pairs(positions, field_name, outcome_field)
        if len(pairs) < MIN_SAMPLE:
            continue

        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]

        r = _pearson_r(xs, ys)
        p_val = _analytical_p_value(r, len(xs))

        # Win/loss split
        win_vals = [x for x, y in pairs if y > 0]
        loss_vals = [x for x, y in pairs if y <= 0]
        win_mean = _safe_mean(win_vals)
        loss_mean = _safe_mean(loss_vals)
        diff = None
        if win_mean is not None and loss_mean is not None:
            diff = win_mean - loss_mean

        effect = _cohens_d(win_vals, loss_vals) if win_vals and loss_vals else 0.0

        # Determine direction from data
        if higher_hint is True:
            direction = "higher_is_better"
        elif higher_hint is False:
            direction = "lower_is_better"
        elif r > 0.05:
            direction = "higher_is_better"
        elif r < -0.05:
            direction = "lower_is_better"
        else:
            direction = "unclear"

        quintiles = _compute_quintiles(pairs)

        results.append(
            FeatureStats(
                feature_name=field_name,
                display_name=display_name,
                pillar=pillar,
                n_valid=len(pairs),
                pearson_r=r,
                p_value=p_val,
                is_significant=p_val < 0.05,
                win_mean=win_mean,
                loss_mean=loss_mean,
                difference=diff,
                effect_size=effect,
                direction=direction,
                quintiles=quintiles,
            )
        )

    # Sort by absolute correlation (most predictive first)
    results.sort(key=lambda s: abs(s.pearson_r), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Computation 2: Feature pair interactions
# ---------------------------------------------------------------------------


def compute_interactions(
    positions: list[PaperPosition],
    top_features: list[FeatureStats],
    outcome_field: str = "current_pnl_pct",
    max_pairs: int = 15,
) -> list[FeaturePairStats]:
    """Test top features for interaction effects using median-split quadrants."""
    # Use top 10 significant features
    sig_features = [f for f in top_features if f.is_significant][:10]
    if len(sig_features) < 2:
        return []

    # Precompute medians and value maps
    feature_data: dict[str, tuple[float, dict[str, float]]] = {}
    for feat in sig_features:
        pairs = _extract_pairs(positions, feat.feature_name, outcome_field)
        if len(pairs) < MIN_SAMPLE:
            continue
        vals = [p[0] for p in pairs]
        med = _safe_median(vals)
        val_map = {}
        for pos in positions:
            fv = getattr(pos, feat.feature_name, None)
            if fv is not None:
                val_map[pos.position_id] = float(fv)
        feature_data[feat.feature_name] = (med, val_map)

    results: list[FeaturePairStats] = []
    display_names = {f.feature_name: f.display_name for f in sig_features}

    checked = 0
    for i, feat_a in enumerate(sig_features):
        if feat_a.feature_name not in feature_data:
            continue
        med_a, vals_a = feature_data[feat_a.feature_name]
        for feat_b in sig_features[i + 1 :]:
            if feat_b.feature_name not in feature_data:
                continue
            if checked >= max_pairs:
                break
            checked += 1

            med_b, vals_b = feature_data[feat_b.feature_name]

            # Split positions into 4 quadrants
            both_high_pnls: list[float] = []
            a_only_pnls: list[float] = []
            b_only_pnls: list[float] = []
            both_low_pnls: list[float] = []

            for pos in positions:
                pid = pos.position_id
                out_val = getattr(pos, outcome_field, None)
                if pid not in vals_a or pid not in vals_b or out_val is None:
                    continue

                pnl = float(out_val)
                a_high = vals_a[pid] >= med_a
                b_high = vals_b[pid] >= med_b

                if a_high and b_high:
                    both_high_pnls.append(pnl)
                elif a_high:
                    a_only_pnls.append(pnl)
                elif b_high:
                    b_only_pnls.append(pnl)
                else:
                    both_low_pnls.append(pnl)

            # Need minimum samples in each quadrant
            if (
                len(both_high_pnls) < 10
                or len(a_only_pnls) < 10
                or len(b_only_pnls) < 10
            ):
                continue

            bh_wr = _win_rate(both_high_pnls)
            ao_wr = _win_rate(a_only_pnls)
            bo_wr = _win_rate(b_only_pnls)
            bl_wr = _win_rate(both_low_pnls) if both_low_pnls else 0.0

            # Adjust direction: for "lower_is_better" features, flip the label
            # but keep the quadrant stats as computed
            lift = bh_wr - max(ao_wr, bo_wr)

            results.append(
                FeaturePairStats(
                    feature_a=feat_a.feature_name,
                    feature_a_display=display_names[feat_a.feature_name],
                    feature_b=feat_b.feature_name,
                    feature_b_display=display_names[feat_b.feature_name],
                    both_high_wr=bh_wr,
                    both_high_avg_return=statistics.mean(both_high_pnls),
                    both_high_n=len(both_high_pnls),
                    a_only_high_wr=ao_wr,
                    a_only_high_n=len(a_only_pnls),
                    b_only_high_wr=bo_wr,
                    b_only_high_n=len(b_only_pnls),
                    both_low_wr=bl_wr,
                    both_low_n=len(both_low_pnls),
                    interaction_lift=lift,
                )
            )

    # Sort by interaction lift (highest synergy first)
    results.sort(key=lambda p: p.interaction_lift, reverse=True)
    return results


# ---------------------------------------------------------------------------
# Computation 3: Weight validation
# ---------------------------------------------------------------------------


def compute_weight_validation(
    feature_stats: list[FeatureStats],
) -> list[WeightComparison]:
    """Compare empirical importance to current pillar weights."""
    # Build lookup from feature_name → (subscore, current_weight)
    weight_map: dict[str, tuple[str, str, float, str]] = {}
    for field_name, display, pillar, subscore, weight, _ in FEATURE_REGISTRY:
        if subscore is not None and weight is not None and pillar is not None:
            weight_map[field_name] = (pillar, subscore, weight, display)

    # Get empirical |r| for mapped features
    empirical: dict[str, tuple[str, str, float, str]] = {}
    for fs in feature_stats:
        if fs.feature_name in weight_map:
            pillar, subscore, current_w, display = weight_map[fs.feature_name]
            empirical[fs.feature_name] = (pillar, subscore, abs(fs.pearson_r), display)

    if not empirical:
        return []

    # Normalize within each pillar so empirical weights sum to 1.0
    pillar_totals: dict[str, float] = {}
    for _, (pillar, _, r_abs, _) in empirical.items():
        pillar_totals[pillar] = pillar_totals.get(pillar, 0.0) + r_abs

    results: list[WeightComparison] = []
    for field_name, (pillar, subscore, r_abs, display) in empirical.items():
        _, _, current_w, _ = weight_map[field_name]
        total = pillar_totals.get(pillar, 1.0)
        emp_weight = r_abs / total if total > 0 else 0.0

        delta = emp_weight - current_w
        if delta > 0.10:
            rec = "increase"
        elif delta < -0.10:
            rec = "decrease"
        else:
            rec = "aligned"

        results.append(
            WeightComparison(
                pillar=pillar,
                subscore=subscore,
                display_name=display,
                current_weight=current_w,
                empirical_importance=emp_weight,
                delta=delta,
                recommendation=rec,
            )
        )

    # Sort by pillar, then by delta magnitude
    results.sort(key=lambda w: (w.pillar, -abs(w.delta)))
    return results


# ---------------------------------------------------------------------------
# Computation 4: Temporal stability
# ---------------------------------------------------------------------------


def compute_temporal_stability(
    positions: list[PaperPosition],
    top_features: list[FeatureStats],
    outcome_field: str = "current_pnl_pct",
    window_days: int = 60,
    step_days: int = 14,
) -> list[TemporalWindow]:
    """Rolling window analysis of feature correlations over time."""
    top_names = [f.feature_name for f in top_features[:10] if f.is_significant]
    if not top_names:
        return []

    # Parse entry_date strings and sort by date
    dated_positions: list[tuple[str, PaperPosition]] = []
    for pos in positions:
        if pos.entry_date:
            dated_positions.append((pos.entry_date, pos))
    dated_positions.sort(key=lambda dp: dp[0])

    if not dated_positions:
        return []

    # Find date range
    first_date = datetime.strptime(dated_positions[0][0], "%Y-%m-%d")
    last_date = datetime.strptime(dated_positions[-1][0], "%Y-%m-%d")

    windows: list[TemporalWindow] = []
    current_start = first_date

    while current_start + timedelta(days=window_days) <= last_date:
        window_end = current_start + timedelta(days=window_days)
        start_str = current_start.strftime("%Y-%m-%d")
        end_str = window_end.strftime("%Y-%m-%d")

        # Filter positions in this window
        window_positions = [
            pos for date_str, pos in dated_positions if start_str <= date_str <= end_str
        ]

        if len(window_positions) < MIN_SAMPLE:
            current_start += timedelta(days=step_days)
            continue

        # Compute correlations for top features
        correlations: dict[str, Optional[float]] = {}
        for feat_name in top_names:
            pairs = _extract_pairs(window_positions, feat_name, outcome_field)
            if len(pairs) >= MIN_SAMPLE:
                xs = [p[0] for p in pairs]
                ys = [p[1] for p in pairs]
                correlations[feat_name] = _pearson_r(xs, ys)
            else:
                correlations[feat_name] = None

        windows.append(
            TemporalWindow(
                window_start=start_str,
                window_end=end_str,
                n_positions=len(window_positions),
                feature_correlations=correlations,
            )
        )

        current_start += timedelta(days=step_days)

    return windows


# ---------------------------------------------------------------------------
# Computation 5: Segment analysis
# ---------------------------------------------------------------------------


def compute_segment_analysis(
    positions: list[PaperPosition],
    outcome_field: str = "current_pnl_pct",
) -> dict[str, list[dict[str, Any]]]:
    """Run feature stats broken down by categorical segments."""
    segments: dict[str, list[dict[str, Any]]] = {}

    # Segment by option_type
    call_positions = [p for p in positions if str(p.option_type) == "CALL"]
    put_positions = [p for p in positions if str(p.option_type) == "PUT"]

    by_type: dict[str, list[dict[str, Any]]] = {}
    if len(call_positions) >= MIN_SAMPLE:
        call_stats = compute_feature_stats(call_positions, outcome_field)
        by_type["CALL"] = [f.to_dict() for f in call_stats]
    if len(put_positions) >= MIN_SAMPLE:
        put_stats = compute_feature_stats(put_positions, outcome_field)
        by_type["PUT"] = [f.to_dict() for f in put_stats]
    if by_type:
        segments["by_option_type"] = [{"segment": k, "features": v} for k, v in by_type.items()]

    # Segment by scanner_source
    scanners: dict[str, list[PaperPosition]] = {}
    for pos in positions:
        src = pos.scanner_source or "UNKNOWN"
        scanners.setdefault(src, []).append(pos)

    by_scanner: list[dict[str, Any]] = []
    for scanner, scanner_positions in scanners.items():
        if len(scanner_positions) >= MIN_SAMPLE:
            stats = compute_feature_stats(scanner_positions, outcome_field)
            by_scanner.append({"segment": scanner, "features": [f.to_dict() for f in stats]})
    if by_scanner:
        segments["by_scanner"] = by_scanner

    # Segment by verdict
    verdict_groups: dict[str, list[PaperPosition]] = {}
    for pos in positions:
        v = str(getattr(pos.verdict_at_entry, "value", pos.verdict_at_entry))
        verdict_groups.setdefault(v, []).append(pos)

    by_verdict: list[dict[str, Any]] = []
    for verdict, v_positions in verdict_groups.items():
        if len(v_positions) >= MIN_SAMPLE:
            stats = compute_feature_stats(v_positions, outcome_field)
            by_verdict.append({"segment": verdict, "features": [f.to_dict() for f in stats]})
    if by_verdict:
        segments["by_verdict"] = by_verdict

    return segments


# ---------------------------------------------------------------------------
# Top-level analysis runner
# ---------------------------------------------------------------------------


def run_feature_importance_analysis(
    positions: list[PaperPosition],
    period: str = "all",
    outcome: str = "pnl",
) -> FeatureImportanceResult:
    """Run the complete feature importance analysis.

    Args:
        positions: Closed paper positions to analyze
        period: Filter label (for metadata only — filtering done before calling)
        outcome: "pnl" uses current_pnl_pct, "mfe" uses max_favorable_excursion

    Returns:
        Complete FeatureImportanceResult with all computations.
    """
    outcome_field = (
        "max_favorable_excursion" if outcome == "mfe" else "current_pnl_pct"
    )

    analysis_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Overall stats
    pnls = [pos.current_pnl_pct for pos in positions if pos.current_pnl_pct is not None]
    overall_wr = _win_rate(pnls) if pnls else 0.0
    overall_avg = statistics.mean(pnls) if pnls else 0.0

    logger.info(
        "Feature importance analysis: %d positions, WR=%.1f%%, avg_ret=%.2f%%",
        len(positions),
        overall_wr,
        overall_avg,
    )

    # Run all computations
    features = compute_feature_stats(positions, outcome_field)
    interactions = compute_interactions(positions, features, outcome_field)
    weight_comparisons = compute_weight_validation(features)
    temporal = compute_temporal_stability(positions, features, outcome_field)
    segments = compute_segment_analysis(positions, outcome_field)

    logger.info(
        "Analysis complete: %d features scored, %d interactions, %d weight comparisons, "
        "%d temporal windows",
        len(features),
        len(interactions),
        len(weight_comparisons),
        len(temporal),
    )

    return FeatureImportanceResult(
        analysis_id=analysis_id,
        created_at=now,
        period=period,
        outcome=outcome,
        n_positions=len(positions),
        overall_win_rate=overall_wr,
        overall_avg_return=overall_avg,
        features=features,
        interactions=interactions,
        weight_comparisons=weight_comparisons,
        temporal_windows=temporal,
        segments=segments,
    )
