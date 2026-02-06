"""Category E: Liquidity Features.

Computes open interest change and liquidity metrics.
Per Section 13.2 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from app.core.schemas import OIHistory

logger = logging.getLogger(__name__)


@dataclass
class LiquidityFeatures:
    """Category E liquidity features."""
    
    open_interest: int
    volume: int
    oi_5d_change_pct: Optional[float] = None


def calculate_oi_5d_change(
    current_oi: int,
    oi_history: Optional[Sequence[OIHistory]],
) -> Optional[float]:
    """Calculate 5-day OI change percentage.
    
    Args:
        current_oi: Current open interest
        oi_history: Historical OI records (most recent first)
        
    Returns:
        Percentage change from 5 days ago, or None if insufficient data
    """
    if not oi_history or len(oi_history) < 5:
        return None
    
    # Get OI from approximately 5 days ago
    # oi_history is ordered most recent first
    oi_5d_ago = oi_history[4].open_interest if len(oi_history) > 4 else oi_history[-1].open_interest
    
    if oi_5d_ago == 0:
        return None
    
    change_pct = ((current_oi - oi_5d_ago) / oi_5d_ago) * 100
    return round(change_pct, 2)


def compute_liquidity_features(
    open_interest: int,
    volume: int,
    oi_history: Optional[Sequence[OIHistory]] = None,
) -> LiquidityFeatures:
    """Compute liquidity features.
    
    Args:
        open_interest: Current open interest
        volume: Current volume
        oi_history: Historical OI records for 5-day change calculation
        
    Returns:
        LiquidityFeatures dataclass
    """
    oi_5d_change_pct = None
    if oi_history:
        oi_5d_change_pct = calculate_oi_5d_change(open_interest, oi_history)
    
    return LiquidityFeatures(
        open_interest=open_interest,
        volume=volume,
        oi_5d_change_pct=oi_5d_change_pct,
    )
