"""Exit condition checking for paper trading.

Per Section 17.3 of OSS_Complete_Requirements.md.

Exit Conditions (Priority Order):
1. PROFIT_TARGET: +50%
2. STOP_LOSS: -50%
3. TIME_EXIT: DTE <= 5
4. EXPIRATION: DTE <= 0
"""

from __future__ import annotations

from typing import Optional

from app.core.schemas import ExitReason, PaperPosition, TrackingConfig


def check_exit_conditions(
    position: PaperPosition,
    current_price: float,
    current_dte: int,
    config: Optional[TrackingConfig] = None,
) -> Optional[ExitReason]:
    """Check exit conditions in priority order.
    
    Per Section 17.3: Exit conditions are checked in priority order.
    The first triggered condition determines the exit reason.
    
    Args:
        position: The paper position to check
        current_price: Current mid price of the option
        current_dte: Current days to expiration
        config: Optional tracking configuration (uses defaults if None)
        
    Returns:
        ExitReason if an exit condition is triggered, None otherwise
    """
    if config is None:
        config = TrackingConfig()
    
    # Calculate current P&L percentage
    if position.entry_price <= 0:
        return None
    
    current_pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
    
    # Priority 1: Profit Target
    if current_pnl_pct >= config.profit_target_pct:
        return ExitReason.PROFIT_TARGET
    
    # Priority 2: Stop Loss
    if current_pnl_pct <= -config.stop_loss_pct:
        return ExitReason.STOP_LOSS
    
    # Priority 3: Time Exit (approaching expiration)
    if current_dte <= config.time_exit_dte and current_dte > 0:
        return ExitReason.TIME_EXIT
    
    # Priority 4: Expiration
    if current_dte <= 0:
        return ExitReason.EXPIRATION
    
    # No exit condition triggered
    return None


def calculate_dte_from_expiration(expiration_date: str) -> int:
    """Calculate DTE from expiration date string.
    
    Args:
        expiration_date: Expiration date in YYYY-MM-DD format
        
    Returns:
        Days to expiration (can be negative if expired)
    """
    from datetime import datetime, timezone
    
    try:
        exp_date = datetime.strptime(expiration_date, "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        return (exp_date - today).days
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
