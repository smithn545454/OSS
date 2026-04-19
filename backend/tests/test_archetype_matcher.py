"""Unit tests for the v4.1.0 archetype matcher."""

from __future__ import annotations

from typing import Any

import pytest

from app.archetypes.defaults import default_archetypes
from app.archetypes.matcher import _evaluate_condition, compute_archetype_match
from app.core.schemas import (
    ArchetypeCondition,
    ArchetypeConfig,
    ArchetypeDefinition,
)
from app.pillars.models import ScoringContext


def _ctx(**overrides: Any) -> ScoringContext:
    defaults: dict[str, Any] = dict(
        evaluation_id="eval-1",
        underlying_ticker="TEST",
        option_type="CALL",
        dte_bucket="B",
        dte=17,
        delta=0.18,
        scanner_source="UNUSUAL_VOLUME",
    )
    defaults.update(overrides)
    return ScoringContext(**defaults)


def _single_archetype(
    conditions: list[ArchetypeCondition],
    *,
    min_fit: float = 75.0,
) -> ArchetypeConfig:
    return ArchetypeConfig(
        archetypes=[
            ArchetypeDefinition(
                archetype_id="TEST",
                display_name="Test",
                description="test",
                conditions=conditions,
                historical_n=100,
                historical_hr200_rate=0.1,
                historical_win_rate=0.5,
                historical_mean_pnl_pct=10.0,
                min_fit_to_match=min_fit,
            )
        ]
    )


class TestArchetypeA:
    """Archetype A — UV Lottery Call."""

    def _config(self) -> ArchetypeConfig:
        return ArchetypeConfig(
            archetypes=[default_archetypes().archetypes[0]]
        )

    def test_perfect_match(self) -> None:
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME", dte=17, delta=0.18, option_type="CALL"
        )
        result = compute_archetype_match(ctx, self._config())
        assert result.best is not None
        assert result.best.archetype_id == "UV_LOTTERY_CALL"
        assert result.best.fit == pytest.approx(100.0)
        assert result.best_match_score == pytest.approx(100.0)

    def test_edge_of_feather(self) -> None:
        # DTE=23 is 2 past hi=21 with feather=3 → fit ≈ 33
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME", dte=23, delta=0.18, option_type="CALL"
        )
        result = compute_archetype_match(ctx, self._config())
        # min-fit = dte_14_21 condition fit
        fit = result.all_fits["UV_LOTTERY_CALL"]
        assert 30.0 <= fit <= 40.0
        # Below threshold (75) → not matched
        assert result.best is None

    def test_outside_feather(self) -> None:
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME", dte=25, delta=0.18, option_type="CALL"
        )
        result = compute_archetype_match(ctx, self._config())
        assert result.all_fits["UV_LOTTERY_CALL"] == pytest.approx(0.0)
        assert result.best is None


class TestMissingFeatures:
    def test_missing_required_fails(self) -> None:
        config = _single_archetype(
            [
                ArchetypeCondition(
                    condition_id="adx_low",
                    display_name="ADX < 20",
                    feature_field="adx_14",
                    lte=20.0,
                    feather=3.0,
                    required=True,
                )
            ]
        )
        ctx = _ctx(adx_14=None)
        result = compute_archetype_match(ctx, config)
        assert result.all_fits["TEST"] == 0.0

    def test_missing_optional_neutral(self) -> None:
        config = _single_archetype(
            [
                ArchetypeCondition(
                    condition_id="adx_low",
                    display_name="ADX < 20",
                    feature_field="adx_14",
                    lte=20.0,
                    feather=3.0,
                    required=False,
                )
            ],
            min_fit=40.0,
        )
        ctx = _ctx(adx_14=None)
        result = compute_archetype_match(ctx, config)
        assert result.all_fits["TEST"] == 50.0
        assert result.best is not None


