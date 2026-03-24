"""Evaluation endpoints."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.db.tables import (
    EvaluationTable,
    FeatureValueTable,
    GateResultTable,
    OpportunityTable,
    PaperPositionTable,
    PillarScoreTable,
    TradeThesisTable,
)
from app.services.catalyst import CatalystDataService
from app.services.earnings_cache import EarningsCacheService
from app.services.finnhub import FinnhubClient
from app.services.polygon import PolygonClient

router = APIRouter()
logger = logging.getLogger(__name__)


def _enum_str(val: Any) -> str:
    """Convert an enum-like value to string."""
    return str(val.value) if hasattr(val, "value") else str(val)


def _opt_enum_str(val: Any) -> str | None:
    """Convert an optional enum-like value to string."""
    if val is None:
        return None
    return str(val.value) if hasattr(val, "value") else str(val)

# In-memory cache for ticker company names (survives across warm Lambda invocations)
_ticker_name_cache: dict[str, Optional[str]] = {}


async def _noop_list() -> list:
    """Return empty list, used as a no-op coroutine in asyncio.gather()."""
    return []


async def _get_company_name(ticker: str) -> Optional[str]:
    """Fetch company name from Polygon, with in-memory caching and 5s timeout."""
    if ticker in _ticker_name_cache:
        return _ticker_name_cache[ticker]

    try:
        async with asyncio.timeout(5):
            async with PolygonClient() as client:
                details = await client.get_ticker_details(ticker)
                name = details.get("name") if details else None
                _ticker_name_cache[ticker] = name
                return name
    except Exception as e:
        logger.debug(f"Failed to fetch company name for {ticker}: {e}")
        _ticker_name_cache[ticker] = None
        return None


def _create_catalyst_service() -> CatalystDataService:
    """Create a CatalystDataService with Finnhub support if configured.

    Returns:
        CatalystDataService with earnings cache if Finnhub API key is available,
        otherwise a basic service without earnings lookup.
    """
    settings = get_settings()

    if settings.finnhub_api_key:
        finnhub_client = FinnhubClient(settings.finnhub_api_key)
        earnings_cache = EarningsCacheService(finnhub_client=finnhub_client)
        return CatalystDataService(earnings_cache=earnings_cache)
    else:
        logger.warning("No Finnhub API key configured, earnings data will be unavailable")
        return CatalystDataService()


# Urgency classification based on scanner type (Section 4.2.5)
URGENCY_BY_SCANNER = {
    "BREAKOUT": "act_now",
    "BREAKDOWN": "act_now",
    "UNUSUAL_VOLUME": "hours",
    "UNUSUAL_VOLUME_SCANNER": "hours",
    "COMPRESSION_EXPANSION": "patient",
    "CHEAP_OPTIONS": "patient",
}


def calculate_theta_adjusted_ev(
    delta: float,
    theta: float,
    mid: float,
    iv: float,
    underlying_price: float,
    dte: int,
    expected_hold_days: int = 5,
) -> float:
    """Calculate theta-adjusted expected value.

    Per Section 4.2.1:
    θ-Adjusted EV = (P_profit × E_gain) - (P_loss × E_loss) - (θ × T_hold)

    Simplified calculation for display purposes.
    """
    if mid <= 0 or dte <= 0:
        return 0.0

    # Daily expected move
    daily_expected_move = underlying_price * (iv / math.sqrt(252))

    # Expected gain from delta exposure over holding period
    expected_price_move = daily_expected_move * math.sqrt(expected_hold_days)
    expected_gain = abs(delta) * expected_price_move * 100  # Per contract

    # Theta cost over holding period
    theta_cost = abs(theta) * expected_hold_days * 100  # Per contract

    # Simplified EV (assumes 50/50 probability for directional move)
    ev = (0.6 * expected_gain) - (0.4 * mid * 0.5 * 100) - theta_cost

    return round(ev, 2)


def calculate_gate_margin(gate_results: list[dict[str, Any]]) -> float:
    """Calculate average gate margin for conviction scoring.

    Per Section 4.2.3:
    Per-gate margin = (Actual - Threshold) / Threshold × 100
    Gate Margin Score = average(all gate margins), clamped 0-100
    """
    if not gate_results:
        return 50.0  # Neutral score if no gates

    margins = []
    for gate in gate_results:
        if not gate.get("enabled", True) or not gate.get("passed", False):
            continue

        measured = gate.get("measured_value", 0)
        threshold = gate.get("threshold_value", 1)
        operator = gate.get("operator", "gte")

        if threshold == 0:
            continue

        # Calculate margin based on operator
        if operator in ("gte", ">="):
            margin = (measured - threshold) / abs(threshold) * 100
        elif operator in ("lte", "<="):
            margin = (threshold - measured) / abs(threshold) * 100
        else:
            margin = 50  # Default for between/equals

        margins.append(max(0, min(100, margin)))

    if not margins:
        return 50.0

    return round(sum(margins) / len(margins), 1)


def determine_urgency(scanner_types: list[str]) -> str:
    """Determine urgency level from scanner types.

    Per Section 4.2.5:
    - 🔴 Act Now: Breakout/Breakdown
    - 🟡 Hours: Unusual Volume
    - 🟢 Patient: Compression, Cheap Options
    """
    urgency_priority = {"act_now": 3, "hours": 2, "patient": 1}

    max_urgency = "patient"
    max_priority = 0

    for scanner in scanner_types:
        scanner_upper = scanner.upper()
        urgency = URGENCY_BY_SCANNER.get(scanner_upper, "patient")
        priority = urgency_priority.get(urgency, 1)

        if priority > max_priority:
            max_priority = priority
            max_urgency = urgency

    return max_urgency


@router.get("")
async def list_evaluations(
    verdict: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List evaluations, optionally filtered by verdict."""
    if verdict:
        items = await EvaluationTable.list_by_verdict(verdict.upper(), limit=limit)
    else:
        # For now, return empty - would need to implement a scan or date-based query
        items = []

    return {
        "evaluations": items,
        "count": len(items),
        "filter": {"verdict": verdict} if verdict else None,
    }


