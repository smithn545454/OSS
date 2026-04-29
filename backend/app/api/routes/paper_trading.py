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

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.tables import PaperPositionTable
from app.paper_trading.metrics import (
    analyze_exit_effectiveness,
    calculate_performance_metrics,
    compare_tiers,
)
from app.paper_trading.position_manager import (
    close_position_manually,
    extract_underlying_from_option_ticker,
)

logger = logging.getLogger(__name__)

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
        "scanner_list": pos.scanner_list,
        "convergence_count": pos.convergence_count,
        "conviction_score": pos.conviction_score,
        "pillar_premium_leverage": pos.pillar_premium_leverage,
        "pillar_underlying_behavior": pos.pillar_underlying_behavior,
        "pillar_setup_quality": pos.pillar_setup_quality,
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
        "matched_rule_ids": pos.matched_rule_ids,
        "matched_rules": pos.matched_rules,
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

    if verdict and verdict.upper() != "ALL":
        filter_parts.append("#verdict = :verdict")
        filter_names["#verdict"] = "verdict_at_entry"
        filter_values[":verdict"] = verdict

    if scanner and scanner.lower() != "all":
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
        positions, next_cursor = await _query_positions(
            PaperPositionTable.list_open_paginated,
            limit=limit, cursor=cursor,
            filter_expr=filter_expr,
            filter_values=filter_values or None,
            filter_names=filter_names or None,
            sk_condition=sk_condition,
        )
    elif resolved_status == "closed":
        positions, next_cursor = await _query_positions(
            PaperPositionTable.list_closed_paginated,
            limit=limit, cursor=cursor,
            filter_expr=filter_expr,
            filter_values=filter_values or None,
            filter_names=filter_names or None,
            sk_condition=sk_condition,
        )
    else:
        # "all" — query both partitions sequentially
        open_pos, open_cursor = await _query_positions(
            PaperPositionTable.list_open_paginated,
            limit=limit, cursor=cursor,
            filter_expr=filter_expr,
            filter_values=filter_values or None,
            filter_names=filter_names or None,
            sk_condition=sk_condition,
        )
        remaining = limit - len(open_pos)
        closed_pos: list = []
        closed_cursor = None
        if remaining > 0:
            closed_pos, closed_cursor = await _query_positions(
                PaperPositionTable.list_closed_paginated,
                limit=remaining, cursor=None,
                filter_expr=filter_expr,
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


@router.get("/positions/browse")
async def browse_positions(
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "entry_date",
    sort_order: str = "desc",
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    period: Optional[str] = None,
    min_score: Optional[float] = None,
    min_return: Optional[float] = None,
    confluence: Optional[bool] = None,
) -> dict[str, Any]:
    """Browse positions with server-side sorting and offset-based pagination.

    Unlike /positions (cursor-based, DynamoDB-native ordering), this endpoint
    loads all matching positions, sorts in-memory, and returns a page slice.
    Designed for the Trade Library tab.

    Args:
        status: Filter by status (open, closed, all). Default: all.
        page: Page number (1-indexed). Default: 1.
        page_size: Items per page (default 50, max 200).
        sort_by: Sort field (entry_date, current_pnl_pct, conviction_score,
                 scanner_source, days_held, underlying_ticker,
                 pillar_premium_leverage, pillar_underlying_behavior,
                 pillar_setup_quality).
        sort_order: asc or desc. Default: desc.
        verdict: Filter by verdict_at_entry (APPROVE, WATCH).
        scanner: Filter by scanner_source.
        period: Filter by entry_date (7d, 14d, 30d, 90d).
        min_score: Minimum conviction score filter.
        min_return: Minimum return % filter.
        confluence: If true, only show positions with convergence_count >= 2.
    """
    page_size = min(max(page_size, 1), 200)
    page = max(page, 1)

    resolved_status = (status or "all").lower()
    positions = await _query_filtered_positions(resolved_status, verdict, scanner, period)

    # Apply additional Trade Library filters (not in DynamoDB filter expression)
    if min_score is not None:
        positions = [p for p in positions
                     if p.conviction_score is not None and p.conviction_score >= min_score]
    if min_return is not None:
        positions = [p for p in positions if p.current_pnl_pct >= min_return]
    if confluence:
        positions = [p for p in positions
                     if (p.convergence_count or 0) >= 2]

    # Sort
    valid_sort_fields = {
        "entry_date", "current_pnl_pct", "conviction_score", "scanner_source",
        "days_held", "underlying_ticker",
        "pillar_premium_leverage", "pillar_underlying_behavior", "pillar_setup_quality",
    }
    if sort_by not in valid_sort_fields:
        sort_by = "entry_date"
    reverse = sort_order.lower() != "asc"

    def sort_key(p):
        val = getattr(p, sort_by, None)
        if val is None:
            # Nulls sort last
            return (1, "")
        if isinstance(val, str):
            return (0, val.lower())
        return (0, val)

    positions.sort(key=sort_key, reverse=reverse)

    # Paginate
    total_count = len(positions)
    total_pages = max(1, -(-total_count // page_size))  # ceil division
    start = (page - 1) * page_size
    end = start + page_size
    page_positions = positions[start:end]

    return {
        "positions": [_position_to_dict(p) for p in page_positions],
        "count": len(page_positions),
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "sort_by": sort_by,
        "sort_order": sort_order,
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
        scorebands_data = await MetricsAggregator.get_scoreband_metrics()
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
            "by_score_band": {
                s.get("band", "?"): {
                    "count": int(s.get("count", 0)),
                    "profitable": int(s.get("win_count", 0)),
                    "profitable_pct": round(
                        int(s.get("win_count", 0)) / int(s.get("closed_count", 1)) * 100, 1
                    ) if int(s.get("closed_count", 0)) > 0 else 0,
                    "closed_count": int(s.get("closed_count", 0)),
                }
                for s in scorebands_data
            } if scorebands_data else {},
        }

    # Filtered path: compute from matching positions
    positions = await _query_filtered_positions(status, verdict, scanner, period)

    open_positions = [p for p in positions if _enum_val(p.status) == "OPEN"]
    closed_positions = [p for p in positions if _enum_val(p.status) == "CLOSED"]

    wins = sum(1 for p in closed_positions if p.current_pnl_pct > 0)
    losses = sum(1 for p in closed_positions if p.current_pnl_pct < 0)
    closed_count = len(closed_positions)

    # Portfolio-wide P&L for the period (matches equity curve).
    # When only a period filter is active (no scanner/verdict), use the same daily equity
    # data that feeds the equity curve so KPI and chart tell the same story.
    has_position_filters = any([verdict, scanner])
    if not has_position_filters and period and period != "all":
        period_days_map = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
        days = period_days_map.get(period, 30)
        daily_points = await MetricsAggregator.get_daily_equity(days=365)
        window_points = daily_points[-days:]
        total_pnl_dollars = sum(float(p.get("daily_pnl", 0)) for p in window_points)
    else:
        # Position-cohort P&L when scanner/verdict filters narrow the population
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
    by_score_band: dict[str, dict[str, Any]] = {}
    for p in positions:
        score = p.conviction_score
        if score is None:
            continue
        band = _score_band_label(float(score))
        entry = by_score_band.setdefault(band, {"count": 0, "profitable": 0})
        entry["count"] += 1
        if p.current_pnl_pct > 0:
            entry["profitable"] += 1

    # Scanner / verdict / tier breakdowns from loaded positions
    by_scanner: dict[str, dict[str, Any]] = {}
    by_verdict_map: dict[str, dict[str, Any]] = {}
    by_tier_map: dict[str, dict[str, Any]] = {}
    for p in positions:
        _bucket = {"count": 0, "closed_count": 0, "win_count": 0,
                   "loss_count": 0, "total_pnl_dollars": 0.0}

        scanner_key = _normalize_scanner(
            getattr(p, "scanner_source", None)
        ) or "UNKNOWN"
        s = by_scanner.setdefault(scanner_key, {**_bucket})
        s["count"] += 1

        verdict_key = str(_enum_val(p.verdict_at_entry))
        v = by_verdict_map.setdefault(verdict_key, {**_bucket})
        v["count"] += 1

        tier_key = str(_enum_val(p.quality_tier_at_entry) or "NONE")
        t = by_tier_map.setdefault(tier_key, {**_bucket})
        t["count"] += 1

        if _enum_val(p.status) == "CLOSED":
            is_win = p.current_pnl_pct > 0
            dollar = (
                ((p.exit_price if p.exit_price is not None else p.current_price)
                 - p.entry_price) * p.quantity * 100
            )
            for bucket in (s, v, t):
                bucket["closed_count"] += 1
                bucket["win_count"] += int(is_win)
                bucket["loss_count"] += int(not is_win)
                bucket["total_pnl_dollars"] += dollar

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
        "by_scanner": by_scanner,
        "by_verdict": by_verdict_map,
        "by_tier": by_tier_map,
        "equity_curve": [],
        "by_score_band": by_score_band,
    }


@router.get("/scanner-performance")
async def get_scanner_performance(
    period: Optional[str] = None,
    verdict: Optional[str] = None,
) -> dict[str, Any]:
    """Get detailed scanner performance data with weekly win rate trends.

    Powers the Scanner Intelligence tab — one row per scanner with metrics
    and sparkline data (weekly win rates for trend visualization).

    Args:
        period: Filter by entry_date (7d, 14d, 30d, 90d, all)
        verdict: Filter by verdict_at_entry (APPROVE, WATCH)
    """
    from datetime import datetime as dt, timezone as tz

    positions = await _query_filtered_positions(None, verdict, None, period)
    closed = [p for p in positions if _enum_val(p.status) == "CLOSED"]

    scanners: dict[str, dict[str, Any]] = {}

    for p in positions:
        key = _normalize_scanner(p.scanner_source) or "UNKNOWN"
        if key not in scanners:
            scanners[key] = {
                "total": 0, "closed": 0, "open": 0,
                "win_count": 0, "loss_count": 0,
                "win_rate": 0, "avg_return": 0,
                "total_pnl_dollars": 0.0,
                "avg_days_held": 0.0,
                "avg_conviction_score": None,
                "best_trade": None,
                "top_trades": [],
                "weekly_win_rates": [],
                "_score_sum": 0.0, "_score_count": 0,
                "_return_sum": 0.0, "_days_sum": 0,
                "_closed_positions": [],
            }
        s = scanners[key]
        s["total"] += 1

        if p.conviction_score is not None:
            s["_score_sum"] += float(p.conviction_score)
            s["_score_count"] += 1

        if _enum_val(p.status) == "CLOSED":
            s["closed"] += 1
            s["_return_sum"] += p.current_pnl_pct
            s["_days_sum"] += p.days_held
            dollar_pnl = (
                ((p.exit_price if p.exit_price is not None else p.current_price)
                 - p.entry_price) * p.quantity * 100
            )
            s["total_pnl_dollars"] += dollar_pnl
            if p.current_pnl_pct > 0:
                s["win_count"] += 1
            else:
                s["loss_count"] += 1
            s["_closed_positions"].append(p)
        else:
            s["open"] += 1

    # Compute derived metrics and weekly trends
    for key, s in scanners.items():
        if s["closed"] > 0:
            s["win_rate"] = round(s["win_count"] / s["closed"] * 100, 2)
            s["avg_return"] = round(s["_return_sum"] / s["closed"], 2)
            s["avg_days_held"] = round(s["_days_sum"] / s["closed"], 1)
        if s["_score_count"] > 0:
            s["avg_conviction_score"] = round(s["_score_sum"] / s["_score_count"], 1)
        s["total_pnl_dollars"] = round(s["total_pnl_dollars"], 2)

        # Best trade and top 5 trades
        closed_sorted = sorted(
            s["_closed_positions"], key=lambda p: p.current_pnl_pct, reverse=True
        )
        if closed_sorted:
            best = closed_sorted[0]
            s["best_trade"] = {
                "ticker": best.underlying_ticker
                or extract_underlying_from_option_ticker(best.option_ticker),
                "return_pct": round(best.current_pnl_pct, 2),
                "position_id": best.position_id,
            }
            s["top_trades"] = [
                {
                    "ticker": p.underlying_ticker
                    or extract_underlying_from_option_ticker(p.option_ticker),
                    "return_pct": round(p.current_pnl_pct, 2),
                    "conviction_score": p.conviction_score,
                    "days_held": p.days_held,
                    "position_id": p.position_id,
                }
                for p in closed_sorted[:5]
            ]

        # Weekly win rates for sparkline
        weekly: dict[str, dict[str, int]] = {}
        for p in s["_closed_positions"]:
            date_str = p.exit_date or p.entry_date
            try:
                d = dt.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            iso_year, iso_week, _ = d.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            w = weekly.setdefault(week_key, {"closed": 0, "wins": 0})
            w["closed"] += 1
            if p.current_pnl_pct > 0:
                w["wins"] += 1

        s["weekly_win_rates"] = sorted([
            {
                "week": wk,
                "closed": data["closed"],
                "wins": data["wins"],
                "win_rate": round(data["wins"] / data["closed"] * 100, 1)
                           if data["closed"] > 0 else 0,
            }
            for wk, data in weekly.items()
        ], key=lambda x: x["week"])

        # Clean up internal fields
        del s["_score_sum"]
        del s["_score_count"]
        del s["_return_sum"]
        del s["_days_sum"]
        del s["_closed_positions"]

    return {"scanners": scanners, "period": period or "all"}


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

    if verdict and verdict.upper() != "ALL":
        filter_parts.append("#verdict = :verdict")
        filter_names["#verdict"] = "verdict_at_entry"
        filter_values[":verdict"] = verdict

    if scanner and scanner.lower() != "all":
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


async def _query_positions(
    query_fn,
    limit: int,
    cursor,
    filter_expr,
    filter_values,
    filter_names,
    sk_condition,
) -> tuple[list, Any]:
    """Query positions, accumulating pages when filters are active.

    DynamoDB's Limit caps items *scanned* (before FilterExpression), not items
    *returned*. A single query with Limit=200 and a selective filter can return
    0 results even when matching items exist deeper in the partition. When a
    filter is active, this function scans multiple DynamoDB pages until the
    requested number of filtered results is found.
    """
    if not filter_expr:
        # No filter — single DynamoDB query is sufficient
        return await query_fn(
            limit=limit, cursor=cursor, sk_condition=sk_condition,
        )

    # Accumulate filtered results across multiple DynamoDB pages
    accumulated: list = []
    db_cursor = cursor
    max_pages = 50  # Safety cap: 50 * 200 = 10,000 items scanned
    while len(accumulated) < limit and max_pages > 0:
        batch, db_cursor = await query_fn(
            limit=200, cursor=db_cursor,
            filter_expression=filter_expr,
            filter_values=filter_values,
            filter_names=filter_names,
            sk_condition=sk_condition,
        )
        accumulated.extend(batch)
        max_pages -= 1
        if not db_cursor:
            break
    return accumulated, db_cursor


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
# Edge Intelligence Endpoints
# ============================================================================


@router.get("/edge-briefing")
async def get_edge_briefing(
    days: int = 10,
    include_market: bool = True,
) -> dict[str, Any]:
    """Get rolling-window edge intelligence briefing.

    Returns performance analytics sliced by option type, scanner, score bucket,
    DTE bucket, quality tier, and convergence count, plus deterministic insights.

    Args:
        days: Number of trading days to look back (default 10).
        include_market: Whether to include SPY/VIX market context.
    """
    import math
    from datetime import datetime as dt, timedelta, timezone as tz

    from app.paper_trading.edge_intelligence import compute_edge_briefing

    now = dt.now(tz.utc)
    calendar_days = math.ceil(days * 7 / 5)
    cutoff = (now - timedelta(days=calendar_days)).strftime("%Y-%m-%d")
    period_end = now.strftime("%Y-%m-%d")

    sk_condition = {"gte": cutoff}

    all_positions: list = []
    all_positions.extend(await _exhaust_paginated(
        PaperPositionTable.list_open_paginated,
        None, None, None, sk_condition,
    ))
    all_positions.extend(await _exhaust_paginated(
        PaperPositionTable.list_closed_paginated,
        None, None, None, sk_condition,
    ))

    # Fetch market context
    market_context = None
    if include_market:
        try:
            from app.services.polygon import PolygonClient

            async with PolygonClient() as polygon:
                spy_snap = await polygon.get_snapshot("SPY")
                vix_snap = await polygon.get_snapshot("VIX")
                market_context = {
                    "spy": {
                        "price": spy_snap.get("close") if spy_snap else None,
                        "change_percent": spy_snap.get("todaysChangePerc") if spy_snap else None,
                    } if spy_snap else None,
                    "vix": {
                        "price": vix_snap.get("close") if vix_snap else None,
                    } if vix_snap else None,
                }
        except Exception as e:
            logger.warning(f"Failed to fetch market context: {e}")

    briefing = compute_edge_briefing(
        positions=all_positions,
        period_start=cutoff,
        period_end=period_end,
        trading_days=days,
        market_context=market_context,
    )
    return briefing.to_dict()


@router.get("/trade-context")
async def get_trade_context(
    option_type: str,
    scanner: Optional[str] = None,
    score: Optional[float] = None,
    dte_bucket: Optional[str] = None,
    days: int = 20,
) -> dict[str, Any]:
    """Get historical context for trades matching specific characteristics.

    Used on the Evaluation Detail page to show "trades like this one."

    Args:
        option_type: CALL or PUT (required).
        scanner: Scanner source (e.g., BREAKOUT).
        score: Conviction score (e.g., 82).
        dte_bucket: DTE bucket letter (A, B, C, D).
        days: Number of trading days to look back (default 20).
    """
    import math
    from datetime import datetime as dt, timedelta, timezone as tz

    from app.paper_trading.edge_intelligence import compute_trade_context

    now = dt.now(tz.utc)
    calendar_days = math.ceil(days * 7 / 5)
    cutoff = (now - timedelta(days=calendar_days)).strftime("%Y-%m-%d")
    sk_condition = {"gte": cutoff}

    all_positions: list = []
    all_positions.extend(await _exhaust_paginated(
        PaperPositionTable.list_open_paginated,
        None, None, None, sk_condition,
    ))
    all_positions.extend(await _exhaust_paginated(
        PaperPositionTable.list_closed_paginated,
        None, None, None, sk_condition,
    ))

    context = compute_trade_context(
        option_type=option_type.upper(),
        scanner=scanner,
        score=score,
        dte_bucket=dte_bucket,
        positions=all_positions,
    )
    return context.to_dict()


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
# Repair & Maintenance Endpoints
# ============================================================================


@router.post("/repair-uv")
async def repair_uv_positions(
    dry_run: bool = True,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Repair corrupted UV paper trading positions.

    UV positions were created with option tickers missing the O: prefix,
    so the batch updater could never find them in the Polygon chain.
    This endpoint fetches historical prices and replays exit conditions.

    Args:
        dry_run: If True (default), report what would change without writing.
                 Set to False to actually apply repairs.
        limit: Max positions to process (for testing). None = all.
    """
    from app.paper_trading.repair_uv_positions import repair_corrupted_uv_positions
    from app.services.polygon import PolygonClient

    try:
        async with PolygonClient() as polygon:
            result = await repair_corrupted_uv_positions(
                polygon_client=polygon,
                dry_run=dry_run,
                limit=limit,
            )

        return {
            "success": True,
            "dry_run": dry_run,
            "total_corrupted": result.total_corrupted,
            "repaired": result.repaired,
            "no_historical_data": result.no_historical_data,
            "errors": result.errors,
            "error_details": result.error_details[:10],
            "sample_repairs": result.sample_repairs,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error repairing UV positions: {str(e)}",
        )


@router.post("/rebuild-metrics")
async def rebuild_metrics() -> dict[str, Any]:
    """Rebuild all pre-aggregated metrics from actual position data.

    Use after repairing positions to reconcile atomic counters.
    """
    from app.paper_trading.metrics_aggregator import MetricsAggregator

    try:
        summary = await MetricsAggregator.rebuild_all_metrics()
        return {"success": True, **summary}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error rebuilding metrics: {str(e)}",
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
        parts = [f" Score: {decision.get('final_score', 'N/A')}."]
        # Emit whichever pillar regime is populated.
        v4_labels = (
            ("directional_conviction_score", "Directional Conviction"),
            ("move_potential_score", "Move Potential"),
            ("trade_structure_score", "Trade Structure"),
        )
        v3_labels = (
            ("premium_leverage_score", "Premium Leverage"),
            ("underlying_behavior_score", "Underlying Behavior"),
            ("setup_quality_score", "Setup Quality"),
        )
        active = v4_labels if all(
            decision.get(k) is not None for k, _ in v4_labels
        ) else v3_labels
        parts.append(" " + ", ".join(
            f"{label}: {decision.get(key, 'N/A')}" for key, label in active
        ) + ".")
        score_info = "".join(parts)

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
    """Get equity curve data from pre-aggregated daily metrics.

    Returns relative performance starting at 0 for the selected period.
    The equity value represents cumulative P&L during the window, not absolute portfolio value.
    """
    from app.paper_trading.metrics_aggregator import MetricsAggregator

    period_days = {"7d": 7, "14d": 14, "30d": 30, "90d": 90, "all": 365}
    days = period_days.get(period, 30)

    all_points = await MetricsAggregator.get_daily_equity(days=365)

    # Slice to the requested window first, then build cumulative from 0
    window_points = all_points[-days:] if period != "all" else all_points

    equity = 0.0
    curve = []
    for point in window_points:
        pnl = float(point.get("daily_pnl", 0))
        equity += pnl
        curve.append({
            "date": point.get("date", ""),
            "daily_pnl": round(pnl, 2),
            "equity": round(equity, 2),
        })

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


# ============================================================================
# Pattern Discovery Endpoints
# ============================================================================


class PatternDiscoveryRequest(BaseModel):
    """Request body for triggering pattern analysis."""

    period: Optional[str] = None
    verdict: Optional[str] = None
    scanner: Optional[str] = None
    min_sample: int = 5
    min_win_rate: float = 0.55


@router.post("/pattern-discovery")
async def run_pattern_discovery(request: PatternDiscoveryRequest) -> dict[str, Any]:
    """Run on-demand pattern discovery analysis using AI.

    Creates a 'running' stub and dispatches an async Lambda worker
    to avoid API Gateway's 30-second timeout. Frontend polls for results.
    """
    import json as json_mod
    import os

    import boto3

    from app.paper_trading.pattern_discovery import create_analysis_stub

    # Create stub — returns immediately with analysis_id
    result = await create_analysis_stub(
        period=request.period,
        verdict=request.verdict,
        scanner=request.scanner,
        min_sample=request.min_sample,
        min_win_rate=request.min_win_rate,
    )

    # If insufficient data, return early (no worker needed)
    if result["status"] != "running":
        return result

    # Fire-and-forget: invoke Lambda async to do the LLM work
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
    payload = {
        "source": "oss.scheduler",
        "action": "pattern_discovery_worker",
        "analysis_id": result["analysis_id"],
        "period": request.period,
        "verdict": request.verdict,
        "scanner": request.scanner,
        "min_sample": request.min_sample,
        "min_win_rate": request.min_win_rate,
    }

    try:
        lambda_client = boto3.client("lambda")
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json_mod.dumps(payload),
        )
        logger.info(f"Dispatched pattern discovery worker for {result['analysis_id']}")
    except Exception as e:
        logger.error(f"Failed to dispatch pattern discovery worker: {e}")
        # Update stub to error
        from app.db.dynamodb import get_dynamodb
        from app.db.tables import PaperPositionTable

        db = get_dynamodb()
        try:
            await db.update_item(
                PaperPositionTable.TABLE,
                f"ANALYSIS#{result['analysis_id']}",
                "META",
                {"status": "error", "error_message": f"Failed to dispatch: {e}"},
            )
        except Exception:
            pass
        result["status"] = "error"
        result["message"] = f"Failed to start analysis: {e}"

    return result


@router.get("/pattern-discovery")
async def list_pattern_analyses(limit: int = 10) -> dict[str, Any]:
    """List previous pattern analysis runs."""
    from app.paper_trading.pattern_discovery import list_analyses

    analyses = await list_analyses(limit=min(limit, 50))
    return {"analyses": analyses, "count": len(analyses)}


@router.get("/pattern-discovery/{analysis_id}")
async def get_pattern_analysis(analysis_id: str) -> dict[str, Any]:
    """Get a specific pattern analysis with its archetypes."""
    from app.paper_trading.pattern_discovery import get_analysis

    result = await get_analysis(analysis_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Analysis not found: {analysis_id}")
    return result


# ============================================================================
# Setup Rules CRUD
# ============================================================================


class SetupRuleRequest(BaseModel):
    """Request body for creating a setup rule."""

    name: str
    criteria: dict[str, Any]
    source: Optional[str] = "ai"  # "ai" | "manual"
    source_analysis_id: Optional[str] = None
    performance_at_creation: Optional[dict[str, Any]] = None
    mode: Optional[str] = "production"  # "production" | "test"
    regime: Optional[str] = None  # Scoring regime; defaults to CURRENT_SCORING_REGIME


class SetupRuleUpdateRequest(BaseModel):
    """Request body for updating a setup rule."""

    is_active: Optional[bool] = None
    name: Optional[str] = None
    mode: Optional[str] = None  # "production" | "test"


@router.get("/setup-rules")
async def get_setup_rules() -> dict[str, Any]:
    """List all setup rules."""
    from app.paper_trading.pattern_discovery import list_setup_rules

    rules = await list_setup_rules()
    return {"rules": rules, "count": len(rules)}


@router.post("/setup-rules")
async def create_setup_rule_endpoint(request: SetupRuleRequest) -> dict[str, Any]:
    """Create a new setup rule (from archetype or manual)."""
    from app.paper_trading.pattern_discovery import create_setup_rule

    rule_data: dict[str, Any] = {
        "name": request.name,
        "criteria": request.criteria,
        "source": request.source or "ai",
        "source_analysis_id": request.source_analysis_id,
        "performance_at_creation": request.performance_at_creation,
        "mode": request.mode or "production",
    }
    if request.regime:
        rule_data["regime"] = request.regime
    rule = await create_setup_rule(rule_data)
    return {"rule": rule, "message": "Setup rule created"}


@router.put("/setup-rules/{rule_id}")
async def update_setup_rule_endpoint(
    rule_id: str, request: SetupRuleUpdateRequest
) -> dict[str, Any]:
    """Update a setup rule (e.g., toggle active/inactive)."""
    from app.paper_trading.pattern_discovery import update_setup_rule

    updates = {}
    if request.is_active is not None:
        updates["is_active"] = request.is_active
    if request.name is not None:
        updates["name"] = request.name
    if request.mode is not None:
        updates["mode"] = request.mode

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    result = await update_setup_rule(rule_id, updates)
    if not result:
        raise HTTPException(status_code=404, detail=f"Setup rule not found: {rule_id}")
    return {"rule": result, "message": "Setup rule updated"}


@router.delete("/setup-rules/{rule_id}")
async def delete_setup_rule_endpoint(rule_id: str) -> dict[str, Any]:
    """Delete a setup rule."""
    from app.paper_trading.pattern_discovery import delete_setup_rule

    success = await delete_setup_rule(rule_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Setup rule not found: {rule_id}")
    return {"message": f"Setup rule {rule_id} deleted"}


@router.get("/setup-rules/performance/batch")
async def get_setup_rules_performance_batch() -> dict[str, Any]:
    """Get performance stats for all setup rules from matched closed positions."""
    import statistics

    from app.db.tables import PaperPositionTable

    closed = await PaperPositionTable.list_closed(limit=2000)

    # Bucket positions by matched rule IDs (set at position creation or by backfill)
    rule_positions: dict[str, list] = {}
    for p in closed:
        if not p.matched_rule_ids:
            continue
        for rid in p.matched_rule_ids:
            rule_positions.setdefault(rid, []).append(p)

    performances: dict[str, Any] = {}
    for rid, positions in rule_positions.items():
        returns = [p.current_pnl_pct for p in positions]
        wins = [r for r in returns if r > 0]
        days_held = [p.days_held for p in positions if p.days_held is not None]

        performances[rid] = {
            "sample_size": len(positions),
            "performance": {
                "win_rate": len(wins) / len(returns),
                "avg_return": sum(returns) / len(returns),
                "median_return": statistics.median(returns),
                "sample_size": len(positions),
                "avg_days_held": sum(days_held) / len(days_held) if days_held else None,
            },
        }

    return {"performances": performances}


@router.get("/setup-rules/{rule_id}/performance")
async def get_setup_rule_performance(rule_id: str) -> dict[str, Any]:
    """Get ongoing performance for a setup rule from matched closed positions."""
    from app.db.tables import PaperPositionTable
    import statistics

    closed = await PaperPositionTable.list_closed(limit=2000)
    matched_positions = [
        p for p in closed
        if p.matched_rule_ids and rule_id in p.matched_rule_ids
    ]

    if not matched_positions:
        return {"rule_id": rule_id, "performance": None, "sample_size": 0}

    returns = [p.current_pnl_pct for p in matched_positions]
    wins = [r for r in returns if r > 0]
    days_held = [p.days_held for p in matched_positions if p.days_held is not None]

    return {
        "rule_id": rule_id,
        "sample_size": len(matched_positions),
        "performance": {
            "win_rate": len(wins) / len(returns),
            "avg_return": sum(returns) / len(returns),
            "median_return": statistics.median(returns),
            "sample_size": len(matched_positions),
            "avg_days_held": sum(days_held) / len(days_held) if days_held else None,
        },
    }


@router.post("/setup-rules/backfill")
async def backfill_setup_rule_matches(
    batch_size: int = 50,
    status_filter: str = "all",
) -> dict[str, Any]:
    """Backfill matched_rule_ids on positions that were created before rules existed.

    For each position missing matched_rule_ids, fetches the original evaluation
    and feature data, re-runs rule matching, and updates the position in DynamoDB.

    Args:
        batch_size: Max positions to process per call (default 50, keeps within
            API Gateway's 30s timeout). Call repeatedly until updated=0.
        status_filter: "open", "closed", or "all" (default "all")
    """
    from app.db.tables import (
        EvaluationTable,
        FeatureValueTable,
        PaperPositionTable,
    )
    from app.paper_trading.pattern_discovery import list_setup_rules
    from app.paper_trading.rule_matcher import format_matched_rules, match_rules

    all_rules = await list_setup_rules()
    if not all_rules:
        return {"message": "No setup rules found", "updated": 0, "skipped": 0}

    # Get positions missing matched_rule_ids
    all_positions: list[PaperPosition] = []
    if status_filter in ("all", "open"):
        all_positions.extend(await PaperPositionTable.list_open(limit=2000))
    if status_filter in ("all", "closed"):
        all_positions.extend(await PaperPositionTable.list_closed(limit=2000))

    needs_backfill = [p for p in all_positions if p.matched_rule_ids is None][:batch_size]
    total_remaining = sum(1 for p in all_positions if p.matched_rule_ids is None)
    logger.info(
        f"Setup rule backfill: processing {len(needs_backfill)} of "
        f"{total_remaining} positions needing matching"
    )

    updated = 0
    skipped = 0
    errors = 0

    for pos in needs_backfill:
        try:
            # Fetch original evaluation
            eval_data = await EvaluationTable.get_by_id(
                pos.underlying_ticker or pos.option_ticker.split("O:")[1][:4]
                if pos.underlying_ticker is None else pos.underlying_ticker,
                pos.evaluation_id,
            )
            if not eval_data:
                skipped += 1
                continue

            # EvaluationTable.get_by_id returns flat dict with decision nested
            evaluation = eval_data
            decision = eval_data.get("decision") or {}

            # Fetch features for this evaluation
            features = await FeatureValueTable.list_by_evaluation(pos.evaluation_id)
            vol_features: dict[str, Any] = {}
            for f in features:
                if f.feature_name in (
                    "iv_percentile", "iv_rv_ratio", "theta_adjusted_edge",
                    "days_to_earnings", "atr14_pct", "rs_20d", "feasibility_ratio",
                ):
                    if f.value is not None:
                        vol_features[f.feature_name] = f.value

            # Build eval_dict from original evaluation data (same as position_manager)
            option_type = evaluation.get("option_type", "")
            if hasattr(option_type, "value"):
                option_type = option_type.value
            eval_dict: dict[str, Any] = {
                "option_type": str(option_type).upper(),
                "dte": evaluation.get("dte"),
                "iv": evaluation.get("iv"),
                "delta": evaluation.get("delta"),
                "spread_pct": evaluation.get("spread_pct"),
                "open_interest": evaluation.get("open_interest"),
                "volume": evaluation.get("volume"),
                "underlying_price": evaluation.get("underlying_price"),
                "moneyness_pct": evaluation.get("moneyness_pct"),
                **vol_features,
            }

            decision_dict = {
                "final_score": decision.get("final_score"),
                "premium_leverage_score": decision.get("premium_leverage_score"),
                "underlying_behavior_score": decision.get("underlying_behavior_score"),
                "setup_quality_score": decision.get("setup_quality_score"),
                "directional_conviction_score": decision.get("directional_conviction_score"),
                "move_potential_score": decision.get("move_potential_score"),
                "trade_structure_score": decision.get("trade_structure_score"),
            }

            scanner_list = pos.scanner_list or []

            matched = match_rules(all_rules, eval_dict, decision_dict, scanner_list)
            if matched:
                rule_ids = [r["rule_id"] for r in matched]
                rule_snapshots = format_matched_rules(matched, include_criteria=True)
                await PaperPositionTable.update(pos, {
                    "matched_rule_ids": rule_ids,
                    "matched_rules": rule_snapshots,
                })
                updated += 1
            else:
                # Mark as processed with empty list so we don't re-check
                await PaperPositionTable.update(pos, {
                    "matched_rule_ids": [],
                    "matched_rules": [],
                })
                skipped += 1

        except Exception as e:
            logger.warning(f"Backfill failed for position {pos.position_id}: {e}")
            errors += 1

    remaining = total_remaining - len(needs_backfill)
    return {
        "message": f"Backfill complete: {updated} updated, {skipped} skipped, {errors} errors",
        "total_positions": len(needs_backfill),
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "remaining": remaining,
    }


@router.post("/setup-rules/migrate-regime")
async def migrate_setup_rules_regime() -> dict[str, Any]:
    """Tag existing setup rules that lack a regime field as 'v1'.

    Idempotent — rules that already have a regime field are skipped.
    """
    from app.paper_trading.pattern_discovery import list_setup_rules, update_setup_rule

    rules = await list_setup_rules()
    migrated = 0
    for rule in rules:
        if not rule.get("regime"):
            await update_setup_rule(rule["rule_id"], {"regime": "v1"})
            migrated += 1

    return {
        "message": f"Migrated {migrated} rules to regime v1",
        "total_rules": len(rules),
        "migrated": migrated,
        "already_tagged": len(rules) - migrated,
    }


# ============================================================================
# Custom Analysis Endpoints
# ============================================================================


class CustomAnalysisRequest(BaseModel):
    """Request body for triggering a custom analysis."""

    prompt: str
    period: Optional[str] = None
    verdict: Optional[str] = None
    scanner: Optional[str] = None
    min_return: Optional[float] = None


@router.post("/custom-analysis")
async def run_custom_analysis_endpoint(request: CustomAnalysisRequest) -> dict[str, Any]:
    """Run a custom analysis on paper trade data using AI.

    Accepts a user-provided analytical question. Creates a 'running' stub
    and dispatches an async Lambda worker. Frontend polls for results.
    Returns both freeform markdown analysis and structured suggested rules.
    """
    import json as json_mod
    import os

    import boto3

    from app.paper_trading.custom_analysis import create_custom_analysis_stub

    if not request.prompt or not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required")

    if len(request.prompt) > 2000:
        raise HTTPException(status_code=400, detail="Prompt must be under 2000 characters")

    result = await create_custom_analysis_stub(
        prompt=request.prompt.strip(),
        period=request.period,
        verdict=request.verdict,
        scanner=request.scanner,
        min_return=request.min_return,
    )

    if result["status"] != "running":
        return result

    # Fire-and-forget: invoke Lambda async
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
    payload = {
        "source": "oss.scheduler",
        "action": "custom_analysis_worker",
        "analysis_id": result["analysis_id"],
        "prompt": request.prompt.strip(),
        "period": request.period,
        "verdict": request.verdict,
        "scanner": request.scanner,
        "min_return": request.min_return,
    }

    try:
        lambda_client = boto3.client("lambda")
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json_mod.dumps(payload),
        )
        logger.info(f"Dispatched custom analysis worker for {result['analysis_id']}")
    except Exception as e:
        logger.error(f"Failed to dispatch custom analysis worker: {e}")
        from app.db.dynamodb import get_dynamodb
        from app.db.tables import PaperPositionTable

        db = get_dynamodb()
        try:
            await db.update_item(
                PaperPositionTable.TABLE,
                f"CUSTOM_ANALYSIS#{result['analysis_id']}",
                "META",
                {"status": "error", "error_message": f"Failed to dispatch: {e}"},
            )
        except Exception:
            pass
        result["status"] = "error"
        result["message"] = f"Failed to start analysis: {e}"

    return result


@router.get("/custom-analyses")
async def list_custom_analyses_endpoint(limit: int = 10) -> dict[str, Any]:
    """List previous custom analysis runs."""
    from app.paper_trading.custom_analysis import list_custom_analyses

    analyses = await list_custom_analyses(limit=min(limit, 50))
    return {"analyses": analyses, "count": len(analyses)}


@router.get("/custom-analysis/{analysis_id}")
async def get_custom_analysis_endpoint(analysis_id: str) -> dict[str, Any]:
    """Get a specific custom analysis with its suggested rules."""
    from app.paper_trading.custom_analysis import get_custom_analysis

    result = await get_custom_analysis(analysis_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Analysis not found: {analysis_id}")
    return result
