"""Unit tests for the Trade Structure pillar (Policy v4.0.0)."""

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
from app.pillars.trade_structure import (
    _dte_sweet_spot,
    _gamma_theta_ratio,
    _strike_pivot_distance,
    compute_trade_structure_pillar,
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
        pillar_id=PillarId.TRADE_STRUCTURE,
        display_name="Trade Structure",
        description="v4 Trade Structure pillar",
        numeric_subscores=[
            # abs_delta: peak around 0.30 but for test we use monotone segment.
            _linear("abs_delta", 0.25, 0.05, 0.50),
            _linear("gamma_theta_ratio", 0.25, 0.0, 50.0),
            _linear("dte_sweet_spot_score", 0.20, 0.0, 100.0),
            _linear("iv_percentile", 0.20, 100.0, 0.0),  # inverted
            # strike_vs_pivot_pct: lower is better → inverted
            _linear("strike_vs_pivot_pct", 0.10, 20.0, 0.0),
        ],
    )


def _ctx(
    *,
    option_type: str = "CALL",
    delta: float = 0.30,
    gamma: Optional[float] = 0.04,
    theta: Optional[float] = -0.08,
    vega: Optional[float] = 0.10,
    iv: float = 0.35,
    iv_percentile: Optional[float] = 20.0,
    close: Optional[float] = 180.0,
    strike: Optional[float] = 185.0,
    high_52w: Optional[float] = 190.0,
    low_52w: Optional[float] = 120.0,
    dte: int = 30,
    days_to_earnings: Optional[int] = None,
) -> ScoringContext:
    return ScoringContext(
        evaluation_id="eval-1",
        underlying_ticker="TEST",
        option_type=option_type,
        dte_bucket="B",
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        iv=iv,
        iv_percentile=iv_percentile,
        close=close,
        strike=strike,
        high_52w=high_52w,
        low_52w=low_52w,
        dte=dte,
        days_to_earnings=days_to_earnings,
    )


class TestGammaThetaRatio:
    def test_positive_ratio_for_healthy_call(self) -> None:
        ctx = _ctx(gamma=0.04, theta=-0.08, iv=0.35, close=180.0)
        ratio = _gamma_theta_ratio(ctx)
        assert ratio is not None
        assert ratio > 0

    def test_zero_theta_returns_none(self) -> None:
        ctx = _ctx(theta=0.0)
        assert _gamma_theta_ratio(ctx) is None

    def test_missing_gamma_returns_none(self) -> None:
        assert _gamma_theta_ratio(_ctx(gamma=None)) is None


class TestDteSweetSpot:
    def test_standard_sweet_spot_high(self) -> None:
        ctx = _ctx(dte=30, days_to_earnings=None)
        assert _dte_sweet_spot(ctx) == 90.0

    def test_too_short_dte_penalized(self) -> None:
        ctx = _ctx(dte=5, days_to_earnings=None)
        assert _dte_sweet_spot(ctx) == 20.0

    def test_catalyst_with_good_buffer(self) -> None:
        # DTE 30, earnings in 25 → 5-day buffer → top tier.
        ctx = _ctx(dte=30, days_to_earnings=25)
        assert _dte_sweet_spot(ctx) == 92.0

    def test_catalyst_right_at_expiry_penalized(self) -> None:
        ctx = _ctx(dte=30, days_to_earnings=29)
        assert _dte_sweet_spot(ctx) == 55.0

    def test_missing_dte_returns_none(self) -> None:
        assert _dte_sweet_spot(_ctx(dte=0)) is None


class TestStrikePivotDistance:
    def test_at_pivot_zero_distance(self) -> None:
        ctx = _ctx(strike=190.0, high_52w=190.0)
        assert _strike_pivot_distance(ctx) == 0.0

    def test_call_uses_high(self) -> None:
        ctx = _ctx(option_type="CALL", strike=200.0, high_52w=190.0)
        result = _strike_pivot_distance(ctx)
        assert result is not None
        assert result > 0

    def test_put_uses_low(self) -> None:
        ctx = _ctx(option_type="PUT", strike=115.0, low_52w=120.0)
        result = _strike_pivot_distance(ctx)
        assert result is not None
        assert result > 0

    def test_missing_pivot_returns_none(self) -> None:
        assert _strike_pivot_distance(_ctx(high_52w=None)) is None


class TestComputeTradeStructure:
    def test_full_structure_scores_high(self) -> None:
        ctx = _ctx()
        result = compute_trade_structure_pillar(ctx, _pillar_config())
        assert result.pillar_id == PillarId.TRADE_STRUCTURE
        assert result.score >= 50
        assert "DELTA_SHARPSHOOTER" in result.tags
        assert "DTE_SWEETSPOT" in result.tags
        assert "IV_RANK_CHEAP" in result.tags

    def test_insufficient_data_returns_zero(self) -> None:
        ctx = ScoringContext(
            evaluation_id="eval-1",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            delta=0.30,
            # everything else None → only abs_delta available.
            gamma=None,
            theta=None,
            iv_percentile=None,
            strike=None,
            high_52w=None,
            low_52w=None,
            dte=0,
        )
        result = compute_trade_structure_pillar(ctx, _pillar_config())
        assert result.score == 0.0
        assert "INSUFFICIENT_DATA" in result.tags

    def test_iv_rank_rich_tag(self) -> None:
        ctx = _ctx(iv_percentile=85.0)
        result = compute_trade_structure_pillar(ctx, _pillar_config())
        assert "IV_RANK_RICH" in result.tags

    def test_gamma_rich_tag(self) -> None:
        # High gamma, low theta → rich ratio.
        ctx = _ctx(gamma=0.08, theta=-0.05, close=180.0, iv=0.40)
        result = compute_trade_structure_pillar(ctx, _pillar_config())
        assert "GAMMA_RICH" in result.tags

    def test_strike_at_pivot_tag(self) -> None:
        ctx = _ctx(strike=188.0, high_52w=190.0)  # dist ≈ 1%
        result = compute_trade_structure_pillar(ctx, _pillar_config())
        assert "STRIKE_AT_PIVOT" in result.tags

    def test_raises_on_wrong_pillar_id(self) -> None:
        wrong = PillarConfigV2(
            pillar_id=PillarId.DIRECTIONAL_CONVICTION,
            display_name="Wrong",
            description="wrong",
            numeric_subscores=[
                NumericSubscoreConfig(
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
        with pytest.raises(ValueError, match="TRADE_STRUCTURE"):
            compute_trade_structure_pillar(_ctx(), wrong)
