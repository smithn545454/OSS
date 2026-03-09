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


def _enum_val(v: Any) -> Any:
    """Extract .value from an enum, or return the value as-is."""
    return v.value if hasattr(v, "value") else v


def _score_band_label(score: float) -> str:
    """Map a conviction score to a display band label matching frontend SCORE_BANDS."""
    if score < 65:
        return "60-64"
    elif score < 70:
        return "65-69"
    elif score < 75:
        return "70-74"
    elif score < 80:
        return "75-79"
    elif score < 85:
        return "80-84"
    elif score < 90:
        return "85-89"
    else:
        return "90+"


def _normalize_scanner(scanner_source: Optional[str]) -> Optional[str]:
    """Normalize scanner_source values (strip _SCANNER suffix from UV Lambda)."""
    if scanner_source and scanner_source.endswith("_SCANNER"):
        return scanner_source[: -len("_SCANNER")]
    return scanner_source


def _position_to_dict(pos: Any) -> dict[str, Any]:
    """Convert a PaperPosition to a JSON-safe dict including enrichment fields."""
    return {
        "position_id": pos.position_id,
        "evaluation_id": pos.evaluation_id,
        "option_ticker": pos.option_ticker,
        "entry_price": pos.entry_price,
        "entry_date": pos.entry_date,
        "quantity": pos.quantity,
        "verdict_at_entry": str(_enum_val(pos.verdict_at_entry)),
        "quality_tier_at_entry": str(_enum_val(pos.quality_tier_at_entry)) if pos.quality_tier_at_entry else None,
        "exit_price": pos.exit_price,
        "exit_date": pos.exit_date,
        "exit_reason": str(_enum_val(pos.exit_reason)) if pos.exit_reason else None,
        "current_price": pos.current_price,
        "current_pnl_pct": round(pos.current_pnl_pct, 2),
        "max_favorable_excursion": round(pos.max_favorable_excursion, 2),
        "max_adverse_excursion": round(pos.max_adverse_excursion, 2),
        "days_held": pos.days_held,
        "status": str(_enum_val(pos.status)),
        "last_updated": pos.last_updated,
        # Enrichment fields (may be None for legacy positions)
        "underlying_ticker": pos.underlying_ticker,
        "scanner_source": _normalize_scanner(pos.scanner_source),
        "convergence_count": pos.convergence_count,
        "conviction_score": pos.conviction_score,
        "pillar_directional": pos.pillar_directional,
        "pillar_volatility": pos.pillar_volatility,
        "pillar_structure": pos.pillar_structure,
        "strike": pos.strike,
        "option_type": pos.option_type,
        "expiration_date": pos.expiration_date,
        "dte_at_entry": pos.dte_at_entry,
        "dte_bucket": pos.dte_bucket,
        "entry_delta": pos.entry_delta,
        "entry_iv": pos.entry_iv,
        "entry_theta": pos.entry_theta,
        "gate_margin": pos.gate_margin,
        "theta_adj_ev": pos.theta_adj_ev,
    }


# ============================================================================
# Position Endpoints
# ============================================================================


