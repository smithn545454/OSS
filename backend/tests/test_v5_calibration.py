"""Tests for v5 calibration helpers (Wilson CI, rate estimation, regime).

Phase 1 of the v5 dual-conviction rebuild. The helpers are dark in
production until Phase 3 — these tests just confirm the math.
"""

from __future__ import annotations

import math

import pytest

from app.calibration.archetype_rates import (
    HR200_THRESHOLD,
    ArchetypeRates,
    estimate_archetype_rates,
    filter_positions_for_archetype,
    normalize_pnl_pct,
)
from app.calibration.regime import RegimeState, compute_regime_alignment
from app.calibration.wilson import wilson_ci


# ============================================================================
# Wilson CI
# ============================================================================


class TestWilsonCI:
    def test_zero_n_returns_zeros(self) -> None:
        assert wilson_ci(0, 0) == (0.0, 0.0, 0.0)

    def test_single_success(self) -> None:
        # 1/1 → point=1, but Wilson lower < 1 (small-sample penalty)
        point, lo, hi = wilson_ci(1, 1)
        assert point == 1.0
        assert lo < 1.0
        assert hi == 1.0

    def test_known_value_20_of_100(self) -> None:
        # Reference: Wilson 95% CI for 20/100 ≈ (0.131, 0.292)
        point, lo, hi = wilson_ci(20, 100)
        assert point == 0.20
        assert abs(lo - 0.131) < 0.005
        assert abs(hi - 0.292) < 0.005

    def test_lower_bound_non_negative(self) -> None:
        # 0/100 → point=0, lower=0 (clamped)
        point, lo, hi = wilson_ci(0, 100)
        assert point == 0.0
        assert lo == 0.0
        assert hi > 0.0  # upper still positive (uncertainty)

    def test_upper_bound_at_most_one(self) -> None:
        # 100/100 → point=1, upper≈1 (small-sample penalty + float clamp), lower < 1
        point, lo, hi = wilson_ci(100, 100)
        assert point == 1.0
        assert math.isclose(hi, 1.0, abs_tol=1e-9)
        assert lo < 1.0

    def test_small_n_wider_ci(self) -> None:
        # Wider interval for smaller sample at the same point estimate
        _, lo_small, hi_small = wilson_ci(2, 10)
        _, lo_large, hi_large = wilson_ci(20, 100)
        assert (hi_small - lo_small) > (hi_large - lo_large)

    def test_invalid_inputs(self) -> None:
        with pytest.raises(ValueError):
            wilson_ci(-1, 10)
        with pytest.raises(ValueError):
            wilson_ci(5, -1)
        with pytest.raises(ValueError):
            wilson_ci(10, 5)  # successes > n

    def test_confidence_levels(self) -> None:
        # Higher confidence → wider interval
        _, lo90, hi90 = wilson_ci(20, 100, confidence=0.90)
        _, lo99, hi99 = wilson_ci(20, 100, confidence=0.99)
        assert (hi99 - lo99) > (hi90 - lo90)

    def test_uv_lottery_call_reproduces_validation(self) -> None:
        # From /tmp/v5_findings_report.md: UV_LOTTERY_CALL n=136, HR200=27
        # Expected: point=19.85%, Wilson lower≈14%, Wilson upper≈27.3%
        point, lo, hi = wilson_ci(27, 136)
        assert abs(point * 100 - 19.85) < 0.05
        assert abs(lo * 100 - 14.02) < 0.5  # Some rounding tolerance
        assert abs(hi * 100 - 27.34) < 0.5


# ============================================================================
# Archetype rate estimation
# ============================================================================


def _pos(arch: str, mfe: float, pnl: float) -> dict:
    """Helper to build a minimal position dict."""
    return {
        "archetype_matched": arch,
        "max_favorable_excursion": mfe,
        "current_pnl_pct": pnl,
    }


