"""Technical indicator calculations for underlying stock analysis.

Computes EMA, RSI, MACD, ADX, OBV from daily bars and assembles
a StockTechnicals response with a synthesized Tape Read verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Sequence

from pydantic import BaseModel

from app.services.polygon import DailyBar


def calculate_n_day_high(bars: "list[DailyBar]", n: int) -> Optional[float]:
    """Highest high in the last n bars; None if fewer than n bars."""
    if len(bars) < n:
        return None
    return max(bar.high for bar in bars[-n:])


def calculate_n_day_low(bars: "list[DailyBar]", n: int) -> Optional[float]:
    """Lowest low in the last n bars; None if fewer than n bars."""
    if len(bars) < n:
        return None
    return min(bar.low for bar in bars[-n:])


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class TapeSignal(BaseModel):
    name: str
    reading: str
    signal: str  # BULLISH | BEARISH | NEUTRAL
    weight: float


class StockTechnicals(BaseModel):
    # Profile
    ticker: str
    company_name: Optional[str] = None
    sector: Optional[str] = None
    market_cap: Optional[int] = None
    homepage_url: Optional[str] = None

    # Price context
    price: float
    prev_close: Optional[float] = None
    change_dollar: Optional[float] = None
    change_pct: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    pct_from_52w_high: Optional[float] = None
    volume: Optional[int] = None
    avg_volume_20d: Optional[float] = None
    relative_volume: Optional[float] = None

    # EMAs
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    ema_alignment: Optional[str] = None

    # Indicators
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    adx_14: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None
    obv_trend: Optional[str] = None

    # Tape Read
    tape_signals: List[TapeSignal] = []
    tape_verdict: str = "NEUTRAL"
    tape_bullish_pct: float = 50.0


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------


def calculate_ema(closes: Sequence[float], period: int) -> float | None:
    """Calculate the latest Exponential Moving Average value.

    Seeds with SMA of the first `period` values, then applies
    the standard EMA multiplier for subsequent values.

    Args:
        closes: Closing prices, oldest first.
        period: EMA period.

    Returns:
        Latest EMA value, or None if insufficient data.
    """
    if len(closes) < period:
        return None

    k = 2.0 / (period + 1)
    # Seed with SMA
    ema = sum(closes[:period]) / period

    for price in closes[period:]:
        ema = price * k + ema * (1 - k)

    return ema


def _ema_series(closes: Sequence[float], period: int) -> list[float]:
    """Full EMA series (used internally for MACD)."""
    if len(closes) < period:
        return []

    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    series = [ema]

    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
        series.append(ema)

    return series


# ---------------------------------------------------------------------------
# RSI (Wilder's smoothing)
# ---------------------------------------------------------------------------


def calculate_rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Calculate the latest RSI using Wilder's smoothing method.

    Args:
        closes: Closing prices, oldest first.
        period: RSI period (default 14).

    Returns:
        RSI value (0-100), or None if insufficient data.
    """
    if len(closes) < period + 1:
        return None

    # Calculate price changes
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Seed average gain/loss with SMA of first `period` changes
    gains = [max(c, 0) for c in changes[:period]]
    losses = [abs(min(c, 0)) for c in changes[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder's smoothing for remaining changes
    for c in changes[period:]:
        gain = max(c, 0)
        loss = abs(min(c, 0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------


@dataclass
class MACDResult:
    macd: float
    signal: float
    histogram: float


def calculate_macd(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> MACDResult | None:
    """Calculate MACD line, signal line, and histogram.

    Args:
        closes: Closing prices, oldest first.
        fast: Fast EMA period (default 12).
        slow: Slow EMA period (default 26).
        signal_period: Signal EMA period (default 9).

    Returns:
        MACDResult or None if insufficient data.
    """
    if len(closes) < slow + signal_period:
        return None

    fast_ema = _ema_series(closes, fast)
    slow_ema = _ema_series(closes, slow)

    # Align: slow_ema starts at index 0 corresponding to closes[slow-1].
    # fast_ema starts at index 0 corresponding to closes[fast-1].
    # We need to align them so they cover the same dates.
    offset = slow - fast
    if offset > len(fast_ema):
        return None

    macd_line = [
        fast_ema[i + offset] - slow_ema[i] for i in range(len(slow_ema))
    ]

    if len(macd_line) < signal_period:
        return None

    # Signal line = EMA of MACD line
    signal_values = _ema_series(macd_line, signal_period)
    if not signal_values:
        return None

    latest_macd = macd_line[-1]
    latest_signal = signal_values[-1]
    return MACDResult(
        macd=latest_macd,
        signal=latest_signal,
        histogram=latest_macd - latest_signal,
    )


# ---------------------------------------------------------------------------
# ADX (Wilder's smoothing)
# ---------------------------------------------------------------------------


@dataclass
class ADXResult:
    adx: float
    plus_di: float
    minus_di: float


def calculate_adx(bars: Sequence[DailyBar], period: int = 14) -> ADXResult | None:
    """Calculate ADX, +DI, and -DI using Wilder's method.

    Args:
        bars: Daily bars, oldest first. Needs at least 2*period + 1 bars.
        period: ADX period (default 14).

    Returns:
        ADXResult or None if insufficient data.
    """
    min_bars = 2 * period + 1
    if len(bars) < min_bars:
        return None

    # Step 1: Calculate +DM, -DM, TR for each bar
    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []
    tr_list: list[float] = []

    for i in range(1, len(bars)):
        high_diff = bars[i].high - bars[i - 1].high
        low_diff = bars[i - 1].low - bars[i].low

        plus_dm = high_diff if high_diff > low_diff and high_diff > 0 else 0.0
        minus_dm = low_diff if low_diff > high_diff and low_diff > 0 else 0.0

        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - bars[i - 1].close),
            abs(bars[i].low - bars[i - 1].close),
        )

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    # Step 2: Wilder's smoothing — first value is sum of first `period`
    smoothed_plus_dm = sum(plus_dm_list[:period])
    smoothed_minus_dm = sum(minus_dm_list[:period])
    smoothed_tr = sum(tr_list[:period])

    # Step 3: Calculate DI and DX series
    dx_list: list[float] = []

    for i in range(period, len(plus_dm_list)):
        # Wilder's smoothing
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]

        if smoothed_tr == 0:
            continue

        plus_di = 100.0 * smoothed_plus_dm / smoothed_tr
        minus_di = 100.0 * smoothed_minus_dm / smoothed_tr
        di_sum = plus_di + minus_di

        if di_sum == 0:
            dx_list.append(0.0)
        else:
            dx_list.append(100.0 * abs(plus_di - minus_di) / di_sum)

    if len(dx_list) < period:
        return None

    # Step 4: ADX = Wilder's smoothed DX
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period

    # Final +DI and -DI from last smoothed values
    if smoothed_tr == 0:
        return None

    final_plus_di = 100.0 * smoothed_plus_dm / smoothed_tr
    final_minus_di = 100.0 * smoothed_minus_dm / smoothed_tr

    return ADXResult(adx=adx, plus_di=final_plus_di, minus_di=final_minus_di)


# ---------------------------------------------------------------------------
# OBV
# ---------------------------------------------------------------------------


def calculate_obv_trend(
    bars: Sequence[DailyBar], lookback: int = 20
) -> dict[str, object] | None:
    """Calculate On Balance Volume and its trend.

    Args:
        bars: Daily bars, oldest first.
        lookback: Number of bars to compare for trend (default 20).

    Returns:
        Dict with 'obv' (int) and 'trend' ("RISING"/"FALLING"/"FLAT"),
        or None if insufficient data.
    """
    if len(bars) < lookback + 1:
        return None

    obv = 0
    obv_values: list[int] = [0]

    for i in range(1, len(bars)):
        if bars[i].close > bars[i - 1].close:
            obv += bars[i].volume
        elif bars[i].close < bars[i - 1].close:
            obv -= bars[i].volume
        obv_values.append(obv)

    current_obv = obv_values[-1]
    past_obv = obv_values[-lookback - 1]

    # Trend based on percentage change, with threshold
    if past_obv == 0:
        trend = "FLAT"
    else:
        change_pct = (current_obv - past_obv) / abs(past_obv)
        if change_pct > 0.05:
            trend = "RISING"
        elif change_pct < -0.05:
            trend = "FALLING"
        else:
            trend = "FLAT"

    return {"obv": current_obv, "trend": trend}


# ---------------------------------------------------------------------------
# EMA alignment
# ---------------------------------------------------------------------------


def classify_ema_alignment(
    price: float,
    ema9: float | None,
    ema21: float | None,
    ema50: float | None,
    ema200: float | None,
) -> str:
    """Classify EMA alignment relative to price.

    Returns one of: BULLISH_STACK, BEARISH_STACK, ABOVE_ALL, BELOW_ALL, MIXED.
    """
    emas = [ema9, ema21, ema50, ema200]
    available = [e for e in emas if e is not None]

    if not available:
        return "MIXED"

    above_all = all(price > e for e in available)
    below_all = all(price < e for e in available)

    if above_all:
        # Check if EMAs are in proper descending order (9 > 21 > 50 > 200)
        valid = [e for e in emas if e is not None]
        if len(valid) >= 3 and all(valid[i] > valid[i + 1] for i in range(len(valid) - 1)):
            return "BULLISH_STACK"
        return "ABOVE_ALL"

    if below_all:
        valid = [e for e in emas if e is not None]
        if len(valid) >= 3 and all(valid[i] < valid[i + 1] for i in range(len(valid) - 1)):
            return "BEARISH_STACK"
        return "BELOW_ALL"

    return "MIXED"


# ---------------------------------------------------------------------------
# Tape Read
# ---------------------------------------------------------------------------

# Signal weights (must sum to 1.0)
_SIGNAL_WEIGHTS = {
    "EMA Alignment": 0.25,
    "RSI": 0.20,
    "MACD": 0.20,
    "ADX + Trend": 0.15,
    "OBV": 0.10,
    "Rel. Volume": 0.10,
}


def _compute_tape_read(
    price: float,
    ema9: float | None,
    ema21: float | None,
    ema50: float | None,
    ema200: float | None,
    ema_alignment: str | None,
    rsi: float | None,
    macd_result: MACDResult | None,
    adx_result: ADXResult | None,
    obv_trend: str | None,
    relative_volume: float | None,
) -> tuple[list[TapeSignal], str, float]:
    """Compute the Tape Read verdict from indicator signals.

    Returns:
        (tape_signals, verdict, bullish_pct)
    """
    signals: list[TapeSignal] = []

    # --- EMA Alignment (weight 0.25) ---
    ema_score = 0.0
    if ema_alignment == "BULLISH_STACK":
        ema_score = 1.0
        reading = "Bullish stack (Price > 9 > 21 > 50 > 200)"
    elif ema_alignment == "ABOVE_ALL":
        ema_score = 0.5
        reading = "Price above all EMAs"
    elif ema_alignment == "BEARISH_STACK":
        ema_score = -1.0
        reading = "Bearish stack (Price < 9 < 21 < 50 < 200)"
    elif ema_alignment == "BELOW_ALL":
        ema_score = -0.5
        reading = "Price below all EMAs"
    else:
        reading = "Mixed alignment"

    signal_label = "BULLISH" if ema_score > 0 else ("BEARISH" if ema_score < 0 else "NEUTRAL")
    signals.append(TapeSignal(
        name="EMA Alignment", reading=reading,
        signal=signal_label, weight=_SIGNAL_WEIGHTS["EMA Alignment"],
    ))

    # --- RSI (weight 0.20) ---
    rsi_score = 0.0
    if rsi is not None:
        if 50 <= rsi <= 70:
            rsi_score = 1.0
            reading = f"RSI {rsi:.0f} — healthy momentum"
        elif 70 < rsi <= 80:
            rsi_score = 0.5
            reading = f"RSI {rsi:.0f} — overbought warning"
        elif rsi > 80:
            rsi_score = 0.0
            reading = f"RSI {rsi:.0f} — extreme overbought"
        elif 40 <= rsi < 50:
            rsi_score = -0.5
            reading = f"RSI {rsi:.0f} — weakening"
        else:
            rsi_score = -1.0
            reading = f"RSI {rsi:.0f} — weak / oversold"
    else:
        reading = "RSI unavailable"

    signal_label = "BULLISH" if rsi_score > 0 else ("BEARISH" if rsi_score < 0 else "NEUTRAL")
    signals.append(TapeSignal(
        name="RSI", reading=reading,
        signal=signal_label, weight=_SIGNAL_WEIGHTS["RSI"],
    ))

    # --- MACD (weight 0.20) ---
    macd_score = 0.0
    if macd_result is not None:
        if macd_result.histogram > 0:
            macd_score = 1.0
            reading = f"Histogram +{macd_result.histogram:.3f}"
        elif macd_result.histogram < 0:
            macd_score = -1.0
            reading = f"Histogram {macd_result.histogram:.3f}"
        else:
            reading = "Histogram at zero"
    else:
        reading = "MACD unavailable"

    signal_label = "BULLISH" if macd_score > 0 else ("BEARISH" if macd_score < 0 else "NEUTRAL")
    signals.append(TapeSignal(
        name="MACD", reading=reading,
        signal=signal_label, weight=_SIGNAL_WEIGHTS["MACD"],
    ))

    # --- ADX + Trend (weight 0.15) ---
    adx_score = 0.0
    if adx_result is not None:
        if adx_result.adx >= 25:
            if adx_result.plus_di > adx_result.minus_di:
                adx_score = 1.0
                reading = f"ADX {adx_result.adx:.0f} — strong bullish trend"
            else:
                adx_score = -1.0
                reading = f"ADX {adx_result.adx:.0f} — strong bearish trend"
        else:
            reading = f"ADX {adx_result.adx:.0f} — no strong trend"
    else:
        reading = "ADX unavailable"

    signal_label = "BULLISH" if adx_score > 0 else ("BEARISH" if adx_score < 0 else "NEUTRAL")
    signals.append(TapeSignal(
        name="ADX + Trend", reading=reading,
        signal=signal_label, weight=_SIGNAL_WEIGHTS["ADX + Trend"],
    ))

    # --- OBV (weight 0.10) ---
    obv_score = 0.0
    if obv_trend == "RISING":
        obv_score = 1.0
        reading = "Accumulation (OBV rising)"
    elif obv_trend == "FALLING":
        obv_score = -1.0
        reading = "Distribution (OBV falling)"
    elif obv_trend is not None:
        reading = "OBV flat"
    else:
        reading = "OBV unavailable"

    signal_label = "BULLISH" if obv_score > 0 else ("BEARISH" if obv_score < 0 else "NEUTRAL")
    signals.append(TapeSignal(
        name="OBV", reading=reading,
        signal=signal_label, weight=_SIGNAL_WEIGHTS["OBV"],
    ))

    # --- Relative Volume (weight 0.10) ---
    rvol_score = 0.0
    if relative_volume is not None:
        if relative_volume > 1.5:
            rvol_score = 1.0
            reading = f"{relative_volume:.1f}x avg — high conviction"
        elif relative_volume < 0.7:
            rvol_score = -1.0
            reading = f"{relative_volume:.1f}x avg — low participation"
        else:
            reading = f"{relative_volume:.1f}x avg — normal"
    else:
        reading = "Volume data unavailable"

    signal_label = "BULLISH" if rvol_score > 0 else ("BEARISH" if rvol_score < 0 else "NEUTRAL")
    signals.append(TapeSignal(
        name="Rel. Volume", reading=reading,
        signal=signal_label, weight=_SIGNAL_WEIGHTS["Rel. Volume"],
    ))

    # --- Weighted sum ---
    scores = [ema_score, rsi_score, macd_score, adx_score, obv_score, rvol_score]
    weights = list(_SIGNAL_WEIGHTS.values())
    weighted_sum = sum(s * w for s, w in zip(scores, weights))
    total_weight = sum(weights)

    bullish_pct = (weighted_sum + total_weight) / (2 * total_weight) * 100
    bullish_pct = max(0.0, min(100.0, bullish_pct))

    if bullish_pct >= 70:
        verdict = "BULLISH"
    elif bullish_pct >= 55:
        verdict = "LEAN_BULLISH"
    elif bullish_pct >= 45:
        verdict = "NEUTRAL"
    elif bullish_pct >= 31:
        verdict = "LEAN_BEARISH"
    else:
        verdict = "BEARISH"

    return signals, verdict, round(bullish_pct, 1)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def compute_stock_technicals(
    ticker: str,
    bars: Sequence[DailyBar],
    ticker_details: dict | None,
) -> StockTechnicals:
    """Compute all stock technicals from daily bars and ticker details.

    Args:
        ticker: Stock ticker symbol.
        bars: Daily OHLCV bars, oldest first. Should be ~252 bars.
        ticker_details: Dict from Polygon /v3/reference/tickers/{ticker}.

    Returns:
        StockTechnicals with all computed fields.
    """
    if not bars:
        return StockTechnicals(ticker=ticker, price=0.0)

    closes = [b.close for b in bars]
    price = closes[-1]

    # --- Profile from ticker details ---
    company_name = None
    sector = None
    market_cap = None
    homepage_url = None

    if ticker_details:
        company_name = ticker_details.get("name")
        sector = ticker_details.get("sic_description")
        market_cap = ticker_details.get("market_cap")
        homepage_url = ticker_details.get("homepage_url")
        if market_cap is not None:
            market_cap = int(market_cap)

    # --- Price context ---
    prev_close = closes[-2] if len(closes) >= 2 else None
    change_dollar = None
    change_pct = None
    if prev_close and prev_close > 0:
        change_dollar = round(price - prev_close, 2)
        change_pct = round((price - prev_close) / prev_close * 100, 2)

    high_52w = calculate_n_day_high(bars, min(252, len(bars)))
    low_52w = calculate_n_day_low(bars, min(252, len(bars)))
    pct_from_52w_high = None
    if high_52w and high_52w > 0:
        pct_from_52w_high = round((price - high_52w) / high_52w * 100, 2)

    volume = int(bars[-1].volume)
    volumes = [b.volume for b in bars]
    avg_volume_20d = None
    relative_volume = None
    if len(volumes) >= 21:
        # Average of the 20 days before today
        avg_volume_20d = sum(volumes[-21:-1]) / 20
        if avg_volume_20d > 0:
            relative_volume = round(volume / avg_volume_20d, 2)

    # --- EMAs ---
    ema_9 = calculate_ema(closes, 9)
    ema_21 = calculate_ema(closes, 21)
    ema_50 = calculate_ema(closes, 50)
    ema_200 = calculate_ema(closes, 200)
    ema_alignment = classify_ema_alignment(price, ema_9, ema_21, ema_50, ema_200)

    # --- Indicators ---
    rsi_14 = calculate_rsi(closes)
    macd_result = calculate_macd(closes)
    adx_result = calculate_adx(bars)
    obv_data = calculate_obv_trend(bars)
    obv_trend_str = obv_data["trend"] if obv_data else None

    # --- Tape Read ---
    tape_signals, tape_verdict, tape_bullish_pct = _compute_tape_read(
        price=price,
        ema9=ema_9, ema21=ema_21, ema50=ema_50, ema200=ema_200,
        ema_alignment=ema_alignment,
        rsi=rsi_14,
        macd_result=macd_result,
        adx_result=adx_result,
        obv_trend=obv_trend_str,
        relative_volume=relative_volume,
    )

    return StockTechnicals(
        ticker=ticker,
        company_name=company_name,
        sector=sector,
        market_cap=market_cap,
        homepage_url=homepage_url,
        price=round(price, 2),
        prev_close=round(prev_close, 2) if prev_close else None,
        change_dollar=change_dollar,
        change_pct=change_pct,
        high_52w=round(high_52w, 2) if high_52w else None,
        low_52w=round(low_52w, 2) if low_52w else None,
        pct_from_52w_high=pct_from_52w_high,
        volume=volume,
        avg_volume_20d=round(avg_volume_20d, 0) if avg_volume_20d else None,
        relative_volume=relative_volume,
        ema_9=round(ema_9, 2) if ema_9 else None,
        ema_21=round(ema_21, 2) if ema_21 else None,
        ema_50=round(ema_50, 2) if ema_50 else None,
        ema_200=round(ema_200, 2) if ema_200 else None,
        ema_alignment=ema_alignment,
        rsi_14=round(rsi_14, 1) if rsi_14 is not None else None,
        macd=round(macd_result.macd, 4) if macd_result else None,
        macd_signal=round(macd_result.signal, 4) if macd_result else None,
        macd_histogram=round(macd_result.histogram, 4) if macd_result else None,
        adx_14=round(adx_result.adx, 1) if adx_result else None,
        plus_di=round(adx_result.plus_di, 1) if adx_result else None,
        minus_di=round(adx_result.minus_di, 1) if adx_result else None,
        obv_trend=obv_trend_str,
        tape_signals=tape_signals,
        tape_verdict=tape_verdict,
        tape_bullish_pct=tape_bullish_pct,
    )
