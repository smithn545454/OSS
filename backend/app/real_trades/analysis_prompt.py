"""LLM prompt for real trade trend analysis."""

from __future__ import annotations

from typing import Any


def build_trade_analysis_prompt(
    trades: list[dict[str, Any]],
    context: dict[str, Any],
) -> str:
    """Build the prompt for analyzing real trade patterns.

    Unlike Pattern Discovery (which finds archetypes in paper trades),
    this focuses on behavioral insights and edge patterns from trades
    the user actually executed with real money.
    """
    trade_summaries = []
    for t in trades:
        snap = t.get("snapshot", {})
        pnl = t.get("realized_pnl_pct")
        pnl_str = f"{float(pnl):+.1f}%" if pnl is not None else "OPEN"

        pillar_str = ""
        for ps in snap.get("pillar_scores", []):
            pillar_str += f"  {ps.get('pillar_id')}: {ps.get('score', 0)}\n"

        trade_summaries.append(
            f"Trade: {snap.get('underlying_ticker', '?')} "
            f"{snap.get('option_type', '?')} ${snap.get('strike', '?')} "
            f"Exp {snap.get('expiration_date', '?')}\n"
            f"  Scanner: {snap.get('scanner_source', 'unknown')}\n"
            f"  Verdict: {snap.get('verdict', '?')} | "
            f"Score: {snap.get('final_score', '?')} | "
            f"DTE: {snap.get('dte', '?')} | "
            f"IV: {snap.get('iv', '?')}\n"
            f"  Entry: ${t.get('entry_price', '?')} | "
            f"Exit: ${t.get('exit_price', '?')} | "
            f"P&L: {pnl_str}\n"
            f"  Exit Reason: {t.get('exit_reason', 'N/A')}\n"
            f"  Pillar Scores:\n{pillar_str}"
            f"  Theta-Adj EV: {snap.get('theta_adjusted_ev', 'N/A')}\n"
            f"  Conviction: {snap.get('conviction_score', 'N/A')}\n"
            f"  Entry Notes: {t.get('entry_notes', '')}\n"
            f"  Exit Notes: {t.get('exit_notes', '')}\n"
        )

    trades_block = "\n---\n".join(trade_summaries)

    return f"""You are analyzing REAL trades executed by an options trader. These are trades
they chose to execute with real money from a system that identifies single-leg long options.

CONTEXT:
- Total real trades: {context.get('total_trades', 0)}
- Closed trades: {context.get('closed_trades', 0)}
- Open trades: {context.get('open_trades', 0)}
- Win rate: {context.get('win_rate', 0)}%
- Average return: {context.get('avg_return', 0)}%
- Best trade: {context.get('best_return', 'N/A')}%
- Worst trade: {context.get('worst_return', 'N/A')}%

TRADES:
{trades_block}

Analyze these real trades and provide insights in the following JSON format:

{{
  "summary": {{
    "total_trades": <int>,
    "win_rate_pct": <float>,
    "avg_return_pct": <float>,
    "best_trade": "<ticker> <return>%",
    "worst_trade": "<ticker> <return>%",
    "key_observation": "<1-2 sentence summary>"
  }},
  "edge_patterns": [
    {{
      "name": "<pattern name>",
      "description": "<what this pattern is>",
      "trade_count": <int>,
      "win_rate_pct": <float>,
      "avg_return_pct": <float>,
      "recommendation": "<actionable advice>"
    }}
  ],
  "behavioral_insights": [
    {{
      "type": "<entry_timing|exit_discipline|position_sizing|selection_bias|other>",
      "finding": "<what was observed>",
      "severity": "<info|warning|critical>",
      "recommendation": "<what to do about it>"
    }}
  ],
  "risk_warnings": [
    {{
      "type": "<concentration|sizing|timing|other>",
      "finding": "<what the risk is>",
      "severity": "<info|warning|critical>"
    }}
  ],
  "next_steps": [
    "<actionable recommendation>"
  ]
}}

Focus on:
1. EDGE PATTERNS: Which scanner + pillar score + DTE combinations produce the best results?
2. BEHAVIORAL INSIGHTS: Entry timing, exit discipline, position sizing patterns
3. RISK WARNINGS: Over-concentration in one scanner/ticker/direction
4. ENTRY QUALITY: Were entry prices reasonable relative to the evaluation data?
5. EXIT ALIGNMENT: Did exits match thesis invalidation or profit targets?

Be specific and data-driven. Reference actual trades by ticker when making observations.
Return ONLY valid JSON, no other text."""
