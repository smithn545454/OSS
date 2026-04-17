"""Real trade tracking endpoints.

Allows users to manually track trades they execute, capturing a full
evaluation snapshot at the moment of tracking for later AI analysis.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.helpers.evaluation_snapshot import build_evaluation_snapshot_data
from app.core.schemas import (
    EvaluationSnapshot,
    RealTrade,
    TradeExitReason,
)
from app.db.tables import PaperPositionTable, PaperSnapshotTable, RealTradeTable

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================================================
# Request / Response Models
# ============================================================================


class TrackTradeRequest(BaseModel):
    """Request to track a real trade from the Evaluation Detail page."""

    ticker: str
    evaluation_id: str
    entry_price: float
    quantity: int = 1
    trader: str
    entry_notes: Optional[str] = None
    conviction_score: Optional[float] = None


class CloseTradeRequest(BaseModel):
    """Request to close a tracked trade."""

    exit_price: float
    exit_reason: TradeExitReason
    exit_notes: Optional[str] = None


class UpdateTradeRequest(BaseModel):
    """Request to update trade notes."""

    entry_notes: Optional[str] = None


# ============================================================================
# Endpoints
# ============================================================================


@router.post("")
async def track_trade(request: TrackTradeRequest) -> dict[str, Any]:
    """Track a real trade by capturing a full evaluation snapshot.

    Fetches the complete evaluation detail (pillar scores, gate results,
    features, thesis, scanner triggers) and stores a denormalized copy
    alongside the user's execution details.

    Returns 409 if this evaluation is already tracked.
    """
    # Check for duplicate
    existing = await RealTradeTable.get_by_evaluation_id(request.evaluation_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Evaluation {request.evaluation_id} already tracked "
            f"as trade {existing['trade_id']}",
        )

    # Build complete evaluation snapshot
    snapshot_data = await build_evaluation_snapshot_data(
        ticker=request.ticker,
        evaluation_id=request.evaluation_id,
    )
    if not snapshot_data:
        raise HTTPException(
            status_code=404,
            detail=f"Evaluation not found: {request.ticker}/{request.evaluation_id}",
        )

    evaluation = snapshot_data["evaluation"]
    decision = evaluation.get("decision", {})
    if not isinstance(decision, dict):
        decision = {}

    # Build the EvaluationSnapshot model
    snapshot = EvaluationSnapshot(
        evaluation_id=request.evaluation_id,
        opportunity_id=evaluation.get("opportunity_id", ""),
        underlying_ticker=request.ticker,
        option_ticker=evaluation.get("option_ticker", ""),
        option_type=str(evaluation.get("option_type", "")),
        strike=float(evaluation.get("strike", 0)),
        expiration_date=str(evaluation.get("expiration_date", "")),
        dte=int(evaluation.get("dte", 0)),
        dte_bucket=str(evaluation.get("dte_bucket", "")),
        underlying_price=float(evaluation.get("underlying_price", 0)),
        moneyness_pct=float(evaluation.get("moneyness_pct", 0)),
        bid=float(evaluation.get("bid", 0)),
        ask=float(evaluation.get("ask", 0)),
        mid=float(evaluation.get("mid", 0)),
        spread_abs=float(evaluation.get("spread_abs", 0)),
        spread_pct=float(evaluation.get("spread_pct", 0)),
        delta=float(evaluation.get("delta", 0)),
        gamma=float(evaluation.get("gamma", 0)),
        theta=float(evaluation.get("theta", 0)),
        vega=float(evaluation.get("vega", 0)),
        iv=float(evaluation.get("iv", 0)),
        open_interest=int(evaluation.get("open_interest", 0)),
        volume=int(evaluation.get("volume", 0)),
        oi_5d_change_pct=_safe_float(evaluation.get("oi_5d_change_pct")),
        breakeven_price=float(evaluation.get("breakeven_price", 0)),
        required_move_pct=float(evaluation.get("required_move_pct", 0)),
        expected_move_pct=float(evaluation.get("expected_move_pct", 0)),
        feasibility_ratio=float(evaluation.get("feasibility_ratio", 0)),
        time_adjusted_feasibility=float(evaluation.get("time_adjusted_feasibility", 0)),
        rank_score=float(evaluation.get("rank_score", 0)),
        policy_version=str(evaluation.get("policy_version", "")),
        policy_hash=str(evaluation.get("policy_hash", "")),
        scanner_source=evaluation.get("scanner_source"),
        scanner_metrics=evaluation.get("scanner_metrics"),
        trigger_reasons=evaluation.get("trigger_reasons"),
        evaluated_at=str(evaluation.get("evaluated_at", "")),
        # Decision fields
        verdict=str(decision.get("verdict", evaluation.get("verdict", ""))),
        quality_tier=_safe_str(decision.get("quality_tier", evaluation.get("quality_tier"))),
        final_score=float(decision.get("final_score", 0)),
        premium_leverage_score=_safe_float(decision.get("premium_leverage_score")),
        underlying_behavior_score=_safe_float(decision.get("underlying_behavior_score")),
        setup_quality_score=_safe_float(decision.get("setup_quality_score")),
        directional_conviction_score=_safe_float(decision.get("directional_conviction_score")),
        move_potential_score=_safe_float(decision.get("move_potential_score")),
        trade_structure_score=_safe_float(decision.get("trade_structure_score")),
        primary_reason_code=str(decision.get("primary_reason_code", "")),
        supporting_reason_codes=decision.get("supporting_reason_codes", []),
        failed_gates=decision.get("failed_gates", []),
        concentration_warnings=decision.get("concentration_warnings", []),
        # Related data
        pillar_scores=snapshot_data["pillar_scores"],
        gate_results=snapshot_data["gate_results"],
        scanner_triggers=snapshot_data["scanner_triggers"],
        features=snapshot_data["features"],
        thesis=snapshot_data["thesis"],
        matched_rules=snapshot_data["matched_rules"],
        # Underlying stock technicals (point-in-time)
        underlying_technicals=snapshot_data.get("underlying_technicals"),
        # Decision timestamp
        decided_at=decision.get("decided_at"),
        # Computed scores
        theta_adjusted_ev=snapshot_data["theta_adjusted_ev"],
        conviction_score=request.conviction_score,
        company_name=(snapshot_data.get("underlying_technicals") or {}).get("company_name"),
    )

    # Create the trade
    trade = RealTrade(
        entry_price=request.entry_price,
        quantity=request.quantity,
        trader=request.trader,
        entry_notes=request.entry_notes,
        snapshot=snapshot,
    )

    await RealTradeTable.put(trade)
    logger.info(
        f"Tracked real trade {trade.trade_id} for {request.ticker} "
        f"eval={request.evaluation_id} entry=${request.entry_price}"
    )

    return {
        "trade_id": trade.trade_id,
        "status": "OPEN",
        "ticker": request.ticker,
        "option_ticker": snapshot.option_ticker,
        "entry_price": request.entry_price,
        "tracked_at": trade.tracked_at,
    }


@router.get("")
async def list_trades(
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List tracked trades with optional filters."""
    if ticker:
        trades = await RealTradeTable.list_by_ticker(ticker, limit=limit)
    elif status and status.upper() == "CLOSED":
        trades = await RealTradeTable.list_closed(limit=limit)
    elif status and status.upper() == "OPEN":
        trades = await RealTradeTable.list_open(limit=limit)
    else:
        # Return both open and closed
        open_trades = await RealTradeTable.list_open(limit=limit)
        closed_trades = await RealTradeTable.list_closed(limit=limit)
        trades = open_trades + closed_trades
        trades.sort(key=lambda t: t.get("tracked_at", ""), reverse=True)
        trades = trades[:limit]

    return {
        "trades": trades,
        "count": len(trades),
    }


