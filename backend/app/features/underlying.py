"""Category A: Underlying Technical Features.

Computes technical indicators for the underlying stock.
Per Section 13.2 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from app.services.polygon import DailyBar
from app.scanners.utils import (
    calculate_sma,
    calculate_returns,
    calculate_atr,
    calculate_rv,
)

logger = logging.getLogger(__name__)


@dataclass
class UnderlyingFeatures:
    """Category A features for an underlying."""
    
    close: float
    sma20: Optional[float] = None
    sma50: Optional[float] = None
    return_5d: Optional[float] = None
    return_20d: Optional[float] = None
    trend_aligned_bullish: bool = False
    trend_aligned_bearish: bool = False
    atr14: Optional[float] = None
    atr14_pct: Optional[float] = None
    rv20: Optional[float] = None  # Also used in Category C


def compute_underlying_features(bars: Sequence[DailyBar]) -> Optional[UnderlyingFeatures]:
    """Compute all underlying technical features from daily bars.
    
    Args:
        bars: Sequence of DailyBar objects, sorted by date ascending.
              Needs at least 51 bars for SMA50 calculation.
              
    Returns:
        UnderlyingFeatures dataclass or None if insufficient data.
    """
    if not bars or len(bars) < 5:
        logger.warning("Insufficient bars for underlying features")
        return None
    
    # Extract close prices
    closes = [bar.close for bar in bars]
    current_close = closes[-1]
    
    # Calculate SMAs
    sma20 = calculate_sma(closes, 20)
    sma50 = calculate_sma(closes, 50)
    
    # Calculate returns
    return_5d = calculate_returns(closes, 5)
    return_20d = calculate_returns(closes, 20)
    
    # Calculate trend alignment
    trend_aligned_bullish = False
    trend_aligned_bearish = False
    
    if sma20 is not None and sma50 is not None:
        if current_close > sma20 > sma50:
            trend_aligned_bullish = True
        elif current_close < sma20 < sma50:
            trend_aligned_bearish = True
    
    # Calculate ATR
    atr14 = calculate_atr(bars, 14)
    atr14_pct = None
    if atr14 is not None and current_close > 0:
        atr14_pct = (atr14 / current_close) * 100
    
    # Calculate Realized Volatility (also used in Category C)
    rv20 = calculate_rv(closes, 20)
    
    return UnderlyingFeatures(
        close=current_close,
        sma20=sma20,
        sma50=sma50,
        return_5d=return_5d,
        return_20d=return_20d,
        trend_aligned_bullish=trend_aligned_bullish,
        trend_aligned_bearish=trend_aligned_bearish,
        atr14=atr14,
        atr14_pct=atr14_pct,
        rv20=rv20,
    )
