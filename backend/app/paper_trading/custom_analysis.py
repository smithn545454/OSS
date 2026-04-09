"""Custom Analysis engine for paper trading.

Allows users to ask arbitrary analytical questions about paper trade data.
Returns freeform markdown analysis + structured suggested rules that can
be saved as setup rules.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from app.core.schemas import PaperPosition
from app.db.tables import PaperPositionTable
from app.llm.provider import get_provider
from app.paper_trading.custom_analysis_prompt import (
    build_custom_analysis_prompt,
    parse_custom_analysis_response,
)
from app.paper_trading.pattern_discovery import (
    MAX_PROMPT_TOKENS,
    PROMPT_OVERHEAD_TOKENS,
    build_trade_csv,
)
from app.paper_trading.trade_statistics import compute_comparative_statistics

logger = logging.getLogger(__name__)

# DynamoDB key patterns for custom analysis results
CUSTOM_ANALYSIS_PK_PREFIX = "CUSTOM_ANALYSIS#"
CUSTOM_ANALYSIS_INDEX_PK = "CUSTOM_ANALYSIS_INDEX"


async def create_custom_analysis_stub(
    prompt: str,
    period: Optional[str] = None,
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    min_return: Optional[float] = None,
) -> dict[str, Any]:
    """Create a 'running' analysis stub and return immediately.

    Args:
        prompt: The user's analytical question
        period: Filter by entry_date (7d, 14d, 30d, 90d, all)
        verdict: Filter by verdict (APPROVE, WATCH)
        scanner: Filter by scanner_source
        min_return: Only include trades with return >= this %

    Returns:
        Dict with analysis_id and status ("running" or "insufficient_data")
    """
    # Quick check: count closed trades
    closed_pos = await PaperPositionTable.list_closed()

    # Apply filters
    filtered = _apply_filters(closed_pos, period, verdict, scanner, min_return)
    closed = [
        p for p in filtered
        if str(getattr(p.status, "value", p.status)) == "CLOSED"
    ]

    if len(closed) < 3:
        return {
            "analysis_id": None,
            "status": "insufficient_data",
            "message": (
                f"Not enough closed trades for analysis. "
                f"Found {len(closed)} closed trades, need at least 3."
            ),
            "positions_analyzed": len(closed),
        }

    # Create a "running" stub in DynamoDB
    analysis_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    meta_item = {
        "PK": f"{CUSTOM_ANALYSIS_PK_PREFIX}{analysis_id}",
        "SK": "META",
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "user_prompt": prompt,
        "positions_analyzed": len(closed),
        "period": period or "all",
        "verdict_filter": verdict,
        "scanner_filter": scanner,
        "min_return_filter": min_return,
    }
    await db.put_item(PaperPositionTable.TABLE, meta_item)

    index_item = {
        "PK": CUSTOM_ANALYSIS_INDEX_PK,
        "SK": f"{now}#{analysis_id}",
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "user_prompt": prompt,
        "positions_analyzed": len(closed),
        "suggested_rule_count": 0,
        "period": period or "all",
    }
    await db.put_item(PaperPositionTable.TABLE, index_item)

    return {
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "positions_analyzed": len(closed),
    }


async def run_custom_analysis(
    analysis_id: str,
    prompt: str,
    period: Optional[str] = None,
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    min_return: Optional[float] = None,
) -> dict[str, Any]:
    """Run custom analysis (worker — no timeout pressure).

    Called asynchronously by the Lambda worker handler.

    Args:
        analysis_id: Pre-created analysis ID to update
        prompt: The user's analytical question
        period: Filter by entry_date
        verdict: Filter by verdict
        scanner: Filter by scanner_source
        min_return: Only include trades with return >= this %

    Returns:
        Analysis results with markdown analysis and suggested rules
    """
    logger.info(f"run_custom_analysis (analysis={analysis_id})")
    from app.db.dynamodb import get_dynamodb

    now = datetime.now(timezone.utc).isoformat()
    db = get_dynamodb()

    try:
        # Gather positions
        open_pos = await PaperPositionTable.list_open()
        closed_pos = await PaperPositionTable.list_closed()
        all_positions = open_pos + closed_pos

        # Apply filters
        filtered = _apply_filters(all_positions, period, verdict, scanner, min_return)
        closed = [
            p for p in filtered
            if str(getattr(p.status, "value", p.status)) == "CLOSED"
        ]

        total_closed = len(closed)
        wins = sum(1 for p in closed if p.current_pnl_pct > 0)
        avg_return = sum(p.current_pnl_pct for p in closed) / total_closed if total_closed else 0

        context = {
            "total_trades": total_closed,
            "win_rate": round(wins / total_closed * 100, 1) if total_closed else 0,
            "avg_return": round(avg_return, 2),
        }

        # Pre-compute comparative statistics
        statistics_summary = compute_comparative_statistics(closed)

        # Fetch sector map
        try:
            from app.db.tables import SP500TickerTable
            sector_map = await SP500TickerTable.get_sector_map()
        except Exception:
            sector_map = {}

        # Sort by most recent, build CSV, trim to fit token budget
        closed_sorted = sorted(closed, key=lambda p: p.entry_date, reverse=True)
        trade_csv = build_trade_csv(closed_sorted, sector_map)

        # Estimate tokens — same logic as pattern discovery
        chars_per_token = 1.4
        # Custom analysis has more overhead due to statistics + user prompt
        overhead = PROMPT_OVERHEAD_TOKENS + len(statistics_summary) / chars_per_token + 2000
        csv_chars = len(trade_csv)
        estimated_tokens = csv_chars / chars_per_token + overhead
        logger.info(
            f"CSV built: {len(closed_sorted)} rows, {csv_chars} chars, "
            f"est_tokens={int(estimated_tokens)}, limit={MAX_PROMPT_TOKENS}"
        )
        sampled = estimated_tokens > MAX_PROMPT_TOKENS

        if sampled:
            csv_lines = trade_csv.split("\n")
            header = csv_lines[0]
            data_lines = [line for line in csv_lines[1:] if line.strip()]
            target_chars = int((MAX_PROMPT_TOKENS - overhead) * chars_per_token)
            trimmed = []
            char_count = len(header) + 1
            for line in data_lines:
                char_count += len(line) + 1
                if char_count > target_chars:
                    break
                trimmed.append(line)
            trade_csv = header + "\n" + "\n".join(trimmed) + "\n"
            closed_sorted = closed_sorted[:len(trimmed)]
            logger.info(
                f"Trimmed to {len(trimmed)} of {total_closed} trades "
                f"(csv_chars={len(trade_csv)})"
            )

        context["sampled"] = sampled
        context["sample_size"] = len(closed_sorted)

        # Build prompt with user question + statistics + CSV
        full_prompt = build_custom_analysis_prompt(
            user_prompt=prompt,
            trade_csv=trade_csv,
            statistics_summary=statistics_summary,
            context=context,
        )
        logger.info(f"Prompt built: {len(full_prompt)} chars")

        provider = get_provider("anthropic")
        llm_response = await provider.generate(full_prompt, max_tokens=8000)

        if not llm_response.success:
            raise RuntimeError(f"LLM returned error: {llm_response.error}")

        # Parse response into analysis + suggested rules
        parsed = parse_custom_analysis_response(llm_response.content)
        analysis_text = parsed["analysis"]
        suggested_rules = parsed["suggested_rules"]

        # Update stub with results
        meta_updates = {
            "status": "complete",
            "positions_analyzed": total_closed,
            "analysis": analysis_text,
            "suggested_rule_count": len(suggested_rules),
            "context": context,
        }
        await db.update_item(
            PaperPositionTable.TABLE,
            f"{CUSTOM_ANALYSIS_PK_PREFIX}{analysis_id}",
            "META",
            meta_updates,
        )

        # Update index item status
        index_items = await db.query(PaperPositionTable.TABLE, CUSTOM_ANALYSIS_INDEX_PK)
        for item in index_items:
            if item.get("analysis_id") == analysis_id:
                await db.update_item(
                    PaperPositionTable.TABLE,
                    CUSTOM_ANALYSIS_INDEX_PK,
                    item["SK"],
                    {"status": "complete", "suggested_rule_count": len(suggested_rules)},
                )
                break

        # Store each suggested rule as separate item
        for i, rule in enumerate(suggested_rules):
            rule_item = {
                "PK": f"{CUSTOM_ANALYSIS_PK_PREFIX}{analysis_id}",
                "SK": f"RULE#{i:03d}",
                **rule,
            }
            await db.put_item(PaperPositionTable.TABLE, rule_item)

        logger.info(
            f"Custom analysis {analysis_id} complete: "
            f"{len(suggested_rules)} rules from {total_closed} trades"
        )

        return {
            "analysis_id": analysis_id,
            "status": "complete",
            "created_at": now,
            "positions_analyzed": total_closed,
            "analysis": analysis_text,
            "suggested_rules": suggested_rules,
            "context": context,
        }

    except Exception as e:
        logger.error(f"Custom analysis {analysis_id} failed: {e}")
        try:
            await db.update_item(
                PaperPositionTable.TABLE,
                f"{CUSTOM_ANALYSIS_PK_PREFIX}{analysis_id}",
                "META",
                {"status": "error", "error_message": str(e)},
            )
            index_items = await db.query(PaperPositionTable.TABLE, CUSTOM_ANALYSIS_INDEX_PK)
            for item in index_items:
                if item.get("analysis_id") == analysis_id:
                    await db.update_item(
                        PaperPositionTable.TABLE,
                        CUSTOM_ANALYSIS_INDEX_PK,
                        item["SK"],
                        {"status": "error"},
                    )
                    break
        except Exception:
            logger.error(f"Failed to update analysis {analysis_id} error status")

        return {
            "analysis_id": analysis_id,
            "status": "error",
            "message": f"Custom analysis failed: {str(e)}",
            "positions_analyzed": 0,
            "analysis": "",
            "suggested_rules": [],
        }


async def list_custom_analyses(limit: int = 10) -> list[dict[str, Any]]:
    """List recent custom analyses.

    Returns:
        List of analysis metadata dicts, most recent first
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    items = await db.query(
        PaperPositionTable.TABLE,
        CUSTOM_ANALYSIS_INDEX_PK,
        limit=limit,
        scan_forward=False,
    )

    analyses = []
    for item in items:
        item.pop("PK", None)
        item.pop("SK", None)
        analyses.append(item)

    return analyses


