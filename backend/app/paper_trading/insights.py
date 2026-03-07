"""AI insights engine for paper trading performance optimization.

Gathers performance data and uses an LLM to generate actionable
recommendations for improving the scoring/gate/exit system.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.db.tables import CalibrationReportTable, PaperPositionTable
from app.llm.provider import get_provider
from app.paper_trading.insights_prompt import build_insights_prompt, parse_insights_response
from app.paper_trading.metrics import (
    analyze_exit_effectiveness,
    calculate_performance_metrics,
    compare_tiers,
)

logger = logging.getLogger(__name__)


async def generate_ai_insights() -> dict[str, Any]:
    """Generate AI-powered insights from paper trading performance data.

    Gathers metrics, tier comparisons, exit analysis, and calibration data,
    then sends to an LLM for analysis and recommendations.

    Returns:
        Dict with insights list, metadata, and data summary
    """
    # 1. Gather performance data
    metrics = await calculate_performance_metrics()
    positions = await PaperPositionTable.list_all(limit=1000)
    tier_data = compare_tiers(positions)
    exit_data = analyze_exit_effectiveness(positions)

    # 2. Get latest calibration report (may be None)
    try:
        reports = await CalibrationReportTable.list_recent(limit=1)
        calibration = reports[0] if reports else None
    except Exception:
        logger.warning("Could not fetch calibration reports")
        calibration = None

    # 3. Check minimum data threshold
    if metrics.closed_positions < 5:
        return {
            "insights": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_summary": {
                "positions_analyzed": metrics.total_positions,
                "win_rate": metrics.win_rate,
                "closed_positions": metrics.closed_positions,
                "calibration_report_used": None,
            },
            "message": "Need at least 5 closed positions for meaningful insights",
            "llm_provider": None,
            "tokens_used": 0,
        }

    # 4. Build prompt and call LLM
    metrics_dict = metrics.to_dict()
    system_prompt, user_prompt = build_insights_prompt(
        metrics_dict, tier_data, exit_data, calibration
    )

    try:
        provider = get_provider("anthropic")
    except Exception:
        provider = get_provider("openai")

    if not provider.is_available():
        return {
            "insights": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_summary": {
                "positions_analyzed": metrics.total_positions,
                "win_rate": metrics.win_rate,
                "closed_positions": metrics.closed_positions,
                "calibration_report_used": calibration.get("report_id") if calibration else None,
            },
            "message": "No LLM provider configured",
            "llm_provider": None,
            "tokens_used": 0,
        }

    response = await provider.generate(
        user_prompt, max_tokens=4000, system_prompt=system_prompt
    )

    # 5. Parse and return
    now = datetime.now(timezone.utc).isoformat()
    data_summary = {
        "positions_analyzed": metrics.total_positions,
        "win_rate": metrics.win_rate,
        "closed_positions": metrics.closed_positions,
        "calibration_report_used": calibration.get("report_id") if calibration else None,
    }

    if not response.success:
        return {
            "insights": [],
            "generated_at": now,
            "data_summary": data_summary,
            "llm_provider": provider.name,
            "tokens_used": response.tokens_used,
            "error": response.error or "LLM call failed",
        }

    insights = parse_insights_response(response.content)

    result: dict[str, Any] = {
        "insights": insights,
        "generated_at": now,
        "data_summary": data_summary,
        "llm_provider": provider.name,
        "tokens_used": response.tokens_used,
    }
    if not insights and response.content:
        result["error"] = "Failed to parse LLM response"

    return result
