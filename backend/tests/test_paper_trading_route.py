"""Tests for api/routes/paper_trading.py.

Covers list_positions, get_position, close_position,
get_metrics, get_tier_comparison, get_exit_analysis,
get_summary, and _generate_exit_insights.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.paper_trading import _generate_exit_insights
from app.core.schemas import PaperPosition, PositionStatus, Verdict
from app.main import create_app
from app.paper_trading.models import PerformanceMetrics


@pytest.fixture
def app():
    return create_app()


def _make_pos(pos_id="pos-1", pnl=10.0, status=PositionStatus.OPEN):
    return PaperPosition(
        position_id=pos_id,
        evaluation_id=f"eval-{pos_id}",
        option_ticker="O:AAPL260320C00185000",
        entry_price=5.0,
        entry_date="2026-01-15",
        verdict_at_entry=Verdict.APPROVE,
        current_price=5.0 * (1 + pnl / 100),
        current_pnl_pct=pnl,
        days_held=3,
        status=status,
    )


class TestListPositions:

    @pytest.mark.asyncio
    async def test_list_all(self, app):
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table:
            mock_table.list_all = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/positions")
            assert resp.status_code == 200
            assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_list_open(self, app):
        pos = _make_pos()
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table:
            mock_table.list_open = AsyncMock(return_value=[pos])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/positions?status=open")
            assert resp.status_code == 200
            assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_list_closed(self, app):
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table:
            mock_table.list_closed = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/positions?status=closed")
            assert resp.status_code == 200


class TestGetPosition:

    @pytest.mark.asyncio
    async def test_position_found(self, app):
        pos = _make_pos("p-found")
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table:
            mock_table.list_all = AsyncMock(return_value=[pos])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/positions/p-found")
            assert resp.status_code == 200
            assert resp.json()["position_id"] == "p-found"

    @pytest.mark.asyncio
    async def test_position_not_found(self, app):
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table:
            mock_table.list_all = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/positions/missing")
            assert resp.status_code == 404


class TestClosePosition:

    @pytest.mark.asyncio
    async def test_close_success(self, app):
        pos = _make_pos("p-close", status=PositionStatus.CLOSED)
        pos.exit_price = 6.0
        pos.exit_reason = "MANUAL"
        with patch("app.api.routes.paper_trading.close_position_manually") as mock_close:
            mock_close.return_value = pos
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/paper-trading/positions/p-close/close",
                    json={"exit_price": 6.0},
                )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_close_not_found(self, app):
        with patch("app.api.routes.paper_trading.close_position_manually") as mock_close:
            mock_close.return_value = None
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/paper-trading/positions/missing/close",
                    json={},
                )
            assert resp.status_code == 404


class TestGetMetrics:

    @pytest.mark.asyncio
    async def test_get_metrics(self, app):
        with patch("app.api.routes.paper_trading.calculate_performance_metrics") as mock_calc:
            mock_calc.return_value = PerformanceMetrics()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/metrics")
            assert resp.status_code == 200
            data = resp.json()
            assert "metrics" in data
            assert "targets" in data


class TestGetTierComparison:

    @pytest.mark.asyncio
    async def test_tiers(self, app):
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table:
            mock_table.list_all = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/metrics/tiers")
            assert resp.status_code == 200


class TestGetExitAnalysis:

    @pytest.mark.asyncio
    async def test_exits(self, app):
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table:
            mock_table.list_all = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/metrics/exits")
            assert resp.status_code == 200
            assert "insights" in resp.json()


class TestGetSummary:

    @pytest.mark.asyncio
    async def test_summary(self, app):
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table, \
             patch("app.api.routes.paper_trading.calculate_performance_metrics") as mock_calc:
            mock_table.list_open = AsyncMock(return_value=[])
            mock_table.list_closed = AsyncMock(return_value=[])
            mock_calc.return_value = PerformanceMetrics()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/summary")
            assert resp.status_code == 200
            data = resp.json()
            assert data["positions"]["open"] == 0


class TestGenerateExitInsights:

    def test_profit_target_insight(self):
        analysis = {"PROFIT_TARGET": {"mfe_left_on_table": 25.0, "avg_mfe": 40.0, "avg_return": 15.0}}
        insights = _generate_exit_insights(analysis)
        assert any("Profit target" in i for i in insights)

    def test_stop_loss_insight(self):
        analysis = {"STOP_LOSS": {"avg_mfe": 20.0}}
        insights = _generate_exit_insights(analysis)
        assert any("stop" in i.lower() for i in insights)

    def test_time_exit_insight(self):
        analysis = {"TIME_EXIT": {"avg_return": -25.0}}
        insights = _generate_exit_insights(analysis)
        assert any("Time exit" in i for i in insights)

    def test_empty_analysis(self):
        insights = _generate_exit_insights({})
        assert insights == ["Exit strategy appears well-balanced"]
