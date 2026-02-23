"""Tests for backtest metrics calculator, equity curve, and segmented analysis."""

import math

import pytest

from app.backtest.equity_curve import generate_equity_curve, generate_monthly_pnl
from app.backtest.metrics import (
    _compute_drawdown,
    _compute_daily_returns,
    _compute_sharpe,
    calculate_metrics,
)
from app.backtest.segments import analyze_all_segments, analyze_segment
from app.core.schemas import BacktestTrade


def _make_trade(
    pnl_dollars: float = 50.0,
    pnl_pct: float = 25.0,
    entry_date: str = "2024-03-15",
    exit_date: str = "2024-03-20",
    days_held: int = 5,
    scanner_type: str = "breakout",
    verdict: str = "APPROVE",
    combined_score: float = 75.0,
    exit_reason: str = "PROFIT_TARGET",
    mfe_pct: float = 30.0,
    mae_pct: float = -10.0,
    market_regime: str = "bull_trend",
    **kwargs,
) -> BacktestTrade:
    """Create a BacktestTrade for testing."""
    return BacktestTrade(
        trade_id=kwargs.get("trade_id", "T001"),
        run_id="RUN001",
        entry_date=entry_date,
        exit_date=exit_date,
        ticker="AAPL",
        option_ticker="O:AAPL240315C00150000",
        option_type="CALL",
        strike=150.0,
        expiration_date="2024-04-19",
        scanner_type=scanner_type,
        verdict=verdict,
        combined_score=combined_score,
        entry_price=2.00,
        exit_price=2.00 + (pnl_dollars if pnl_dollars else 0),
        exit_reason=exit_reason,
        pnl_dollars=pnl_dollars,
        pnl_pct=pnl_pct,
        days_held=days_held,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        peak_price=2.60,
        market_regime=market_regime,
    )


# ============================================================
# Metrics Calculator Tests
# ============================================================


class TestCalculateMetrics:
    def test_empty_trades(self):
        result = calculate_metrics([])
        assert result["total_trades"] == 0
        assert result["win_rate"] == 0
        assert result["sharpe_ratio"] is None

    def test_single_winner(self):
        trades = [_make_trade(pnl_dollars=100, pnl_pct=50)]
        result = calculate_metrics(trades, starting_capital=10_000)
        assert result["total_trades"] == 1
        assert result["winners"] == 1
        assert result["losers"] == 0
        assert result["win_rate"] == 100.0
        assert result["net_pnl"] == 100.0
        assert result["total_return_pct"] == 1.0

    def test_single_loser(self):
        trades = [_make_trade(pnl_dollars=-50, pnl_pct=-25)]
        result = calculate_metrics(trades, starting_capital=10_000)
        assert result["total_trades"] == 1
        assert result["winners"] == 0
        assert result["losers"] == 1
        assert result["win_rate"] == 0
        assert result["net_pnl"] == -50.0

    def test_mixed_trades(self):
        trades = [
            _make_trade(
                pnl_dollars=100, pnl_pct=50, exit_date="2024-03-18",
                trade_id="T1",
            ),
            _make_trade(
                pnl_dollars=-30, pnl_pct=-15, exit_date="2024-03-19",
                trade_id="T2", exit_reason="STOP_LOSS",
            ),
            _make_trade(
                pnl_dollars=60, pnl_pct=30, exit_date="2024-03-20",
                trade_id="T3",
            ),
            _make_trade(
                pnl_dollars=-20, pnl_pct=-10, exit_date="2024-03-21",
                trade_id="T4", exit_reason="TIME_EXIT",
            ),
        ]
        result = calculate_metrics(trades, starting_capital=10_000)
        assert result["total_trades"] == 4
        assert result["winners"] == 2
        assert result["losers"] == 2
        assert result["win_rate"] == 50.0
        assert result["net_pnl"] == 110.0
        assert result["profit_factor"] == round(160 / 50, 4)
        assert result["exit_reasons"]["PROFIT_TARGET"] == 2
        assert result["exit_reasons"]["STOP_LOSS"] == 1
        assert result["exit_reasons"]["TIME_EXIT"] == 1

    def test_profit_factor_no_losses(self):
        trades = [
            _make_trade(pnl_dollars=100, pnl_pct=50, trade_id="T1"),
            _make_trade(pnl_dollars=50, pnl_pct=25, trade_id="T2"),
        ]
        result = calculate_metrics(trades)
        assert result["profit_factor"] is None  # inf → None

    def test_trades_with_no_pnl_skipped(self):
        trades = [
            _make_trade(pnl_dollars=100, pnl_pct=50, trade_id="T1"),
            BacktestTrade(
                trade_id="T2",
                run_id="RUN001",
                entry_date="2024-03-15",
                exit_date=None,
                ticker="AAPL",
                option_ticker="O:AAPL240315C00150000",
                option_type="CALL",
                strike=150.0,
                expiration_date="2024-04-19",
                scanner_type="breakout",
                verdict="APPROVE",
                combined_score=75.0,
                entry_price=2.00,
                exit_price=None,
                pnl_dollars=None,
                pnl_pct=None,
            ),
        ]
        result = calculate_metrics(trades)
        assert result["total_trades"] == 1  # Only the one with P&L

    def test_mfe_mae_averages(self):
        trades = [
            _make_trade(mfe_pct=40, mae_pct=-10, trade_id="T1"),
            _make_trade(mfe_pct=20, mae_pct=-30, trade_id="T2"),
        ]
        result = calculate_metrics(trades)
        assert result["avg_mfe_pct"] == 30.0
        assert result["avg_mae_pct"] == -20.0


