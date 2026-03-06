"""Exit condition checking for paper trading and backtesting.

Per Section 17.3 of OSS_Complete_Requirements.md.

Exit Conditions (Priority Order):
1. PROFIT_TARGET: gain >= target%
2. STOP_LOSS: loss >= stop%
3. TIME_EXIT: DTE <= threshold
4. MAX_HOLDING: days held >= max days (backtest only)
5. TRAILING_STOP: price dropped >= X% from peak (backtest only)
6. EXPIRATION: DTE <= 0
"""

from __future__ import annotations

from typing import Optional

from app.core.schemas import (
    BacktestExitConfig,
    ExitReason,
    PaperPosition,
    TrackingConfig,
)


def check_exit_conditions(
    position: PaperPosition,
    current_price: float,
    current_dte: int,
    config: Optional[TrackingConfig] = None,
    days_held: Optional[int] = None,
    peak_price: Optional[float] = None,
    backtest_exit_config: Optional[BacktestExitConfig] = None,
) -> Optional[ExitReason]:
    """Check exit conditions in priority order.

    The first triggered condition determines the exit reason.

    Args:
        position: The paper position to check
        current_price: Current mid price of the option
        current_dte: Current days to expiration
        config: Optional tracking configuration (uses defaults if None)
        days_held: Days the position has been held (for MAX_HOLDING check)
        peak_price: Peak option price since entry (for TRAILING_STOP check)
        backtest_exit_config: Extended exit config for backtesting

    Returns:
        ExitReason if an exit condition is triggered, None otherwise
    """
    if config is None:
        config = TrackingConfig()

    # Calculate current P&L percentage
    if position.entry_price <= 0:
        return None

    current_pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100

    # Determine thresholds — thesis-driven > backtest config > tracking config
    profit_target = (
        position.thesis_tp1_pct if position.thesis_tp1_pct is not None
        else backtest_exit_config.profit_target_pct if backtest_exit_config
        else config.profit_target_pct
    )
    stop_loss = (
        position.thesis_sl_pct if position.thesis_sl_pct is not None
        else backtest_exit_config.stop_loss_pct if backtest_exit_config
        else config.stop_loss_pct
    )
    time_exit_dte = (
        position.thesis_time_exit_dte if position.thesis_time_exit_dte is not None
        else backtest_exit_config.min_dte_at_exit if backtest_exit_config
        else config.time_exit_dte
    )

    # Priority 1: Profit Target
    if current_pnl_pct >= profit_target:
        return ExitReason.PROFIT_TARGET

    # Priority 2: Stop Loss
    if current_pnl_pct <= -stop_loss:
        return ExitReason.STOP_LOSS

    # Priority 3: Time Exit (approaching expiration)
    if current_dte <= time_exit_dte and current_dte > 0:
        return ExitReason.TIME_EXIT

    # Priority 4: Max Holding Period (backtest only)
    if backtest_exit_config and days_held is not None:
        if days_held >= backtest_exit_config.max_holding_days:
            return ExitReason.MAX_HOLDING

    # Priority 5: Trailing Stop (backtest only)
    if backtest_exit_config and peak_price is not None:
        trailing_pct = backtest_exit_config.trailing_stop_pct
        if trailing_pct is not None and peak_price > 0:
            drawdown_from_peak = ((peak_price - current_price) / peak_price) * 100
            if drawdown_from_peak >= trailing_pct:
                return ExitReason.TRAILING_STOP

    # Priority 6: Expiration
    if current_dte <= 0:
        return ExitReason.EXPIRATION

    # No exit condition triggered
    return None


def calculate_dte_from_expiration(
    expiration_date: str,
    as_of_date=None,
) -> int:
    """Calculate DTE from expiration date string.

    Args:
        expiration_date: Expiration date in YYYY-MM-DD format
        as_of_date: Reference date (defaults to today)

    Returns:
        Days to expiration (can be negative if expired)
    """
    from datetime import datetime, timezone

    try:
        exp_date = datetime.strptime(expiration_date, "%Y-%m-%d").date()
        if as_of_date is None:
            as_of_date = datetime.now(timezone.utc).date()
        return (exp_date - as_of_date).days
    except (ValueError, TypeError):
        # If we can't parse the date, assume far from expiration
        return 999


def should_exit(
    position: PaperPosition,
    current_price: float,
    expiration_date: str,
    config: Optional[TrackingConfig] = None,
) -> tuple[bool, Optional[ExitReason]]:
    """Convenience function to check if position should exit.

    Args:
        position: The paper position
        current_price: Current mid price
        expiration_date: Contract expiration date (YYYY-MM-DD)
        config: Optional tracking configuration

    Returns:
        Tuple of (should_exit: bool, exit_reason: Optional[ExitReason])
    """
    current_dte = calculate_dte_from_expiration(expiration_date)
    exit_reason = check_exit_conditions(position, current_price, current_dte, config)

    return exit_reason is not None, exit_reason


def get_exit_price_estimate(
    position: PaperPosition,
    exit_reason: ExitReason,
    current_price: float,
) -> float:
    """Estimate exit price based on exit reason.

    For most cases, use current price. For expiration, estimate
    based on intrinsic value (would need underlying price and strike).

    Args:
        position: The paper position
        exit_reason: The triggered exit reason
        current_price: Current mid price

    Returns:
        Estimated exit price
    """
    if exit_reason == ExitReason.EXPIRATION:
        # At expiration, option worth intrinsic value (could be 0)
        # For now, use current price as best estimate
        # In production, would calculate intrinsic value
        return max(current_price, 0.01)  # Minimum $0.01

    # For all other exits, use current mid price
    return current_price
