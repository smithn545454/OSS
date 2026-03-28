"""LLM narrative prompt builder for feature importance analysis.

Converts pre-computed statistics into a compact prompt (~1,200 tokens)
for generating a trader-facing narrative summary. The LLM never sees
raw trade data — only aggregated statistics.
"""

from __future__ import annotations

from app.calibration.feature_importance_models import FeatureImportanceResult


def build_narrative_prompt(result: FeatureImportanceResult) -> str:
    """Build a prompt from pre-aggregated feature importance stats."""
    lines: list[str] = []

    lines.append(
        "You are analyzing the statistical performance of an options trading system's "
        "features to determine which entry signals predict profitable trades. "
        "Write a concise 2-3 paragraph analysis for a retail options trader."
    )
    lines.append("")

    # Overall context
    lines.append(
        f"OVERALL: {result.n_positions} closed positions, "
        f"{result.overall_win_rate:.1f}% win rate, "
        f"{result.overall_avg_return:.2f}% avg return, "
        f"period={result.period}, outcome={result.outcome}"
    )
    lines.append("")

    # Top predictive features
    lines.append("TOP PREDICTIVE FEATURES (by |correlation with P&L|):")
    for i, f in enumerate(result.features[:10], 1):
        sig = "***" if f.is_significant else ""
        win_str = f"{f.win_mean:.2f}" if f.win_mean is not None else "N/A"
        loss_str = f"{f.loss_mean:.2f}" if f.loss_mean is not None else "N/A"
        lines.append(
            f"  {i}. {f.display_name}: r={f.pearson_r:+.3f}, p={f.p_value:.3f}{sig}, "
            f"direction={f.direction}, wins_avg={win_str}, losses_avg={loss_str}"
        )
        if f.quintiles:
            qwrs = " ".join(f"Q{q.quintile}={q.win_rate:.0f}%" for q in f.quintiles)
            lines.append(f"     Quintile WRs: {qwrs}")
    lines.append("")

    # Strongest interactions
    if result.interactions:
        lines.append("STRONGEST FEATURE INTERACTIONS:")
        for pair in result.interactions[:5]:
            lines.append(
                f"  - {pair.feature_a_display} + {pair.feature_b_display}: "
                f"both high = {pair.both_high_wr:.1f}% WR (n={pair.both_high_n}), "
                f"lift: {pair.interaction_lift:+.1f}pp vs single feature"
            )
        lines.append("")

    # Weight mismatches
    mismatches = [w for w in result.weight_comparisons if w.recommendation != "aligned"]
    if mismatches:
        lines.append("WEIGHT MISMATCHES (current vs empirical):")
        for w in mismatches:
            lines.append(
                f"  - {w.pillar}/{w.subscore}: current={w.current_weight:.0%}, "
                f"empirical={w.empirical_importance:.0%}, delta={w.delta:+.0%} "
                f"→ {w.recommendation}"
            )
        lines.append("")

    # Temporal stability
    if len(result.temporal_windows) >= 2:
        first_w = result.temporal_windows[0]
        last_w = result.temporal_windows[-1]
        lines.append("TEMPORAL STABILITY (first vs latest window):")
        for feat_name in list(first_w.feature_correlations.keys())[:5]:
            r_first = first_w.feature_correlations.get(feat_name)
            r_last = last_w.feature_correlations.get(feat_name)
            if r_first is not None and r_last is not None:
                direction = "stable" if abs(r_last - r_first) < 0.1 else (
                    "strengthening" if abs(r_last) > abs(r_first) else "weakening"
                )
                # Look up display name
                display = feat_name
                for f in result.features:
                    if f.feature_name == feat_name:
                        display = f.display_name
                        break
                lines.append(
                    f"  - {display}: r {r_first:+.3f} → {r_last:+.3f} ({direction})"
                )
        lines.append("")

    lines.append(
        "Focus on: (1) which features most reliably predict profit and why, "
        "(2) feature combinations that create outsized edge, "
        "(3) weight adjustments that would improve the scoring system, "
        "(4) any features losing predictive power. "
        "Be specific about what the trader should DO differently based on this analysis."
    )

    return "\n".join(lines)


def parse_narrative_response(response_text: str) -> str:
    """Clean up the LLM's narrative response."""
    return response_text.strip()