class TestDailyReturns:
    def test_empty(self):
        assert _compute_daily_returns([], 10000) == []

    def test_single_trade(self):
        trades = [_make_trade(pnl_dollars=100, exit_date="2024-03-20")]
        returns = _compute_daily_returns(trades, 10000)
        assert len(returns) == 1
        assert abs(returns[0] - 0.01) < 0.001  # 100/10000 = 1%

    def test_multi_day(self):
        trades = [
            _make_trade(
                pnl_dollars=100, exit_date="2024-03-18", trade_id="T1",
            ),
            _make_trade(
                pnl_dollars=-50, exit_date="2024-03-19", trade_id="T2",
            ),
        ]
        returns = _compute_daily_returns(trades, 10000)
        assert len(returns) == 2
        assert abs(returns[0] - 0.01) < 0.001
        # Day 2: capital is 10100, pnl is -50
        assert abs(returns[1] - (-50 / 10100)) < 0.001


class TestSharpe:
    def test_too_few_returns(self):
        assert math.isnan(_compute_sharpe([]))
        assert math.isnan(_compute_sharpe([0.01]))

    def test_positive_returns(self):
        returns = [0.01, 0.02, 0.01, 0.015, 0.01]
        sharpe = _compute_sharpe(returns)
        assert sharpe > 0  # Positive returns → positive Sharpe

    def test_zero_variance(self):
        returns = [0.01, 0.01, 0.01]
        assert math.isnan(_compute_sharpe(returns))


class TestDrawdown:
    def test_no_trades(self):
        dd, duration = _compute_drawdown([], 10000)
        assert dd == 0
        assert duration == 0

    def test_only_winners(self):
        trades = [
            _make_trade(
                pnl_dollars=100, exit_date="2024-03-18", trade_id="T1",
            ),
            _make_trade(
                pnl_dollars=100, exit_date="2024-03-19", trade_id="T2",
            ),
        ]
        dd, duration = _compute_drawdown(trades, 10000)
        assert dd == 0  # No drawdown with only winners

    def test_drawdown_recovery(self):
        trades = [
            _make_trade(
                pnl_dollars=-500, exit_date="2024-03-18", trade_id="T1",
            ),
            _make_trade(
                pnl_dollars=600, exit_date="2024-03-25", trade_id="T2",
            ),
        ]
        dd, duration = _compute_drawdown(trades, 10000)
        assert dd == 5.0  # 500/10000 = 5%
        assert duration == 7  # 7 days from 03-18 to 03-25


