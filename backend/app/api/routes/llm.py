"""LLM API endpoints for thesis management.

Per Section 21 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.schemas import ThesisConfig
from app.db.tables import (
    EvaluationTable,
    LLMUsageTable,
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
    """Dispatch async AI trade thesis generation for an approved evaluation.

    Validates the evaluation, persists a GENERATING stub to DynamoDB, and
    invokes the Lambda asynchronously to do the actual LLM work. Returns
    immediately so the frontend can poll for completion.
    """
    import json as json_mod
    import os
    from uuid import uuid4

    import boto3

    from app.core.schemas import (
        ExitPlanThesis,
        LLMProvider as LLMProviderEnum,
        ThesisStatus,
        TradeThesis,
    )

    evaluation_id = body.evaluationId

    # Check if thesis already exists
    existing = await TradeThesisTable.get_by_evaluation_id(evaluation_id)
    if existing:
        existing_status = str(getattr(existing.status, "value", existing.status))
        if existing_status == "COMPLETED":
            return _thesis_to_dict(existing)
        if existing_status == "GENERATING":
            # Already in progress — return current state (idempotent)
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

    ticker = eval_dict.get("underlying_ticker", body.ticker or "")

    # Determine thesis_id: reuse from existing FAILED thesis if retrying
    thesis_id = str(uuid4())
    if existing:
        thesis_id = existing.thesis_id

    # Create GENERATING stub in DynamoDB
    generating_thesis = TradeThesis(
        thesis_id=thesis_id,
        evaluation_id=evaluation_id,
        setup_summary="",
        thesis="",
        supporting_evidence=[],
        risks=[],
        invalidation_conditions=[],
        exit_plan=ExitPlanThesis(),
        llm_provider=LLMProviderEnum.ANTHROPIC,
        model_used="",
        tokens_used=0,
        status=ThesisStatus.GENERATING,
        error_message=None,
    )
    await TradeThesisTable.put(generating_thesis)

    # Fire-and-forget: invoke Lambda async to do the LLM work
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
    payload = {
        "source": "oss.scheduler",
        "action": "thesis_worker",
        "evaluation_id": evaluation_id,
        "thesis_id": thesis_id,
        "ticker": ticker,
    }

    try:
        lambda_client = boto3.client("lambda")
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json_mod.dumps(payload),
        )
        logger.info(f"Dispatched thesis worker for {evaluation_id}")
    except Exception as e:
        logger.error(f"Failed to dispatch thesis worker: {e}")
        failed_thesis = generating_thesis.model_copy(
            update={
                "status": ThesisStatus.FAILED,
                "error_message": f"Failed to dispatch generation: {e}",
            }
        )
        await TradeThesisTable.put(failed_thesis)
        return _thesis_to_dict(failed_thesis)

    return _thesis_to_dict(generating_thesis)
