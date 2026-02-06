"""Data models for paper trading module.

Per Section 17 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.schemas import ExitReason


@dataclass
class UpdateResult:
    """Result of updating a single position.
    
    Tracks what happened during a daily position update.
    """
    
    position_id: str
    option_ticker: str
    previous_price: float
    current_price: float
    previous_pnl_pct: float
    current_pnl_pct: float
    mfe_updated: bool
    mae_updated: bool
    exit_triggered: bool
    exit_reason: Optional[ExitReason] = None
    error: Optional[str] = None
    
    @property
    def price_change_pct(self) -> float:
        """Calculate price change percentage."""
        if self.previous_price == 0:
            return 0.0
        return ((self.current_price - self.previous_price) / self.previous_price) * 100


@dataclass
class TierPerformance:
    """Performance metrics for a specific quality tier.
    
    Tracks how each tier (TIER_1, TIER_2, TIER_3) performs.
    """
    
    tier: str
    total_positions: int = 0
    closed_positions: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_return_pct: float = 0.0
    
    @property
    def win_rate(self) -> float:
        """Calculate win rate for this tier."""
        if self.closed_positions == 0:
            return 0.0
        return (self.win_count / self.closed_positions) * 100
    
    @property
    def avg_return_pct(self) -> float:
        """Calculate average return for this tier."""
        if self.closed_positions == 0:
            return 0.0
        return self.total_return_pct / self.closed_positions


@dataclass
class PerformanceMetrics:
    """Aggregate performance metrics for paper trading.
    
    Per Section 17.4 of requirements:
    - Win Rate
    - Average Win/Loss
    - Expectancy
    - MFE/MAE analysis
    - Exit type distribution
    """
    
    # Position counts
    total_positions: int = 0
    open_positions: int = 0
    closed_positions: int = 0
    
    # Win/Loss tracking
    win_count: int = 0
    loss_count: int = 0
    breakeven_count: int = 0
    
    # Return metrics
    total_return_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    
    # MFE/MAE analysis
    avg_mfe: float = 0.0
    avg_mae: float = 0.0
    max_mfe: float = 0.0
    max_mae: float = 0.0
    
    # Holding period
    avg_days_held: float = 0.0
    max_days_held: int = 0
    
    # Exit distribution (count by exit reason)
    exit_distribution: dict[str, int] = field(default_factory=dict)
    
    # Performance by verdict at entry
    approve_performance: dict[str, float] = field(default_factory=dict)
    watch_performance: dict[str, float] = field(default_factory=dict)
    
    # Performance by quality tier
    tier_performance: dict[str, TierPerformance] = field(default_factory=dict)
    
    @property
    def win_rate(self) -> float:
        """Calculate win rate as percentage.
        
        Win rate = win_count / closed_positions * 100
        Per Section 4.5: Target > 55%
        """
        if self.closed_positions == 0:
            return 0.0
        return (self.win_count / self.closed_positions) * 100
    
    @property
    def loss_rate(self) -> float:
        """Calculate loss rate as percentage."""
        if self.closed_positions == 0:
            return 0.0
        return (self.loss_count / self.closed_positions) * 100
    
    @property
    def expectancy(self) -> float:
        """Calculate expectancy (expected return per trade).
        
        Expectancy = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss))
        
        A positive expectancy means the system is profitable over time.
        """
        if self.closed_positions == 0:
            return 0.0
        
        win_rate_decimal = self.win_rate / 100
        loss_rate_decimal = 1 - win_rate_decimal
        
        # Use absolute value of avg_loss (it's stored as negative)
        expectancy = (win_rate_decimal * self.avg_win_pct) - (loss_rate_decimal * abs(self.avg_loss_pct))
        return round(expectancy, 2)
    
    @property
    def profit_factor(self) -> Optional[float]:
        """Calculate profit factor (gross profit / gross loss).
        
        > 1.0 indicates profitable system.
        """
        if self.loss_count == 0 or self.avg_loss_pct == 0:
            return None
        
        gross_profit = self.win_count * self.avg_win_pct
        gross_loss = self.loss_count * abs(self.avg_loss_pct)
        
        if gross_loss == 0:
            return None
        
        return round(gross_profit / gross_loss, 2)
    
    @property
    def mfe_capture_ratio(self) -> float:
        """Calculate MFE capture ratio.
        
        Shows how much of the maximum favorable move was captured.
        avg_win / avg_mfe
        """
        if self.avg_mfe == 0:
            return 0.0
        return round(self.avg_win_pct / self.avg_mfe, 2) if self.avg_win_pct > 0 else 0.0
    
    @property
    def mae_recovery_ratio(self) -> float:
        """Calculate MAE recovery ratio.
        
        Shows how often positions recovered from adverse moves.
        Useful for tuning stop loss levels.
        """
        if self.max_mae == 0:
            return 0.0
        # Positions that ended positive despite negative MAE
        return round(1 - (abs(self.avg_mae) / abs(self.max_mae)), 2) if self.max_mae != 0 else 0.0
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        tier_perf_dict = {}
        for tier, perf in self.tier_performance.items():
            tier_perf_dict[tier] = {
                "total_positions": perf.total_positions,
                "closed_positions": perf.closed_positions,
                "win_count": perf.win_count,
                "loss_count": perf.loss_count,
                "win_rate": round(perf.win_rate, 2),
                "avg_return_pct": round(perf.avg_return_pct, 2),
            }
        
        return {
            # Counts
            "total_positions": self.total_positions,
            "open_positions": self.open_positions,
            "closed_positions": self.closed_positions,
            # Win/Loss
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "breakeven_count": self.breakeven_count,
            "win_rate": round(self.win_rate, 2),
            "loss_rate": round(self.loss_rate, 2),
            # Returns
            "total_return_pct": round(self.total_return_pct, 2),
            "avg_win_pct": round(self.avg_win_pct, 2),
            "avg_loss_pct": round(self.avg_loss_pct, 2),
            "best_trade_pct": round(self.best_trade_pct, 2),
            "worst_trade_pct": round(self.worst_trade_pct, 2),
            "expectancy": self.expectancy,
            "profit_factor": self.profit_factor,
            # MFE/MAE
            "avg_mfe": round(self.avg_mfe, 2),
            "avg_mae": round(self.avg_mae, 2),
            "max_mfe": round(self.max_mfe, 2),
            "max_mae": round(self.max_mae, 2),
            "mfe_capture_ratio": self.mfe_capture_ratio,
            # Holding period
            "avg_days_held": round(self.avg_days_held, 1),
            "max_days_held": self.max_days_held,
            # Distributions
            "exit_distribution": self.exit_distribution,
            "tier_performance": tier_perf_dict,
            "approve_performance": self.approve_performance,
            "watch_performance": self.watch_performance,
        }


@dataclass
class ShadowPosition:
    """Shadow-tracked position for REJECT validation.
    
    Per Section 17.5: Track sampled REJECTs to measure false negatives.
    
    These are REJECTs that we track without actually "entering" to see
    if they would have been profitable (indicating a false negative).
    """
    
    evaluation_id: str
    option_ticker: str
    entry_price: float  # Mid at evaluation time
    entry_date: str
    rejection_reason: str  # Primary reason code
    final_score: float  # Score when rejected
    failed_gates: list[str] = field(default_factory=list)
    sample_type: str = "RANDOM"  # RANDOM, NEAR_MISS, SINGLE_GATE
    
    # Tracking fields (updated daily)
    current_price: float = 0.0
    current_pnl_pct: float = 0.0
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    days_tracked: int = 0
    
    # Outcome tracking
    would_have_hit_target: bool = False  # Hit +50%?
    would_have_hit_stop: bool = False    # Hit -50%?
    peak_pnl_pct: float = 0.0
    trough_pnl_pct: float = 0.0
    
    @property
    def is_false_negative(self) -> bool:
        """Determine if this was a false negative (would have been profitable).
        
        A REJECT is a false negative if it:
        - Would have hit profit target (+50%)
        - OR had a peak P&L > 25% (significant opportunity missed)
        """
        return self.would_have_hit_target or self.peak_pnl_pct > 25
    
    def update(self, current_price: float) -> None:
        """Update shadow position with current price.
        
        Args:
            current_price: Current mid price of the option
        """
        self.current_price = current_price
        
        if self.entry_price > 0:
            self.current_pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
        
        # Update MFE/MAE
        if self.current_pnl_pct > 0:
            self.max_favorable_excursion = max(
                self.max_favorable_excursion, self.current_pnl_pct
            )
        else:
            self.max_adverse_excursion = min(
                self.max_adverse_excursion, self.current_pnl_pct
            )
        
        # Track peak/trough
        self.peak_pnl_pct = max(self.peak_pnl_pct, self.current_pnl_pct)
        self.trough_pnl_pct = min(self.trough_pnl_pct, self.current_pnl_pct)
        
        # Check if would have hit targets
        if self.current_pnl_pct >= 50:
            self.would_have_hit_target = True
        if self.current_pnl_pct <= -50:
            self.would_have_hit_stop = True
        
        self.days_tracked += 1
