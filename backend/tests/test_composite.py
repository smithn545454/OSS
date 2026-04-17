"""Unit tests for the v3 + v4 composite formula dispatch (app/pillars/composite.py)."""

from __future__ import annotations

import math

import pytest

from app.core.schemas import (
    NumericSubscoreConfig,
    PillarConfig,
    PillarConfigV2,
    PillarId,
    PillarWeights,
    SubscoreBreakpoint,
)
from app.pillars.composite import (
    V4_MIN_SUBSCORES,
    V4_PILLAR_FLOOR,
    apply_v4_rules,
    compute_composite_score,
    weighted_geometric_mean,
    weighted_sum,
)
from app.pillars.models import PillarResult, Subscore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _v4_pillar_config(pillar_id: PillarId, subscore_id: str = "sub") -> PillarConfigV2:
    """Minimal v4 PillarConfigV2 — a single trivial subscore that scores 50."""
    return PillarConfigV2(
        pillar_id=pillar_id,
        display_name="Test",
        description="test",
        numeric_subscores=[
            NumericSubscoreConfig(
                subscore_id=subscore_id,
                display_name=subscore_id,
                feature_field="close",
                weight=1.0,
                source_tier="tier2",
                breakpoints=[
                    SubscoreBreakpoint(value=0.0, score=50),
                    SubscoreBreakpoint(value=1000.0, score=50),
                ],
            )
        ],
    )


def _v4_pillar_config_full() -> PillarConfig:
    """Full v4 PillarConfig with minimal subscores per pillar — enough to
    exercise the composite formulas without wiring real breakpoints.
    """
    return PillarConfig(
        weights=PillarWeights.v4_default(),
        composite_formula="weighted_geometric_mean",
        directional_conviction=_v4_pillar_config(PillarId.DIRECTIONAL_CONVICTION),
        move_potential=_v4_pillar_config(PillarId.MOVE_POTENTIAL),
        trade_structure=_v4_pillar_config(PillarId.TRADE_STRUCTURE),
    )


def _pillar_result(pillar_id: PillarId, score: float) -> PillarResult:
    return PillarResult(pillar_id=pillar_id, evaluation_id="eval-1", score=score)


# ---------------------------------------------------------------------------
# weighted_sum (v3)
# ---------------------------------------------------------------------------


class TestWeightedSum:
    def test_arithmetic_mean(self) -> None:
        weights = PillarWeights(
            premium_leverage=0.25,
            underlying_behavior=0.35,
            setup_quality=0.40,
        )
        results = [
            _pillar_result(PillarId.PREMIUM_LEVERAGE, 80.0),
            _pillar_result(PillarId.UNDERLYING_BEHAVIOR, 70.0),
            _pillar_result(PillarId.SETUP_QUALITY, 90.0),
        ]
        expected = 0.25 * 80.0 + 0.35 * 70.0 + 0.40 * 90.0
        assert weighted_sum(results, weights) == pytest.approx(expected)

    def test_clamps_high_scores(self) -> None:
        weights = PillarWeights.v3_default()
        results = [
            _pillar_result(PillarId.PREMIUM_LEVERAGE, 500.0),
            _pillar_result(PillarId.UNDERLYING_BEHAVIOR, 500.0),
            _pillar_result(PillarId.SETUP_QUALITY, 500.0),
        ]
        assert weighted_sum(results, weights) == 100.0

    def test_missing_pillars_default_to_fifty(self) -> None:
        """When a pillar weight has no matching result, the formula uses 50."""
        weights = PillarWeights.v3_default()
        assert weighted_sum([], weights) == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# weighted_geometric_mean (v4)
# ---------------------------------------------------------------------------


