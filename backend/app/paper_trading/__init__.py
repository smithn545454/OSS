"""Paper trading module for OSS.

Provides:
- Position creation for Convex finalised candidates
- Daily position updates with P&L, MFE/MAE tracking
- Exit condition checking (profit target, stop loss, time exit, expiration)
- Performance metrics calculation
"""

from app.paper_trading.models import (
    PerformanceMetrics,
    TierPerformance,
    UpdateResult,
)
from app.paper_trading.position_manager import (
    create_position_from_convex_candidate,
    update_open_positions,
    update_position,
)
from app.paper_trading.exit_checker import check_exit_conditions
from app.paper_trading.metrics import calculate_performance_metrics

__all__ = [
    # Models
    "PerformanceMetrics",
    "TierPerformance",
    "UpdateResult",
    # Position management
    "create_position_from_convex_candidate",
    "update_open_positions",
    "update_position",
    # Exit conditions
    "check_exit_conditions",
    # Metrics
    "calculate_performance_metrics",
]
