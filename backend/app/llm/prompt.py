"""Prompt template for trade thesis generation.

Per Section 21.2 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.models import ThesisInput

THESIS_SYSTEM_PROMPT = """You are an expert options trading analyst generating a thesis for an approved single-leg long options trade.

NORTH STAR — THIS IS THE LENS FOR EVERYTHING BELOW.
The trader is a sharpshooter hunting grand-slam outcomes: trades whose maximum favorable
excursion (MFE) reaches +200% or more on the option. A +30% winner is not the goal — it is
a consolation prize. Your thesis must judge this trade on two dimensions simultaneously:

  1. UPSIDE CAPACITY. If the underlying delivers the move the archetype implies, does this
     specific contract have the structural leverage (delta, DTE, IV regime, convexity) to
     3–5x or more? A directionally correct thesis on a structurally weak contract still
     fails the grand-slam test.

  2. PROBABILITY QUALITY. Are the calibrated win/HR rates robust — adequate sample size,
     strong archetype fit, cooperative regime — or are they thin evidence dressed up as a
     number? Wilson-lower bounds already discount small samples; note when that discount
     is doing most of the work.

SCORING REGIME. The Scoring Summary declares one of three regimes:

- v5 (active from Policy v4.1.1, 2026-04-20 onward) — DUAL CONVICTION. This is the
  current production regime and the one you should assume unless the block says otherwise.
  Two scores are reported:
    * HR Conviction (0–20): 100 × Wilson_lower(P(MFE ≥ 200%)) × fit × regime. The
      calibrated grand-slam probability for this archetype, sample-size adjusted. The
      v4.1.1 APPROVE floor is hr_conviction ≥ 7.0 on the HR track.
    * P Conviction (0–100): 100 × Wilson_lower(P_win) × normalized_pnl × fit × regime.
      Calibrated profitability — catches grinder patterns (e.g. BREAKDOWN) where the
      edge is high win rate rather than tail MFE. The v4.1.1 APPROVE floor is
      p_conviction ≥ 50.0 on the P track.
    * Verdict driver: OR gate — either track clearing its threshold is sufficient. The
      block names which track drove the APPROVE. An HR-driven trade should be framed
      explicitly as a home-run bet (asymmetric tail payoff is the entire reason to
      take it). A P-driven trade should be framed as a high-base-rate profitability
      play; the thesis should still check whether the contract has enough leverage
      to capture occasional fat-tail outcomes when they come.
    * Fit and regime multipliers live in [0.5, 1.5] and multiplicatively compound with
      the Wilson-lower rate. A strong raw rate with weak fit or a hostile regime ends
      up at a modest conviction — when that happens, call out the discount as a risk.
    * GBM co-scorer (when present): independent lognormal probability estimate from
      the contract's IV and DTE. Treat it as a sanity check on the archetype's implied
      probability — large divergence between archetype P and GBM P is a data point
      worth naming.

- v4 (Policy v4.x, legacy for scanners not in v5_active_scanners) — three pillars
  (Directional Conviction 0.40, Move Potential 0.35, Trade Structure 0.25) combined via
  weighted geometric mean. A lagging pillar collapses the composite — treat it as a
  material risk.

- v3 (Policy v3.x, historical) — Premium Leverage / Underlying Behavior / Setup Quality,
  combined via arithmetic weighted sum.

SETUP RULE / ARCHETYPE CONTEXT.
Alongside the conviction scores, the block may list matched archetypes (v5) and setup
rules (legacy rule library). In v5, the HR archetype and P archetype are the load-bearing
historical evidence — their rates are already folded into the conviction scores. Setup
rules from the legacy library are complementary.

- Reference which archetypes/rules matched and why the pattern applies to this trade.
- For v5 archetypes, cite the Wilson_lower HR/win rate and sample size — these are
  calibrated and already discount low samples. A large-sample archetype with a modest
  rate is often better evidence than a tiny-sample archetype with a dazzling one.
- If multiple archetypes or rules match (confluence), call it out as increasing conviction.
- If zero archetypes match but v5 is active, note it — in v5 no HR archetype means
  hr_conviction defaulted to zero and the APPROVE came entirely from the P track.

Based on this data, produce a thesis that answers:
 1. Why this setup has grand-slam potential (or, if P-driven, why the profitability edge
    is robust and whether the structure still has enough leverage for occasional home runs).
 2. The structural qualities of THIS contract that enable or constrain a 3–5x+ outcome —
    delta (leverage), DTE (time for the move), IV (entry premium richness), expected move
    vs. required move (feasibility), liquidity (actually exitable near MFE).
 3. Key supporting evidence — cite calibrated rates, sample sizes, fit, regime.
 4. Primary risks to monitor — including low fit, thin sample, regime headwind, or
    structural weakness that would cap the upside even if the direction is right.
 5. Conditions that would invalidate the thesis before or during the trade.
 6. Specific numerical exit targets calibrated to the grand-slam thesis (see below).

