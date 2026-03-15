"""Real trade AI trend analysis.

Two-step async pattern:
1. create_analysis_stub() — writes a 'running' stub, returns analysis_id immediately
2. run_trade_analysis() — background worker, gathers trades, calls LLM, stores results
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.db import dynamodb as db
from app.db.tables import RealTradeTable

logger = logging.getLogger(__name__)


async def create_analysis_stub() -> dict[str, Any]:
    """Create a 'running' analysis stub and return immediately.

    The caller (API endpoint) should then invoke run_trade_analysis()
    in the background.
    """
    # Gather trade counts for validation
    open_trades = await RealTradeTable.list_open(limit=500)
    closed_trades = await RealTradeTable.list_closed(limit=500)

    total = len(open_trades) + len(closed_trades)
    if total < 3:
        return {
            "status": "insufficient_data",
            "message": f"Need at least 3 trades for analysis (have {total})",
            "analysis_id": None,
        }

    analysis_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Write META item
    meta_item = {
        "PK": f"ANALYSIS#{analysis_id}",
        "SK": "META",
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "total_trades": total,
        "closed_trades": len(closed_trades),
        "open_trades": len(open_trades),
    }
    await db.put_item(RealTradeTable.TABLE, meta_item)

    # Write INDEX item for listing
    index_item = {
        "PK": "ANALYSIS_INDEX",
        "SK": f"{now}#{analysis_id}",
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "total_trades": total,
    }
    await db.put_item(RealTradeTable.TABLE, index_item)

    return {
        "analysis_id": analysis_id,
        "status": "running",
        "total_trades": total,
    }


async def run_trade_analysis(analysis_id: str) -> dict[str, Any]:
    """Run the actual AI analysis on real trades.

    Gathers all trades, calls the LLM, and stores results in DynamoDB.
    """
    try:
        open_trades = await RealTradeTable.list_open(limit=500)
        closed_trades = await RealTradeTable.list_closed(limit=500)
        all_trades = open_trades + closed_trades

        # Compute context stats
        wins = 0
        total_return = 0.0
        best_return: Optional[float] = None
        worst_return: Optional[float] = None

        for t in closed_trades:
            pnl = t.get("realized_pnl_pct")
            if pnl is not None:
                pnl_f = float(pnl)
                total_return += pnl_f
                if pnl_f > 0:
                    wins += 1
                if best_return is None or pnl_f > best_return:
                    best_return = pnl_f
                if worst_return is None or pnl_f < worst_return:
                    worst_return = pnl_f

        closed_count = len(closed_trades)
        context = {
            "total_trades": len(all_trades),
            "closed_trades": closed_count,
            "open_trades": len(open_trades),
            "win_rate": round(wins / closed_count * 100, 1) if closed_count > 0 else 0,
            "avg_return": round(total_return / closed_count, 2) if closed_count > 0 else 0,
            "best_return": round(best_return, 1) if best_return is not None else None,
            "worst_return": round(worst_return, 1) if worst_return is not None else None,
        }

        # Build prompt and call LLM
        from app.real_trades.analysis_prompt import build_trade_analysis_prompt

        prompt = build_trade_analysis_prompt(all_trades, context)

        from app.llm.provider import get_provider

        provider = get_provider("anthropic")
        llm_response = await provider.generate(prompt, max_tokens=4000)
        result = _parse_analysis_response(llm_response.content)

        # Update META with results
        now = datetime.now(timezone.utc).isoformat()
        meta_updates = {
            "status": "complete",
            "completed_at": now,
            "context": context,
            "edge_pattern_count": len(result.get("edge_patterns", [])),
            "insight_count": len(result.get("behavioral_insights", [])),
            "warning_count": len(result.get("risk_warnings", [])),
        }
        await db.update_item(
            RealTradeTable.TABLE,
            f"ANALYSIS#{analysis_id}",
            "META",
            meta_updates,
        )

        # Store result sections as separate items
        result_item = {
            "PK": f"ANALYSIS#{analysis_id}",
            "SK": "RESULT",
            "summary": result.get("summary", {}),
            "edge_patterns": result.get("edge_patterns", []),
            "behavioral_insights": result.get("behavioral_insights", []),
            "risk_warnings": result.get("risk_warnings", []),
            "next_steps": result.get("next_steps", []),
        }
        await db.put_item(RealTradeTable.TABLE, result_item)

        # Update INDEX item
        index_items = await db.query(
            RealTradeTable.TABLE, "ANALYSIS_INDEX", limit=20
        )
        for item in index_items:
            if item.get("analysis_id") == analysis_id:
                await db.update_item(
                    RealTradeTable.TABLE,
                    "ANALYSIS_INDEX",
                    item["SK"],
                    {
                        "status": "complete",
                        "edge_pattern_count": len(result.get("edge_patterns", [])),
                    },
                )
                break

        logger.info(
            f"Trade analysis {analysis_id} complete: "
            f"{len(result.get('edge_patterns', []))} patterns, "
            f"{len(result.get('behavioral_insights', []))} insights"
        )

        return {"status": "complete", "analysis_id": analysis_id, **result}

    except Exception as e:
        logger.error(f"Trade analysis {analysis_id} failed: {e}")
        await db.update_item(
            RealTradeTable.TABLE,
            f"ANALYSIS#{analysis_id}",
            "META",
            {"status": "error", "error_message": str(e)},
        )
        return {"status": "error", "analysis_id": analysis_id, "message": str(e)}


async def get_analysis(analysis_id: str) -> Optional[dict[str, Any]]:
    """Get an analysis by ID, including results if complete."""
    items = await db.query(
        RealTradeTable.TABLE, f"ANALYSIS#{analysis_id}", limit=10
    )
    if not items:
        return None

    meta = None
    result = None
    for item in items:
        sk = item.get("SK", "")
        if sk == "META":
            meta = item
        elif sk == "RESULT":
            result = item

    if not meta:
        return None

    # Strip DynamoDB keys
    for key in ("PK", "SK"):
        meta.pop(key, None)
        if result:
            result.pop(key, None)

    response = {**meta}
    if result:
        response.update(result)

    return response


async def get_latest_analysis() -> Optional[dict[str, Any]]:
    """Get the most recent analysis."""
    index_items = await db.query(
        RealTradeTable.TABLE,
        "ANALYSIS_INDEX",
        limit=1,
        scan_forward=False,
    )
    if not index_items:
        return None

    analysis_id = index_items[0].get("analysis_id")
    if not analysis_id:
        return None

    return await get_analysis(analysis_id)


async def list_analyses(limit: int = 10) -> list[dict[str, Any]]:
    """List recent analyses."""
    index_items = await db.query(
        RealTradeTable.TABLE,
        "ANALYSIS_INDEX",
        limit=limit,
        scan_forward=False,
    )

    results = []
    for item in index_items:
        for key in ("PK", "SK"):
            item.pop(key, None)
        results.append(item)

    return results


def _parse_analysis_response(content: str) -> dict[str, Any]:
    """Parse the LLM response JSON."""
    # Strip markdown code fences if present
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (fences)
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM analysis response as JSON")
        return {
            "summary": {"key_observation": content[:500]},
            "edge_patterns": [],
            "behavioral_insights": [],
            "risk_warnings": [],
            "next_steps": [],
        }
