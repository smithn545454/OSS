"""Observability endpoints.

Per Section 18.3 of OSS_Complete_Requirements.md - Representative Trace Sampling.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.observability import get_representative_traces

router = APIRouter()


@router.get("/traces")
async def get_traces(
    gate_failure_limit: int = 10,
    reject_sample_limit: int = 10,
    approve_sample_limit: int = 10,
) -> dict[str, Any]:
    """Get representative trace samples for observability.
    
    Per Section 18.3 of requirements, returns:
    - Common Gate Failures: Most frequent rejection points (default 10)
    - Highest REJECT Scores: Near-miss rejects (default 10)
    - Lowest APPROVE Scores: Borderline approves (default 10)
    - TIER_1 Approvals: All high-confidence trades
    
    Args:
        gate_failure_limit: Max common gate failures to return
        reject_sample_limit: Max highest-scoring rejects to return
        approve_sample_limit: Max lowest-scoring approves to return
        
    Returns:
        Representative traces with samples and summary statistics
    """
    traces = await get_representative_traces(
        gate_failure_limit=gate_failure_limit,
        reject_sample_limit=reject_sample_limit,
        approve_sample_limit=approve_sample_limit,
    )
    
    return traces.to_dict()
