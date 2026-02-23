"""Backtest metrics calculator.

Computes post-run aggregated metrics from BacktestTrade records:
- Core P&L metrics (net P&L, return %, win rate, profit factor)
- Risk metrics (Sharpe ratio, max drawdown, max DD duration)
- Trade quality metrics (expectancy, avg win/loss, MFE/MAE ratios)
"""

import logging
import math
from datetime import datetime

from app.core.schemas import BacktestTrade

logger = logging.getLogger(__name__)


def calculate_metrics(
    trades: list[BacktestTrade],
    starting_capital: float = 10_000.0,
) -> dict:
    """Calculate comprehensive metrics from a list of backtest trades.

    Args:
        trades: Completed trades with P&L data.
        starting_capital: Initial capital for return calculations.

    Returns:
        Dictionary of aggregated metrics.
    """
    if not trades:
        return _empty_metrics()

    # Separate winners and losers
    closed = [t for t in trades if t.pnl_dollars is not None]
    if not closed:
        return _empty_metrics()

    winners = [t for t in closed if (t.pnl_dollars or 0) > 0]
    losers = [t for t in closed if (t.pnl_dollars or 0) < 0]
    breakevens = [t for t in closed if (t.pnl_dollars or 0) == 0]

    # --- Core P&L ---
    total_pnl = sum(t.pnl_dollars or 0 for t in closed)
    gross_profit = sum(t.pnl_dollars or 0 for t in winners)
    gross_loss = abs(sum(t.pnl_dollars or 0 for t in losers))

    win_rate = len(winners) / len(closed) * 100 if closed else 0
    avg_win_pct = (
        sum(t.pnl_pct or 0 for t in winners) / len(winners) if winners else 0
    )
    avg_loss_pct = (
        sum(t.pnl_pct or 0 for t in losers) / len(losers) if losers else 0
    )
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # --- Return ---
    total_return_pct = (total_pnl / starting_capital) * 100

    # --- Risk Metrics ---
    daily_returns = _compute_daily_returns(closed, starting_capital)
    sharpe_ratio = _compute_sharpe(daily_returns)
    max_drawdown, max_dd_duration = _compute_drawdown(closed, starting_capital)

    # --- Trade Quality ---
    avg_days_held = (
        sum(t.days_held or 0 for t in closed) / len(closed) if closed else 0
    )
    avg_pnl_per_trade = total_pnl / len(closed) if closed else 0
    expectancy = (
        (win_rate / 100 * avg_win_pct) + ((1 - win_rate / 100) * avg_loss_pct)
    )

    # MFE/MAE averages
    mfe_trades = [t for t in closed if t.mfe_pct is not None]
    mae_trades = [t for t in closed if t.mae_pct is not None]
    avg_mfe = sum(t.mfe_pct or 0 for t in mfe_trades) / len(mfe_trades) if mfe_trades else 0
    avg_mae = sum(t.mae_pct or 0 for t in mae_trades) / len(mae_trades) if mae_trades else 0

    # --- Exit reason breakdown ---
    exit_reasons: dict[str, int] = {}
    for t in closed:
        reason = t.exit_reason or "UNKNOWN"
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    # --- Date range ---
    entry_dates = sorted(t.entry_date for t in closed)
    first_trade = entry_dates[0] if entry_dates else None
    last_trade = entry_dates[-1] if entry_dates else None

    # Trades per trading day
    unique_entry_dates = len(set(t.entry_date for t in closed))
    trades_per_day = len(closed) / unique_entry_dates if unique_entry_dates > 0 else 0

    return {
        # Core
        "total_trades": len(closed),
        "winners": len(winners),
        "losers": len(losers),
        "breakevens": len(breakevens),
        "win_rate": round(win_rate, 2),
        "net_pnl": round(total_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "total_return_pct": round(total_return_pct, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else None,
        # Averages
        "avg_win_pct": round(avg_win_pct, 2),
        "avg_loss_pct": round(avg_loss_pct, 2),
        "avg_pnl_per_trade": round(avg_pnl_per_trade, 2),
        "avg_days_held": round(avg_days_held, 1),
        "trades_per_day": round(trades_per_day, 2),
        # Risk
        "sharpe_ratio": round(sharpe_ratio, 4) if not math.isnan(sharpe_ratio) else None,
        "max_drawdown_pct": round(max_drawdown, 2),
        "max_drawdown_duration_days": max_dd_duration,
        # Quality
        "expectancy": round(expectancy, 2),
        "avg_mfe_pct": round(avg_mfe, 2),
        "avg_mae_pct": round(avg_mae, 2),
        # Breakdown
        "exit_reasons": exit_reasons,
        # Meta
        "starting_capital": starting_capital,
        "first_trade_date": first_trade,
        "last_trade_date": last_trade,
    }


def _empty_metrics() -> dict:
    """Return metrics structure with zero values."""
    return {
        "total_trades": 0,
        "winners": 0,
        "losers": 0,
        "breakevens": 0,
        "win_rate": 0,
        "net_pnl": 0,
        "gross_profit": 0,
        "gross_loss": 0,
        "total_return_pct": 0,
        "profit_factor": None,
        "avg_win_pct": 0,
        "avg_loss_pct": 0,
        "avg_pnl_per_trade": 0,
        "avg_days_held": 0,
        "trades_per_day": 0,
        "sharpe_ratio": None,
        "max_drawdown_pct": 0,
        "max_drawdown_duration_days": 0,
        "expectancy": 0,
        "avg_mfe_pct": 0,
        "avg_mae_pct": 0,
        "exit_reasons": {},
        "starting_capital": 10_000.0,
        "first_trade_date": None,
        "last_trade_date": None,
    }


def _compute_daily_returns(
    trades: list[BacktestTrade],
    starting_capital: float,
) -> list[float]:
    """Compute daily portfolio returns from trade P&L.

    Groups trade P&L by exit date to get daily realized returns.
    """
    if not trades:
        return []

    # Group realized P&L by exit date
    daily_pnl: dict[str, float] = {}
    for t in trades:
        if t.exit_date and t.pnl_dollars is not None:
            daily_pnl[t.exit_date] = daily_pnl.get(t.exit_date, 0) + t.pnl_dollars

    if not daily_pnl:
        return []

    # Sort by date and compute returns
    sorted_dates = sorted(daily_pnl.keys())
    cumulative = starting_capital
    returns = []
    for d in sorted_dates:
        daily_return = daily_pnl[d] / cumulative if cumulative > 0 else 0
        returns.append(daily_return)
        cumulative += daily_pnl[d]

    return returns


def _compute_sharpe(
    daily_returns: list[float],
    risk_free_rate: float = 0.0,
    trading_days: int = 252,
) -> float:
    """Compute annualized Sharpe ratio from daily returns."""
    if len(daily_returns) < 2:
        return float("nan")

    mean_return = sum(daily_returns) / len(daily_returns)
    excess = mean_return - (risk_free_rate / trading_days)

    variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std = math.sqrt(variance) if variance > 0 else 0

    if std == 0:
        return float("nan")

    return (excess / std) * math.sqrt(trading_days)


def _compute_drawdown(
    trades: list[BacktestTrade],
    starting_capital: float,
) -> tuple[float, int]:
    """Compute maximum drawdown percentage and duration in days.

    Returns:
        Tuple of (max_drawdown_pct, max_duration_days).
    """
    if not trades:
        return 0.0, 0

    # Build cumulative equity by exit date
    daily_pnl: dict[str, float] = {}
    for t in trades:
        if t.exit_date and t.pnl_dollars is not None:
            daily_pnl[t.exit_date] = daily_pnl.get(t.exit_date, 0) + t.pnl_dollars

    if not daily_pnl:
        return 0.0, 0

    sorted_dates = sorted(daily_pnl.keys())
    equity = starting_capital
    peak_equity = starting_capital
    max_dd_pct = 0.0
    max_dd_duration = 0

    # Track drawdown duration
    dd_start_date = None

    for date_str in sorted_dates:
        equity += daily_pnl[date_str]
        if equity > peak_equity:
            peak_equity = equity
            # Drawdown ended — check duration
            if dd_start_date is not None:
                dt_start = datetime.strptime(dd_start_date, "%Y-%m-%d")
                dt_end = datetime.strptime(date_str, "%Y-%m-%d")
                duration = (dt_end - dt_start).days
                max_dd_duration = max(max_dd_duration, duration)
            dd_start_date = None
        else:
            # In drawdown
            if dd_start_date is None:
                dd_start_date = date_str
            dd_pct = ((peak_equity - equity) / peak_equity) * 100
            max_dd_pct = max(max_dd_pct, dd_pct)

    # Handle still in drawdown at end
    if dd_start_date is not None and sorted_dates:
        dt_start = datetime.strptime(dd_start_date, "%Y-%m-%d")
        dt_end = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
        duration = (dt_end - dt_start).days
        max_dd_duration = max(max_dd_duration, duration)

    return max_dd_pct, max_dd_duration