class TestArchetypeRateEstimation:
    def test_filter_archetype_basic(self) -> None:
        positions = [
            _pos("UV_LOTTERY_CALL", 250, 100),
            _pos("UV_LOTTERY_CALL", 50, -20),
            _pos("UV_STRUCTURAL", 300, 200),
            _pos(None, 100, 50),  # Unmatched
        ]
        filtered = filter_positions_for_archetype(positions, "UV_LOTTERY_CALL")
        assert len(filtered) == 2

    def test_filter_custom_field(self) -> None:
        positions = [
            {"hr_archetype_matched": "X", "max_favorable_excursion": 100, "current_pnl_pct": 0},
            {"hr_archetype_matched": "Y", "max_favorable_excursion": 100, "current_pnl_pct": 0},
        ]
        filtered = filter_positions_for_archetype(
            positions, "X", archetype_field="hr_archetype_matched",
        )
        assert len(filtered) == 1

    def test_estimate_empty_cohort(self) -> None:
        rates = estimate_archetype_rates([], archetype_id="EMPTY")
        assert rates.hr200.n_effective == 0
        assert rates.hr200.point == 0.0
        assert rates.win_rate.point == 0.0
        assert rates.mean_pnl_pct == 0.0

    def test_estimate_simple_cohort(self) -> None:
        # 4 positions: 1 HR200, 2 wins, mean P&L = 25%
        positions = [
            _pos("X", 250, 100),  # HR200 + win
            _pos("X", 50, 50),    # win
            _pos("X", 30, -20),   # loss
            _pos("X", 80, -30),   # loss
        ]
        rates = estimate_archetype_rates(positions, archetype_id="X")
        assert rates.hr200.n_effective == 4
        assert rates.hr200.point == 0.25  # 1 / 4
        assert rates.win_rate.point == 0.50  # 2 / 4
        assert rates.mean_pnl_pct == 25.0

    def test_hr200_threshold_at_exactly_200(self) -> None:
        # Boundary: MFE == 200 should count as HR200
        positions = [_pos("X", HR200_THRESHOLD, 100)]
        rates = estimate_archetype_rates(positions, archetype_id="X")
        assert rates.hr200.point == 1.0

    def test_rolling_window_keeps_most_recent(self) -> None:
        # 10 positions, only last 5 should count under window=5
        positions = [_pos("X", 250 if i >= 5 else 0, 100 if i >= 5 else -50) for i in range(10)]
        rates = estimate_archetype_rates(positions, archetype_id="X", rolling_window_n=5)
        assert rates.hr200.n_effective == 5
        assert rates.hr200.point == 1.0  # All 5 most recent are HR200

    def test_ewma_weights_recent_higher(self) -> None:
        # 100 trades alternating HR/no-HR; recent should weight more
        positions = [_pos("X", 250 if i % 2 == 0 else 0, 100 if i % 2 == 0 else -50) for i in range(100)]
        # Without EWMA: 50% HR rate
        rates_uniform = estimate_archetype_rates(positions, archetype_id="X")
        # With aggressive EWMA: should still be ~50% (alternating preserves it)
        rates_ewma = estimate_archetype_rates(positions, archetype_id="X", ewma_half_life_n=10)
        assert abs(rates_uniform.hr200.point - 0.5) < 0.01
        # EWMA preserves point estimate when pattern is balanced
        assert abs(rates_ewma.hr200.point - 0.5) < 0.1
        # n_effective is reduced under EWMA (effective sample size)
        assert rates_ewma.hr200.n_effective < 100


class TestPnlNormalization:
    def test_floor(self) -> None:
        assert normalize_pnl_pct(-50) == 0.0
        assert normalize_pnl_pct(-100) == 0.0  # Clamped

    def test_midpoint(self) -> None:
        assert normalize_pnl_pct(0) == 1.0

    def test_ceiling(self) -> None:
        assert normalize_pnl_pct(50) == 2.0
        assert normalize_pnl_pct(200) == 2.0  # Clamped

    def test_intermediate(self) -> None:
        assert normalize_pnl_pct(25) == 1.5
        assert normalize_pnl_pct(-25) == 0.5

    def test_misconfigured_returns_neutral(self) -> None:
        assert normalize_pnl_pct(50, floor_pct=10, ceiling_pct=10) == 1.0