EXIT TARGET GUIDELINES (calibrated to the grand-slam thesis — NOT generic +25/+50/+100).

- TP1 CONSERVATIVE (+50-80%): The "de-risk" take. Lock in enough to make the trade
  materially winning even if the tail doesn't come.
- TP2 HOME RUN (+100-200%): THIS IS THE BASE CASE for a grand-slam thesis, not a stretch.
  This is the level the archetype's historical HR rate implies is achievable.
- TP3 STRETCH (+300%+): The archetype's long-tail outcome. Sized for partial exit only.
- Calculate underlying price levels using delta; refresh with gamma if the move is large
  (delta expands with ITM-ness, so TP3 underlying prices are typically closer than a
  linear delta extrapolation suggests).
- For CALLs: TP underlying prices ABOVE current, SL BELOW. For PUTs: inverted.
- Stop loss should account for bid-ask spread and 1x daily ATR — if a single ATR of noise
  would stop you out, the sizing or stop level is wrong.
- Time exit: options bought with high grand-slam potential should be exited before theta
  acceleration hollows out the convexity — typically DTE ≤ 5-7 for short-dated trades.
- All option_pnl_pct values are the percentage change in the OPTION price.

Your response MUST be valid JSON matching the exact schema provided."""

THESIS_OUTPUT_SCHEMA = """{
  "setup_summary": "string - One paragraph executive summary framing this as a grand-slam candidate (or a P-driven profitability play with upside optionality), naming the driving archetype and what the HR/P rates imply",
  "thesis": "string - 2-3 paragraphs: (1) the grand-slam case — directional thesis plus why this contract's delta/DTE/IV position it for a 3-5x outcome if the move comes, (2) the calibrated evidence — HR/P convictions, Wilson-lower rates, sample size, fit, regime, (3) what would cap or unlock the tail",
  "supporting_evidence": ["string - 3-5 specific calibrated data points: HR Conviction value, P Conviction value, archetype Wilson-lower rates with sample size, fit × regime multipliers, structural leverage indicators (delta, feasibility ratio, expected vs required move)"],
  "risks": ["string - 2-4 risks — name fit/regime discounts explicitly when they are doing real work, call out structural caps on upside (low delta, wide spread, theta drag) if present"],
  "invalidation_conditions": ["string - 2-3 conditions — include both directional (price/volume breakdown of the thesis) and structural (IV crush, liquidity dry-up, theta acceleration) invalidators"],
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
        "option_pnl_pct": 60.0,
        "underlying_price": 188.00,
        "rationale": "Conservative de-risk — ~1x expected move; locks in a materially positive trade while leaving the tail open"
      },
      {
        "tier": 2,
        "option_pnl_pct": 150.0,
        "underlying_price": 196.00,
        "rationale": "Home-run base case — the level the archetype's historical HR200 rate says is reachable when the thesis plays out"
      },
      {
        "tier": 3,
        "option_pnl_pct": 300.0,
        "underlying_price": 205.00,
        "rationale": "Stretch / long-tail — partial exit only; sized to capture the occasional grand-slam outlier"
      }
    ],
    "stop_loss_level": {
      "option_pnl_pct": -40.0,
      "underlying_price": 168.00,
      "rationale": "Below invalidation level — more than 1 daily ATR away so noise cannot stop you out"
    },
    "time_exit_level": {
      "dte_threshold": 7,
      "rationale": "Exit before theta acceleration hollows out convexity — grand-slam potential lives in gamma, not in held premium"
    },
    "profit_target": "string - Human-readable summary of the tiered exit strategy",
    "stop_loss": "string - Human-readable summary of stop loss",
    "time_exit": "string - Human-readable summary of time-based exit"
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

    # Format the scores section — dispatches on active regime.
    scores = data["scores"]
    scores_text = _format_scores_block(scores, data)

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


