"""Scanner Performance Analysis — AI-powered analysis of why scanners underperform.

Pure computation module. Assembles data from paper positions into a structured
prompt for Claude, then parses the response. No database imports, no side effects.
"""

from __future__ import annotations

import json
import logging
from statistics import median
from typing import Any, Optional

from app.paper_trading.edge_intelligence import _compute_stats, _normalize_scanner, _val

logger = logging.getLogger(__name__)

# Gate ID → (GateConfig field name, operator description)
GATE_CONFIG_MAP = {
    "GATE_MIN_OPEN_INTEREST": ("min_open_interest", ">="),
    "GATE_MIN_VOLUME": ("min_volume", ">="),
    "GATE_MAX_SPREAD_PCT": ("max_spread_pct", "<="),
    "GATE_DTE_RANGE": ("dte_min / dte_max", "between"),
    "GATE_MOVE_SUFFICIENCY": ("move_sufficiency_max", "<="),
    "GATE_IV_PERCENTILE_MAX": ("iv_percentile_max", "<="),
    "GATE_BREAKOUT_VOLUME": ("breakout_volume_min", ">="),
    "GATE_GREEKS_COHERENCE": ("(structural validation)", "n/a"),
    "GATE_THETA_BURDEN_MAX": ("theta_burden_max", "<="),
}

KNOWN_SCANNERS = {
    "BREAKOUT", "BREAKDOWN", "COMPRESSION_EXPANSION",
    "CHEAP_OPTIONS", "UNUSUAL_VOLUME",
}

ANALYSIS_SYSTEM_PROMPT = """You are an expert options trading system analyst reviewing \
performance data for a specific scanner in an automated options evaluation pipeline.

The system has hard gates that filter evaluations before they can be APPROVED for \
paper trading. Trades that pass all gates are enrolled. You are analyzing the outcomes \
of those enrolled trades to understand why this scanner's win rate is low.

RULES:
- Every recommendation must include the EXACT current threshold value and a specific \
suggested new value.
- Cite the specific data provided to justify each recommendation (e.g., "losers avg IV \
was 0.82 vs winners at 0.45").
- Do not suggest changes that would dramatically reduce the overall system's selectivity \
— changes should be surgical, targeting this scanner's failure modes.
- If the scanner is actually performing acceptably (>55% WR), say so and suggest minimal \
or no changes.
- Consider sample size in your confidence assessment. Below 30 closed trades is low \
confidence. Below 100 is medium.
- Focus on the most impactful 2-3 recommendations, not an exhaustive list.

Respond with ONLY valid JSON matching the schema provided. No markdown, no commentary."""


def build_scanner_analysis_input(
    scanner_name: str,
    positions: list,
    gate_config_dict: dict[str, Any],
) -> dict[str, Any]:
    """Build structured data package for scanner analysis prompt.

    Args:
        scanner_name: Normalized scanner name (e.g., "UNUSUAL_VOLUME")
        positions: All closed PaperPosition objects (all scanners)
        gate_config_dict: Active policy gate config as dict

    Returns:
        Dict with all data needed for the prompt
    """
    # Split positions by scanner
    scanner_positions = [
        p for p in positions
        if _normalize_scanner(p.scanner_source) == scanner_name
        and _val(p.status) == "CLOSED"
    ]
    if not scanner_positions:
        return {"error": "no_data", "scanner_name": scanner_name}

    # Split into winners and losers
    winners = [p for p in scanner_positions if p.current_pnl_pct > 0]
    losers = [p for p in scanner_positions if p.current_pnl_pct <= 0]

    # Aggregate stats
    overall = _compute_stats(scanner_name, scanner_positions)

    # Winner / loser profiles
    winner_profile = _build_profile(winners, "winners")
    loser_profile = _build_profile(losers, "losers")

    # Feature divergences
    features = _compute_feature_divergences(winners, losers)

    # Exit reason breakdown (losers only)
    exit_reasons = _count_exit_reasons(losers)

    # Scanner comparison
    scanner_comparison = _build_scanner_comparison(positions)

    return {
        "scanner_name": scanner_name,
        "total_closed": overall.closed,
        "win_rate": overall.win_rate,
        "avg_return": overall.avg_return,
        "expectancy": overall.expectancy,
        "total_pnl_dollars": overall.total_pnl_dollars,
        "avg_days_held": overall.avg_days_held,
        "winners": winner_profile,
        "losers": loser_profile,
        "feature_divergences": features,
        "exit_reasons": exit_reasons,
        "scanner_comparison": scanner_comparison,
        "gate_thresholds": gate_config_dict,
    }


