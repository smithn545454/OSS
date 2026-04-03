"""Tests for pillars/models.py and pillar scoring modules.

Covers Subscore, PillarResult, ScoringContext and the pillar computation functions.
"""

from __future__ import annotations

import pytest

from app.core.schemas import PillarId
from app.pillars.models import PillarResult, ScoringContext, Subscore


# ---------------------------------------------------------------------------
# Subscore
# ---------------------------------------------------------------------------


class TestSubscore:
    def test_weighted_contribution(self):
        s = Subscore(name="test", raw_value=1.0, score=80.0, weight=0.30)
        assert s.weighted_contribution == pytest.approx(24.0)

    def test_distance_from_neutral(self):
        s = Subscore(name="test", raw_value=1.0, score=90.0, weight=0.30)
        assert s.distance_from_neutral == 40.0

    def test_to_contributor(self):
        s = Subscore(name="test", raw_value=1.0, score=80.0, weight=0.30)
        c = s.to_contributor()
        assert c.feature_name == "test"
        assert c.subscore == 80.0


# ---------------------------------------------------------------------------
# PillarResult
# ---------------------------------------------------------------------------


class TestPillarResult:
    def test_top_contributors(self):
        result = PillarResult(
            pillar_id=PillarId.DIRECTIONAL,
            evaluation_id="e1",
            score=75.0,
            subscores=[
                Subscore(name="trend", raw_value=None, score=90.0, weight=0.30),
                Subscore(name="momentum", raw_value=None, score=50.0, weight=0.25),
                Subscore(name="signal", raw_value=None, score=30.0, weight=0.20),
                Subscore(name="strength", raw_value=None, score=55.0, weight=0.15),
            ],
        )
        top = result.top_contributors
        assert len(top) == 3
        # Trend (90 - 50 = 40) should be first
        assert top[0].feature_name == "trend"

    def test_to_pillar_score(self):
        result = PillarResult(
            pillar_id=PillarId.DIRECTIONAL,
            evaluation_id="e1",
            score=72.5,
            subscores=[
                Subscore(name="trend", raw_value=None, score=90.0, weight=0.30),
            ],
            tags=["STRONG_TREND"],
        )
        ps = result.to_pillar_score()
        assert ps.pillar_id == PillarId.DIRECTIONAL
        assert ps.score == 72  # int(round(72.5)) with banker's rounding


# ---------------------------------------------------------------------------
# ScoringContext
# ---------------------------------------------------------------------------


class TestScoringContext:
    def test_is_call(self):
        ctx = ScoringContext(
            evaluation_id="e1",
            underlying_ticker="AAPL",
            option_type="CALL",
            dte_bucket="B",
        )
        assert ctx.is_call is True

    def test_is_put(self):
        ctx = ScoringContext(
            evaluation_id="e1",
            underlying_ticker="AAPL",
            option_type="PUT",
            dte_bucket="B",
        )
        assert ctx.is_call is False


# ---------------------------------------------------------------------------
# Directional Pillar Subscores
# ---------------------------------------------------------------------------


class TestDirectionalPillarSubscores:
    def _ctx(self, **kwargs) -> ScoringContext:
        defaults = dict(
            evaluation_id="e1",
            underlying_ticker="AAPL",
            option_type="CALL",
            dte_bucket="B",
            close=150.0,
            sma20=145.0,
            sma50=140.0,
            return_5d=3.0,
            return_20d=6.0,
            rs_20d=5.0,
        )
        defaults.update(kwargs)
        return ScoringContext(**defaults)

    def test_trend_bullish_call(self):
        from app.pillars.directional import compute_trend_alignment_subscore
        ctx = self._ctx(close=150, sma20=145, sma50=140)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 90.0

    def test_trend_bearish_put(self):
        from app.pillars.directional import compute_trend_alignment_subscore
        ctx = self._ctx(option_type="PUT", close=130, sma20=140, sma50=145)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 90.0  # Bearish is good for PUT

    def test_trend_missing_data(self):
        from app.pillars.directional import compute_trend_alignment_subscore
        ctx = self._ctx(sma20=None)
        sub = compute_trend_alignment_subscore(ctx)
        assert sub.score == 50.0  # Neutral


# ---------------------------------------------------------------------------
# Volatility Pillar Subscores
# ---------------------------------------------------------------------------


class TestVolatilityPillarSubscores:
    def _ctx(self, **kwargs) -> ScoringContext:
        defaults = dict(
            evaluation_id="e1",
            underlying_ticker="AAPL",
            option_type="CALL",
            dte_bucket="B",
            iv=0.30,
            rv20=0.25,
            iv_rv_ratio=1.2,
            iv_percentile=40.0,
        )
        defaults.update(kwargs)
        return ScoringContext(**defaults)

    def test_iv_vs_rv_no_data(self):
        from app.pillars.volatility import compute_iv_vs_rv_subscore
        ctx = self._ctx(iv_rv_ratio=None)
        sub = compute_iv_vs_rv_subscore(ctx)
        assert sub.score == 50.0

    def test_iv_vs_rv_cheap(self):
        from app.pillars.volatility import compute_iv_vs_rv_subscore
        ctx = self._ctx(iv_rv_ratio=0.8)
        sub = compute_iv_vs_rv_subscore(ctx)
        assert sub.score == 95.0

    def test_iv_percentile_low(self):
        from app.pillars.volatility import compute_iv_percentile_subscore
        ctx = self._ctx(iv_percentile=15.0)
        sub = compute_iv_percentile_subscore(ctx)
        assert sub.score == 95.0

    def test_iv_percentile_none(self):
        from app.pillars.volatility import compute_iv_percentile_subscore
        ctx = self._ctx(iv_percentile=None)
        sub = compute_iv_percentile_subscore(ctx)
        assert sub.score == 50.0


# ---------------------------------------------------------------------------
# Structure Pillar Subscores
# ---------------------------------------------------------------------------


class TestEntryQualityPillarSubscores:
    """Tests for Entry Quality pillar subscores (stored as STRUCTURE)."""

    def _ctx(self, **kwargs) -> ScoringContext:
        defaults = dict(
            evaluation_id="e1",
            underlying_ticker="AAPL",
            option_type="CALL",
            dte_bucket="B",
            delta=0.15,
            iv=0.25,
            dte=30,
        )
        defaults.update(kwargs)
        return ScoringContext(**defaults)

    def test_delta_sweet_spot(self):
        from app.pillars.structure import compute_delta_moneyness_subscore
        ctx = self._ctx(delta=0.10)
        sub = compute_delta_moneyness_subscore(ctx)
        assert sub.score >= 90.0

    def test_delta_high(self):
        from app.pillars.structure import compute_delta_moneyness_subscore
        ctx = self._ctx(delta=0.65)
        sub = compute_delta_moneyness_subscore(ctx)
        assert sub.score <= 25.0

    def test_raw_iv_low(self):
        from app.pillars.structure import compute_raw_iv_subscore
        ctx = self._ctx(iv=0.12)
        sub = compute_raw_iv_subscore(ctx)
        assert sub.score >= 90.0

    def test_dte_good(self):
        from app.pillars.structure import compute_dte_appropriateness_subscore
        ctx = self._ctx(dte=45)
        sub = compute_dte_appropriateness_subscore(ctx)
        assert sub.score >= 80.0