# ============================================================
# Equity Curve Tests
# ============================================================


class TestEquityCurve:
    def test_empty(self):
        assert generate_equity_curve([]) == []

    def test_single_trade(self):
        trades = [
            _make_trade(
                pnl_dollars=100,
                entry_date="2024-03-18",
                exit_date="2024-03-20",
            ),
        ]
        curve = generate_equity_curve(trades, starting_capital=10_000)
        assert len(curve) > 0
        # Find the exit day
        exit_day = [p for p in curve if p["date"] == "2024-03-20"]
        assert len(exit_day) == 1
        assert exit_day[0]["equity"] == 10_100.0
        assert exit_day[0]["trades_closed"] == 1

    def test_drawdown_tracked(self):
        trades = [
            _make_trade(
                pnl_dollars=-200,
                entry_date="2024-03-18",
                exit_date="2024-03-18",
                trade_id="T1",
            ),
            _make_trade(
                pnl_dollars=400,
                entry_date="2024-03-19",
                exit_date="2024-03-19",
                trade_id="T2",
            ),
        ]
        curve = generate_equity_curve(trades, starting_capital=10_000)
        # After first day: equity 9800, drawdown = 200/10000 = 2%
        day1 = [p for p in curve if p["date"] == "2024-03-18"][0]
        assert day1["equity"] == 9_800.0
        assert day1["drawdown_pct"] == 2.0

    def test_skips_weekends(self):
        trades = [
            _make_trade(
                pnl_dollars=100,
                entry_date="2024-03-18",  # Monday
                exit_date="2024-03-22",  # Friday
            ),
        ]
        curve = generate_equity_curve(trades, starting_capital=10_000)
        dates = [p["date"] for p in curve]
        # Should not contain Saturday (03-23) or Sunday (03-24)
        assert "2024-03-23" not in dates
        assert "2024-03-24" not in dates


class TestMonthlyPnl:
    def test_empty(self):
        assert generate_monthly_pnl([]) == []

    def test_single_month(self):
        trades = [
            _make_trade(
                pnl_dollars=100, exit_date="2024-03-18", trade_id="T1",
            ),
            _make_trade(
                pnl_dollars=-30, exit_date="2024-03-20", trade_id="T2",
            ),
        ]
        result = generate_monthly_pnl(trades)
        assert len(result) == 1
        assert result[0]["month"] == "2024-03"
        assert result[0]["pnl"] == 70.0
        assert result[0]["trades"] == 2
        assert result[0]["winners"] == 1
        assert result[0]["losers"] == 1
        assert result[0]["win_rate"] == 50.0

    def test_multi_month(self):
        trades = [
            _make_trade(
                pnl_dollars=100, exit_date="2024-03-18", trade_id="T1",
            ),
            _make_trade(
                pnl_dollars=200, exit_date="2024-04-15", trade_id="T2",
            ),
        ]
        result = generate_monthly_pnl(trades)
        assert len(result) == 2
        assert result[0]["month"] == "2024-03"
        assert result[1]["month"] == "2024-04"


# ============================================================
# Segmented Analysis Tests
# ============================================================