def _format_scores_block(scores: dict[str, Any], data: dict[str, Any]) -> str:
    """Format the Scoring Summary — dispatches on active regime (v3 / v4 / v5)."""
    regime = scores.get("regime", "v3")
    final = scores.get("final", 0.0) or 0.0
    quality_tier = data.get("quality_tier") or "N/A"
    policy_version = data.get("policy_version", "N/A")

    if regime == "v5":
        body = _format_v5_scores(scores, final)
    elif regime == "v4":
        dc = scores.get("directional_conviction") or 0.0
        mp = scores.get("move_potential") or 0.0
        ts = scores.get("trade_structure") or 0.0
        body = (
            f"- Final Score (weighted geometric mean): {final:.1f}/100\n"
            f"- Directional Conviction Score: {dc:.1f}/100 (exponent 0.40)\n"
            f"- Move Potential Score: {mp:.1f}/100 (exponent 0.35)\n"
            f"- Trade Structure Score: {ts:.1f}/100 (exponent 0.25)\n"
        )
    else:
        pl = scores.get("premium_leverage") or 0.0
        ub = scores.get("underlying_behavior") or 0.0
        sq = scores.get("setup_quality") or 0.0
        body = (
            f"- Final Score (weighted arithmetic sum): {final:.1f}/100\n"
            f"- Premium Leverage Score: {pl:.1f}/100\n"
            f"- Underlying Behavior Score: {ub:.1f}/100\n"
            f"- Setup Quality Score: {sq:.1f}/100\n"
        )
    return (
        f"\n**Scoring Summary (regime={regime})**\n"
        + body
        + f"- Quality Tier: {quality_tier}\n"
        + f"- Policy Version: {policy_version}\n"
    )


def _format_v5_scores(scores: dict[str, Any], final: float) -> str:
    """Format the v5 dual-conviction scoring block.

    Renders both tracks side-by-side with Wilson bounds, sample size, fit, and
    regime so the LLM can judge the quality of the evidence — not just the
    headline number.
    """
    hr_conv = scores.get("hr_conviction")
    p_conv = scores.get("p_conviction")
    driver = scores.get("verdict_driver") or "neither"
    hr_thresh = scores.get("v5_hr_threshold") or 7.0
    p_thresh = scores.get("v5_p_threshold") or 50.0
    regime_mult = scores.get("regime_alignment")
    gbm_hr = scores.get("gbm_hr_score")
    gbm_p = scores.get("gbm_p_score")
    v5_version = scores.get("v5_scoring_version") or "v5.x"

    def _pct(x: Any) -> str:
        return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else "N/A"

    def _num(x: Any, fmt: str = ".1f") -> str:
        return format(x, fmt) if isinstance(x, (int, float)) else "N/A"

    # HR track
    hr_matched = scores.get("hr_archetype_matched") or "none"
    hr_fit = scores.get("hr_archetype_fit")
    hr_p_point = scores.get("hr_p_point")
    hr_p_lower = scores.get("hr_p_lower")
    hr_p_upper = scores.get("hr_p_upper")
    hr_n = scores.get("hr_n_trades")
    hr_cleared = (
        "CLEARED THRESHOLD"
        if isinstance(hr_conv, (int, float)) and hr_conv >= hr_thresh
        else "below threshold"
    )

    # P track
    p_matched = scores.get("p_archetype_matched") or "none"
    p_fit = scores.get("p_archetype_fit")
    p_win_point = scores.get("p_win_point")
    p_win_lower = scores.get("p_win_lower")
    p_mean_pnl = scores.get("p_mean_pnl_estimate")
    p_cleared = (
        "CLEARED THRESHOLD"
        if isinstance(p_conv, (int, float)) and p_conv >= p_thresh
        else "below threshold"
    )

    lines = [
        f"- Composite Final Score: {final:.1f}/100 (legacy display only in v5)",
        f"- v5 Scoring Version: {v5_version}",
        f"- Verdict Driver: {driver}  "
        f"(OR-gate between HR and P tracks — {driver} had the larger margin above threshold)",
        "",
        "**HR Conviction Track (grand-slam: P(MFE ≥ 200%))**",
        f"  - HR Conviction: {_num(hr_conv)}/20  [{hr_cleared}; v4.1.1 floor = {hr_thresh:.1f}]",
        f"  - Matched HR Archetype: {hr_matched}  (fit = {_num(hr_fit)}/100)",
        f"  - Archetype HR200 Rate — point: {_pct(hr_p_point)}, "
        f"Wilson lower: {_pct(hr_p_lower)}, Wilson upper: {_pct(hr_p_upper)}",
        f"  - Sample Size (n): {hr_n if hr_n is not None else 'N/A'}  "
        "(the Wilson lower bound is what drives conviction; "
        "a modest point rate at large n can beat a dazzling rate at tiny n)",
        "",
        "**P Conviction Track (profitability: Wilson_lower(P_win) × normalized P&L)**",
        f"  - P Conviction: {_num(p_conv)}/100  [{p_cleared}; v4.1.1 floor = {p_thresh:.1f}]",
        f"  - Matched P Archetype: {p_matched}  (fit = {_num(p_fit)}/100)",
        f"  - Archetype Win Rate — point: {_pct(p_win_point)}, Wilson lower: {_pct(p_win_lower)}",
        f"  - Cohort Mean P&L: {_num(p_mean_pnl)}%",
        "",
        "**Shared Modifiers**",
        f"  - Regime Alignment Multiplier: {_num(regime_mult, '.2f')}  "
        "(applied multiplicatively to both convictions; <1.0 = regime headwind, >1.0 = tailwind)",
        f"  - GBM Co-Scorer — P(HR200): {_num(gbm_hr)}/100, P(profit): {_num(gbm_p)}/100  "
        "(independent lognormal sanity check from contract IV + DTE; "
        "large divergence from archetype rates is a data point)",
    ]
    return "\n".join(lines) + "\n"


