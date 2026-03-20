"""Prompt template for Pattern Discovery AI analysis.

Sends closed trade data to Claude to identify statistically significant
trade archetypes — repeatable setups with above-average win rates.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

DISCOVERY_SYSTEM_PROMPT = (
    "You are a quantitative trading pattern analyst. "
    "You receive a dataset of closed options paper trades and must identify "
    "statistically significant trade archetypes — repeatable combinations "
    "of characteristics that correlate with above-average win rates and returns.\n"
    "\n"
    "Available fields per trade:\n"
    "- scanner: Which scanner detected the opportunity "
    "(BREAKOUT, COMPRESSION, CHEAP_OPTIONS, UNUSUAL_VOLUME)\n"
    "- scanner_list: All scanners that fired (multi-scanner confluence)\n"
    "- conviction_score: Overall score 0-100\n"
    "- pillar_directional: Directional edge score 0-100\n"
    "- pillar_structure: Structure & quality score 0-100\n"
    "- pillar_volatility: Volatility score 0-100\n"
    "- option_type: CALL or PUT\n"
    "- dte_at_entry: Days to expiration when position opened\n"
    "- dte_bucket: Bucketed DTE range\n"
    "- entry_iv: Implied volatility at entry\n"
    "- entry_iv_percentile: IV percentile rank 0-100 "
    "(lower = historically cheaper options, null if unavailable)\n"
    "- entry_iv_rv_ratio: IV/RV ratio "
    "(below 1.0 = IV cheaper than realized volatility — a tailwind for long options, "
    "null if unavailable)\n"
    "- entry_theta_adjusted_edge: Expected delta gain per unit of theta decay "
    "(above 1.5 = strong edge, null if unavailable)\n"
    "- convergence_count: Number of scanners that fired (1-4)\n"
    "- return_pct: Final return percentage\n"
    "- days_held: Calendar days held\n"
    "\n"
    "Your analysis MUST:\n"
    "1. Look for combinations of 2-4 characteristics "
    "that produce above-average results\n"
    "2. Only report archetypes with sample size >= the minimum threshold\n"
    "3. Only report archetypes with win rate above the minimum threshold\n"
    "4. Define each archetype with specific, testable criteria "
    "(thresholds, ranges)\n"
    "5. Include reasoning for why each pattern might work\n"
    "\n"
    "Respond with ONLY a valid JSON array of archetype objects."
)

DISCOVERY_OUTPUT_SCHEMA = """{
  "archetypes": [
    {
      "name": "Descriptive name for the archetype",
      "criteria": {
        "scanners": ["COMPRESSION"],
        "conviction_score_min": 78,
        "pillar_directional_min": 75,
        "dte_min": 15,
        "dte_max": 30
      },
      "performance": {
        "win_rate": 0.76,
        "avg_return": 42.0,
        "median_return": 35.0,
        "sample_size": 23,
        "avg_days_held": 8.5
      },
      "matching_trade_indices": [0, 3, 7, 12],
      "reasoning": "Why this pattern works"
    }
  ]
}

The criteria object can include any combination of:
- scanners: list of scanner names
- scanner_confluence: true (requires 2+ scanners)
- conviction_score_min / conviction_score_max: score thresholds
- pillar_directional_min / pillar_structure_min / pillar_volatility_min (score floors)
- dte_min / dte_max: DTE range
- option_type: "CALL" or "PUT"
- entry_iv_min / entry_iv_max: IV thresholds
- iv_percentile_max / iv_percentile_min: IV percentile thresholds
- iv_rv_ratio_max / iv_rv_ratio_min: IV/RV ratio (1.0 = IV < realized vol)
- theta_adjusted_edge_min: minimum theta-adjusted edge ratio
"""


def build_discovery_prompt(
    trades: list[dict[str, Any]],
    context: dict[str, Any],
) -> str:
    """Build the pattern discovery prompt.

    Args:
        trades: List of trade summary dicts
        context: Aggregate context (total_trades, win_rate, avg_return, thresholds)

    Returns:
        Complete prompt string for the LLM
    """
    trade_json = json.dumps(trades, indent=None, default=str)

    prompt = (
        f"{DISCOVERY_SYSTEM_PROMPT}\n\n"
        f"## Dataset Context\n"
        f"- Total closed trades: {context['total_trades']}\n"
        f"- Overall win rate: {context['win_rate']}%\n"
        f"- Overall avg return: {context['avg_return']}%\n"
        f"- Minimum sample size per archetype: {context['min_sample_size']}\n"
        f"- Minimum win rate threshold: {context['min_win_rate_pct']}%\n\n"
        f"## Trade Data (JSON)\n{trade_json}\n\n"
        f"## Required Output Format\n{DISCOVERY_OUTPUT_SCHEMA}\n\n"
        f"Identify 3-7 distinct archetypes. Focus on combinations "
        f"that meaningfully outperform the overall "
        f"{context['win_rate']}% win rate. "
        f"Each archetype should be distinct — avoid overlapping criteria."
    )

    return prompt


def parse_discovery_response(raw_response: str) -> list[dict[str, Any]]:
    """Parse the LLM response into archetype objects.

    Args:
        raw_response: Raw text from Claude

    Returns:
        List of archetype dicts
    """
    # Try to extract JSON from the response
    text = raw_response.strip()

    # Handle markdown code blocks
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse discovery response as JSON: {text[:200]}...")
        return []

    # Handle both {"archetypes": [...]} and bare [...]
    if isinstance(parsed, dict):
        archetypes = parsed.get("archetypes", [])
    elif isinstance(parsed, list):
        archetypes = parsed
    else:
        logger.error(f"Unexpected response format: {type(parsed)}")
        return []

    # Validate and normalize each archetype
    validated = []
    for arch in archetypes:
        if not isinstance(arch, dict):
            continue
        if "name" not in arch or "criteria" not in arch:
            continue

        # Ensure performance dict exists with defaults
        perf = arch.get("performance", {})
        arch["performance"] = {
            "win_rate": perf.get("win_rate", 0),
            "avg_return": perf.get("avg_return", 0),
            "median_return": perf.get("median_return", perf.get("avg_return", 0)),
            "sample_size": perf.get("sample_size", 0),
            "avg_days_held": perf.get("avg_days_held", 0),
        }

        # Compute confidence level from sample size
        sample = arch["performance"]["sample_size"]
        if sample < 5:
            arch["confidence"] = "INSUFFICIENT"
            arch["confidence_label"] = "Too few trades"
        elif sample < 10:
            arch["confidence"] = "LOW"
            arch["confidence_label"] = "Emerging — limited data"
        elif sample <= 30:
            arch["confidence"] = "MODERATE"
            arch["confidence_label"] = "Directional signal"
        elif sample <= 100:
            arch["confidence"] = "HIGH"
            arch["confidence_label"] = "Reliable pattern"
        else:
            arch["confidence"] = "VERY_HIGH"
            arch["confidence_label"] = "Statistically robust"

        validated.append(arch)

    return validated
