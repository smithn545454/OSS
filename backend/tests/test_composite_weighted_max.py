"""Unit tests for the v4.1.0 weighted_max composite formula."""

from __future__ import annotations

import pytest

from app.core.schemas import PillarConfig, PillarId, PillarWeights
from app.pillars.composite import (
    compute_composite_score,
    compute_weighted_max,
    weighted_geometric_mean,
)
from app.pillars.models import PillarResult


def _r(pillar_id: PillarId, score: float) -> PillarResult:
    return PillarResult(pillar_id=pillar_id, evaluation_id="eval-1", score=score)


def _v4_weights() -> PillarWeights:
    return PillarWeights.v4_default()


def _results(dc: float, mp: float, ts: float) -> list[PillarResult]:
    return [
        _r(PillarId.DIRECTIONAL_CONVICTION, dc),
        _r(PillarId.MOVE_POTENTIAL, mp),
        _r(PillarId.TRADE_STRUCTURE, ts),
    ]


class TestWeightedMax:
    def test_strong_ts_middling_dc_mp_surfaces(self) -> None:
        # Historical grand-slam profile: one dominant pillar.
        score = compute_weighted_max(_results(51.0, 52.0, 76.0), _v4_weights())
        assert 65 <= score <= 72

    def test_geo_mean_same_inputs_underperforms_for_asymmetric(self) -> None:
        weights = _v4_weights()
        results = _results(51.0, 52.0, 76.0)
        wmax = compute_weighted_max(results, weights)
        gmean = weighted_geometric_mean(results, weights)
        assert wmax > gmean + 5

    def test_balanced_high_pillars_all_formulas_similar(self) -> None:
        weights = _v4_weights()
        results = _results(85.0, 85.0, 85.0)
        wmax = compute_weighted_max(results, weights)
        assert 82 <= wmax <= 88

    def test_soft_floor_penalty_fires_below_25(self) -> None:
        weights = _v4_weights()
        # Pillar at 20 triggers the 0.7× multiplier.
        penalized = compute_weighted_max(_results(20.0, 80.0, 80.0), weights)
        # Same inputs but nudged to 30 does not trigger the penalty.
        clean = compute_weighted_max(_results(30.0, 80.0, 80.0), weights)
        assert penalized < clean
        # And the gap is roughly the 0.7× factor (within tolerance).
        assert penalized < clean * 0.9

    def test_zero_pillar_no_longer_collapses(self) -> None:
        # Unlike the geometric mean, a zero pillar does not zero the composite.
        score = compute_weighted_max(_results(0.0, 80.0, 80.0), _v4_weights())
        assert score > 10

    def test_clamped_to_0_100(self) -> None:
        score = compute_weighted_max(_results(100.0, 100.0, 100.0), _v4_weights())
        assert score == pytest.approx(100.0, rel=1e-6)

    def test_empty_results_returns_zero(self) -> None:
        assert compute_weighted_max([], _v4_weights()) == 0.0


class TestDispatch:
    def _cfg(self, formula: str) -> PillarConfig:
        from tests.test_composite import _v4_pillar_config_full

        cfg = _v4_pillar_config_full()
        return cfg.model_copy(update={"composite_formula": formula})

    def test_weighted_max_dispatches(self) -> None:
        cfg = self._cfg("weighted_max")
        score = compute_composite_score(_results(51.0, 52.0, 76.0), cfg)
        expected = compute_weighted_max(_results(51.0, 52.0, 76.0), cfg.weights)
        assert score == pytest.approx(expected, rel=1e-6)


class TestSchemaValidator:
    def test_v4_accepts_weighted_max(self) -> None:
        from tests.test_composite import _v4_pillar_config_full

        cfg = _v4_pillar_config_full().model_copy(
            update={"composite_formula": "weighted_max"}
        )
        # Re-validate through the model to hit _validate_regime_consistency.
        PillarConfig.model_validate(cfg.model_dump())

    def test_v3_rejects_weighted_max(self) -> None:
        cfg_v3 = PillarConfig.v3_default()
        payload = cfg_v3.model_dump()
        payload["composite_formula"] = "weighted_max"
        with pytest.raises(Exception):
            PillarConfig.model_validate(payload)