class TestMultiMatch:
    def test_best_match_selected(self) -> None:
        # Two archetypes — one matches with fit 100, one with fit 80.
        config = ArchetypeConfig(
            archetypes=[
                ArchetypeDefinition(
                    archetype_id="A",
                    display_name="A",
                    description="",
                    conditions=[
                        ArchetypeCondition(
                            condition_id="dte",
                            display_name="DTE",
                            feature_field="dte",
                            between=[14.0, 21.0],
                            feather=5.0,
                        )
                    ],
                    historical_n=1,
                    historical_hr200_rate=0,
                    historical_win_rate=0,
                    historical_mean_pnl_pct=0,
                ),
                ArchetypeDefinition(
                    archetype_id="B",
                    display_name="B",
                    description="",
                    conditions=[
                        ArchetypeCondition(
                            condition_id="dte",
                            display_name="DTE",
                            feature_field="dte",
                            between=[17.0, 17.0],
                            feather=3.0,
                        )
                    ],
                    historical_n=1,
                    historical_hr200_rate=0,
                    historical_win_rate=0,
                    historical_mean_pnl_pct=0,
                ),
            ]
        )
        # dte=17: A fits 100 (within [14,21]), B fits 100 (exact).
        # Make dte=16 so A still 100 but B fits (3-1)/3 = 66.7 → below threshold.
        ctx = _ctx(dte=19)
        result = compute_archetype_match(ctx, config)
        # B: dte=19 → 19 outside [17,17], offset=2, feather=3 → fit=33.
        # A: within [14,21] → fit=100
        assert result.best is not None
        assert result.best.archetype_id == "A"


class TestBelowThreshold:
    def test_below_min_fit_not_matched(self) -> None:
        config = _single_archetype(
            [
                ArchetypeCondition(
                    condition_id="dte",
                    display_name="DTE",
                    feature_field="dte",
                    between=[14.0, 21.0],
                    feather=10.0,
                )
            ],
            min_fit=75.0,
        )
        # dte=27: 6 past hi=21 with feather=10 → fit=40
        ctx = _ctx(dte=27)
        result = compute_archetype_match(ctx, config)
        assert result.all_fits["TEST"] == pytest.approx(40.0, abs=1.0)
        assert result.best is None


class TestDiscreteEq:
    def test_mismatched_scanner_zero(self) -> None:
        config = _single_archetype(
            [
                ArchetypeCondition(
                    condition_id="scanner",
                    display_name="Scanner",
                    feature_field="scanner_source",
                    eq="UNUSUAL_VOLUME",
                )
            ]
        )
        ctx = _ctx(scanner_source="BREAKOUT")
        result = compute_archetype_match(ctx, config)
        assert result.all_fits["TEST"] == 0.0


class TestDerivedFeatures:
    def test_rs_contrarian_call_with_negative_rs(self) -> None:
        cond = ArchetypeCondition(
            condition_id="rs_c",
            display_name="rs contrarian",
            feature_field="rs_contrarian",
            eq=1.0,
        )
        ctx = _ctx(option_type="CALL", rs_20d=-2.0)
        fit = _evaluate_condition(ctx, cond, {})
        assert fit.fit == 100.0

    def test_abs_delta_resolution(self) -> None:
        cond = ArchetypeCondition(
            condition_id="ad",
            display_name="abs delta",
            feature_field="abs_delta",
            lte=0.25,
        )
        ctx = _ctx(delta=-0.18)
        fit = _evaluate_condition(ctx, cond, {})
        assert fit.fit == 100.0

    def test_pillar_score_alias(self) -> None:
        cond = ArchetypeCondition(
            condition_id="ts",
            display_name="TS",
            feature_field="ts_score",
            gte=75.0,
        )
        ctx = _ctx()
        fit = _evaluate_condition(ctx, cond, {"TS": 80.0})
        assert fit.fit == 100.0


class TestConditionValidator:
    def test_rejects_multi_op(self) -> None:
        with pytest.raises(Exception):
            ArchetypeCondition(
                condition_id="bad",
                display_name="bad",
                feature_field="dte",
                between=[0.0, 1.0],
                lte=5.0,
            )

    def test_rejects_zero_op(self) -> None:
        with pytest.raises(Exception):
            ArchetypeCondition(
                condition_id="bad",
                display_name="bad",
                feature_field="dte",
            )

    def test_rejects_bad_between(self) -> None:
        with pytest.raises(Exception):
            ArchetypeCondition(
                condition_id="bad",
                display_name="bad",
                feature_field="dte",
                between=[1.0],
            )


class TestDefaults:
    def test_six_default_archetypes(self) -> None:
        config = default_archetypes()
        assert len(config.archetypes) == 6
        ids = {a.archetype_id for a in config.archetypes}
        assert ids == {
            "UV_LOTTERY_CALL",
            "UV_STRUCTURAL",
            "UV_REVERSAL_PUT",
            "CHEAP_COMPRESSION",
            "CHEAP_VOL_REVERSAL",
            "CHEAP_ULTRA_CALL",
        }

    def test_empty_result_when_no_config(self) -> None:
        """Calling with an empty archetypes list returns no best."""
        config = ArchetypeConfig(archetypes=[])
        result = compute_archetype_match(_ctx(), config)
        assert result.best is None
        assert result.best_match_score is None
        assert result.all_fits == {}
