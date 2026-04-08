"""Prompt template and models for AI stock summary generation."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

STOCK_SUMMARY_SYSTEM_PROMPT = """You are a senior equity research analyst preparing a \
pre-trade briefing for an options trader. The trader has identified an options opportunity \
on the stock below and needs fundamental context BEFORE deciding whether to trade.

Your job is to surface anything that would materially affect an options position on this \
stock. Focus on ACTIONABLE information — not generic company overviews.

Key areas to cover:
1. Company snapshot — what this company does, in 1-2 sentences
2. Material recent events — acquisitions/mergers/buyouts, FDA decisions, earnings \
surprises, management changes, lawsuits, regulatory actions, restatements, activist \
investors, special dividends, spin-offs, delistings
3. Critical trading considerations — anything that caps upside (acquisition at fixed \
price), creates binary risk (pending FDA decision, earnings within expiration window), \
introduces unusual volatility (biotech catalyst), or changes the fundamental picture
4. Impact on the specific option trade — connect the above to the specific contract \
(strike, expiration, direction) the trader is evaluating

PRIORITY RULES:
- If the company is being acquired at a fixed price, this is the MOST critical fact — \
it caps the stock's upside and likely makes OTM calls above the deal price worthless. \
Lead with this.
- If a major binary event (earnings, FDA ruling, trial readout) falls within the option's \
expiration window, call this out prominently with the expected date
- If the stock is a penny stock, micro-cap, or has low liquidity, flag the additional risk
- If there are no material recent events or red flags, say so clearly — "no red flags \
found" is valuable information
- Be direct and opinionated — the trader needs your honest assessment, not hedged language

Your response MUST be valid JSON matching the exact schema provided."""

STOCK_SUMMARY_OUTPUT_SCHEMA = """{
  "company_snapshot": "1-2 sentence description of what the company does",
  "sector_context": "Brief sector/industry context relevant to trading",
  "material_events": [
    {
      "event": "What happened",
      "date": "When (approximate)",
      "impact": "How this affects the stock and options",
      "severity": "HIGH or MEDIUM or LOW"
    }
  ],
  "trading_considerations": [
    "Each consideration as a clear, actionable statement"
  ],
  "trade_impact_assessment": "2-3 sentences connecting fundamental context to the option contract",
  "risk_level": "LOW or MODERATE or ELEVATED or HIGH",
  "risk_level_rationale": "One sentence explaining the risk level"
}"""


@dataclass
class StockSummaryInput:
    """Input data for stock summary generation."""

    ticker: str
    company_name: str = ""
    company_description: str = ""
    sic_description: str = ""
    market_cap: Optional[float] = None
    price: Optional[float] = None
    news_items: list[dict[str, Any]] = field(default_factory=list)
    sec_filings: list[dict[str, str]] = field(default_factory=list)
    option_type: str = ""
    strike: Optional[float] = None
    expiration: str = ""
    dte: Optional[int] = None
    mid: Optional[float] = None
    verdict: str = ""
    final_score: Optional[float] = None


def _format_market_cap(cap: Optional[float]) -> str:
    if cap is None:
        return "Unknown"
    if cap >= 1_000_000_000_000:
        return f"${cap / 1_000_000_000_000:.1f}T"
    if cap >= 1_000_000_000:
        return f"${cap / 1_000_000_000:.1f}B"
    if cap >= 1_000_000:
        return f"${cap / 1_000_000:.0f}M"
    return f"${cap:,.0f}"


def build_stock_summary_prompt(data: StockSummaryInput) -> str:
    """Build the user prompt for stock summary generation."""
    sections = []

    # Company profile
    sections.append("## Company Profile")
    sections.append(f"- Ticker: {data.ticker}")
    if data.company_name:
        sections.append(f"- Company Name: {data.company_name}")
    if data.company_description:
        sections.append(f"- Description: {data.company_description}")
    if data.sic_description:
        sections.append(f"- Sector/Industry: {data.sic_description}")
    sections.append(f"- Market Cap: {_format_market_cap(data.market_cap)}")
    if data.price is not None:
        sections.append(f"- Current Price: ${data.price:.2f}")

    # Recent news
    sections.append("")
    sections.append("## Recent News (last 30 days)")
    if data.news_items:
        for item in data.news_items[:10]:
            date = item.get("date", "Unknown")
            title = item.get("title", "")
            source = item.get("source", "")
            summary = item.get("summary", "")
            line = f"- [{date}] {title}"
            if source:
                line += f" ({source})"
            if summary:
                line += f": {summary[:200]}"
            sections.append(line)
    else:
        sections.append("No recent news articles found for this ticker.")

    # SEC filings
    sections.append("")
    sections.append("## Recent SEC Filings (last 30 days)")
    if data.sec_filings:
        for filing in data.sec_filings[:10]:
            form = filing.get("form", "8-K")
            date = filing.get("filingDate", "Unknown")
            desc = filing.get("primaryDocument", "")
            line = f"- [{date}] {form}"
            if desc:
                line += f": {desc}"
            sections.append(line)
    else:
        sections.append("No recent 8-K filings found.")

    # Option contract context
    sections.append("")
    sections.append("## Option Contract Being Evaluated")
    if data.option_type:
        sections.append(f"- Type: {data.option_type}")
    if data.strike is not None:
        sections.append(f"- Strike: ${data.strike:.2f}")
    if data.expiration:
        dte_str = f" ({data.dte} DTE)" if data.dte is not None else ""
        sections.append(f"- Expiration: {data.expiration}{dte_str}")
    if data.mid is not None:
        sections.append(f"- Premium: ${data.mid:.2f}")
    if data.verdict:
        score_str = f" (Score: {data.final_score:.0f}/100)" if data.final_score else ""
        sections.append(f"- Verdict: {data.verdict}{score_str}")

    # Output schema
    sections.append("")
    sections.append("## Required Output Format")
    sections.append(
        "Respond with ONLY valid JSON matching this schema "
        "(no markdown, no code blocks):"
    )
    sections.append(STOCK_SUMMARY_OUTPUT_SCHEMA)

    return "\n".join(sections)


def parse_stock_summary_response(response: str) -> dict[str, Any]:
    """Parse LLM response into stock summary dict.

    Handles JSON wrapped in markdown code blocks.
    """
    text = response.strip()

    # Strip markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last line if it's ```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    # Try direct JSON parse
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            raise ValueError(f"Could not parse JSON from response: {text[:200]}")

    # Validate required fields
    required = [
        "company_snapshot",
        "material_events",
        "trading_considerations",
        "trade_impact_assessment",
        "risk_level",
    ]
    for field_name in required:
        if field_name not in data:
            raise ValueError(f"Missing required field: {field_name}")

    return data
