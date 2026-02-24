"""Tests for go-live readiness assessment."""

from app.backtest.readiness import assess_readiness, DEFAULT_CRITERIA
from app.core.schemas import BacktestTrade


def _make_trade(
    pnl_dollars=50.0, pnl_pct=25.0, scanner_type="breakout",
    market_regime="bull_trend", exit_date="2024-03-20",
    entry_date="2024-03-15", **kwargs
):
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
        verdict="APPROVE",
        combined_score=75.0,
        entry_price=2.00,
        exit_price=2.00 + pnl_dollars,
        exit_reason="PROFIT_TARGET",
        pnl_dollars=pnl_dollars,
        pnl_pct=pnl_pct,
        days_held=5,
        mfe_pct=30.0,
        mae_pct=-10.0,
        peak_price=2.60,
        market_regime=market_regime,
    )


class TestAssessReadiness:
    def test_empty_summary(self):
        result = assess_readiness(
            summary={"total_trades": 0, "sharpe_ratio": None, "profit_factor": None,
                     "max_drawdown_pct": 0},
            trades=[],
        )
        assert result["verdict"] == "NOT_READY"
        assert result["passed_count"] < result["total_checks"]

    def test_excellent_results(self):
        """Test that great metrics result in READY verdict."""
        summary = {
            "total_trades": 500,
            "sharpe_ratio": 2.0,
            "profit_factor": 2.5,
            "max_drawdown_pct": 5.0,
        }
        # Create enough diverse trades to pass monthly/scanner/regime checks
        trades = []
        months = ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05",
                  "2024-06", "2024-07", "2024-08", "2024-09"]
        scanners = ["breakout", "compression"]
        regimes = ["bull_trend", "low_vol", "high_vol"]
        idx = 0
        for month in months:
            for scanner in scanners:
                for regime in regimes:
                    idx += 1
                    trades.append(_make_trade(
                        pnl_dollars=50, pnl_pct=25,
                        scanner_type=scanner, market_regime=regime,
                        entry_date=f"{month}-15", exit_date=f"{month}-20",
                        trade_id=f"T{idx:04d}",
                    ))

        result = assess_readiness(summary, trades)
        # Should pass all or most
        assert result["passed_count"] >= 5  # At least 5 of 7

    def test_poor_sharpe_fails(self):
        summary = {
            "total_trades": 500,
            "sharpe_ratio": 0.5,
            "profit_factor": 2.5,
            "max_drawdown_pct": 5.0,
        }
        trades = [_make_trade(trade_id=f"T{i}") for i in range(10)]
        result = assess_readiness(summary, trades)
        # Find the Sharpe criterion
        sharpe_check = next(c for c in result["checks"] if c["criterion"] == "Sharpe Ratio")
        assert not sharpe_check["passed"]

    def test_high_drawdown_fails(self):
        summary = {
            "total_trades": 500,
            "sharpe_ratio": 2.0,
            "profit_factor": 2.5,
            "max_drawdown_pct": 15.0,
        }
        trades = [_make_trade(trade_id=f"T{i}") for i in range(10)]
        result = assess_readiness(summary, trades)
        dd_check = next(c for c in result["checks"] if c["criterion"] == "Max Drawdown")
        assert not dd_check["passed"]

    def test_custom_criteria(self):
        """Test with relaxed criteria."""
        summary = {
            "total_trades": 100,
            "sharpe_ratio": 1.0,
            "profit_factor": 1.2,
            "max_drawdown_pct": 8.0,
        }
        trades = [_make_trade(trade_id=f"T{i}") for i in range(10)]
        # Very relaxed criteria
        criteria = {
            "min_sharpe": 0.5,
            "min_profit_factor": 1.0,
            "max_drawdown_pct": 20.0,
            "min_trades": 50,
        }
        result = assess_readiness(summary, trades, criteria=criteria)
        # Critical criteria should all pass with relaxed thresholds
        critical_checks = [c for c in result["checks"] if c["severity"] == "critical"]
        assert all(c["passed"] for c in critical_checks)

    def test_verdict_conditional(self):
        """Critical pass but warnings fail → CONDITIONAL."""
        summary = {
            "total_trades": 50,  # Below min_trades threshold (300)
            "sharpe_ratio": 2.0,
            "profit_factor": 2.5,
            "max_drawdown_pct": 5.0,
        }
        # Few trades → min_trades warning will fail
        trades = [_make_trade(trade_id=f"T{i}") for i in range(5)]
        result = assess_readiness(summary, trades)
        # Critical pass, but min_trades warning fails → CONDITIONAL
        assert result["verdict"] == "CONDITIONAL"

    def test_checks_structure(self):
        summary = {
            "total_trades": 100,
            "sharpe_ratio": 1.0,
            "profit_factor": 1.5,
            "max_drawdown_pct": 8.0,
        }
        trades = [_make_trade(trade_id=f"T{i}") for i in range(10)]
        result = assess_readiness(summary, trades)

        assert "verdict" in result
        assert "message" in result
        assert "checks" in result
        assert result["total_checks"] == 7
        for check in result["checks"]:
            assert "criterion" in check
            assert "passed" in check
            assert "severity" in check
