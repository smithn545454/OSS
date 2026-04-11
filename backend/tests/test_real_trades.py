"""Tests for RealTradeTable CRUD operations."""

from __future__ import annotations

import pytest

from app.core.schemas import (
    EvaluationSnapshot,
    RealTrade,
    TradeExitReason,
    TradeStatus,
)
from app.db.tables import RealTradeTable


def _make_snapshot(**overrides) -> EvaluationSnapshot:
    """Create a test EvaluationSnapshot with sensible defaults."""
    defaults = {
        "evaluation_id": "eval-001",
        "opportunity_id": "opp-001",
        "underlying_ticker": "AAPL",
        "option_ticker": "O:AAPL260320C00185000",
        "option_type": "CALL",
        "strike": 185.0,
        "expiration_date": "2026-03-20",
        "dte": 62,
        "dte_bucket": "C",
        "underlying_price": 189.0,
        "moneyness_pct": -2.12,
        "bid": 8.50,
        "ask": 8.80,
        "mid": 8.65,
        "spread_abs": 0.30,
        "spread_pct": 3.47,
        "delta": 0.55,
        "gamma": 0.03,
        "theta": -0.08,
        "vega": 0.25,
        "iv": 0.32,
        "open_interest": 5000,
        "volume": 500,
        "breakeven_price": 193.65,
        "required_move_pct": 2.46,
        "expected_move_pct": 5.0,
        "feasibility_ratio": 0.49,
        "time_adjusted_feasibility": 0.45,
        "rank_score": 85.0,
        "policy_version": "v2.0.0",
        "policy_hash": "test-hash",
        "evaluated_at": "2026-01-17T16:00:00+00:00",
        "verdict": "APPROVE",
        "quality_tier": "TIER_2",
        "final_score": 82.0,
        "premium_leverage_score": 78.0,
        "underlying_behavior_score": 85.0,
        "setup_quality_score": 80.0,
        "primary_reason_code": "ALL_GATES_PASSED",
        "supporting_reason_codes": ["STRONG_VOLATILITY"],
        "failed_gates": [],
        "pillar_scores": [
            {"pillar_id": "DIRECTIONAL", "score": 78, "contributors": [], "tags": []},
            {"pillar_id": "VOLATILITY", "score": 85, "contributors": [], "tags": []},
            {"pillar_id": "STRUCTURE", "score": 80, "contributors": [], "tags": []},
        ],
        "gate_results": [
            {
                "gate_id": "GATE_MIN_OI",
                "enabled": True,
                "passed": True,
                "measured_value": 5000,
                "threshold_value": 300,
                "operator": "gte",
                "units": "contracts",
                "reason_code": "OI_SUFFICIENT",
            }
        ],
        "scanner_triggers": [
            {
                "scanner_type": "BREAKOUT",
                "reason_codes": ["BREAKOUT_ABOVE_20D_HIGH"],
                "metrics": {"breakout_pct": 2.5},
                "triggered_at": "2026-01-17T16:00:00+00:00",
            }
        ],
        "features": {"trend_alignment": {"value": 0.85, "units": "ratio"}},
        "theta_adjusted_ev": 3.50,
        "conviction_score": 82.0,
    }
    defaults.update(overrides)
    return EvaluationSnapshot(**defaults)


def _make_trade(
    trade_id: str = "trade-001",
    evaluation_id: str = "eval-001",
    ticker: str = "AAPL",
    **overrides,
) -> RealTrade:
    """Create a test RealTrade."""
    snapshot = _make_snapshot(evaluation_id=evaluation_id, underlying_ticker=ticker)
    defaults = {
        "trade_id": trade_id,
        "entry_price": 8.80,
        "quantity": 1,
        "trader": "TestTrader",
        "tracked_at": "2026-01-17T16:30:00+00:00",
        "snapshot": snapshot,
    }
    defaults.update(overrides)
    return RealTrade(**defaults)


