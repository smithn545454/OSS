"""Tests for pillars/directional.py.

Covers:
- Trend alignment: EMA stack classification + SMA fallback
- Momentum: RSI + MACD + returns composite
- Trend strength: ADX with +DI/-DI
- Signal confirmation: scanner triggers
- Relative strength: RS vs SPY + OBV confirmation
- Catalyst: earnings proximity + SEC filings
- Tag generation
"""

from __future__ import annotations

from app.core.schemas import DirectionalPillarConfig, ScannerType
from app.pillars.directional import (
    compute_catalyst_subscore,
    compute_directional_pillar,
    compute_momentum_subscore,
    compute_relative_strength_subscore,
    compute_signal_confirmation_subscore,
    compute_trend_alignment_subscore,
    compute_trend_strength_subscore,
    generate_directional_tags,
)
from app.pillars.models import ScoringContext, Subscore


def _ctx(**overrides) -> ScoringContext:
    defaults = dict(
        evaluation_id="e1",
        underlying_ticker="AAPL",
        option_type="CALL",
        dte_bucket="B",
        close=150.0,
        sma20=145.0,
        sma50=140.0,
        return_5d=5.0,
        return_20d=10.0,
        trend_aligned_bullish=True,
        trend_aligned_bearish=False,
        scanner_triggers=[],
        direction_hint="NONE",
        days_to_earnings=None,
        recent_sec_filing=False,
    )
    defaults.update(overrides)
    return ScoringContext(**defaults)


# ---------------------------------------------------------------------------
# Trend Alignment — EMA path
# ---------------------------------------------------------------------------