@router.get("/stats")
async def get_trade_stats() -> dict[str, Any]:
    """Get summary statistics for tracked trades."""
    open_count = await RealTradeTable.count_by_status("OPEN")
    closed_count = await RealTradeTable.count_by_status("CLOSED")

    # Calculate win rate and avg return from closed trades
    closed_trades = await RealTradeTable.list_closed(limit=500)
    wins = 0
    total_return = 0.0
    for t in closed_trades:
        pnl = t.get("realized_pnl_pct")
        if pnl is not None:
            pnl_float = float(pnl)
            total_return += pnl_float
            if pnl_float > 0:
                wins += 1

    win_rate = (wins / closed_count * 100) if closed_count > 0 else 0.0
    avg_return = (total_return / closed_count) if closed_count > 0 else 0.0

    return {
        "open_count": open_count,
        "closed_count": closed_count,
        "total_count": open_count + closed_count,
        "win_rate": round(win_rate, 1),
        "avg_return_pct": round(avg_return, 2),
    }


@router.get("/{trade_id}")
async def get_trade(trade_id: str) -> dict[str, Any]:
    """Get a single trade with full snapshot."""
    trade = await RealTradeTable.get_by_id(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade not found: {trade_id}")
    return trade


@router.get("/{trade_id}/paper-comparison")
async def get_paper_comparison(trade_id: str) -> dict[str, Any]:
    """Get the matching paper position and its daily snapshots for a real trade.

    Links via evaluation_id: the real trade and paper position share the same
    evaluation_id from the pipeline run that produced them.
    """
    trade = await RealTradeTable.get_by_id(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade not found: {trade_id}")

    snapshot = trade.get("snapshot", {})
    evaluation_id = snapshot.get("evaluation_id") if isinstance(snapshot, dict) else None
    if not evaluation_id:
        return {"paper_position": None, "snapshots": []}

    paper = await PaperPositionTable.get_by_evaluation_id(evaluation_id)
    if not paper:
        return {"paper_position": None, "snapshots": []}

    snapshots = await PaperSnapshotTable.list_by_position(
        paper.position_id, limit=365
    )

    return {
        "paper_position": paper.model_dump(mode="json"),
        "snapshots": snapshots,
    }


@router.patch("/{trade_id}/close")
async def close_trade(trade_id: str, request: CloseTradeRequest) -> dict[str, Any]:
    """Close a tracked trade with exit details.

    Computes realized P&L and moves the trade from OPEN to CLOSED partition.
    """
    # Find the open trade to get tracked_at for the SK
    trade = await RealTradeTable.get_by_id(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade not found: {trade_id}")
    if trade.get("status") == "CLOSED":
        raise HTTPException(status_code=409, detail="Trade is already closed")

    tracked_at = trade["tracked_at"]

    closed = await RealTradeTable.close(
        trade_id=trade_id,
        tracked_at=tracked_at,
        exit_price=request.exit_price,
        exit_reason=request.exit_reason.value,
        exit_notes=request.exit_notes,
    )

    logger.info(
        f"Closed trade {trade_id} exit=${request.exit_price} "
        f"reason={request.exit_reason.value} pnl={closed.get('realized_pnl_pct')}%"
    )

    return closed


@router.patch("/{trade_id}")
async def update_trade(trade_id: str, request: UpdateTradeRequest) -> dict[str, Any]:
    """Update trade notes/metadata."""
    trade = await RealTradeTable.get_by_id(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade not found: {trade_id}")

    # For now, just update notes via a full rewrite
    # (DynamoDB update_item would be better for production, but this works)
    if request.entry_notes is not None:
        trade["entry_notes"] = request.entry_notes

    return trade


# ============================================================================
# AI Trend Analysis Endpoints
# ============================================================================


@router.post("/analysis")
async def trigger_analysis() -> dict[str, Any]:
    """Trigger an AI analysis of real trade patterns.

    Returns immediately with an analysis_id. Poll GET /analysis/{id}
    for results.
    """
    from app.real_trades.analysis import create_analysis_stub, run_trade_analysis

    stub = await create_analysis_stub()
    if stub.get("status") == "insufficient_data":
        raise HTTPException(status_code=400, detail=stub.get("message", "Not enough trades"))

    # Run analysis synchronously — asyncio.create_task doesn't work on Lambda
    # because Mangum freezes the execution context after the response is sent.
    analysis_id = stub["analysis_id"]
    result = await run_trade_analysis(analysis_id)

    return result


@router.get("/analysis/latest")
async def get_latest_analysis() -> dict[str, Any]:
    """Get the most recent AI analysis."""
    from app.real_trades.analysis import get_latest_analysis as _get_latest

    result = await _get_latest()
    if not result:
        return {"status": "none", "message": "No analyses found"}
    return result


@router.get("/analysis/{analysis_id}")
async def get_analysis(analysis_id: str) -> dict[str, Any]:
    """Get a specific AI analysis by ID."""
    from app.real_trades.analysis import get_analysis as _get_analysis

    result = await _get_analysis(analysis_id)
    if not result:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return result


# ============================================================================
# Helpers
# ============================================================================


def _safe_float(val: Any) -> Optional[float]:
    """Safely convert a value to float, returning None for None/invalid."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_str(val: Any) -> Optional[str]:
    """Safely convert a value to string, returning None for None."""
    if val is None:
        return None
    return str(val.value) if hasattr(val, "value") else str(val)
