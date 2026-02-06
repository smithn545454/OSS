"""Category B: Relative Strength Features.

Computes relative strength vs SPY benchmark.
Per Section 13.2 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from app.services.polygon import DailyBar
from app.scanners.utils import calculate_returns

logger = logging.getLogger(__name__)


@dataclass
class RelativeStrengthFeatures:
    """Category B features for relative strength."""
    
    spy_return_5d: Optional[float] = None
    spy_return_20d: Optional[float] = None
    rs_5d: Optional[float] = None  # return_5d - spy_return_5d
    rs_20d: Optional[float] = None  # return_20d - spy_return_20d


def compute_relative_strength_features(
    underlying_return_5d: Optional[float],
    underlying_return_20d: Optional[float],
    spy_bars: Sequence[DailyBar],
) -> RelativeStrengthFeatures:
    """Compute relative strength features vs SPY.
    
    Args:
        underlying_return_5d: 5-day return of the underlying
        underlying_return_20d: 20-day return of the underlying
        spy_bars: Daily bars for SPY benchmark
        
    Returns:
        RelativeStrengthFeatures dataclass
    """
    # Calculate SPY returns
    spy_return_5d = None
    spy_return_20d = None
    
    if spy_bars and len(spy_bars) >= 6:
        spy_closes = [bar.close for bar in spy_bars]
        spy_return_5d = calculate_returns(spy_closes, 5)
        
    if spy_bars and len(spy_bars) >= 21:
        spy_closes = [bar.close for bar in spy_bars]
        spy_return_20d = calculate_returns(spy_closes, 20)
    
    # Calculate relative strength
    rs_5d = None
    rs_20d = None
    
    if underlying_return_5d is not None and spy_return_5d is not None:
        rs_5d = underlying_return_5d - spy_return_5d
        
    if underlying_return_20d is not None and spy_return_20d is not None:
        rs_20d = underlying_return_20d - spy_return_20d
    
    return RelativeStrengthFeatures(
        spy_return_5d=spy_return_5d,
        spy_return_20d=spy_return_20d,
        rs_5d=rs_5d,
        rs_20d=rs_20d,
    )
