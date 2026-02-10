"""API route tests for policy endpoints.

Tests HTTP behavior (status codes, response shapes, error handling)
by mocking the DB layer at the table level.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.schemas import Policy, PolicyConfig


@pytest.fixture
async def client():
    """Create an async test client for the FastAPI app."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestListPolicies:
    """Tests for GET /api/policies."""

    @pytest.mark.asyncio
    async def test_list_policies_returns_200(self, client):
        config = PolicyConfig()
        mock_policy = Policy(
            version="v2.0.0",
            policy_hash=Policy.compute_hash(config),
            config=config,
            created_by="test",
            is_active=True,
        )
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.list_versions = AsyncMock(return_value=[mock_policy])
            resp = await client.get("/api/policies")

        assert resp.status_code == 200
        data = resp.json()
        assert "policies" in data
        assert "count" in data
        assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_list_policies_empty(self, client):
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.list_versions = AsyncMock(return_value=[])
            resp = await client.get("/api/policies")

        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestGetActivePolicy:
    """Tests for GET /api/policies/active."""

    @pytest.mark.asyncio
    async def test_get_active_returns_200(self, client):
        config = PolicyConfig()
        mock_policy = Policy(
            version="v2.0.0",
            policy_hash=Policy.compute_hash(config),
            config=config,
            created_by="test",
            is_active=True,
        )
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.get_active = AsyncMock(return_value=mock_policy)
            resp = await client.get("/api/policies/active")

        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "v2.0.0"

    @pytest.mark.asyncio
    async def test_get_active_returns_404_when_none(self, client):
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.get_active = AsyncMock(return_value=None)
            resp = await client.get("/api/policies/active")

        assert resp.status_code == 404


class TestGetPolicyVersion:
    """Tests for GET /api/policies/{version}."""

    @pytest.mark.asyncio
    async def test_get_version_returns_200(self, client):
        config = PolicyConfig()
        mock_policy = Policy(
            version="v1.0.0",
            policy_hash=Policy.compute_hash(config),
            config=config,
            created_by="test",
            is_active=False,
        )
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.get_version = AsyncMock(return_value=mock_policy)
            resp = await client.get("/api/policies/v1.0.0")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_version_returns_404(self, client):
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.get_version = AsyncMock(return_value=None)
            resp = await client.get("/api/policies/v999.0.0")

        assert resp.status_code == 404


class TestCreatePolicy:
    """Tests for POST /api/policies."""

    @pytest.mark.asyncio
    async def test_create_policy_returns_200(self, client):
        config = PolicyConfig()
        mock_policy = Policy(
            version="v3.0.0",
            policy_hash=Policy.compute_hash(config),
            config=config,
            created_by="admin",
            is_active=False,
        )
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.create_version = AsyncMock(return_value=mock_policy)
            resp = await client.post(
                "/api/policies",
                json={"config": config.model_dump(mode="json"), "created_by": "admin"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "policy" in data
        assert data["message"] == "Policy created successfully"


class TestActivatePolicy:
    """Tests for POST /api/policies/{version}/activate."""

    @pytest.mark.asyncio
    async def test_activate_returns_200(self, client):
        config = PolicyConfig()
        mock_policy = Policy(
            version="v2.0.0",
            policy_hash=Policy.compute_hash(config),
            config=config,
            created_by="test",
            is_active=True,
        )
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.activate_version = AsyncMock(return_value=mock_policy)
            resp = await client.post("/api/policies/v2.0.0/activate")

        assert resp.status_code == 200
        assert "activated" in resp.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_activate_nonexistent_returns_404(self, client):
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.activate_version = AsyncMock(return_value=None)
            resp = await client.post("/api/policies/v999.0.0/activate")

        assert resp.status_code == 404


class TestDiffPolicies:
    """Tests for GET /api/policies/diff/{v1}/{v2}."""

    @pytest.mark.asyncio
    async def test_diff_returns_404_when_not_found(self, client):
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.diff_versions = AsyncMock(return_value=None)
            resp = await client.get("/api/policies/diff/v1.0.0/v2.0.0")

        assert resp.status_code == 404
