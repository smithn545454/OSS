"""Tests for the v3.1.0 Setup Pocket pillar wiring.

The fitted breakpoints for v3.1.0 are produced by `fit_pillars_v31.py` against
real DynamoDB data, which is not available in CI. These tests instead verify
the *wiring*: that `ScoringContext` exposes `entry_price` and `scanner_source`,
that both factory methods populate them correctly, and that the scoring
engine reads them via `getattr` so a synthetic Setup Pocket `PillarConfigV2`
produces a deterministic score.

The tests also smoke-test the v3.1.0 PillarWeights defaults.
"""

from __future__ import annotations

import pytest

from app.core.schemas import (
    CategoricalSubscoreConfig,
    NumericSubscoreConfig,
    PillarConfigV2,
    PillarId,
    PillarWeights,
    SubscoreBreakpoint,
)
from app.pillars.models import ScoringContext
from app.pillars.scoring_engine import score_pillar

# ============================================================================
# Synthetic Setup Pocket pillar fixture
# ============================================================================


@pytest.fixture
def setup_pocket_config() -> PillarConfigV2:
    """A hand-built Setup Pocket pillar that mirrors the v3.1.0 layout
    (DTE, entry_price, scanner_source, abs_delta, convergence_count) so we
    can deterministically score a context against known breakpoints.
    """
    return PillarConfigV2(
        pillar_id=PillarId.SETUP_QUALITY,
        display_name="Setup Pocket",
        description="Test fixture for v3.1.0 Setup Pocket pillar wiring.",
        numeric_subscores=[
            NumericSubscoreConfig(
                subscore_id="dte",
                display_name="DTE at Entry",
                feature_field="dte",
                weight=0.30,
                source_tier="tier1",
                breakpoints=[
                    SubscoreBreakpoint(value=0.0, score=20.0),
                    SubscoreBreakpoint(value=14.0, score=90.0),
                    SubscoreBreakpoint(value=60.0, score=20.0),
                ],
            ),
            NumericSubscoreConfig(
                subscore_id="entry_price",
                display_name="Entry Price Tier",
                feature_field="entry_price",
                weight=0.25,
                source_tier="tier1",
                breakpoints=[
                    SubscoreBreakpoint(value=0.10, score=70.0),
                    SubscoreBreakpoint(value=2.50, score=85.0),
                    SubscoreBreakpoint(value=15.0, score=20.0),
                ],
            ),
            NumericSubscoreConfig(
                subscore_id="abs_delta",
                display_name="|Entry Delta|",
                feature_field="abs_delta",
                weight=0.15,
                source_tier="tier1",
                breakpoints=[
                    SubscoreBreakpoint(value=0.05, score=30.0),
                    SubscoreBreakpoint(value=0.30, score=85.0),
                    SubscoreBreakpoint(value=0.80, score=20.0),
                ],
            ),
            NumericSubscoreConfig(
                subscore_id="convergence_count",
                display_name="Scanner Convergence",
                feature_field="convergence_count",
                weight=0.10,
                source_tier="tier1",
                breakpoints=[
                    SubscoreBreakpoint(value=0.0, score=40.0),
                    SubscoreBreakpoint(value=3.0, score=80.0),
                ],
            ),
        ],
        categorical_subscores=[
            CategoricalSubscoreConfig(
                subscore_id="scanner_source",
                display_name="Scanner Source",
                feature_field="scanner_source",
                weight=0.20,
                category_scores={
                    "UNUSUAL_VOLUME": 90.0,
                    "BREAKDOWN": 80.0,
                    "REVALIDATION": 70.0,
                    "CHEAP_OPTIONS": 55.0,
                    "COMPRESSION_EXPANSION": 40.0,
                    "BREAKOUT": 25.0,
                },
                default_score=50.0,
            ),
        ],
    )


# ============================================================================
# ScoringContext field exposure
# ============================================================================


