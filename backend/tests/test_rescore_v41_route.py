"""Tests for POST /api/paper-trading/rescore-v4.1.0.

The endpoint applies archetype-aware scoring to v4.0 paper positions:
- Skips v3 positions (no v4 pillar scores).
- Skips positions already at scoring_version="v4.1.0".
- Writes archetype fields + snapshots v4.0 tier into quality_tier_v40.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.archetypes.defaults import default_anti_archetypes, default_archetypes
from app.core.schemas import (
    PaperPosition,
    PolicyConfig,
    PositionStatus,
    QualityTier,
    Verdict,
)
from app.main import app

_ROUTE = "app.api.routes.paper_trading"


def _v4_position(
    position_id: str = "pos-v4",
    tier: QualityTier = QualityTier.TIER_2,
) -> PaperPosition:
    """Build a v4 paper position strong enough to match Archetype B (TS≥75)."""
    return PaperPosition(
        position_id=position_id,
        evaluation_id=f"eval-{position_id}",
        option_ticker="AAPL240119C180000",
        underlying_ticker="AAPL",
        entry_price=1.00,
        entry_date="2026-04-01",
        verdict_at_entry=Verdict.APPROVE,
        quality_tier_at_entry=tier,
        current_price=1.00,
        current_pnl_pct=0.0,
        status=PositionStatus.OPEN,
        option_type="CALL",
        dte_at_entry=45,
        dte_bucket="B",
        entry_delta=0.45,
        entry_iv=0.30,
        entry_spread_pct=3.0,
        entry_underlying_price=180.0,
        scanner_source="BREAKOUT",
        scanner_list=["BREAKOUT"],
        convergence_count=1,
        conviction_score=78.0,
        pillar_directional_conviction=70.0,
        pillar_move_potential=70.0,
        pillar_trade_structure=80.0,
    )


def _v3_position() -> PaperPosition:
    """v3 position (no v4 pillar scores) — must be skipped by rescore."""
    return PaperPosition(
        position_id="pos-v3",
        evaluation_id="eval-v3",
        option_ticker="MSFT240119C400000",
        underlying_ticker="MSFT",
        entry_price=2.00,
        entry_date="2026-03-01",
        verdict_at_entry=Verdict.APPROVE,
        quality_tier_at_entry=QualityTier.TIER_1,
        current_price=2.00,
        current_pnl_pct=0.0,
        status=PositionStatus.OPEN,
        pillar_premium_leverage=80.0,
        pillar_underlying_behavior=75.0,
        pillar_setup_quality=70.0,
        conviction_score=76.0,
    )


def _mock_policy_with_archetypes():
    policy = MagicMock()
    policy.config = PolicyConfig(
        archetypes=default_archetypes(),
        anti_archetypes=default_anti_archetypes(),
    )
    return policy


def _pos_table_mock(positions: list[PaperPosition]) -> MagicMock:
    mock = MagicMock()
    mock.list_open = AsyncMock(return_value=positions)
    mock.list_closed = AsyncMock(return_value=[])
    mock.update = AsyncMock(return_value=None)
    return mock


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestRescoreV41Route:
    @pytest.mark.asyncio
    async def test_rejects_policy_without_archetypes(self, client):
        empty_policy = MagicMock()
        empty_policy.config = PolicyConfig()  # archetypes=None, anti_archetypes=None
        ps = MagicMock()
        ps.return_value.get_active = AsyncMock(return_value=empty_policy)

        with patch("app.core.policy.PolicyService", ps):
            resp = await client.post("/api/paper-trading/rescore-v4.1.0")
        assert resp.status_code == 400
        assert "archetype" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_skips_v3_positions(self, client):
        pos_mock = _pos_table_mock([_v3_position()])
        ps = MagicMock()
        ps.return_value.get_active = AsyncMock(return_value=_mock_policy_with_archetypes())

        with patch("app.core.policy.PolicyService", ps), \
             patch(f"{_ROUTE}.PaperPositionTable", pos_mock), \
             patch("app.db.tables.FeatureValueTable") as fv_mock:
            fv_mock.list_by_evaluation = AsyncMock(return_value=[])
            resp = await client.post("/api/paper-trading/rescore-v4.1.0")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_needing_rescore"] == 0
        assert body["rescored"] == 0
        pos_mock.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_already_rescored(self, client):
        done = _v4_position(position_id="pos-done")
        done.scoring_version = "v4.1.0"
        pos_mock = _pos_table_mock([done])
        ps = MagicMock()
        ps.return_value.get_active = AsyncMock(return_value=_mock_policy_with_archetypes())

        with patch("app.core.policy.PolicyService", ps), \
             patch(f"{_ROUTE}.PaperPositionTable", pos_mock), \
             patch("app.db.tables.FeatureValueTable") as fv_mock:
            fv_mock.list_by_evaluation = AsyncMock(return_value=[])
            resp = await client.post("/api/paper-trading/rescore-v4.1.0")

        assert resp.status_code == 200
        assert resp.json()["total_needing_rescore"] == 0
        pos_mock.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rescore_writes_archetype_fields_and_snapshot(self, client):
        pos = _v4_position()
        pos_mock = _pos_table_mock([pos])
        ps = MagicMock()
        ps.return_value.get_active = AsyncMock(return_value=_mock_policy_with_archetypes())

        with patch("app.core.policy.PolicyService", ps), \
             patch(f"{_ROUTE}.PaperPositionTable", pos_mock), \
             patch("app.db.tables.FeatureValueTable") as fv_mock:
            fv_mock.list_by_evaluation = AsyncMock(return_value=[])
            resp = await client.post("/api/paper-trading/rescore-v4.1.0")

        assert resp.status_code == 200
        body = resp.json()
        assert body["rescored"] == 1
        assert body["errors"] == 0

        pos_mock.update.assert_awaited_once()
        _, updates = pos_mock.update.await_args.args
        assert updates["scoring_version"] == "v4.1.0"
        assert "rescored_at" in updates
        # Old tier snapshotted exactly once.
        assert updates["quality_tier_v40"] == QualityTier.TIER_2.value
        # Archetype fields always written (even when None).
        assert "archetype_matched" in updates
        assert "archetype_all_fits" in updates
        assert "anti_archetype_triggered" in updates

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(self, client):
        pos_mock = _pos_table_mock([_v4_position()])
        ps = MagicMock()
        ps.return_value.get_active = AsyncMock(return_value=_mock_policy_with_archetypes())

        with patch("app.core.policy.PolicyService", ps), \
             patch(f"{_ROUTE}.PaperPositionTable", pos_mock), \
             patch("app.db.tables.FeatureValueTable") as fv_mock:
            fv_mock.list_by_evaluation = AsyncMock(return_value=[])
            resp = await client.post("/api/paper-trading/rescore-v4.1.0?dry_run=true")

        assert resp.status_code == 200
        assert resp.json()["rescored"] == 1
        pos_mock.update.assert_not_awaited()
