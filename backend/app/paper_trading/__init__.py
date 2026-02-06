"""Paper trading module for OSS.

Implements Stage 8: Paper Trading & Performance Tracking.
Per Section 17 of OSS_Complete_Requirements.md.

This module provides:
- Position entry for APPROVE/WATCH decisions
- Daily position updates with P&L, MFE/MAE tracking
- Exit condition checking (profit target, stop loss, time exit, expiration)
- Performance metrics calculation
- Shadow tracking for sampled REJECTs
"""

from app.paper_trading.models import (
    PerformanceMetrics,
    ShadowPosition,
    TierPerformance,
    UpdateResult,
)
from app.paper_trading.position_manager import (
    create_position_from_evaluation,
    create_positions_from_decisions,
    update_open_positions,
    update_position,
)
from app.paper_trading.exit_checker import check_exit_conditions
from app.paper_trading.metrics import calculate_performance_metrics
from app.paper_trading.shadow_tracker import select_shadow_candidates
from app.paper_trading.stage import PaperTradingStage, run_paper_trading

__all__ = [
    # Models
    "PerformanceMetrics",
    "ShadowPosition",
    "TierPerformance",
    "UpdateResult",
    # Position management
    "create_position_from_evaluation",
    "create_positions_from_decisions",
    "update_open_positions",
    "update_position",
    # Exit conditions
    "check_exit_conditions",
    # Metrics
    "calculate_performance_metrics",
    # Shadow tracking
    "select_shadow_candidates",
    # Pipeline stage
    "PaperTradingStage",
    "run_paper_trading",
]
