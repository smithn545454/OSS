"""Alert configuration and management API routes."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class AlertConfigUpdate(BaseModel):
    """Request body for updating alert config."""

    enabled: Optional[bool] = None
    score_threshold: Optional[int] = None
    require_urgency_or_convergence: Optional[bool] = None
    cooldown_minutes: Optional[int] = None
    daily_cap: Optional[int] = None
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    webhook_channels: Optional[list[dict[str, str]]] = None
    setup_rule_filter_ids: Optional[list[str]] = None
    verdicts: Optional[list[str]] = None


class TestAlertRequest(BaseModel):
    """Request body for sending a test alert."""

    channel_index: Optional[int] = None


@router.get("/config")
async def get_alert_config() -> dict[str, Any]:
    """Get the current alert configuration."""
    from app.services.slack import load_alert_config, mask_config_for_response

    config = await load_alert_config()
    return mask_config_for_response(config)


@router.put("/config")
async def update_alert_config(update: AlertConfigUpdate) -> dict[str, Any]:
    """Update alert configuration.

    Persists to DynamoDB and reconfigures the live SlackAlertService.
    """
    from app.services.slack import (
        get_slack_service,
        load_alert_config,
        mask_config_for_response,
        save_alert_config,
    )

    # Load current config
    current = await load_alert_config()

    # Merge updates
    update_dict = update.model_dump(exclude_none=True)
    merged = {**current, **update_dict}

    # Validate
    if merged.get("score_threshold", 75) < 0 or merged.get("score_threshold", 75) > 100:
        raise HTTPException(status_code=400, detail="score_threshold must be 0-100")
    if merged.get("daily_cap", 10) < 1:
        raise HTTPException(status_code=400, detail="daily_cap must be >= 1")
    if merged.get("cooldown_minutes", 30) < 1:
        raise HTTPException(status_code=400, detail="cooldown_minutes must be >= 1")

    # Save to DynamoDB
    saved = await save_alert_config(merged)

    # Reconfigure live service
    slack_service = get_slack_service()
    slack_service.configure(saved)

    logger.info(f"Alert config updated: enabled={saved.get('enabled')}")
    return mask_config_for_response(saved)


@router.post("/test")
async def send_test_alert(request: TestAlertRequest) -> dict[str, Any]:
    """Send a test alert to verify webhook configuration."""
    from app.services.slack import get_slack_service

    service = get_slack_service()
    success, error = await service.send_test_alert(channel_index=request.channel_index)

    if not success:
        raise HTTPException(status_code=400, detail=error or "Test alert failed")
    return {"success": True, "message": "Test alert sent successfully"}


@router.get("/history")
async def get_alert_history_endpoint(
    date: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get recent alert history."""
    from app.services.slack import get_alert_history

    entries = await get_alert_history(date=date, limit=limit)
    return {"entries": entries, "count": len(entries), "date": date}


@router.get("/preview")
async def get_alert_preview(days: int = 3) -> dict[str, Any]:
    """Estimate how many alerts per day a configuration would produce.

    Analyzes recent APPROVE evaluations against the current alert config
    (score threshold, urgency/convergence requirement, setup rule filter)
    to estimate daily alert volume.
    """
    from app.db.tables import EvaluationTable
    from app.scoring.conviction import calculate_conviction_score, determine_urgency
    from app.services.slack import load_alert_config

    config = await load_alert_config()
    threshold = config.get("score_threshold", 75)
    require_uc = config.get("require_urgency_or_convergence", True)
    filter_ids = config.get("setup_rule_filter_ids", [])

    # Load setup rules if filter is active
    rules_by_id: dict[str, dict[str, Any]] = {}
    if filter_ids:
        try:
            from app.paper_trading.pattern_discovery import list_setup_rules
            from app.paper_trading.rule_matcher import match_rules

            all_rules = await list_setup_rules()
            rules_by_id = {r["rule_id"]: r for r in all_rules if r["rule_id"] in filter_ids}
        except Exception as e:
            logger.warning(f"Failed to load setup rules for preview: {e}")

    # Query recent APPROVE evaluations
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        evaluations = await EvaluationTable.list_by_verdict_since("APPROVE", since, limit=500)
    except Exception:
        # Fallback: try listing recent evaluations directly
        evaluations = []

    breakdown = {
        "totalEvaluations": len(evaluations),
        "belowScoreThreshold": 0,
        "failedUrgencyConvergence": 0,
        "noMatchingSetupRule": 0,
        "wouldAlert": 0,
    }

    for eval_item in evaluations:
        # Extract data needed for conviction score
        pillar_scores = {}
        for pillar in ("DIRECTIONAL", "VOLATILITY", "STRUCTURE"):
            key = f"{pillar.lower()}_score"
            val = eval_item.get(key) or eval_item.get(f"pillar_{key}")
            if val is not None:
                pillar_scores[pillar] = float(val)

        theta_ev = eval_item.get("theta_adjusted_ev") or eval_item.get("thetaAdjustedEV") or 0.0
        gate_margin = eval_item.get("gate_margin") or eval_item.get("gateMargin") or 50.0
        scanner_types = eval_item.get("scanner_source_list") or eval_item.get("scanner_list") or []
        if isinstance(scanner_types, str):
            scanner_types = [scanner_types]

        # Compute conviction score
        result = calculate_conviction_score(
            theta_adj_ev=float(theta_ev),
            pillar_scores=pillar_scores,
            gate_margin=float(gate_margin),
            scanner_types=scanner_types,
        )

        # Check score threshold
        if result.total < threshold:
            breakdown["belowScoreThreshold"] += 1
            continue

        # Check urgency/convergence
        urgency = determine_urgency(scanner_types)
        convergence = len(scanner_types)
        if require_uc and urgency != "act_now" and convergence < 2:
            breakdown["failedUrgencyConvergence"] += 1
            continue

        # Check setup rule filter
        if filter_ids and rules_by_id:
            from app.paper_trading.rule_matcher import match_rules

            decision_data = {
                "final_score": eval_item.get("final_score", 0),
                "directional_score": eval_item.get("directional_score", 0),
                "volatility_score": eval_item.get("volatility_score", 0),
                "structure_score": eval_item.get("structure_score", 0),
            }
            matched = match_rules(
                list(rules_by_id.values()),
                eval_item,
                decision_data,
                scanner_types,
            )
            if not matched:
                breakdown["noMatchingSetupRule"] += 1
                continue

        breakdown["wouldAlert"] += 1

    estimated_per_day = breakdown["wouldAlert"] / max(days, 1)

    return {
        "estimatedAlertsPerDay": round(estimated_per_day, 1),
        "daysAnalyzed": days,
        "breakdown": breakdown,
    }
