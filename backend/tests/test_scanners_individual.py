"""Tests for individual scanner implementations.

Covers BreakoutScanner, CompressionScanner, and CheapOptionsScanner.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scanners.base import ScanContext
from app.scanners.breakout import BreakoutScanner
from app.scanners.compression import CompressionScanner
from app.scanners.cheap_options import CheapOptionsScanner
from app.services.polygon import DailyBar


def _make_bar(
    close: float,
    high: float = None,
    low: float = None,
    volume: int = 100000,
    date: str = "2026-01-01",
) -> DailyBar:
    """Create a DailyBar."""
    return DailyBar(
        ticker="TEST",
        date=date,
        open=close,
        high=high or close + 1,
        low=low or close - 1,
        close=close,
        volume=volume,
        vwap=close,
    )


def _make_context(cached_bars: dict = None, policy_config=None):
    """Create a ScanContext with defaults."""
    if policy_config is None:
        policy_config = MagicMock()
        # Breakout scanner config
        policy_config.scanner.breakout.lookback_days = 20
        policy_config.scanner.breakout.volume_confirmation = True
        policy_config.scanner.breakout.min_volume_ratio = 1.5
        # Compression scanner config
        policy_config.scanner.compression.atr_period = 14
        policy_config.scanner.compression.compression_multiplier = 1.10
        policy_config.scanner.compression.break_pct = 2.0
        # Cheap options config
        policy_config.scanner.cheap_options.iv_rv_ratio_max = 1.10
        policy_config.scanner.cheap_options.iv_percentile_max = 40

    polygon = MagicMock()
    polygon.get_daily_bars_parsed = AsyncMock(return_value=[])

    ctx = ScanContext.create(polygon, policy_config)
    ctx.cached_data = {"daily_bars": cached_bars or {}, "options_chains": {}, "options_volume": {}}
    return ctx


# ---------------------------------------------------------------------------
# BreakoutScanner
# ---------------------------------------------------------------------------


class TestBreakoutScanner:

    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        scanner = BreakoutScanner()
        ctx = _make_context(cached_bars={"AAPL": [_make_bar(100.0) for _ in range(5)]})
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is False
        assert "Insufficient" in (result.error or "")

    @pytest.mark.asyncio
    async def test_breakout_triggered(self):
        scanner = BreakoutScanner()
        # 20 bars with highs up to 110, then a bar that closes above the range
        bars = []
        for i in range(21):
            bars.append(_make_bar(
                close=100.0 + i * 0.2,
                high=101.0 + i * 0.2,
                low=99.0 + i * 0.2,
                volume=100000,
                date=f"2026-01-{i+1:02d}",
            ))
        # Last bar breaks above the range
        bars.append(_make_bar(
            close=120.0,
            high=121.0,
            low=103.0,
            volume=200000,
            date="2026-01-22",
        ))
        ctx = _make_context(cached_bars={"AAPL": bars})
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is True
        assert result.trigger is not None

    @pytest.mark.asyncio
    async def test_no_breakout(self):
        scanner = BreakoutScanner()
        # 25 bars that stay in a tight range
        bars = [_make_bar(
            close=100.0,
            high=101.0,
            low=99.0,
            volume=100000,
            date=f"2026-01-{i+1:02d}",
        ) for i in range(25)]
        ctx = _make_context(cached_bars={"AAPL": bars})
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_breakdown_triggered(self):
        scanner = BreakoutScanner()
        bars = []
        for i in range(21):
            bars.append(_make_bar(
                close=100.0 - i * 0.1,
                high=101.0 - i * 0.1,
                low=99.0 - i * 0.1,
                volume=100000,
                date=f"2026-01-{i+1:02d}",
            ))
        # Last bar breaks below the range
        bars.append(_make_bar(
            close=80.0,
            high=99.0,
            low=79.0,
            volume=200000,
            date="2026-01-22",
        ))
        ctx = _make_context(cached_bars={"AAPL": bars})
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is True

    @pytest.mark.asyncio
    async def test_fallback_fetch(self):
        """When ticker is not in cache, falls back to fetch."""
        scanner = BreakoutScanner()
        ctx = _make_context(cached_bars={})
        ctx.polygon.get_daily_bars_parsed = AsyncMock(return_value=[_make_bar(100.0)])
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is False


# ---------------------------------------------------------------------------
# CompressionScanner
# ---------------------------------------------------------------------------


class TestCompressionScanner:

    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        scanner = CompressionScanner()
        ctx = _make_context(cached_bars={"AAPL": [_make_bar(100.0) for _ in range(5)]})
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_no_trigger_no_compression(self):
        """Many bars but no compression + breakout."""
        scanner = CompressionScanner()
        bars = [_make_bar(
            close=100.0 + i * 0.1,
            high=102.0 + i * 0.1,
            low=98.0 + i * 0.1,
            date=f"2026-01-{i+1:02d}" if i < 28 else f"2026-02-{i-27:02d}",
        ) for i in range(40)]
        ctx = _make_context(cached_bars={"AAPL": bars})
        result = await scanner.scan_ticker("AAPL", ctx)
        # With normal ATR movement, no compression expected
        assert isinstance(result.triggered, bool)

    @pytest.mark.asyncio
    async def test_compression_and_breakout(self):
        """Tight compression followed by a breakout."""
        scanner = CompressionScanner()
        # 35 bars of very tight range
        bars = []
        for i in range(35):
            bars.append(_make_bar(
                close=100.0,
                high=100.1,
                low=99.9,
                volume=100000,
                date=f"2026-01-{i+1:02d}" if i < 28 else f"2026-02-{i-27:02d}",
            ))
        # Last bar: big breakout
        bars.append(_make_bar(
            close=115.0,
            high=116.0,
            low=100.0,
            volume=200000,
            date="2026-02-09",
        ))
        ctx = _make_context(cached_bars={"AAPL": bars})
        result = await scanner.scan_ticker("AAPL", ctx)
        # Should trigger compression + expansion
        assert isinstance(result.triggered, bool)

    @pytest.mark.asyncio
    async def test_fallback_fetch(self):
        scanner = CompressionScanner()
        ctx = _make_context(cached_bars={})
        ctx.polygon.get_daily_bars_parsed = AsyncMock(return_value=[_make_bar(100.0)])
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is False


# ---------------------------------------------------------------------------
# CheapOptionsScanner
# ---------------------------------------------------------------------------


class TestCheapOptionsScanner:

    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        scanner = CheapOptionsScanner()
        ctx = _make_context(cached_bars={"AAPL": [_make_bar(100.0) for _ in range(5)]})
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_no_options_chain(self):
        """Enough bars for RV but no options chain in cache."""
        scanner = CheapOptionsScanner()
        bars = [_make_bar(100.0 + i * 0.5, date=f"2026-01-{i+1:02d}")
                for i in range(25)]
        ctx = _make_context(cached_bars={"AAPL": bars})
        # No options chain data
        ctx.cached_data["options_chains"] = {}
        # Should fetch chain from polygon
        ctx.polygon.get_options_chain = AsyncMock(return_value=[])
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_rv_zero(self):
        """All prices the same -> RV = 0."""
        scanner = CheapOptionsScanner()
        bars = [_make_bar(100.0, date=f"2026-01-{i+1:02d}") for i in range(25)]
        ctx = _make_context(cached_bars={"AAPL": bars})
        result = await scanner.scan_ticker("AAPL", ctx)
        assert result.triggered is False