class TestTrendAlignmentEMA:
    def test_bullish_stack_call(self):
        """BULLISH_STACK => 92 for CALL."""
        ctx = _ctx(ema_alignment="BULLISH_STACK", ema_50=140.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 92.0

    def test_bearish_stack_call(self):
        """BEARISH_STACK => 8 for CALL."""
        ctx = _ctx(ema_alignment="BEARISH_STACK", ema_50=160.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 8.0

    def test_above_all_call(self):
        """ABOVE_ALL => 75 for CALL."""
        ctx = _ctx(ema_alignment="ABOVE_ALL", ema_50=140.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 75.0

    def test_below_all_call(self):
        """BELOW_ALL => 25 for CALL."""
        ctx = _ctx(ema_alignment="BELOW_ALL", ema_50=160.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 25.0

    def test_mixed_above_ema50(self):
        """MIXED + close > EMA50 => 58 for CALL."""
        ctx = _ctx(close=150.0, ema_alignment="MIXED", ema_50=140.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 58.0

    def test_mixed_below_ema50(self):
        """MIXED + close < EMA50 => 42 for CALL."""
        ctx = _ctx(close=130.0, ema_alignment="MIXED", ema_50=140.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 42.0

    def test_put_inverts_ema(self):
        """PUT: BULLISH_STACK => 100 - 92 = 8."""
        ctx = _ctx(
            ema_alignment="BULLISH_STACK", ema_50=140.0, option_type="PUT"
        )
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 8.0

    def test_bearish_stack_put(self):
        """PUT: BEARISH_STACK => 100 - 8 = 92."""
        ctx = _ctx(
            ema_alignment="BEARISH_STACK", ema_50=160.0, option_type="PUT"
        )
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 92.0


# ---------------------------------------------------------------------------
# Trend Alignment — SMA fallback
# ---------------------------------------------------------------------------


class TestTrendAlignmentSMAFallback:
    def test_strong_bullish_call(self):
        """close > sma20 > sma50 => base 90 for CALL (SMA fallback)."""
        ctx = _ctx(close=150.0, sma20=145.0, sma50=140.0, ema_alignment=None)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 90.0

    def test_partial_bullish_call(self):
        ctx = _ctx(close=150.0, sma20=145.0, sma50=148.0, ema_alignment=None)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 65.0

    def test_strong_bearish_call(self):
        ctx = _ctx(close=130.0, sma20=140.0, sma50=150.0, ema_alignment=None)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 10.0

    def test_partial_bearish_call(self):
        ctx = _ctx(close=130.0, sma20=140.0, sma50=135.0, ema_alignment=None)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 35.0

    def test_neutral_call(self):
        ctx = _ctx(close=145.0, sma20=145.0, sma50=145.0, ema_alignment=None)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 50.0

    def test_put_inverts_sma(self):
        ctx = _ctx(
            close=150.0, sma20=145.0, sma50=140.0,
            option_type="PUT", ema_alignment=None,
        )
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 10.0

    def test_missing_data_neutral(self):
        ctx = _ctx(close=None, sma20=None, sma50=None, ema_alignment=None)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 50.0


# ---------------------------------------------------------------------------
# Momentum — composite RSI + MACD + returns
# ---------------------------------------------------------------------------


class TestMomentum:
    def test_momentum_with_all_indicators(self):
        """When RSI, MACD, and returns are all available."""
        ctx = _ctx(
            dte_bucket="A", return_5d=5.0, return_20d=10.0,
            rsi_14=60.0, macd_histogram=0.5,
        )
        config = DirectionalPillarConfig()
        sub = compute_momentum_subscore(ctx, config)
        assert sub.name == "momentum"
        assert sub.score > 60  # All indicators bullish for CALL

    def test_momentum_none_returns(self):
        """When both returns are None and no indicators, default 50."""
        ctx = _ctx(return_5d=None, return_20d=None)
        config = DirectionalPillarConfig()
        sub = compute_momentum_subscore(ctx, config)
        assert sub.score == 50.0

    def test_momentum_rsi_only(self):
        """When only RSI is available alongside returns."""
        ctx = _ctx(
            return_5d=5.0, return_20d=10.0,
            rsi_14=60.0, macd_histogram=None,
        )
        config = DirectionalPillarConfig()
        sub = compute_momentum_subscore(ctx, config)
        assert sub.score > 50  # Bullish RSI + positive returns

    def test_momentum_overbought_rsi_dampens_call(self):
        """Overbought RSI (>70) should dampen CALL momentum."""
        ctx_normal = _ctx(
            return_5d=5.0, return_20d=10.0,
            rsi_14=60.0, macd_histogram=0.5,
        )
        ctx_overbought = _ctx(
            return_5d=5.0, return_20d=10.0,
            rsi_14=85.0, macd_histogram=0.5,
        )
        config = DirectionalPillarConfig()
        sub_normal = compute_momentum_subscore(ctx_normal, config)
        sub_overbought = compute_momentum_subscore(ctx_overbought, config)
        assert sub_normal.score > sub_overbought.score

    def test_momentum_put_inversions(self):
        """PUT with bearish indicators should score high."""
        ctx = _ctx(
            option_type="PUT",
            return_5d=-5.0, return_20d=-10.0,
            rsi_14=25.0, macd_histogram=-0.5,
        )
        config = DirectionalPillarConfig()
        sub = compute_momentum_subscore(ctx, config)
        assert sub.score > 60  # Bearish signals = high for PUT

    def test_momentum_bucket_a(self):
        ctx = _ctx(dte_bucket="A", return_5d=5.0, return_20d=10.0)
        config = DirectionalPillarConfig()
        sub = compute_momentum_subscore(ctx, config)
        assert 0 <= sub.score <= 100


# ---------------------------------------------------------------------------
# Trend Strength (ADX)
# ---------------------------------------------------------------------------


class TestTrendStrength:
    def test_strong_bullish_trend_call(self):
        """ADX >= 40, +DI > -DI => high score for CALL."""
        ctx = _ctx(adx_14=45.0, plus_di=30.0, minus_di=15.0)
        sub = compute_trend_strength_subscore(ctx)
        assert sub.score == 92.0

    def test_strong_bearish_trend_call(self):
        """ADX >= 40, -DI > +DI => low score for CALL (inverted)."""
        ctx = _ctx(adx_14=45.0, plus_di=15.0, minus_di=30.0)
        sub = compute_trend_strength_subscore(ctx)
        assert sub.score == 8.0  # 100 - 92

    def test_strong_bearish_trend_put(self):
        """ADX >= 40, -DI > +DI => high score for PUT."""
        ctx = _ctx(
            adx_14=45.0, plus_di=15.0, minus_di=30.0, option_type="PUT"
        )
        sub = compute_trend_strength_subscore(ctx)
        assert sub.score == 92.0

    def test_moderate_trend(self):
        """ADX 25-40, matching direction."""
        ctx = _ctx(adx_14=30.0, plus_di=25.0, minus_di=15.0)
        sub = compute_trend_strength_subscore(ctx)
        assert 80 <= sub.score <= 92

    def test_weak_trend(self):
        """ADX 15-25 => near neutral."""
        ctx = _ctx(adx_14=20.0, plus_di=20.0, minus_di=15.0)
        sub = compute_trend_strength_subscore(ctx)
        assert 55 <= sub.score <= 80

    def test_no_trend(self):
        """ADX < 15 => neutral (50)."""
        ctx = _ctx(adx_14=10.0, plus_di=15.0, minus_di=14.0)
        sub = compute_trend_strength_subscore(ctx)
        assert sub.score == 50.0

    def test_missing_adx_neutral(self):
        """When ADX data is unavailable, score is neutral."""
        ctx = _ctx(adx_14=None, plus_di=None, minus_di=None)
        sub = compute_trend_strength_subscore(ctx)
        assert sub.score == 50.0


# ---------------------------------------------------------------------------
# Signal Confirmation
# ---------------------------------------------------------------------------


class TestSignalConfirmation:
    def test_no_triggers(self):
        ctx = _ctx(scanner_triggers=[])
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 45.0

    def test_breakout_call(self):
        ctx = _ctx(scanner_triggers=[ScannerType.BREAKOUT], option_type="CALL")
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 85.0

    def test_breakdown_put(self):
        ctx = _ctx(scanner_triggers=[ScannerType.BREAKDOWN], option_type="PUT")
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 85.0

    def test_breakout_put_conflicting(self):
        ctx = _ctx(scanner_triggers=[ScannerType.BREAKOUT], option_type="PUT")
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 25.0

    def test_breakdown_call_conflicting(self):
        ctx = _ctx(scanner_triggers=[ScannerType.BREAKDOWN], option_type="CALL")
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 25.0

    def test_compression_matching_call(self):
        ctx = _ctx(
            scanner_triggers=[ScannerType.COMPRESSION_EXPANSION],
            option_type="CALL",
            direction_hint="CALL",
        )
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 75.0

    def test_compression_matching_put(self):
        ctx = _ctx(
            scanner_triggers=[ScannerType.COMPRESSION_EXPANSION],
            option_type="PUT",
            direction_hint="PUT",
        )
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 75.0

    def test_compression_neutral(self):
        ctx = _ctx(
            scanner_triggers=[ScannerType.COMPRESSION_EXPANSION],
            option_type="CALL",
            direction_hint="NONE",
        )
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 55.0

    def test_compression_conflicting(self):
        ctx = _ctx(
            scanner_triggers=[ScannerType.COMPRESSION_EXPANSION],
            option_type="PUT",
            direction_hint="CALL",
        )
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 35.0

    def test_unusual_volume_matching(self):
        ctx = _ctx(
            scanner_triggers=[ScannerType.UNUSUAL_VOLUME],
            option_type="CALL",
            direction_hint="CALL",
        )
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 65.0

    def test_unusual_volume_neutral(self):
        ctx = _ctx(
            scanner_triggers=[ScannerType.UNUSUAL_VOLUME],
            option_type="CALL",
            direction_hint="NONE",
        )
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 55.0

    def test_unusual_volume_conflicting(self):
        ctx = _ctx(
            scanner_triggers=[ScannerType.UNUSUAL_VOLUME],
            option_type="CALL",
            direction_hint="PUT",
        )
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 40.0

    def test_cheap_options_scanner(self):
        ctx = _ctx(scanner_triggers=[ScannerType.CHEAP_OPTIONS], option_type="CALL")
        sub = compute_signal_confirmation_subscore(ctx)
        assert sub.score == 70.0


# ---------------------------------------------------------------------------
# Relative Strength + OBV
# ---------------------------------------------------------------------------


class TestRelativeStrength:
    def test_rs_only(self):
        """When OBV is unavailable, score is pure RS."""
        ctx = _ctx(rs_20d=5.0, obv_trend=None)
        config = DirectionalPillarConfig()
        sub = compute_relative_strength_subscore(ctx, config)
        assert sub.score > 50  # Positive RS for CALL

    def test_rs_with_obv_rising_call(self):
        """Positive RS + RISING OBV for CALL => high score."""
        ctx = _ctx(rs_20d=5.0, obv_trend="RISING")
        config = DirectionalPillarConfig()
        sub = compute_relative_strength_subscore(ctx, config)
        assert sub.score > 60

    def test_rs_with_obv_falling_call(self):
        """Positive RS but FALLING OBV dampens CALL score."""
        ctx_rising = _ctx(rs_20d=5.0, obv_trend="RISING")
        ctx_falling = _ctx(rs_20d=5.0, obv_trend="FALLING")
        config = DirectionalPillarConfig()
        sub_rising = compute_relative_strength_subscore(ctx_rising, config)
        sub_falling = compute_relative_strength_subscore(ctx_falling, config)
        assert sub_rising.score > sub_falling.score

    def test_obv_flat_is_neutral(self):
        """FLAT OBV contributes 50."""
        ctx = _ctx(rs_20d=0.0, obv_trend="FLAT")
        config = DirectionalPillarConfig()
        sub = compute_relative_strength_subscore(ctx, config)
        # RS near zero + OBV at 50 => near neutral
        assert 45 <= sub.score <= 60

    def test_put_obv_falling_is_good(self):
        """For PUT, FALLING OBV => high OBV score."""
        ctx = _ctx(rs_20d=-3.0, obv_trend="FALLING", option_type="PUT")
        config = DirectionalPillarConfig()
        sub = compute_relative_strength_subscore(ctx, config)
        assert sub.score > 55

    def test_missing_rs_neutral(self):
        ctx = _ctx(rs_20d=None, obv_trend=None)
        config = DirectionalPillarConfig()
        sub = compute_relative_strength_subscore(ctx, config)
        assert sub.score == 50.0


# ---------------------------------------------------------------------------
# Catalyst
# ---------------------------------------------------------------------------


class TestCatalyst:
    def test_no_catalyst(self):
        ctx = _ctx(days_to_earnings=None, recent_sec_filing=False)
        sub = compute_catalyst_subscore(ctx)
        assert sub.score == 50.0

    def test_earnings_7_days(self):
        ctx = _ctx(days_to_earnings=5)
        sub = compute_catalyst_subscore(ctx)
        assert sub.score == 70.0

    def test_earnings_14_days(self):
        ctx = _ctx(days_to_earnings=10)
        sub = compute_catalyst_subscore(ctx)
        assert sub.score == 60.0

    def test_earnings_30_days(self):
        ctx = _ctx(days_to_earnings=20)
        sub = compute_catalyst_subscore(ctx)
        assert sub.score == 55.0

    def test_sec_filing_only(self):
        ctx = _ctx(days_to_earnings=None, recent_sec_filing=True)
        sub = compute_catalyst_subscore(ctx)
        assert sub.score == 60.0

    def test_sec_filing_and_earnings(self):
        ctx = _ctx(days_to_earnings=5, recent_sec_filing=True)
        sub = compute_catalyst_subscore(ctx)
        assert sub.score == 70.0


# ---------------------------------------------------------------------------
# Tag Generation
# ---------------------------------------------------------------------------


class TestTagGeneration:
    def test_strong_trend_tag(self):
        subscores = [Subscore(name="trend_alignment", raw_value={}, score=90.0, weight=0.25)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "STRONG_TREND" in tags

    def test_counter_trend_tag(self):
        subscores = [Subscore(name="trend_alignment", raw_value={}, score=15.0, weight=0.25)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "COUNTER_TREND" in tags

    def test_ema_bullish_stack_tag(self):
        subscores = [
            Subscore(
                name="trend_alignment",
                raw_value={"ema_alignment": "BULLISH_STACK"},
                score=92.0, weight=0.25,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "EMA_BULLISH_STACK" in tags
        assert "STRONG_TREND" in tags

    def test_ema_bearish_stack_tag(self):
        subscores = [
            Subscore(
                name="trend_alignment",
                raw_value={"ema_alignment": "BEARISH_STACK"},
                score=8.0, weight=0.25,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "EMA_BEARISH_STACK" in tags
        assert "COUNTER_TREND" in tags

    def test_high_momentum_tag(self):
        subscores = [Subscore(name="momentum", raw_value={}, score=85.0, weight=0.25)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "HIGH_MOMENTUM" in tags

    def test_negative_momentum_tag(self):
        subscores = [Subscore(name="momentum", raw_value={}, score=15.0, weight=0.25)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "NEGATIVE_MOMENTUM" in tags

    def test_rsi_overbought_tag(self):
        subscores = [
            Subscore(
                name="momentum",
                raw_value={"rsi_14": 75.0, "macd_histogram": None},
                score=60.0, weight=0.25,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "RSI_OVERBOUGHT" in tags

    def test_rsi_oversold_tag(self):
        subscores = [
            Subscore(
                name="momentum",
                raw_value={"rsi_14": 25.0, "macd_histogram": None},
                score=30.0, weight=0.25,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "RSI_OVERSOLD" in tags

    def test_macd_bullish_tag(self):
        subscores = [
            Subscore(
                name="momentum",
                raw_value={"rsi_14": None, "macd_histogram": 0.5},
                score=60.0, weight=0.25,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "MACD_BULLISH" in tags

    def test_macd_bearish_tag(self):
        subscores = [
            Subscore(
                name="momentum",
                raw_value={"rsi_14": None, "macd_histogram": -0.3},
                score=40.0, weight=0.25,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "MACD_BEARISH" in tags

    def test_strong_adx_tag(self):
        subscores = [
            Subscore(
                name="trend_strength",
                raw_value={"adx_14": 30.0},
                score=80.0, weight=0.15,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "STRONG_ADX" in tags

    def test_weak_trend_tag(self):
        subscores = [
            Subscore(
                name="trend_strength",
                raw_value={"adx_14": 10.0},
                score=50.0, weight=0.15,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "WEAK_TREND" in tags

    def test_breakout_confirmed_call(self):
        subscores = [Subscore(name="signal_confirmation", raw_value={}, score=85.0, weight=0.15)]
        ctx = _ctx(option_type="CALL")
        tags = generate_directional_tags(subscores, ctx)
        assert "BREAKOUT_CONFIRMED" in tags

    def test_breakdown_confirmed_put(self):
        subscores = [Subscore(name="signal_confirmation", raw_value={}, score=85.0, weight=0.15)]
        ctx = _ctx(option_type="PUT")
        tags = generate_directional_tags(subscores, ctx)
        assert "BREAKDOWN_CONFIRMED" in tags

    def test_sector_outperform(self):
        subscores = [Subscore(name="relative_strength", raw_value={}, score=85.0, weight=0.10)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "SECTOR_OUTPERFORM" in tags

    def test_sector_underperform(self):
        subscores = [Subscore(name="relative_strength", raw_value={}, score=15.0, weight=0.10)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "SECTOR_UNDERPERFORM" in tags

    def test_volume_confirmed_tag(self):
        subscores = [
            Subscore(
                name="relative_strength",
                raw_value={"obv_trend": "RISING", "obv_score": 80.0},
                score=75.0, weight=0.10,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "VOLUME_CONFIRMED" in tags

    def test_earnings_imminent_tag(self):
        subscores = [
            Subscore(
                name="catalyst",
                raw_value={"days_to_earnings": 5, "recent_sec_filing": False},
                score=70.0, weight=0.10,
            )
        ]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "EARNINGS_IMMINENT" in tags


# ---------------------------------------------------------------------------
# Full Pillar
# ---------------------------------------------------------------------------


class TestDirectionalPillar:
    def test_full_pillar_produces_6_subscores(self):
        """The pillar should produce exactly 6 subscores."""
        ctx = _ctx()
        result = compute_directional_pillar(ctx)
        assert len(result.subscores) == 6
        names = [s.name for s in result.subscores]
        assert "trend_alignment" in names
        assert "momentum" in names
        assert "trend_strength" in names
        assert "signal_confirmation" in names
        assert "relative_strength" in names
        assert "catalyst" in names

    def test_full_pillar_score_in_range(self):
        ctx = _ctx()
        result = compute_directional_pillar(ctx)
        assert 0 <= result.score <= 100

    def test_weights_sum_to_one(self):
        ctx = _ctx()
        result = compute_directional_pillar(ctx)
        total = sum(s.weight for s in result.subscores)
        assert abs(total - 1.0) < 1e-4

    def test_all_none_indicators_produce_neutral_trend_strength(self):
        """When no technical indicators available, trend_strength = 50."""
        ctx = _ctx(adx_14=None, plus_di=None, minus_di=None)
        result = compute_directional_pillar(ctx)
        ts = [s for s in result.subscores if s.name == "trend_strength"][0]
        assert ts.score == 50.0