# ============================================================================
# Regime alignment
# ============================================================================


class TestRegimeAlignment:
    def test_missing_data_returns_neutral(self) -> None:
        assert compute_regime_alignment(RegimeState(), option_type="CALL") == 1.0

    def test_bullish_calm_call(self) -> None:
        regime = RegimeState(spy_return_20d_pct=6.0, vix_level=15.0)
        assert compute_regime_alignment(regime, option_type="CALL") == 1.3

    def test_bullish_calm_put(self) -> None:
        regime = RegimeState(spy_return_20d_pct=6.0, vix_level=15.0)
        assert compute_regime_alignment(regime, option_type="PUT") == 0.7

    def test_bearish_fear_put(self) -> None:
        regime = RegimeState(spy_return_20d_pct=-7.0, vix_level=30.0)
        assert compute_regime_alignment(regime, option_type="PUT") == 1.3

    def test_bearish_fear_call(self) -> None:
        regime = RegimeState(spy_return_20d_pct=-7.0, vix_level=30.0)
        assert compute_regime_alignment(regime, option_type="CALL") == 0.7

    def test_chop(self) -> None:
        regime = RegimeState(spy_return_20d_pct=0.5, vix_level=22.0)
        assert compute_regime_alignment(regime, option_type="CALL") == 0.85
        assert compute_regime_alignment(regime, option_type="PUT") == 0.85

    def test_neutral_regime(self) -> None:
        # Mid-VIX, modest decline — neither bullish-calm nor bearish-fear
        regime = RegimeState(spy_return_20d_pct=-3.0, vix_level=19.0)
        assert compute_regime_alignment(regime, option_type="CALL") == 1.0
        assert compute_regime_alignment(regime, option_type="PUT") == 1.0

    def test_clamping(self) -> None:
        regime = RegimeState(spy_return_20d_pct=6.0, vix_level=15.0)
        # Tighter clamps should bring 1.3 down to 1.1
        assert compute_regime_alignment(
            regime, option_type="CALL", multiplier_max=1.1,
        ) == 1.1


# ============================================================================
# Schema migration: v3/v4 records still load with new optional fields
# ============================================================================


class TestSchemaMigration:
    """v5 added many Optional fields — historical records must still load."""

    def test_decision_loads_without_v5_fields(self) -> None:
        from app.core.schemas import Decision, Verdict

        # Mimic a v3 Decision record (no v5 fields, no v4.1.0 archetype fields)
        record = {
            "evaluation_id": "eval-test",
            "verdict": Verdict.APPROVE.value,
            "final_score": 75.0,
            "primary_reason_code": "GATE_PASSED",
            "supporting_reason_codes": [],
            "failed_gates": [],
            "concentration_warnings": [],
            "policy_version": "v3.0.0",
            "premium_leverage_score": 70.0,
            "underlying_behavior_score": 80.0,
            "setup_quality_score": 75.0,
        }
        d = Decision.model_validate(record)
        assert d.evaluation_id == "eval-test"
        # All v5 fields should be None
        assert d.hr_conviction is None
        assert d.p_conviction is None
        assert d.gbm_hr_score is None
        assert d.v5_scoring_version is None

    def test_paper_position_loads_without_v5_fields(self) -> None:
        from app.core.schemas import PaperPosition, Verdict

        record = {
            "evaluation_id": "eval-test",
            "option_ticker": "AAPL250117C00150000",
            "entry_price": 5.0,
            "entry_date": "2026-01-15",
            "verdict_at_entry": Verdict.APPROVE.value,
            "current_price": 7.5,
            "current_pnl_pct": 50.0,
        }
        p = PaperPosition.model_validate(record)
        assert p.evaluation_id == "eval-test"
        # All v5 fields should be None
        assert p.hr_conviction is None
        assert p.p_conviction is None
        assert p.regime_alignment is None
