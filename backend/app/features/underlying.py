"""Category A: Underlying Technical Features.

Computes technical indicators for the underlying stock.
Per Section 13.2 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from app.scanners.utils import (
    calculate_atr,
    calculate_returns,
    calculate_rv,
    calculate_sma,
)
from app.services.polygon import DailyBar
from app.services.technicals import (
    calculate_adx,
    calculate_ema,
    calculate_macd,
    calculate_obv_trend,
    calculate_rsi,
    classify_ema_alignment,
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

    # Category G: Technical Indicators (for Directional pillar)
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    ema_alignment: Optional[str] = None  # BULLISH_STACK, BEARISH_STACK, etc.
    rsi_14: Optional[float] = None
    macd_histogram: Optional[float] = None
    adx_14: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None
    obv_trend: Optional[str] = None  # RISING, FALLING, FLAT


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

    # =========================================================================
    # Category G: Technical Indicators (for Directional pillar)
    # =========================================================================
    ema_9 = calculate_ema(closes, 9)
    ema_21 = calculate_ema(closes, 21)
    ema_50 = calculate_ema(closes, 50)
    ema_200 = calculate_ema(closes, 200)

    ema_alignment_val = None
    if any(e is not None for e in [ema_9, ema_21, ema_50, ema_200]):
        ema_alignment_val = classify_ema_alignment(
            current_close, ema_9, ema_21, ema_50, ema_200
        )

    rsi_14 = calculate_rsi(closes, 14)

    macd_result = calculate_macd(closes)
    macd_histogram = macd_result.histogram if macd_result else None

    adx_result = calculate_adx(bars, 14)
    adx_14 = adx_result.adx if adx_result else None
    plus_di = adx_result.plus_di if adx_result else None
    minus_di = adx_result.minus_di if adx_result else None

    obv_result = calculate_obv_trend(bars, 20)
    obv_trend_val = obv_result["trend"] if obv_result else None

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
        ema_9=ema_9,
        ema_21=ema_21,
        ema_50=ema_50,
        ema_200=ema_200,
        ema_alignment=ema_alignment_val,
        rsi_14=rsi_14,
        macd_histogram=macd_histogram,
        adx_14=adx_14,
        plus_di=plus_di,
        minus_di=minus_di,
        obv_trend=obv_trend_val,
    )
