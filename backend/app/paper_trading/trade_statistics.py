"""Pre-compute comparative statistics for custom analysis LLM prompts.

Computes feature distributions for top-performing trades vs the rest,
giving the LLM quantitative backing before it analyzes the raw CSV data.
"""

from __future__ import annotations

import logging

from app.core.schemas import PaperPosition

logger = logging.getLogger(__name__)


def _safe_median(values: list[float]) -> float | None:
    """Compute median of non-empty list, or None."""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _safe_mean(values: list[float]) -> float | None:
    """Compute mean of non-empty list, or None."""
    if not values:
        return None
    return sum(values) / len(values)


def _extract_numeric(positions: list[PaperPosition], attr: str) -> list[float]:
    """Extract non-None numeric values for an attribute."""
    vals = []
    for p in positions:
        v = getattr(p, attr, None)
        if v is not None:
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass
    return vals


def _scanner_distribution(positions: list[PaperPosition]) -> dict[str, int]:
    """Count positions by scanner source."""
    counts: dict[str, int] = {}
    for p in positions:
        scanner = p.scanner_source or "UNKNOWN"
        counts[scanner] = counts.get(scanner, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _fmt_stat(val: float | None, decimals: int = 1) -> str:
    """Format a stat for text output."""
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}"


# Features to compare between top performers and the rest
COMPARISON_FEATURES = [
    ("conviction_score", "Conviction Score", 0),
    ("pillar_premium_leverage", "Premium Leverage Pillar", 0),
    ("pillar_underlying_behavior", "Underlying Behavior Pillar", 0),
    ("pillar_setup_quality", "Setup Quality Pillar", 0),
    ("pillar_directional_conviction", "Directional Conviction Pillar", 0),
    ("pillar_move_potential", "Move Potential Pillar", 0),
    ("pillar_trade_structure", "Trade Structure Pillar", 0),
    ("entry_iv", "Entry IV", 2),
    ("entry_iv_percentile", "IV Percentile", 0),
    ("entry_iv_rv_ratio", "IV/RV Ratio", 2),
    ("entry_theta_adjusted_edge", "Theta-Adj Edge", 2),
    ("gate_margin", "Gate Margin", 2),
    ("entry_moneyness_pct", "Moneyness %", 1),
    ("entry_spread_pct", "Spread %", 2),
    ("dte_at_entry", "DTE", 0),
    ("convergence_count", "Scanner Convergence", 0),
    ("entry_atr14_pct", "ATR %", 1),
    ("entry_rs_20d", "Relative Strength", 1),
    ("entry_feasibility_ratio", "Feasibility Ratio", 1),
    ("days_held", "Days Held", 0),
]


def compute_comparative_statistics(
    positions: list[PaperPosition],
    threshold_pct: float = 200.0,
) -> str:
    """Compute comparative statistics between top performers and the rest.

    Args:
        positions: All closed paper positions
        threshold_pct: Return threshold for "top performer" classification

    Returns:
        Concise text summary (~400-600 tokens) for LLM prompt inclusion
    """
    if not positions:
        return "No closed positions available for statistical analysis."

    top = [p for p in positions if p.current_pnl_pct >= threshold_pct]
    rest = [p for p in positions if p.current_pnl_pct < threshold_pct]
    winners = [p for p in positions if p.current_pnl_pct > 0]
    losers = [p for p in positions if p.current_pnl_pct <= 0]

    lines = [
        f"## Pre-Computed Statistics (threshold: {threshold_pct}%+ return)",
        f"- Total closed trades: {len(positions)}",
        f"- Winners: {len(winners)} ({len(winners)/len(positions)*100:.0f}%), "
        f"Losers: {len(losers)} ({len(losers)/len(positions)*100:.0f}%)",
        f"- Top performers ({threshold_pct}%+): {len(top)} "
        f"({len(top)/len(positions)*100:.1f}% of all trades)",
    ]

    if not top:
        lines.append(f"\nNo trades found with {threshold_pct}%+ return. "
                      "Consider lowering the threshold in your analysis.")
        return "\n".join(lines)

    # Return distribution of top performers
    returns = sorted([p.current_pnl_pct for p in top])
    lines.append(
        f"- Top performer returns: min={returns[0]:.0f}%, "
        f"median={_safe_median(returns):.0f}%, max={returns[-1]:.0f}%"
    )

    # Scanner distribution for top performers
    top_scanners = _scanner_distribution(top)
    all_scanners = _scanner_distribution(positions)
    lines.append("\n### Scanner Distribution (top performers vs all)")
    for scanner, count in top_scanners.items():
        pct_of_top = count / len(top) * 100
        all_count = all_scanners.get(scanner, 0)
        pct_of_all = all_count / len(positions) * 100
        lines.append(f"- {scanner}: {pct_of_top:.0f}% of top vs {pct_of_all:.0f}% of all")

    # Feature comparison table
    lines.append("\n### Feature Comparison: Top Performers vs Rest (median values)")
    lines.append("Feature | Top Performers | Rest | Delta")
    lines.append("---|---|---|---")

    for attr, label, decimals in COMPARISON_FEATURES:
        top_vals = _extract_numeric(top, attr)
        rest_vals = _extract_numeric(rest, attr)
        top_med = _safe_median(top_vals)
        rest_med = _safe_median(rest_vals)

        if top_med is not None and rest_med is not None and rest_med != 0:
            delta = top_med - rest_med
            delta_str = f"{delta:+.{decimals}f}"
        else:
            delta_str = "N/A"

        lines.append(
            f"{label} | {_fmt_stat(top_med, decimals)} | "
            f"{_fmt_stat(rest_med, decimals)} | {delta_str}"
        )

    # Verdict distribution for top performers
    top_verdicts: dict[str, int] = {}
    for p in top:
        v = str(getattr(p.verdict_at_entry, "value", p.verdict_at_entry))
        top_verdicts[v] = top_verdicts.get(v, 0) + 1
    if top_verdicts:
        lines.append("\n### Verdict at Entry (top performers)")
        for verdict, count in sorted(top_verdicts.items(), key=lambda x: -x[1]):
            lines.append(f"- {verdict}: {count} ({count/len(top)*100:.0f}%)")

    # Option type distribution
    top_types: dict[str, int] = {}
    for p in top:
        t = str(p.option_type or "UNKNOWN")
        top_types[t] = top_types.get(t, 0) + 1
    if top_types:
        lines.append("\n### Option Type (top performers)")
        for otype, count in sorted(top_types.items(), key=lambda x: -x[1]):
            lines.append(f"- {otype}: {count} ({count/len(top)*100:.0f}%)")

    return "\n".join(lines)
