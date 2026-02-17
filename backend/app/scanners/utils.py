"""Shared calculation utilities for scanners.

Contains implementations of technical analysis calculations used by multiple
scanners, following the exact formulas specified in the requirements document.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from app.services.polygon import DailyBar


@dataclass
class TechnicalData:
    """Container for computed technical indicators."""

    # Price data
    close: float
    sma20: Optional[float] = None
    sma50: Optional[float] = None

    # Returns
    return_5d: Optional[float] = None
    return_20d: Optional[float] = None

    # Volatility
    atr14: Optional[float] = None
    atr14_pct: Optional[float] = None
    rv20: Optional[float] = None

    # Trend
    trend_aligned_bullish: bool = False
    trend_aligned_bearish: bool = False


def calculate_sma(closes: Sequence[float], period: int) -> Optional[float]:
    """Calculate Simple Moving Average.

    Args:
        closes: Sequence of closing prices (most recent last)
        period: Number of periods for the average

    Returns:
        SMA value or None if insufficient data
    """
    if len(closes) < period:
        return None

    # Take the last `period` values
    window = closes[-period:]
    return sum(window) / period


def calculate_returns(
    closes: Sequence[float],
    period: int,
) -> Optional[float]:
    """Calculate N-day return as a percentage.

    Formula: (close_today - close_N_days_ago) / close_N_days_ago * 100

    Args:
        closes: Sequence of closing prices (most recent last)
        period: Number of days for return calculation

    Returns:
        Return percentage or None if insufficient data
    """
    if len(closes) < period + 1:
        return None

    current_close = closes[-1]
    prior_close = closes[-(period + 1)]

    if prior_close == 0:
        return None

    return ((current_close - prior_close) / prior_close) * 100


def calculate_true_range(
    high: float,
    low: float,
    prev_close: float,
) -> float:
    """Calculate True Range for a single bar.

    True Range = MAX of:
      - current_high - current_low
      - ABS(current_high - previous_close)
      - ABS(current_low - previous_close)

    Args:
        high: Current bar high
        low: Current bar low
        prev_close: Previous bar close

    Returns:
        True Range value
    """
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close),
    )


def calculate_atr(
    bars: Sequence[DailyBar],
    period: int = 14,
) -> Optional[float]:
    """Calculate Average True Range.

    ATR(N) = Simple Moving Average of True Range over N periods

    Args:
        bars: Sequence of DailyBar objects (sorted by date ascending)
        period: ATR period (default 14 as per requirements)

    Returns:
        ATR value or None if insufficient data
    """
    # Need at least period + 1 bars (for TR calculation needing prev close)
    if len(bars) < period + 1:
        return None

    # Calculate True Range for each bar (starting from second bar)
    true_ranges: list[float] = []
    for i in range(1, len(bars)):
        tr = calculate_true_range(
            bars[i].high,
            bars[i].low,
            bars[i - 1].close,
        )
        true_ranges.append(tr)

    # Take the last `period` true ranges for the average
    if len(true_ranges) < period:
        return None

    window = true_ranges[-period:]
    return sum(window) / period


def calculate_atr_series(
    bars: Sequence[DailyBar],
    period: int = 14,
) -> list[Optional[float]]:
    """Calculate ATR series for all bars.

    Returns a list of ATR values aligned with the input bars.
    First `period` values will be None.

    Args:
        bars: Sequence of DailyBar objects (sorted by date ascending)
        period: ATR period

    Returns:
        List of ATR values (same length as bars, with leading Nones)
    """
    if len(bars) < period + 1:
        return [None] * len(bars)

    # Calculate True Range for each bar
    true_ranges: list[float] = [0.0]  # First bar has no TR
    for i in range(1, len(bars)):
        tr = calculate_true_range(
            bars[i].high,
            bars[i].low,
            bars[i - 1].close,
        )
        true_ranges.append(tr)

    # Calculate rolling ATR
    atr_values: list[Optional[float]] = [None] * len(bars)

    for i in range(period, len(bars)):
        window = true_ranges[i - period + 1 : i + 1]
        atr_values[i] = sum(window) / period

    return atr_values


def calculate_rv(
    closes: Sequence[float],
    window: int = 20,
) -> Optional[float]:
    """Calculate Realized Volatility (annualized).

    Formula per Section 10.5 (Cheap Options scanner):
    1. Compute daily log returns: daily_returns[i] = LN(close[i] / close[i-1])
    2. std_dev = STDEV(daily_returns)
    3. RV = std_dev * SQRT(252)  # Annualize

    Args:
        closes: Sequence of closing prices (most recent last)
        window: Number of days for RV calculation (default 20)

    Returns:
        Annualized RV as decimal (e.g., 0.25 for 25%) or None if insufficient data
    """
    # Need window + 1 closes to get `window` returns
    if len(closes) < window + 1:
        return None

    # Get the relevant closes
    relevant_closes = closes[-(window + 1) :]

    # Calculate log returns
    log_returns: list[float] = []
    for i in range(1, len(relevant_closes)):
        if relevant_closes[i - 1] <= 0 or relevant_closes[i] <= 0:
            return None
        log_return = math.log(relevant_closes[i] / relevant_closes[i - 1])
        log_returns.append(log_return)

    if len(log_returns) < 2:
        return None

    # Calculate standard deviation
    mean_return = sum(log_returns) / len(log_returns)
    squared_diffs = [(r - mean_return) ** 2 for r in log_returns]
    variance = sum(squared_diffs) / (len(log_returns) - 1)  # Sample variance
    std_dev = math.sqrt(variance)

    # Annualize
    rv = std_dev * math.sqrt(252)
    return rv


def calculate_n_day_high(bars: Sequence[DailyBar], n: int) -> Optional[float]:
    """Calculate N-day high from daily bars.

    Args:
        bars: Sequence of DailyBar objects (most recent last)
        n: Number of days to look back

    Returns:
        Highest high in the N-day window or None if insufficient data
    """
    if len(bars) < n:
        return None

    window = bars[-n:]
    return max(bar.high for bar in window)


def calculate_n_day_low(bars: Sequence[DailyBar], n: int) -> Optional[float]:
    """Calculate N-day low from daily bars.

    Args:
        bars: Sequence of DailyBar objects (most recent last)
        n: Number of days to look back

    Returns:
        Lowest low in the N-day window or None if insufficient data
    """
    if len(bars) < n:
        return None

    window = bars[-n:]
    return min(bar.low for bar in window)


def calculate_technical_data(bars: Sequence[DailyBar]) -> Optional[TechnicalData]:
    """Calculate all technical indicators from bar data.

    Args:
        bars: Sequence of DailyBar objects (sorted by date ascending)

    Returns:
        TechnicalData with computed indicators or None if insufficient data
    """
    if not bars:
        return None

    closes = [bar.close for bar in bars]
    current_close = closes[-1]

    # Calculate indicators
    sma20 = calculate_sma(closes, 20)
    sma50 = calculate_sma(closes, 50)
    return_5d = calculate_returns(closes, 5)
    return_20d = calculate_returns(closes, 20)
    atr14 = calculate_atr(bars, 14)
    rv20 = calculate_rv(closes, 20)

    # Calculate trend alignment
    trend_aligned_bullish = False
    trend_aligned_bearish = False

    if sma20 is not None and sma50 is not None:
        if current_close > sma20 > sma50:
            trend_aligned_bullish = True
        elif current_close < sma20 < sma50:
            trend_aligned_bearish = True

    # Calculate ATR percentage
    atr14_pct = None
    if atr14 is not None and current_close > 0:
        atr14_pct = (atr14 / current_close) * 100

    return TechnicalData(
        close=current_close,
        sma20=sma20,
        sma50=sma50,
        return_5d=return_5d,
        return_20d=return_20d,
        atr14=atr14,
        atr14_pct=atr14_pct,
        rv20=rv20,
        trend_aligned_bullish=trend_aligned_bullish,
        trend_aligned_bearish=trend_aligned_bearish,
    )


@dataclass
class IVProxyResult:
    """Result of IV proxy calculation."""

    iv_proxy: float
    atm_call_iv: float
    atm_put_iv: float
    atm_call_strike: float
    atm_put_strike: float
    atm_dte: int


def calculate_iv_proxy(
    options_chain: list[dict],
    underlying_price: float,
    min_dte: int = 7,
    max_dte: int = 90,
) -> Optional[IVProxyResult]:
    """Calculate IV proxy from ATM options in a DTE range.

    Per Section 10.5:
    1. Filter chain to contracts with DTE between min_dte and max_dte
    2. For each side (CALL, PUT), find contract closest to ATM
    3. iv_proxy = AVERAGE(atm_call_iv, atm_put_iv)

    Args:
        options_chain: List of option contract dicts from Polygon
        underlying_price: Current underlying price
        min_dte: Minimum DTE for contracts (default 7)
        max_dte: Maximum DTE for contracts (default 90)

    Returns:
        IVProxyResult with iv_proxy, IVs, strikes, and DTE, or None if not found
    """
    from datetime import datetime

    today = datetime.now().date()

    # Filter and organize contracts by type
    calls: list[tuple[float, float, int]] = []  # (strike, iv, dte)
    puts: list[tuple[float, float, int]] = []

    for contract in options_chain:
        details = contract.get("details", {})
        greeks = contract.get("greeks", {})

        contract_type = details.get("contract_type", "").upper()
        strike = details.get("strike_price", 0)
        exp_date_str = details.get("expiration_date", "")
        iv = contract.get("implied_volatility") or greeks.get("implied_volatility")

        if not all([contract_type, strike, exp_date_str, iv]):
            continue

        # Calculate DTE
        try:
            exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days
        except ValueError:
            continue

        if min_dte <= dte <= max_dte:
            if contract_type == "CALL":
                calls.append((strike, iv, dte))
            elif contract_type == "PUT":
                puts.append((strike, iv, dte))

    if not calls or not puts:
        return None

    # Find ATM contracts (closest strike to underlying price)
    atm_call = min(calls, key=lambda x: abs(x[0] - underlying_price))
    atm_put = min(puts, key=lambda x: abs(x[0] - underlying_price))

    atm_call_strike = atm_call[0]
    atm_call_iv = atm_call[1]
    atm_put_strike = atm_put[0]
    atm_put_iv = atm_put[1]
    iv_proxy = (atm_call_iv + atm_put_iv) / 2
    avg_dte = (atm_call[2] + atm_put[2]) // 2

    return IVProxyResult(
        iv_proxy=iv_proxy,
        atm_call_iv=atm_call_iv,
        atm_put_iv=atm_put_iv,
        atm_call_strike=atm_call_strike,
        atm_put_strike=atm_put_strike,
        atm_dte=avg_dte,
    )


def calculate_volume_ratio(
    today_volume: int,
    avg_volume: float,
) -> float:
    """Calculate volume ratio vs average.

    Args:
        today_volume: Today's volume
        avg_volume: Average volume (e.g., 20-day average)

    Returns:
        Ratio of today's volume to average (0 if avg is 0)
    """
    if avg_volume <= 0:
        return float("inf") if today_volume > 0 else 0.0
    return today_volume / avg_volume


def calculate_pct_change(
    current: float,
    previous: float,
) -> Optional[float]:
    """Calculate percentage change.

    Args:
        current: Current value
        previous: Previous value

    Returns:
        Percentage change or None if previous is 0
    """
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100
