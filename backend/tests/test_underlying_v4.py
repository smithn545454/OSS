"""Tests for Pillar v4 additions to UnderlyingFeatures.

Covers ma_150, ma_200, 52-week high/low, and Bollinger Band width
percentile — the features that require 252+ daily bars.
"""

from __future__ import annotations

import math

import pytest

from app.features.underlying import (
    _bb_width_at,
    _compute_bb_width,
    compute_underlying_features,
)
from app.services.polygon import DailyBar


def _bars(closes: list[float], start_date: str = "2025-01-01") -> list[DailyBar]:
    """Build a sequence of DailyBar objects from a list of closes."""
    from datetime import date, timedelta

    d0 = date.fromisoformat(start_date)
    bars = []
    for i, close in enumerate(closes):
        bars.append(
            DailyBar(
                ticker="TEST",
                date=(d0 + timedelta(days=i)).isoformat(),
                open=close,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=1_000_000,
            )
        )
    return bars


# ---------------------------------------------------------------------------
# BB width
# ---------------------------------------------------------------------------


def test_bb_width_at_constant_prices_is_zero():
    closes = [100.0] * 25
    width = _bb_width_at(closes, 24)
    assert width is not None
    assert width == pytest.approx(0.0)


def test_bb_width_at_returns_none_for_short_window():
    closes = [100.0] * 10
    assert _bb_width_at(closes, 9) is None


def test_bb_width_at_high_volatility_greater_than_low():
    # Two 20-bar windows: first has tiny fluctuations, second has big ones.
    low_vol = [100.0 + (i % 2) * 0.5 for i in range(20)]
    high_vol = [100.0 + (i % 2) * 10.0 for i in range(20)]
    lo = _bb_width_at(low_vol, 19)
    hi = _bb_width_at(high_vol, 19)
    assert lo is not None and hi is not None
    assert hi > lo


def test_compute_bb_width_returns_none_percentile_without_history():
    # 20 bars: enough for a current width, not enough for a ranking.
    closes = [100.0 + i * 0.5 for i in range(20)]
    width, percentile = _compute_bb_width(closes)
    assert width is not None
    assert percentile is None  # <60 historical values


def test_compute_bb_width_ranks_against_history():
    # 280 bars: stable chop for first 220, then a volatility expansion.
    closes = [100.0 + (i % 5) * 0.2 for i in range(220)]
    # Final 60 bars have larger moves; current width should land near top.
    closes += [100.0 + (i % 2) * 8.0 for i in range(60)]
    width, percentile = _compute_bb_width(closes)
    assert width is not None
    assert percentile is not None
    assert 80 <= percentile <= 100


# ---------------------------------------------------------------------------
# compute_underlying_features — v4 fields
# ---------------------------------------------------------------------------


def test_v4_fields_none_when_history_short():
    """<150 bars: ma_150, ma_200, 52w fields, bb_width_percentile all None."""
    bars = _bars([100.0 + i for i in range(60)])
    features = compute_underlying_features(bars)
    assert features is not None
    assert features.ma_150 is None
    assert features.ma_200 is None
    assert features.high_52w is None
    assert features.low_52w is None
    assert features.dist_to_52w_high_pct is None
    assert features.dist_to_52w_low_pct is None
    # BB width is available (>= 20 bars), but percentile isn't.
    assert features.bb_width is not None
    assert features.bb_width_percentile is None


def test_v4_fields_populated_with_full_history():
    """With 260 bars, all v4 fields populate and match expected values."""
    # Linear ramp 100 → 360 (deterministic closes).
    closes = [100.0 + i for i in range(260)]
    bars = _bars(closes)
    features = compute_underlying_features(bars)
    assert features is not None

    # MA 150 = mean of last 150 closes = mean of 210 … 359 = 284.5
    assert features.ma_150 == pytest.approx(sum(closes[-150:]) / 150)
    # MA 200 = mean of last 200 closes
    assert features.ma_200 == pytest.approx(sum(closes[-200:]) / 200)

    # 52-week high/low over last 252 closes.
    recent = closes[-252:]
    assert features.high_52w == max(recent)
    assert features.low_52w == min(recent)

    # Current close = closes[-1]; dist to high/low as percentages.
    current = closes[-1]
    assert features.dist_to_52w_high_pct == pytest.approx(
        (current - max(recent)) / max(recent) * 100
    )
    assert features.dist_to_52w_low_pct == pytest.approx(
        (current - min(recent)) / min(recent) * 100
    )

    # BB width percentile exists with enough history.
    assert features.bb_width_percentile is not None
    assert 0 <= features.bb_width_percentile <= 100


def test_dist_to_52w_high_is_zero_at_new_high():
    """Ticker making a fresh 252-day high has dist_to_52w_high_pct == 0."""
    closes = [100.0] * 251 + [150.0]
    features = compute_underlying_features(_bars(closes))
    assert features is not None
    assert features.high_52w == 150.0
    assert features.dist_to_52w_high_pct == pytest.approx(0.0)


def test_ma_stage2_alignment_with_uptrend():
    """Monotonic uptrend: close > ma_50 > ma_150 > ma_200, matches Stage 2."""
    closes = [100.0 + i * 0.5 for i in range(260)]
    features = compute_underlying_features(_bars(closes))
    assert features is not None
    # Sanity — in a linear uptrend, close is above all MAs and shorter MAs
    # are above longer MAs.
    assert features.ma_150 is not None and features.ma_200 is not None
    assert features.close > features.ma_150 > features.ma_200
    assert features.sma50 is not None
    assert features.sma50 > features.ma_150


def test_ma_stage2_breakdown():
    """Monotonic downtrend: close < ma_150 < ma_200 (Stage 4)."""
    closes = [360.0 - i * 0.5 for i in range(260)]
    features = compute_underlying_features(_bars(closes))
    assert features is not None
    assert features.ma_150 is not None and features.ma_200 is not None
    assert features.close < features.ma_150 < features.ma_200


def test_bb_width_constant_close_produces_nan_free_zero():
    """Constant closes produce bb_width == 0, not NaN."""
    closes = [100.0] * 30
    features = compute_underlying_features(_bars(closes))
    assert features is not None
    assert features.bb_width is not None
    assert features.bb_width == pytest.approx(0.0)
    assert not math.isnan(features.bb_width)
