"""Prompt template for Custom Analysis of paper trading data.

Sends closed trade data to Claude with a user-provided question.
Returns both freeform markdown analysis AND structured suggested rules
using the same criteria schema as Pattern Discovery archetypes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Shared CSV column reference — also used by pattern_discovery_prompt.py
CSV_COLUMN_REFERENCE = (
    "## CSV Column Reference\n"
    "The data uses abbreviated column names and values for compactness:\n"
    "- tkr: Ticker symbol\n"
    "- sec: Sector (Tech=Technology, HC=Healthcare, Enrg=Energy, Matl=Materials, "
    "Fin=Financials, ConD=Consumer Discretionary, ConS=Consumer Staples, "
    "Ind=Industrials, Util=Utilities, RE=Real Estate, Comm=Communication Services)\n"
    "- scn: Scanner (BRK=BREAKOUT, CMP=COMPRESSION, CHP=CHEAP_OPTIONS, UV=UNUSUAL_VOLUME)\n"
    "- conv: Scanner confluence count (1-4)\n"
    "- cscore: Conviction score 0-100\n"
    "- p_dir/p_vol/p_str: Directional/Volatility/Structure pillar scores 0-100\n"
    "- type: C=CALL, P=PUT\n"
    "- dte: Days to expiration at entry\n"
    "- iv: Implied volatility at entry\n"
    "- iv_pct: IV percentile 0-100 (lower = historically cheaper)\n"
    "- ivrv: IV/RV ratio (below 1.0 = IV < realized vol, tailwind for longs)\n"
    "- theta_edge: Delta gain per theta decay (above 1.5 = strong)\n"
    "- gate_m: Distance above gate threshold (higher = safer)\n"
    "- money_pct: OTM/ITM % (positive = OTM for calls)\n"
    "- spread: Bid-ask spread % of mid (lower = more liquid)\n"
    "- oi/vol: Open interest / volume at entry\n"
    "- dte_earn: Days to earnings\n"
    "- atr: 14-day ATR as % of price\n"
    "- rs: 20-day relative strength vs SPY\n"
    "- feas: Feasibility ratio (lower = easier breakeven)\n"
    "- ret: Final return %\n"
    "- days: Days held\n"
    "- verdict: Entry verdict (A=APPROVE, W=WATCH, R=REJECT)\n"
    "\n"
    "Empty cells = data unavailable.\n"
)

CRITERIA_SCHEMA_REFERENCE = """
## Suggested Rule Criteria Schema
Each suggested_rule should use these criteria keys (same as setup rules):
- sectors: list of sector names (e.g. ["Energy", "Materials"])
- scanners: list of FULL scanner names (BREAKOUT, COMPRESSION, CHEAP_OPTIONS, UNUSUAL_VOLUME)
- scanner_confluence: true (requires 2+ scanners)
- conviction_score_min / conviction_score_max: score thresholds
- pillar_premium_leverage_min / pillar_underlying_behavior_min / pillar_setup_quality_min: score floors (Policy v3.0.0)
- dte_min / dte_max: DTE range
- option_type: "CALL" or "PUT"
- entry_iv_min / entry_iv_max: IV thresholds
- iv_percentile_max / iv_percentile_min: IV percentile thresholds
- iv_rv_ratio_max / iv_rv_ratio_min: IV/RV ratio thresholds
- theta_adjusted_edge_min: minimum theta-adjusted edge ratio
- gate_margin_min: minimum gate margin
- moneyness_pct_min / moneyness_pct_max: OTM/ITM depth range
- spread_pct_max: maximum bid-ask spread percentage
- open_interest_min: minimum open interest
- volume_min: minimum volume
- days_to_earnings_min / days_to_earnings_max: earnings proximity
- atr14_pct_min / atr14_pct_max: stock volatility range
- rs_20d_min: minimum relative strength vs SPY
- feasibility_ratio_max: max feasibility ratio (lower = easier)
"""

CUSTOM_ANALYSIS_SYSTEM_PROMPT = (
    "You are a quantitative trading analyst specializing in options. "
    "You receive a dataset of closed options paper trades in CSV format, "
    "pre-computed statistics, and a user question. "
    "Your job is to answer the question with data-backed analysis "
    "and suggest actionable setup rules.\n"
    "\n"
    + CSV_COLUMN_REFERENCE
    + "\n"
    "IMPORTANT: In criteria, use FULL scanner names "
    "(BREAKOUT, COMPRESSION, CHEAP_OPTIONS, UNUSUAL_VOLUME), "
    "not CSV abbreviations.\n"
    "\n"
    "Your response MUST be a valid JSON object with two fields:\n"
    "1. \"analysis\": A markdown string with your detailed analysis. "
    "Include specific data points, trade counts, percentages. "
    "Reference specific tickers as examples where relevant. "
    "Be thorough but concise.\n"
    "2. \"suggested_rules\": An array of 1-5 setup rule objects. "
    "Each rule represents a testable, repeatable trade setup "
    "identified from your analysis. Only include rules backed by data.\n"
    "\n"
    "Each suggested_rule object:\n"
    "{\n"
    "  \"name\": \"Descriptive rule name\",\n"
    "  \"criteria\": { ... criteria keys from schema ... },\n"
    "  \"performance\": {\n"
    "    \"win_rate\": 0.76,\n"
    "    \"avg_return\": 42.0,\n"
    "    \"median_return\": 35.0,\n"
    "    \"sample_size\": 23,\n"
    "    \"avg_days_held\": 8.5\n"
    "  },\n"
    "  \"reasoning\": \"Why this rule captures the pattern\"\n"
    "}\n"
    "\n"
    + CRITERIA_SCHEMA_REFERENCE
    + "\n"
    "If the question doesn't naturally lead to setup rules "
    "(e.g., it's purely informational), return an empty suggested_rules array."
)


def build_custom_analysis_prompt(
    user_prompt: str,
    trade_csv: str,
    statistics_summary: str,
    context: dict[str, Any],
) -> str:
    """Build the custom analysis prompt.

    Args:
        user_prompt: The user's analytical question
        trade_csv: CSV string with header row and trade data rows
        statistics_summary: Pre-computed statistics text from trade_statistics.py
        context: Aggregate context (total_trades, win_rate, avg_return, etc.)

    Returns:
        Complete prompt string for the LLM
    """
    sampling_note = ""
    if context.get("sampled"):
        sampling_note = (
            f"- NOTE: This is a sample of {context['sample_size']} trades "
            f"from {context['total_trades']} total. "
            f"Scale counts proportionally when estimating.\n"
        )

    prompt = (
        f"{CUSTOM_ANALYSIS_SYSTEM_PROMPT}\n\n"
        f"## Dataset Context\n"
        f"- Total closed trades: {context['total_trades']}\n"
        f"- Overall win rate: {context['win_rate']}%\n"
        f"- Overall avg return: {context['avg_return']}%\n"
        f"{sampling_note}\n"
        f"{statistics_summary}\n\n"
        f"## Trade Data (CSV)\n{trade_csv}\n\n"
        f"## User Question\n{user_prompt}\n\n"
        f"Respond with ONLY a valid JSON object: "
        f"{{\"analysis\": \"...\", \"suggested_rules\": [...]}}"
    )

    return prompt


def parse_custom_analysis_response(raw_response: str) -> dict[str, Any]:
    """Parse the LLM response into analysis + suggested rules.

    Args:
        raw_response: Raw text from Claude

    Returns:
        Dict with 'analysis' (markdown string) and 'suggested_rules' (list)
    """
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
        logger.error(f"Failed to parse custom analysis response as JSON: {text[:200]}...")
        # Fall back to treating the whole response as analysis text
        return {
            "analysis": raw_response,
            "suggested_rules": [],
        }

    if not isinstance(parsed, dict):
        logger.error(f"Unexpected response format: {type(parsed)}")
        return {
            "analysis": raw_response,
            "suggested_rules": [],
        }

    analysis = parsed.get("analysis", "")
    suggested_rules = parsed.get("suggested_rules", [])

    # Validate and normalize suggested rules
    validated_rules = []
    for rule in suggested_rules:
        if not isinstance(rule, dict):
            continue
        if "name" not in rule or "criteria" not in rule:
            continue

        # Ensure performance dict exists with defaults
        perf = rule.get("performance", {})
        rule["performance"] = {
            "win_rate": perf.get("win_rate", 0),
            "avg_return": perf.get("avg_return", 0),
            "median_return": perf.get("median_return", perf.get("avg_return", 0)),
            "sample_size": perf.get("sample_size", 0),
            "avg_days_held": perf.get("avg_days_held", 0),
        }

        # Compute confidence level from sample size
        sample = rule["performance"]["sample_size"]
        if sample < 5:
            rule["confidence"] = "INSUFFICIENT"
            rule["confidence_label"] = "Too few trades"
        elif sample < 10:
            rule["confidence"] = "LOW"
            rule["confidence_label"] = "Emerging — limited data"
        elif sample <= 30:
            rule["confidence"] = "MODERATE"
            rule["confidence_label"] = "Directional signal"
        elif sample <= 100:
            rule["confidence"] = "HIGH"
            rule["confidence_label"] = "Reliable pattern"
        else:
            rule["confidence"] = "VERY_HIGH"
            rule["confidence_label"] = "Statistically robust"

        validated_rules.append(rule)

    return {
        "analysis": analysis,
        "suggested_rules": validated_rules,
    }
