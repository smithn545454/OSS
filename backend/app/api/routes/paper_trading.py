"""Paper trading API endpoints.

Per Section 17 of OSS_Complete_Requirements.md.

Provides endpoints for:
- Listing positions (open/closed/all)
- Getting single position
- Manual position close
- Performance metrics
- Triggering daily updates
- Shadow tracking results
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.tables import PaperPositionTable
from app.paper_trading.metrics import (
    analyze_exit_effectiveness,
    calculate_performance_metrics,
    compare_tiers,
)
from app.paper_trading.position_manager import close_position_manually

router = APIRouter()


class ManualCloseRequest(BaseModel):
    """Request body for manual position close."""
    
    exit_price: Optional[float] = None


class UpdateResponse(BaseModel):
    """Response for position update."""
    
    positions_updated: int
    exits_triggered: int
    errors: int


# ============================================================================
# Position Endpoints
# ============================================================================


@router.get("/positions")
async def list_positions(
    status: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List paper trading positions.
    
    Args:
        status: Filter by status (open, closed, or all)
        limit: Maximum positions to return
        
    Returns:
        List of positions with metadata
    """
    if status and status.lower() == "open":
        positions = await PaperPositionTable.list_open(limit=limit)
    elif status and status.lower() == "closed":
        positions = await PaperPositionTable.list_closed(limit=limit)
    else:
        positions = await PaperPositionTable.list_all(limit=limit)
    
    # Convert to dict for JSON serialization
    position_dicts = []
    for pos in positions:
        pos_dict = {
            "position_id": pos.position_id,
            "evaluation_id": pos.evaluation_id,
            "option_ticker": pos.option_ticker,
            "entry_price": pos.entry_price,
            "entry_date": pos.entry_date,
            "quantity": pos.quantity,
            "verdict_at_entry": str(pos.verdict_at_entry.value) if hasattr(pos.verdict_at_entry, 'value') else str(pos.verdict_at_entry),
            "quality_tier_at_entry": str(pos.quality_tier_at_entry.value) if pos.quality_tier_at_entry and hasattr(pos.quality_tier_at_entry, 'value') else str(pos.quality_tier_at_entry) if pos.quality_tier_at_entry else None,
            "exit_price": pos.exit_price,
            "exit_date": pos.exit_date,
            "exit_reason": str(pos.exit_reason.value) if pos.exit_reason and hasattr(pos.exit_reason, 'value') else str(pos.exit_reason) if pos.exit_reason else None,
            "current_price": pos.current_price,
            "current_pnl_pct": round(pos.current_pnl_pct, 2),
            "max_favorable_excursion": round(pos.max_favorable_excursion, 2),
            "max_adverse_excursion": round(pos.max_adverse_excursion, 2),
            "days_held": pos.days_held,
            "status": str(pos.status.value) if hasattr(pos.status, 'value') else str(pos.status),
            "last_updated": pos.last_updated,
        }
        position_dicts.append(pos_dict)
    
    return {
        "positions": position_dicts,
        "count": len(position_dicts),
        "filter": {"status": status} if status else None,
    }


@router.get("/positions/{position_id}")
async def get_position(position_id: str) -> dict[str, Any]:
    """Get a specific position by ID.
    
    Args:
        position_id: The position ID
        
    Returns:
        Position details
    """
    # Search in both open and closed
    all_positions = await PaperPositionTable.list_all(limit=500)
    
    for pos in all_positions:
        if pos.position_id == position_id:
            return {
                "position_id": pos.position_id,
                "evaluation_id": pos.evaluation_id,
                "option_ticker": pos.option_ticker,
                "entry_price": pos.entry_price,
                "entry_date": pos.entry_date,
                "quantity": pos.quantity,
                "verdict_at_entry": str(pos.verdict_at_entry.value) if hasattr(pos.verdict_at_entry, 'value') else str(pos.verdict_at_entry),
                "quality_tier_at_entry": str(pos.quality_tier_at_entry.value) if pos.quality_tier_at_entry and hasattr(pos.quality_tier_at_entry, 'value') else str(pos.quality_tier_at_entry) if pos.quality_tier_at_entry else None,
                "exit_price": pos.exit_price,
                "exit_date": pos.exit_date,
                "exit_reason": str(pos.exit_reason.value) if pos.exit_reason and hasattr(pos.exit_reason, 'value') else str(pos.exit_reason) if pos.exit_reason else None,
                "current_price": pos.current_price,
                "current_pnl_pct": round(pos.current_pnl_pct, 2),
                "max_favorable_excursion": round(pos.max_favorable_excursion, 2),
                "max_adverse_excursion": round(pos.max_adverse_excursion, 2),
                "days_held": pos.days_held,
                "status": str(pos.status.value) if hasattr(pos.status, 'value') else str(pos.status),
                "last_updated": pos.last_updated,
            }
    
    raise HTTPException(
        status_code=404,
        detail=f"Position not found: {position_id}",
    )


