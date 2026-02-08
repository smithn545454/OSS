"""Tests for the Observability API routes.

Covers trace listing and pipeline monitor data endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestGetTraces:
    """Test GET /api/observability/traces."""

    @pytest.mark.asyncio
    async def test_traces_returns_200(self, client):
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "gate_failures": [],
            "reject_samples": [],
            "approve_samples": [],
        }
        with patch("app.api.routes.observability.get_representative_traces") as mock_traces:
            mock_traces.return_value = mock_result
            response = await client.get("/api/observability/traces")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_traces_with_custom_limits(self, client):
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "gate_failures": [],
            "reject_samples": [],
            "approve_samples": [],
        }
        with patch("app.api.routes.observability.get_representative_traces") as mock_traces:
            mock_traces.return_value = mock_result
            response = await client.get(
                "/api/observability/traces?gate_failure_limit=5"
            )
        assert response.status_code == 200
