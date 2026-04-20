"""Alert configuration and management API routes (v5 dual-conviction)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class AlertConfigUpdate(BaseModel):
    """Request body for updating alert config.

    v5-native schema. Legacy knobs (score_threshold, cheap_gem_*,
    per_scanner_thresholds, excluded_scanners, setup_rule_filter_ids,
    require_urgency_or_convergence) have been retired — see
    ``app.services.slack.DEFAULT_CONFIG`` for the canonical shape.
    """

    enabled: Optional[bool] = None
    # v5 eligibility
    hr_conviction_min: Optional[float] = None       # 0-20
    p_conviction_min: Optional[float] = None        # 0-100
    require_hr_archetype: Optional[bool] = None
    min_archetype_fit: Optional[float] = None       # 0-100
    min_regime_alignment: Optional[float] = None    # 0.0-2.0 (policy clamp is [0.5, 1.5])
    max_premium: Optional[float] = None             # Optional ceiling; None = off
    # Regime-independent
    tier_1_bypass: Optional[bool] = None
    cooldown_minutes: Optional[int] = None
    daily_cap: Optional[int] = None
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    webhook_channels: Optional[list[dict[str, str]]] = None
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

    current = await load_alert_config()
    update_dict = update.model_dump(exclude_none=True)
    merged = {**current, **update_dict}

    # --- Validation ---
    hr_min = merged.get("hr_conviction_min", 10.0)
    if hr_min is None or not 0 <= hr_min <= 20:
        raise HTTPException(
            status_code=400, detail="hr_conviction_min must be 0-20"
        )
    p_min = merged.get("p_conviction_min", 70.0)
    if p_min is None or not 0 <= p_min <= 100:
        raise HTTPException(
            status_code=400, detail="p_conviction_min must be 0-100"
        )
    fit_min = merged.get("min_archetype_fit", 60.0)
    if fit_min is None or not 0 <= fit_min <= 100:
        raise HTTPException(
            status_code=400, detail="min_archetype_fit must be 0-100"
        )
    regime_min = merged.get("min_regime_alignment", 0.0)
    if regime_min is None or not 0 <= regime_min <= 2:
        raise HTTPException(
            status_code=400, detail="min_regime_alignment must be 0.0-2.0"
        )
    if merged.get("daily_cap", 10) < 1:
        raise HTTPException(status_code=400, detail="daily_cap must be >= 1")
    if merged.get("cooldown_minutes", 30) < 1:
        raise HTTPException(status_code=400, detail="cooldown_minutes must be >= 1")
    if merged.get("max_premium") is not None and merged["max_premium"] < 0:
        raise HTTPException(status_code=400, detail="max_premium must be >= 0")

    saved = await save_alert_config(merged)

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
    """Estimate how many alerts per day the v5 config would produce.

    Replays recent APPROVE decisions against the configured HR/P floors,
    archetype fit, regime alignment, and optional premium ceiling. Tier 1
    (Sharpshooter) trades always count when ``tier_1_bypass`` is on.

    Returns a breakdown showing how many evaluations fell out at each gate,
    so the user can see whether their thresholds are too strict / loose.
    """
    from app.db.tables import EvaluationTable
    from app.services.slack import V5_HR_FLOOR, V5_P_FLOOR, load_alert_config

    config = await load_alert_config()
    hr_min = config.get("hr_conviction_min", 10.0) or 0.0
    p_min = config.get("p_conviction_min", 70.0) or 0.0
    fit_min = config.get("min_archetype_fit", 60.0) or 0.0
    regime_min = config.get("min_regime_alignment", 0.0) or 0.0
    max_premium = config.get("max_premium")
    require_hr = config.get("require_hr_archetype", False)
    tier_1_bypass = config.get("tier_1_bypass", True)
    allowed_verdicts = config.get("verdicts", ["APPROVE"])

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    evaluations: list[dict[str, Any]] = []
    for verdict in allowed_verdicts:
        try:
            evals = await EvaluationTable.list_by_verdict_since(verdict, since, limit=500)
            evaluations.extend(evals)
        except Exception:
            pass

    breakdown = {
        "totalEvaluations": len(evaluations),
        "tier1Bypassed": 0,
        "missingHrArchetype": 0,
        "regimeHeadwind": 0,
        "bothTracksFailed": 0,
        "aboveMaxPremium": 0,
        "wouldAlert": 0,
    }

    if not evaluations:
        return {
            "estimatedAlertsPerDay": 0,
            "daysAnalyzed": days,
            "breakdown": breakdown,
        }

    for item in evaluations:
        decision = item.get("decision") or {}
        quality_tier = item.get("quality_tier") or decision.get("quality_tier")

        # Tier 1 fast path
        if tier_1_bypass and quality_tier == "TIER_1":
            breakdown["tier1Bypassed"] += 1
            breakdown["wouldAlert"] += 1
            continue

        hr = decision.get("hr_conviction") or 0.0
        p = decision.get("p_conviction") or 0.0
        hr_archetype = decision.get("hr_archetype_matched")
        hr_fit = decision.get("hr_archetype_fit") or 0.0
        p_fit = decision.get("p_archetype_fit") or 0.0
        regime = decision.get("regime_alignment")

        # Sharpshooter-only mode
        if require_hr and not hr_archetype:
            breakdown["missingHrArchetype"] += 1
            continue

        # Regime headwind
        if regime_min > 0 and (regime is None or regime < regime_min):
            breakdown["regimeHeadwind"] += 1
            continue

        # Conviction + fit — either track on its own
        hr_pass = (
            hr >= hr_min
            and hr >= V5_HR_FLOOR  # still gated by system APPROVE floor
            and hr_fit >= fit_min
        )
        p_pass = (
            p >= p_min
            and p >= V5_P_FLOOR
            and p_fit >= fit_min
        )
        if not (hr_pass or p_pass):
            breakdown["bothTracksFailed"] += 1
            continue

        # Premium ceiling
        if max_premium is not None and (item.get("mid") or 0) > max_premium:
            breakdown["aboveMaxPremium"] += 1
            continue

        breakdown["wouldAlert"] += 1

    estimated_per_day = breakdown["wouldAlert"] / max(days, 1)

    return {
        "estimatedAlertsPerDay": round(estimated_per_day, 1),
        "daysAnalyzed": days,
        "breakdown": breakdown,
    }
