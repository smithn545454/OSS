"""Tests for the PolicyService (core/policy.py).

Covers CRUD operations, version computation, changelog generation,
and diff comparison.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.policy import PolicyService
from app.core.schemas import Policy, PolicyChangelog, PolicyConfig, PolicyDiff


@pytest.fixture
def service():
    return PolicyService()


def _make_policy(version="v2.0.0", config=None, active=True):
    config = config or PolicyConfig()
    return Policy(
        version=version,
        policy_hash=Policy.compute_hash(config),
        config=config,
        created_by="test",
        is_active=active,
    )


# ---------------------------------------------------------------------------
# Tests: create_version
# ---------------------------------------------------------------------------


class TestCreateVersion:

    @pytest.mark.asyncio
    async def test_create_new_version(self, service):
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.put = AsyncMock()
            mock_table.list = AsyncMock(return_value=[])

            policy = await service.create_version(
                config=PolicyConfig(), user="test_user"
            )

        assert policy is not None
        assert policy.version == "v2.0.0"  # Default when no existing
        assert policy.created_by == "test_user"
        assert policy.is_active is False
        mock_table.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_with_base_version(self, service):
        base = _make_policy("v2.0.0")
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.put = AsyncMock()
            mock_table.get = AsyncMock(return_value=base)

            policy = await service.create_version(
                config=PolicyConfig(), user="admin", base_version="v2.0.0"
            )

        assert policy.version == "v2.0.1"

    @pytest.mark.asyncio
    async def test_create_increments_existing(self, service):
        existing = _make_policy("v2.1.3")
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.put = AsyncMock()
            mock_table.list = AsyncMock(return_value=[existing])

            policy = await service.create_version(
                config=PolicyConfig(), user="admin"
            )

        assert policy.version == "v2.1.4"


# ---------------------------------------------------------------------------
# Tests: get_version / get_active / list_versions
# ---------------------------------------------------------------------------


class TestReadOperations:

    @pytest.mark.asyncio
    async def test_get_version(self, service):
        expected = _make_policy("v2.0.0")
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.get = AsyncMock(return_value=expected)
            result = await service.get_version("v2.0.0")
        assert result.version == "v2.0.0"

    @pytest.mark.asyncio
    async def test_get_version_not_found(self, service):
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            result = await service.get_version("v999.0.0")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_active(self, service):
        expected = _make_policy("v2.0.0")
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.get_active = AsyncMock(return_value=expected)
            result = await service.get_active()
        assert result is not None

    @pytest.mark.asyncio
    async def test_list_versions(self, service):
        policies = [_make_policy("v2.0.0"), _make_policy("v2.0.1")]
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.list = AsyncMock(return_value=policies)
            result = await service.list_versions()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Tests: activate_version
# ---------------------------------------------------------------------------


class TestActivateVersion:

    @pytest.mark.asyncio
    async def test_activate_existing(self, service):
        policy = _make_policy("v2.0.0", active=False)
        activated = _make_policy("v2.0.0", active=True)
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.get = AsyncMock(side_effect=[policy, activated])
            mock_table.set_active = AsyncMock()
            result = await service.activate_version("v2.0.0")
        assert result is not None
        mock_table.set_active.assert_called_once_with("v2.0.0")

    @pytest.mark.asyncio
    async def test_activate_nonexistent(self, service):
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            result = await service.activate_version("v999.0.0")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: diff_versions
# ---------------------------------------------------------------------------


class TestDiffVersions:

    @pytest.mark.asyncio
    async def test_diff_identical(self, service):
        p1 = _make_policy("v2.0.0")
        p2 = _make_policy("v2.0.1")
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.get = AsyncMock(side_effect=[p1, p2])
            result = await service.diff_versions("v2.0.0", "v2.0.1")
        assert result is not None
        assert result.identical is True
        assert len(result.changes) == 0

    @pytest.mark.asyncio
    async def test_diff_with_changes(self, service):
        from app.core.schemas import GateConfig

        config1 = PolicyConfig()
        # Create config2 with a different gate threshold (frozen models)
        new_gates = GateConfig(min_open_interest=500)
        config2 = config1.model_copy(update={"gates": new_gates})

        p1 = _make_policy("v2.0.0", config=config1)
        p2 = _make_policy("v2.0.1", config=config2)

        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.get = AsyncMock(side_effect=[p1, p2])
            result = await service.diff_versions("v2.0.0", "v2.0.1")

        assert result is not None
        assert result.identical is False
        assert len(result.changes) > 0

    @pytest.mark.asyncio
    async def test_diff_version_not_found(self, service):
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            result = await service.diff_versions("v1", "v2")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _generate_changelog / _compare_dicts (pure logic)
# ---------------------------------------------------------------------------


class TestChangelogGeneration:

    def test_generate_changelog_no_changes(self, service):
        config = PolicyConfig()
        changes = service._generate_changelog(config, config, "user")
        assert len(changes) == 0

    def test_generate_changelog_with_changes(self, service):
        from app.core.schemas import GateConfig

        old = PolicyConfig()
        new_gates = GateConfig(min_open_interest=500)
        new = old.model_copy(update={"gates": new_gates})

        changes = service._generate_changelog(old, new, "user")
        assert len(changes) >= 1

        oi_change = [c for c in changes if "min_open_interest" in c.field_path]
        assert len(oi_change) == 1
        assert oi_change[0].old_value == 300
        assert oi_change[0].new_value == 500

    def test_compare_dicts_nested(self, service):
        old = {"a": {"b": 1, "c": 2}}
        new = {"a": {"b": 1, "c": 3}}
        changes: list = []
        service._compare_dicts(old, new, "", changes, "user", "now")
        assert len(changes) == 1
        assert changes[0].field_path == "a.c"

    def test_compare_dicts_added_key(self, service):
        old = {"a": 1}
        new = {"a": 1, "b": 2}
        changes: list = []
        service._compare_dicts(old, new, "", changes, "user", "now")
        assert len(changes) == 1
        assert changes[0].field_path == "b"


# ---------------------------------------------------------------------------
# Tests: _compute_next_version (pure logic)
# ---------------------------------------------------------------------------


class TestComputeNextVersion:

    @pytest.mark.asyncio
    async def test_increment_base(self, service):
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.list = AsyncMock(return_value=[])
            result = await service._compute_next_version("v2.0.0")
        assert result == "v2.0.1"

    @pytest.mark.asyncio
    async def test_increment_from_latest(self, service):
        existing = _make_policy("v3.1.5")
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.list = AsyncMock(return_value=[existing])
            result = await service._compute_next_version()
        assert result == "v3.1.6"

    @pytest.mark.asyncio
    async def test_default_when_empty(self, service):
        with patch("app.core.policy.PolicyTable") as mock_table:
            mock_table.list = AsyncMock(return_value=[])
            result = await service._compute_next_version()
        assert result == "v2.0.0"
