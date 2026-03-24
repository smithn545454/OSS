"""Alert configuration and management API routes."""

from __future__ import annotations

import asyncio
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

    Analyzes recent evaluations against the current alert config
    (score threshold, urgency/convergence requirement, setup rule filter)
    to estimate daily alert volume. Enriches raw evaluation items with
    pillar scores, gate results, and scanner types from their respective
    tables — matching how the pipeline computes conviction scores.
    """
    from app.api.routes.evaluations import (
        calculate_gate_margin,
        calculate_theta_adjusted_ev,
    )
    from app.db.tables import (
        EvaluationTable,
        FeatureValueTable,
        GateResultTable,
        OpportunityTable,
        PillarScoreTable,
    )
    from app.scoring.conviction import calculate_conviction_score, determine_urgency
    from app.services.slack import load_alert_config

    config = await load_alert_config()
    threshold = config.get("score_threshold", 75)
    require_uc = config.get("require_urgency_or_convergence", True)
    filter_ids = config.get("setup_rule_filter_ids", [])
    allowed_verdicts = config.get("verdicts", ["APPROVE"])

    # Load setup rules if filter is active
    rules_by_id: dict[str, dict[str, Any]] = {}
    if filter_ids:
        try:
            from app.paper_trading.pattern_discovery import list_setup_rules

            all_rules = await list_setup_rules()
            rules_by_id = {r["rule_id"]: r for r in all_rules if r["rule_id"] in filter_ids}
        except Exception as e:
            logger.warning(f"Failed to load setup rules for preview: {e}")

    # Query recent evaluations for all configured verdicts
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
        "belowScoreThreshold": 0,
        "failedUrgencyConvergence": 0,
        "noMatchingSetupRule": 0,
        "wouldAlert": 0,
    }

    if not evaluations:
        return {
            "estimatedAlertsPerDay": 0,
            "daysAnalyzed": days,
            "breakdown": breakdown,
        }

    # ------------------------------------------------------------------
    # Enrich evaluations with pillar scores, gate results, scanner types
    # (these live in separate tables, not on the raw Evaluation item)
    # ------------------------------------------------------------------

    def _enum_str(val: Any) -> str:
        return str(val.value) if hasattr(val, "value") else str(val)

    sem = asyncio.Semaphore(50)

    async def _limited(coro):  # type: ignore[no-untyped-def]
        async with sem:
            return await coro

    # Pre-fetch opportunities by ticker for scanner type resolution
    unique_tickers = list({
        item.get("underlying_ticker", "")
        for item in evaluations
    } - {""})

    opp_results = await asyncio.gather(*[
        _limited(OpportunityTable.list_by_ticker(t, limit=50))
        for t in unique_tickers
    ])
    opp_by_ticker: dict[str, list[Any]] = dict(zip(unique_tickers, opp_results))

    def _scanner_types_for(item: dict[str, Any]) -> list[str]:
        opportunity_id = item.get("opportunity_id")
        ticker = item.get("underlying_ticker", "")
        if opportunity_id and ticker in opp_by_ticker:
            for opp in opp_by_ticker[ticker]:
                if opp.opportunity_id == opportunity_id:
                    return [_enum_str(st.scanner_type) for st in opp.scanner_triggers]
        scanner_source = item.get("scanner_source")
        if scanner_source:
            return [scanner_source]
        return []

    # Feature names needed for setup rule matching
    _feature_names = {
        "iv_percentile", "iv_rv_ratio", "theta_adjusted_edge",
        "days_to_earnings", "atr14_pct", "rs_20d", "feasibility_ratio",
    }

    # Enrich each evaluation with pillar scores, gate results, and features
    async def _enrich(item: dict[str, Any]) -> dict[str, Any]:
        evaluation_id = item.get("evaluation_id", "")
        pillar_scores_raw, gate_results_raw, feature_values = await asyncio.gather(
            _limited(PillarScoreTable.list_by_evaluation(evaluation_id)),
            _limited(GateResultTable.list_by_evaluation(evaluation_id)),
            _limited(FeatureValueTable.list_by_evaluation(evaluation_id)),
        )

        # Merge feature values into item for rule matching
        for fv in feature_values:
            if fv.feature_name in _feature_names and fv.value is not None:
                item[fv.feature_name] = fv.value

        pillar_dict = {
            _enum_str(ps.pillar_id): ps.score
            for ps in pillar_scores_raw
        }

        gate_list = [
            {
                "enabled": gr.enabled,
                "passed": gr.passed,
                "measured_value": gr.measured_value,
                "threshold_value": gr.threshold_value,
                "operator": _enum_str(gr.operator),
            }
            for gr in gate_results_raw
        ]

        scanner_types = _scanner_types_for(item)

        gate_margin = calculate_gate_margin(gate_list)

        theta_ev = calculate_theta_adjusted_ev(
            delta=item.get("delta") or 0,
            theta=item.get("theta") or 0,
            mid=item.get("mid") or 0,
            iv=item.get("iv") or 0,
            underlying_price=item.get("underlying_price") or 0,
            dte=item.get("dte") or 30,
        )

        # Merge gate_margin into item for rule matching
        item["gate_margin"] = gate_margin

        return {
            "pillar_scores": pillar_dict,
            "gate_margin": gate_margin,
            "theta_ev": theta_ev,
            "scanner_types": scanner_types,
            "item": item,
        }

    enriched = list(await asyncio.gather(*[_enrich(item) for item in evaluations]))

    # ------------------------------------------------------------------
    # Aggregate scanner convergence per-ticker (matches /approve endpoint)
    # Multiple evaluations for the same ticker from different scanners
    # should all reflect the full convergence count.
    # ------------------------------------------------------------------
    from collections import defaultdict

    ticker_scanners: dict[str, set[str]] = defaultdict(set)
    ticker_indices: dict[str, list[int]] = defaultdict(list)
    for idx, e in enumerate(enriched):
        ticker = e["item"].get("underlying_ticker", "")
        ticker_scanners[ticker].update(e["scanner_types"])
        ticker_indices[ticker].append(idx)

    for ticker, indices in ticker_indices.items():
        all_scanners = list(ticker_scanners[ticker])
        for idx in indices:
            enriched[idx]["scanner_types"] = all_scanners

    # ------------------------------------------------------------------
    # Score and filter against alert criteria
    # ------------------------------------------------------------------
    for e in enriched:
        pillar_scores = e["pillar_scores"]
        scanner_types = e["scanner_types"]
        item = e["item"]

        result = calculate_conviction_score(
            theta_adj_ev=e["theta_ev"],
            pillar_scores=pillar_scores,
            gate_margin=e["gate_margin"],
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
                "final_score": item.get("final_score", 0),
                "directional_score": pillar_scores.get("DIRECTIONAL", 0),
                "volatility_score": pillar_scores.get("VOLATILITY", 0),
                "structure_score": pillar_scores.get("STRUCTURE", 0),
            }
            matched = match_rules(
                list(rules_by_id.values()),
                item,
                decision_data,
                scanner_types,
                active_only=False,  # User explicitly selected these rules
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
