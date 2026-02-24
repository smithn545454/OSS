"""Tests for backtest coordinator and worker."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.backtest.coordinator import (
    batch_days,
    create_backtest_run,
    generate_trading_days,
    start_backtest_run,
)
from app.core.schemas import BacktestRunConfig, PolicyConfig


# ============================================================================
# Trading Day Generation
# ============================================================================


class TestGenerateTradingDays:
    def test_one_week(self):
        days = generate_trading_days(date(2026, 1, 19), date(2026, 1, 23))
        assert len(days) == 5  # Mon-Fri
        assert days[0] == date(2026, 1, 19)
        assert days[-1] == date(2026, 1, 23)

    def test_skips_weekends(self):
        days = generate_trading_days(date(2026, 1, 19), date(2026, 1, 26))
        # Mon 19 - Mon 26 = 6 weekdays
        assert len(days) == 6
        for d in days:
            assert d.weekday() < 5

    def test_empty_range(self):
        days = generate_trading_days(date(2026, 1, 20), date(2026, 1, 19))
        assert days == []

    def test_single_day(self):
        days = generate_trading_days(date(2026, 1, 20), date(2026, 1, 20))
        assert len(days) == 1

    def test_full_month(self):
        days = generate_trading_days(date(2026, 1, 1), date(2026, 1, 31))
        assert 20 <= len(days) <= 23  # ~22 trading days in January


# ============================================================================
# Batch Days
# ============================================================================


class TestBatchDays:
    def test_exact_batches(self):
        days = [date(2026, 1, i) for i in range(1, 11)]  # 10 days
        batches = batch_days(days, batch_size=5)
        assert len(batches) == 2
        assert len(batches[0]) == 5
        assert len(batches[1]) == 5

    def test_partial_last_batch(self):
        days = [date(2026, 1, i) for i in range(1, 8)]  # 7 days
        batches = batch_days(days, batch_size=5)
        assert len(batches) == 2
        assert len(batches[0]) == 5
        assert len(batches[1]) == 2

    def test_single_batch(self):
        days = [date(2026, 1, i) for i in range(1, 4)]  # 3 days
        batches = batch_days(days, batch_size=5)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_empty(self):
        batches = batch_days([])
        assert batches == []


# ============================================================================
# Create Backtest Run
# ============================================================================


class TestCreateBacktestRun:
    @pytest.mark.asyncio
    async def test_creates_run_with_correct_fields(self):
        config = BacktestRunConfig(
            name="Test Run",
            start_date="2026-01-05",
            end_date="2026-01-16",
            policy_snapshot=PolicyConfig(),
        )

        with patch("app.backtest.coordinator.BacktestRunTable") as mock_table:
            mock_table.put = AsyncMock()
            run = await create_backtest_run(config, persist=True)

        assert run.run_id  # UUID assigned
        assert run.status == "PENDING"
        assert run.config.name == "Test Run"
        assert run.progress["days_total"] == 10  # 2 weeks of weekdays
        assert run.progress["days_completed"] == 0
        assert run.created_at != ""

    @pytest.mark.asyncio
    async def test_no_persist_skips_db(self):
        config = BacktestRunConfig(
            name="Test",
            start_date="2026-01-05",
            end_date="2026-01-09",
            policy_snapshot=PolicyConfig(),
        )

        with patch("app.backtest.coordinator.BacktestRunTable") as mock_table:
            mock_table.put = AsyncMock()
            run = await create_backtest_run(config, persist=False)
            mock_table.put.assert_not_called()

        assert run.status == "PENDING"


class TestStartBacktestRun:
    @pytest.mark.asyncio
    async def test_transitions_to_running(self):
        config = BacktestRunConfig(
            name="Test",
            start_date="2026-01-05",
            end_date="2026-01-09",
            policy_snapshot=PolicyConfig(),
        )

        with patch("app.backtest.coordinator.BacktestRunTable") as mock_table:
            mock_table.put = AsyncMock()
            mock_table.update_status = AsyncMock()
            run = await create_backtest_run(config, persist=False)
            batches = await start_backtest_run(run, persist=False)

        assert run.status == "RUNNING"
        assert len(batches) >= 1
        # 5 weekdays in Jan 5-9
        total_days = sum(len(b) for b in batches)
        assert total_days == 5
