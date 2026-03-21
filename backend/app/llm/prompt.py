"""Prompt template for trade thesis generation.

Per Section 21.2 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.models import ThesisInput

THESIS_SYSTEM_PROMPT = """You are an expert options trading analyst. Your task is to generate a detailed trade thesis for an approved options trade recommendation.

You will receive data about:
- The underlying stock (ticker, price, technical indicators, ATR)
- The option contract (type, strike, expiration, Greeks, breakeven, expected move)
- Scoring metrics (directional, volatility, structure scores)
- The factors that contributed to the recommendation
- Setup rule matches (codified trade archetypes from the trader's rule library)

Based on this data, provide a comprehensive trade thesis that explains:
1. Why this trade setup is favorable
2. The key supporting evidence
3. The primary risks to monitor
4. Conditions that would invalidate the thesis
5. Specific exit targets with numerical price levels

EXIT TARGET GUIDELINES:
- Provide 3 tiered take-profit targets: TP1 (conservative, +25-40%), TP2 (moderate, +50-80%), TP3 (stretch, +100%+)
- Calculate underlying price levels using delta: a $1 stock move changes the option by ~delta dollars
- For CALLs: TP underlying prices should be ABOVE current price; SL underlying price BELOW
- For PUTs: TP underlying prices should be BELOW current price; SL underlying price ABOVE
- Stop loss should account for the bid-ask spread and daily volatility (ATR)
- Time exit should consider theta decay acceleration near expiration
- All option_pnl_pct values represent the percentage change in the option's price (positive for gains, negative for losses)

SETUP RULE CONTEXT:
The trader maintains a library of setup rules — codified trade archetypes based on historical
patterns and volatility research. When one or more rules match this opportunity, incorporate
this into your thesis:

- Reference which rules matched and why that archetype applies to this trade
- If a rule has historical performance data (win rate, sample size), cite it as supporting
  evidence. Weight rules with larger samples (n>15) and higher win rates (>70%) more heavily.
- If multiple rules match (confluence), explicitly call this out as increasing conviction.
  Multi-rule confluence is the trader's highest-confidence signal.
- If a matched rule has no historical data yet (no win rate or sample size), note that the
  thesis aligns with the archetype but remains unvalidated by historical performance.
- If zero rules match, note this explicitly — it means the trade does not fit any established
  archetype, which is a caution flag worth acknowledging.

Your response MUST be valid JSON matching the exact schema provided."""

THESIS_OUTPUT_SCHEMA = """{
  "setup_summary": "string - One paragraph executive summary of the trade setup, referencing matched setup rules if any",
  "thesis": "string - Detailed 2-3 paragraph explanation of why this trade is attractive, integrating setup rule context",
  "supporting_evidence": ["string - List of 3-5 specific data points supporting the trade, including setup rule performance where available"],
  "risks": ["string - List of 2-4 key risks to monitor"],
  "invalidation_conditions": ["string - List of 2-3 conditions that would invalidate this thesis"],
  "setup_rule_matches": {
    "count": 2,
    "total_active_rules": 9,
    "matched_rules": [
      {
        "name": "Rule Name Here",
        "has_historical_data": true,
        "win_rate": 74.0,
        "sample_size": 19
      }
    ],
    "confluence_assessment": "string - How rule overlap affects conviction level for this trade"
  },
  "exit_plan": {
    "take_profits": [
      {
        "tier": 1,
        "option_pnl_pct": 30.0,
        "underlying_price": 185.50,
        "rationale": "Conservative target near SMA resistance"
      },
      {
        "tier": 2,
        "option_pnl_pct": 60.0,
        "underlying_price": 192.00,
        "rationale": "Moderate target at 1x expected move"
      },
      {
        "tier": 3,
        "option_pnl_pct": 100.0,
        "underlying_price": 200.00,
        "rationale": "Stretch target if momentum accelerates"
      }
    ],
    "stop_loss_level": {
      "option_pnl_pct": -40.0,
      "underlying_price": 168.00,
      "rationale": "Below key support at SMA50 minus 1 ATR"
    },
    "time_exit_level": {
      "dte_threshold": 5,
      "rationale": "Exit before theta decay accelerates in final week"
    },
    "profit_target": "string - Human-readable summary of take profit strategy",
    "stop_loss": "string - Human-readable summary of stop loss strategy",
    "time_exit": "string - Human-readable summary of time-based exit rule"
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
    atr14 = f"${underlying['atr14']:.2f}" if underlying['atr14'] else "N/A"
    atr14_pct = f"{underlying['atr14_pct']:.1f}%" if underlying['atr14_pct'] else "N/A"
    underlying_text = f"""
**Underlying Stock: {underlying['ticker']}**
- Current Price: ${underlying['price']:.2f}
- 20-Day SMA: {sma20}
- 50-Day SMA: {sma50}
- 5-Day Return: {ret_5d}
- 20-Day Return: {ret_20d}
- 14-Day ATR: {atr14} ({atr14_pct})
"""

    # Format the contract section
    contract = data["contract"]
    gamma = f"{contract['gamma']:.4f}" if contract['gamma'] else "N/A"
    vega = f"{contract['vega']:.3f}" if contract['vega'] else "N/A"
    oi = f"{contract['open_interest']:,}" if contract['open_interest'] else "N/A"
    vol = f"{contract['volume']:,}" if contract['volume'] else "N/A"
    spread = f"{contract['spread_pct']:.1f}%" if contract['spread_pct'] else "N/A"
    breakeven = (
        f"${contract['breakeven_price']:.2f}" if contract.get('breakeven_price') else "N/A"
    )
    expected_move = (
        f"{contract['expected_move_pct']:.1f}%" if contract.get('expected_move_pct') else "N/A"
    )
    feasibility = (
        f"{contract['feasibility_ratio']:.2f}" if contract.get('feasibility_ratio') else "N/A"
    )
    theta_pct = (
        f"{contract['theta_pct']:.2f}%" if contract.get('theta_pct') else "N/A"
    )
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

    # Format exit-relevant data section
    exit_data_text = f"""
**Exit-Relevant Data**
- Breakeven Price: {breakeven}
- Expected Move (1σ over DTE): {expected_move}
- Feasibility Ratio: {feasibility} (required move / expected move; <1.0 = achievable)
- Daily Theta Decay: {theta_pct} of premium per day
- ATR (14-day): {atr14} ({atr14_pct} of stock price)
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

    # Format setup rule matches
    setup_rules_text = "\n**Setup Rule Matches**\n"
    setup_rules = data.get("setup_rule_matches", [])
    total_active_rules = data.get("total_active_rules", 0)

    if setup_rules:
        for rule in setup_rules:
            rule_name = rule.get("name", "Unknown Rule")
            rule_mode = rule.get("mode", "Production")
            rule_source = rule.get("source", "AI-Discovered")

            # Build tag string
            tags = []
            if rule_source == "Manual":
                tags.append("Manual")
            tags.append(rule_mode)
            tag_str = f"[{', '.join(tags)}]"

            setup_rules_text += f"- {rule_name} {tag_str}\n"

            # Historical performance if available
            win_rate = rule.get("win_rate")
            avg_return = rule.get("avg_return")
            sample_size = rule.get("sample_size")
            if win_rate is not None and sample_size is not None:
                setup_rules_text += f"  Win Rate: {win_rate:.1f}%, Avg Return: {avg_return:.1f}%, Sample: n={sample_size}\n"
            else:
                setup_rules_text += "  Win Rate: N/A (no historical data yet)\n"

            # Matched criteria
            matched_criteria = rule.get("matched_criteria", {})
            if matched_criteria:
                criteria_parts = [
                    f"{k}: {v}" for k, v in matched_criteria.items()
                ]
                setup_rules_text += f"  Matched Criteria: {', '.join(criteria_parts)}\n"

        setup_rules_text += f"\nRule Confluence Count: {len(setup_rules)} of {total_active_rules} active rules matched\n"
    else:
        setup_rules_text += "- No setup rules matched this opportunity\n"
        if total_active_rules > 0:
            setup_rules_text += f"  (0 of {total_active_rules} active rules triggered — trade does not fit any established archetype)\n"

    # Build the complete prompt
    prompt = f"""{THESIS_SYSTEM_PROMPT}

---

## Trade Data

{underlying_text}
{contract_text}
{exit_data_text}
{scores_text}
{contributors_text}
{triggers_text}
{setup_rules_text}

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

    # Validate setup_rule_matches if present (not required for backward compatibility)
    setup_rule_matches = data.get("setup_rule_matches")
    if setup_rule_matches and isinstance(setup_rule_matches, dict):
        if "matched_rules" in setup_rule_matches:
            for rule in setup_rule_matches["matched_rules"]:
                rule_required = ["name", "has_historical_data"]
                rule_missing = [f for f in rule_required if f not in rule]
                if rule_missing:
                    raise ValueError(
                        f"setup_rule_matches.matched_rules entry missing fields: {rule_missing}"
                    )

    # Validate exit_plan structure
    exit_plan = data.get("exit_plan", {})

    # Check for structured exit plan (new format)
    has_structured = "take_profits" in exit_plan and isinstance(
        exit_plan["take_profits"], list
    )

    if has_structured:
        # Validate structured format
        take_profits = exit_plan["take_profits"]
        if len(take_profits) < 1 or len(take_profits) > 3:
            raise ValueError(
                f"take_profits must have 1-3 items, got {len(take_profits)}"
            )
        for tp in take_profits:
            tp_required = ["tier", "option_pnl_pct", "underlying_price", "rationale"]
            tp_missing = [f for f in tp_required if f not in tp]
            if tp_missing:
                raise ValueError(
                    f"take_profit tier missing fields: {tp_missing}"
                )

        sl = exit_plan.get("stop_loss_level")
        if sl and isinstance(sl, dict):
            sl_required = ["option_pnl_pct", "underlying_price", "rationale"]
            sl_missing = [f for f in sl_required if f not in sl]
            if sl_missing:
                raise ValueError(f"stop_loss_level missing fields: {sl_missing}")

        # Ensure legacy string fields exist (may be generated alongside structured)
        for field_name in ["profit_target", "stop_loss", "time_exit"]:
            if field_name not in exit_plan:
                exit_plan[field_name] = ""
    else:
        # Legacy format — validate string fields
        exit_fields = ["profit_target", "stop_loss", "time_exit"]
        missing_exit = [f for f in exit_fields if f not in exit_plan]
        if missing_exit:
            raise ValueError(f"Missing exit_plan fields: {missing_exit}")

    return data
