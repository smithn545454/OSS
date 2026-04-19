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
    _adx_directional_agreement,
    _breakout_proximity,
    _DirectionalConvictionAccessor,
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
        # v4.1.0: ADX=35 is past the peak (22), so expectation relaxed to >=70.
        ctx = _ctx(adx_14=35.0, plus_di=35.0, minus_di=15.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert score >= 70

    def test_strong_trend_with_disagreement_scores_low(self) -> None:
        # Strong ADX but +DI < -DI for a CALL → penalized.
        ctx = _ctx(adx_14=35.0, plus_di=15.0, minus_di=35.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert score <= 60

    def test_weak_trend_scores_moderate(self) -> None:
        # v4.1.0 inverted-U: ADX=10 → base=50, small +DI lead adds ~5 bonus.
        ctx = _ctx(adx_14=10.0, plus_di=20.0, minus_di=18.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert 40 <= score <= 65

    def test_returns_none_without_adx(self) -> None:
        assert _adx_directional_agreement(_ctx(adx_14=None)) is None


class TestAdxInvertedU:
    """v4.1.0 ADX curve: inverted-U peak at 22, decline beyond 30."""

    def test_peak_at_22(self) -> None:
        ctx = _ctx(adx_14=22.0, plus_di=35.0, minus_di=15.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert score >= 95

    def test_very_weak_trend_scores_low(self) -> None:
        ctx = _ctx(adx_14=5.0, plus_di=20.0, minus_di=20.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert 30 <= score <= 50

    def test_very_strong_trend_scores_lower_than_peak(self) -> None:
        # ADX=50 is late-stage → should be well below the peak.
        ctx = _ctx(adx_14=50.0, plus_di=35.0, minus_di=15.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert 45 <= score <= 70

    def test_late_stage_trend_penalized_vs_early_stage(self) -> None:
        early = _adx_directional_agreement(
            _ctx(adx_14=22.0, plus_di=30.0, minus_di=15.0)
        )
        late = _adx_directional_agreement(
            _ctx(adx_14=48.0, plus_di=30.0, minus_di=15.0)
        )
        assert early is not None and late is not None
        assert early > late

    def test_piecewise_linear_interpolation(self) -> None:
        # ADX=15 sits between (10,50) and (18,85); neutral DI → small +5 bonus.
        ctx = _ctx(adx_14=15.0, plus_di=22.0, minus_di=20.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert 68 <= score <= 85


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


# ---------------------------------------------------------------------------
# Direction-awareness symmetry tests for rs_20d + sector_rs_20d
# ---------------------------------------------------------------------------


class TestRs20dDirectionFlip:
    """rs_20d must be read through the direction-aware accessor so that a
    monotonic-upward breakpoint curve rewards strength on CALLs and
    weakness on PUTs symmetrically.
    """

    def test_call_returns_raw_value(self) -> None:
        ctx = _ctx(option_type="CALL", rs_20d=8.0)
        assert _DirectionalConvictionAccessor(ctx).rs_20d == 8.0

    def test_put_negates_value(self) -> None:
        ctx = _ctx(option_type="PUT", rs_20d=8.0)
        # For a PUT, rs_20d=+8 is bearish-unfavorable → accessor returns -8
        assert _DirectionalConvictionAccessor(ctx).rs_20d == -8.0

    def test_put_weakness_becomes_positive(self) -> None:
        ctx = _ctx(option_type="PUT", rs_20d=-8.0)
        # Weak stock + put = favorable → accessor returns +8
        assert _DirectionalConvictionAccessor(ctx).rs_20d == 8.0

    def test_none_propagates(self) -> None:
        ctx = _ctx(option_type="PUT", rs_20d=None)
        assert _DirectionalConvictionAccessor(ctx).rs_20d is None

    def test_symmetric_call_vs_put(self) -> None:
        """A CALL with rs_20d=+X and a PUT with rs_20d=-X should score identically."""
        call_ctx = _ctx(option_type="CALL", rs_20d=6.0)
        put_ctx = _ctx(option_type="PUT", rs_20d=-6.0)
        assert (
            _DirectionalConvictionAccessor(call_ctx).rs_20d
            == _DirectionalConvictionAccessor(put_ctx).rs_20d
        )


class TestSectorRs20dDirectionFlip:
    def test_call_returns_raw_value(self) -> None:
        ctx = _ctx(option_type="CALL", sector_rs_20d=3.0)
        assert _DirectionalConvictionAccessor(ctx).sector_rs_20d == 3.0

    def test_put_negates_value(self) -> None:
        ctx = _ctx(option_type="PUT", sector_rs_20d=3.0)
        assert _DirectionalConvictionAccessor(ctx).sector_rs_20d == -3.0

    def test_put_sector_weakness_becomes_positive(self) -> None:
        ctx = _ctx(option_type="PUT", sector_rs_20d=-4.0)
        assert _DirectionalConvictionAccessor(ctx).sector_rs_20d == 4.0

    def test_none_propagates(self) -> None:
        ctx = _ctx(option_type="PUT", sector_rs_20d=None)
        assert _DirectionalConvictionAccessor(ctx).sector_rs_20d is None


class TestFullPillarDirectionSymmetry:
    """End-to-end: a bearish PUT setup and its mirror-image bullish CALL
    setup should produce similar DC pillar scores. Before the direction
    fix, the PUT would score ~20-30 points lower because rs_20d +
    sector_rs_20d were not flipped.
    """

    def test_bearish_put_matches_bullish_call(self) -> None:
        config = _pillar_config()
        # Bullish CALL setup: stock in Stage 2, strong RS, positive sector RS
        call_ctx = _ctx(
            option_type="CALL",
            close=180.0, sma50=160.0, ma_150=150.0, ma_200=145.0,
            high_52w=185.0, low_52w=100.0,
            rs_20d=6.0, adx_14=28.0, plus_di=30.0, minus_di=15.0,
            obv_trend="RISING", sector_rs_20d=3.0,
        )
        # Mirror-image bearish PUT setup: stock in Stage 4 downtrend, weak
        # RS, negative sector RS, OBV falling, -DI dominant
        put_ctx = _ctx(
            option_type="PUT",
            close=100.0, sma50=120.0, ma_150=130.0, ma_200=140.0,
            high_52w=180.0, low_52w=95.0,
            rs_20d=-6.0, adx_14=28.0, plus_di=15.0, minus_di=30.0,
            obv_trend="FALLING", sector_rs_20d=-3.0,
        )
        call_result = compute_directional_conviction_pillar(call_ctx, config)
        put_result = compute_directional_conviction_pillar(put_ctx, config)
        # Should score within 10 points of each other. Minor differences
        # come from stage-2 template comparisons (which use ratios like
        # "close is 30% above low_52w") and breakout-proximity curves
        # that are NOT perfectly mirror-symmetric given fixture asymmetry.
        # The core symmetry guarantee is that the PUT should NOT be ~20+
        # points lower than the CALL (which was the pre-fix behavior).
        assert abs(call_result.score - put_result.score) <= 10, (
            f"Direction asymmetry: CALL={call_result.score:.1f} "
            f"PUT={put_result.score:.1f}"
        )
        # Both should score high (>= 70) since both setups are favorable
        # for their respective direction.
        assert call_result.score >= 70, f"CALL score too low: {call_result.score}"
        assert put_result.score >= 70, f"PUT score too low: {put_result.score}"

    def test_wrong_direction_put_scores_low(self) -> None:
        """A PUT on a stock in a Stage 2 uptrend should score LOW on DC
        (everything is wrong for a short)."""
        config = _pillar_config()
        ctx = _ctx(
            option_type="PUT",
            close=180.0, sma50=160.0, ma_150=150.0, ma_200=145.0,
            high_52w=185.0, low_52w=100.0,
            rs_20d=6.0,  # Strong RS = bad for PUT
            adx_14=28.0, plus_di=30.0, minus_di=15.0,  # +DI > -DI = bad
            obv_trend="RISING",  # Rising OBV = bad for PUT
            sector_rs_20d=3.0,  # Sector leader = bad for PUT
        )
        result = compute_directional_conviction_pillar(ctx, config)
        # v4.1.0: ADX base at 28 is higher under the inverted-U curve; the
        # wrong-direction penalty still keeps the overall pillar low (<=35).
        assert result.score <= 35, (
            f"Wrong-direction PUT should score low but got {result.score}"
        )


class TestDirectionAwareTags:
    def test_call_rs_leader_tag_fires_on_positive_rs(self) -> None:
        config = _pillar_config()
        ctx = _ctx(option_type="CALL", rs_20d=8.0)
        result = compute_directional_conviction_pillar(ctx, config)
        assert "RS_LEADER" in result.tags
        assert "RS_LAGGARD" not in result.tags

    def test_put_rs_leader_tag_fires_on_negative_rs(self) -> None:
        config = _pillar_config()
        ctx = _ctx(option_type="PUT", rs_20d=-8.0)
        result = compute_directional_conviction_pillar(ctx, config)
        # Bearish PUT with weak stock → leader (for puts)
        assert "RS_LEADER" in result.tags

    def test_put_with_strong_stock_tags_laggard(self) -> None:
        """A PUT on a stock with strong RS is anti-aligned → LAGGARD for puts."""
        config = _pillar_config()
        ctx = _ctx(option_type="PUT", rs_20d=8.0)
        result = compute_directional_conviction_pillar(ctx, config)
        assert "RS_LAGGARD" in result.tags

    def test_sector_leader_tag_direction_aware(self) -> None:
        config = _pillar_config()
        call_ctx = _ctx(option_type="CALL", sector_rs_20d=3.0)
        put_ctx = _ctx(option_type="PUT", sector_rs_20d=-3.0)
        call_result = compute_directional_conviction_pillar(call_ctx, config)
        put_result = compute_directional_conviction_pillar(put_ctx, config)
        assert "SECTOR_LEADER" in call_result.tags
        assert "SECTOR_LEADER" in put_result.tags