class TestRealTradeTable:
    """Test RealTradeTable CRUD operations."""

    @pytest.mark.asyncio
    async def test_put_and_get_by_id(self, fresh_dynamodb_client):
        trade = _make_trade()
        await RealTradeTable.put(trade)

        result = await RealTradeTable.get_by_id("trade-001")
        assert result is not None
        assert result["trade_id"] == "trade-001"
        assert result["entry_price"] == 8.80
        assert result["status"] == "OPEN"
        assert result["snapshot"]["underlying_ticker"] == "AAPL"

    @pytest.mark.asyncio
    async def test_dedup_by_evaluation_id(self, fresh_dynamodb_client):
        trade = _make_trade()
        await RealTradeTable.put(trade)

        existing = await RealTradeTable.get_by_evaluation_id("eval-001")
        assert existing is not None
        assert existing["trade_id"] == "trade-001"

        # Non-existent evaluation should return None
        missing = await RealTradeTable.get_by_evaluation_id("eval-999")
        assert missing is None

    @pytest.mark.asyncio
    async def test_list_open_and_closed(self, fresh_dynamodb_client):
        trade1 = _make_trade(trade_id="t1", evaluation_id="e1")
        trade2 = _make_trade(
            trade_id="t2",
            evaluation_id="e2",
            status=TradeStatus.CLOSED,
            tracked_at="2026-01-18T10:00:00+00:00",
        )
        await RealTradeTable.put(trade1)
        await RealTradeTable.put(trade2)

        open_trades = await RealTradeTable.list_open()
        assert len(open_trades) == 1
        assert open_trades[0]["trade_id"] == "t1"

        closed_trades = await RealTradeTable.list_closed()
        assert len(closed_trades) == 1
        assert closed_trades[0]["trade_id"] == "t2"

    @pytest.mark.asyncio
    async def test_list_by_ticker(self, fresh_dynamodb_client):
        trade_aapl = _make_trade(trade_id="t1", evaluation_id="e1", ticker="AAPL")
        trade_msft = _make_trade(trade_id="t2", evaluation_id="e2", ticker="MSFT")
        await RealTradeTable.put(trade_aapl)
        await RealTradeTable.put(trade_msft)

        aapl_trades = await RealTradeTable.list_by_ticker("AAPL")
        assert len(aapl_trades) == 1
        assert aapl_trades[0]["trade_id"] == "t1"

    @pytest.mark.asyncio
    async def test_close_trade(self, fresh_dynamodb_client):
        trade = _make_trade()
        await RealTradeTable.put(trade)

        closed = await RealTradeTable.close(
            trade_id="trade-001",
            tracked_at="2026-01-17T16:30:00+00:00",
            exit_price=12.50,
            exit_reason="PROFIT_TARGET",
            exit_notes="Hit first target",
        )

        assert closed["status"] == "CLOSED"
        assert closed["exit_reason"] == "PROFIT_TARGET"
        assert closed["exit_notes"] == "Hit first target"
        # P&L: (12.50 - 8.80) / 8.80 * 100 = 42.05%
        assert abs(float(closed["realized_pnl_pct"]) - 42.05) < 0.1
        # Dollar P&L: (12.50 - 8.80) * 1 * 100 = $370
        assert abs(float(closed["realized_pnl_dollars"]) - 370.0) < 1.0

        # Verify moved from OPEN to CLOSED
        open_trades = await RealTradeTable.list_open()
        assert len(open_trades) == 0

        closed_trades = await RealTradeTable.list_closed()
        assert len(closed_trades) == 1

    @pytest.mark.asyncio
    async def test_close_nonexistent_trade_raises(self, fresh_dynamodb_client):
        with pytest.raises(ValueError, match="Open trade not found"):
            await RealTradeTable.close(
                trade_id="nonexistent",
                tracked_at="2026-01-17T16:30:00+00:00",
                exit_price=10.0,
                exit_reason="MANUAL",
            )

    @pytest.mark.asyncio
    async def test_count_by_status(self, fresh_dynamodb_client):
        trade1 = _make_trade(trade_id="t1", evaluation_id="e1")
        trade2 = _make_trade(trade_id="t2", evaluation_id="e2",
                             tracked_at="2026-01-18T10:00:00+00:00")
        await RealTradeTable.put(trade1)
        await RealTradeTable.put(trade2)

        open_count = await RealTradeTable.count_by_status("OPEN")
        assert open_count == 2

        closed_count = await RealTradeTable.count_by_status("CLOSED")
        assert closed_count == 0

    @pytest.mark.asyncio
    async def test_snapshot_preserved_through_roundtrip(self, fresh_dynamodb_client):
        """Ensure the full snapshot survives DynamoDB serialization."""
        trade = _make_trade()
        await RealTradeTable.put(trade)

        result = await RealTradeTable.get_by_id("trade-001")
        snapshot = result["snapshot"]

        # Verify all key snapshot fields survived
        assert snapshot["evaluation_id"] == "eval-001"
        assert snapshot["verdict"] == "APPROVE"
        assert snapshot["quality_tier"] == "TIER_2"
        assert snapshot["final_score"] == 82.0
        assert len(snapshot["pillar_scores"]) == 3
        assert len(snapshot["gate_results"]) == 1
        assert len(snapshot["scanner_triggers"]) == 1
        assert "trend_alignment" in snapshot["features"]
        assert snapshot["theta_adjusted_ev"] == 3.5
        assert snapshot["conviction_score"] == 82.0
