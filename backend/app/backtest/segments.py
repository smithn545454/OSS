"""Segmented analysis for backtest runs.

Slices trades by various dimensions (scanner, verdict, score bucket,
market regime, month, holding period) and computes per-segment metrics.
"""

import logging
import math
from typing import Any

from app.core.schemas import BacktestTrade

logger = logging.getLogger(__name__)

# Segment type definitions
SEGMENT_TYPES = [
    "scanner",
    "verdict",
    "score_bucket",
    "regime",
    "month",
    "holding_period",
    "exit_reason",
]


def analyze_segment(
    trades: list[BacktestTrade],
    segment_type: str,
) -> list[dict[str, Any]]:
    """Segment trades by the given dimension and compute per-segment metrics.

    Args:
        trades: Completed trades with P&L data.
        segment_type: One of SEGMENT_TYPES.

    Returns:
        List of segment records with metrics.
    """
    if segment_type not in SEGMENT_TYPES:
        raise ValueError(
            f"Unknown segment type: {segment_type}. "
            f"Must be one of: {SEGMENT_TYPES}"
        )

    closed = [t for t in trades if t.pnl_dollars is not None]
    if not closed:
        return []

    # Group trades by segment key
    groups: dict[str, list[BacktestTrade]] = {}
    for t in closed:
        key = _get_segment_key(t, segment_type)
        if key not in groups:
            groups[key] = []
        groups[key].append(t)

    # Compute metrics per group
    results = []
    for key, group_trades in sorted(groups.items()):
        metrics = _compute_segment_metrics(group_trades)
        metrics["segment"] = key
        metrics["segment_type"] = segment_type
        results.append(metrics)

    return results


def analyze_all_segments(
    trades: list[BacktestTrade],
) -> dict[str, list[dict[str, Any]]]:
    """Run segmented analysis across all dimensions.

    Returns:
        Dictionary keyed by segment type, each containing segment results.
    """
    result = {}
    for seg_type in SEGMENT_TYPES:
        result[seg_type] = analyze_segment(trades, seg_type)
    return result


def _get_segment_key(trade: BacktestTrade, segment_type: str) -> str:
    """Extract the segment key for a trade based on segment type."""
    if segment_type == "scanner":
        return trade.scanner_type or "unknown"

    if segment_type == "verdict":
        return trade.verdict or "unknown"

    if segment_type == "score_bucket":
        score = trade.combined_score or 0
        if score >= 90:
            return "90+"
        elif score >= 80:
            return "80-89"
        elif score >= 70:
            return "70-79"
        elif score >= 60:
            return "60-69"
        else:
            return "<60"

    if segment_type == "regime":
        return trade.market_regime or "unknown"

    if segment_type == "month":
        return (trade.entry_date or "")[:7] or "unknown"  # YYYY-MM

    if segment_type == "holding_period":
        days = trade.days_held or 0
        if days <= 5:
            return "1-5d"
        elif days <= 10:
            return "6-10d"
        elif days <= 20:
            return "11-20d"
        else:
            return "20+d"

    if segment_type == "exit_reason":
        return trade.exit_reason or "unknown"

    return "unknown"


def _compute_segment_metrics(trades: list[BacktestTrade]) -> dict[str, Any]:
    """Compute metrics for a group of trades."""
    winners = [t for t in trades if (t.pnl_dollars or 0) > 0]
    losers = [t for t in trades if (t.pnl_dollars or 0) < 0]

    total_pnl = sum(t.pnl_dollars or 0 for t in trades)
    gross_profit = sum(t.pnl_dollars or 0 for t in winners)
    gross_loss = abs(sum(t.pnl_dollars or 0 for t in losers))

    win_rate = len(winners) / len(trades) * 100 if trades else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    avg_return = sum(t.pnl_pct or 0 for t in trades) / len(trades) if trades else 0
    avg_win = (
        sum(t.pnl_pct or 0 for t in winners) / len(winners) if winners else 0
    )
    avg_loss = (
        sum(t.pnl_pct or 0 for t in losers) / len(losers) if losers else 0
    )
    avg_days = (
        sum(t.days_held or 0 for t in trades) / len(trades) if trades else 0
    )

    # Sharpe for segment (simplified — per-trade returns)
    returns = [t.pnl_pct or 0 for t in trades]
    sharpe = _segment_sharpe(returns)

    return {
        "trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(win_rate, 2),
        "net_pnl": round(total_pnl, 2),
        "avg_return_pct": round(avg_return, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "sharpe": round(sharpe, 4) if not math.isnan(sharpe) else None,
        "avg_days_held": round(avg_days, 1),
    }


def _segment_sharpe(returns: list[float]) -> float:
    """Compute simplified Sharpe from per-trade return percentages."""
    if len(returns) < 2:
        return float("nan")

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance) if variance > 0 else 0

    if std == 0:
        return float("nan")

    # Annualize assuming ~1 trade per day on average
    return (mean / std) * math.sqrt(252)
