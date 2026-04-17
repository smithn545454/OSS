"""Category A: Underlying Technical Features.

Computes technical indicators for the underlying stock.
Per Section 13.2 of OSS_Complete_Requirements.md.

Pillar v4 adds 150/200-day simple moving averages, 52-week high/low
proximity, and Bollinger Band width percentile. These power the
Stage 2 Minervini trend template and the volatility-regime subscore.
"""

from __future__ import annotations

import logging
import math
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

# Pillar v4 constants
_BB_PERIOD = 20
_BB_STDEV = 2.0
_BB_PERCENTILE_LOOKBACK = 252
_TRADING_DAYS_52W = 252
_MA_150 = 150
_MA_200 = 200


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

    # Pillar v4 additions (require ≥252 daily bars for full coverage)
    ma_150: Optional[float] = None  # 150-day simple moving average (Stage 2)
    ma_200: Optional[float] = None  # 200-day simple moving average (Stage 2)
    high_52w: Optional[float] = None  # 252-day high of daily close
    low_52w: Optional[float] = None  # 252-day low of daily close
    dist_to_52w_high_pct: Optional[float] = None  # (close - high_52w) / high_52w * 100
    dist_to_52w_low_pct: Optional[float] = None  # (close - low_52w) / low_52w * 100
    bb_width: Optional[float] = None  # (upper - lower) / SMA20
    bb_width_percentile: Optional[float] = None  # 0-100 rank over past 252 days


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

    # =========================================================================
    # Pillar v4 additions — require longer bar windows; degrade gracefully
    # when the bars sequence is shorter than the required lookback.
    # =========================================================================
    ma_150 = calculate_sma(closes, _MA_150)
    ma_200 = calculate_sma(closes, _MA_200)

    high_52w = low_52w = None
    dist_to_52w_high_pct = dist_to_52w_low_pct = None
    if len(closes) >= _TRADING_DAYS_52W:
        recent = closes[-_TRADING_DAYS_52W:]
        high_52w = max(recent)
        low_52w = min(recent)
        if high_52w > 0:
            dist_to_52w_high_pct = (
                (current_close - high_52w) / high_52w
            ) * 100
        if low_52w > 0:
            dist_to_52w_low_pct = (
                (current_close - low_52w) / low_52w
            ) * 100

    bb_width, bb_width_percentile = _compute_bb_width(closes)

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
        ma_150=ma_150,
        ma_200=ma_200,
        high_52w=high_52w,
        low_52w=low_52w,
        dist_to_52w_high_pct=dist_to_52w_high_pct,
        dist_to_52w_low_pct=dist_to_52w_low_pct,
        bb_width=bb_width,
        bb_width_percentile=bb_width_percentile,
    )


def _bb_width_at(closes: Sequence[float], end_idx: int) -> Optional[float]:
    """Compute Bollinger Band width at ``end_idx`` using the prior 20 closes.

    BB width = (upper - lower) / middle, where middle = SMA20,
    upper = middle + 2*stdev, lower = middle - 2*stdev. Returns None
    when fewer than 20 bars are available or when the middle is zero.
    """
    if end_idx < _BB_PERIOD - 1:
        return None
    window = closes[end_idx - _BB_PERIOD + 1 : end_idx + 1]
    if len(window) < _BB_PERIOD:
        return None
    mean = sum(window) / _BB_PERIOD
    if mean <= 0:
        return None
    variance = sum((x - mean) ** 2 for x in window) / _BB_PERIOD
    stdev = math.sqrt(variance)
    upper = mean + _BB_STDEV * stdev
    lower = mean - _BB_STDEV * stdev
    return (upper - lower) / mean


def _compute_bb_width(
    closes: Sequence[float],
) -> tuple[Optional[float], Optional[float]]:
    """Return ``(current_bb_width, percentile)`` from a close-price series.

    The percentile ranks the current BB width against the last 252
    rolling BB-width values. Returns ``(width, None)`` when we have
    a current width but fewer than ~60 historical values to rank
    against (below that the percentile is too noisy to be useful).
    """
    if len(closes) < _BB_PERIOD:
        return None, None

    current_width = _bb_width_at(closes, len(closes) - 1)
    if current_width is None:
        return None, None

    # Build historical BB-width series for percentile ranking.
    lookback = min(_BB_PERCENTILE_LOOKBACK, len(closes) - _BB_PERIOD + 1)
    historical: list[float] = []
    # Start_idx so that we produce `lookback` rolling widths ending at
    # the most recent bar.
    start = len(closes) - lookback
    for i in range(start, len(closes)):
        w = _bb_width_at(closes, i)
        if w is not None:
            historical.append(w)

    if len(historical) < 60:
        return current_width, None

    below = sum(1 for w in historical if w < current_width)
    percentile = (below / len(historical)) * 100
    return current_width, round(percentile, 2)