CONVEX_THESIS_SYSTEM_PROMPT = """You are an expert options trading analyst generating a thesis for an approved single-leg long options trade selected by the OSS Convex pipeline.

NORTH STAR. The trader is a sharpshooter hunting grand-slam outcomes — trades whose maximum favorable excursion (MFE) reaches +200% or more on the option. A +30% winner is a consolation prize, not the goal. Your thesis must judge the trade on two dimensions simultaneously:

  1. UPSIDE CAPACITY. If the underlying delivers the move the setup implies, does this specific contract have the structural leverage (delta, DTE, IV regime, convexity) to 3–5x or more?
  2. EVIDENCE QUALITY. The Convex pipeline applies four sequential gates — Kinetic Universe, Catalyst, Volatility Mispricing, Contract Selection — and each gate carries a strength score. A trade that cleared every gate at high strength is structurally cleaner than one that scraped through.

CONVEX SCORING MODEL. The evaluation is described by:

- **Tier (A/B/C):** A is awarded only when every dimension is strong (Stage 2 strength ≥ 0.75 AND Stage 3 composite ≥ 0.70 AND Stage 4 strength ≥ 0.85). B is "all gates passed at moderate strength". C is "all gates passed at borderline strength". Tier A is the home-run bucket; Tier C still passed every gate but is best treated as a small probe.
- **Composite strength (0.00–1.00):** within-tier ranking by Stage 3 strength (cheaper convexity ranks first). Use it to gauge how attractive this trade is *relative to other Convex APPROVES today*, not as an absolute probability.
- **Smart Money Confirmation:** when True, an Unusual Volume signal aligned directionally with the Stage 3 thesis. Strong tailwind. When False, the trade is structurally clean but lacks that confirmation.

STAGE BLOCKS. The four stage payloads in the input each carry: a result (PASS/FAIL — only PASS reaches you), a strength score, a free-text summary, and a `criteria` dict with the specific values that drove the gate. Cite specific numbers when framing the thesis.

- **Stage 1 — Kinetic Universe:** establishes that the underlying has the structural conditions to move (options volume, market cap, ATM spread, tail-event count, HV regime). This is a backdrop check, not a thesis driver.
- **Stage 2 — Catalyst:** identifies *why now*. Date-known catalyst (earnings/FDA), compression breakout, unusual volume, or sympathy move. The catalyst is the most important part of the directional thesis — call it out by name and explain its expected mechanism.
- **Stage 3 — Volatility Mispricing:** confirms that IV is cheap relative to historical realized vol. Cite IV rank, IV percentile, and IV/HV ratio. This is the structural edge — buying convexity when the market is under-pricing future moves.
- **Stage 4 — Contract Selection:** chose a specific contract within the delta/DTE/spread envelope. Cite the delta, DTE, spread, and open interest. Note whether the chosen contract is in the ideal sub-range (drives Tier A) or at the periphery.

DIRECTION. The candidate carries a `direction` field (``bullish`` / ``bearish`` / ``ambiguous``). Use the matching contract type. For ``ambiguous`` direction, frame the thesis around volatility expansion (long premium plays both sides via the IV expansion).

Based on this data, produce a thesis that answers:
 1. Why this Convex trade has grand-slam potential — frame Stage 2 catalyst + Stage 3 vol mispricing + Stage 4 contract leverage as a coherent story.
 2. What the catalyst's expected mechanism is and how it gets priced in.
 3. Whether the contract structure is leveraged enough to capture the move (delta/DTE trade-off, theta drag, convexity).
 4. What invalidates the thesis (clean exit conditions).

OUTPUT — RETURN VALID JSON MATCHING EXACTLY THIS STRUCTURE:

{
  "setup_summary": "string — one-sentence framing (e.g. 'Compression breakout with cheap IV on AAPL post-earnings')",
  "thesis": "string — 2–4 sentences explaining the grand-slam mechanism",
  "supporting_evidence": ["string", ...],
  "risks": ["string", ...],
  "invalidation_conditions": ["string", ...],
  "exit_plan": {
    "take_profits": [
      {"tier": 1, "option_pnl_pct": 60.0, "underlying_price": 0.0, "rationale": "Conservative de-risk — ~1x expected move"},
      {"tier": 2, "option_pnl_pct": 150.0, "underlying_price": 0.0, "rationale": "Home-run base case"},
      {"tier": 3, "option_pnl_pct": 300.0, "underlying_price": 0.0, "rationale": "Stretch / long-tail"}
    ],
    "stop_loss_level": {"option_pnl_pct": -40.0, "underlying_price": 0.0, "rationale": "Below thesis invalidation"},
    "time_exit_level": {"dte_threshold": 7, "rationale": "Exit before theta acceleration"},
    "profit_target": "string — human-readable summary",
    "stop_loss": "string — human-readable summary",
    "time_exit": "string — human-readable summary"
  }
}"""