@router.get("/positions")
async def list_positions(
    status: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    period: Optional[str] = None,
) -> dict[str, Any]:
    """List paper trading positions with server-side filtering and pagination.

    Args:
        status: Filter by status (open, closed, or all). Default: open.
        limit: Page size (default 50, max 200)
        cursor: Base64-encoded pagination cursor from previous response
        verdict: Filter by verdict_at_entry (APPROVE, WATCH)
        scanner: Filter by scanner_source
        period: Filter by entry_date (7d, 14d, 30d, 90d)
    """
    limit = min(limit, 200)

    # Build optional DynamoDB FilterExpression
    filter_parts: list[str] = []
    filter_values: dict[str, Any] = {}
    filter_names: dict[str, str] = {}

    if verdict:
        filter_parts.append("#verdict = :verdict")
        filter_names["#verdict"] = "verdict_at_entry"
        filter_values[":verdict"] = verdict

    if scanner:
        # Match both normalized (UNUSUAL_VOLUME) and raw (UNUSUAL_VOLUME_SCANNER) values
        filter_parts.append("(#scanner = :scanner OR #scanner = :scanner_alt)")
        filter_names["#scanner"] = "scanner_source"
        filter_values[":scanner"] = scanner
        filter_values[":scanner_alt"] = scanner + "_SCANNER"

    filter_expr = " AND ".join(filter_parts) if filter_parts else None

    # Build SK condition for period filtering (SK = entry_date#position_id)
    sk_condition = None
    if period and period != "all":
        from datetime import datetime as dt, timedelta, timezone as tz
        days_map = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
        days = days_map.get(period)
        if days:
            cutoff = (dt.now(tz.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            sk_condition = {"gte": cutoff}

    resolved_status = (status or "open").lower()

    if resolved_status == "open":
        positions, next_cursor = await PaperPositionTable.list_open_paginated(
            limit=limit, cursor=cursor,
            filter_expression=filter_expr,
            filter_values=filter_values or None,
            filter_names=filter_names or None,
            sk_condition=sk_condition,
        )
    elif resolved_status == "closed":
        positions, next_cursor = await PaperPositionTable.list_closed_paginated(
            limit=limit, cursor=cursor,
            filter_expression=filter_expr,
            filter_values=filter_values or None,
            filter_names=filter_names or None,
            sk_condition=sk_condition,
        )
    else:
        # "all" — query both partitions sequentially
        open_pos, open_cursor = await PaperPositionTable.list_open_paginated(
            limit=limit, cursor=cursor,
            filter_expression=filter_expr,
            filter_values=filter_values or None,
            filter_names=filter_names or None,
            sk_condition=sk_condition,
        )
        remaining = limit - len(open_pos)
        closed_pos: list = []
        closed_cursor = None
        if remaining > 0:
            closed_pos, closed_cursor = await PaperPositionTable.list_closed_paginated(
                limit=remaining,
                filter_expression=filter_expr,
                filter_values=filter_values or None,
                filter_names=filter_names or None,
                sk_condition=sk_condition,
            )
        positions = open_pos + closed_pos
        next_cursor = open_cursor or closed_cursor

    position_dicts = [_position_to_dict(pos) for pos in positions]

    return {
        "positions": position_dicts,
        "count": len(position_dicts),
        "next_cursor": next_cursor,
        "filter": {
            "status": resolved_status,
            "verdict": verdict,
            "scanner": scanner,
            "period": period,
        },
    }


@router.get("/positions/{position_id}")
async def get_position(position_id: str) -> dict[str, Any]:
    """Get a specific position by ID.

    Searches OPEN partition first, then CLOSED, avoiding a full table scan.
    """
    for fetch_fn in [PaperPositionTable.list_open, PaperPositionTable.list_closed]:
        positions = await fetch_fn()
        for pos in positions:
            if pos.position_id == position_id:
                return _position_to_dict(pos)

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
# Summary Metrics (Pre-Aggregated — Instant Response)
# ============================================================================


@router.get("/summary-metrics")
async def get_summary_metrics(
    status: Optional[str] = None,
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    period: Optional[str] = None,
) -> dict[str, Any]:
    """Get summary metrics for the KPI strip and dashboards.

    When no filters are active, returns pre-aggregated atomic counters (<50ms).
    When any filter is active, computes metrics from matching positions.

    Args:
        status: Filter by status (open, closed). Omit for all.
        verdict: Filter by verdict_at_entry (APPROVE, WATCH)
        scanner: Filter by scanner_source
        period: Filter by entry_date (7d, 14d, 30d, 90d)
    """
    from app.paper_trading.metrics_aggregator import MetricsAggregator

    has_filters = any([verdict, scanner, period and period != "all",
                       status and status not in ("all", None)])

    if not has_filters:
        # Fast path: pre-aggregated counters
        summary = await MetricsAggregator.get_summary()
        scanners_data = await MetricsAggregator.get_scanner_metrics()
        verdicts_data = await MetricsAggregator.get_verdict_metrics()
        tiers = await MetricsAggregator.get_tier_metrics()
        equity_curve = await MetricsAggregator.get_daily_equity(days=90)

        closed = summary.get("closed_count", 0)
        wins = summary.get("win_count", 0)
        losses = summary.get("loss_count", 0)
        # total_pnl stores sum of current_pnl_pct (percentages) — used for avg_return
        sum_pnl_pct = summary.get("total_pnl", 0)
        # total_pnl_dollars stores actual dollar P&L — used for Paper P&L display
        total_pnl_dollars = summary.get("total_pnl_dollars", 0)

        win_rate = (wins / closed * 100) if closed > 0 else 0
        avg_return = (sum_pnl_pct / closed) if closed > 0 else 0

        # Avg score from pre-aggregated counters
        score_sum = float(summary.get("score_sum", 0))
        score_count = int(summary.get("score_count", 0))
        avg_score = round(score_sum / score_count, 1) if score_count > 0 else None

        # Best trade P&L
        best_trade_pnl_raw = summary.get("best_trade_pnl")
        best_trade_pnl = round(float(best_trade_pnl_raw), 1) if best_trade_pnl_raw is not None else None

        return {
            "global": {
                "open_count": summary.get("open_count", 0),
                "closed_count": closed,
                "total_count": summary.get("total_count", 0),
                "win_count": wins,
                "loss_count": losses,
                "total_pnl": round(float(total_pnl_dollars), 2),
                "win_rate": round(win_rate, 2),
                "avg_return": round(float(avg_return), 2),
                "avg_score": avg_score,
                "best_trade_pnl": best_trade_pnl,
                "last_updated": summary.get("last_updated"),
            },
            "by_scanner": {
                _normalize_scanner(s.get("scanner_type", "?")) or "?": s
                for s in scanners_data
            } if scanners_data else {},
            "by_verdict": {
                v.get("verdict", "?"): v for v in verdicts_data
            } if verdicts_data else {},
            "by_tier": {
                t.get("tier", "?"): t for t in tiers
            } if tiers else {},
            "equity_curve": equity_curve,
            "by_score_band": {},
        }

    # Filtered path: compute from matching positions
    positions = await _query_filtered_positions(status, verdict, scanner, period)

    open_positions = [p for p in positions if _enum_val(p.status) == "OPEN"]
    closed_positions = [p for p in positions if _enum_val(p.status) == "CLOSED"]

    wins = sum(1 for p in closed_positions if p.current_pnl_pct > 0)
    losses = sum(1 for p in closed_positions if p.current_pnl_pct < 0)
    closed_count = len(closed_positions)

    # Dollar P&L across all positions (for Paper P&L display)
    total_pnl_dollars = sum(
        ((p.exit_price if p.exit_price is not None else p.current_price) - p.entry_price)
        * p.quantity * 100
        for p in positions
    )

    # Average percentage return (closed positions only)
    avg_return_pct = (
        sum(p.current_pnl_pct for p in closed_positions) / closed_count
        if closed_count > 0 else 0
    )

    # Best trade percentage
    best_trade_pnl = max(
        (p.current_pnl_pct for p in positions), default=None
    )

    win_rate = (wins / closed_count * 100) if closed_count > 0 else 0

    # Score band analysis: group positions by conviction score band
    by_score_band: dict[str, dict[str, int]] = {}
    for p in positions:
        score = p.conviction_score
        if score is None:
            continue
        band = _score_band_label(float(score))
        entry = by_score_band.setdefault(band, {"count": 0, "profitable": 0})
        entry["count"] += 1
        if p.current_pnl_pct > 0:
            entry["profitable"] += 1

    return {
        "global": {
            "open_count": len(open_positions),
            "closed_count": closed_count,
            "total_count": len(positions),
            "win_count": wins,
            "loss_count": losses,
            "total_pnl": round(float(total_pnl_dollars), 2),
            "win_rate": round(win_rate, 2),
            "avg_return": round(float(avg_return_pct), 2),
            "best_trade_pnl": round(float(best_trade_pnl), 1) if best_trade_pnl is not None else None,
            "last_updated": None,
        },
        "by_scanner": {},
        "by_verdict": {},
        "by_tier": {},
        "equity_curve": [],
        "by_score_band": by_score_band,
    }


async def _query_filtered_positions(
    status: Optional[str],
    verdict: Optional[str],
    scanner: Optional[str],
    period: Optional[str],
) -> list:
    """Query all positions matching filters (no pagination limit)."""
    filter_parts: list[str] = []
    filter_values: dict[str, Any] = {}
    filter_names: dict[str, str] = {}

    if verdict:
        filter_parts.append("#verdict = :verdict")
        filter_names["#verdict"] = "verdict_at_entry"
        filter_values[":verdict"] = verdict

    if scanner:
        # Match both normalized (UNUSUAL_VOLUME) and raw (UNUSUAL_VOLUME_SCANNER) values
        filter_parts.append("(#scanner = :scanner OR #scanner = :scanner_alt)")
        filter_names["#scanner"] = "scanner_source"
        filter_values[":scanner"] = scanner
        filter_values[":scanner_alt"] = scanner + "_SCANNER"

    filter_expr = " AND ".join(filter_parts) if filter_parts else None

    sk_condition = None
    if period and period != "all":
        from datetime import datetime as dt, timedelta, timezone as tz
        days_map = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
        days = days_map.get(period)
        if days:
            cutoff = (dt.now(tz.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            sk_condition = {"gte": cutoff}

    resolved_status = (status or "all").lower()

    all_positions: list = []
    if resolved_status in ("open", "all"):
        all_positions.extend(await _exhaust_paginated(
            PaperPositionTable.list_open_paginated,
            filter_expr, filter_values or None, filter_names or None, sk_condition,
        ))
    if resolved_status in ("closed", "all"):
        all_positions.extend(await _exhaust_paginated(
            PaperPositionTable.list_closed_paginated,
            filter_expr, filter_values or None, filter_names or None, sk_condition,
        ))

    return all_positions


async def _exhaust_paginated(
    query_fn,
    filter_expression,
    filter_values,
    filter_names,
    sk_condition,
) -> list:
    """Iterate through all pages of a paginated query."""
    all_items: list = []
    cursor = None
    while True:
        positions, next_cursor = await query_fn(
            limit=200,
            cursor=cursor,
            filter_expression=filter_expression,
            filter_values=filter_values,
            filter_names=filter_names,
            sk_condition=sk_condition,
        )
        all_items.extend(positions)
        if not next_cursor:
            break
        cursor = next_cursor
    return all_items


# ============================================================================
# Performance Breakdown (Date-Range-Filtered Slices)
# ============================================================================


@router.get("/performance-breakdown")
async def get_performance_breakdown(
    days: int = 5,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    """Get performance metrics broken down by option_type, scanner, and score bucket.

    Args:
        days: Number of trading days to look back (default 5). Ignored if
              start_date/end_date provided.
        start_date: Explicit start date (YYYY-MM-DD). Overrides days.
        end_date: Explicit end date (YYYY-MM-DD). Overrides days.

    Returns:
        Breakdown by option_type, scanner_source, and conviction score bucket.
    """
    from datetime import datetime as dt, timedelta, timezone as tz
    import math

    now = dt.now(tz.utc)

    if start_date and end_date:
        cutoff = start_date
        period_end = end_date
        trading_days = days
    else:
        # Convert trading days to calendar days (approx: trading_days * 7/5)
        calendar_days = math.ceil(days * 7 / 5)
        cutoff = (now - timedelta(days=calendar_days)).strftime("%Y-%m-%d")
        period_end = now.strftime("%Y-%m-%d")
        trading_days = days

    sk_condition = {"gte": cutoff}

    # Fetch all positions in the date range
    all_positions: list = []
    all_positions.extend(await _exhaust_paginated(
        PaperPositionTable.list_open_paginated,
        None, None, None, sk_condition,
    ))
    all_positions.extend(await _exhaust_paginated(
        PaperPositionTable.list_closed_paginated,
        None, None, None, sk_condition,
    ))

    closed = [p for p in all_positions if _enum_val(p.status) == "CLOSED"]

    def _bucket_metrics(positions: list) -> dict[str, Any]:
        """Compute win rate and avg return for a group of positions."""
        closed_in_group = [p for p in positions if _enum_val(p.status) == "CLOSED"]
        wins = sum(1 for p in closed_in_group if p.current_pnl_pct > 0)
        total_return = sum(p.current_pnl_pct for p in closed_in_group)
        closed_count = len(closed_in_group)
        return {
            "count": len(positions),
            "closed": closed_count,
            "win_rate": round(wins / closed_count * 100, 1) if closed_count > 0 else None,
            "avg_return": round(total_return / closed_count, 2) if closed_count > 0 else None,
        }

    # By option_type
    by_option_type: dict[str, list] = {}
    for p in all_positions:
        opt_type = p.option_type or "UNKNOWN"
        by_option_type.setdefault(opt_type, []).append(p)

    # By scanner_source
    by_scanner: dict[str, list] = {}
    for p in all_positions:
        scanner = p.scanner_source or "UNKNOWN"
        by_scanner.setdefault(scanner, []).append(p)

    # By conviction score bucket
    def _score_bucket(score: Any) -> str:
        if score is None:
            return "UNKNOWN"
        s = float(score)
        if s < 65:
            return "<65"
        elif s < 70:
            return "65-69"
        elif s < 75:
            return "70-74"
        elif s < 80:
            return "75-79"
        else:
            return "80+"

    by_score: dict[str, list] = {}
    for p in all_positions:
        bucket = _score_bucket(p.conviction_score)
        by_score.setdefault(bucket, []).append(p)

    return {
        "period": {
            "start": cutoff,
            "end": period_end,
            "trading_days": trading_days,
        },
        "total_positions": len(all_positions),
        "total_closed": len(closed),
        "by_option_type": {k: _bucket_metrics(v) for k, v in sorted(by_option_type.items())},
        "by_scanner": {k: _bucket_metrics(v) for k, v in sorted(by_scanner.items())},
        "by_score_bucket": {k: _bucket_metrics(v) for k, v in sorted(by_score.items())},
    }


# ============================================================================
# Metrics Endpoints (Legacy — Computed on Read)
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
    positions = await PaperPositionTable.list_all()
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
    positions = await PaperPositionTable.list_all()
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
# Snapshots & Analysis Endpoints
# ============================================================================


@router.get("/positions/{position_id}/snapshots")
async def get_position_snapshots(position_id: str) -> dict[str, Any]:
    """Get daily snapshots for a position."""
    from app.db.tables import PaperSnapshotTable

    snapshots = await PaperSnapshotTable.list_by_position(position_id)
    return {
        "position_id": position_id,
        "snapshots": snapshots,
        "count": len(snapshots),
    }


@router.post("/positions/{position_id}/analyze")
async def analyze_position(position_id: str) -> dict[str, Any]:
    """Generate AI analysis for a position."""
    from datetime import datetime, timezone, timedelta

    from app.db.tables import EvaluationTable
    from app.llm.provider import get_provider
    from app.paper_trading.position_manager import extract_underlying_from_option_ticker

    # Find the position (search open then closed to avoid full scan)
    position = None
    for fetch_fn in [PaperPositionTable.list_open, PaperPositionTable.list_closed]:
        for p in await fetch_fn():
            if p.position_id == position_id:
                position = p
                break
        if position:
            break

    if not position:
        raise HTTPException(status_code=404, detail=f"Position not found: {position_id}")

    # Check cache (4h TTL)
    if position.ai_analysis and position.ai_analysis_at:
        try:
            cached_at = datetime.fromisoformat(position.ai_analysis_at)
            if datetime.now(timezone.utc) - cached_at < timedelta(hours=4):
                return {"analysis": position.ai_analysis, "cached": True}
        except (ValueError, TypeError):
            pass

    # Fetch evaluation for context
    underlying = extract_underlying_from_option_ticker(position.option_ticker)
    eval_data = await EvaluationTable.get_by_id(underlying, position.evaluation_id)

    # Build prompt
    score_info = ""
    if eval_data and isinstance(eval_data, dict):
        decision = eval_data.get("decision", {})
        score_info = (
            f" Score: {decision.get('final_score', 'N/A')}."
            f" Directional: {decision.get('directional_score', 'N/A')},"
            f" Volatility: {decision.get('volatility_score', 'N/A')},"
            f" Structure: {decision.get('structure_score', 'N/A')}."
        )

    prompt = (
        f"Analyze this options position in 2-3 sentences: "
        f"{position.option_ticker}, entry ${position.entry_price:.2f}, "
        f"current P&L {position.current_pnl_pct:.1f}%, "
        f"{position.days_held} days held, status {position.status}."
        f"{score_info}"
    )

    provider = get_provider()
    analysis = await provider.generate(prompt)

    # Cache the result
    now = datetime.now(timezone.utc).isoformat()
    await PaperPositionTable.update(
        position, {"ai_analysis": analysis, "ai_analysis_at": now}
    )

    return {"analysis": analysis, "cached": False}


@router.get("/equity-curve")
async def get_equity_curve(period: str = "30d") -> dict[str, Any]:
    """Get equity curve data from pre-aggregated daily metrics."""
    from app.paper_trading.metrics_aggregator import MetricsAggregator

    period_days = {"7d": 7, "14d": 14, "30d": 30, "90d": 90, "all": 365}
    days = period_days.get(period, 30)

    # Fetch all history to compute correct starting equity for windowed views
    all_points = await MetricsAggregator.get_daily_equity(days=365)

    # Build cumulative equity from full history
    equity = 10000.0
    full_curve = []
    for point in all_points:
        pnl = float(point.get("daily_pnl", 0))
        equity += pnl
        full_curve.append({
            "date": point.get("date", ""),
            "daily_pnl": round(pnl, 2),
            "equity": round(equity, 2),
        })

    # Return only the last N points (with correct cumulative equity)
    curve = full_curve[-days:] if period != "all" else full_curve

    return {"curve": curve, "period": period}


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
    open_positions = await PaperPositionTable.list_open()
    closed_positions = await PaperPositionTable.list_closed()
    
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
