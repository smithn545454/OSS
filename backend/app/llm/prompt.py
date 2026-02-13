"""Prompt template for trade thesis generation.

Per Section 21.2 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.models import ThesisInput

THESIS_SYSTEM_PROMPT = """You are an expert options trading analyst. Your task is to generate a detailed trade thesis for an approved options trade recommendation.

You will receive data about:
- The underlying stock (ticker, price, technical indicators)
- The option contract (type, strike, expiration, Greeks)
- Scoring metrics (directional, volatility, structure scores)
- The factors that contributed to the recommendation

Based on this data, provide a comprehensive trade thesis that explains:
1. Why this trade setup is favorable
2. The key supporting evidence
3. The primary risks to monitor
4. Conditions that would invalidate the thesis
5. Recommended exit strategy

Your response MUST be valid JSON matching the exact schema provided."""

THESIS_OUTPUT_SCHEMA = """{
  "setup_summary": "string - One paragraph executive summary of the trade setup",
  "thesis": "string - Detailed 2-3 paragraph explanation of why this trade is attractive",
  "supporting_evidence": ["string - List of 3-5 specific data points supporting the trade"],
  "risks": ["string - List of 2-4 key risks to monitor"],
  "invalidation_conditions": ["string - List of 2-3 conditions that would invalidate this thesis"],
  "exit_plan": {
    "profit_target": "string - When to take profits (e.g., 'Exit at 50% gain or when underlying reaches $XXX')",
    "stop_loss": "string - When to cut losses (e.g., 'Exit if position loses 50% or breaks below $XXX')",
    "time_exit": "string - Time-based exit rule (e.g., 'Close position when DTE reaches 5 regardless of P&L')"
  }
}"""


def build_thesis_prompt(input_data: ThesisInput) -> str:
    """Build the complete prompt for thesis generation.

    Args:
        input_data: ThesisInput with all trade data

    Returns:
        Formatted prompt string
    """
    data = input_data.to_dict()

    # Format the underlying section
    underlying = data["underlying"]
    sma20 = f"${underlying['sma20']:.2f}" if underlying['sma20'] else "N/A"
    sma50 = f"${underlying['sma50']:.2f}" if underlying['sma50'] else "N/A"
    ret_5d = f"{underlying['return_5d']:.1f}%" if underlying['return_5d'] else "N/A"
    ret_20d = f"{underlying['return_20d']:.1f}%" if underlying['return_20d'] else "N/A"
    underlying_text = f"""
**Underlying Stock: {underlying['ticker']}**
- Current Price: ${underlying['price']:.2f}
- 20-Day SMA: {sma20}
- 50-Day SMA: {sma50}
- 5-Day Return: {ret_5d}
- 20-Day Return: {ret_20d}
"""

    # Format the contract section
    contract = data["contract"]
    gamma = f"{contract['gamma']:.4f}" if contract['gamma'] else "N/A"
    vega = f"{contract['vega']:.3f}" if contract['vega'] else "N/A"
    oi = f"{contract['open_interest']:,}" if contract['open_interest'] else "N/A"
    vol = f"{contract['volume']:,}" if contract['volume'] else "N/A"
    spread = f"{contract['spread_pct']:.1f}%" if contract['spread_pct'] else "N/A"
    contract_text = f"""
**Option Contract**
- Type: {contract['type']}
- Strike: ${contract['strike']:.2f}
- Expiration: {contract['expiration']}
- Days to Expiration: {contract['dte']}
- Mid Price: ${contract['mid']:.2f}
- Implied Volatility: {contract['iv'] * 100:.1f}%
- Delta: {contract['delta']:.3f}
- Theta: ${contract['theta']:.3f}
- Gamma: {gamma}
- Vega: {vega}
- Open Interest: {oi}
- Volume: {vol}
- Bid-Ask Spread: {spread}
"""

    # Format the scores section
    scores = data["scores"]
    scores_text = f"""
**Scoring Summary**
- Final Score: {scores['final']:.1f}/100
- Directional Score: {scores['directional']:.1f}/100
- Volatility Score: {scores['volatility']:.1f}/100
- Structure Score: {scores['structure']:.1f}/100
- Quality Tier: {data['quality_tier'] or 'N/A'}
- Policy Version: {data.get('policy_version', 'N/A')}
"""

    # Format pillar contributors
    contributors_text = "\n**Key Scoring Factors**\n"
    for pillar, contributors in data["pillar_contributors"].items():
        contributors_text += f"\n_{pillar.title()} Pillar:_\n"
        for c in contributors[:3]:  # Top 3 per pillar
            contributors_text += f"- {c['feature']}: {c['value']} (score: {c['subscore']:.0f}, contribution: +{c['contribution']:.1f})\n"

    # Format scanner triggers
    triggers_text = "\n**Scanner Triggers**\n"
    if data["scanner_triggers"]:
        for trigger in data["scanner_triggers"]:
            triggers_text += f"- {trigger['type']}: {', '.join(trigger['reasons'])}\n"
            if trigger["metrics"]:
                for k, v in trigger["metrics"].items():
                    formatted_v = f"{v:.2f}" if isinstance(v, float) else str(v)
                    triggers_text += f"  - {k}: {formatted_v}\n"
    else:
        triggers_text += "- No specific scanner triggers\n"

    # Build the complete prompt
    prompt = f"""{THESIS_SYSTEM_PROMPT}

---

## Trade Data

{underlying_text}
{contract_text}
{scores_text}
{contributors_text}
{triggers_text}

---

## Required Output Format

Respond with ONLY valid JSON matching this exact schema:

{THESIS_OUTPUT_SCHEMA}

---

Generate the trade thesis now:"""

    return prompt


def parse_thesis_response(response: str) -> dict[str, Any]:
    """Parse LLM response into thesis data.

    Args:
        response: Raw LLM response string

    Returns:
        Parsed thesis data dictionary

    Raises:
        ValueError: If response is not valid JSON or missing required fields
    """
    # Try to extract JSON from response
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
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in LLM response: {e}")

    # Validate required fields
    required_fields = [
        "setup_summary",
        "thesis",
        "supporting_evidence",
        "risks",
        "invalidation_conditions",
        "exit_plan",
    ]

    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Missing required fields in thesis: {missing}")

    # Validate exit_plan structure
    exit_plan = data.get("exit_plan", {})
    exit_fields = ["profit_target", "stop_loss", "time_exit"]
    missing_exit = [f for f in exit_fields if f not in exit_plan]
    if missing_exit:
        raise ValueError(f"Missing exit_plan fields: {missing_exit}")

    return data