def build_convex_thesis_prompt(
    *,
    ticker: str,
    direction: str,
    tier: str,
    composite_strength: float,
    smart_money_confirmation: bool,
    stages: dict[str, Any],
    selected_contract: dict[str, Any],
    policy_version: str,
) -> str:
    """Build a Convex-shaped prompt for the LLM thesis generator.

    The output asks the model to return the same JSON contract used by
    the legacy thesis path, so ``parse_thesis_response`` and
    ``ThesisOutput.from_dict`` work without modification.
    """
    contract_lines = []
    if selected_contract:
        contract_lines.append(f"- Type: {selected_contract.get('option_type', '?')}")
        contract_lines.append(f"- Strike: ${selected_contract.get('strike', 0):.2f}")
        contract_lines.append(f"- Expiry: {selected_contract.get('expiry', '?')}")
        contract_lines.append(f"- DTE: {selected_contract.get('dte', '?')}")
        delta = selected_contract.get("delta")
        if delta is not None:
            contract_lines.append(f"- Delta: {delta:.3f}")
        bid = selected_contract.get("bid")
        ask = selected_contract.get("ask")
        if bid is not None and ask is not None:
            contract_lines.append(f"- Bid/Ask: ${bid:.2f} / ${ask:.2f}")
        oi = selected_contract.get("open_interest")
        vol = selected_contract.get("volume")
        if oi is not None:
            contract_lines.append(f"- Open Interest: {oi:,}")
        if vol is not None:
            contract_lines.append(f"- Volume: {vol:,}")
    contract_text = "\n".join(contract_lines) if contract_lines else "- (no contract data)"

    stage_blocks = []
    for stage_num in (1, 2, 3, 4):
        payload = stages.get(f"stage_{stage_num}")
        if not payload:
            continue
        strength = payload.get("strength")
        strength_str = f"{strength:.2f}" if strength is not None else "—"
        criteria = payload.get("criteria") or {}
        criteria_str = (
            ", ".join(f"{k}={v}" for k, v in criteria.items())
            if criteria
            else "(none)"
        )
        stage_blocks.append(
            f"**Stage {stage_num} — {payload.get('stage_name', '?')}**\n"
            f"- Result: {payload.get('result', '?')} (strength {strength_str})\n"
            f"- Summary: {payload.get('summary', '')}\n"
            f"- Criteria: {criteria_str}"
        )
    stages_text = "\n\n".join(stage_blocks) if stage_blocks else "(no stage data)"

    sm_text = (
        "True (UV scanner shows directionally aligned unusual volume — strong tailwind)"
        if smart_money_confirmation
        else "False (no UV confirmation — trade is structurally clean but lacks UV tailwind)"
    )

    user_prompt = f"""**Ticker:** {ticker}
**Direction:** {direction}
**Convex Tier:** {tier}
**Composite Strength:** {composite_strength:.2f} (within-tier ranking, 0.00–1.00)
**Smart Money Confirmation:** {sm_text}
**Policy Version:** {policy_version}

**Selected Contract**
{contract_text}

**Convex Stage Walkthrough**

{stages_text}

Generate the thesis JSON per the system prompt. Cite specific stage numbers/values to ground every claim. The setup_summary must name the catalyst type from Stage 2."""

    return f"{CONVEX_THESIS_SYSTEM_PROMPT}\n\n---\n\n{user_prompt}"


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
