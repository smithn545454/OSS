"""Shared helper for building a full evaluation snapshot.

Used by both the evaluation detail endpoint and the real trade tracking endpoint
to build a complete, denormalized view of an evaluation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.db.tables import (
    EvaluationTable,
    FeatureValueTable,
    GateResultTable,
    OpportunityTable,
    PillarScoreTable,
    TradeThesisTable,
)

logger = logging.getLogger(__name__)


def _enum_str(val: Any) -> str:
    """Convert an enum-like value to string."""
    return str(val.value) if hasattr(val, "value") else str(val)


def _opt_enum_str(val: Any) -> str | None:
    """Convert an optional enum-like value to string."""
    if val is None:
        return None
    return str(val.value) if hasattr(val, "value") else str(val)


async def _noop_list() -> list:
    return []


async def _fetch_stock_technicals(ticker: str) -> dict[str, Any] | None:
    """Fetch stock technicals for the underlying ticker.

    Returns the full StockTechnicals model as a dict, or None on failure.
    Runs in parallel with other snapshot fetches — no added latency.
    """
    from datetime import datetime, timedelta, timezone

    from app.services.polygon import PolygonClient
    from app.services.technicals import compute_stock_technicals

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from_date = (datetime.now(timezone.utc) - timedelta(days=370)).strftime("%Y-%m-%d")

    try:
        async with PolygonClient() as client:
            details, bars = await asyncio.gather(
                client.get_ticker_details(ticker),
                client.get_daily_bars_parsed(ticker, from_date, today),
            )
        result = compute_stock_technicals(ticker, bars, details)
        return result.model_dump()
    except Exception as e:
        logger.warning(f"Failed to fetch stock technicals for {ticker}: {e}")
        return None


async def build_evaluation_snapshot_data(
    ticker: str,
    evaluation_id: str,
    evaluation: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Fetch and build complete evaluation snapshot data.

    Fetches all related data (pillar scores, gate results, features,
    thesis, scanner triggers, matched rules) in parallel and returns
    a structured dict suitable for creating an EvaluationSnapshot.

    Args:
        ticker: The underlying ticker
        evaluation_id: The evaluation ID
        evaluation: Pre-fetched evaluation dict. If None, will be fetched.

    Returns:
        Dict with all snapshot data, or None if evaluation not found.
    """
    if evaluation is None:
        evaluation = await EvaluationTable.get_by_id(ticker, evaluation_id)
        if not evaluation:
            return None

    opportunity_id = evaluation.get("opportunity_id")

    # Fetch all related data in parallel
    (
        pillar_scores,
        gate_results,
        opportunities,
        features,
        thesis,
        underlying_technicals,
    ) = await asyncio.gather(
        PillarScoreTable.list_by_evaluation(evaluation_id),
        GateResultTable.list_by_evaluation(evaluation_id),
        OpportunityTable.list_by_ticker(ticker, limit=20) if opportunity_id else _noop_list(),
        FeatureValueTable.list_by_evaluation(evaluation_id),
        TradeThesisTable.get_by_evaluation_id(evaluation_id),
        _fetch_stock_technicals(ticker),
    )

    # Process pillar scores
    pillar_scores_list = [
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
    gate_results_list = [
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
        has_api = hasattr(thesis.exit_plan, "to_api_dict")
        exit_plan = (
            thesis.exit_plan.to_api_dict()
            if has_api
            else {
                "profit_target": thesis.exit_plan.profit_target,
                "stop_loss": thesis.exit_plan.stop_loss,
                "time_exit": thesis.exit_plan.time_exit,
                "take_profits": [],
                "stop_loss_level": None,
                "time_exit_level": None,
            }
        )
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

    # Enrich with sector for sector-aware rule matching
    try:
        from app.db.tables import SP500TickerTable
        sector_map = await SP500TickerTable.get_sector_map()
        eval_ticker = evaluation.get("underlying_ticker", "")
        if eval_ticker in sector_map:
            evaluation["sector"] = sector_map[eval_ticker]
    except Exception:
        pass

    # Match setup rules
    matched_rules_list: list[dict[str, Any]] = []
    try:
        from app.paper_trading.pattern_discovery import list_setup_rules
        from app.paper_trading.rule_matcher import format_matched_rules, match_rules

        all_rules = await list_setup_rules()
        if all_rules:
            eval_decision = evaluation.get("decision", {})
            if not isinstance(eval_decision, dict):
                eval_decision = {}
            if not eval_decision.get("final_score"):
                for ps in pillar_scores_list:
                    pid = ps.get("pillar_id", "")
                    if pid == "PREMIUM_LEVERAGE":
                        eval_decision.setdefault(
                            "premium_leverage_score", ps.get("score")
                        )
                    elif pid == "UNDERLYING_BEHAVIOR":
                        eval_decision.setdefault(
                            "underlying_behavior_score", ps.get("score")
                        )
                    elif pid == "SETUP_QUALITY":
                        eval_decision.setdefault(
                            "setup_quality_score", ps.get("score")
                        )

            eval_scanners = [
                st.get("scanner_type", "") for st in scanner_triggers if st.get("scanner_type")
            ]
            matched = match_rules(all_rules, evaluation, eval_decision, eval_scanners)
            matched_rules_list = format_matched_rules(matched, include_criteria=True)
    except Exception as e:
        logger.warning(f"Setup rule matching failed: {e}")

    # Compute theta-adjusted EV
    from app.api.routes.evaluations import calculate_theta_adjusted_ev

    rv20_feat = features_dict.get("rv20")
    rv20_val = rv20_feat.get("value") if rv20_feat else None

    theta_adjusted_ev = calculate_theta_adjusted_ev(
        theta=evaluation.get("theta", 0),
        iv=evaluation.get("iv", 0),
        rv20=rv20_val,
    )

    return {
        "evaluation": evaluation,
        "pillar_scores": pillar_scores_list,
        "gate_results": gate_results_list,
        "scanner_triggers": scanner_triggers,
        "features": features_dict,
        "thesis": thesis_dict,
        "matched_rules": matched_rules_list,
        "theta_adjusted_ev": theta_adjusted_ev,
        "underlying_technicals": underlying_technicals,
    }
