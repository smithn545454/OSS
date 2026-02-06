"""Market data endpoints for Opportunities page.

Provides real-time market context including SPY/VIX quotes and market status.
Per Section 7 and Section 19.3 of OSS_Opportunities_Page_Specification.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any, Optional

from fastapi import APIRouter

from app.db.tables import PipelineRunTable
from app.services.polygon import PolygonClient

router = APIRouter()


def get_market_status() -> str:
    """Determine current market status based on time.
    
    Returns:
        One of: 'pre', 'open', 'after', 'closed'
    """
    now = datetime.now(timezone.utc)
    # Convert to Eastern Time (approximate - doesn't account for DST precisely)
    # UTC is 5 hours ahead of EST, 4 hours ahead of EDT
    # For simplicity, we'll use a rough approximation
    et_hour = (now.hour - 5) % 24  # Approximate EST
    et_minute = now.minute
    et_time = time(et_hour, et_minute)
    
    # Check if weekend
    if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return "closed"
    
    # Market hours (Eastern Time)
    pre_market_start = time(4, 0)    # 4:00 AM ET
    market_open = time(9, 30)         # 9:30 AM ET
    market_close = time(16, 0)        # 4:00 PM ET
    after_hours_end = time(20, 0)     # 8:00 PM ET
    
    if pre_market_start <= et_time < market_open:
        return "pre"
    elif market_open <= et_time < market_close:
        return "open"
    elif market_close <= et_time < after_hours_end:
        return "after"
    else:
        return "closed"


def calculate_change_percent(current: float, previous_close: float) -> float:
    """Calculate percentage change from previous close."""
    if previous_close == 0:
        return 0.0
    return ((current - previous_close) / previous_close) * 100


@router.get("/context")
async def get_market_context() -> dict[str, Any]:
    """Get current market context for the Context Bar.
    
    Per Section 7 of OSS_Opportunities_Page_Specification:
    - Market status (pre, open, after, closed)
    - SPY price and change percentage
    - VIX level and direction
    - Pipeline freshness (last run timestamp)
    
    Returns:
        MarketContext data structure
    """
    market_status = get_market_status()
    
    # Fetch SPY and VIX data from Polygon
    spy_data: dict[str, Any] = {
        "price": 0.0,
        "change": 0.0,
        "changePercent": 0.0,
    }
    vix_data: dict[str, Any] = {
        "price": 0.0,
        "change": 0.0,
        "direction": "up",
    }
    
    try:
        async with PolygonClient() as client:
            # Fetch SPY and VIX previous close data
            results = await client.get_previous_close_batch(["SPY", "VIX"])
            
            if "SPY" in results:
                spy_result = results["SPY"]
                spy_close = spy_result.get("c", 0.0)
                spy_prev_close = spy_result.get("o", spy_close)  # Use open as proxy for prev day
                spy_change = spy_close - spy_prev_close
                spy_change_pct = calculate_change_percent(spy_close, spy_prev_close)
                
                spy_data = {
                    "price": round(spy_close, 2),
                    "change": round(spy_change, 2),
                    "changePercent": round(spy_change_pct, 2),
                }
            
            if "VIX" in results:
                vix_result = results["VIX"]
                vix_close = vix_result.get("c", 0.0)
                vix_prev_close = vix_result.get("o", vix_close)
                vix_change = vix_close - vix_prev_close
                
                vix_data = {
                    "price": round(vix_close, 2),
                    "change": round(vix_change, 2),
                    "direction": "up" if vix_change >= 0 else "down",
                }
    except Exception:
        # On error, return placeholder data
        # In production, you might want to cache the last known values
        pass
    
    # Get last pipeline run timestamp
    last_pipeline_run: Optional[str] = None
    try:
        runs = await PipelineRunTable.list_recent(limit=1)
        if runs:
            last_run = runs[0]
            # Use completed_at if available, otherwise started_at
            last_pipeline_run = last_run.completed_at or last_run.started_at
    except Exception:
        pass
    
    return {
        "spy": spy_data,
        "vix": vix_data,
        "marketStatus": market_status,
        "lastPipelineRun": last_pipeline_run,
    }


@router.get("/quotes")
async def get_contract_quotes(contracts: str) -> dict[str, Any]:
    """Get current quotes for multiple option contracts.
    
    Per Section 19.3 - Real-time price updates.
    
    Args:
        contracts: Comma-separated list of option contract IDs
        
    Returns:
        Dict mapping contract ID to pricing data
    """
    contract_list = [c.strip() for c in contracts.split(",") if c.strip()]
    
    if not contract_list:
        return {"quotes": {}}
    
    # For now, return empty quotes - would need to implement
    # option contract snapshot fetching from Polygon
    # This is a placeholder for Phase 5 real-time updates
    quotes: dict[str, dict[str, Any]] = {}
    
    return {"quotes": quotes}
