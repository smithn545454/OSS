"""Tests for backtest exit rules, exit resolver, and run config models."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.schemas import (
    BacktestExitConfig,
    BacktestRun,
    BacktestRunConfig,
    BacktestTrade,
    ExitReason,
    PaperPosition,
    PolicyConfig,
)
from app.paper_trading.exit_checker import (
    calculate_dte_from_expiration,
    check_exit_conditions,
)
from app.backtest.exit_resolver import (
    apply_entry_slippage,
    resolve_exit,
    _next_trading_day,
)


# ============================================================================
# Exit Checker — New Exit Reasons
# ============================================================================


def _make_position(entry_price: float = 5.0) -> PaperPosition:
    """Create a minimal PaperPosition for testing."""
    return PaperPosition(
        position_id="test-pos",
        evaluation_id="test-eval",
        option_ticker="O:AAPL260320C00185000",
        underlying_ticker="AAPL",
        entry_price=entry_price,
        entry_date="2026-01-20",
        status="OPEN",
        verdict_at_entry="APPROVE",
        current_price=entry_price,
        current_pnl_pct=0.0,
    )


class TestExitCheckerExtensions:
    """Test the new MAX_HOLDING and TRAILING_STOP exit reasons."""

    def test_profit_target_with_backtest_config(self):
        pos = _make_position(entry_price=5.0)
        config = BacktestExitConfig(profit_target_pct=100.0)
        result = check_exit_conditions(
            pos, current_price=10.0, current_dte=30,
            backtest_exit_config=config,
        )
        assert result == ExitReason.PROFIT_TARGET

    def test_stop_loss_with_backtest_config(self):
        pos = _make_position(entry_price=5.0)
        config = BacktestExitConfig(stop_loss_pct=50.0)
        result = check_exit_conditions(
            pos, current_price=2.0, current_dte=30,
            backtest_exit_config=config,
        )
        assert result == ExitReason.STOP_LOSS

    def test_max_holding_triggers(self):
        pos = _make_position(entry_price=5.0)
        config = BacktestExitConfig(max_holding_days=21)
        result = check_exit_conditions(
            pos, current_price=5.5, current_dte=30,
            days_held=21, backtest_exit_config=config,
        )
        assert result == ExitReason.MAX_HOLDING

    def test_max_holding_not_triggered(self):
        pos = _make_position(entry_price=5.0)
        config = BacktestExitConfig(max_holding_days=21)
        result = check_exit_conditions(
            pos, current_price=5.5, current_dte=30,
            days_held=15, backtest_exit_config=config,
        )
        assert result is None

    def test_trailing_stop_triggers(self):
        pos = _make_position(entry_price=5.0)
        config = BacktestExitConfig(trailing_stop_pct=30.0)
        # Peak was 8.0, current is 5.0 => 37.5% drawdown
        result = check_exit_conditions(
            pos, current_price=5.0, current_dte=30,
            peak_price=8.0, backtest_exit_config=config,
        )
        assert result == ExitReason.TRAILING_STOP

    def test_trailing_stop_not_triggered(self):
        pos = _make_position(entry_price=5.0)
        config = BacktestExitConfig(trailing_stop_pct=30.0)
        # Peak was 6.0, current is 5.0 => 16.7% drawdown
        result = check_exit_conditions(
            pos, current_price=5.0, current_dte=30,
            peak_price=6.0, backtest_exit_config=config,
        )
        assert result is None

    def test_trailing_stop_disabled_when_none(self):
        pos = _make_position(entry_price=5.0)
        config = BacktestExitConfig(trailing_stop_pct=None)
        result = check_exit_conditions(
            pos, current_price=3.0, current_dte=30,
            peak_price=10.0, backtest_exit_config=config,
        )
        # Should not trigger trailing stop since it's disabled
        # (but might trigger stop_loss if drop >= 50%)
        assert result != ExitReason.TRAILING_STOP

    def test_priority_order_profit_over_max_holding(self):
        pos = _make_position(entry_price=5.0)
        config = BacktestExitConfig(profit_target_pct=100.0, max_holding_days=5)
        result = check_exit_conditions(
            pos, current_price=11.0, current_dte=30,
            days_held=10, backtest_exit_config=config,
        )
        # Profit target should fire before max holding
        assert result == ExitReason.PROFIT_TARGET

    def test_priority_order_stop_loss_over_trailing(self):
        pos = _make_position(entry_price=5.0)
        config = BacktestExitConfig(
            stop_loss_pct=50.0, trailing_stop_pct=20.0,
        )
        result = check_exit_conditions(
            pos, current_price=2.0, current_dte=30,
            peak_price=5.0, backtest_exit_config=config,
        )
        assert result == ExitReason.STOP_LOSS

    def test_backwards_compatible_without_backtest_config(self):
        """Existing paper trading behavior preserved."""
        pos = _make_position(entry_price=5.0)
        result = check_exit_conditions(
            pos, current_price=8.0, current_dte=30,
        )
        # Default TrackingConfig has profit_target_pct=50.0
        # 60% gain >= 50% target
        assert result == ExitReason.PROFIT_TARGET


class TestCalculateDteFromExpiration:
    def test_with_as_of_date(self):
        dte = calculate_dte_from_expiration(
            "2026-03-20", as_of_date=date(2026, 1, 20),
        )
        assert dte == 59  # Jan 20 to Mar 20

    def test_negative_dte(self):
        dte = calculate_dte_from_expiration(
            "2026-01-10", as_of_date=date(2026, 1, 20),
        )
        assert dte == -10

    def test_invalid_date(self):
        dte = calculate_dte_from_expiration("not-a-date")
        assert dte == 999


# ============================================================================
# Entry Slippage
# ============================================================================


class TestEntrySlippage:
    def test_mid_model(self):
        price = apply_entry_slippage(5.4, 5.2, "mid", 0.05)
        assert price == 5.2

    def test_ask_model(self):
        price = apply_entry_slippage(5.4, 5.2, "ask", 0.05)
        assert price == 5.4

    def test_ask_plus_pct_model(self):
        price = apply_entry_slippage(5.4, 5.2, "ask_plus_pct", 0.05)
        assert price == pytest.approx(5.67)

    def test_unknown_model_defaults_to_ask(self):
        price = apply_entry_slippage(5.4, 5.2, "unknown", 0.05)
        assert price == 5.4


# ============================================================================
# Exit Resolver
# ============================================================================


def _make_mock_data_provider(
    prices_by_date: dict[date, float] | None = None,
) -> AsyncMock:
    """Build a mock DataProvider that returns contract prices by date."""
    provider = AsyncMock()

    if prices_by_date is None:
        prices_by_date = {}

    async def mock_chain(ticker, as_of, min_dte=0, max_dte=365):
        price = prices_by_date.get(as_of)
        if price is None:
            return []
        bid = price * 0.98
        ask = price * 1.02
        return [{
            "details": {
                "ticker": "O:AAPL260320C00185000",
                "strike_price": 185.0,
                "expiration_date": "2026-03-20",
                "contract_type": "call",
            },
            "day": {"close": price},
            "last_quote": {"bid": bid, "ask": ask},
            "open_interest": 5000,
        }]

    async def mock_contract_price(ticker, strike, expiration_date, option_type, as_of):
        price = prices_by_date.get(as_of)
        if price is None:
            return None
        bid = price * 0.98
        ask = price * 1.02
        return (bid + ask) / 2

    provider.get_options_chain = AsyncMock(side_effect=mock_chain)
    provider.get_contract_price = AsyncMock(side_effect=mock_contract_price)
    return provider


class TestExitResolver:
    @pytest.mark.asyncio
    async def test_profit_target_hit(self):
        """Trade should exit when profit target is reached."""
        entry = date(2026, 1, 20)
        prices = {
            date(2026, 1, 21): 5.5,
            date(2026, 1, 22): 6.0,
            date(2026, 1, 23): 11.0,  # +100% from 5.0 entry
        }
        provider = _make_mock_data_provider(prices)
        config = BacktestExitConfig(profit_target_pct=100.0)

        trade = await resolve_exit(
            data_provider=provider, entry_date=entry, entry_price=5.0,
            option_ticker="O:AAPL260320C00185000",
            underlying_ticker="AAPL", option_type="CALL",
            strike=185.0, expiration_date="2026-03-20",
            exit_config=config, scanner_type="BREAKOUT",
            verdict="APPROVE", combined_score=75.0, run_id="test-run",
        )

        assert trade.exit_reason == "PROFIT_TARGET"
        assert trade.exit_date == "2026-01-23"
        assert trade.pnl_pct > 0

    @pytest.mark.asyncio
    async def test_stop_loss_hit(self):
        """Trade should exit when stop loss is reached."""
        entry = date(2026, 1, 20)
        prices = {
            date(2026, 1, 21): 4.0,
            date(2026, 1, 22): 2.0,  # -60% from 5.0 entry
        }
        provider = _make_mock_data_provider(prices)
        config = BacktestExitConfig(stop_loss_pct=50.0)

        trade = await resolve_exit(
            data_provider=provider, entry_date=entry, entry_price=5.0,
            option_ticker="O:AAPL260320C00185000",
            underlying_ticker="AAPL", option_type="CALL",
            strike=185.0, expiration_date="2026-03-20",
            exit_config=config, scanner_type="BREAKOUT",
            verdict="APPROVE", combined_score=75.0, run_id="test-run",
        )

        assert trade.exit_reason == "STOP_LOSS"
        assert trade.pnl_pct < 0

    @pytest.mark.asyncio
    async def test_max_holding_exit(self):
        """Trade should exit after max holding days."""
        entry = date(2026, 1, 2)
        # Price stays flat for many days
        prices = {}
        current = entry + timedelta(days=1)
        for i in range(40):
            if current.weekday() < 5:
                prices[current] = 5.0
            current += timedelta(days=1)

        provider = _make_mock_data_provider(prices)
        config = BacktestExitConfig(max_holding_days=10)

        trade = await resolve_exit(
            data_provider=provider, entry_date=entry, entry_price=5.0,
            option_ticker="O:AAPL260320C00185000",
            underlying_ticker="AAPL", option_type="CALL",
            strike=185.0, expiration_date="2026-03-20",
            exit_config=config, scanner_type="BREAKOUT",
            verdict="APPROVE", combined_score=75.0, run_id="test-run",
        )

        assert trade.exit_reason == "MAX_HOLDING"
        assert trade.days_held >= 10

    @pytest.mark.asyncio
    async def test_trailing_stop_exit(self):
        """Trade should exit when trailing stop fires."""
        entry = date(2026, 1, 20)
        prices = {
            date(2026, 1, 21): 6.0,
            date(2026, 1, 22): 7.0,
            date(2026, 1, 23): 8.0,  # Peak
            date(2026, 1, 26): 5.0,  # 37.5% drop from peak
        }
        provider = _make_mock_data_provider(prices)
        config = BacktestExitConfig(trailing_stop_pct=30.0)

        trade = await resolve_exit(
            data_provider=provider, entry_date=entry, entry_price=5.0,
            option_ticker="O:AAPL260320C00185000",
            underlying_ticker="AAPL", option_type="CALL",
            strike=185.0, expiration_date="2026-03-20",
            exit_config=config, scanner_type="BREAKOUT",
            verdict="APPROVE", combined_score=75.0, run_id="test-run",
        )

        assert trade.exit_reason == "TRAILING_STOP"
        assert trade.peak_price == pytest.approx(8.0, abs=0.2)

    @pytest.mark.asyncio
    async def test_expiration_exit(self):
        """Trade should exit at expiration if no other condition fires."""
        entry = date(2026, 3, 15)
        # Price stays flat through expiration
        prices = {
            date(2026, 3, 16): 5.0,
            date(2026, 3, 17): 5.0,
            date(2026, 3, 18): 5.0,
            date(2026, 3, 19): 5.0,
            date(2026, 3, 20): 5.0,  # Expiration day
        }
        provider = _make_mock_data_provider(prices)
        config = BacktestExitConfig(
            min_dte_at_exit=3,  # Should trigger TIME_EXIT at DTE<=3
        )

        trade = await resolve_exit(
            data_provider=provider, entry_date=entry, entry_price=5.0,
            option_ticker="O:AAPL260320C00185000",
            underlying_ticker="AAPL", option_type="CALL",
            strike=185.0, expiration_date="2026-03-20",
            exit_config=config, scanner_type="BREAKOUT",
            verdict="APPROVE", combined_score=75.0, run_id="test-run",
        )

        # Should exit with TIME_EXIT when DTE <= 3
        assert trade.exit_reason in ("TIME_EXIT", "EXPIRATION")

    @pytest.mark.asyncio
    async def test_mfe_mae_tracking(self):
        """Trade should track MFE and MAE correctly."""
        entry = date(2026, 1, 20)
        prices = {
            date(2026, 1, 21): 7.0,  # Up
            date(2026, 1, 22): 3.0,  # Down
            date(2026, 1, 23): 2.0,  # Stop loss at -60%
        }
        provider = _make_mock_data_provider(prices)
        config = BacktestExitConfig(stop_loss_pct=50.0)

        trade = await resolve_exit(
            data_provider=provider, entry_date=entry, entry_price=5.0,
            option_ticker="O:AAPL260320C00185000",
            underlying_ticker="AAPL", option_type="CALL",
            strike=185.0, expiration_date="2026-03-20",
            exit_config=config, scanner_type="BREAKOUT",
            verdict="APPROVE", combined_score=75.0, run_id="test-run",
        )

        # MFE should capture peak at 7.0 (40% above entry)
        assert trade.mfe_pct > 0
        # MAE should capture trough (loss from entry)
        assert trade.mae_pct >= 0

    @pytest.mark.asyncio
    async def test_no_data_days_skipped(self):
        """Days with no options data should be skipped gracefully."""
        entry = date(2026, 1, 20)
        prices = {
            # Gap on Jan 21 (no data)
            date(2026, 1, 22): 11.0,  # Profit target
        }
        provider = _make_mock_data_provider(prices)
        config = BacktestExitConfig(profit_target_pct=100.0)

        trade = await resolve_exit(
            data_provider=provider, entry_date=entry, entry_price=5.0,
            option_ticker="O:AAPL260320C00185000",
            underlying_ticker="AAPL", option_type="CALL",
            strike=185.0, expiration_date="2026-03-20",
            exit_config=config, scanner_type="BREAKOUT",
            verdict="APPROVE", combined_score=75.0, run_id="test-run",
        )

        assert trade.exit_reason == "PROFIT_TARGET"


# ============================================================================
# Date Helpers
# ============================================================================


class TestNextTradingDay:
    def test_weekday(self):
        assert _next_trading_day(date(2026, 1, 20)) == date(2026, 1, 21)

    def test_friday_to_monday(self):
        assert _next_trading_day(date(2026, 1, 23)) == date(2026, 1, 26)

    def test_saturday(self):
        assert _next_trading_day(date(2026, 1, 24)) == date(2026, 1, 26)


# ============================================================================
# Run Config Models
# ============================================================================


class TestBacktestModels:
    def test_exit_config_defaults(self):
        config = BacktestExitConfig()
        assert config.stop_loss_pct == 50.0
        assert config.profit_target_pct == 100.0
        assert config.max_holding_days == 21
        assert config.trailing_stop_pct is None

    def test_run_config_defaults(self):
        config = BacktestRunConfig(
            name="Test Run",
            start_date="2024-01-01",
            end_date="2024-12-31",
            policy_snapshot=PolicyConfig(),
        )
        assert len(config.scanners_enabled) == 4
        assert config.slippage_model == "ask_plus_pct"
        assert config.starting_capital == 10_000.0

    def test_backtest_run_creation(self):
        run = BacktestRun(
            run_id="test-123",
            config=BacktestRunConfig(
                name="Test",
                start_date="2024-01-01",
                end_date="2024-06-30",
                policy_snapshot=PolicyConfig(),
            ),
        )
        assert run.status == "PENDING"
        assert run.progress["days_completed"] == 0

    def test_backtest_trade_creation(self):
        trade = BacktestTrade(
            trade_id="t1",
            run_id="r1",
            entry_date="2024-01-15",
            ticker="AAPL",
            option_ticker="O:AAPL240315C00185000",
            option_type="CALL",
            strike=185.0,
            expiration_date="2024-03-15",
            scanner_type="BREAKOUT",
            verdict="APPROVE",
            combined_score=78.5,
            entry_price=5.0,
        )
        assert trade.exit_price is None
        assert trade.pnl_dollars is None
