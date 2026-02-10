"""Tests for api/routes/policies.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app():
    return create_app()


class TestListPolicies:

    @pytest.mark.asyncio
    async def test_list_empty(self, app):
        with patch("app.api.routes.policies.policy_service") as svc:
            svc.list_versions = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/policies")
            assert resp.status_code == 200
            assert resp.json()["count"] == 0


class TestGetActivePolicy:

    @pytest.mark.asyncio
    async def test_no_active(self, app):
        with patch("app.api.routes.policies.policy_service") as svc:
            svc.get_active = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/policies/active")
            assert resp.status_code == 404


class TestGetPolicy:

    @pytest.mark.asyncio
    async def test_not_found(self, app):
        with patch("app.api.routes.policies.policy_service") as svc:
            svc.get_version = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/policies/v999")
            assert resp.status_code == 404


class TestActivatePolicy:

    @pytest.mark.asyncio
    async def test_activate_not_found(self, app):
        with patch("app.api.routes.policies.policy_service") as svc:
            svc.activate_version = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/policies/v999/activate")
            assert resp.status_code == 404


class TestSeedPolicy:

    @pytest.mark.asyncio
    async def test_seed_already_exists_and_active(self, app):
        with patch("app.api.routes.policies.policy_service") as svc:
            mock_policy = MagicMock()
            mock_policy.is_active = True
            mock_policy.model_dump.return_value = {"version": "v2.0.0"}
            svc.get_version = AsyncMock(return_value=mock_policy)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/policies/seed")
            assert resp.status_code == 200
            assert resp.json()["activated"] is False

    @pytest.mark.asyncio
    async def test_seed_exists_not_active(self, app):
        with patch("app.api.routes.policies.policy_service") as svc:
            mock_policy = MagicMock()
            mock_policy.is_active = False
            mock_policy.model_dump.return_value = {"version": "v2.0.0"}
            svc.get_version = AsyncMock(return_value=mock_policy)
            svc.activate_version = AsyncMock()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/policies/seed")
            assert resp.status_code == 200
            assert resp.json()["activated"] is True

    @pytest.mark.asyncio
    async def test_seed_new(self, app):
        with patch("app.api.routes.policies.policy_service") as svc:
            svc.get_version = AsyncMock(return_value=None)
            mock_policy = MagicMock()
            mock_policy.version = "v2.0.0"
            mock_policy.model_dump.return_value = {"version": "v2.0.0"}
            svc.create_version = AsyncMock(return_value=mock_policy)
            svc.activate_version = AsyncMock()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/policies/seed")
            assert resp.status_code == 200
            assert resp.json()["activated"] is True


class TestDiffPolicies:

    @pytest.mark.asyncio
    async def test_diff_not_found(self, app):
        with patch("app.api.routes.policies.policy_service") as svc:
            svc.diff_versions = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/policies/diff/v1/v2")
            assert resp.status_code == 404


class TestOpportunitiesConfig:

    @pytest.mark.asyncio
    async def test_get_config(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/policies/config/opportunities")
        assert resp.status_code == 200
        assert "scoring" in resp.json()

    @pytest.mark.asyncio
    async def test_update_config(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/policies/config/opportunities",
                json={"scoring": {"evBenchmark": 600}},
            )
        assert resp.status_code == 200