@router.get("/detail/{ticker}/{evaluation_id}")
async def get_evaluation_detail_by_id(
    ticker: str,
    evaluation_id: str,
) -> dict[str, Any]:
    """Get complete evaluation details by ticker and evaluation_id (no timestamp needed).

    Uses EvaluationTable.get_by_id() which queries by PK and scans SK suffix,
    avoiding URL-encoding issues with ISO timestamps containing +00:00.

    MUST be defined BEFORE the catch-all /{ticker}/{timestamp}/{evaluation_id}
    route to avoid FastAPI route collision (both are 3-segment paths).
    """
    evaluation = await EvaluationTable.get_by_id(ticker, evaluation_id)
    if not evaluation:
        raise HTTPException(
            status_code=404,
            detail=f"Evaluation not found: {ticker}/{evaluation_id}",
        )

    opportunity_id = evaluation.get("opportunity_id")

    # Fetch all related data in parallel
    (
        pillar_scores,
        gate_results,
        position,
        opportunities,
        features,
        thesis,
        company_name,
    ) = await asyncio.gather(
        PillarScoreTable.list_by_evaluation(evaluation_id),
        GateResultTable.list_by_evaluation(evaluation_id),
        PaperPositionTable.get_by_evaluation_id(evaluation_id),
        OpportunityTable.list_by_ticker(ticker, limit=50) if opportunity_id else _noop_list(),
        FeatureValueTable.list_by_evaluation(evaluation_id),
        TradeThesisTable.get_by_evaluation_id(evaluation_id),
        _get_company_name(ticker),
    )

    # Process pillar scores
    pillar_scores_dict = [
        {
            "pillar_id": _enum_str(ps.pillar_id),
            "score": ps.score,
            "contributors": [
                {
                    "feature_name": c.feature_name,
                    "subscore": c.subscore,
                    "weight": c.weight,
                    "weighted_contribution": c.weighted_contribution,
                    "raw_value": c.raw_value,
                    "distance_from_neutral": c.distance_from_neutral,
                }
                for c in ps.contributors
            ],
            "tags": ps.tags,
        }
        for ps in pillar_scores
    ]

    # Process gate results
    gate_results_dict = [
        {
            "gate_id": gr.gate_id,
            "enabled": gr.enabled,
            "passed": gr.passed,
            "measured_value": gr.measured_value,
            "threshold_value": gr.threshold_value,
            "operator": _enum_str(gr.operator),
            "units": gr.units,
            "reason_code": gr.reason_code,
            "notes": gr.notes,
        }
        for gr in gate_results
    ]

    # Process paper position
    position_dict = None
    if position:
        position_dict = {
            "position_id": position.position_id,
            "option_ticker": position.option_ticker,
            "entry_price": position.entry_price,
            "entry_date": position.entry_date,
            "quantity": position.quantity,
            "verdict_at_entry": _enum_str(position.verdict_at_entry),
            "quality_tier_at_entry": _opt_enum_str(position.quality_tier_at_entry),
            "exit_price": position.exit_price,
            "exit_date": position.exit_date,
            "exit_reason": _opt_enum_str(position.exit_reason),
            "current_price": position.current_price,
            "current_pnl_pct": round(position.current_pnl_pct, 2),
            "max_favorable_excursion": round(position.max_favorable_excursion, 2),
            "max_adverse_excursion": round(position.max_adverse_excursion, 2),
            "days_held": position.days_held,
            "status": _enum_str(position.status),
            "last_updated": position.last_updated,
        }

    # Extract scanner triggers from opportunities
    scanner_triggers = []
    if opportunity_id:
        for opp in opportunities:
            if opp.opportunity_id == opportunity_id:
                scanner_triggers = [
                    {
                        "scanner_type": _enum_str(st.scanner_type),
                        "reason_codes": st.reason_codes,
                        "metrics": st.metrics,
                        "triggered_at": st.triggered_at,
                    }
                    for st in opp.scanner_triggers
                ]
                break

    # Process features
    features_dict = {
        f.feature_name: {
            "value": f.value,
            "units": f.units,
            "computed_at": f.computed_at,
        }
        for f in features
    }

    # Process thesis
    thesis_dict = None
    if thesis:
        has_api = hasattr(thesis.exit_plan, 'to_api_dict')
        exit_plan = thesis.exit_plan.to_api_dict() if has_api else {
            "profit_target": thesis.exit_plan.profit_target,
            "stop_loss": thesis.exit_plan.stop_loss,
            "time_exit": thesis.exit_plan.time_exit,
            "take_profits": [],
            "stop_loss_level": None,
            "time_exit_level": None,
        }
        thesis_dict = {
            "thesis_id": thesis.thesis_id,
            "status": _enum_str(thesis.status),
            "setup_summary": thesis.setup_summary,
            "thesis": thesis.thesis,
            "supporting_evidence": thesis.supporting_evidence,
            "risks": thesis.risks,
            "invalidation_conditions": thesis.invalidation_conditions,
            "exit_plan": exit_plan,
            "llm_provider": _enum_str(thesis.llm_provider),
            "model_used": thesis.model_used,
            "tokens_used": thesis.tokens_used,
            "generated_at": thesis.generated_at,
            "error_message": thesis.error_message,
        }

    all_gates_passed = all(gr.passed for gr in gate_results if gr.enabled)
    failed_gates = [gr.gate_id for gr in gate_results if gr.enabled and not gr.passed]

    theta_adjusted_ev = calculate_theta_adjusted_ev(
        delta=evaluation.get("delta", 0),
        theta=evaluation.get("theta", 0),
        mid=evaluation.get("mid", 0),
        iv=evaluation.get("iv", 0),
        underlying_price=evaluation.get("underlying_price", 0),
        dte=evaluation.get("dte", 30),
    )

    # Merge feature values into evaluation dict for rule matching
    for feat_key in (
        "iv_percentile", "iv_rv_ratio", "theta_adjusted_edge",
        "days_to_earnings", "atr14_pct", "rs_20d", "feasibility_ratio",
    ):
        feat = features_dict.get(feat_key)
        if feat and feat.get("value") is not None:
            evaluation[feat_key] = feat["value"]

    # Enrich with sector for sector-aware rule matching
    try:
        from app.db.tables import SP500TickerTable
        sector_map = await SP500TickerTable.get_sector_map()
        eval_ticker = evaluation.get("underlying_ticker", "")
        if eval_ticker in sector_map:
            evaluation["sector"] = sector_map[eval_ticker]
    except Exception:
        pass

    # Match setup rules (all active rules, both production and test)
    matched_rules_list: list[dict[str, Any]] = []
    try:
        from app.paper_trading.pattern_discovery import list_setup_rules  # noqa: I001
        from app.paper_trading.rule_matcher import match_rules, format_matched_rules

        all_rules = await list_setup_rules()
        if all_rules:
            # Build decision dict from evaluation's decision field
            eval_decision = evaluation.get("decision", {})
            if not isinstance(eval_decision, dict):
                eval_decision = {}
            # Also try pillar scores for decision fields
            if not eval_decision.get("final_score"):
                for ps in pillar_scores_dict:
                    pid = ps.get("pillar_id", "")
                    if pid == "DIRECTIONAL":
                        eval_decision.setdefault("directional_score", ps.get("score"))
                    elif pid == "VOLATILITY":
                        eval_decision.setdefault("volatility_score", ps.get("score"))
                    elif pid == "STRUCTURE":
                        eval_decision.setdefault("structure_score", ps.get("score"))

            # Extract scanner types from scanner_triggers
            eval_scanners = [
                st.get("scanner_type", "")
                for st in scanner_triggers
                if st.get("scanner_type")
            ]

            # Fallback: use evaluation's scanner_source if opportunity wasn't found
            if not eval_scanners:
                eval_scanner_source = evaluation.get("scanner_source")
                if eval_scanner_source:
                    eval_scanners = [eval_scanner_source]

            matched = match_rules(all_rules, evaluation, eval_decision, eval_scanners)
            matched_rules_list = format_matched_rules(matched, include_criteria=True)
    except Exception as e:
        logger.warning(f"Setup rule matching failed on detail page: {e}")

    return {
        "evaluation": evaluation,
        "thetaAdjustedEV": theta_adjusted_ev,
        "company_name": company_name,
        "pillar_scores": pillar_scores_dict,
        "gate_results": gate_results_dict,
        "position": position_dict,
        "scanner_triggers": scanner_triggers,
        "features": features_dict,
        "thesis": thesis_dict,
        "matched_rules": matched_rules_list,
        "summary": {
            "all_gates_passed": all_gates_passed,
            "failed_gates": failed_gates,
            "pillar_count": len(pillar_scores_dict),
            "gate_count": len(gate_results_dict),
            "has_position": position_dict is not None,
            "feature_count": len(features_dict),
            "has_thesis": thesis_dict is not None,
        },
    }


