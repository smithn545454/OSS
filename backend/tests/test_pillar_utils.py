"""Tests for pillars/utils.py.

Covers all utility functions: interpolation, mapping, clamping, scoring.
"""

from __future__ import annotations

import pytest

from app.pillars.utils import (
    blend_values,
    clamp_score,
    distance_from_neutral,
    linear_interpolate,
    linear_map_range,
    map_iv_percentile_score,
    map_iv_rv_ratio_score,
    map_liquidity_trend_score,
    map_momentum_score,
    map_open_interest_score,
    map_rs_score,
    map_spread_score,
    map_theta_adjusted_edge_score,
    map_theta_burden_score,
    map_volume_score,
    safe_divide,
)


# ---------------------------------------------------------------------------
# linear_interpolate
# ---------------------------------------------------------------------------


class TestLinearInterpolate:
    def test_empty_breakpoints(self):
        assert linear_interpolate(5.0, []) == 50.0

    def test_below_min(self):
        bp = [(0.0, 10.0), (10.0, 90.0)]
        assert linear_interpolate(-5.0, bp) == 10.0

    def test_above_max(self):
        bp = [(0.0, 10.0), (10.0, 90.0)]
        assert linear_interpolate(15.0, bp) == 90.0

    def test_exact_breakpoint(self):
        bp = [(0.0, 10.0), (5.0, 50.0), (10.0, 90.0)]
        assert linear_interpolate(5.0, bp) == pytest.approx(50.0)

    def test_midpoint(self):
        bp = [(0.0, 0.0), (10.0, 100.0)]
        assert linear_interpolate(5.0, bp) == pytest.approx(50.0)

    def test_same_x_values(self):
        bp = [(5.0, 50.0), (5.0, 60.0)]  # degenerate
        result = linear_interpolate(5.0, bp)
        assert result == 50.0


# ---------------------------------------------------------------------------
# linear_map_range
# ---------------------------------------------------------------------------


class TestLinearMapRange:
    def test_basic(self):
        assert linear_map_range(50.0, 0, 100, 0, 1.0) == pytest.approx(0.5)

    def test_clamped(self):
        assert linear_map_range(150.0, 0, 100, 0, 1.0, clamp=True) == 1.0

    def test_unclamped(self):
        assert linear_map_range(150.0, 0, 100, 0, 1.0, clamp=False) == 1.5

    def test_degenerate_range(self):
        assert linear_map_range(5.0, 5, 5, 0, 100) == 50.0


# ---------------------------------------------------------------------------
# clamp_score
# ---------------------------------------------------------------------------


class TestClampScore:
    def test_in_range(self):
        assert clamp_score(50.0) == 50.0

    def test_below(self):
        assert clamp_score(-10.0) == 0.0

    def test_above(self):
        assert clamp_score(110.0) == 100.0


# ---------------------------------------------------------------------------
# distance_from_neutral
# ---------------------------------------------------------------------------


class TestDistanceFromNeutral:
    def test_neutral(self):
        assert distance_from_neutral(50.0) == 0.0

    def test_above(self):
        assert distance_from_neutral(90.0) == 40.0

    def test_below(self):
        assert distance_from_neutral(10.0) == 40.0


# ---------------------------------------------------------------------------
# safe_divide
# ---------------------------------------------------------------------------


class TestSafeDivide:
    def test_normal(self):
        assert safe_divide(10.0, 2.0) == 5.0

    def test_zero_denominator(self):
        assert safe_divide(10.0, 0.0) == 0.0

    def test_custom_default(self):
        assert safe_divide(10.0, 0.0, default=-1.0) == -1.0


# ---------------------------------------------------------------------------
# blend_values
# ---------------------------------------------------------------------------


class TestBlendValues:
    def test_both_present(self):
        assert blend_values(80.0, 60.0, 0.5) == pytest.approx(70.0)

    def test_first_none(self):
        assert blend_values(None, 60.0, 0.5) == 60.0

    def test_second_none(self):
        assert blend_values(80.0, None, 0.5) == 80.0

    def test_both_none(self):
        assert blend_values(None, None, 0.5) is None


# ---------------------------------------------------------------------------
# Score Mapping Functions
# ---------------------------------------------------------------------------


class TestMapMomentumScore:
    def test_high_positive_call(self):
        score = map_momentum_score(12.0, is_call=True)
        assert score == 95.0

    def test_very_negative_call(self):
        score = map_momentum_score(-12.0, is_call=True)
        assert score == 5.0

    def test_invert_for_put(self):
        # For PUT, -10% return should give high score (bearish is good)
        score = map_momentum_score(-10.0, is_call=False)
        assert score >= 90


class TestMapRSScore:
    def test_strong_positive_call(self):
        score = map_rs_score(10.0, is_call=True)
        assert score == 95.0

    def test_strong_negative_call(self):
        score = map_rs_score(-10.0, is_call=True)
        assert score == 20.0

    def test_neutral(self):
        score = map_rs_score(0.0, is_call=True)
        assert 45.0 <= score <= 65.0


class TestMapIVRVRatioScore:
    def test_cheap_options(self):
        assert map_iv_rv_ratio_score(0.8) == 95.0

    def test_expensive_options(self):
        assert map_iv_rv_ratio_score(2.0) == 20.0

    def test_fair(self):
        score = map_iv_rv_ratio_score(1.0)
        assert 80.0 <= score <= 90.0


class TestMapIVPercentileScore:
    def test_low_percentile(self):
        assert map_iv_percentile_score(10.0) == 95.0

    def test_high_percentile(self):
        assert map_iv_percentile_score(90.0) < 30.0

    def test_mid_percentile(self):
        score = map_iv_percentile_score(50.0)
        assert 60.0 <= score <= 70.0


class TestMapThetaAdjustedEdgeScore:
    def test_high_edge(self):
        assert map_theta_adjusted_edge_score(3.0) == 95.0

    def test_low_edge(self):
        score = map_theta_adjusted_edge_score(0.0)
        assert score == 20.0

    def test_mid_edge(self):
        score = map_theta_adjusted_edge_score(1.5)
        assert score == pytest.approx(65.0)


class TestMapSpreadScore:
    def test_tight(self):
        assert map_spread_score(1.0) == 95.0

    def test_wide(self):
        score = map_spread_score(12.0)
        assert score < 35

    def test_moderate(self):
        score = map_spread_score(5.0)
        assert 60 <= score <= 80


class TestMapOpenInterestScore:
    def test_high_oi(self):
        assert map_open_interest_score(5000) == 95.0

    def test_low_oi(self):
        score = map_open_interest_score(100)
        assert score < 35.0

    def test_moderate_oi(self):
        score = map_open_interest_score(1000)
        assert 70.0 <= score <= 85.0


class TestMapVolumeScore:
    def test_high_volume(self):
        assert map_volume_score(800) == 90.0

    def test_low_volume(self):
        score = map_volume_score(10)
        assert score < 35.0


class TestMapThetaBurdenScore:
    def test_low_burden(self):
        assert map_theta_burden_score(0.3) == 90.0

    def test_high_burden(self):
        score = map_theta_burden_score(4.0)
        assert score < 30.0


class TestMapLiquidityTrendScore:
    def test_none(self):
        assert map_liquidity_trend_score(None) == 50.0

    def test_growing(self):
        score = map_liquidity_trend_score(15.0)
        assert score >= 80.0

    def test_shrinking(self):
        score = map_liquidity_trend_score(-25.0)
        assert score < 40.0
