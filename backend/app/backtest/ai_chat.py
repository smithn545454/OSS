"""Backtest AI Chat: Interactive analysis of backtest results.

Provides an AI-powered chat interface for querying backtest results.
Uses pre-computed analysis context + trade data to answer questions
about performance, patterns, and optimization opportunities.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """\
You are an expert options trading analyst reviewing backtest results for an \
automated option scanner system. The system evaluates single-leg long options \
trades (calls and puts) using a deterministic 8-stage pipeline with 4 scanners \
(Breakout, Compression, Cheap Options, Unusual Volume).

Your role is to analyze the backtest data provided and answer questions about:
- Overall performance and key metrics
- Scanner-level and strategy-level performance breakdowns
- Pattern identification (what works, what doesn't)
- Risk assessment and drawdown analysis
- Actionable recommendations for configuration tuning

Be specific and data-driven. Reference actual numbers from the analysis context. \
When suggesting configuration changes, explain the expected impact based on the data.

Keep responses concise and actionable. Use markdown formatting for clarity."""

CHAT_MODEL = "claude-haiku-4-5-20251001"
MAX_CHAT_TOKENS = 2000


async def _load_analysis_context(
    run_id: str,
    s3_bucket: str,
) -> Optional[dict[str, Any]]:
    """Try to load pre-computed analysis context from S3."""
    try:
        import boto3
        s3 = boto3.client("s3")
        key = f"results/RUN#{run_id}/analysis_context.json"
        response = s3.get_object(Bucket=s3_bucket, Key=key)
        return json.loads(response["Body"].read())
    except Exception:
        return None


async def _build_context_from_db(run_id: str) -> dict[str, Any]:
    """Build analysis context from DynamoDB if S3 export is unavailable."""
    run = await BacktestRunTable.get(run_id)
    if not run:
        return {}

    summary = run.get("summary", {})
    config = run.get("config", {})
    trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=10000)

    # Build basic context
    context: dict[str, Any] = {
        "summary": {
            "total_trades": summary.get("total_trades", len(trade_dicts)),
            "win_rate": summary.get("win_rate", 0),
            "net_pnl": summary.get("net_pnl", 0),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "profit_factor": summary.get("profit_factor"),
            "max_drawdown_pct": summary.get("max_drawdown_pct", 0),
        },
        "config": {
            "start_date": config.get("start_date"),
            "end_date": config.get("end_date"),
            "scanners": config.get("scanners_enabled", []),
            "starting_capital": config.get("starting_capital", 10000),
        },
    }

    # Scanner breakdown
    scanner_stats: dict[str, dict] = {}
    for t in trade_dicts:
        scanner = t.get("scanner_type", "UNKNOWN")
        if scanner not in scanner_stats:
            scanner_stats[scanner] = {"trades": 0, "wins": 0, "total_pnl": 0}
        scanner_stats[scanner]["trades"] += 1
        pnl_pct = float(t.get("pnl_pct", 0) or 0)
        if pnl_pct > 0:
            scanner_stats[scanner]["wins"] += 1
        scanner_stats[scanner]["total_pnl"] += pnl_pct

    context["scanner_performance"] = [
        {
            "scanner": s,
            "trades": d["trades"],
            "win_rate": round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else 0,
            "avg_pnl_pct": round(d["total_pnl"] / d["trades"], 2) if d["trades"] else 0,
        }
        for s, d in scanner_stats.items()
    ]

    # Top winners/losers
    sorted_by_pnl = sorted(
        [t for t in trade_dicts if t.get("pnl_pct") is not None],
        key=lambda t: float(t.get("pnl_pct", 0)),
    )
    if sorted_by_pnl:
        context["top_winners"] = [
            {
                "ticker": t.get("ticker"),
                "scanner": t.get("scanner_type"),
                "pnl_pct": t.get("pnl_pct"),
                "entry_date": t.get("entry_date"),
                "exit_reason": t.get("exit_reason"),
            }
            for t in sorted_by_pnl[-5:]
        ]
        context["top_losers"] = [
            {
                "ticker": t.get("ticker"),
                "scanner": t.get("scanner_type"),
                "pnl_pct": t.get("pnl_pct"),
                "entry_date": t.get("entry_date"),
                "exit_reason": t.get("exit_reason"),
            }
            for t in sorted_by_pnl[:5]
        ]

    # Exit reason distribution
    exit_reasons: dict[str, int] = {}
    for t in trade_dicts:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    context["exit_distribution"] = exit_reasons

    return context


def _build_chat_messages(
    analysis_context: dict[str, Any],
    user_message: str,
    conversation_history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build the messages array for the Claude API call."""
    # First message provides the analysis context
    context_msg = (
        "Here is the analysis context for this backtest run:\n\n"
        f"```json\n{json.dumps(analysis_context, indent=2, default=str)}\n```\n\n"
        "Use this data to answer the user's questions about the backtest results."
    )

    messages: list[dict[str, str]] = [
        {"role": "user", "content": context_msg},
        {
            "role": "assistant",
            "content": "I've reviewed the backtest analysis data. "
                       "What would you like to know about the results?",
        },
    ]

    # Add conversation history
    for msg in conversation_history:
        messages.append(msg)

    # Add current user message
    messages.append({"role": "user", "content": user_message})

    return messages


async def chat_with_backtest(
    run_id: str,
    message: str,
    conversation_history: list[dict[str, str]] | None = None,
    s3_bucket: str = "",
) -> dict[str, Any]:
    """Send a chat message about a backtest run and get an AI response.

    Args:
        run_id: Backtest run ID to analyze
        message: User's question or message
        conversation_history: Previous messages in this conversation
        s3_bucket: S3 bucket for loading pre-computed context

    Returns:
        Dict with response text and metadata.
    """
    import os

    if not conversation_history:
        conversation_history = []

    # Load analysis context (try S3 first, fall back to DB)
    analysis_context = None
    if s3_bucket:
        analysis_context = await _load_analysis_context(run_id, s3_bucket)

    if not analysis_context:
        analysis_context = await _build_context_from_db(run_id)

    if not analysis_context:
        return {
            "response": "I couldn't find any data for this backtest run. "
                       "Make sure the run has completed successfully.",
            "error": "no_data",
        }

    # Build messages
    messages = _build_chat_messages(analysis_context, message, conversation_history)

    # Call Claude
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            # Try Secrets Manager
            from app.config import get_settings
            settings = get_settings()
            api_key = settings.anthropic_api_key or ""

        if not api_key:
            return {
                "response": "AI chat is not available — Anthropic API key not configured.",
                "error": "no_api_key",
            }

        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=CHAT_MODEL,
            max_tokens=MAX_CHAT_TOKENS,
            system=CHAT_SYSTEM_PROMPT,
            messages=messages,
        )

        content = ""
        if response.content:
            content = response.content[0].text

        return {
            "response": content,
            "tokens_used": response.usage.output_tokens if response.usage else 0,
            "model": CHAT_MODEL,
        }

    except Exception as e:
        logger.error(f"AI chat failed for run {run_id}: {e}")
        return {
            "response": f"AI chat encountered an error: {str(e)}",
            "error": str(e),
        }
