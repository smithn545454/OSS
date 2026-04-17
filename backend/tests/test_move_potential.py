"""Unit tests for the Move Potential pillar (Policy v4.0.0)."""

from __future__ import annotations

from typing import Optional

import pytest

from app.core.schemas import (
    NumericSubscoreConfig,
    PillarConfigV2,
    PillarId,
    SubscoreBreakpoint,
)
from app.pillars.models import ScoringContext
from app.pillars.move_potential import (
    _expected_vs_required_ratio,
    _move_trigger_score,
    compute_move_potential_pillar,
)


def _pillar_config() -> PillarConfigV2:
    def _linear(field: str, weight: float, lo: float, hi: float) -> NumericSubscoreConfig:
        return NumericSubscoreConfig(
            subscore_id=field,
            display_name=field,
            feature_field=field,
            weight=weight,
            source_tier="tier2",
            breakpoints=[
                SubscoreBreakpoint(value=lo, score=0),
                SubscoreBreakpoint(value=hi, score=100),
            ],
        )

    return PillarConfigV2(
        pillar_id=PillarId.MOVE_POTENTIAL,
        display_name="Move Potential",
        description="v4 Move Potential pillar",
        numeric_subscores=[
            _linear("move_trigger_score", 0.35, 0.0, 100.0),
            _linear("historical_move_magnitude", 0.20, 0.0, 10.0),
            _linear("iv_rv_ratio", 0.15, 2.0, 0.5),  # inverted: low is better
            _linear("bb_width_percentile", 0.15, 100.0, 0.0),  # inverted
            _linear("expected_vs_required", 0.15, 0.5, 2.0),
        ],
    )


def _ctx(
    *,
    dte: int = 30,
    days_to_earnings: Optional[int] = 10,
    iv_rv_ratio: Optional[float] = 0.85,
    bb_width_percentile: Optional[float] = 15.0,
    expected_move_pct: float = 8.0,
    required_move_pct: float = 4.0,
    historical_move_magnitude: Optional[float] = 5.5,
    historical_move_confidence: Optional[int] = 4,
    close: Optional[float] = 180.0,
    high_52w: Optional[float] = 185.0,
    low_52w: Optional[float] = 100.0,
    option_type: str = "CALL",
) -> ScoringContext:
    return ScoringContext(
        evaluation_id="eval-1",
        underlying_ticker="TEST",
        option_type=option_type,
        dte_bucket="B",
        dte=dte,
        days_to_earnings=days_to_earnings,
        iv_rv_ratio=iv_rv_ratio,
        bb_width_percentile=bb_width_percentile,
        expected_move_pct=expected_move_pct,
        required_move_pct=required_move_pct,
        historical_move_magnitude=historical_move_magnitude,
        historical_move_confidence=historical_move_confidence,
        close=close,
        high_52w=high_52w,
        low_52w=low_52w,
    )


class TestMoveTriggerScore:
    def test_earnings_in_sweet_window_scores_high(self) -> None:
        ctx = _ctx(dte=30, days_to_earnings=10)
        assert _move_trigger_score(ctx) == 90.0

    def test_earnings_just_before_expiry_penalized(self) -> None:
        ctx = _ctx(dte=30, days_to_earnings=2)
        # 2 <= 4 → 70.0
        assert _move_trigger_score(ctx) == 70.0

    def test_earnings_after_expiry_small_lift(self) -> None:
        ctx = _ctx(dte=14, days_to_earnings=18)
        # 18 - 14 = 4 ≤ 7 window → 45
        assert _move_trigger_score(ctx) == 45.0

    def test_no_earnings_with_breakout_and_tight_range_scores_medium(self) -> None:
        ctx = _ctx(
            days_to_earnings=None,
            close=183.0,
            high_52w=185.0,
            bb_width_percentile=20.0,
        )
        assert _move_trigger_score(ctx) == 70.0

    def test_no_earnings_no_breakout_returns_none(self) -> None:
        ctx = _ctx(
            days_to_earnings=None,
            close=140.0,
            high_52w=185.0,
            bb_width_percentile=70.0,
        )
        assert _move_trigger_score(ctx) is None


class TestExpectedVsRequiredRatio:
    def test_compute_ratio(self) -> None:
        ctx = _ctx(expected_move_pct=10.0, required_move_pct=4.0)
        assert _expected_vs_required_ratio(ctx) == 2.5

    def test_returns_none_when_inputs_missing(self) -> None:
        assert _expected_vs_required_ratio(_ctx(expected_move_pct=0.0)) is None
        assert _expected_vs_required_ratio(_ctx(required_move_pct=0.0)) is None


class TestComputeMovePotential:
    def test_strong_setup_scores_high(self) -> None:
        ctx = _ctx()
        result = compute_move_potential_pillar(ctx, _pillar_config())
        assert result.pillar_id == PillarId.MOVE_POTENTIAL
        assert result.score >= 70
        assert "CATALYST_IN_WINDOW" in result.tags
        assert "VOLATILITY_COMPRESSION" in result.tags
        assert "CHEAP_IV_EXPANSION" in result.tags

    def test_insufficient_data_returns_zero(self) -> None:
        # Only expected_vs_required computable → need 3+.
        ctx = ScoringContext(
            evaluation_id="eval-1",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            dte=30,
            days_to_earnings=None,
            iv_rv_ratio=None,
            bb_width_percentile=None,
            historical_move_magnitude=None,
            expected_move_pct=5.0,
            required_move_pct=4.0,
            close=140.0,  # Not near high_52w
            high_52w=185.0,
        )
        result = compute_move_potential_pillar(ctx, _pillar_config())
        assert result.score == 0.0
        assert "INSUFFICIENT_DATA" in result.tags

    def test_low_historical_confidence_tag(self) -> None:
        ctx = _ctx(historical_move_confidence=1)
        result = compute_move_potential_pillar(ctx, _pillar_config())
        assert "LOW_HISTORICAL_CONFIDENCE" in result.tags

    def test_raises_on_wrong_pillar_id(self) -> None:
        from app.core.schemas import NumericSubscoreConfig as N

        wrong = PillarConfigV2(
            pillar_id=PillarId.TRADE_STRUCTURE,
            display_name="Wrong",
            description="wrong",
            numeric_subscores=[
                N(
                    subscore_id="x",
                    display_name="x",
                    feature_field="close",
                    weight=1.0,
                    source_tier="tier2",
                    breakpoints=[
                        SubscoreBreakpoint(value=0.0, score=50),
                        SubscoreBreakpoint(value=1.0, score=50),
                    ],
                )
            ],
        )
        with pytest.raises(ValueError, match="MOVE_POTENTIAL"):
            compute_move_potential_pillar(_ctx(), wrong)

    def test_expensive_iv_tag(self) -> None:
        ctx = _ctx(iv_rv_ratio=1.5)
        result = compute_move_potential_pillar(ctx, _pillar_config())
        assert "EXPENSIVE_IV" in result.tags
