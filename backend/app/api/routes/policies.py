"""Policy management endpoints."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from app.core.policy import PolicyService
from app.core.schemas import PolicyConfig, PolicyCreate

router = APIRouter()
policy_service = PolicyService()


@router.get("")
async def list_policies(limit: int = 20) -> dict[str, Any]:
    """List all policy versions."""
    policies = await policy_service.list_versions(limit=limit)
    return {
        "policies": [p.model_dump() for p in policies],
        "count": len(policies),
    }


@router.get("/active")
async def get_active_policy() -> dict[str, Any]:
    """Get the currently active policy."""
    policy = await policy_service.get_active()
    if not policy:
        raise HTTPException(status_code=404, detail="No active policy found")
    return policy.model_dump()


@router.get("/{version}")
async def get_policy(version: str) -> dict[str, Any]:
    """Get a specific policy version."""
    policy = await policy_service.get_version(version)
    if not policy:
        raise HTTPException(status_code=404, detail=f"Policy version {version} not found")
    return policy.model_dump()


@router.post("")
async def create_policy(policy_create: PolicyCreate) -> dict[str, Any]:
    """Create a new policy version."""
    policy = await policy_service.create_version(
        config=policy_create.config,
        user=policy_create.created_by,
    )
    return {
        "message": "Policy created successfully",
        "policy": policy.model_dump(),
    }


@router.post("/{version}/activate")
async def activate_policy(version: str) -> dict[str, Any]:
    """Activate a specific policy version."""
    policy = await policy_service.activate_version(version)
    if not policy:
        raise HTTPException(status_code=404, detail=f"Policy version {version} not found")
    return {
        "message": f"Policy {version} activated",
        "policy": policy.model_dump(),
    }


@router.get("/diff/{v1}/{v2}")
async def diff_policies(v1: str, v2: str) -> dict[str, Any]:
    """Compare two policy versions."""
    diff = await policy_service.diff_versions(v1, v2)
    if not diff:
        raise HTTPException(status_code=404, detail="One or both policy versions not found")
    return diff.model_dump()


# ============================================================================
# Opportunities Page Configuration (Section 17)
# ============================================================================

# In-memory config (would be in DynamoDB in production)
_opportunities_config: dict[str, Any] = {
    "scoring": {
        "weights": {
            "thetaAdjustedEv": 0.40,
            "compositePillar": 0.25,
            "gateMargin": 0.15,
            "scannerConvergence": 0.10,
            "timeSensitivity": 0.10,
        },
        "evBenchmark": 500,
        "holdingPeriodMomentum": 5,
        "holdingPeriodStructural": 10,
    },
    "convictionQueue": {
        "threshold": 75,
    },
    "slackAlerts": {
        "enabled": False,
        "scoreThreshold": 85,
        "requireUrgencyOrConvergence": True,
        "cooldownMinutes": 30,
        "dailyCap": 10,
        "quietHoursStart": "22:00",
        "quietHoursEnd": "08:00",
    },
    "earningsFilter": {
        "exclusionDays": 7,
        "showNotice": True,
    },
}


@router.get("/config/opportunities")
async def get_opportunities_config() -> dict[str, Any]:
    """Get Opportunities page configuration.
    
    Per Section 17 of OSS_Opportunities_Page_Specification.
    
    Returns:
        Current opportunities configuration
    """
    return _opportunities_config


@router.put("/config/opportunities")
async def update_opportunities_config(config: dict[str, Any]) -> dict[str, Any]:
    """Update Opportunities page configuration.
    
    Per Section 17 of OSS_Opportunities_Page_Specification.
    
    Args:
        config: Partial configuration to update
        
    Returns:
        Updated configuration
    """
    global _opportunities_config
    
    # Deep merge the config
    def deep_merge(base: dict, update: dict) -> dict:
        result = base.copy()
        for key, value in update.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    _opportunities_config = deep_merge(_opportunities_config, config)
    
    # Update Slack service config if relevant
    if "slackAlerts" in config:
        from app.services.slack import get_slack_service
        slack_config = config["slackAlerts"]
        slack_service = get_slack_service()
        slack_service.configure(
            score_threshold=slack_config.get("scoreThreshold"),
            require_urgency_or_convergence=slack_config.get("requireUrgencyOrConvergence"),
            cooldown_minutes=slack_config.get("cooldownMinutes"),
            daily_cap=slack_config.get("dailyCap"),
            quiet_hours_start=slack_config.get("quietHoursStart"),
            quiet_hours_end=slack_config.get("quietHoursEnd"),
        )
    
    return _opportunities_config
