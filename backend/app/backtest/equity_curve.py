"""Equity curve generator for backtest runs.

Builds a daily equity curve from trade lifecycle data,
tracking cumulative P&L, drawdown, and benchmark comparison.
"""

import logging
from datetime import datetime, timedelta

from app.core.schemas import BacktestTrade

logger = logging.getLogger(__name__)


def generate_equity_curve(
    trades: list[BacktestTrade],
    starting_capital: float = 10_000.0,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Build a daily equity curve from trade P&L.

    For each trading day in the range:
    - Realize P&L from trades that exit on that day
    - Track peak equity and current drawdown

    Args:
        trades: Completed trades with P&L data.
        starting_capital: Initial capital.
        start_date: Curve start date (YYYY-MM-DD). Defaults to first trade entry.
        end_date: Curve end date (YYYY-MM-DD). Defaults to last trade exit.

    Returns:
        List of daily equity data points.
    """
    if not trades:
        return []

    closed = [t for t in trades if t.pnl_dollars is not None and t.exit_date]
    if not closed:
        return []

    # Group realized P&L by exit date
    daily_pnl: dict[str, float] = {}
    daily_trade_count: dict[str, int] = {}
    for t in closed:
        exit_d = t.exit_date or ""
        daily_pnl[exit_d] = daily_pnl.get(exit_d, 0) + (t.pnl_dollars or 0)
        daily_trade_count[exit_d] = daily_trade_count.get(exit_d, 0) + 1

    # Determine date range
    all_entry_dates = [t.entry_date for t in closed]
    all_exit_dates = [t.exit_date for t in closed if t.exit_date]

    dt_start = datetime.strptime(
        start_date or min(all_entry_dates), "%Y-%m-%d"
    ).date()
    dt_end = datetime.strptime(
        end_date or max(all_exit_dates), "%Y-%m-%d"
    ).date()

    # Build daily curve
    curve: list[dict] = []
    equity = starting_capital
    peak_equity = starting_capital
    current = dt_start

    while current <= dt_end:
        date_str = current.strftime("%Y-%m-%d")

        # Skip weekends
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        # Realize P&L for trades that exit today
        day_pnl = daily_pnl.get(date_str, 0)
        equity += day_pnl

        # Track peak and drawdown
        if equity > peak_equity:
            peak_equity = equity
        drawdown_pct = (
            ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else 0
        )

        curve.append({
            "date": date_str,
            "equity": round(equity, 2),
            "daily_pnl": round(day_pnl, 2),
            "drawdown_pct": round(drawdown_pct, 2),
            "peak_equity": round(peak_equity, 2),
            "trades_closed": daily_trade_count.get(date_str, 0),
            "return_pct": round(
                ((equity - starting_capital) / starting_capital) * 100, 2
            ),
        })

        current += timedelta(days=1)

    return curve


def generate_monthly_pnl(trades: list[BacktestTrade]) -> list[dict]:
    """Compute monthly P&L aggregations from trades.

    Returns:
        List of monthly P&L records sorted by month.
    """
    if not trades:
        return []

    closed = [t for t in trades if t.pnl_dollars is not None and t.exit_date]
    if not closed:
        return []

    # Group by exit month
    monthly: dict[str, dict] = {}
    for t in closed:
        month_key = (t.exit_date or "")[:7]  # YYYY-MM
        if month_key not in monthly:
            monthly[month_key] = {
                "month": month_key,
                "pnl": 0.0,
                "trades": 0,
                "winners": 0,
                "losers": 0,
            }
        monthly[month_key]["pnl"] += t.pnl_dollars or 0
        monthly[month_key]["trades"] += 1
        if (t.pnl_dollars or 0) > 0:
            monthly[month_key]["winners"] += 1
        elif (t.pnl_dollars or 0) < 0:
            monthly[month_key]["losers"] += 1

    # Sort and compute win rates
    result = []
    for m in sorted(monthly.values(), key=lambda x: x["month"]):
        m["pnl"] = round(m["pnl"], 2)
        m["win_rate"] = (
            round(m["winners"] / m["trades"] * 100, 1) if m["trades"] > 0 else 0
        )
        result.append(m)

    return result