async def get_custom_analysis(analysis_id: str) -> Optional[dict[str, Any]]:
    """Get a specific custom analysis with its suggested rules.

    Args:
        analysis_id: The analysis UUID

    Returns:
        Analysis with metadata, markdown analysis, and suggested rules
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    items = await db.query(
        PaperPositionTable.TABLE,
        f"{CUSTOM_ANALYSIS_PK_PREFIX}{analysis_id}",
    )

    if not items:
        return None

    meta = None
    suggested_rules = []
    for item in items:
        sk = item.pop("SK", "")
        item.pop("PK", None)
        if sk == "META":
            meta = item
        elif sk.startswith("RULE#"):
            suggested_rules.append(item)

    if not meta:
        return None

    # Sort rules by index
    suggested_rules.sort(key=lambda x: x.get("SK", ""))
    meta["suggested_rules"] = suggested_rules
    return meta


def _apply_filters(
    positions: list[PaperPosition],
    period: Optional[str] = None,
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    min_return: Optional[float] = None,
) -> list[PaperPosition]:
    """Apply common filters to a position list."""
    result = positions

    if verdict and verdict.upper() != "ALL":
        result = [
            p for p in result
            if str(getattr(p.verdict_at_entry, "value", p.verdict_at_entry)) == verdict
        ]

    if scanner and scanner.lower() != "all":
        result = [
            p for p in result
            if p.scanner_source == scanner or p.scanner_source == scanner + "_SCANNER"
        ]

    if period and period != "all":
        days_map = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
        days = days_map.get(period)
        if days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            result = [p for p in result if p.entry_date >= cutoff]

    if min_return is not None:
        result = [
            p for p in result
            if p.current_pnl_pct >= min_return
        ]

    return result
