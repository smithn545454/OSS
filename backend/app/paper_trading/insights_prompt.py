"""Prompt template for AI-powered paper trading insights.

Generates system optimization recommendations by analyzing
paper trading performance data with an LLM.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

INSIGHTS_SYSTEM_PROMPT = """You are a quantitative trading system analyst reviewing paper trading results for an options scanning system (OSS). Your job is to identify actionable improvements to the system's scoring, gates, exit strategies, and tier assignments.

The system uses:
- Hard gates (binary pass/fail checks) that reject trades
- Three scoring pillars: Directional, Volatility, Structure (each 0-100)
- Quality tiers: TIER_1 (best), TIER_2, TIER_3
- Exit strategies: PROFIT_TARGET, STOP_LOSS, TIME_EXIT, EXPIRATION
- Verdicts: APPROVE, WATCH, REJECT

Your analysis MUST:
1. Reference specific numbers from the data
2. Quantify estimated improvements (e.g., "could improve win rate by ~3-5%")
3. Focus on actionable gate threshold, exit parameter, or scoring weight changes
4. Prioritize recommendations by potential impact

Respond with ONLY a valid JSON array of insight objects."""

INSIGHTS_OUTPUT_SCHEMA = """[
  {
    "category": "GATE_TUNING|EXIT_STRATEGY|TIER_QUALITY|SCORE_OPTIMIZATION|RISK_MANAGEMENT",
    "severity": "HIGH|MEDIUM|LOW",
    "title": "Short headline (max 80 chars)",
    "description": "Detailed data-backed explanation (2-3 sentences)",
    "data_points": {"metric_name": "value"},
    "suggested_action": "Specific recommendation with concrete values",
    "estimated_impact": "Quantified improvement estimate"
  }
]"""


def build_insights_prompt(
    metrics: dict[str, Any],
    tier_data: dict[str, Any],
    exit_data: dict[str, Any],
    calibration_report: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Build the prompt for AI insights generation.

    Args:
        metrics: Performance metrics dict from PerformanceMetrics.to_dict()
        tier_data: Tier comparison data from compare_tiers()
        exit_data: Exit analysis data from analyze_exit_effectiveness()
        calibration_report: Optional latest calibration report

    Returns:
        Tuple of (system_prompt, user_prompt)
    """
    metrics_text = f"""
**Overall Performance**
- Total Positions: {metrics.get('total_positions', 0)}
- Open: {metrics.get('open_positions', 0)} | Closed: {metrics.get('closed_positions', 0)}
- Win Rate: {metrics.get('win_rate', 0):.1f}% (target: >55%)
- Avg Win: {metrics.get('avg_win_pct', 0):.1f}% | Avg Loss: {metrics.get('avg_loss_pct', 0):.1f}%
- Best Trade: {metrics.get('best_trade_pct', 0):.1f}% | Worst Trade: {metrics.get('worst_trade_pct', 0):.1f}%
- Expectancy: {metrics.get('expectancy', 0):.2f}%
- Profit Factor: {metrics.get('profit_factor', 'N/A')}
- MFE Capture Ratio: {metrics.get('mfe_capture_ratio', 0):.1f}%
- Avg MFE: {metrics.get('avg_mfe', 0):.1f}% | Avg MAE: {metrics.get('avg_mae', 0):.1f}%
- Max MFE: {metrics.get('max_mfe', 0):.1f}% | Max MAE: {metrics.get('max_mae', 0):.1f}%
- Avg Days Held: {metrics.get('avg_days_held', 0):.1f} | Max Days Held: {metrics.get('max_days_held', 0)}
"""

    # APPROVE vs WATCH performance
    approve_perf = metrics.get("approve_performance", {})
    watch_perf = metrics.get("watch_performance", {})
    verdict_text = f"""
**Verdict Performance**
- APPROVE: win_rate={approve_perf.get('win_rate', 'N/A')}%, avg_return={approve_perf.get('avg_return', 'N/A')}%, count={approve_perf.get('count', 'N/A')}
- WATCH: win_rate={watch_perf.get('win_rate', 'N/A')}%, avg_return={watch_perf.get('avg_return', 'N/A')}%, count={watch_perf.get('count', 'N/A')}
"""

    # Tier performance
    tier_text = "\n**Tier Performance** (expect TIER_1 > TIER_2 > TIER_3)\n"
    for tier_name in ["TIER_1", "TIER_2", "TIER_3", "NONE"]:
        if tier_name in tier_data:
            t = tier_data[tier_name]
            tier_text += (
                f"- {tier_name}: win_rate={t.get('win_rate', 0):.1f}%, "
                f"avg_return={t.get('avg_return', 0):.1f}%, "
                f"total={t.get('total', 0)}, closed={t.get('closed', 0)}\n"
            )

    # Exit analysis
    exit_text = "\n**Exit Strategy Analysis**\n"
    for exit_reason, data in exit_data.items():
        exit_text += (
            f"- {exit_reason}: count={data.get('count', 0)}, "
            f"avg_return={data.get('avg_return', 0):.1f}%, "
            f"avg_mfe={data.get('avg_mfe', 0):.1f}%, "
            f"avg_mae={data.get('avg_mae', 0):.1f}%, "
            f"avg_days={data.get('avg_days_held', 0):.1f}"
        )
        if "mfe_left_on_table" in data:
            exit_text += f", mfe_left={data['mfe_left_on_table']:.1f}%"
        exit_text += "\n"

    # Exit distribution
    exit_dist = metrics.get("exit_distribution", {})
    if exit_dist:
        exit_text += "\nExit Distribution: " + ", ".join(
            f"{k}={v}" for k, v in exit_dist.items()
        ) + "\n"

    # Calibration data (optional)
    calibration_text = ""
    if calibration_report:
        calibration_text = "\n**Latest Calibration Report**\n"
        gate_analyses = calibration_report.get("gate_analyses", [])
        if gate_analyses:
            calibration_text += "Gate Effectiveness:\n"
            for ga in gate_analyses[:10]:
                calibration_text += (
                    f"- {ga.get('gate_id', '?')}: "
                    f"rejection_rate={ga.get('rejection_rate', 0):.1f}%, "
                    f"false_neg_rate={ga.get('false_negative_rate', 0):.1f}%, "
                    f"effectiveness={ga.get('effectiveness_score', 0):.1f}, "
                    f"recommendation={ga.get('recommendation', 'N/A')}\n"
                )

        suggestions = calibration_report.get("suggestions", [])
        if suggestions:
            calibration_text += "\nPending Threshold Suggestions:\n"
            for s in suggestions[:5]:
                calibration_text += (
                    f"- {s.get('gate_id', '?')}: "
                    f"current={s.get('current_value', '?')} → suggested={s.get('suggested_value', '?')} "
                    f"(status={s.get('status', '?')})\n"
                )

        score_bands = calibration_report.get("score_band_analysis", [])
        if score_bands:
            calibration_text += "\nScore Band Analysis:\n"
            for band in score_bands:
                calibration_text += (
                    f"- {band.get('band', '?')}: "
                    f"count={band.get('count', 0)}, "
                    f"win_rate={band.get('win_rate', 0):.1f}%, "
                    f"avg_return={band.get('avg_return', 0):.1f}%\n"
                )

    user_prompt = f"""## Paper Trading Data

{metrics_text}
{verdict_text}
{tier_text}
{exit_text}
{calibration_text}

---

## Required Output Format

Respond with ONLY a valid JSON array matching this schema:

{INSIGHTS_OUTPUT_SCHEMA}

Generate 3-6 insights, prioritized by severity (HIGH first). Each insight must reference specific numbers from the data above.

---

Generate the insights now:"""

    return INSIGHTS_SYSTEM_PROMPT, user_prompt


