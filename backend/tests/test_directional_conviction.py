"""Unit tests for the Directional Conviction pillar (Policy v4.0.0)."""

from __future__ import annotations

from typing import Optional

import pytest

from app.core.schemas import (
    NumericSubscoreConfig,
    PillarConfigV2,
    PillarId,
    SubscoreBreakpoint,
)
from app.pillars.directional_conviction import (
    _DirectionalConvictionAccessor,
    _adx_directional_agreement,
    _breakout_proximity,
    _obv_confirmation,
    _stage_2_trend_template,
    compute_directional_conviction_pillar,
)
from app.pillars.models import ScoringContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pillar_config() -> PillarConfigV2:
    """Minimal 6-subscore config for Directional Conviction.

    Each subscore has a simple monotone breakpoint set so unit tests can
    predict the weighted output without hand-fitting curves.
    """

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
        pillar_id=PillarId.DIRECTIONAL_CONVICTION,
        display_name="Directional Conviction",
        description="v4 Sharpshooter direction pillar",
        numeric_subscores=[
            _linear("stage_2_trend_score", 0.30, 0.0, 100.0),
            _linear("rs_20d", 0.20, -10.0, 10.0),
            _linear("adx_directional_score", 0.15, 0.0, 100.0),
            _linear("breakout_proximity_pct", 0.15, -10.0, 10.0),
            _linear("obv_confirmation_score", 0.10, 0.0, 100.0),
            _linear("sector_rs_20d", 0.10, -5.0, 5.0),
        ],
    )


def _ctx(
    *,
    option_type: str = "CALL",
    close: Optional[float] = 180.0,
    sma50: Optional[float] = 160.0,
    ma_150: Optional[float] = 150.0,
    ma_200: Optional[float] = 145.0,
    high_52w: Optional[float] = 185.0,
    low_52w: Optional[float] = 100.0,
    rs_20d: Optional[float] = 5.0,
    adx_14: Optional[float] = 28.0,
    plus_di: Optional[float] = 30.0,
    minus_di: Optional[float] = 15.0,
    obv_trend: Optional[str] = "RISING",
    sector_rs_20d: Optional[float] = 2.5,
) -> ScoringContext:
    return ScoringContext(
        evaluation_id="eval-1",
        underlying_ticker="TEST",
        option_type=option_type,
        dte_bucket="B",
        close=close,
        sma50=sma50,
        ma_150=ma_150,
        ma_200=ma_200,
        high_52w=high_52w,
        low_52w=low_52w,
        rs_20d=rs_20d,
        adx_14=adx_14,
        plus_di=plus_di,
        minus_di=minus_di,
        obv_trend=obv_trend,
        sector_rs_20d=sector_rs_20d,
    )


# ---------------------------------------------------------------------------
# Derived feature tests
# ---------------------------------------------------------------------------


class TestStage2Template:
    def test_full_uptrend_scores_high(self) -> None:
        # Bullish Minervini setup: close > sma50 > ma_150 > ma_200, well above
        # 52w low, close to 52w high.
        ctx = _ctx()
        score = _stage_2_trend_template(ctx)
        assert score is not None
        assert score >= 85

    def test_downtrend_call_scores_low(self) -> None:
        # Close below MAs — fails bullish criteria.
        ctx = _ctx(close=120.0, sma50=160.0, ma_150=170.0, ma_200=180.0)
        score = _stage_2_trend_template(ctx)
        assert score is not None
        assert score <= 20

    def test_inverted_template_for_put(self) -> None:
        # Same close=120 but for PUT: below MAs is *favorable* for bearish.
        ctx = _ctx(
            option_type="PUT",
            close=120.0,
            sma50=160.0,
            ma_150=170.0,
            ma_200=180.0,
            high_52w=220.0,
            low_52w=115.0,
        )
        score = _stage_2_trend_template(ctx)
        assert score is not None
        assert score >= 70

    def test_returns_none_when_insufficient_data(self) -> None:
        # close + only sma50 → < 5 criteria computable.
        ctx = _ctx(
            ma_150=None,
            ma_200=None,
            high_52w=None,
            low_52w=None,
        )
        assert _stage_2_trend_template(ctx) is None