class TestScoringContextNewFields:
    def test_entry_price_default_is_zero(self):
        ctx = ScoringContext(
            evaluation_id="t",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
        )
        assert ctx.entry_price == 0.0

    def test_scanner_source_default_is_none(self):
        ctx = ScoringContext(
            evaluation_id="t",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
        )
        assert ctx.scanner_source is None

    def test_entry_price_and_scanner_source_settable(self):
        ctx = ScoringContext(
            evaluation_id="t",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            entry_price=2.50,
            scanner_source="UNUSUAL_VOLUME",
        )
        assert ctx.entry_price == 2.50
        assert ctx.scanner_source == "UNUSUAL_VOLUME"

    def test_getattr_lookup_works_for_scoring_engine(self):
        """The scoring engine reads feature values via getattr."""
        ctx = ScoringContext(
            evaluation_id="t",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            entry_price=2.50,
            scanner_source="BREAKDOWN",
        )
        assert getattr(ctx, "entry_price") == 2.50
        assert getattr(ctx, "scanner_source") == "BREAKDOWN"


# ============================================================================
# Setup Pocket scoring through the engine
# ============================================================================


class TestSetupPocketScoring:
    def test_ideal_pocket_scores_high(self, setup_pocket_config):
        """A 14-DTE / $2.50 / UNUSUAL_VOLUME / 0.30-delta / convergence=2
        context lands on the peak of every breakpoint and should score >70.
        """
        ctx = ScoringContext(
            evaluation_id="t",
            underlying_ticker="AAPL",
            option_type="CALL",
            dte_bucket="A",
            dte=14,
            entry_price=2.50,
            scanner_source="UNUSUAL_VOLUME",
            delta=0.30,
            convergence_count=2,
        )
        score, _subs = score_pillar(setup_pocket_config, ctx)
        assert score >= 70.0

    def test_bad_pocket_scores_low(self, setup_pocket_config):
        """A 90-DTE / $15 / BREAKOUT / 0.65-delta / convergence=1 context
        sits on the wrong end of every breakpoint and should score <50.
        """
        ctx = ScoringContext(
            evaluation_id="t",
            underlying_ticker="AAPL",
            option_type="CALL",
            dte_bucket="D",
            dte=90,
            entry_price=15.0,
            scanner_source="BREAKOUT",
            delta=0.65,
            convergence_count=1,
        )
        score, _subs = score_pillar(setup_pocket_config, ctx)
        assert score <= 50.0

    def test_unknown_scanner_falls_back_to_default(self, setup_pocket_config):
        ctx = ScoringContext(
            evaluation_id="t",
            underlying_ticker="AAPL",
            option_type="CALL",
            dte_bucket="A",
            dte=14,
            entry_price=2.50,
            scanner_source="NEW_EXPERIMENTAL_SCANNER",
            delta=0.30,
            convergence_count=2,
        )
        score, subs = score_pillar(setup_pocket_config, ctx)
        scanner_sub = next(s for s in subs if s.name == "scanner_source")
        # Default category score is 50.0
        assert scanner_sub.score == pytest.approx(50.0)

    def test_missing_scanner_drops_subscore(self, setup_pocket_config):
        """If scanner_source is None, the subscore is unavailable and its
        weight is redistributed by the engine; the pillar still scores.
        """
        ctx = ScoringContext(
            evaluation_id="t",
            underlying_ticker="AAPL",
            option_type="CALL",
            dte_bucket="A",
            dte=14,
            entry_price=2.50,
            scanner_source=None,
            delta=0.30,
            convergence_count=2,
        )
        score, _subs = score_pillar(setup_pocket_config, ctx)
        assert 0.0 <= score <= 100.0


# ============================================================================
# Factory method wiring
# ============================================================================


