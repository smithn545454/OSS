"""Tests for backtest DynamoDB table operations."""

from __future__ import annotations

import pytest
from app.db.backtest_tables import BacktestInsightTable, BacktestRunTable, BacktestTradeTable


@pytest.fixture
def sample_run() -> dict:
    return {
        "run_id": "run-001",
        "name": "Test Backtest",
        "status": "PENDING",
        "created_at": "2026-01-15T12:00:00+00:00",
        "config": {"start_date": "2024-01-02", "end_date": "2024-01-31"},
        "progress": {"days_completed": 0, "days_total": 22},
    }


@pytest.fixture
def sample_trade() -> dict:
    return {
        "run_id": "run-001",
        "trade_id": "trade-001",
        "ticker": "AAPL",
        "option_ticker": "O:AAPL240119C00190000",
        "entry_date": "2024-01-05",
        "entry_price": 5.50,
        "exit_date": "2024-01-12",
        "exit_price": 7.25,
        "pnl_pct": 31.82,
        "scanner_type": "breakout",
        "market_regime": "bull_trend",
    }


class TestBacktestRunTable:
    @pytest.mark.asyncio
    async def test_put_and_get(self, moto_dynamodb, sample_run):
        await BacktestRunTable.put(sample_run)
        result = await BacktestRunTable.get("run-001")
        assert result is not None
        assert result["run_id"] == "run-001"
        assert result["name"] == "Test Backtest"
        assert result["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_get_not_found(self, moto_dynamodb):
        result = await BacktestRunTable.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_status(self, moto_dynamodb, sample_run):
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.update_status("run-001", "RUNNING")
        result = await BacktestRunTable.get("run-001")
        assert result["status"] == "RUNNING"

    @pytest.mark.asyncio
    async def test_update_status_completed_sets_timestamp(self, moto_dynamodb, sample_run):
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.update_status("run-001", "COMPLETED")
        result = await BacktestRunTable.get("run-001")
        assert result["status"] == "COMPLETED"
        assert "completed_at" in result

    @pytest.mark.asyncio
    async def test_update_progress(self, moto_dynamodb, sample_run):
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.update_progress("run-001", {"days_completed": 10, "days_total": 22})
        result = await BacktestRunTable.get("run-001")
        assert result["progress"]["days_completed"] == 10

    @pytest.mark.asyncio
    async def test_list_runs_all(self, moto_dynamodb, sample_run):
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.put({**sample_run, "run_id": "run-002", "name": "Second Run"})
        runs = await BacktestRunTable.list_runs()
        assert len(runs) >= 2

    @pytest.mark.asyncio
    async def test_list_runs_by_status(self, moto_dynamodb, sample_run):
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.put({
            **sample_run,
            "run_id": "run-002",
            "status": "COMPLETED",
        })
        pending_runs = await BacktestRunTable.list_runs(status="PENDING")
        assert any(r["run_id"] == "run-001" for r in pending_runs)

    @pytest.mark.asyncio
    async def test_delete(self, moto_dynamodb, sample_run):
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.delete("run-001")
        result = await BacktestRunTable.get("run-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_atomic_increment_progress(self, moto_dynamodb, sample_run):
        """Atomic progress increment uses DynamoDB ADD, safe for concurrent workers."""
        await BacktestRunTable.put(sample_run)
        result = await BacktestRunTable.atomic_increment_progress("run-001", 3, 5)
        assert result["days_completed"] == 3
        assert result["trades_found"] == 5
        # Increment again
        result = await BacktestRunTable.atomic_increment_progress("run-001", 2, 10)
        assert result["days_completed"] == 5
        assert result["trades_found"] == 15

    @pytest.mark.asyncio
    async def test_atomic_increment_progress_no_op(self, moto_dynamodb, sample_run):
        """No-op increment returns current values."""
        await BacktestRunTable.put(sample_run)
        result = await BacktestRunTable.atomic_increment_progress("run-001", 0, 0)
        assert result["days_completed"] == 0
        assert result["trades_found"] == 0

    @pytest.mark.asyncio
    async def test_set_total_batches(self, moto_dynamodb, sample_run):
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.set_total_batches("run-001", 10)
        result = await BacktestRunTable.get("run-001")
        assert result["total_batches"] == 10
        assert result["batches_completed"] == 0

    @pytest.mark.asyncio
    async def test_atomic_increment_batches_completed(self, moto_dynamodb, sample_run):
        """Atomic batch increment for last-worker detection."""
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.set_total_batches("run-001", 3)

        count = await BacktestRunTable.atomic_increment_batches_completed("run-001")
        assert count == 1
        count = await BacktestRunTable.atomic_increment_batches_completed("run-001")
        assert count == 2
        count = await BacktestRunTable.atomic_increment_batches_completed("run-001")
        assert count == 3

        # Verify final state
        result = await BacktestRunTable.get("run-001")
        assert result["batches_completed"] == 3

    @pytest.mark.asyncio
    async def test_update_progress_delegates_to_atomic(self, moto_dynamodb, sample_run):
        """update_progress with increments should use atomic operations."""
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.update_progress("run-001", days_increment=5, trades_increment=3)
        await BacktestRunTable.update_progress("run-001", days_increment=2, trades_increment=7)
        # Verify the atomic result is correct (ADD expressions accumulate)
        result = await BacktestRunTable.get("run-001")
        progress = result.get("progress", {})
        assert int(progress.get("days_completed", 0)) == 7
        assert int(progress.get("trades_found", 0)) == 10


class TestBacktestTradeTable:
    @pytest.mark.asyncio
    async def test_put_and_list_by_run(self, moto_dynamodb, sample_trade):
        await BacktestTradeTable.put(sample_trade)
        trades = await BacktestTradeTable.list_by_run("run-001")
        assert len(trades) == 1
        assert trades[0]["ticker"] == "AAPL"
        assert trades[0]["pnl_pct"] == pytest.approx(31.82)

    @pytest.mark.asyncio
    async def test_put_batch(self, moto_dynamodb, sample_trade):
        trades = [
            {**sample_trade, "trade_id": f"trade-{i}", "ticker": f"T{i}"}
            for i in range(5)
        ]
        await BacktestTradeTable.put_batch(trades)
        result = await BacktestTradeTable.list_by_run("run-001")
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_list_by_scanner(self, moto_dynamodb, sample_trade):
        await BacktestTradeTable.put(sample_trade)
        await BacktestTradeTable.put({
            **sample_trade,
            "trade_id": "trade-002",
            "scanner_type": "compression",
        })
        breakout_trades = await BacktestTradeTable.list_by_scanner("breakout")
        assert len(breakout_trades) == 1
        assert breakout_trades[0]["scanner_type"] == "breakout"

    @pytest.mark.asyncio
    async def test_list_by_regime(self, moto_dynamodb, sample_trade):
        await BacktestTradeTable.put(sample_trade)
        trades = await BacktestTradeTable.list_by_regime("bull_trend")
        assert len(trades) == 1

    @pytest.mark.asyncio
    async def test_count_by_run(self, moto_dynamodb, sample_trade):
        trades = [
            {**sample_trade, "trade_id": f"trade-{i}"}
            for i in range(3)
        ]
        await BacktestTradeTable.put_batch(trades)
        count = await BacktestTradeTable.count_by_run("run-001")
        assert count == 3

    @pytest.mark.asyncio
    async def test_delete_by_run(self, moto_dynamodb, sample_trade):
        trades = [
            {**sample_trade, "trade_id": f"trade-{i}"}
            for i in range(3)
        ]
        await BacktestTradeTable.put_batch(trades)
        deleted = await BacktestTradeTable.delete_by_run("run-001")
        assert deleted == 3
        remaining = await BacktestTradeTable.list_by_run("run-001")
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_float_roundtrip(self, moto_dynamodb):
        trade = {
            "run_id": "run-001",
            "trade_id": "trade-float",
            "entry_price": 3.14159,
            "pnl_pct": -12.345,
            "scanner_type": "cheap_options",
            "entry_date": "2024-01-05",
        }
        await BacktestTradeTable.put(trade)
        trades = await BacktestTradeTable.list_by_run("run-001")
        assert len(trades) == 1
        assert trades[0]["entry_price"] == pytest.approx(3.14159)
        assert trades[0]["pnl_pct"] == pytest.approx(-12.345)


class TestBacktestInsightTable:
    @pytest.mark.asyncio
    async def test_put_and_list(self, moto_dynamodb):
        insight = {
            "run_id": "run-001",
            "insight_id": "insight-001",
            "type": "pattern_detection",
            "title": "Breakout scanner outperforms",
            "summary": "Breakout trades show 2.3x better returns",
            "confidence": 0.85,
        }
        await BacktestInsightTable.put(insight)
        insights = await BacktestInsightTable.list_by_run("run-001")
        assert len(insights) == 1
        assert insights[0]["title"] == "Breakout scanner outperforms"

    @pytest.mark.asyncio
    async def test_delete_by_run(self, moto_dynamodb):
        for i in range(3):
            await BacktestInsightTable.put({
                "run_id": "run-001",
                "insight_id": f"insight-{i}",
                "type": "test",
                "title": f"Insight {i}",
            })
        deleted = await BacktestInsightTable.delete_by_run("run-001")
        assert deleted == 3
        remaining = await BacktestInsightTable.list_by_run("run-001")
        assert len(remaining) == 0


class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_run_with_trades_and_insights(self, moto_dynamodb, sample_run, sample_trade):
        # Create run
        await BacktestRunTable.put(sample_run)
        await BacktestRunTable.update_status("run-001", "RUNNING")

        # Add trades
        trades = [
            {**sample_trade, "trade_id": f"trade-{i}", "ticker": f"T{i}"}
            for i in range(5)
        ]
        await BacktestTradeTable.put_batch(trades)

        # Add insight
        await BacktestInsightTable.put({
            "run_id": "run-001",
            "insight_id": "insight-001",
            "type": "summary",
            "title": "Run Summary",
        })

        # Complete run
        await BacktestRunTable.update_status("run-001", "COMPLETED")

        # Verify
        run = await BacktestRunTable.get("run-001")
        assert run["status"] == "COMPLETED"
        assert "completed_at" in run

        trade_count = await BacktestTradeTable.count_by_run("run-001")
        assert trade_count == 5

        insights = await BacktestInsightTable.list_by_run("run-001")
        assert len(insights) == 1
