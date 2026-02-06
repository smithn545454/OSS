"""Tests for scanner utility functions."""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from app.scanners.utils import (
    calculate_atr,
    calculate_atr_series,
    calculate_iv_proxy,
    calculate_n_day_high,
    calculate_n_day_low,
    calculate_pct_change,
    calculate_returns,
    calculate_rv,
    calculate_sma,
    calculate_technical_data,
    calculate_true_range,
    calculate_volume_ratio,
)
from app.services.polygon import DailyBar


class TestCalculateSMA:
    """Tests for Simple Moving Average calculation."""

    def test_sma_basic(self):
        """Test basic SMA calculation."""
        closes = [10.0, 11.0, 12.0, 13.0, 14.0]
        result = calculate_sma(closes, 5)
        assert result == 12.0  # (10 + 11 + 12 + 13 + 14) / 5

    def test_sma_insufficient_data(self):
        """Test SMA with insufficient data returns None."""
        closes = [10.0, 11.0, 12.0]
        result = calculate_sma(closes, 5)
        assert result is None

    def test_sma_uses_last_n_values(self):
        """Test SMA uses the last N values."""
        closes = [5.0, 6.0, 10.0, 11.0, 12.0, 13.0, 14.0]
        result = calculate_sma(closes, 5)
        assert result == 12.0  # Last 5: 10, 11, 12, 13, 14

    def test_sma_period_20(self):
        """Test SMA with 20-period."""
        closes = [float(i) for i in range(1, 22)]  # 1 to 21
        result = calculate_sma(closes, 20)
        # Last 20: 2-21, average = (2+21)/2 = 11.5
        assert result == 11.5


class TestCalculateReturns:
    """Tests for N-day return calculation."""

    def test_returns_basic(self):
        """Test basic return calculation."""
        # 5-day return: (110 - 100) / 100 * 100 = 10%
        closes = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
        result = calculate_returns(closes, 5)
        assert result == 10.0

    def test_returns_negative(self):
        """Test negative return calculation."""
        closes = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0]
        result = calculate_returns(closes, 5)
        assert result == -10.0

    def test_returns_insufficient_data(self):
        """Test returns with insufficient data."""
        closes = [100.0, 105.0]
        result = calculate_returns(closes, 5)
        assert result is None


class TestCalculateTrueRange:
    """Tests for True Range calculation."""

    def test_true_range_high_low(self):
        """Test TR when high-low is largest."""
        result = calculate_true_range(high=110.0, low=90.0, prev_close=100.0)
        assert result == 20.0  # 110 - 90

    def test_true_range_high_prev_close(self):
        """Test TR when |high - prev_close| is largest."""
        result = calculate_true_range(high=120.0, low=105.0, prev_close=100.0)
        assert result == 20.0  # |120 - 100|

    def test_true_range_low_prev_close(self):
        """Test TR when |low - prev_close| is largest."""
        result = calculate_true_range(high=95.0, low=80.0, prev_close=100.0)
        assert result == 20.0  # |80 - 100|


class TestCalculateATR:
    """Tests for Average True Range calculation."""

    def _create_bars(self, prices: list[tuple[float, float, float]]) -> list[DailyBar]:
        """Create DailyBar list from (high, low, close) tuples."""
        return [
            DailyBar(
                ticker="TEST",
                date=f"2024-01-{i+1:02d}",
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1000,
            )
            for i, (high, low, close) in enumerate(prices)
        ]

    def test_atr_basic(self):
        """Test basic ATR calculation."""
        # Create bars with constant TR of 10
        prices = [(110.0, 100.0, 105.0)] * 20
        bars = self._create_bars(prices)

        result = calculate_atr(bars, period=14)
        # TR for each bar after first is 10 (110-100)
        assert result is not None
        assert round(result, 2) == 10.0

    def test_atr_insufficient_data(self):
        """Test ATR with insufficient data."""
        prices = [(110.0, 100.0, 105.0)] * 10
        bars = self._create_bars(prices)

        result = calculate_atr(bars, period=14)
        assert result is None