@router.post("/positions/{position_id}/close")
async def close_position(
    position_id: str,
    request: ManualCloseRequest,
) -> dict[str, Any]:
    """Manually close a position.
    
    Args:
        position_id: The position ID to close
        request: Close request with optional exit price
        
    Returns:
        The closed position details
    """
    closed = await close_position_manually(
        position_id=position_id,
        exit_price=request.exit_price,
    )
    
    if not closed:
        raise HTTPException(
            status_code=404,
            detail=f"Position not found or already closed: {position_id}",
        )
    
    return {
        "message": "Position closed successfully",
        "position": {
            "position_id": closed.position_id,
            "option_ticker": closed.option_ticker,
            "exit_price": closed.exit_price,
            "exit_reason": str(closed.exit_reason.value) if closed.exit_reason and hasattr(closed.exit_reason, 'value') else str(closed.exit_reason),
            "final_pnl_pct": round(closed.current_pnl_pct, 2),
            "status": str(closed.status.value) if hasattr(closed.status, 'value') else str(closed.status),
        },
    }


# ============================================================================
# Metrics Endpoints
# ============================================================================


@router.get("/metrics")
async def get_metrics() -> dict[str, Any]:
    """Get overall performance metrics.
    
    Returns:
        Performance metrics including win rate, expectancy, MFE/MAE, etc.
    """
    metrics = await calculate_performance_metrics()
    return {
        "metrics": metrics.to_dict(),
        "targets": {
            "approve_win_rate": "> 55%",
            "approve_avg_return": "> 25%",
            "reject_false_negative_rate": "< 10%",
        },
    }


@router.get("/metrics/tiers")
async def get_tier_comparison() -> dict[str, Any]:
    """Get performance comparison by quality tier.
    
    Returns:
        Performance breakdown by TIER_1, TIER_2, TIER_3
    """
    positions = await PaperPositionTable.list_all(limit=1000)
    comparison = compare_tiers(positions)
    
    return {
        "tier_comparison": comparison,
        "expectation": "TIER_1 > TIER_2 > TIER_3 in win rate and avg return",
    }


@router.get("/metrics/exits")
async def get_exit_analysis() -> dict[str, Any]:
    """Get analysis of exit effectiveness.
    
    Returns:
        Analysis by exit type (profit target, stop loss, etc.)
    """
    positions = await PaperPositionTable.list_all(limit=1000)
    analysis = analyze_exit_effectiveness(positions)
    
    return {
        "exit_analysis": analysis,
        "insights": _generate_exit_insights(analysis),
    }


def _generate_exit_insights(analysis: dict) -> list[str]:
    """Generate insights from exit analysis."""
    insights = []
    
    if "PROFIT_TARGET" in analysis:
        pt = analysis["PROFIT_TARGET"]
        if pt.get("mfe_left_on_table", 0) > 20:
            insights.append(
                f"Profit target may be too low - avg MFE {pt['avg_mfe']:.1f}% "
                f"vs avg return {pt['avg_return']:.1f}%"
            )
    
    if "STOP_LOSS" in analysis:
        sl = analysis["STOP_LOSS"]
        if sl.get("avg_mfe", 0) > 15:
            insights.append(
                f"Some stop loss exits had significant MFE ({sl['avg_mfe']:.1f}%) - "
                "consider wider stops or trailing stops"
            )
    
    if "TIME_EXIT" in analysis:
        te = analysis["TIME_EXIT"]
        if te.get("avg_return", 0) < -20:
            insights.append(
                f"Time exits averaging {te['avg_return']:.1f}% - "
                "consider earlier exits for losing positions"
            )
    
    if not insights:
        insights.append("Exit strategy appears well-balanced")
    
    return insights


