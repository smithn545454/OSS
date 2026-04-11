"""Tests for the Paper Trading Workstation endpoints.

Covers:
- GET /api/paper-trading/positions/{id}/snapshots
- POST /api/paper-trading/positions/{id}/analyze (with LLM mock)
- POST /api/paper-trading/positions/{id}/analyze (cache hit)
- GET /api/paper-trading/equity-curve (deterministic math)
- GET /api/paper-trading/equity-curve (empty data)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.schemas import PaperPosition, PositionStatus, Verdict
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


def _make_pos(
    pos_id: str = "pos-1",
    pnl: float = 10.0,
    status: PositionStatus = PositionStatus.OPEN,
    entry_price: float = 5.0,
    entry_date: str = "2026-01-15",
    ai_analysis: str | None = None,
    ai_analysis_at: str | None = None,
) -> PaperPosition:
    return PaperPosition(
        position_id=pos_id,
        evaluation_id=f"eval-{pos_id}",
        option_ticker="O:AAPL260320C00185000",
        entry_price=entry_price,
        entry_date=entry_date,
        verdict_at_entry=Verdict.APPROVE,
        current_price=entry_price * (1 + pnl / 100),
        current_pnl_pct=pnl,
        days_held=3,
        status=status,
        ai_analysis=ai_analysis,
        ai_analysis_at=ai_analysis_at,
    )


# ---------------------------------------------------------------------------
# 1. GET /api/paper-trading/positions/{id}/snapshots
# ---------------------------------------------------------------------------


class TestGetSnapshotsEndpoint:

    @pytest.mark.asyncio
    async def test_get_snapshots_endpoint(self, app):
        """GET snapshots returns snapshot data for a position."""
        mock_snapshots = [
            {
                "position_id": "pos-snap-1",
                "snapshot_date": "2026-01-17",
                "price": 5.5,
                "delta": 0.55,
            },
            {
                "position_id": "pos-snap-1",
                "snapshot_date": "2026-01-16",
                "price": 5.3,
                "delta": 0.54,
            },
        ]

        # PaperSnapshotTable is imported locally inside the endpoint function,
        # so patch at the source module.
        with patch(
            "app.db.tables.PaperSnapshotTable.list_by_position",
            new_callable=AsyncMock,
            return_value=mock_snapshots,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/paper-trading/positions/pos-snap-1/snapshots"
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["position_id"] == "pos-snap-1"
            assert data["count"] == 2
            assert len(data["snapshots"]) == 2
            assert data["snapshots"][0]["price"] == 5.5


# ---------------------------------------------------------------------------
# 2. POST /api/paper-trading/positions/{id}/analyze -- LLM response
# ---------------------------------------------------------------------------


class TestAnalyzePositionReturnsText:

    @pytest.mark.asyncio
    async def test_analyze_position_returns_text(self, app):
        """POST analyze returns LLM-generated analysis text."""
        pos = _make_pos("pos-analyze-1", pnl=8.0)

        with patch(
            "app.db.tables.PaperPositionTable.list_open",
            new_callable=AsyncMock,
            return_value=[pos],
        ), patch(
            "app.db.tables.PaperPositionTable.list_closed",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.db.tables.PaperPositionTable.update",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.db.tables.EvaluationTable.get_by_id",
            new_callable=AsyncMock,
            return_value={
                "decision": {
                    "final_score": 82.0,
                    "verdict": "APPROVE",
                    "premium_leverage_score": 78.0,
                    "underlying_behavior_score": 85.0,
                    "setup_quality_score": 80.0,
                }
            },
        ), patch(
            "app.llm.provider.get_provider",
        ) as mock_get_provider:
            mock_provider = AsyncMock()
            mock_provider.generate = AsyncMock(
                return_value="This position shows moderate upside momentum."
            )
            mock_get_provider.return_value = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/paper-trading/positions/pos-analyze-1/analyze"
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["cached"] is False
            assert "momentum" in data["analysis"]


# ---------------------------------------------------------------------------
# 3. POST /api/paper-trading/positions/{id}/analyze -- cache hit
# ---------------------------------------------------------------------------


class TestAnalyzePositionUsesCache:

    @pytest.mark.asyncio
    async def test_analyze_position_uses_cache(self, app):
        """Position with recent ai_analysis_at returns cached result."""
        # Set ai_analysis_at to 1 hour ago (within the 4h TTL)
        recent_time = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()

        pos = _make_pos(
            "pos-cached-1",
            pnl=12.0,
            ai_analysis="Cached analysis from earlier.",
            ai_analysis_at=recent_time,
        )

        # analyze_position searches open then closed partitions
        with patch(
            "app.db.tables.PaperPositionTable.list_open",
            new_callable=AsyncMock,
            return_value=[pos],
        ), patch(
            "app.db.tables.PaperPositionTable.list_closed",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/paper-trading/positions/pos-cached-1/analyze"
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["cached"] is True
            assert data["analysis"] == "Cached analysis from earlier."


# ---------------------------------------------------------------------------
# 4. GET /api/paper-trading/equity-curve -- deterministic math
# ---------------------------------------------------------------------------


class TestEquityCurveComputation:

    @pytest.mark.asyncio
    async def test_equity_curve_computation(self, app):
        """Verify equity curve math with pre-aggregated daily data."""
        mock_daily = [
            {"date": "2026-01-11", "daily_pnl": 100.0},
            {"date": "2026-01-12", "daily_pnl": 100.0},
        ]

        with patch(
            "app.paper_trading.metrics_aggregator.MetricsAggregator.get_daily_equity",
            new_callable=AsyncMock,
            return_value=mock_daily,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/paper-trading/equity-curve?period=30d"
                )

            assert resp.status_code == 200
            data = resp.json()
            curve = data["curve"]
            assert len(curve) == 2

            assert curve[0]["date"] == "2026-01-11"
            assert curve[0]["daily_pnl"] == 100.0
            assert curve[0]["equity"] == 100.0  # 0 + 100

            assert curve[1]["date"] == "2026-01-12"
            assert curve[1]["daily_pnl"] == 100.0
            assert curve[1]["equity"] == 200.0  # 100 + 100


# ---------------------------------------------------------------------------
# 5. GET /api/paper-trading/equity-curve -- empty
# ---------------------------------------------------------------------------


class TestEquityCurveEmpty:

    @pytest.mark.asyncio
    async def test_equity_curve_empty(self, app):
        """No data returns empty curve."""
        with patch(
            "app.paper_trading.metrics_aggregator.MetricsAggregator.get_daily_equity",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/equity-curve")

            assert resp.status_code == 200
            data = resp.json()
            assert data["curve"] == []
            assert data["period"] == "30d"