class TestWeightedGeometricMean:
    def test_three_equal_scores_returns_equal_composite(self) -> None:
        weights = PillarWeights.v4_default()
        results = [
            _pillar_result(PillarId.DIRECTIONAL_CONVICTION, 80.0),
            _pillar_result(PillarId.MOVE_POTENTIAL, 80.0),
            _pillar_result(PillarId.TRADE_STRUCTURE, 80.0),
        ]
        assert weighted_geometric_mean(results, weights) == pytest.approx(80.0, rel=1e-3)

    def test_grand_slam(self) -> None:
        """100/100/100 → 100."""
        weights = PillarWeights.v4_default()
        results = [
            _pillar_result(PillarId.DIRECTIONAL_CONVICTION, 100.0),
            _pillar_result(PillarId.MOVE_POTENTIAL, 100.0),
            _pillar_result(PillarId.TRADE_STRUCTURE, 100.0),
        ]
        assert weighted_geometric_mean(results, weights) == pytest.approx(100.0, rel=1e-3)

    def test_asymmetric_scores(self) -> None:
        """100/100/50 with weights 0.40/0.35/0.25 ≈ 81.2 (geometric mean)."""
        weights = PillarWeights.v4_default()
        results = [
            _pillar_result(PillarId.DIRECTIONAL_CONVICTION, 100.0),
            _pillar_result(PillarId.MOVE_POTENTIAL, 100.0),
            _pillar_result(PillarId.TRADE_STRUCTURE, 50.0),
        ]
        expected = math.pow(100.0, 0.40) * math.pow(100.0, 0.35) * math.pow(50.0, 0.25)
        assert weighted_geometric_mean(results, weights) == pytest.approx(expected, rel=1e-3)

    def test_zero_collapses_composite(self) -> None:
        """Any pillar score 0 → composite 0 (insufficient-data auto-REJECT)."""
        weights = PillarWeights.v4_default()
        results = [
            _pillar_result(PillarId.DIRECTIONAL_CONVICTION, 100.0),
            _pillar_result(PillarId.MOVE_POTENTIAL, 100.0),
            _pillar_result(PillarId.TRADE_STRUCTURE, 0.0),
        ]
        assert weighted_geometric_mean(results, weights) == 0.0

    def test_missing_pillar_zero(self) -> None:
        """Missing pillar_id in weights-populated set → treated as 0 → composite 0."""
        weights = PillarWeights.v4_default()
        results = [
            _pillar_result(PillarId.DIRECTIONAL_CONVICTION, 80.0),
            _pillar_result(PillarId.MOVE_POTENTIAL, 80.0),
            # trade_structure missing
        ]
        assert weighted_geometric_mean(results, weights) == 0.0

    def test_weak_pillar_drags_more_than_arithmetic(self) -> None:
        """Geometric < arithmetic for mixed scores (the whole point of v4)."""
        weights = PillarWeights.v4_default()
        results = [
            _pillar_result(PillarId.DIRECTIONAL_CONVICTION, 90.0),
            _pillar_result(PillarId.MOVE_POTENTIAL, 90.0),
            _pillar_result(PillarId.TRADE_STRUCTURE, 30.0),
        ]
        geo = weighted_geometric_mean(results, weights)
        # Arithmetic sum equivalent for comparison
        arith_weights = PillarWeights(
            premium_leverage=0.40,
            underlying_behavior=0.35,
            setup_quality=0.25,
        )
        arith_results = [
            _pillar_result(PillarId.PREMIUM_LEVERAGE, 90.0),
            _pillar_result(PillarId.UNDERLYING_BEHAVIOR, 90.0),
            _pillar_result(PillarId.SETUP_QUALITY, 30.0),
        ]
        arith = weighted_sum(arith_results, arith_weights)
        assert geo < arith, f"geometric={geo}, arithmetic={arith}"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_dispatches_to_arithmetic_for_v3_config(self) -> None:
        config = PillarConfig.v3_default()
        results = [
            _pillar_result(PillarId.PREMIUM_LEVERAGE, 80.0),
            _pillar_result(PillarId.UNDERLYING_BEHAVIOR, 80.0),
            _pillar_result(PillarId.SETUP_QUALITY, 80.0),
        ]
        assert compute_composite_score(results, config) == pytest.approx(80.0)

    def test_dispatches_to_geometric_for_v4_config(self) -> None:
        config = _v4_pillar_config_full()
        results = [
            _pillar_result(PillarId.DIRECTIONAL_CONVICTION, 80.0),
            _pillar_result(PillarId.MOVE_POTENTIAL, 80.0),
            _pillar_result(PillarId.TRADE_STRUCTURE, 80.0),
        ]
        assert compute_composite_score(results, config) == pytest.approx(80.0, rel=1e-3)

    def test_zero_collapses_v4_dispatch(self) -> None:
        config = _v4_pillar_config_full()
        results = [
            _pillar_result(PillarId.DIRECTIONAL_CONVICTION, 99.0),
            _pillar_result(PillarId.MOVE_POTENTIAL, 99.0),
            _pillar_result(PillarId.TRADE_STRUCTURE, 0.0),
        ]
        assert compute_composite_score(results, config) == 0.0


# ---------------------------------------------------------------------------
# apply_v4_rules — min-subscore + floor
# ---------------------------------------------------------------------------


def _make_subscore(available: bool, score: float = 60.0) -> Subscore:
    sub = Subscore(name="s", raw_value=1.0, score=score, weight=0.2)
    object.__setattr__(sub, "_available", available)
    return sub


class TestApplyV4Rules:
    def test_below_minimum_returns_zero(self) -> None:
        subs = [_make_subscore(True), _make_subscore(False), _make_subscore(False)]
        assert apply_v4_rules(80.0, subs) == 0.0

    def test_at_minimum_does_not_collapse(self) -> None:
        subs = [
            _make_subscore(True),
            _make_subscore(True),
            _make_subscore(True),
            _make_subscore(False),
        ]
        assert count_avail(subs) == V4_MIN_SUBSCORES
        assert apply_v4_rules(70.0, subs) == 70.0

    def test_applies_floor(self) -> None:
        subs = [_make_subscore(True), _make_subscore(True), _make_subscore(True)]
        # Floor of 1.0 — if computed score is 0.5, result should be 1.0
        assert apply_v4_rules(0.5, subs) == V4_PILLAR_FLOOR

    def test_clamps_above_100(self) -> None:
        subs = [_make_subscore(True), _make_subscore(True), _make_subscore(True)]
        assert apply_v4_rules(150.0, subs) == 100.0

    def test_missing_availability_flag_counts_as_available(self) -> None:
        """Subscores built outside the engine have no _available flag — treat as available."""
        bare = Subscore(name="s", raw_value=1.0, score=50.0, weight=0.5)
        assert apply_v4_rules(50.0, [bare, bare, bare]) == 50.0


def count_avail(subs: list[Subscore]) -> int:
    from app.pillars.composite import count_available

    return count_available(subs)