class TestAdxDirectionalAgreement:
    def test_strong_trend_with_agreement_scores_high(self) -> None:
        ctx = _ctx(adx_14=35.0, plus_di=35.0, minus_di=15.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert score >= 80

    def test_strong_trend_with_disagreement_scores_low(self) -> None:
        # Strong ADX but +DI < -DI for a CALL → penalized.
        ctx = _ctx(adx_14=35.0, plus_di=15.0, minus_di=35.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert score <= 60

    def test_weak_trend_scores_moderate(self) -> None:
        ctx = _ctx(adx_14=10.0, plus_di=20.0, minus_di=18.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert 0 <= score <= 50

    def test_returns_none_without_adx(self) -> None:
        assert _adx_directional_agreement(_ctx(adx_14=None)) is None


class TestBreakoutProximity:
    def test_at_pivot_returns_zero(self) -> None:
        ctx = _ctx(close=185.0, high_52w=185.0)
        assert _breakout_proximity(ctx) == 0.0

    def test_above_pivot_positive_for_call(self) -> None:
        ctx = _ctx(close=200.0, high_52w=190.0)
        result = _breakout_proximity(ctx)
        assert result is not None
        assert result > 0

    def test_below_pivot_positive_for_put(self) -> None:
        # PUT: pivot = 52w low. close > low_52w → post-breakdown-reversal (positive)
        ctx = _ctx(option_type="PUT", close=95.0, low_52w=90.0)
        result = _breakout_proximity(ctx)
        assert result is not None
        assert result > 0


class TestObvConfirmation:
    def test_rising_call_scores_high(self) -> None:
        assert _obv_confirmation(_ctx(obv_trend="RISING")) == 90.0

    def test_falling_put_scores_high(self) -> None:
        assert _obv_confirmation(_ctx(option_type="PUT", obv_trend="FALLING")) == 90.0

    def test_disagreement_scores_low(self) -> None:
        assert _obv_confirmation(_ctx(option_type="PUT", obv_trend="RISING")) == 10.0

    def test_flat_returns_neutral(self) -> None:
        assert _obv_confirmation(_ctx(obv_trend="FLAT")) == 50.0

    def test_missing_returns_none(self) -> None:
        assert _obv_confirmation(_ctx(obv_trend=None)) is None


# ---------------------------------------------------------------------------
# Full pillar integration
# ---------------------------------------------------------------------------


class TestComputeDirectionalConviction:
    def test_bullish_grand_slam_scores_high(self) -> None:
        ctx = _ctx()
        result = compute_directional_conviction_pillar(ctx, _pillar_config())
        assert result.pillar_id == PillarId.DIRECTIONAL_CONVICTION
        assert result.score >= 70
        assert "STAGE_2_TREND" in result.tags
        assert "RS_LEADER" in result.tags
        assert "SECTOR_LEADER" in result.tags

    def test_insufficient_data_returns_zero(self) -> None:
        # Only stage_2_trend computable; rest None → < 3 subscores.
        ctx = ScoringContext(
            evaluation_id="eval-1",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            close=180.0,
            sma50=160.0,
            ma_150=150.0,
            ma_200=145.0,
            high_52w=185.0,
            low_52w=100.0,
            rs_20d=None,
            adx_14=None,
            plus_di=None,
            minus_di=None,
            obv_trend=None,
            sector_rs_20d=None,
        )
        result = compute_directional_conviction_pillar(ctx, _pillar_config())
        assert result.score == 0.0
        assert "INSUFFICIENT_DATA" in result.tags

    def test_scores_bounded_to_0_100(self) -> None:
        ctx = _ctx(rs_20d=1000.0, sector_rs_20d=-1000.0)
        result = compute_directional_conviction_pillar(ctx, _pillar_config())
        assert 0.0 <= result.score <= 100.0

    def test_raises_on_wrong_pillar_id(self) -> None:
        wrong = PillarConfigV2(
            pillar_id=PillarId.MOVE_POTENTIAL,
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
        with pytest.raises(ValueError, match="DIRECTIONAL_CONVICTION"):
            compute_directional_conviction_pillar(_ctx(), wrong)

    def test_accessor_forwards_unknown_attrs(self) -> None:
        ctx = _ctx()
        accessor = _DirectionalConvictionAccessor(ctx)
        # Non-derived attribute passes through via __getattr__
        assert accessor.close == ctx.close
        # Derived attribute comes from the accessor
        assert accessor.stage_2_trend_score == _stage_2_trend_template(ctx)