class TestSegmentedAnalysis:
    def _diverse_trades(self) -> list[BacktestTrade]:
        """Create a set of diverse trades for segment testing."""
        return [
            _make_trade(
                pnl_dollars=100, pnl_pct=50, scanner_type="breakout",
                verdict="APPROVE", combined_score=85, market_regime="bull_trend",
                days_held=3, exit_reason="PROFIT_TARGET", trade_id="T1",
                entry_date="2024-03-15", exit_date="2024-03-18",
            ),
            _make_trade(
                pnl_dollars=-30, pnl_pct=-15, scanner_type="compression",
                verdict="WATCH", combined_score=65, market_regime="high_vol",
                days_held=8, exit_reason="STOP_LOSS", trade_id="T2",
                entry_date="2024-03-20", exit_date="2024-03-28",
            ),
            _make_trade(
                pnl_dollars=60, pnl_pct=30, scanner_type="breakout",
                verdict="APPROVE", combined_score=92, market_regime="bull_trend",
                days_held=15, exit_reason="PROFIT_TARGET", trade_id="T3",
                entry_date="2024-04-01", exit_date="2024-04-16",
            ),
            _make_trade(
                pnl_dollars=-20, pnl_pct=-10, scanner_type="cheap_options",
                verdict="APPROVE", combined_score=72, market_regime="choppy",
                days_held=21, exit_reason="MAX_HOLDING", trade_id="T4",
                entry_date="2024-04-10", exit_date="2024-05-01",
            ),
        ]

    def test_scanner_segments(self):
        trades = self._diverse_trades()
        result = analyze_segment(trades, "scanner")
        scanners = {s["segment"]: s for s in result}
        assert "breakout" in scanners
        assert scanners["breakout"]["trades"] == 2
        assert scanners["breakout"]["winners"] == 2
        assert "compression" in scanners
        assert scanners["compression"]["trades"] == 1

    def test_verdict_segments(self):
        trades = self._diverse_trades()
        result = analyze_segment(trades, "verdict")
        verdicts = {s["segment"]: s for s in result}
        assert verdicts["APPROVE"]["trades"] == 3
        assert verdicts["WATCH"]["trades"] == 1

    def test_score_bucket_segments(self):
        trades = self._diverse_trades()
        result = analyze_segment(trades, "score_bucket")
        buckets = {s["segment"]: s for s in result}
        assert "90+" in buckets
        assert "80-89" in buckets
        assert "70-79" in buckets
        assert "60-69" in buckets

    def test_regime_segments(self):
        trades = self._diverse_trades()
        result = analyze_segment(trades, "regime")
        regimes = {s["segment"]: s for s in result}
        assert regimes["bull_trend"]["trades"] == 2
        assert "high_vol" in regimes
        assert "choppy" in regimes

    def test_holding_period_segments(self):
        trades = self._diverse_trades()
        result = analyze_segment(trades, "holding_period")
        periods = {s["segment"]: s for s in result}
        assert "1-5d" in periods
        assert "6-10d" in periods
        assert "11-20d" in periods
        assert "20+d" in periods

    def test_exit_reason_segments(self):
        trades = self._diverse_trades()
        result = analyze_segment(trades, "exit_reason")
        reasons = {s["segment"]: s for s in result}
        assert reasons["PROFIT_TARGET"]["trades"] == 2
        assert reasons["STOP_LOSS"]["trades"] == 1
        assert reasons["MAX_HOLDING"]["trades"] == 1

    def test_month_segments(self):
        trades = self._diverse_trades()
        result = analyze_segment(trades, "month")
        months = {s["segment"]: s for s in result}
        assert "2024-03" in months
        assert "2024-04" in months

    def test_analyze_all_segments(self):
        trades = self._diverse_trades()
        result = analyze_all_segments(trades)
        assert len(result) == len(
            ["scanner", "verdict", "score_bucket", "regime",
             "month", "holding_period", "exit_reason"]
        )
        for seg_type, segments in result.items():
            assert len(segments) > 0

    def test_unknown_segment_type(self):
        with pytest.raises(ValueError, match="Unknown segment type"):
            analyze_segment([], "invalid_type")

    def test_empty_trades(self):
        result = analyze_segment([], "scanner")
        assert result == []

    def test_segment_metrics_accuracy(self):
        """Verify per-segment metric calculations."""
        trades = [
            _make_trade(
                pnl_dollars=100, pnl_pct=50, scanner_type="breakout",
                trade_id="T1",
            ),
            _make_trade(
                pnl_dollars=-40, pnl_pct=-20, scanner_type="breakout",
                trade_id="T2", exit_reason="STOP_LOSS",
            ),
        ]
        result = analyze_segment(trades, "scanner")
        assert len(result) == 1
        seg = result[0]
        assert seg["trades"] == 2
        assert seg["win_rate"] == 50.0
        assert seg["net_pnl"] == 60.0
        assert seg["profit_factor"] == round(100 / 40, 4)
        assert seg["avg_return_pct"] == 15.0  # (50 + -20) / 2