@router.get("/{ticker}/{timestamp}/{evaluation_id}")
async def get_evaluation(
    ticker: str,
    timestamp: str,
    evaluation_id: str,
) -> dict[str, Any]:
    """Get a specific evaluation by key."""
    item = await EvaluationTable.get(ticker, timestamp, evaluation_id)
    if not item:
        raise HTTPException(
            status_code=404,
            detail=f"Evaluation not found: {ticker}/{timestamp}/{evaluation_id}",
        )
    return item


@router.get("/{ticker}/{timestamp}/{evaluation_id}/detail")
async def get_evaluation_detail(
    ticker: str,
    timestamp: str,
    evaluation_id: str,
) -> dict[str, Any]:
    """Get complete evaluation details including pillar scores, gates, and position.

    Per Section 19.1 of OSS_Complete_Requirements.md - Evaluation Detail Page.

    Returns:
        Evaluation + Decision + PillarScores (3) + GateResults (all) +
        PaperPosition (if exists) + Opportunity scanner triggers + Features
    """
    evaluation = await EvaluationTable.get(ticker, timestamp, evaluation_id)
    if not evaluation:
        raise HTTPException(
            status_code=404,
            detail=f"Evaluation not found: {ticker}/{timestamp}/{evaluation_id}",
        )

    opportunity_id = evaluation.get("opportunity_id")

    # Fetch all related data in parallel
    (
        pillar_scores,
        gate_results,
        position,
        opportunities,
        features,
        thesis,
    ) = await asyncio.gather(
        PillarScoreTable.list_by_evaluation(evaluation_id),
        GateResultTable.list_by_evaluation(evaluation_id),
        PaperPositionTable.get_by_evaluation_id(evaluation_id),
        OpportunityTable.list_by_ticker(ticker, limit=50) if opportunity_id else _noop_list(),
        FeatureValueTable.list_by_evaluation(evaluation_id),
        TradeThesisTable.get_by_evaluation_id(evaluation_id),
    )

    # Process pillar scores
    pillar_scores_dict = [
        {
            "pillar_id": _enum_str(ps.pillar_id),
            "score": ps.score,
            "contributors": [
                {
                    "feature_name": c.feature_name,
                    "subscore": c.subscore,
                    "weight": c.weight,
                    "weighted_contribution": c.weighted_contribution,
                    "raw_value": c.raw_value,
                    "distance_from_neutral": c.distance_from_neutral,
                }
                for c in ps.contributors
            ],
            "tags": ps.tags,
        }
        for ps in pillar_scores
    ]

    # Process gate results
    gate_results_dict = [
        {
            "gate_id": gr.gate_id,
            "enabled": gr.enabled,
            "passed": gr.passed,
            "measured_value": gr.measured_value,
            "threshold_value": gr.threshold_value,
            "operator": _enum_str(gr.operator),
            "units": gr.units,
            "reason_code": gr.reason_code,
            "notes": gr.notes,
        }
        for gr in gate_results
    ]

    # Process paper position
    position_dict = None
    if position:
        position_dict = {
            "position_id": position.position_id,
            "option_ticker": position.option_ticker,
            "entry_price": position.entry_price,
            "entry_date": position.entry_date,
            "quantity": position.quantity,
            "verdict_at_entry": _enum_str(position.verdict_at_entry),
            "quality_tier_at_entry": _opt_enum_str(position.quality_tier_at_entry),
            "exit_price": position.exit_price,
            "exit_date": position.exit_date,
            "exit_reason": _opt_enum_str(position.exit_reason),
            "current_price": position.current_price,
            "current_pnl_pct": round(position.current_pnl_pct, 2),
            "max_favorable_excursion": round(position.max_favorable_excursion, 2),
            "max_adverse_excursion": round(position.max_adverse_excursion, 2),
            "days_held": position.days_held,
            "status": _enum_str(position.status),
            "last_updated": position.last_updated,
        }

    # Extract scanner triggers from opportunities
    scanner_triggers = []
    if opportunity_id:
        for opp in opportunities:
            if opp.opportunity_id == opportunity_id:
                scanner_triggers = [
                    {
                        "scanner_type": _enum_str(st.scanner_type),
                        "reason_codes": st.reason_codes,
                        "metrics": st.metrics,
                        "triggered_at": st.triggered_at,
                    }
                    for st in opp.scanner_triggers
                ]
                break

    # Process features
    features_dict = {
        f.feature_name: {
            "value": f.value,
            "units": f.units,
            "computed_at": f.computed_at,
        }
        for f in features
    }

    # Process thesis
    thesis_dict = None
    if thesis:
        has_api = hasattr(thesis.exit_plan, 'to_api_dict')
        exit_plan = thesis.exit_plan.to_api_dict() if has_api else {
            "profit_target": thesis.exit_plan.profit_target,
            "stop_loss": thesis.exit_plan.stop_loss,
            "time_exit": thesis.exit_plan.time_exit,
            "take_profits": [],
            "stop_loss_level": None,
            "time_exit_level": None,
        }
        thesis_dict = {
            "thesis_id": thesis.thesis_id,
            "status": _enum_str(thesis.status),
            "setup_summary": thesis.setup_summary,
            "thesis": thesis.thesis,
            "supporting_evidence": thesis.supporting_evidence,
            "risks": thesis.risks,
            "invalidation_conditions": thesis.invalidation_conditions,
            "exit_plan": exit_plan,
            "llm_provider": _enum_str(thesis.llm_provider),
            "model_used": thesis.model_used,
            "tokens_used": thesis.tokens_used,
            "generated_at": thesis.generated_at,
            "error_message": thesis.error_message,
        }

    all_gates_passed = all(gr.passed for gr in gate_results if gr.enabled)
    failed_gates = [gr.gate_id for gr in gate_results if gr.enabled and not gr.passed]

    return {
        "evaluation": evaluation,
        "pillar_scores": pillar_scores_dict,
        "gate_results": gate_results_dict,
        "position": position_dict,
        "scanner_triggers": scanner_triggers,
        "features": features_dict,
        "thesis": thesis_dict,
        "summary": {
            "all_gates_passed": all_gates_passed,
            "failed_gates": failed_gates,
            "pillar_count": len(pillar_scores_dict),
            "gate_count": len(gate_results_dict),
            "has_position": position_dict is not None,
            "feature_count": len(features_dict),
            "has_thesis": thesis_dict is not None,
        },
    }