def _build_profile(positions: list, label: str) -> dict[str, Any]:
    """Build statistical profile for a group of positions."""
    count = len(positions)
    if count == 0:
        return {"count": 0, "label": label}

    def _safe_avg(vals: list[float]) -> Optional[float]:
        clean = [v for v in vals if v is not None]
        return sum(clean) / len(clean) if clean else None

    return {
        "count": count,
        "label": label,
        "avg_return": _safe_avg([p.current_pnl_pct for p in positions]),
        "avg_mfe": _safe_avg([p.max_favorable_excursion for p in positions]),
        "avg_mae": _safe_avg([p.max_adverse_excursion for p in positions]),
        "avg_days_held": _safe_avg(
            [float(p.days_held) for p in positions if p.days_held is not None]
        ),
        "avg_gate_margin": _safe_avg(
            [p.gate_margin for p in positions if p.gate_margin is not None]
        ),
        "avg_theta_adj_ev": _safe_avg(
            [p.theta_adj_ev for p in positions if p.theta_adj_ev is not None]
        ),
        "avg_conviction_score": _safe_avg(
            [p.conviction_score for p in positions if p.conviction_score is not None]
        ),
        "avg_entry_iv": _safe_avg(
            [p.entry_iv for p in positions if p.entry_iv is not None]
        ),
        "avg_entry_delta": _safe_avg(
            [abs(p.entry_delta) for p in positions if p.entry_delta is not None]
        ),
        "avg_dte_at_entry": _safe_avg(
            [float(p.dte_at_entry) for p in positions if p.dte_at_entry is not None]
        ),
        "avg_pillar_premium_leverage": _safe_avg(
            [p.pillar_premium_leverage for p in positions if p.pillar_premium_leverage is not None]
        ),
        "avg_pillar_underlying_behavior": _safe_avg(
            [p.pillar_underlying_behavior for p in positions if p.pillar_underlying_behavior is not None]
        ),
        "avg_pillar_setup_quality": _safe_avg(
            [p.pillar_setup_quality for p in positions if p.pillar_setup_quality is not None]
        ),
        "by_option_type": _count_by(positions, lambda p: p.option_type or "UNKNOWN"),
        "by_exit_reason": _count_by(
            positions, lambda p: _val(p.exit_reason) if p.exit_reason else "UNKNOWN"
        ),
    }


def _compute_feature_divergences(
    winners: list, losers: list
) -> list[dict[str, Any]]:
    """Compute winner vs loser averages for key features."""
    features = [
        ("entry_iv", "Entry IV", lambda p: p.entry_iv),
        ("entry_delta", "Entry Delta (abs)",
         lambda p: abs(p.entry_delta) if p.entry_delta else None),
        ("dte_at_entry", "DTE at Entry",
         lambda p: float(p.dte_at_entry) if p.dte_at_entry else None),
        ("gate_margin", "Gate Margin", lambda p: p.gate_margin),
        ("theta_adj_ev", "Theta-Adj EV", lambda p: p.theta_adj_ev),
        ("conviction_score", "Conviction Score", lambda p: p.conviction_score),
        ("pillar_premium_leverage", "Premium Leverage Pillar", lambda p: p.pillar_premium_leverage),
        ("pillar_underlying_behavior", "Underlying Behavior Pillar", lambda p: p.pillar_underlying_behavior),
        ("pillar_setup_quality", "Setup Quality Pillar", lambda p: p.pillar_setup_quality),
    ]

    result = []
    for key, name, extractor in features:
        w_vals = [v for v in (extractor(p) for p in winners) if v is not None]
        l_vals = [v for v in (extractor(p) for p in losers) if v is not None]

        if not w_vals or not l_vals:
            continue

        w_avg = sum(w_vals) / len(w_vals)
        l_avg = sum(l_vals) / len(l_vals)
        w_med = median(w_vals) if w_vals else None
        l_med = median(l_vals) if l_vals else None

        # Divergence as percentage of the larger value
        max_val = max(abs(w_avg), abs(l_avg), 0.001)
        divergence_pct = abs(w_avg - l_avg) / max_val * 100

        result.append({
            "feature": name,
            "winners_avg": round(w_avg, 3),
            "losers_avg": round(l_avg, 3),
            "winners_median": round(w_med, 3) if w_med is not None else None,
            "losers_median": round(l_med, 3) if l_med is not None else None,
            "divergence_pct": round(divergence_pct, 1),
        })

    # Sort by divergence descending
    result.sort(key=lambda x: x["divergence_pct"], reverse=True)
    return result


