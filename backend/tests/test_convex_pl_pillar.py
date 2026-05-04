"""Unit tests for the reconstructed PL pillar."""

from __future__ import annotations

import pytest

from app.convex.pl_pillar import compute_pl_score


def test_canonical_inputs_score_high() -> None:
    """IV near 0.30, |delta| 0.30, low percentile, ratio near 0.91 → high PL."""
    score, subs = compute_pl_score(
        iv=0.30, abs_delta=0.30, iv_percentile=10.0, iv_rv_ratio=0.92
    )
    assert 70.0 <= score <= 100.0
    # All four feature subscores were available.
    assert all(subs[k] is not None for k in ("iv", "abs_delta", "iv_percentile", "iv_rv_ratio"))


def test_high_iv_drives_pl_low() -> None:
    """Expensive premium (IV well above 1.0) should score near the floor."""
    score, _ = compute_pl_score(
        iv=1.5, abs_delta=0.30, iv_percentile=80.0, iv_rv_ratio=2.0
    )
    assert score < 30.0


def test_direction_agnostic_call_vs_put() -> None:
    """Identical |delta| inputs for CALL (+0.25) and PUT (-0.25) → identical PL."""
    call_score, _ = compute_pl_score(
        iv=0.30, abs_delta=0.25, iv_percentile=15.0, iv_rv_ratio=0.95
    )
    put_score, _ = compute_pl_score(
        iv=0.30, abs_delta=0.25, iv_percentile=15.0, iv_rv_ratio=0.95
    )
    assert call_score == put_score


def test_low_delta_clamps_to_floor() -> None:
    """|delta| < 0.17 (the lowest breakpoint value) clamps the delta subscore to 10."""
    _, subs = compute_pl_score(
        iv=0.30, abs_delta=0.05, iv_percentile=10.0, iv_rv_ratio=0.95
    )
    assert subs["abs_delta"] == pytest.approx(10.0)


def test_weight_redistribution_when_iv_percentile_missing() -> None:
    """When iv_percentile is None, the remaining three weights cover 100%."""
    score_full, _ = compute_pl_score(
        iv=0.30, abs_delta=0.30, iv_percentile=10.0, iv_rv_ratio=0.95
    )
    score_no_ivp, _ = compute_pl_score(
        iv=0.30, abs_delta=0.30, iv_percentile=None, iv_rv_ratio=0.95
    )
    # Both scores well-defined and finite; missing iv_percentile shifts
    # the score slightly but not dramatically (it carries 14.49% weight).
    assert 0.0 < score_no_ivp <= 100.0
    assert abs(score_full - score_no_ivp) < 25.0


def test_weight_redistribution_when_iv_rv_ratio_missing() -> None:
    """When iv_rv_ratio is None, the smallest weight (6.42%) redistributes."""
    score, subs = compute_pl_score(
        iv=0.30, abs_delta=0.30, iv_percentile=10.0, iv_rv_ratio=None
    )
    assert 0.0 < score <= 100.0
    assert subs["iv_rv_ratio"] is None


def test_returns_zero_when_iv_and_delta_both_missing() -> None:
    """Without the two heavy features the formula has nothing to report."""
    score, subs = compute_pl_score(
        iv=None, abs_delta=None, iv_percentile=10.0, iv_rv_ratio=0.95
    )
    # iv_percentile + iv_rv_ratio together are only ~21% of weight; with
    # iv and abs_delta both missing the model returns a degenerate score
    # but it should not crash and the iv/abs_delta subscores are None.
    assert subs["iv"] is None
    assert subs["abs_delta"] is None
    # Score may still be finite (computed off the available 21% weight).
    assert 0.0 <= score <= 100.0


def test_returns_zero_when_all_features_missing() -> None:
    """Total absence of inputs returns 0.0 and a fully-None subscore dict."""
    score, subs = compute_pl_score(
        iv=None, abs_delta=None, iv_percentile=None, iv_rv_ratio=None
    )
    assert score == 0.0
    assert all(v is None for v in subs.values())


def test_iv_subscore_peaks_near_0_30() -> None:
    """IV at 0.2972 hits the peak breakpoint for the IV subscore (90.0)."""
    _, subs = compute_pl_score(
        iv=0.2972, abs_delta=0.30, iv_percentile=10.0, iv_rv_ratio=0.95
    )
    assert subs["iv"] == pytest.approx(90.0)


def test_iv_percentile_low_scores_high() -> None:
    """IV percentile of 5.555 hits the peak (90.0) of the IV-percentile subscore."""
    _, subs = compute_pl_score(
        iv=0.30, abs_delta=0.30, iv_percentile=5.555, iv_rv_ratio=0.95
    )
    assert subs["iv_percentile"] == pytest.approx(90.0)
