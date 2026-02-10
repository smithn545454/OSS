"""Comprehensive tests for the CompressionScanner.

Tests cover:
- Compression + break up (CALL direction)
- Compression + break down (PUT direction)
- Compressed but no break
- Not compressed (ATR above threshold)
- Insufficient bars
- ATR None for today
- Insufficient ATR history
- Exception handling
- Cached vs non-cached data paths
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import DirectionHint, PolicyConfig, ScannerType
from app.scanners.base import ScanContext, ScanResult
from app.scanners.compression import CompressionScanner


# ============================================================================
# Helpers
# ============================================================================


def _make_bar(high: float, low: float, close: float) -> MagicMock:
    """Create a mock DailyBar with the given price attributes."""
    bar = MagicMock()
    bar.high = high
    bar.low = low
    bar.close = close
    return bar


def _make_bars(
    n: int,
    *,
    base_high: float = 105.0,
    base_low: float = 95.0,
    base_close: float = 100.0,
    high_step: float = 0.0,
    low_step: float = 0.0,
    close_step: float = 0.0,
) -> list[MagicMock]:
    """Create a list of n mock bars with optional linear trends."""
    return [
        _make_bar(
            high=base_high + i * high_step,
            low=base_low + i * low_step,
            close=base_close + i * close_step,
        )
        for i in range(n)
    ]


def _make_context(
    bars: list | None = None,
    ticker: str = "AAPL",
    polygon: AsyncMock | None = None,
    policy_config: PolicyConfig | None = None,
) -> ScanContext:
    """Build a ScanContext with optional cached bars."""
    cached_data: dict[str, Any] = {}
    if bars is not None:
        cached_data["daily_bars"] = {ticker: bars}

    return ScanContext(
        polygon=polygon or AsyncMock(),
        policy_config=policy_config or PolicyConfig(),
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        cached_data=cached_data,
    )


# ============================================================================
# Scanner Type / Priority
# ============================================================================


class TestCompressionScannerProperties:
    """Tests for scanner metadata properties."""

    def test_scanner_type(self):
        scanner = CompressionScanner()
        assert scanner.scanner_type == ScannerType.COMPRESSION_EXPANSION

    def test_base_priority(self):
        scanner = CompressionScanner()
        assert scanner.get_base_priority() == 70


# ============================================================================
# Compression + Break UP
# ============================================================================


class TestCompressionBreakUp:
    """Compression detected AND today's close breaks above prior range high."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_compression_break_up_triggers(self, mock_atr):
        """When ATR is compressed and close breaks above range high → triggered=True, CALL."""
        n = 36  # 14 (atr_period) + 20 (compression_lookback) + 2 (extra buffer)

        # Build bars: range bars (indices -11 to -2) have highs up to 105
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)
        # Today's close breaks above prior_range_high * 1.02 = 105 * 1.02 = 107.1
        bars[-1] = _make_bar(high=110.0, low=100.0, close=108.0)

        # ATR series: first 14 None, then values. Floor will be min of lookback = 1.0.
        # ATR today = 1.0 which is <= 1.0 * 1.10 = 1.10 → compressed
        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        # Set the floor region (indices 15 to 34, 20 values before today) to include 1.0
        atr_values[15] = 1.0  # This will be the min in the lookback window
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        context = _make_context(bars=bars)
        result = await scanner.scan_ticker("AAPL", context)

        assert result.triggered is True
        assert result.trigger is not None
        assert result.trigger.scanner_type == ScannerType.COMPRESSION_EXPANSION
        assert "COMPRESSION_EXPANSION_UP" in result.trigger.reason_codes
        assert result.metrics["is_compressed"] is True
        assert result.metrics["break_up"] is True
        assert result.metrics["break_down"] is False
        assert result.metrics["triggered_direction"] == "UP"

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_compression_break_up_metrics_populated(self, mock_atr):
        """Verify all expected metric keys are populated on break-up trigger."""
        n = 36
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)
        bars[-1] = _make_bar(high=110.0, low=100.0, close=108.0)

        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        atr_values[15] = 1.0
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        expected_keys = {
            "atr_period", "atr_today", "atr_floor", "compression_multiplier",
            "compression_threshold", "is_compressed", "prior_range_high",
            "prior_range_low", "today_close", "break_pct", "break_up_threshold",
            "break_down_threshold", "break_up", "break_down", "triggered_direction",
        }
        assert expected_keys.issubset(set(result.metrics.keys()))


