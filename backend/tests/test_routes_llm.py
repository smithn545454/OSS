"""Tests for the LLM API routes.

Covers thesis listing, usage tracking, and config endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

_ROUTE = "app.api.routes.llm"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestLLMUsage:
    """Test GET /api/llm/usage."""

    @pytest.mark.asyncio
    async def test_usage_returns_200(self, client):
        with patch(f"{_ROUTE}.LLMUsageTable") as mock:
            mock.list_recent = AsyncMock(return_value=[])
            mock.get_usage_summary = AsyncMock(return_value={})
            response = await client.get("/api/llm/usage")
        assert response.status_code == 200


class TestLLMConfig:
    """Test GET /api/llm/config."""

    @pytest.mark.asyncio
    async def test_config_returns_200(self, client):
        with patch(f"{_ROUTE}.RateLimiter") as mock_rl:
            mock_instance = MagicMock()
            mock_instance.get_status = AsyncMock(return_value={
                "model": "claude", "limit": 100, "remaining": 80
            })
            mock_rl.return_value = mock_instance
            response = await client.get("/api/llm/config")
        assert response.status_code == 200


class TestListTheses:
    """Test GET /api/llm/theses."""

    @pytest.mark.asyncio
    async def test_theses_returns_200(self, client):
        with patch(f"{_ROUTE}.TradeThesisTable") as mock:
            mock.list_recent = AsyncMock(return_value=[])
            mock.list_by_date = AsyncMock(return_value=[])
            response = await client.get("/api/llm/theses")
        assert response.status_code == 200


class TestGetThesis:
    """Test GET /api/llm/theses/{evaluation_id}."""

    @pytest.mark.asyncio
    async def test_get_thesis_not_found(self, client):
        with patch(f"{_ROUTE}.TradeThesisTable") as mock:
            mock.get_by_evaluation = AsyncMock(return_value=None)
            mock.get_by_evaluation_id = AsyncMock(return_value=None)
            mock.get = AsyncMock(return_value=None)
            response = await client.get("/api/llm/theses/nonexistent")
        assert response.status_code == 404
