"""LLM API endpoints for thesis management.

Per Section 21 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from app.core.schemas import ThesisConfig, ThesisStatus
from app.db.tables import LLMUsageTable, TradeThesisTable
from app.llm.rate_limiter import RateLimiter

router = APIRouter()


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
    
    return {
        "thesis_id": thesis.thesis_id,
        "evaluation_id": thesis.evaluation_id,
        "status": thesis.status.value if hasattr(thesis.status, 'value') else str(thesis.status),
        "setup_summary": thesis.setup_summary,
        "thesis": thesis.thesis,
        "supporting_evidence": thesis.supporting_evidence,
        "risks": thesis.risks,
        "invalidation_conditions": thesis.invalidation_conditions,
        "exit_plan": {
            "profit_target": thesis.exit_plan.profit_target,
            "stop_loss": thesis.exit_plan.stop_loss,
            "time_exit": thesis.exit_plan.time_exit,
        },
        "llm_provider": thesis.llm_provider.value if hasattr(thesis.llm_provider, 'value') else str(thesis.llm_provider),
        "model_used": thesis.model_used,
        "tokens_used": thesis.tokens_used,
        "generated_at": thesis.generated_at,
        "error_message": thesis.error_message,
    }
