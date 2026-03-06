"""LLM API endpoints for thesis management.

Per Section 21 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.schemas import (
    Decision,
    Evaluation,
    ThesisConfig,
)
from app.db.tables import (
    EvaluationTable,
    FeatureValueTable,
    LLMUsageTable,
    OpportunityTable,
    PaperPositionTable,
    PillarScoreTable,
    TradeThesisTable,
)
from app.llm.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter()
thesis_router = APIRouter()


@router.get("/usage")
async def get_llm_usage() -> dict[str, Any]:
    """Get current LLM usage statistics.
    
    Returns:
        Usage stats including calls made, remaining, and daily limit.
    """
    config = ThesisConfig()
    rate_limiter = RateLimiter(max_daily_calls=config.max_daily_calls)
    stats = await rate_limiter.get_usage_stats()
    
    # Also get recent history from DynamoDB
    recent = await LLMUsageTable.list_recent(days=7)
    
    return {
        "today": stats,
        "recent": [
            {
                "date": u.date,
                "calls_made": u.calls_made,
                "tokens_used": u.tokens_used,
            }
            for u in recent
        ],
    }


@router.get("/config")
async def get_llm_config() -> dict[str, Any]:
    """Get current LLM configuration.
    
    Returns:
        LLM configuration including limits and provider settings.
    """
    config = ThesisConfig()
    return {
        "enabled": config.enabled,
        "max_daily_calls": config.max_daily_calls,
        "output_token_limit": config.output_token_limit,
        "preferred_provider": config.preferred_provider.value if hasattr(config.preferred_provider, 'value') else str(config.preferred_provider),
        "fallback_enabled": config.fallback_enabled,
    }


@router.get("/theses")
async def list_theses(
    date: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List generated trade theses.
    
    Args:
        date: Filter by date (YYYY-MM-DD). Defaults to today.
        limit: Maximum records to return.
        
    Returns:
        List of trade theses.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    theses = await TradeThesisTable.list_by_date(date, limit=limit)
    
    return {
        "date": date,
        "theses": [
            {
                "thesis_id": t.thesis_id,
                "evaluation_id": t.evaluation_id,
                "status": t.status.value if hasattr(t.status, 'value') else str(t.status),
                "setup_summary": t.setup_summary,
                "llm_provider": t.llm_provider.value if hasattr(t.llm_provider, 'value') else str(t.llm_provider),
                "model_used": t.model_used,
                "tokens_used": t.tokens_used,
                "generated_at": t.generated_at,
                "error_message": t.error_message,
            }
            for t in theses
        ],
        "count": len(theses),
    }


@router.get("/theses/{evaluation_id}")
async def get_thesis(evaluation_id: str) -> dict[str, Any]:
    """Get trade thesis for a specific evaluation.
    
    Args:
        evaluation_id: The evaluation ID.
        
    Returns:
        Complete trade thesis including all sections.
    """
    thesis = await TradeThesisTable.get_by_evaluation_id(evaluation_id)
    
    if not thesis:
        raise HTTPException(
            status_code=404,
            detail=f"No thesis found for evaluation: {evaluation_id}",
        )
    
    return _thesis_to_dict(thesis)


# ============================================================================
# On-Demand Thesis Generation (thesis_router, mounted at /api/thesis)
# ============================================================================


class GenerateThesisRequest(BaseModel):
    """Request body for on-demand thesis generation."""

    evaluationId: str
    ticker: Optional[str] = None


def _thesis_to_dict(thesis: Any) -> dict[str, Any]:
    """Convert a TradeThesis to a serializable dict."""
    exit_plan = thesis.exit_plan.to_api_dict() if hasattr(thesis.exit_plan, 'to_api_dict') else {
        "profit_target": thesis.exit_plan.profit_target,
        "stop_loss": thesis.exit_plan.stop_loss,
        "time_exit": thesis.exit_plan.time_exit,
        "take_profits": [],
        "stop_loss_level": None,
        "time_exit_level": None,
    }
    return {
        "thesis_id": thesis.thesis_id,
        "evaluation_id": thesis.evaluation_id,
        "status": str(thesis.status.value) if hasattr(thesis.status, "value") else str(thesis.status),
        "setup_summary": thesis.setup_summary,
        "thesis": thesis.thesis,
        "supporting_evidence": thesis.supporting_evidence,
        "risks": thesis.risks,
        "invalidation_conditions": thesis.invalidation_conditions,
        "exit_plan": exit_plan,
        "llm_provider": str(thesis.llm_provider.value) if hasattr(thesis.llm_provider, "value") else str(thesis.llm_provider),
        "model_used": thesis.model_used,
        "tokens_used": thesis.tokens_used,
        "generated_at": thesis.generated_at,
        "error_message": thesis.error_message,
    }


@thesis_router.post("/generate")
async def generate_thesis(body: GenerateThesisRequest) -> dict[str, Any]:
    """Generate an AI trade thesis on-demand for an approved evaluation.

    Fetches all prerequisite data (evaluation, pillar scores, gate results,
    features, scanner triggers) and calls the ThesisGenerator.
    """
    from app.llm.generator import ThesisGenerator

    evaluation_id = body.evaluationId

    # Check if thesis already exists
    existing = await TradeThesisTable.get_by_evaluation_id(evaluation_id)
    if existing and str(getattr(existing.status, "value", existing.status)) == "COMPLETED":
        return _thesis_to_dict(existing)

    # Find the evaluation — use direct lookup when ticker is provided
    eval_dict = None
    if body.ticker:
        eval_dict = await EvaluationTable.get_by_id(body.ticker, evaluation_id)

    # Fallback: scan by verdict (backward compat for callers without ticker)
    if not eval_dict:
        eval_items = await EvaluationTable.list_by_verdict("APPROVE", limit=200)
        for item in eval_items:
            if item.get("evaluation_id") == evaluation_id:
                eval_dict = item
                break

    if not eval_dict:
        watch_items = await EvaluationTable.list_by_verdict("WATCH", limit=200)
        for item in watch_items:
            if item.get("evaluation_id") == evaluation_id:
                eval_dict = item
                break

    if not eval_dict:
        raise HTTPException(
            status_code=404,
            detail=f"Evaluation not found: {evaluation_id}",
        )

    # Extract decision data
    decision_data = eval_dict.pop("decision", None)
    if not decision_data:
        raise HTTPException(
            status_code=400,
            detail="Evaluation has no decision data",
        )

    # Check verdict is APPROVE
    verdict = decision_data.get("verdict", "")
    if isinstance(verdict, str) and verdict != "APPROVE":
        raise HTTPException(
            status_code=400,
            detail=f"Thesis generation requires APPROVE verdict, got {verdict}",
        )

    # Construct models
    try:
        evaluation = Evaluation(**eval_dict)
    except Exception as e:
        logger.error(f"Failed to construct Evaluation model: {e}")
        raise HTTPException(status_code=500, detail=f"Invalid evaluation data: {e}")

    try:
        decision = Decision(**decision_data)
    except Exception as e:
        logger.error(f"Failed to construct Decision model: {e}")
        raise HTTPException(status_code=500, detail=f"Invalid decision data: {e}")

    # Fetch prerequisites in parallel
    ticker = evaluation.underlying_ticker
    pillar_scores, features, opportunities = await asyncio.gather(
        PillarScoreTable.list_by_evaluation(evaluation_id),
        FeatureValueTable.list_by_evaluation(evaluation_id),
        OpportunityTable.list_by_ticker(ticker, limit=20),
    )

    # Extract scanner triggers from the matching opportunity
    scanner_triggers = []
    opportunity_id = evaluation.opportunity_id
    for opp in opportunities:
        if opp.opportunity_id == opportunity_id:
            scanner_triggers = list(opp.scanner_triggers)
            break

    # Build features dict
    features_dict = {f.feature_name: f.value for f in features}

    # Generate thesis
    generator = ThesisGenerator()
    thesis = await generator.generate(
        evaluation=evaluation,
        decision=decision,
        pillar_scores=pillar_scores,
        scanner_triggers=scanner_triggers,
        features=features_dict,
    )

    # Persist the thesis
    await TradeThesisTable.put(thesis)
    logger.info(f"Generated thesis for {evaluation_id}: status={thesis.status}")

    # Apply structured exit levels to linked paper position
    thesis_status = str(thesis.status.value) if hasattr(thesis.status, "value") else str(thesis.status)
    if thesis_status == "COMPLETED" and thesis.exit_plan.take_profits:
        try:
            position = await PaperPositionTable.get_by_evaluation_id(evaluation_id)
            if position:
                pos_status = str(getattr(position.status, "value", position.status))
                if pos_status == "OPEN":
                    updates: dict[str, Any] = {}
                    tp1 = thesis.exit_plan.take_profits[0]
                    updates["thesis_tp1_pct"] = tp1.option_pnl_pct
                    if thesis.exit_plan.stop_loss_level:
                        updates["thesis_sl_pct"] = abs(
                            thesis.exit_plan.stop_loss_level.option_pnl_pct
                        )
                    if thesis.exit_plan.time_exit_level:
                        updates["thesis_time_exit_dte"] = (
                            thesis.exit_plan.time_exit_level.dte_threshold
                        )
                    await PaperPositionTable.update(position, updates)
                    logger.info(
                        f"Applied thesis exit levels to position {position.position_id}"
                    )
        except Exception as e:
            logger.warning(f"Failed to apply thesis exit levels to position: {e}")

    return _thesis_to_dict(thesis)
