"""Pattern Discovery engine for paper trading.

Analyzes closed trade data to identify statistically significant
trade archetypes using an LLM. Stores results in DynamoDB for
display on the Pattern Discovery tab.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.core.schemas import PaperPosition
from app.db.tables import PaperPositionTable
from app.llm.provider import get_provider
from app.paper_trading.pattern_discovery_prompt import (
    build_discovery_prompt,
    parse_discovery_response,
)

logger = logging.getLogger(__name__)

# DynamoDB key patterns for analysis results
ANALYSIS_PK_PREFIX = "ANALYSIS#"
SETUP_RULE_PK = "SETUP_RULE"


def _position_summary(p: PaperPosition) -> dict[str, Any]:
    """Convert a position to a compact summary for LLM analysis."""
    return {
        "ticker": p.underlying_ticker or p.option_ticker,
        "scanner": p.scanner_source or "UNKNOWN",
        "scanner_list": p.scanner_list or ([p.scanner_source] if p.scanner_source else []),
        "conviction_score": p.conviction_score,
        "pillar_directional": p.pillar_directional,
        "pillar_volatility": p.pillar_volatility,
        "pillar_structure": p.pillar_structure,
        "option_type": p.option_type,
        "dte_at_entry": p.dte_at_entry,
        "dte_bucket": p.dte_bucket,
        "entry_iv": p.entry_iv,
        "entry_iv_percentile": p.entry_iv_percentile,
        "entry_iv_rv_ratio": (
            round(p.entry_iv_rv_ratio, 3) if p.entry_iv_rv_ratio is not None else None
        ),
        "entry_theta_adjusted_edge": (
            round(p.entry_theta_adjusted_edge, 2)
            if p.entry_theta_adjusted_edge is not None else None
        ),
        "entry_delta": p.entry_delta,
        "gate_margin": p.gate_margin,
        "theta_adj_ev": p.theta_adj_ev,
        "strike": p.strike,
        "underlying_price": p.entry_underlying_price,
        "moneyness_pct": p.entry_moneyness_pct,
        "spread_pct": p.entry_spread_pct,
        "open_interest": p.entry_open_interest,
        "volume": p.entry_volume,
        "days_to_earnings": p.entry_days_to_earnings,
        "atr14_pct": (
            round(p.entry_atr14_pct, 2)
            if p.entry_atr14_pct is not None else None
        ),
        "rs_20d": (
            round(p.entry_rs_20d, 2)
            if p.entry_rs_20d is not None else None
        ),
        "feasibility_ratio": (
            round(p.entry_feasibility_ratio, 2)
            if p.entry_feasibility_ratio is not None else None
        ),
        "return_pct": round(p.current_pnl_pct, 2),
        "days_held": p.days_held,
        "mfe": round(p.max_favorable_excursion, 2),
        "mae": round(p.max_adverse_excursion, 2),
        "verdict": str(getattr(p.verdict_at_entry, "value", p.verdict_at_entry)),
        "convergence_count": p.convergence_count or 1,
    }


async def create_analysis_stub(
    period: Optional[str] = None,
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    min_sample: int = 5,
    min_win_rate: float = 0.55,
) -> dict[str, Any]:
    """Create a 'running' analysis stub and return immediately.

    Checks that enough closed trades exist, then writes a stub record
    to DynamoDB so the frontend can poll for results.

    Returns:
        Dict with analysis_id and status ("running" or "insufficient_data")
    """
    # Quick check: gather positions and count closed trades
    open_pos = await PaperPositionTable.list_open()
    closed_pos = await PaperPositionTable.list_closed()
    all_positions = open_pos + closed_pos

    if verdict and verdict.upper() != "ALL":
        all_positions = [
            p for p in all_positions
            if str(getattr(p.verdict_at_entry, "value", p.verdict_at_entry)) == verdict
        ]
    if scanner and scanner.lower() != "all":
        all_positions = [
            p for p in all_positions
            if p.scanner_source == scanner or p.scanner_source == scanner + "_SCANNER"
        ]
    if period and period != "all":
        from datetime import timedelta
        days_map = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
        days = days_map.get(period)
        if days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            all_positions = [p for p in all_positions if p.entry_date >= cutoff]

    closed = [
        p for p in all_positions
        if str(getattr(p.status, "value", p.status)) == "CLOSED"
    ]

    if len(closed) < min_sample:
        return {
            "analysis_id": None,
            "status": "insufficient_data",
            "message": (
                f"Not enough closed trades for reliable pattern analysis. "
                f"Found {len(closed)} closed trades, need at least {min_sample}. "
                f"Continue accumulating data."
            ),
            "positions_analyzed": len(closed),
            "archetypes": [],
        }

    # Create a "running" stub in DynamoDB
    analysis_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    meta_item = {
        "PK": f"{ANALYSIS_PK_PREFIX}{analysis_id}",
        "SK": "META",
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "positions_analyzed": len(closed),
        "period": period or "all",
        "verdict_filter": verdict,
        "scanner_filter": scanner,
        "archetype_count": 0,
    }
    await db.put_item(PaperPositionTable.TABLE, meta_item)

    index_item = {
        "PK": "ANALYSIS_INDEX",
        "SK": f"{now}#{analysis_id}",
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "positions_analyzed": len(closed),
        "archetype_count": 0,
        "period": period or "all",
    }
    await db.put_item(PaperPositionTable.TABLE, index_item)

    return {
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "positions_analyzed": len(closed),
        "archetypes": [],
    }


async def run_pattern_analysis(
    analysis_id: str,
    period: Optional[str] = None,
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    min_sample: int = 5,
    min_win_rate: float = 0.55,
) -> dict[str, Any]:
    """Run pattern discovery analysis (worker — no timeout pressure).

    Called asynchronously by the Lambda worker handler. Queries positions,
    runs the LLM call, and updates the analysis stub in DynamoDB.

    Args:
        analysis_id: Pre-created analysis ID to update
        period: Filter by entry_date (7d, 14d, 30d, 90d, all)
        verdict: Filter by verdict (APPROVE, WATCH)
        scanner: Filter by scanner_source
        min_sample: Minimum trades per archetype (default 5)
        min_win_rate: Minimum win rate for archetype (default 55%)

    Returns:
        Analysis results with archetypes
    """
    from app.db.dynamodb import get_dynamodb

    now = datetime.now(timezone.utc).isoformat()
    db = get_dynamodb()

    try:
        # Gather all positions, applying filters
        open_pos = await PaperPositionTable.list_open()
        closed_pos = await PaperPositionTable.list_closed()
        all_positions = open_pos + closed_pos

        if verdict and verdict.upper() != "ALL":
            all_positions = [
                p for p in all_positions
                if str(getattr(p.verdict_at_entry, "value", p.verdict_at_entry)) == verdict
            ]
        if scanner and scanner.lower() != "all":
            all_positions = [
                p for p in all_positions
                if p.scanner_source == scanner or p.scanner_source == scanner + "_SCANNER"
            ]
        if period and period != "all":
            from datetime import timedelta
            days_map = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
            days = days_map.get(period)
            if days:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
                all_positions = [p for p in all_positions if p.entry_date >= cutoff]

        closed = [
            p for p in all_positions
            if str(getattr(p.status, "value", p.status)) == "CLOSED"
        ]

        total_closed = len(closed)
        wins = sum(1 for p in closed if p.current_pnl_pct > 0)
        avg_return = sum(p.current_pnl_pct for p in closed) / total_closed if total_closed else 0

        context = {
            "total_trades": total_closed,
            "win_rate": round(wins / total_closed * 100, 1) if total_closed else 0,
            "avg_return": round(avg_return, 2),
            "min_sample_size": min_sample,
            "min_win_rate_pct": round(min_win_rate * 100, 1),
        }

        # Sample most recent trades for LLM prompt
        closed_sorted = sorted(closed, key=lambda p: p.entry_date, reverse=True)[:1000]
        trade_data = [_position_summary(p) for p in closed_sorted]

        # Build and send prompt
        prompt = build_discovery_prompt(trade_data, context)

        provider = get_provider("anthropic")
        llm_response = await provider.generate(prompt, max_tokens=4000)

        if not llm_response.success:
            raise RuntimeError(f"LLM returned error: {llm_response.error}")

        # Parse response
        archetypes = parse_discovery_response(llm_response.content)

        # Update stub with results
        meta_updates = {
            "status": "complete",
            "positions_analyzed": total_closed,
            "archetype_count": len(archetypes),
            "context": context,
        }
        await db.update_item(
            PaperPositionTable.TABLE,
            f"{ANALYSIS_PK_PREFIX}{analysis_id}",
            "META",
            meta_updates,
        )

        # Update index item status
        # Query to find the index SK for this analysis
        index_items = await db.query(PaperPositionTable.TABLE, "ANALYSIS_INDEX")
        for item in index_items:
            if item.get("analysis_id") == analysis_id:
                await db.update_item(
                    PaperPositionTable.TABLE,
                    "ANALYSIS_INDEX",
                    item["SK"],
                    {"status": "complete", "archetype_count": len(archetypes)},
                )
                break

        # Store each archetype
        for i, archetype in enumerate(archetypes):
            arch_item = {
                "PK": f"{ANALYSIS_PK_PREFIX}{analysis_id}",
                "SK": f"ARCHETYPE#{i:03d}",
                **archetype,
            }
            await db.put_item(PaperPositionTable.TABLE, arch_item)

        logger.info(
            f"Pattern analysis {analysis_id} complete: "
            f"{len(archetypes)} archetypes from {total_closed} trades"
        )

        return {
            "analysis_id": analysis_id,
            "status": "complete",
            "created_at": now,
            "positions_analyzed": total_closed,
            "context": context,
            "archetypes": archetypes,
        }

    except Exception as e:
        logger.error(f"Pattern analysis {analysis_id} failed: {e}")
        # Update stub to error status
        try:
            await db.update_item(
                PaperPositionTable.TABLE,
                f"{ANALYSIS_PK_PREFIX}{analysis_id}",
                "META",
                {"status": "error", "error_message": str(e)},
            )
        except Exception:
            logger.error(f"Failed to update analysis {analysis_id} error status")

        return {
            "analysis_id": analysis_id,
            "status": "error",
            "message": f"AI analysis failed: {str(e)}",
            "positions_analyzed": 0,
            "archetypes": [],
        }


async def list_analyses(limit: int = 10) -> list[dict[str, Any]]:
    """List recent pattern analysis runs.

    Uses the ANALYSIS_INDEX partition for efficient querying.

    Returns:
        List of analysis metadata dicts, most recent first
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    items = await db.query(
        PaperPositionTable.TABLE,
        "ANALYSIS_INDEX",
        limit=limit,
        scan_forward=False,  # Most recent first (SK is timestamp-based)
    )

    analyses = []
    for item in items:
        item.pop("PK", None)
        item.pop("SK", None)
        analyses.append(item)

    return analyses