def parse_insights_response(response: str) -> list[dict[str, Any]]:
    """Parse LLM response into a list of insight objects.

    Args:
        response: Raw LLM response string

    Returns:
        List of validated insight dictionaries
    """
    response = response.strip()

    # Handle markdown code blocks
    if response.startswith("```json"):
        response = response[7:]
    if response.startswith("```"):
        response = response[3:]
    if response.endswith("```"):
        response = response[:-3]

    response = response.strip()

    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        logger.warning(
            "Failed to parse LLM insights response as JSON. First 500 chars: %s",
            response[:500],
        )
        return []

    if not isinstance(data, list):
        logger.warning("LLM insights response is not a JSON array, got %s", type(data).__name__)
        return []

    valid_categories = {
        "GATE_TUNING", "EXIT_STRATEGY", "TIER_QUALITY",
        "SCORE_OPTIMIZATION", "RISK_MANAGEMENT",
    }
    valid_severities = {"HIGH", "MEDIUM", "LOW"}
    required_fields = [
        "category", "severity", "title", "description",
        "suggested_action", "estimated_impact",
    ]

    validated = []
    for item in data:
        if not isinstance(item, dict):
            continue

        # Check required fields
        missing = [f for f in required_fields if f not in item]
        if missing:
            logger.warning("Insight missing fields: %s", missing)
            continue

        # Validate enum values
        if item["category"] not in valid_categories:
            item["category"] = "SCORE_OPTIMIZATION"
        if item["severity"] not in valid_severities:
            item["severity"] = "MEDIUM"

        # Ensure data_points is a dict
        if not isinstance(item.get("data_points"), dict):
            item["data_points"] = {}

        validated.append(item)

    return validated