class TestCalculateRV:
    """Tests for Realized Volatility calculation."""

    def test_rv_basic(self):
        """Test RV calculation returns positive value."""
        # Generate some varying closes
        closes = [100.0 + i * 0.5 for i in range(25)]  # Slight uptrend
        result = calculate_rv(closes, window=20)

        assert result is not None
        assert result > 0

    def test_rv_higher_volatility(self):
        """Test that higher volatility produces higher RV."""
        # Low volatility: small changes
        low_vol_closes = [100.0 + (i % 2) * 0.1 for i in range(25)]
        rv_low = calculate_rv(low_vol_closes, window=20)

        # High volatility: larger changes
        high_vol_closes = [100.0 + (i % 2) * 5.0 for i in range(25)]
        rv_high = calculate_rv(high_vol_closes, window=20)

        assert rv_low is not None
        assert rv_high is not None
        assert rv_high > rv_low

    def test_rv_insufficient_data(self):
        """Test RV with insufficient data."""
        closes = [100.0] * 15  # Need 21 for 20 returns
        result = calculate_rv(closes, window=20)
        assert result is None


class TestCalculateNDayHighLow:
    """Tests for N-day high/low calculations."""

    def _create_bars(self, prices: list[tuple[float, float, float]]) -> list[DailyBar]:
        """Create DailyBar list from (high, low, close) tuples."""
        return [
            DailyBar(
                ticker="TEST",
                date=f"2024-01-{i+1:02d}",
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1000,
            )
            for i, (high, low, close) in enumerate(prices)
        ]

    def test_n_day_high(self):
        """Test N-day high calculation."""
        prices = [
            (100.0, 95.0, 98.0),
            (105.0, 99.0, 102.0),
            (110.0, 104.0, 108.0),
            (108.0, 103.0, 105.0),
            (106.0, 101.0, 103.0),
        ]
        bars = self._create_bars(prices)

        result = calculate_n_day_high(bars, n=5)
        assert result == 110.0

    def test_n_day_low(self):
        """Test N-day low calculation."""
        prices = [
            (100.0, 95.0, 98.0),
            (105.0, 90.0, 102.0),
            (110.0, 104.0, 108.0),
            (108.0, 103.0, 105.0),
            (106.0, 101.0, 103.0),
        ]
        bars = self._create_bars(prices)

        result = calculate_n_day_low(bars, n=5)
        assert result == 90.0


class TestCalculateVolumeRatio:
    """Tests for volume ratio calculation."""

    def test_volume_ratio_basic(self):
        """Test basic volume ratio."""
        result = calculate_volume_ratio(200, 100.0)
        assert result == 2.0

    def test_volume_ratio_zero_avg(self):
        """Test volume ratio with zero average."""
        result = calculate_volume_ratio(100, 0.0)
        assert result == float("inf")

    def test_volume_ratio_both_zero(self):
        """Test volume ratio when both are zero."""
        result = calculate_volume_ratio(0, 0.0)
        assert result == 0.0


class TestCalculatePctChange:
    """Tests for percentage change calculation."""

    def test_pct_change_positive(self):
        """Test positive percentage change."""
        result = calculate_pct_change(110.0, 100.0)
        assert result == 10.0

    def test_pct_change_negative(self):
        """Test negative percentage change."""
        result = calculate_pct_change(90.0, 100.0)
        assert result == -10.0

    def test_pct_change_zero_previous(self):
        """Test percentage change with zero previous."""
        result = calculate_pct_change(100.0, 0.0)
        assert result is None


class TestCalculateTechnicalData:
    """Tests for comprehensive technical data calculation."""

    def _create_bars(self, closes: list[float]) -> list[DailyBar]:
        """Create DailyBar list from closes."""
        return [
            DailyBar(
                ticker="TEST",
                date=f"2024-01-{i+1:02d}",
                open=close,
                high=close * 1.02,
                low=close * 0.98,
                close=close,
                volume=1000,
            )
            for i, close in enumerate(closes)
        ]

    def test_technical_data_bullish_trend(self):
        """Test technical data with bullish trend alignment."""
        # Create uptrending closes: close > sma20 > sma50
        closes = [100.0 + i * 0.5 for i in range(60)]
        bars = self._create_bars(closes)

        result = calculate_technical_data(bars)

        assert result is not None
        assert result.close == closes[-1]
        assert result.sma20 is not None
        assert result.sma50 is not None
        assert result.trend_aligned_bullish is True
        assert result.trend_aligned_bearish is False

    def test_technical_data_bearish_trend(self):
        """Test technical data with bearish trend alignment."""
        # Create downtrending closes
        closes = [150.0 - i * 0.5 for i in range(60)]
        bars = self._create_bars(closes)

        result = calculate_technical_data(bars)

        assert result is not None
        assert result.trend_aligned_bullish is False
        assert result.trend_aligned_bearish is True

    def test_technical_data_empty_bars(self):
        """Test technical data with empty bars."""
        result = calculate_technical_data([])
        assert result is None
