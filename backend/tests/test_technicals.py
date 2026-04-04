"""Tests for technical indicator calculations."""

from __future__ import annotations

import pytest

from app.services.polygon import DailyBar
from app.services.technicals import (
    ADXResult,
    MACDResult,
    calculate_adx,
    calculate_ema,
    calculate_macd,
    calculate_obv_trend,
    calculate_rsi,
    compute_stock_technicals,
    classify_ema_alignment,
    _compute_tape_read,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bars(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[int] | None = None,
) -> list[DailyBar]:
    """Create DailyBar list from price lists."""
    n = len(closes)
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    if volumes is None:
        volumes = [1_000_000] * n

    return [
        DailyBar(
            ticker="TEST",
            date=f"2025-01-{i + 1:02d}",
            open=closes[i],
            high=highs[i],
            low=lows[i],
            close=closes[i],
            volume=volumes[i],
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# EMA Tests
# ---------------------------------------------------------------------------


class TestEMA:
    def test_insufficient_data(self):
        assert calculate_ema([100, 101], 5) is None

    def test_exact_period_returns_sma(self):
        closes = [10.0, 11.0, 12.0, 13.0, 14.0]
        result = calculate_ema(closes, 5)
        assert result == pytest.approx(12.0)  # SMA of [10,11,12,13,14]

    def test_ema_responds_to_price_changes(self):
        closes = [100.0] * 10 + [110.0]  # Flat, then spike
        ema = calculate_ema(closes, 10)
        assert ema is not None
        assert ema > 100.0  # Should have moved up from spike
        assert ema < 110.0  # But not fully to the new price

    def test_ema_with_known_values(self):
        # 5-period EMA, multiplier = 2/(5+1) = 0.3333
        closes = [22.0, 22.5, 22.3, 22.7, 23.0, 23.5]
        ema = calculate_ema(closes, 5)
        assert ema is not None
        # SMA seed = (22 + 22.5 + 22.3 + 22.7 + 23) / 5 = 22.5
        # EMA = 23.5 * 0.3333 + 22.5 * 0.6667 = 7.833 + 15.0 = 22.833
        assert ema == pytest.approx(22.833, abs=0.01)


# ---------------------------------------------------------------------------
# RSI Tests
# ---------------------------------------------------------------------------


class TestRSI:
    def test_insufficient_data(self):
        assert calculate_rsi([100.0] * 10) is None

    def test_all_gains_returns_100(self):
        closes = [float(i) for i in range(100, 120)]  # Monotonically increasing
        rsi = calculate_rsi(closes, 14)
        assert rsi == 100.0

    def test_all_losses_returns_0(self):
        closes = [float(200 - i) for i in range(20)]  # Monotonically decreasing
        rsi = calculate_rsi(closes, 14)
        assert rsi == pytest.approx(0.0, abs=0.1)

    def test_rsi_in_valid_range(self):
        # Alternating up/down should give mid-range RSI
        closes = []
        price = 100.0
        for i in range(50):
            if i % 2 == 0:
                price += 1.5
            else:
                price -= 1.0
            closes.append(price)

        rsi = calculate_rsi(closes, 14)
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_flat_price_returns_50(self):
        # Alternating +1, -1 gives equal avg gain and avg loss
        closes = [100.0]
        for i in range(30):
            if i % 2 == 0:
                closes.append(closes[-1] + 1)
            else:
                closes.append(closes[-1] - 1)

        rsi = calculate_rsi(closes, 14)
        assert rsi is not None
        assert 40 < rsi < 60  # Approximately 50


# ---------------------------------------------------------------------------
# MACD Tests
# ---------------------------------------------------------------------------


class TestMACD:
    def test_insufficient_data(self):
        assert calculate_macd([100.0] * 30) is None  # Need 26+9=35

    def test_macd_with_trending_data(self):
        # Uptrend: MACD line should be positive
        closes = [100.0 + i * 0.5 for i in range(50)]
        result = calculate_macd(closes)
        assert result is not None
        assert result.macd > 0  # Fast EMA > Slow EMA in uptrend

    def test_macd_with_downtrend(self):
        closes = [200.0 - i * 0.5 for i in range(50)]
        result = calculate_macd(closes)
        assert result is not None
        assert result.macd < 0

    def test_macd_histogram_is_difference(self):
        closes = [100.0 + i * 0.3 for i in range(50)]
        result = calculate_macd(closes)
        assert result is not None
        assert result.histogram == pytest.approx(result.macd - result.signal, abs=1e-10)


# ---------------------------------------------------------------------------
# ADX Tests
# ---------------------------------------------------------------------------


class TestADX:
    def test_insufficient_data(self):
        bars = _make_bars([100.0] * 20)
        assert calculate_adx(bars, 14) is None  # Need 29+ bars

    def test_adx_with_trending_data(self):
        # Strong uptrend
        closes = [100.0 + i * 2.0 for i in range(40)]
        highs = [c + 3 for c in closes]
        lows = [c - 1 for c in closes]
        bars = _make_bars(closes, highs=highs, lows=lows)

        result = calculate_adx(bars)
        assert result is not None
        assert result.adx > 20  # Should show trend
        assert result.plus_di > result.minus_di  # Uptrend: +DI > -DI

    def test_adx_returns_valid_values(self):
        closes = [100.0 + i * 0.5 for i in range(40)]
        bars = _make_bars(closes)
        result = calculate_adx(bars)
        assert result is not None
        assert 0 <= result.adx <= 100
        assert result.plus_di >= 0
        assert result.minus_di >= 0


# ---------------------------------------------------------------------------
# OBV Tests
# ---------------------------------------------------------------------------


class TestOBV:
    def test_insufficient_data(self):
        bars = _make_bars([100.0] * 10)
        assert calculate_obv_trend(bars, lookback=20) is None

    def test_rising_obv(self):
        # Prices going up -> volume accumulates
        closes = [100.0 + i for i in range(30)]
        volumes = [1_000_000] * 30
        bars = _make_bars(closes, volumes=volumes)

        result = calculate_obv_trend(bars)
        assert result is not None
        assert result["trend"] == "RISING"

    def test_falling_obv(self):
        closes = [200.0 - i for i in range(30)]
        volumes = [1_000_000] * 30
        bars = _make_bars(closes, volumes=volumes)

        result = calculate_obv_trend(bars)
        assert result is not None
        assert result["trend"] == "FALLING"

    def test_flat_obv(self):
        # Alternating up/down with equal volume -> flat
        closes = []
        price = 100.0
        for i in range(30):
            if i % 2 == 0:
                price += 1
            else:
                price -= 1
            closes.append(price)
        volumes = [1_000_000] * 30
        bars = _make_bars(closes, volumes=volumes)

        result = calculate_obv_trend(bars)
        assert result is not None
        assert result["trend"] == "FLAT"


# ---------------------------------------------------------------------------
# EMA Alignment Tests
# ---------------------------------------------------------------------------


class TestEMAAlignment:
    def test_bullish_stack(self):
        result = classify_ema_alignment(
            price=110, ema9=108, ema21=105, ema50=100, ema200=90
        )
        assert result == "BULLISH_STACK"

    def test_bearish_stack(self):
        result = classify_ema_alignment(
            price=80, ema9=82, ema21=85, ema50=90, ema200=100
        )
        assert result == "BEARISH_STACK"

    def test_above_all_no_stack(self):
        # Price above all but EMAs not in descending order
        result = classify_ema_alignment(
            price=110, ema9=105, ema21=108, ema50=100, ema200=90
        )
        assert result == "ABOVE_ALL"

    def test_below_all(self):
        result = classify_ema_alignment(
            price=80, ema9=90, ema21=85, ema50=95, ema200=100
        )
        assert result == "BELOW_ALL"

    def test_mixed(self):
        result = classify_ema_alignment(
            price=100, ema9=95, ema21=105, ema50=90, ema200=110
        )
        assert result == "MIXED"

    def test_partial_emas(self):
        result = classify_ema_alignment(
            price=110, ema9=108, ema21=105, ema50=100, ema200=None
        )
        assert result == "BULLISH_STACK"


# ---------------------------------------------------------------------------
# Tape Read Tests
# ---------------------------------------------------------------------------


class TestTapeRead:
    def test_all_bullish(self):
        signals, verdict, pct = _compute_tape_read(
            price=110,
            ema9=108, ema21=105, ema50=100, ema200=90,
            ema_alignment="BULLISH_STACK",
            rsi=60.0,
            macd_result=MACDResult(macd=1.0, signal=0.5, histogram=0.5),
            adx_result=ADXResult(adx=30.0, plus_di=25.0, minus_di=15.0),
            obv_trend="RISING",
            relative_volume=2.0,
        )
        assert verdict == "BULLISH"
        assert pct >= 70

    def test_all_bearish(self):
        signals, verdict, pct = _compute_tape_read(
            price=80,
            ema9=82, ema21=85, ema50=90, ema200=100,
            ema_alignment="BEARISH_STACK",
            rsi=30.0,
            macd_result=MACDResult(macd=-1.0, signal=-0.5, histogram=-0.5),
            adx_result=ADXResult(adx=30.0, plus_di=10.0, minus_di=25.0),
            obv_trend="FALLING",
            relative_volume=0.5,
        )
        assert verdict == "BEARISH"
        assert pct <= 30

    def test_neutral(self):
        signals, verdict, pct = _compute_tape_read(
            price=100,
            ema9=99, ema21=101, ema50=98, ema200=102,
            ema_alignment="MIXED",
            rsi=45.0,  # 40-50 range scores -0.5, keeps overall near neutral
            macd_result=MACDResult(macd=0.0, signal=0.0, histogram=0.0),
            adx_result=ADXResult(adx=15.0, plus_di=20.0, minus_di=20.0),
            obv_trend="FLAT",
            relative_volume=1.0,
        )
        assert verdict == "NEUTRAL"
        assert 40 <= pct <= 60

    def test_missing_data_defaults_neutral(self):
        signals, verdict, pct = _compute_tape_read(
            price=100,
            ema9=None, ema21=None, ema50=None, ema200=None,
            ema_alignment="MIXED",
            rsi=None,
            macd_result=None,
            adx_result=None,
            obv_trend=None,
            relative_volume=None,
        )
        assert verdict == "NEUTRAL"
        assert pct == 50.0

    def test_signal_count(self):
        signals, _, _ = _compute_tape_read(
            price=100,
            ema9=99, ema21=98, ema50=97, ema200=96,
            ema_alignment="BULLISH_STACK",
            rsi=55.0,
            macd_result=MACDResult(macd=0.5, signal=0.3, histogram=0.2),
            adx_result=ADXResult(adx=25.0, plus_di=20.0, minus_di=15.0),
            obv_trend="RISING",
            relative_volume=1.0,
        )
        assert len(signals) == 6


# ---------------------------------------------------------------------------
# Orchestrator Tests
# ---------------------------------------------------------------------------


class TestComputeStockTechnicals:
    def test_empty_bars(self):
        result = compute_stock_technicals("AAPL", [], None)
        assert result.ticker == "AAPL"
        assert result.price == 0.0
        assert result.tape_verdict == "NEUTRAL"

    def test_with_minimal_bars(self):
        closes = [100.0 + i * 0.5 for i in range(50)]
        bars = _make_bars(closes)
        result = compute_stock_technicals("AAPL", bars, None)

        assert result.ticker == "AAPL"
        assert result.price == closes[-1]
        assert result.ema_9 is not None
        assert result.ema_21 is not None
        assert result.rsi_14 is not None

    def test_with_ticker_details(self):
        closes = [100.0 + i * 0.5 for i in range(50)]
        bars = _make_bars(closes)
        details = {
            "name": "Apple Inc.",
            "sic_description": "Technology",
            "market_cap": 3_000_000_000_000,
            "homepage_url": "https://apple.com",
        }
        result = compute_stock_technicals("AAPL", bars, details)

        assert result.company_name == "Apple Inc."
        assert result.sector == "Technology"
        assert result.market_cap == 3_000_000_000_000
        assert result.homepage_url == "https://apple.com"

    def test_full_252_bars(self):
        # Simulate a realistic uptrending stock
        closes = [150.0]
        for i in range(251):
            closes.append(closes[-1] + (0.3 if i % 3 != 0 else -0.1))
        bars = _make_bars(closes)
        result = compute_stock_technicals("AAPL", bars, None)

        assert result.ema_200 is not None
        assert result.adx_14 is not None
        assert result.macd is not None
        assert result.high_52w is not None
        assert result.low_52w is not None
        assert result.tape_verdict in [
            "BULLISH", "LEAN_BULLISH", "NEUTRAL", "LEAN_BEARISH", "BEARISH"
        ]
        assert len(result.tape_signals) == 6

    def test_price_context_calculations(self):
        closes = [100.0, 102.0]
        bars = _make_bars(closes)
        result = compute_stock_technicals("TEST", bars, None)

        assert result.price == 102.0
        assert result.prev_close == 100.0
        assert result.change_dollar == 2.0
        assert result.change_pct == pytest.approx(2.0)