def _count_exit_reasons(positions: list) -> dict[str, int]:
    """Count exit reasons for a set of positions."""
    return _count_by(
        positions, lambda p: _val(p.exit_reason) if p.exit_reason else "UNKNOWN"
    )


def _count_by(positions: list, key_fn) -> dict[str, int]:
    """Count positions by a key function."""
    counts: dict[str, int] = {}
    for p in positions:
        k = str(key_fn(p))
        counts[k] = counts.get(k, 0) + 1
    return counts


def _build_scanner_comparison(positions: list) -> list[dict[str, Any]]:
    """Build comparison stats for all scanners."""
    from app.paper_trading.edge_intelligence import _group_by

    closed = [p for p in positions if _val(p.status) == "CLOSED"]
    by_scanner = _group_by(closed, lambda p: _normalize_scanner(p.scanner_source))

    result = []
    for name, group in sorted(by_scanner.items()):
        if name == "UNKNOWN":
            continue
        stats = _compute_stats(name, group)
        result.append({
            "scanner": name,
            "closed": stats.closed,
            "win_rate": stats.win_rate,
            "avg_return": stats.avg_return,
        })

    return result


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

OUTPUT_SCHEMA = """{
  "summary": "2-3 sentence executive summary of findings",
  "root_causes": [
    {
      "cause": "Specific root cause of underperformance",
      "evidence": "Data from the input supporting this conclusion",
      "severity": "high | medium | low"
    }
  ],
  "gate_recommendations": [
    {
      "gate_id": "GATE_MAX_SPREAD_PCT",
      "gate_name": "Max Spread %",
      "current_value": 8.0,
      "suggested_value": 5.0,
      "direction": "tighten | loosen",
      "rationale": "Why this change helps, citing specific data",
      "expected_impact": "What % of losers this would filter vs winners lost"
    }
  ],
  "additional_filters": [
    {
      "description": "A filter suggestion not tied to an existing gate",
      "rationale": "Data-backed reasoning"
    }
  ],
  "confidence": "high | medium | low",
  "confidence_rationale": "Why this confidence level (sample size, data quality)"
}"""


