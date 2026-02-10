"""Tests for features/calculator.py.

Covers FeatureComputer.compute_features and compute_features_batch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.features.calculator import FeatureComputer
from app.services.polygon import DailyBar


def _make_eval(eid: str = "e1", ticker: str = "AAPL"):
    """Create a mock Evaluation."""
    e = MagicMock()
    e.evaluation_id = eid
    e.underlying_ticker = ticker
    e.option_ticker = f"O:{ticker}"
    e.underlying_price = 150.0
    e.iv = 0.30
    e.mid = 5.0
    e.spread_pct = 3.0
    e.open_interest = 500
    e.volume = 200
    e.theta = -0.05
    e.breakeven_price = 155.0
    e.required_move_pct = 3.3
    e.expected_move_pct = 5.0
    e.feasibility_ratio = 1.5
    e.time_adjusted_feasibility = 1.3
    e.dte = 45
    return e


def _make_opp(ticker: str = "AAPL"):
    """Create a mock Opportunity."""
    opp = MagicMock()
    opp.underlying_ticker = ticker
    opp.scanner_triggers = []
    opp.direction_hint = "NONE"
    return opp


def _make_bars(n: int = 60, ticker: str = "TEST") -> list[DailyBar]:
    """Create a list of DailyBar for testing."""
    return [
        DailyBar(
            ticker=ticker,
            date=f"2026-01-{i+1:02d}" if i < 28 else f"2026-02-{i-27:02d}",
            open=100.0 + i * 0.5,
            high=101.0 + i * 0.5,
            low=99.0 + i * 0.5,
            close=100.0 + i * 0.5,
            volume=100000,
            vwap=100.0 + i * 0.5,
        )
        for i in range(n)
    ]


class TestFeatureComputerComputeFeatures:

    @pytest.mark.asyncio
    async def test_with_provided_data(self):
        """Test compute_features when all data is provided directly."""
        polygon = MagicMock()
        polygon.get_daily_bars_parsed = AsyncMock(return_value=[])
        computer = FeatureComputer(polygon_client=polygon)

        bars = _make_bars(60)
        spy_bars = _make_bars(60, "SPY")
        evaluation = _make_eval()
        opportunity = _make_opp()

        result = await computer.compute_features(
            evaluation=evaluation,
            opportunity=opportunity,
            underlying_bars=bars,
            spy_bars=spy_bars,
            days_to_earnings=30,
            recent_sec_filing=False,
        )

        assert result is not None
        assert result.evaluation_id == "e1"
        assert result.close is not None
        assert result.sma20 is not None

    @pytest.mark.asyncio
    async def test_fetches_bars_when_not_provided(self):
        """Test that bars are fetched from polygon when not provided."""
        polygon = MagicMock()
        bars = _make_bars(60)
        polygon.get_daily_bars_parsed = AsyncMock(return_value=bars)

        computer = FeatureComputer(polygon_client=polygon)
        evaluation = _make_eval()
        opportunity = _make_opp()

        result = await computer.compute_features(
            evaluation=evaluation,
            opportunity=opportunity,
        )

        assert result is not None
        # Should have called polygon for underlying bars and SPY bars
        assert polygon.get_daily_bars_parsed.call_count == 2

    @pytest.mark.asyncio
    async def test_with_catalyst_service(self):
        """Test integration with catalyst service."""
        polygon = MagicMock()
        bars = _make_bars(60)
        polygon.get_daily_bars_parsed = AsyncMock(return_value=bars)

        catalyst = MagicMock()
        catalyst.get_days_to_earnings = AsyncMock(return_value=15)
        catalyst.get_recent_sec_filing = AsyncMock(return_value=True)

        computer = FeatureComputer(polygon_client=polygon, catalyst_service=catalyst)
        evaluation = _make_eval()
        opportunity = _make_opp()

        result = await computer.compute_features(
            evaluation=evaluation,
            opportunity=opportunity,
        )

        assert result.days_to_earnings == 15
        assert result.recent_sec_filing is True


class TestFeatureComputerBatch:

    @pytest.mark.asyncio
    async def test_empty_evaluations(self):
        polygon = MagicMock()
        computer = FeatureComputer(polygon_client=polygon)
        result = await computer.compute_features_batch([], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_with_one_evaluation(self):
        polygon = MagicMock()
        bars = _make_bars(60)
        polygon.get_daily_bars_batch = AsyncMock(return_value={
            "AAPL": bars,
            "SPY": bars,
        })

        computer = FeatureComputer(polygon_client=polygon)
        evaluation = _make_eval()
        opportunity = _make_opp()

        result = await computer.compute_features_batch(
            evaluations=[evaluation],
            opportunities=[opportunity],
        )

        assert len(result) == 1
        assert result[0].evaluation_id == "e1"

    @pytest.mark.asyncio
    async def test_batch_no_matching_opportunity(self):
        polygon = MagicMock()
        bars = _make_bars(60)
        polygon.get_daily_bars_batch = AsyncMock(return_value={
            "AAPL": bars,
            "SPY": bars,
        })

        computer = FeatureComputer(polygon_client=polygon)
        evaluation = _make_eval(ticker="TSLA")  # Different ticker
        opportunity = _make_opp(ticker="AAPL")

        result = await computer.compute_features_batch(
            evaluations=[evaluation],
            opportunities=[opportunity],
        )

        assert len(result) == 0  # No matching opportunity


class TestFeatureComputerClearCache:

    def test_clear_cache(self):
        polygon = MagicMock()
        computer = FeatureComputer(polygon_client=polygon)
        computer._underlying_bars_cache["AAPL"] = [_make_bars(1)]
        computer._spy_bars = _make_bars(1)
        computer.clear_cache()
        assert computer._underlying_bars_cache == {}
        assert computer._spy_bars is None

    def test_clear_cache_with_catalyst(self):
        polygon = MagicMock()
        catalyst = MagicMock()
        computer = FeatureComputer(polygon_client=polygon, catalyst_service=catalyst)
        computer.clear_cache()
        catalyst.clear_cache.assert_called_once()