# ============================================================================
# Update Endpoint
# ============================================================================


@router.post("/update")
async def trigger_update() -> dict[str, Any]:
    """Trigger daily position update job.
    
    This fetches current prices for all open positions and:
    - Updates P&L
    - Updates MFE/MAE
    - Checks exit conditions
    - Closes positions if exit triggered
    
    Returns:
        Summary of updates performed
    """
    from app.services.polygon import PolygonClient
    from app.paper_trading.position_manager import update_open_positions
    
    try:
        async with PolygonClient() as polygon:
            results = await update_open_positions(polygon)
        
        exits = [r for r in results if r.exit_triggered]
        errors = [r for r in results if r.error]
        
        return {
            "success": True,
            "positions_updated": len(results),
            "exits_triggered": len(exits),
            "exit_details": [
                {
                    "position_id": r.position_id,
                    "option_ticker": r.option_ticker,
                    "exit_reason": str(r.exit_reason.value) if r.exit_reason else None,
                    "final_pnl_pct": round(r.current_pnl_pct, 2),
                }
                for r in exits
            ],
            "errors": len(errors),
            "error_details": [
                {"position_id": r.position_id, "error": r.error}
                for r in errors
            ] if errors else None,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating positions: {str(e)}",
        )


# ============================================================================
# Summary Endpoint
# ============================================================================


@router.get("/ai-insights")
async def get_ai_insights() -> dict[str, Any]:
    """Generate AI-powered insights for system optimization.

    Analyzes paper trading performance data and generates
    actionable recommendations using an LLM.

    Returns:
        AI-generated insights with data summary
    """
    from app.paper_trading.insights import generate_ai_insights

    try:
        return await generate_ai_insights()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Insights generation failed: {e}",
        )


@router.get("/summary")
async def get_summary() -> dict[str, Any]:
    """Get a summary of paper trading status.
    
    Returns:
        Summary with position counts, key metrics, and recent activity
    """
    open_positions = await PaperPositionTable.list_open(limit=100)
    closed_positions = await PaperPositionTable.list_closed(limit=100)
    
    metrics = await calculate_performance_metrics()
    
    # Calculate open position stats
    total_open_pnl = sum(p.current_pnl_pct for p in open_positions)
    avg_open_pnl = total_open_pnl / len(open_positions) if open_positions else 0
    
    # Get recent closes (last 5)
    recent_closes = sorted(
        closed_positions,
        key=lambda p: p.exit_date or "",
        reverse=True
    )[:5]
    
    return {
        "positions": {
            "open": len(open_positions),
            "closed": len(closed_positions),
            "total": len(open_positions) + len(closed_positions),
        },
        "open_positions_summary": {
            "total_pnl_pct": round(total_open_pnl, 2),
            "avg_pnl_pct": round(avg_open_pnl, 2),
            "positions_in_profit": sum(1 for p in open_positions if p.current_pnl_pct > 0),
            "positions_in_loss": sum(1 for p in open_positions if p.current_pnl_pct < 0),
        },
        "performance": {
            "win_rate": round(metrics.win_rate, 2),
            "avg_win_pct": round(metrics.avg_win_pct, 2),
            "avg_loss_pct": round(metrics.avg_loss_pct, 2),
            "expectancy": metrics.expectancy,
        },
        "recent_closes": [
            {
                "option_ticker": p.option_ticker,
                "exit_date": p.exit_date,
                "exit_reason": str(p.exit_reason.value) if p.exit_reason and hasattr(p.exit_reason, 'value') else str(p.exit_reason),
                "pnl_pct": round(p.current_pnl_pct, 2),
            }
            for p in recent_closes
        ],
    }