def build_scanner_analysis_prompt(
    analysis_input: dict[str, Any],
) -> tuple[str, str]:
    """Build system prompt and user prompt for scanner analysis.

    Returns:
        (system_prompt, user_prompt)
    """
    d = analysis_input
    winners = d.get("winners", {})
    losers = d.get("losers", {})

    # Section 1: Scanner Performance Summary
    sections = [
        f"## Scanner: {d['scanner_name'].replace('_', ' ')}",
        f"- Closed Trades: {d['total_closed']}",
        f"- Win Rate: {_fmt(d['win_rate'])}%",
        f"- Avg Return: {_fmt(d['avg_return'])}%",
        f"- Expectancy: {_fmt(d['expectancy'])}",
        f"- Total P&L: ${_fmt(d['total_pnl_dollars'])}",
        f"- Avg Days Held: {_fmt(d['avg_days_held'])}",
        "",
    ]

    # Section 2: Winner vs Loser Profiles
    sections.append("## Winner vs Loser Profile")
    sections.append("")
    profile_fields = [
        ("Count", "count"),
        ("Avg Return %", "avg_return"),
        ("Avg MFE %", "avg_mfe"),
        ("Avg MAE %", "avg_mae"),
        ("Avg Days Held", "avg_days_held"),
        ("Avg Gate Margin", "avg_gate_margin"),
        ("Avg Theta-Adj EV", "avg_theta_adj_ev"),
        ("Avg Conviction Score", "avg_conviction_score"),
        ("Avg Entry IV", "avg_entry_iv"),
        ("Avg Entry Delta (abs)", "avg_entry_delta"),
        ("Avg DTE at Entry", "avg_dte_at_entry"),
        ("Avg Premium Leverage Pillar", "avg_pillar_premium_leverage"),
        ("Avg Underlying Behavior Pillar", "avg_pillar_underlying_behavior"),
        ("Avg Setup Quality Pillar", "avg_pillar_setup_quality"),
    ]
    sections.append(f"{'Metric':<28} {'Winners':<14} {'Losers':<14}")
    sections.append("-" * 56)
    for label, key in profile_fields:
        w_val = _fmt(winners.get(key))
        l_val = _fmt(losers.get(key))
        sections.append(f"{label:<28} {w_val:<14} {l_val:<14}")
    sections.append("")

    # Winner/loser option type breakdown
    w_types = winners.get("by_option_type", {})
    l_types = losers.get("by_option_type", {})
    if w_types or l_types:
        sections.append("Winner option types: " + ", ".join(
            f"{k}={v}" for k, v in w_types.items()
        ))
        sections.append("Loser option types: " + ", ".join(
            f"{k}={v}" for k, v in l_types.items()
        ))
        sections.append("")

    # Section 3: Feature Divergences
    divergences = d.get("feature_divergences", [])
    if divergences:
        sections.append("## Feature Divergences (Winner avg vs Loser avg)")
        sections.append("")
        sections.append(f"{'Feature':<24} {'W Avg':<10} {'L Avg':<10} {'Divergence':<10}")
        sections.append("-" * 54)
        for f in divergences:
            sections.append(
                f"{f['feature']:<24} {_fmt(f['winners_avg']):<10} "
                f"{_fmt(f['losers_avg']):<10} {_fmt(f['divergence_pct'])}%"
            )
        sections.append("")

    # Section 4: Exit Reason Breakdown (losers)
    exit_reasons = d.get("exit_reasons", {})
    if exit_reasons:
        sections.append("## Loser Exit Reasons")
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            pct = count / max(losers.get("count", 1), 1) * 100
            sections.append(f"- {reason}: {count} ({pct:.0f}%)")
        sections.append("")

    # Section 5: Scanner Comparison
    comparison = d.get("scanner_comparison", [])
    if comparison:
        sections.append("## Scanner Comparison")
        sections.append(f"{'Scanner':<24} {'Closed':<10} {'WR %':<10} {'Avg Ret %':<10}")
        sections.append("-" * 54)
        for s in comparison:
            marker = " ← THIS" if s["scanner"] == d["scanner_name"] else ""
            sections.append(
                f"{s['scanner']:<24} {s['closed']:<10} "
                f"{_fmt(s['win_rate']):<10} {_fmt(s['avg_return']):<10}{marker}"
            )
        sections.append("")

    # Section 6: Current Gate Thresholds
    gate_cfg = d.get("gate_thresholds", {})
    if gate_cfg:
        sections.append("## Current Gate Thresholds (from active Policy)")
        sections.append("")
        for gate_id, (field, op) in GATE_CONFIG_MAP.items():
            val = gate_cfg.get(field.split(" / ")[0], "N/A")
            if field == "dte_min / dte_max":
                val = f"{gate_cfg.get('dte_min', '?')} - {gate_cfg.get('dte_max', '?')}"
            sections.append(f"- {gate_id} ({field}): {val}  [{op}]")
        sections.append("")

    # Output schema
    sections.append("## Required Output Format")
    sections.append("Respond with ONLY valid JSON matching this schema:")
    sections.append(OUTPUT_SCHEMA)

    user_prompt = "\n".join(sections)
    return ANALYSIS_SYSTEM_PROMPT, user_prompt


def _fmt(val: Any) -> str:
    """Format a numeric value for prompt display."""
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.2f}"
    return str(val)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ["summary", "root_causes", "gate_recommendations", "confidence"]


def parse_scanner_analysis_response(response: str) -> dict[str, Any]:
    """Parse LLM response into structured analysis.

    Args:
        response: Raw LLM response string

    Returns:
        Parsed analysis dict

    Raises:
        ValueError: If response is not valid JSON or missing required fields
    """
    response = response.strip()

    # Strip markdown code blocks
    if response.startswith("```json"):
        response = response[7:]
    if response.startswith("```"):
        response = response[3:]
    if response.endswith("```"):
        response = response[:-3]
    response = response.strip()

    try:
        data = json.loads(response)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in scanner analysis response: {e}")

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise ValueError(f"Missing required fields in analysis: {missing}")

    # Ensure lists are lists
    if not isinstance(data.get("root_causes"), list):
        raise ValueError("root_causes must be a list")
    if not isinstance(data.get("gate_recommendations"), list):
        raise ValueError("gate_recommendations must be a list")

    # Default optional fields
    data.setdefault("additional_filters", [])
    data.setdefault("confidence_rationale", "")

    return data