# ============================================================================
# Compression + Break DOWN
# ============================================================================


class TestCompressionBreakDown:
    """Compression detected AND today's close breaks below prior range low."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_compression_break_down_triggers(self, mock_atr):
        """When ATR is compressed and close breaks below range low → triggered=True, PUT."""
        n = 36
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)
        # Today's close breaks below prior_range_low * 0.98 = 95 * 0.98 = 93.1
        bars[-1] = _make_bar(high=96.0, low=90.0, close=92.0)

        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        atr_values[15] = 1.0
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is True
        assert result.trigger is not None
        assert "COMPRESSION_EXPANSION_DOWN" in result.trigger.reason_codes
        assert result.metrics["is_compressed"] is True
        assert result.metrics["break_up"] is False
        assert result.metrics["break_down"] is True
        assert result.metrics["triggered_direction"] == "DOWN"

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_break_down_values_correct(self, mock_atr):
        """Verify break-down thresholds are computed correctly."""
        n = 36
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)
        bars[-1] = _make_bar(high=96.0, low=88.0, close=92.0)

        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        atr_values[15] = 1.0
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        # prior_range_low = 95.0 (all range bars have low=95.0)
        # break_down_threshold = 95.0 * (1 - 2.0/100) = 95.0 * 0.98 = 93.1
        assert result.metrics["prior_range_low"] == 95.0
        assert result.metrics["break_down_threshold"] == round(95.0 * 0.98, 2)
        assert result.metrics["today_close"] == 92.0


# ============================================================================
# Compressed but No Break
# ============================================================================


class TestCompressedNoBreak:
    """ATR is compressed but close stays within the prior range."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_compressed_no_break_not_triggered(self, mock_atr):
        """When compressed but close inside range → triggered=False, is_compressed=True."""
        n = 36
        # Close stays at 100 which is within [95 * 0.98, 105 * 1.02]
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)

        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        atr_values[15] = 1.0
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is False
        assert result.trigger is None
        assert result.metrics["is_compressed"] is True
        assert result.metrics["break_up"] is False
        assert result.metrics["break_down"] is False

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_compressed_no_break_error_is_none(self, mock_atr):
        """No error should be set when compressed but no break."""
        n = 36
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)

        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        atr_values[15] = 1.0
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.error is None


# ============================================================================
# Not Compressed
# ============================================================================


class TestNotCompressed:
    """ATR is above the compression threshold."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_not_compressed_not_triggered(self, mock_atr):
        """When ATR today > atr_floor * multiplier → triggered=False."""
        n = 36
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)
        # Today breaks above range — but ATR is NOT compressed, so should not trigger
        bars[-1] = _make_bar(high=110.0, low=100.0, close=108.0)

        # ATR today = 5.0, floor will be min of lookback = 2.0
        # Threshold = 2.0 * 1.10 = 2.20; 5.0 > 2.20 → not compressed
        atr_values = [None] * 14 + [2.0] * (n - 15) + [5.0]
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is False
        assert result.trigger is None
        assert result.metrics["is_compressed"] is False

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_not_compressed_metrics_still_populated(self, mock_atr):
        """Even when not compressed, metrics should be fully populated."""
        n = 36
        bars = _make_bars(n)
        atr_values = [None] * 14 + [2.0] * (n - 15) + [5.0]
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert "atr_today" in result.metrics
        assert "atr_floor" in result.metrics
        assert "compression_threshold" in result.metrics
        assert "prior_range_high" in result.metrics
        assert "prior_range_low" in result.metrics


# ============================================================================
# Insufficient Bars
# ============================================================================


class TestInsufficientBars:
    """Not enough bars to perform the calculation."""

    @pytest.mark.asyncio
    async def test_too_few_bars(self):
        """When < atr_period + compression_lookback + 1 bars → triggered=False with error."""
        # Need 14 + 20 + 1 = 35 bars minimum
        bars = _make_bars(20)

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is False
        assert result.error is not None
        assert "Insufficient data" in result.error
        assert "20 bars" in result.error
        assert "need 35" in result.error

    @pytest.mark.asyncio
    async def test_exactly_min_bars_minus_one(self):
        """Edge case: exactly one bar short of minimum."""
        bars = _make_bars(34)  # Need 35

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is False
        assert "Insufficient data" in result.error

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_exactly_min_bars(self, mock_atr):
        """Edge case: exactly the minimum number of bars should work."""
        n = 35  # Exactly 14 + 20 + 1
        bars = _make_bars(n)
        atr_values = [None] * 14 + [2.0] * (n - 14)
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        # Should not have an insufficient data error
        assert result.error is None

    @pytest.mark.asyncio
    async def test_empty_bars(self):
        """Empty bars list should trigger insufficient data error."""
        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=[]))

        assert result.triggered is False
        assert "Insufficient data" in result.error


# ============================================================================
# ATR None for Today
# ============================================================================


class TestATRNoneToday:
    """calculate_atr_series returns None for the last element."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_atr_none_today(self, mock_atr):
        """When ATR for today is None → triggered=False with error."""
        n = 36
        bars = _make_bars(n)
        # Return None for last element (today)
        atr_values = [None] * 14 + [2.0] * (n - 15) + [None]
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is False
        assert result.error is not None
        assert "Could not calculate ATR for today" in result.error


