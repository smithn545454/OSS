"""Performance metrics calculation for paper trading.

Per Section 17.4 of OSS_Complete_Requirements.md.

Calculates:
- Win Rate
- Average Win/Loss
- Expectancy
- MFE/MAE analysis
- Exit type distribution
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.core.schemas import PaperPosition, PositionStatus, Verdict
from app.db.tables import PaperPositionTable
from app.paper_trading.models import PerformanceMetrics, TierPerformance

logger = logging.getLogger(__name__)


async def calculate_performance_metrics() -> PerformanceMetrics:
    """Calculate aggregate performance metrics for all paper trading.
    
    Per Section 17.4 and Section 4.5 targets:
    - APPROVE win rate > 55%
    - APPROVE average return > 25%
    
    Returns:
        PerformanceMetrics with all calculated values
    """
    # Fetch all positions
    open_positions = await PaperPositionTable.list_open(limit=1000)
    closed_positions = await PaperPositionTable.list_closed(limit=1000)
    all_positions = open_positions + closed_positions
    
    return compute_metrics_from_positions(all_positions)


def compute_metrics_from_positions(
    positions: Sequence[PaperPosition],
) -> PerformanceMetrics:
    """Compute metrics from a list of positions.
    
    Args:
        positions: List of paper positions (open and/or closed)
        
    Returns:
        PerformanceMetrics with calculated values
    """
    if not positions:
        return PerformanceMetrics()
    
    metrics = PerformanceMetrics()
    
    # Separate open and closed
    open_positions = [p for p in positions if p.status == PositionStatus.OPEN]
    closed_positions = [p for p in positions if p.status == PositionStatus.CLOSED]
    
    metrics.total_positions = len(positions)
    metrics.open_positions = len(open_positions)
    metrics.closed_positions = len(closed_positions)
    
    if not closed_positions:
        # No closed positions to analyze
        return metrics
    
    # Analyze closed positions
    wins: list[float] = []
    losses: list[float] = []
    all_returns: list[float] = []
    all_mfe: list[float] = []
    all_mae: list[float] = []
    all_days: list[int] = []
    
    # Exit distribution
    exit_counts: dict[str, int] = {}
    
    # By verdict
    approve_returns: list[float] = []
    watch_returns: list[float] = []
    
    # By tier
    tier_data: dict[str, TierPerformance] = {
        "TIER_1": TierPerformance(tier="TIER_1"),
        "TIER_2": TierPerformance(tier="TIER_2"),
        "TIER_3": TierPerformance(tier="TIER_3"),
        "NONE": TierPerformance(tier="NONE"),
    }
    
    for position in closed_positions:
        pnl = position.current_pnl_pct
        all_returns.append(pnl)
        all_mfe.append(position.max_favorable_excursion)
        all_mae.append(position.max_adverse_excursion)
        all_days.append(position.days_held)
        
        # Classify win/loss
        if pnl > 0:
            wins.append(pnl)
            metrics.win_count += 1
        elif pnl < 0:
            losses.append(pnl)
            metrics.loss_count += 1
        else:
            metrics.breakeven_count += 1
        
        # Exit distribution
        if position.exit_reason:
            reason = str(position.exit_reason)
            if hasattr(position.exit_reason, 'value'):
                reason = position.exit_reason.value
            exit_counts[reason] = exit_counts.get(reason, 0) + 1
        
        # By verdict at entry
        verdict = position.verdict_at_entry
        if hasattr(verdict, 'value'):
            verdict = verdict.value
        verdict_str = str(verdict)
        
        if verdict_str == "APPROVE":
            approve_returns.append(pnl)
        elif verdict_str == "WATCH":
            watch_returns.append(pnl)
        
        # By tier
        tier = position.quality_tier_at_entry
        if tier:
            tier_str = str(tier)
            if hasattr(tier, 'value'):
                tier_str = tier.value
        else:
            tier_str = "NONE"
        
        if tier_str in tier_data:
            tier_data[tier_str].total_positions += 1
            tier_data[tier_str].closed_positions += 1
            tier_data[tier_str].total_return_pct += pnl
            if pnl > 0:
                tier_data[tier_str].win_count += 1
            elif pnl < 0:
                tier_data[tier_str].loss_count += 1
    
    # Calculate averages
    if all_returns:
        metrics.total_return_pct = sum(all_returns)
        metrics.best_trade_pct = max(all_returns)
        metrics.worst_trade_pct = min(all_returns)
    
    if wins:
        metrics.avg_win_pct = sum(wins) / len(wins)
    
    if losses:
        metrics.avg_loss_pct = sum(losses) / len(losses)
    
    if all_mfe:
        metrics.avg_mfe = sum(all_mfe) / len(all_mfe)
        metrics.max_mfe = max(all_mfe)
    
    if all_mae:
        metrics.avg_mae = sum(all_mae) / len(all_mae)
        metrics.max_mae = min(all_mae)  # Most negative
    
    if all_days:
        metrics.avg_days_held = sum(all_days) / len(all_days)
        metrics.max_days_held = max(all_days)
    
    # Exit distribution
    metrics.exit_distribution = exit_counts
    
    # By verdict performance
    if approve_returns:
        approve_win_count = sum(1 for r in approve_returns if r > 0)
        metrics.approve_performance = {
            "count": len(approve_returns),
            "win_rate": (approve_win_count / len(approve_returns)) * 100,
            "avg_return": sum(approve_returns) / len(approve_returns),
        }
    
    if watch_returns:
        watch_win_count = sum(1 for r in watch_returns if r > 0)
        metrics.watch_performance = {
            "count": len(watch_returns),
            "win_rate": (watch_win_count / len(watch_returns)) * 100,
            "avg_return": sum(watch_returns) / len(watch_returns),
        }
    
    # Tier performance
    metrics.tier_performance = {k: v for k, v in tier_data.items() if v.total_positions > 0}
    
    return metrics


def calculate_rolling_metrics(
    positions: Sequence[PaperPosition],
    window_days: int = 30,
) -> list[dict]:
    """Calculate rolling performance metrics over time.
    
    Useful for seeing how performance evolves.
    
    Args:
        positions: List of closed positions
        window_days: Rolling window size in days
        
    Returns:
        List of dicts with date and metrics
    """
    from datetime import datetime, timedelta
    
    closed = [p for p in positions if p.status == PositionStatus.CLOSED and p.exit_date]
    if not closed:
        return []
    
    # Sort by exit date
    closed.sort(key=lambda p: p.exit_date or "")
    
    # Get date range
    first_date = datetime.strptime(closed[0].exit_date, "%Y-%m-%d")
    last_date = datetime.strptime(closed[-1].exit_date, "%Y-%m-%d")
    
    results = []
    current_date = first_date
    
    while current_date <= last_date:
        window_end = current_date
        window_start = current_date - timedelta(days=window_days)
        
        # Get positions closed in this window
        window_positions = [
            p for p in closed
            if window_start <= datetime.strptime(p.exit_date, "%Y-%m-%d") <= window_end
        ]
        
        if window_positions:
            window_metrics = compute_metrics_from_positions(window_positions)
            results.append({
                "date": current_date.strftime("%Y-%m-%d"),
                "positions_count": len(window_positions),
                "win_rate": window_metrics.win_rate,
                "avg_return": window_metrics.total_return_pct / len(window_positions) if window_positions else 0,
                "expectancy": window_metrics.expectancy,
            })
        
        current_date += timedelta(days=1)
    
    return results


def compare_tiers(
    positions: Sequence[PaperPosition],
) -> dict[str, dict]:
    """Compare performance across quality tiers.
    
    Per Section 16.3, we expect TIER_1 > TIER_2 > TIER_3 in performance.
    
    Args:
        positions: List of positions
        
    Returns:
        Dict with comparison data per tier
    """
    metrics = compute_metrics_from_positions(positions)
    
    comparison = {}
    for tier, perf in metrics.tier_performance.items():
        comparison[tier] = {
            "total": perf.total_positions,
            "closed": perf.closed_positions,
            "wins": perf.win_count,
            "losses": perf.loss_count,
            "win_rate": round(perf.win_rate, 2),
            "avg_return": round(perf.avg_return_pct, 2),
        }
    
    return comparison


def analyze_exit_effectiveness(
    positions: Sequence[PaperPosition],
) -> dict[str, dict]:
    """Analyze effectiveness of each exit type.
    
    Helps tune profit targets and stop losses.
    
    Args:
        positions: List of closed positions
        
    Returns:
        Analysis by exit reason
    """
    closed = [p for p in positions if p.status == PositionStatus.CLOSED and p.exit_reason]
    
    if not closed:
        return {}
    
    by_exit: dict[str, list[PaperPosition]] = {}
    for p in closed:
        reason = str(p.exit_reason)
        if hasattr(p.exit_reason, 'value'):
            reason = p.exit_reason.value
        
        if reason not in by_exit:
            by_exit[reason] = []
        by_exit[reason].append(p)
    
    analysis = {}
    for reason, positions_list in by_exit.items():
        returns = [p.current_pnl_pct for p in positions_list]
        mfes = [p.max_favorable_excursion for p in positions_list]
        maes = [p.max_adverse_excursion for p in positions_list]
        days = [p.days_held for p in positions_list]
        
        analysis[reason] = {
            "count": len(positions_list),
            "avg_return": round(sum(returns) / len(returns), 2),
            "avg_mfe": round(sum(mfes) / len(mfes), 2),
            "avg_mae": round(sum(maes) / len(maes), 2),
            "avg_days_held": round(sum(days) / len(days), 1),
            "mfe_left_on_table": round(sum(mfes) / len(mfes) - sum(returns) / len(returns), 2),
        }
    
    return analysis
