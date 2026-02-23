"""Go-Live Readiness Assessment.

Structured pass/fail evaluation against criteria that determine whether
the backtest results support transitioning to live trading.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core.schemas import BacktestTrade

logger = logging.getLogger(__name__)

# Default readiness thresholds
DEFAULT_CRITERIA = {
    "min_sharpe": 1.5,
    "min_profit_factor": 1.8,
    "max_drawdown_pct": 10.0,
    "min_trades": 300,
    "min_profitable_months_pct": 77.8,  # 7/9 months
    "min_scanner_pf": 1.0,  # No scanner with PF < 1.0
    "min_profitable_regimes_pct": 75.0,  # 3/4 regimes
}


def assess_readiness(
    summary: dict[str, Any],
    trades: list[BacktestTrade],
    criteria: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Evaluate backtest results against go-live readiness criteria.

    Args:
        summary: Metrics summary from calculate_metrics().
        trades: Full list of backtest trades.
        criteria: Custom thresholds (defaults to DEFAULT_CRITERIA).

    Returns:
        Readiness assessment with per-criterion results and overall verdict.
    """
    c = {**DEFAULT_CRITERIA, **(criteria or {})}
    checks: list[dict[str, Any]] = []

    # 1. Sharpe ratio
    sharpe = summary.get("sharpe_ratio")
    checks.append({
        "criterion": "Sharpe Ratio",
        "threshold": f">= {c['min_sharpe']}",
        "actual": sharpe,
        "passed": sharpe is not None and sharpe >= c["min_sharpe"],
        "severity": "critical",
    })

    # 2. Profit factor
    pf = summary.get("profit_factor")
    checks.append({
        "criterion": "Profit Factor",
        "threshold": f">= {c['min_profit_factor']}",
        "actual": pf,
        "passed": pf is not None and pf >= c["min_profit_factor"],
        "severity": "critical",
    })

    # 3. Max drawdown
    dd = summary.get("max_drawdown_pct", 0)
    checks.append({
        "criterion": "Max Drawdown",
        "threshold": f"<= {c['max_drawdown_pct']}%",
        "actual": dd,
        "passed": dd <= c["max_drawdown_pct"],
        "severity": "critical",
    })

    # 4. Minimum trades
    total_trades = summary.get("total_trades", 0)
    checks.append({
        "criterion": "Minimum Trades",
        "threshold": f">= {int(c['min_trades'])}",
        "actual": total_trades,
        "passed": total_trades >= c["min_trades"],
        "severity": "warning",
    })

    # 5. Monthly profitability
    monthly_check = _check_monthly_profitability(trades, c["min_profitable_months_pct"])
    checks.append(monthly_check)

    # 6. Scanner profitability
    scanner_check = _check_scanner_profitability(trades, c["min_scanner_pf"])
    checks.append(scanner_check)

    # 7. Regime profitability
    regime_check = _check_regime_profitability(trades, c["min_profitable_regimes_pct"])
    checks.append(regime_check)

    # Overall verdict
    critical_passed = all(ch["passed"] for ch in checks if ch["severity"] == "critical")
    all_passed = all(ch["passed"] for ch in checks)

    if all_passed:
        verdict = "READY"
        message = "All readiness criteria met. System is ready for live trading."
    elif critical_passed:
        verdict = "CONDITIONAL"
        message = "Critical criteria met but some warnings remain. Review before going live."
    else:
        verdict = "NOT_READY"
        failed = [ch["criterion"] for ch in checks if not ch["passed"]]
        message = f"Failed criteria: {', '.join(failed)}. Continue optimizing."

    return {
        "verdict": verdict,
        "message": message,
        "checks": checks,
        "passed_count": sum(1 for ch in checks if ch["passed"]),
        "total_checks": len(checks),
        "criteria_used": c,
    }


def _check_monthly_profitability(
    trades: list[BacktestTrade],
    min_pct: float,
) -> dict[str, Any]:
    """Check if enough months are profitable."""
    from app.backtest.equity_curve import generate_monthly_pnl

    monthly = generate_monthly_pnl(trades)
    total_months = len(monthly)
    profitable_months = sum(1 for m in monthly if m["pnl"] > 0)

    if total_months == 0:
        return {
            "criterion": "Monthly Profitability",
            "threshold": f">= {min_pct}% of months profitable",
            "actual": "No months with data",
            "passed": False,
            "severity": "warning",
        }

    pct = (profitable_months / total_months) * 100
    return {
        "criterion": "Monthly Profitability",
        "threshold": f">= {min_pct}% of months profitable",
        "actual": f"{profitable_months}/{total_months} ({pct:.1f}%)",
        "passed": pct >= min_pct,
        "severity": "warning",
    }


def _check_scanner_profitability(
    trades: list[BacktestTrade],
    min_pf: float,
) -> dict[str, Any]:
    """Check that no scanner has profit factor below threshold."""
    from app.backtest.segments import analyze_segment

    scanner_segments = analyze_segment(trades, "scanner")
    if not scanner_segments:
        return {
            "criterion": "Scanner Profitability",
            "threshold": f"All scanners PF >= {min_pf}",
            "actual": "No scanner data",
            "passed": False,
            "severity": "warning",
        }

    failing = [
        s for s in scanner_segments
        if s.get("profit_factor") is not None and s["profit_factor"] < min_pf
    ]

    if failing:
        names = [s["segment"] for s in failing]
        return {
            "criterion": "Scanner Profitability",
            "threshold": f"All scanners PF >= {min_pf}",
            "actual": f"Failing: {', '.join(names)}",
            "passed": False,
            "severity": "warning",
        }

    return {
        "criterion": "Scanner Profitability",
        "threshold": f"All scanners PF >= {min_pf}",
        "actual": f"All {len(scanner_segments)} scanners pass",
        "passed": True,
        "severity": "warning",
    }


def _check_regime_profitability(
    trades: list[BacktestTrade],
    min_pct: float,
) -> dict[str, Any]:
    """Check profitability across market regimes."""
    from app.backtest.segments import analyze_segment

    regime_segments = analyze_segment(trades, "regime")
    if not regime_segments:
        return {
            "criterion": "Regime Profitability",
            "threshold": f">= {min_pct}% of regimes profitable",
            "actual": "No regime data",
            "passed": False,
            "severity": "warning",
        }

    total = len(regime_segments)
    profitable = sum(1 for s in regime_segments if s.get("net_pnl", 0) > 0)
    pct = (profitable / total * 100) if total > 0 else 0

    return {
        "criterion": "Regime Profitability",
        "threshold": f">= {min_pct}% of regimes profitable",
        "actual": f"{profitable}/{total} ({pct:.1f}%)",
        "passed": pct >= min_pct,
        "severity": "warning",
    }