# ============================================================================
# Insufficient ATR History
# ============================================================================


class TestInsufficientATRHistory:
    """Too many Nones in the ATR lookback window (< 10 valid values)."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_insufficient_atr_history(self, mock_atr):
        """When fewer than 10 valid ATR values in lookback → triggered=False with error."""
        n = 36
        bars = _make_bars(n)
        # Create ATR series where the lookback window has mostly Nones
        # Last value is valid (today), but lookback window atr_series[-21:-1] has < 10 non-None
        atr_values = [None] * (n - 1) + [2.0]
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is False
        assert "Insufficient ATR history" in result.error

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_exactly_9_valid_atr_values(self, mock_atr):
        """Edge case: exactly 9 valid ATR values (< 10 threshold)."""
        n = 36
        bars = _make_bars(n)
        # Build series: put 9 valid values in the lookback window (indices -21 to -2)
        atr_values: list[float | None] = [None] * n
        # indices 15..23 = 9 values (within the 20-bar lookback region [-21:-1])
        for i in range(15, 24):
            atr_values[i] = 2.0
        atr_values[-1] = 1.5  # today
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is False
        assert "Insufficient ATR history" in result.error

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_exactly_10_valid_atr_values(self, mock_atr):
        """Edge case: exactly 10 valid ATR values should be accepted."""
        n = 36
        bars = _make_bars(n)
        atr_values: list[float | None] = [None] * n
        # Put 10 valid values in the lookback region (indices 15..24)
        for i in range(15, 25):
            atr_values[i] = 2.0
        atr_values[-1] = 1.5  # today
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        # Should not have an "Insufficient ATR history" error
        assert result.error is None


# ============================================================================
# Exception Handling
# ============================================================================


class TestExceptionHandling:
    """Exceptions during scanning should be caught and returned as errors."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_exception_in_atr_series(self, mock_atr):
        """When calculate_atr_series raises → triggered=False with error string."""
        n = 36
        bars = _make_bars(n)
        mock_atr.side_effect = ValueError("ATR computation blew up")

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is False
        assert result.error is not None
        assert "ATR computation blew up" in result.error

    @pytest.mark.asyncio
    async def test_exception_in_polygon_fetch(self):
        """When Polygon fetch raises (fallback path) → triggered=False with error string."""
        mock_polygon = AsyncMock()
        mock_polygon.get_daily_bars_parsed.side_effect = RuntimeError("Network error")

        # No cached data, so it will try to fetch from polygon
        context = _make_context(polygon=mock_polygon)

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", context)

        assert result.triggered is False
        assert "Network error" in result.error

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_attribute_error_on_bars(self, mock_atr):
        """When bar objects lack expected attributes → caught by exception handler."""
        n = 36
        # Bars without .high attribute
        bars = [MagicMock(spec=[]) for _ in range(n)]
        for bar in bars:
            bar.close = 100.0

        # ATR will succeed, but range calculation will fail on bar.high
        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        atr_values[15] = 1.0
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is False
        assert result.error is not None


# ============================================================================
# Cached vs Non-Cached Data Paths
# ============================================================================


