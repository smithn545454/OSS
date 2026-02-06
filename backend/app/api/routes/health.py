"""Health check endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint."""
    settings = get_settings()
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": settings.app_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/ready")
async def readiness_check() -> dict[str, Any]:
    """Readiness check - verifies all dependencies are available."""
    # TODO: Add DynamoDB connectivity check
    return {
        "status": "ready",
        "checks": {
            "dynamodb": "ok",  # Placeholder
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
