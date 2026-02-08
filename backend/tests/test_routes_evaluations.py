"""Tests for the Evaluations API routes.

Covers listing, detail, and verdict filtering endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

_ROUTE = "app.api.routes.evaluations"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestListEvaluations:
    """Test GET /api/evaluations."""

    @pytest.mark.asyncio
    async def test_list_returns_200(self, client):
        with patch(f"{_ROUTE}.EvaluationTable") as mock:
            mock.list_by_verdict = AsyncMock(return_value=[])
            response = await client.get("/api/evaluations")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_with_verdict_filter(self, client):
        with patch(f"{_ROUTE}.EvaluationTable") as mock:
            mock.list_by_verdict = AsyncMock(return_value=[])
            response = await client.get("/api/evaluations?verdict=APPROVE")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_with_limit(self, client):
        with patch(f"{_ROUTE}.EvaluationTable") as mock:
            mock.list_by_verdict = AsyncMock(return_value=[])
            response = await client.get("/api/evaluations?limit=10")
        assert response.status_code == 200


class TestFilteredEvaluations:
    """Test GET /api/evaluations/filtered."""

    @pytest.mark.asyncio
    async def test_filtered_returns_200(self, client):
        with patch(f"{_ROUTE}.EvaluationTable") as mock:
            mock.list_by_verdict = AsyncMock(return_value=[])
            response = await client.get("/api/evaluations/filtered")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_filtered_by_verdict_and_dte(self, client):
        with patch(f"{_ROUTE}.EvaluationTable") as mock:
            mock.list_by_verdict = AsyncMock(return_value=[])
            response = await client.get(
                "/api/evaluations/filtered?verdict=APPROVE&dte_bucket=C"
            )
        assert response.status_code == 200


class TestApproveEvaluations:
    """Test GET /api/evaluations/approve."""

    @pytest.mark.asyncio
    async def test_approve_returns_200(self, client):
        with patch(f"{_ROUTE}.EvaluationTable") as mock_eval:
            mock_eval.list_by_verdict = AsyncMock(return_value=[])
            response = await client.get("/api/evaluations/approve")
        assert response.status_code == 200