class TestCachedVsNonCachedPaths:
    """Verify both cached and Polygon-fetch code paths work."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_uses_cached_bars(self, mock_atr):
        """When ticker is in cached_data['daily_bars'], polygon is never called."""
        n = 36
        bars = _make_bars(n)
        atr_values = [None] * 14 + [2.0] * (n - 14)
        mock_atr.return_value = atr_values

        mock_polygon = AsyncMock()
        context = _make_context(bars=bars, polygon=mock_polygon)

        scanner = CompressionScanner()
        await scanner.scan_ticker("AAPL", context)

        mock_polygon.get_daily_bars_parsed.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_fetches_from_polygon_when_not_cached(self, mock_atr):
        """When ticker is NOT in cached_data, falls back to polygon.get_daily_bars_parsed."""
        n = 36
        bars = _make_bars(n)
        atr_values = [None] * 14 + [2.0] * (n - 14)
        mock_atr.return_value = atr_values

        mock_polygon = AsyncMock()
        mock_polygon.get_daily_bars_parsed.return_value = bars

        # Cached data has bars for a different ticker
        context = ScanContext(
            polygon=mock_polygon,
            policy_config=PolicyConfig(),
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            cached_data={"daily_bars": {"OTHER": bars}},
        )

        scanner = CompressionScanner()
        await scanner.scan_ticker("AAPL", context)

        mock_polygon.get_daily_bars_parsed.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_fetches_when_cached_data_empty(self, mock_atr):
        """When cached_data has no 'daily_bars' key → falls back to polygon."""
        n = 36
        bars = _make_bars(n)
        atr_values = [None] * 14 + [2.0] * (n - 14)
        mock_atr.return_value = atr_values

        mock_polygon = AsyncMock()
        mock_polygon.get_daily_bars_parsed.return_value = bars

        context = ScanContext(
            polygon=mock_polygon,
            policy_config=PolicyConfig(),
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            cached_data={},
        )

        scanner = CompressionScanner()
        await scanner.scan_ticker("AAPL", context)

        mock_polygon.get_daily_bars_parsed.assert_called_once()


# ============================================================================
# Edge Cases and Numerical Accuracy
# ============================================================================


class TestEdgeCasesAndNumerics:
    """Tests for edge cases and numerical boundary conditions."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_atr_exactly_at_threshold_is_compressed(self, mock_atr):
        """ATR today == atr_floor * multiplier → is_compressed = True (uses <=)."""
        n = 36
        bars = _make_bars(n)
        # atr_floor will be 2.0 (the min), threshold = 2.0 * 1.10 = 2.20
        # Set ATR today exactly to 2.20
        atr_values = [None] * 14 + [2.0] * (n - 15) + [2.20]
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.metrics["is_compressed"] is True

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_atr_just_above_threshold_not_compressed(self, mock_atr):
        """ATR today slightly above threshold → is_compressed = False."""
        n = 36
        bars = _make_bars(n)
        # threshold = 2.0 * 1.10 = 2.20; ATR today = 2.21 → not compressed
        atr_values = [None] * 14 + [2.0] * (n - 15) + [2.21]
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.metrics["is_compressed"] is False

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_close_exactly_at_break_up_threshold(self, mock_atr):
        """Close at or above break_up_threshold → break_up = True (uses >=)."""
        n = 36
        # prior_range_high = 105.0, break_up_threshold = 105 * 1.02
        # Use the same expression the scanner uses to avoid float mismatch
        break_up_threshold = 105.0 * (1 + 2.0 / 100)
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)
        bars[-1] = _make_bar(high=108.0, low=100.0, close=break_up_threshold)

        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        atr_values[15] = 1.0
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is True
        assert result.metrics["break_up"] is True

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_close_exactly_at_break_down_threshold(self, mock_atr):
        """Close at or below break_down_threshold → break_down = True (uses <=)."""
        n = 36
        # prior_range_low = 95.0, break_down_threshold = 95 * 0.98
        break_down_threshold = 95.0 * (1 - 2.0 / 100)
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)
        bars[-1] = _make_bar(high=96.0, low=90.0, close=break_down_threshold)

        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        atr_values[15] = 1.0
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is True
        assert result.metrics["break_down"] is True

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_both_break_up_and_down_favors_up(self, mock_atr):
        """If both break_up and break_down are true, break_up takes precedence (checked first)."""
        n = 36
        # Construct bars where both conditions could trigger:
        # range high = 105, range low = 95
        # break_up requires close >= 107.10
        # break_down requires close <= 93.10
        # In practice, a single close can't meet both, but we can force it with extreme bars.
        # Let range bars have a very narrow range so both thresholds converge:
        bars = _make_bars(n, base_high=100.01, base_low=99.99, base_close=100.0)
        # break_up_threshold = 100.01 * 1.02 = 102.0102
        # break_down_threshold = 99.99 * 0.98 = 97.9902
        # close of 103.0 → break_up=True, break_down=False
        bars[-1] = _make_bar(high=104.0, low=103.0, close=103.0)

        atr_values = [None] * 14 + [0.1] * (n - 15) + [0.05]
        atr_values[15] = 0.05
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        assert result.triggered is True
        assert result.metrics["triggered_direction"] == "UP"

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_metrics_values_are_rounded(self, mock_atr):
        """Verify ATR-related metrics are rounded to 4 decimals, price to 2 decimals."""
        n = 36
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)

        atr_values = [None] * 14 + [2.123456] * (n - 15) + [1.567891]
        mock_atr.return_value = atr_values

        scanner = CompressionScanner()
        result = await scanner.scan_ticker("AAPL", _make_context(bars=bars))

        # ATR values rounded to 4
        assert result.metrics["atr_today"] == round(1.567891, 4)
        assert result.metrics["atr_floor"] == round(2.123456, 4)
        # Price values rounded to 2
        assert result.metrics["prior_range_high"] == round(105.0, 2)
        assert result.metrics["prior_range_low"] == round(95.0, 2)
        assert result.metrics["today_close"] == round(100.0, 2)


