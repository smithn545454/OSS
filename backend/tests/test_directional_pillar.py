"""Tests for pillars/directional.py.

Covers uncovered branches:
- Trend alignment: partial bullish, partial bearish, neutral
- Signal confirmation: breakdown+PUT, breakout+CALL, compression, unusual volume, cheap, conflicting
- Catalyst: various proximity windows
- Tag generation: STRONG_TREND, COUNTER_TREND, HIGH_MOMENTUM, NEGATIVE_MOMENTUM, etc.
"""

from __future__ import annotations

from app.core.schemas import DirectionalPillarConfig, ScannerType
from app.pillars.directional import (
    compute_catalyst_subscore,
    compute_momentum_subscore,
    compute_signal_confirmation_subscore,
    compute_trend_alignment_subscore,
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
# Trend Alignment
# ---------------------------------------------------------------------------


class TestTrendAlignment:
    def test_strong_bullish_call(self):
        """close > sma20 > sma50 => base 90 for CALL."""
        ctx = _ctx(close=150.0, sma20=145.0, sma50=140.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 90.0

    def test_partial_bullish_call(self):
        """close > sma20, sma20 <= sma50 => base 65 for CALL."""
        ctx = _ctx(close=150.0, sma20=145.0, sma50=148.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 65.0

    def test_strong_bearish_call(self):
        """close < sma20 < sma50 => base 10 for CALL."""
        ctx = _ctx(close=130.0, sma20=140.0, sma50=150.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 10.0

    def test_partial_bearish_call(self):
        """close < sma20, sma20 >= sma50 => base 35 for CALL."""
        ctx = _ctx(close=130.0, sma20=140.0, sma50=135.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 35.0

    def test_neutral_call(self):
        """When conditions don't match any branch => base 50."""
        # close == sma20 == sma50 hits the else branch
        ctx = _ctx(close=145.0, sma20=145.0, sma50=145.0)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 50.0

    def test_put_inverts_score(self):
        """PUT contract: base is inverted (100 - base)."""
        # Strong bullish => base 90, inverted => 10
        ctx = _ctx(close=150.0, sma20=145.0, sma50=140.0, option_type="PUT")
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 10.0


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------


class TestMomentum:
    def test_momentum_bucket_a(self):
        ctx = _ctx(dte_bucket="A", return_5d=5.0, return_20d=10.0)
        config = DirectionalPillarConfig()
        sub = compute_momentum_subscore(ctx, config)
        assert sub.name == "momentum"
        assert 0 <= sub.score <= 100

    def test_momentum_none_returns(self):
        """When both returns are None, default 50."""
        ctx = _ctx(return_5d=None, return_20d=None)
        config = DirectionalPillarConfig()
        sub = compute_momentum_subscore(ctx, config)
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
        """SEC filing should take max if both present."""
        ctx = _ctx(days_to_earnings=5, recent_sec_filing=True)
        sub = compute_catalyst_subscore(ctx)
        assert sub.score == 70.0  # max(70 from earnings, 60 from SEC)


# ---------------------------------------------------------------------------
# Tag Generation
# ---------------------------------------------------------------------------


class TestTagGeneration:
    def test_strong_trend_tag(self):
        subscores = [Subscore(name="trend_alignment", raw_value={}, score=90.0, weight=0.3)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "STRONG_TREND" in tags

    def test_counter_trend_tag(self):
        subscores = [Subscore(name="trend_alignment", raw_value={}, score=15.0, weight=0.3)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
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

    def test_breakout_confirmed_call(self):
        subscores = [Subscore(name="signal_confirmation", raw_value={}, score=85.0, weight=0.2)]
        ctx = _ctx(option_type="CALL")
        tags = generate_directional_tags(subscores, ctx)
        assert "BREAKOUT_CONFIRMED" in tags

    def test_breakdown_confirmed_put(self):
        subscores = [Subscore(name="signal_confirmation", raw_value={}, score=85.0, weight=0.2)]
        ctx = _ctx(option_type="PUT")
        tags = generate_directional_tags(subscores, ctx)
        assert "BREAKDOWN_CONFIRMED" in tags

    def test_sector_outperform(self):
        subscores = [Subscore(name="relative_strength", raw_value={}, score=85.0, weight=0.15)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "SECTOR_OUTPERFORM" in tags

    def test_sector_underperform(self):
        subscores = [Subscore(name="relative_strength", raw_value={}, score=15.0, weight=0.15)]
        ctx = _ctx()
        tags = generate_directional_tags(subscores, ctx)
        assert "SECTOR_UNDERPERFORM" in tags