@router.get("/by-verdict/{verdict}")
async def list_by_verdict(
    verdict: str,
    limit: int = 50,
) -> dict[str, Any]:
    """List evaluations by verdict (APPROVE, WATCH, REJECT)."""
    valid_verdicts = ["APPROVE", "WATCH", "REJECT"]
    verdict_upper = verdict.upper()

    if verdict_upper not in valid_verdicts:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid verdict. Must be one of: {valid_verdicts}",
        )

    items = await EvaluationTable.list_by_verdict(verdict_upper, limit=limit)

    return {
        "verdict": verdict_upper,
        "evaluations": items,
        "count": len(items),
    }


@router.get("/filtered")
async def list_evaluations_filtered(
    verdict: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    option_type: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List evaluations with filters for Pipeline Monitor breakdown.

    Per Section 19.2 - Pipeline Monitor breakdown controls.

    Args:
        verdict: Filter by verdict (APPROVE, WATCH, REJECT)
        dte_bucket: Filter by DTE bucket (A, B, C, D)
        option_type: Filter by option type (CALL, PUT)
        limit: Maximum evaluations to return

    Returns:
        Filtered list of evaluations with summary statistics
    """
    # Start with verdict-based query if provided, else get all verdicts
    if verdict:
        items = await EvaluationTable.list_by_verdict(verdict.upper(), limit=500)
    else:
        # Get all verdicts
        approve_items = await EvaluationTable.list_by_verdict("APPROVE", limit=200)
        watch_items = await EvaluationTable.list_by_verdict("WATCH", limit=200)
        reject_items = await EvaluationTable.list_by_verdict("REJECT", limit=200)
        items = approve_items + watch_items + reject_items

    # Apply in-memory filters
    filtered = items

    if dte_bucket:
        filtered = [e for e in filtered if e.get("dte_bucket") == dte_bucket.upper()]

    if option_type:
        filtered = [e for e in filtered if e.get("option_type") == option_type.upper()]

    # Sort by evaluated_at descending and limit
    filtered.sort(key=lambda x: x.get("evaluated_at", ""), reverse=True)
    filtered = filtered[:limit]

    # Compute summary statistics
    stats = {
        "total": len(filtered),
        "by_verdict": {},
        "by_dte_bucket": {},
        "by_option_type": {},
    }

    for item in filtered:
        # By verdict
        decision = item.get("decision")
        v = decision.get("verdict") if isinstance(decision, dict) else None
        if v:
            stats["by_verdict"][v] = stats["by_verdict"].get(v, 0) + 1

        # By DTE bucket
        bucket = item.get("dte_bucket")
        if bucket:
            stats["by_dte_bucket"][bucket] = stats["by_dte_bucket"].get(bucket, 0) + 1

        # By option type
        ot = item.get("option_type")
        if ot:
            stats["by_option_type"][ot] = stats["by_option_type"].get(ot, 0) + 1

    return {
        "evaluations": filtered,
        "count": len(filtered),
        "filters": {
            "verdict": verdict,
            "dte_bucket": dte_bucket,
            "option_type": option_type,
        },
        "statistics": stats,
    }


def _trading_days_cutoff(trading_days: int) -> str:
    """Return ISO timestamp N trading days (weekdays) ago from now."""
    now = datetime.now(timezone.utc)
    days_back = 0
    counted = 0
    while counted < trading_days:
        days_back += 1
        if (now - timedelta(days=days_back)).weekday() < 5:  # Mon=0..Fri=4
            counted += 1
    return (now - timedelta(days=days_back)).isoformat()


@router.get("/approve")
async def list_approve_evaluations(
    exclude_earnings: bool = True,
    earnings_days: int = 7,
    scanner: Optional[str] = None,
    max_age_trading_days: int = 2,
    limit: int = 100,
) -> dict[str, Any]:
    """Get APPROVE evaluations with enhanced data for Opportunities page.

    Per Section 19.1 of OSS_Opportunities_Page_Specification:
    - Fetch APPROVE evaluations from latest pipeline run
    - Include scanner convergence data
    - Include gate margin calculations
    - Include theta-adjusted EV
    - Optionally exclude contracts with earnings within N days

    All per-evaluation enrichment queries run in parallel to stay well under
    the API Gateway 30-second integration timeout.
    """
    sem = asyncio.Semaphore(50)

    async def _limited(coro):  # type: ignore[no-untyped-def]
        async with sem:
            return await coro

    # ------------------------------------------------------------------
    # 1. Fetch APPROVE evaluations and deduplicate by contract
    # ------------------------------------------------------------------
    cutoff_iso = _trading_days_cutoff(max_age_trading_days)
    items = await EvaluationTable.list_by_verdict_since("APPROVE", cutoff_iso, limit=500)

    contract_counts: dict[str, int] = {}
    for item in items:
        key = item.get("option_ticker", "")
        if key:
            contract_counts[key] = contract_counts.get(key, 0) + 1

    seen_contracts: set[str] = set()
    unique_items: list[dict[str, Any]] = []
    for item in items:
        key = item.get("option_ticker", "")
        if key and key not in seen_contracts:
            seen_contracts.add(key)
            item["approvalCount"] = contract_counts.get(key, 1)
            unique_items.append(item)
    items = unique_items

    # ------------------------------------------------------------------
    # 2. Pre-fetch opportunities for all unique tickers (parallel)
    # ------------------------------------------------------------------
    unique_tickers = list({
        item.get("underlying_ticker", "")
        for item in items
    } - {""})

    opp_results = await asyncio.gather(*[
        _limited(OpportunityTable.list_by_ticker(t, limit=50))
        for t in unique_tickers
    ])
    opp_by_ticker: dict[str, list[Any]] = dict(zip(unique_tickers, opp_results))

    def _scanner_types_for(item: dict[str, Any]) -> list[str]:
        """Resolve scanner types from pre-fetched opportunities or eval field."""
        opportunity_id = item.get("opportunity_id")
        ticker = item.get("underlying_ticker", "")
        if opportunity_id and ticker in opp_by_ticker:
            for opp in opp_by_ticker[ticker]:
                if opp.opportunity_id == opportunity_id:
                    return [
                        _enum_str(st.scanner_type)
                        for st in opp.scanner_triggers
                    ]
        eval_scanner_source = item.get("scanner_source")
        if eval_scanner_source:
            return [eval_scanner_source]
        return []

    # ------------------------------------------------------------------
    # 3. Filter by scanner using pre-fetched opportunities
    # ------------------------------------------------------------------
    if scanner:
        scanner_upper = scanner.upper()
        items = [
            item for item in items
            if scanner_upper in [s.upper() for s in _scanner_types_for(item)]
        ]

    # ------------------------------------------------------------------
    # 4. Pre-fetch earnings for unique tickers (parallel)
    # ------------------------------------------------------------------
    excluded_for_earnings: list[dict[str, Any]] = []

    if exclude_earnings:
        catalyst_service = _create_catalyst_service()
        remaining_tickers = list({
            item.get("underlying_ticker", "")
            for item in items
        } - {""})

        earnings_results = await asyncio.gather(*[
            _limited(catalyst_service.get_days_to_earnings(t))
            for t in remaining_tickers
        ], return_exceptions=True)

        earnings_by_ticker_raw: dict[str, int | None] = {}
        for t, result in zip(remaining_tickers, earnings_results):
            if isinstance(result, BaseException):
                earnings_by_ticker_raw[t] = None
            else:
                earnings_by_ticker_raw[t] = result

        # Filter out items near earnings
        kept_items: list[dict[str, Any]] = []
        for item in items:
            ticker = item.get("underlying_ticker", "")
            days_to = earnings_by_ticker_raw.get(ticker)
            if days_to is not None and days_to <= earnings_days:
                excluded_for_earnings.append({
                    "ticker": ticker,
                    "earningsDate": (
                        datetime.now(timezone.utc) + timedelta(days=days_to)
                    ).strftime("%Y-%m-%d"),
                    "contractCount": 1,
                })
            else:
                kept_items.append(item)
        items = kept_items

    # ------------------------------------------------------------------
    # 5. Enrich all remaining evaluations in parallel
    # ------------------------------------------------------------------
    async def _enrich(item: dict[str, Any]) -> dict[str, Any]:
        evaluation_id = item.get("evaluation_id", "")

        pillar_scores, gate_results, thesis, feature_values = await asyncio.gather(
            _limited(PillarScoreTable.list_by_evaluation(evaluation_id)),
            _limited(GateResultTable.list_by_evaluation(evaluation_id)),
            _limited(TradeThesisTable.get_by_evaluation_id(evaluation_id)),
            _limited(FeatureValueTable.list_by_evaluation(evaluation_id)),
        )

        # Merge volatility features into item for rule matching
        for fv in feature_values:
            if fv.feature_name in (
                "iv_percentile", "iv_rv_ratio", "theta_adjusted_edge",
                "days_to_earnings", "atr14_pct", "rs_20d",
                "feasibility_ratio",
            ):
                if fv.value is not None:
                    item[fv.feature_name] = fv.value

        pillar_dict = {
            _enum_str(ps.pillar_id): ps.score
            for ps in pillar_scores
        }

        gate_list = [
            {
                "gate_id": gr.gate_id,
                "enabled": gr.enabled,
                "passed": gr.passed,
                "measured_value": gr.measured_value,
                "threshold_value": gr.threshold_value,
                "operator": _enum_str(gr.operator),
            }
            for gr in gate_results
        ]

        scanner_types = _scanner_types_for(item)

        headline = None
        if thesis and thesis.setup_summary:
            headline = thesis.setup_summary[:120]

        return {
            **item,
            "pillarScores": pillar_dict,
            "gateResults": gate_list,
            "gateMargin": calculate_gate_margin(gate_list),
            "scannerSource": scanner_types,
            "thetaAdjustedEV": calculate_theta_adjusted_ev(
                delta=item.get("delta", 0),
                theta=item.get("theta", 0),
                mid=item.get("mid", 0),
                iv=item.get("iv", 0),
                underlying_price=item.get("underlying_price", 0),
                dte=item.get("dte", 30),
            ),
            "urgency": determine_urgency(scanner_types),
            "headline": headline,
        }

    enhanced_items = list(await asyncio.gather(*[_enrich(item) for item in items]))

    # ------------------------------------------------------------------
    # 6. Scanner convergence + sort + limit
    # ------------------------------------------------------------------
    ticker_to_evals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in enhanced_items:
        ticker_to_evals[e.get("underlying_ticker", "")].append(e)

    for ticker, evals in ticker_to_evals.items():
        if len(evals) > 1:
            all_scanners = set()
            for e in evals:
                all_scanners.update(e.get("scannerSource", []))
            convergence_count = len(all_scanners)
            for e in evals:
                e["scannerConvergence"] = convergence_count
        else:
            for e in evals:
                e["scannerConvergence"] = len(e.get("scannerSource", []))

    enhanced_items.sort(
        key=lambda x: (
            x.get("decision", {}).get("final_score", 0)
            if isinstance(x.get("decision"), dict) else 0
        ),
        reverse=True,
    )
    enhanced_items = enhanced_items[:limit]

    # ------------------------------------------------------------------
    # 7. Match setup rules (production mode only)
    # ------------------------------------------------------------------
    # Enrich with sector for sector-aware rule matching
    try:
        from app.db.tables import SP500TickerTable
        sector_map = await SP500TickerTable.get_sector_map()
        for item in enhanced_items:
            t = item.get("underlying_ticker", "")
            if t in sector_map:
                item["sector"] = sector_map[t]
    except Exception:
        pass

    try:
        from app.paper_trading.pattern_discovery import list_setup_rules  # noqa: I001
        from app.paper_trading.rule_matcher import match_rules, format_matched_rules

        all_rules = await list_setup_rules()
        if all_rules:
            for item in enhanced_items:
                decision = item.get("decision", {})
                if not isinstance(decision, dict):
                    decision = {}
                scanner_source = item.get("scannerSource", [])
                matched = match_rules(
                    all_rules, item, decision, scanner_source,
                    mode_filter="production",
                )
                if matched:
                    item["matchedRules"] = format_matched_rules(matched)
    except Exception as e:
        logger.warning(f"Setup rule matching failed: {e}")

    # Consolidate earnings exclusions by ticker
    earnings_by_ticker: dict[str, dict[str, Any]] = {}
    for exc in excluded_for_earnings:
        ticker = exc["ticker"]
        if ticker in earnings_by_ticker:
            earnings_by_ticker[ticker]["contractCount"] += 1
        else:
            earnings_by_ticker[ticker] = exc

    return {
        "evaluations": enhanced_items,
        "excludedForEarnings": list(earnings_by_ticker.values()),
        "meta": {
            "total": len(enhanced_items),
            "excludedCount": len(excluded_for_earnings),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "maxAgeTradingDays": max_age_trading_days,
            "cutoffTimestamp": cutoff_iso,
        },
    }


@router.get("/watch/insights")
async def get_watch_insights(
    since_days: int = 7,
) -> dict[str, Any]:
    """Get WATCH intelligence insights for pattern detection.

    Per Section 11 of OSS_Opportunities_Page_Specification:
    - Gate Pressure: identify gates causing most WATCH failures
    - Recurring Near-Miss: contracts evaluated multiple times
    - WATCH to APPROVE Conversion tracking

    Args:
        since_days: Number of days to analyze

    Returns:
        List of WatchInsight objects
    """
    # Fetch WATCH evaluations
    watch_items = await EvaluationTable.list_by_verdict("WATCH", limit=500)
    approve_items = await EvaluationTable.list_by_verdict("APPROVE", limit=200)

    insights: list[dict[str, Any]] = []

    # 1. Gate Pressure Analysis
    gate_failure_counts: dict[str, int] = defaultdict(int)
    total_watch = len(watch_items)

    for item in watch_items:
        evaluation_id = item.get("evaluation_id", "")
        gate_results = await GateResultTable.list_by_evaluation(evaluation_id)

        for gr in gate_results:
            if gr.enabled and not gr.passed:
                gate_failure_counts[gr.gate_id] += 1

    # Find top failing gates
    if gate_failure_counts and total_watch > 0:
        sorted_gates = sorted(gate_failure_counts.items(), key=lambda x: x[1], reverse=True)
        top_gate, top_count = sorted_gates[0]
        percentage = round((top_count / total_watch) * 100, 1)

        if percentage >= 10:  # Only report if significant
            insights.append({
                "type": "gate_pressure",
                "headline": (
                    f"{top_count} contracts failed the "
                    f"{top_gate.replace('_', ' ')} gate today "
                    f"({percentage}% of all WATCH)"
                ),
                "gateName": top_gate,
                "failCount": top_count,
                "percentage": percentage,
                "highPotentialCount": min(top_count, 5),
                "actionLink": {
                    "label": "Review threshold in Policy",
                    "url": "/config",
                },
            })

    # 2. Recurring Near-Miss Detection
    # Group by contract identifier (ticker + strike + expiration)
    contract_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in watch_items:
        ticker = item.get("underlying_ticker", "")
        strike = item.get("strike", 0)
        expiration = item.get("expiration_date", "")
        contract_key = f"{ticker}_{strike}_{expiration}"
        contract_occurrences[contract_key].append(item)

    # Find contracts evaluated multiple times
    for contract_key, occurrences in contract_occurrences.items():
        if len(occurrences) >= 2:
            parts = contract_key.split("_")
            ticker = parts[0] if parts else ""

            # Get the most recent evaluation's failing gate
            latest = max(occurrences, key=lambda x: x.get("evaluated_at", ""))
            evaluation_id = latest.get("evaluation_id", "")
            gate_results = await GateResultTable.list_by_evaluation(evaluation_id)

            failing_gate = None
            for gr in gate_results:
                if gr.enabled and not gr.passed:
                    failing_gate = gr.gate_id
                    break

            insights.append({
                "type": "recurring_near_miss",
                "headline": (
                    f"{ticker} {latest.get('strike', '')} "
                    f"{latest.get('option_type', '')} "
                    f"{latest.get('expiration_date', '')} "
                    f"has been evaluated {len(occurrences)} times this week"
                ),
                "subInsight": (
                    f"Currently failing: "
                    f"{failing_gate.replace('_', ' ') if failing_gate else 'Unknown'}"
                ),
                "contractId": contract_key,
                "ticker": ticker,
                "strike": latest.get("strike", 0),
                "expiration": latest.get("expiration_date", ""),
                "occurrences": len(occurrences),
                "failingGate": failing_gate,
            })

    # 3. WATCH to APPROVE Conversion Tracking
    # Find contracts that were WATCH and later became APPROVE
    watch_contracts = set()
    for item in watch_items:
        ticker = item.get("underlying_ticker", "")
        strike = item.get("strike", 0)
        expiration = item.get("expiration_date", "")
        watch_contracts.add(f"{ticker}_{strike}_{expiration}")

    conversions: list[dict[str, Any]] = []
    for item in approve_items:
        ticker = item.get("underlying_ticker", "")
        strike = item.get("strike", 0)
        expiration = item.get("expiration_date", "")
        contract_key = f"{ticker}_{strike}_{expiration}"

        if contract_key in watch_contracts:
            evaluation_id = item.get("evaluation_id", "")
            gate_results = await GateResultTable.list_by_evaluation(evaluation_id)

            # Find which gate now passed
            gate_passed = None
            for gr in gate_results:
                if gr.enabled and gr.passed:
                    gate_passed = gr.gate_id
                    break

            conversions.append({
                "contractId": contract_key,
                "ticker": ticker,
                "strike": strike,
                "expiration": expiration,
                "gatePassed": gate_passed,
            })

    if conversions:
        insights.append({
            "type": "watch_to_approve",
            "headline": f"{len(conversions)} contracts converted from WATCH to APPROVE",
            "contracts": [
                f"{c['ticker']} {c['strike']} {c['expiration']}"
                for c in conversions[:5]
            ],
            "conversions": conversions[:10],
        })

    return {
        "insights": insights,
        "watchCount": total_watch,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