class _FakePosition:
    """Minimal stand-in for a PaperPosition used by from_position_and_features."""

    def __init__(
        self,
        evaluation_id="ev",
        underlying_ticker="AAPL",
        option_type="CALL",
        dte_bucket="A",
        scanner_list=None,
        scanner_source=None,
        convergence_count=2,
        entry_price=2.50,
        entry_delta=0.30,
        dte_at_entry=14,
        **rest,
    ):
        self.evaluation_id = evaluation_id
        self.underlying_ticker = underlying_ticker
        self.option_type = option_type
        self.dte_bucket = dte_bucket
        self.scanner_list = ["UNUSUAL_VOLUME"] if scanner_list is None else scanner_list
        self.scanner_source = scanner_source
        self.convergence_count = convergence_count
        self.entry_price = entry_price
        self.entry_delta = entry_delta
        self.dte_at_entry = dte_at_entry
        # Attributes the factory looks up but we don't care about
        for k, v in rest.items():
            setattr(self, k, v)
        for attr in (
            "entry_underlying_price",
            "entry_atr14_pct",
            "entry_rs_20d",
            "entry_rv20",
            "entry_iv",
            "entry_iv_rv_ratio",
            "entry_iv_percentile",
            "entry_spread_pct",
            "entry_theta_adjusted_edge",
            "entry_feasibility_ratio",
            "entry_open_interest",
            "entry_volume",
            "entry_days_to_earnings",
        ):
            if not hasattr(self, attr):
                setattr(self, attr, None)


class TestFromPositionAndFeaturesWiring:
    def test_populates_entry_price_and_scanner_source(self):
        pos = _FakePosition(
            scanner_source="BREAKDOWN", entry_price=1.85
        )
        ctx = ScoringContext.from_position_and_features(pos, feature_set=None)
        assert ctx.entry_price == 1.85
        assert ctx.scanner_source == "BREAKDOWN"

    def test_falls_back_to_first_scanner_trigger(self):
        pos = _FakePosition(
            scanner_source=None, scanner_list=["UNUSUAL_VOLUME", "CHEAP_OPTIONS"]
        )
        ctx = ScoringContext.from_position_and_features(pos, feature_set=None)
        assert ctx.scanner_source == "UNUSUAL_VOLUME"

    def test_handles_missing_scanner_list(self):
        pos = _FakePosition(scanner_source=None, scanner_list=[])
        ctx = ScoringContext.from_position_and_features(pos, feature_set=None)
        assert ctx.scanner_source is None


class _FakeTrigger:
    def __init__(self, scanner_type):
        self.scanner_type = scanner_type


class _FakeOpportunity:
    def __init__(self, scanner_types, direction_hint="NONE"):
        self.scanner_triggers = [_FakeTrigger(s) for s in scanner_types]
        self.direction_hint = direction_hint


class _FakeEvaluation:
    def __init__(self):
        self.evaluation_id = "ev1"
        self.underlying_ticker = "AAPL"
        self.option_type = "CALL"
        self.dte_bucket = "A"
        self.iv = 0.30
        self.mid = 2.10
        self.delta = 0.32
        self.dte = 14
        self.spread_pct = 3.0
        self.required_move_pct = 1.5
        self.expected_move_pct = 2.0
        self.feasibility_ratio = 0.75
        self.time_adjusted_feasibility = 0.65
        self.open_interest = 500
        self.volume = 200
        self.underlying_price = 180.0


class TestFromEvaluationAndFeaturesWiring:
    def test_populates_entry_price_from_evaluation_mid(self):
        ev = _FakeEvaluation()
        opp = _FakeOpportunity(["UNUSUAL_VOLUME", "BREAKDOWN"])
        ctx = ScoringContext.from_evaluation_and_features(ev, feature_set=None, opportunity=opp)
        assert ctx.entry_price == pytest.approx(2.10)
        assert ctx.scanner_source == "UNUSUAL_VOLUME"

    def test_handles_missing_opportunity(self):
        ev = _FakeEvaluation()
        ctx = ScoringContext.from_evaluation_and_features(ev, feature_set=None, opportunity=None)
        assert ctx.scanner_source is None


# ============================================================================
# PillarWeights v3.1.0 defaults sanity check
# ============================================================================


class TestPillarWeightsDefaults:
    def test_v31_defaults_promote_setup_pocket(self):
        w = PillarWeights()
        assert w.premium_leverage == 0.25
        assert w.underlying_behavior == 0.35
        assert w.setup_quality == 0.40
        assert w.setup_quality > w.premium_leverage  # Setup Pocket dominates
