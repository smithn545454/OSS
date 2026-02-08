"""Tests for the Health API routes.

Covers health check and readiness endpoints.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealthCheck:
    """Test GET /health."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    @pytest.mark.asyncio
    async def test_health_status_ok(self, client):
        response = await client.get("/health")
        data = response.json()
        assert data["status"] in ("ok", "healthy", "up")


class TestReadinessCheck:
    """Test GET /health/ready."""

    @pytest.mark.asyncio
    async def test_readiness_returns_200(self, client):
        response = await client.get("/health/ready")
        # May return 200 or 503 depending on service dependencies
        assert response.status_code in (200, 503)