async def get_analysis(analysis_id: str) -> Optional[dict[str, Any]]:
    """Get a specific analysis with its archetypes.

    Args:
        analysis_id: The analysis UUID

    Returns:
        Analysis with metadata and archetypes, or None if not found
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    items = await db.query(
        PaperPositionTable.TABLE,
        f"{ANALYSIS_PK_PREFIX}{analysis_id}",
    )

    if not items:
        return None

    meta = None
    archetypes = []
    for item in items:
        sk = item.pop("SK", "")
        item.pop("PK", None)
        if sk == "META":
            meta = item
        elif sk.startswith("ARCHETYPE#"):
            archetypes.append(item)

    if not meta:
        return None

    # Sort archetypes by index
    archetypes.sort(key=lambda x: x.get("SK", ""))
    meta["archetypes"] = archetypes
    return meta


# ============================================================================
# Setup Rules
# ============================================================================


async def list_setup_rules() -> list[dict[str, Any]]:
    """List all setup rules."""
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    items = await db.query(
        PaperPositionTable.TABLE,
        SETUP_RULE_PK,
    )

    rules = []
    for item in items:
        item.pop("PK", None)
        item.pop("SK", None)
        rules.append(item)

    return rules


async def create_setup_rule(rule_data: dict[str, Any]) -> dict[str, Any]:
    """Create a setup rule from an archetype.

    Args:
        rule_data: Dict with name, criteria, source_analysis_id, performance_at_creation

    Returns:
        The created rule with generated rule_id
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    rule_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    item = {
        "PK": SETUP_RULE_PK,
        "SK": f"RULE#{rule_id}",
        "rule_id": rule_id,
        "name": rule_data.get("name", "Unnamed Rule"),
        "criteria": rule_data.get("criteria", {}),
        "is_active": True,
        "mode": rule_data.get("mode", "production"),
        "created_at": now,
        "source": rule_data.get("source", "ai"),
        "source_analysis_id": rule_data.get("source_analysis_id"),
        "performance_at_creation": rule_data.get("performance_at_creation"),
    }

    await db.put_item(PaperPositionTable.TABLE, item)
    item.pop("PK", None)
    item.pop("SK", None)
    return item


async def update_setup_rule(rule_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Update a setup rule (e.g., toggle is_active).

    Args:
        rule_id: The rule UUID
        updates: Fields to update

    Returns:
        Updated rule or None if not found
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    item = await db.update_item(
        PaperPositionTable.TABLE,
        SETUP_RULE_PK,
        f"RULE#{rule_id}",
        updates,
    )

    if item:
        item.pop("PK", None)
        item.pop("SK", None)
    return item


async def delete_setup_rule(rule_id: str) -> bool:
    """Delete a setup rule.

    Args:
        rule_id: The rule UUID

    Returns:
        True if deleted successfully
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    try:
        await db.delete_item(
            PaperPositionTable.TABLE,
            SETUP_RULE_PK,
            f"RULE#{rule_id}",
        )
        return True
    except Exception as e:
        logger.error(f"Failed to delete setup rule {rule_id}: {e}")
        return False