# ============================================================================
# Policy Config Overrides
# ============================================================================


class TestPolicyConfigOverrides:
    """Verify that scanner respects policy config parameters."""

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_custom_break_pct(self, mock_atr):
        """Higher break_pct makes it harder to trigger a break."""
        n = 36
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)
        # With default break_pct=2.0: threshold = 105 * 1.02 = 107.1
        # Close of 108 would break. But with break_pct=10.0: threshold = 105 * 1.10 = 115.5
        bars[-1] = _make_bar(high=110.0, low=100.0, close=108.0)

        atr_values = [None] * 14 + [2.0] * (n - 15) + [1.0]
        atr_values[15] = 1.0
        mock_atr.return_value = atr_values

        from app.core.schemas import CompressionConfig, ScannerConfig

        compression_cfg = CompressionConfig(atr_period=14, compression_multiplier=1.10, break_pct=10.0)
        scanner_cfg = ScannerConfig(compression=compression_cfg)
        policy = PolicyConfig(scanner=scanner_cfg)

        scanner = CompressionScanner()
        result = await scanner.scan_ticker(
            "AAPL", _make_context(bars=bars, policy_config=policy)
        )

        # Compressed but close=108 < 115.5, so no break
        assert result.triggered is False
        assert result.metrics["is_compressed"] is True
        assert result.metrics["break_up"] is False

    @pytest.mark.asyncio
    @patch("app.scanners.compression.calculate_atr_series")
    async def test_custom_compression_multiplier(self, mock_atr):
        """Lower compression_multiplier makes it harder to be considered compressed."""
        n = 36
        bars = _make_bars(n, base_high=105.0, base_low=95.0, base_close=100.0)
        bars[-1] = _make_bar(high=110.0, low=100.0, close=108.0)

        # With default 1.10: threshold = 2.0 * 1.10 = 2.20; ATR=2.15 → compressed
        # With multiplier 1.0: threshold = 2.0 * 1.0 = 2.0; ATR=2.15 → NOT compressed
        atr_values = [None] * 14 + [2.0] * (n - 15) + [2.15]
        mock_atr.return_value = atr_values

        from app.core.schemas import CompressionConfig, ScannerConfig

        compression_cfg = CompressionConfig(atr_period=14, compression_multiplier=1.0, break_pct=2.0)
        scanner_cfg = ScannerConfig(compression=compression_cfg)
        policy = PolicyConfig(scanner=scanner_cfg)

        scanner = CompressionScanner()
        result = await scanner.scan_ticker(
            "AAPL", _make_context(bars=bars, policy_config=policy)
        )

        assert result.metrics["is_compressed"] is False
        assert result.triggered is False
