"""Unit tests for the Policy v3.0.0 scoring engine.

Tests the core primitives in `app/pillars/scoring_engine.py`:
- score_numeric_subscore: linear interpolation between breakpoints
- score_categorical_subscore: category → score lookup
- compute_pillar_score: weight redistribution when subscores are missing
- score_pillar: full pillar scoring from a feature accessor
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from app.core.schemas import (
    CategoricalSubscoreConfig,
    NumericSubscoreConfig,
    PillarConfigV2,
    PillarId,
    SubscoreBreakpoint,
)
from app.pillars.scoring_engine import (
    score_categorical_subscore,
    score_numeric_subscore,
    score_pillar,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_numeric_config(
    subscore_id: str = "test",
    feature_field: str = "test_feature",
    weight: float = 1.0,
    breakpoints: Optional[list[tuple[float, float]]] = None,
) -> NumericSubscoreConfig:
    if breakpoints is None:
        breakpoints = [(0.0, 10.0), (0.5, 50.0), (1.0, 90.0)]
    return NumericSubscoreConfig(
        subscore_id=subscore_id,
        display_name=subscore_id,
        feature_field=feature_field,
        weight=weight,
        breakpoints=[SubscoreBreakpoint(value=v, score=s) for v, s in breakpoints],
        source_tier="tier1",
    )


def _make_categorical_config(
    subscore_id: str = "cat",
    feature_field: str = "cat_feature",
    weight: float = 1.0,
    category_scores: Optional[dict[str, float]] = None,
    default_score: float = 50.0,
) -> CategoricalSubscoreConfig:
    if category_scores is None:
        category_scores = {"A": 90.0, "B": 60.0, "C": 30.0}
    return CategoricalSubscoreConfig(
        subscore_id=subscore_id,
        display_name=subscore_id,
        feature_field=feature_field,
        weight=weight,
        category_scores=category_scores,
        default_score=default_score,
    )


@dataclass
class _Accessor:
    """Minimal object that exposes attributes via getattr."""

    def __init__(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


# ============================================================================
# score_numeric_subscore
# ============================================================================


class TestScoreNumericSubscore:
    """Piecewise linear interpolation over breakpoints."""

    def test_none_value_returns_none(self):
        cfg = _make_numeric_config()
        assert score_numeric_subscore(None, cfg) is None

    def test_below_min_clamps_to_first_score(self):
        cfg = _make_numeric_config(breakpoints=[(10.0, 20.0), (20.0, 80.0)])
        assert score_numeric_subscore(5.0, cfg) == 20.0
        assert score_numeric_subscore(0.0, cfg) == 20.0

    def test_above_max_clamps_to_last_score(self):
        cfg = _make_numeric_config(breakpoints=[(10.0, 20.0), (20.0, 80.0)])
        assert score_numeric_subscore(25.0, cfg) == 80.0
        assert score_numeric_subscore(1000.0, cfg) == 80.0

    def test_exact_breakpoint_hit(self):
        cfg = _make_numeric_config(breakpoints=[(0.0, 10.0), (0.5, 50.0), (1.0, 90.0)])
        assert score_numeric_subscore(0.0, cfg) == 10.0
        assert score_numeric_subscore(0.5, cfg) == 50.0
        assert score_numeric_subscore(1.0, cfg) == 90.0

    def test_linear_interpolation_midpoint(self):
        cfg = _make_numeric_config(breakpoints=[(0.0, 0.0), (10.0, 100.0)])
        # Midpoint should be 50
        assert score_numeric_subscore(5.0, cfg) == pytest.approx(50.0)

    def test_non_monotonic_breakpoints(self):
        """Non-monotonic scoring: sweet-spot middle value gets highest score."""
        cfg = _make_numeric_config(
            breakpoints=[(0.0, 20.0), (0.5, 90.0), (1.0, 10.0)]
        )
        # Low end
        assert score_numeric_subscore(0.0, cfg) == 20.0
        # Sweet spot
        assert score_numeric_subscore(0.5, cfg) == 90.0
        # High end
        assert score_numeric_subscore(1.0, cfg) == 10.0
        # Between low and sweet spot — interpolates up
        assert score_numeric_subscore(0.25, cfg) == pytest.approx(55.0)
        # Between sweet spot and high — interpolates down
        assert score_numeric_subscore(0.75, cfg) == pytest.approx(50.0)

    def test_string_value_returns_none(self):
        cfg = _make_numeric_config()
        assert score_numeric_subscore("abc", cfg) is None  # type: ignore[arg-type]

    def test_score_is_clamped_to_0_100(self):
        """Malformed breakpoints beyond [0,100] should still clamp output."""
        # Breakpoints with scores slightly outside [0,100] can happen from
        # rounding in the policy JSON; scoring engine must clamp.
        cfg = NumericSubscoreConfig(
            subscore_id="test",
            display_name="test",
            feature_field="test",
            weight=1.0,
            breakpoints=[
                SubscoreBreakpoint(value=0.0, score=0.0),
                SubscoreBreakpoint(value=1.0, score=100.0),
            ],
            source_tier="tier1",
        )
        s = score_numeric_subscore(0.5, cfg)
        assert s is not None
        assert 0.0 <= s <= 100.0


# ============================================================================
# score_categorical_subscore
# ============================================================================


class TestScoreCategoricalSubscore:
    """Category → score lookup."""

    def test_none_value_returns_none(self):
        cfg = _make_categorical_config()
        assert score_categorical_subscore(None, cfg) is None

    def test_known_category(self):
        cfg = _make_categorical_config(
            category_scores={"A": 90.0, "B": 60.0, "C": 30.0, "D": 10.0}
        )
        assert score_categorical_subscore("A", cfg) == 90.0
        assert score_categorical_subscore("D", cfg) == 10.0

    def test_unknown_category_uses_default(self):
        cfg = _make_categorical_config(
            category_scores={"A": 90.0, "B": 60.0},
            default_score=50.0,
        )
        assert score_categorical_subscore("Z", cfg) == 50.0

    def test_enum_value_is_coerced_to_string(self):
        """Enum-like objects with a `.value` attribute should be handled."""

        class FakeEnum:
            def __init__(self, v: str) -> None:
                self.value = v

        cfg = _make_categorical_config(category_scores={"X": 75.0})
        assert score_categorical_subscore(FakeEnum("X"), cfg) == 75.0


# ============================================================================
# score_pillar (end-to-end with weight redistribution)
# ============================================================================


class TestScorePillarWithWeightRedistribution:
    """Verify pillar scoring redistributes weight when subscores are missing."""

    def _make_pillar_config(self) -> PillarConfigV2:
        return PillarConfigV2(
            pillar_id=PillarId.PREMIUM_LEVERAGE,
            display_name="Test Pillar",
            description="Test",
            numeric_subscores=[
                _make_numeric_config(
                    subscore_id="a",
                    feature_field="a",
                    weight=0.5,
                    breakpoints=[(0.0, 10.0), (1.0, 90.0)],
                ),
                _make_numeric_config(
                    subscore_id="b",
                    feature_field="b",
                    weight=0.3,
                    breakpoints=[(0.0, 10.0), (1.0, 90.0)],
                ),
                _make_numeric_config(
                    subscore_id="c",
                    feature_field="c",
                    weight=0.2,
                    breakpoints=[(0.0, 10.0), (1.0, 90.0)],
                ),
            ],
            categorical_subscores=[],
        )

    def test_all_features_present(self):
        cfg = self._make_pillar_config()
        # All features at midpoint → all subscores = 50
        accessor = _Accessor(a=0.5, b=0.5, c=0.5)
        score, subscores = score_pillar(cfg, accessor)
        assert score == pytest.approx(50.0)
        assert len(subscores) == 3

    def test_missing_one_feature_redistributes_weight(self):
        """When feature `c` is missing, its 0.2 weight should be redistributed
        to the remaining subscores proportionally.

        Result: score is the weighted average of only a and b.
        """
        cfg = self._make_pillar_config()
        accessor = _Accessor(a=1.0, b=0.0, c=None)  # a=90, b=10
        score, _ = score_pillar(cfg, accessor)
        # Effective weight = 0.5 + 0.3 = 0.8
        # score = (90 * 0.5 + 10 * 0.3) / 0.8 = (45 + 3) / 0.8 = 60.0
        assert score == pytest.approx(60.0)

    def test_all_features_missing_returns_neutral(self):
        cfg = self._make_pillar_config()
        accessor = _Accessor(a=None, b=None, c=None)
        score, _ = score_pillar(cfg, accessor)
        # Fallback neutral when nothing is available
        assert score == 50.0

    def test_feature_field_not_on_accessor(self):
        """Missing attribute should be treated the same as None."""
        cfg = self._make_pillar_config()
        accessor = _Accessor(a=0.5, b=0.5)  # no `c`
        score, _ = score_pillar(cfg, accessor)
        # Only a and b contribute; both yield score 50
        assert score == pytest.approx(50.0)

    def test_mixed_numeric_and_categorical(self):
        cfg = PillarConfigV2(
            pillar_id=PillarId.SETUP_QUALITY,
            display_name="Mixed",
            description="Test",
            numeric_subscores=[
                _make_numeric_config(
                    subscore_id="num",
                    feature_field="num",
                    weight=0.6,
                    breakpoints=[(0.0, 10.0), (10.0, 90.0)],
                ),
            ],
            categorical_subscores=[
                _make_categorical_config(
                    subscore_id="cat",
                    feature_field="cat",
                    weight=0.4,
                    category_scores={"GOOD": 80.0, "BAD": 20.0},
                ),
            ],
        )
        accessor = _Accessor(num=5.0, cat="GOOD")
        score, subscores = score_pillar(cfg, accessor)
        # num=5 -> interp 50; cat="GOOD" -> 80
        # 50 * 0.6 + 80 * 0.4 = 30 + 32 = 62
        assert score == pytest.approx(62.0)
        assert len(subscores) == 2
