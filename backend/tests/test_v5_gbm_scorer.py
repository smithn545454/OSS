"""Tests for v5 GBM co-scorer (non-archetype fallback probability scorer).

The scorer is logistic regression + isotonic calibration behind the
`gbm_` naming convention. Tests cover:
  * Real bundled models (HR + P) load and return sensible scores
  * Inference handles missing features gracefully (mean imputation)
  * Isotonic interp matches sklearn's clip-out-of-bounds behavior
  * Scores clamped to [0, 100]
  * Idempotent module-level load + explicit reload
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from app.v5 import gbm_scorer
from app.v5.gbm_scorer import (
    FEATURE_NAMES,
    GBMModel,
    GBMScoreResult,
    _isotonic_interp,
    build_feature_dict_from_context,
    reload_models,
    score_trade,
)

# ============================================================================
# Helpers
# ============================================================================


def _fixture_linear_model(target: str, strong_feature: str = "abs_delta") -> dict:
    """Build a minimal fixture model that's predictable: P increases with
    ``strong_feature``, others neutral. Used in tests that don't need real
    statistics — they verify the math and ergonomics of the scorer.
    """
    feature_names = list(FEATURE_NAMES)
    k = len(feature_names)
    idx = feature_names.index(strong_feature)
    coef = [0.0] * k
    coef[idx] = 2.0  # Strong positive coefficient on this feature
    return {
        "target": target,
        "feature_names": feature_names,
        "feature_means": [0.0] * k,
        "feature_stds": [1.0] * k,
        "intercept": 0.0,
        "coef": coef,
        "n_train": 1000,
        "n_holdout": 200,
        "auc_calibrated": 0.7,
        "calibration": [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]],  # Identity
        "version": "v5.0.0-fixture",
        "algorithm": "logistic_regression_l2_isotonic",
    }


def _install_fixture_models(tmp_dir: Path, monkeypatch) -> None:
    """Patch gbm_scorer to load fixture models from tmp_dir."""
    hr_path = tmp_dir / "v5_gbm_hr.json"
    p_path = tmp_dir / "v5_gbm_p.json"
    with open(hr_path, "w") as fp:
        json.dump(_fixture_linear_model("hr200"), fp)
    with open(p_path, "w") as fp:
        json.dump(_fixture_linear_model("profit"), fp)

    def _fake_model_path(target: str) -> Path:
        return hr_path if target == "hr" else p_path

    monkeypatch.setattr(gbm_scorer, "_model_path", _fake_model_path)
    reload_models()


# ============================================================================
# Isotonic interpolation
# ============================================================================


class TestIsotonicInterp:
    def test_empty_table_clips(self) -> None:
        assert _isotonic_interp(0.3, [], []) == 0.3
        assert _isotonic_interp(-1.0, [], []) == 0.0
        assert _isotonic_interp(5.0, [], []) == 1.0

    def test_single_point(self) -> None:
        # Single point, any x returns that y
        assert _isotonic_interp(0.5, [0.3], [0.8]) == 0.8
        assert _isotonic_interp(0.1, [0.3], [0.8]) == 0.8
        assert _isotonic_interp(0.9, [0.3], [0.8]) == 0.8

    def test_two_points_linear(self) -> None:
        # Linear interp between (0, 0) and (1, 1) → identity
        assert math.isclose(_isotonic_interp(0.5, [0.0, 1.0], [0.0, 1.0]), 0.5)
        assert math.isclose(_isotonic_interp(0.25, [0.0, 1.0], [0.0, 1.0]), 0.25)

    def test_clip_below(self) -> None:
        # Below range → clip to first y
        assert _isotonic_interp(-0.5, [0.0, 1.0], [0.1, 0.9]) == 0.1

    def test_clip_above(self) -> None:
        # Above range → clip to last y
        assert _isotonic_interp(2.0, [0.0, 1.0], [0.1, 0.9]) == 0.9

    def test_monotone_step_up(self) -> None:
        # Isotonic typically has step-like shape — verify interp is sane
        xs = [0.0, 0.2, 0.5, 0.8, 1.0]
        ys = [0.05, 0.1, 0.4, 0.75, 0.9]
        # At exact xs points
        assert _isotonic_interp(0.2, xs, ys) == 0.1
        assert _isotonic_interp(0.5, xs, ys) == 0.4
        # Midway between 0.5 and 0.8 → midway of 0.4 and 0.75
        mid = _isotonic_interp(0.65, xs, ys)
        assert math.isclose(mid, (0.4 + 0.75) / 2, abs_tol=0.001)


# ============================================================================
# GBMModel.predict_probability
# ============================================================================


class TestGBMModelPredict:
    def _build(self, target: str = "hr200", strong_feature: str = "abs_delta") -> GBMModel:
        d = _fixture_linear_model(target, strong_feature)
        return GBMModel(
            target=d["target"],
            feature_names=d["feature_names"],
            feature_means=d["feature_means"],
            feature_stds=d["feature_stds"],
            intercept=d["intercept"],
            coef=d["coef"],
            calibration_xs=[p[0] for p in d["calibration"]],
            calibration_ys=[p[1] for p in d["calibration"]],
            auc_calibrated=d["auc_calibrated"],
            n_train=d["n_train"],
            version=d["version"],
        )

    def test_zero_features_yields_half(self) -> None:
        model = self._build()
        # All zeros → sigmoid(0) = 0.5
        p = model.predict_probability({})
        assert math.isclose(p, 0.5, abs_tol=0.01)

    def test_strong_feature_pushes_probability_up(self) -> None:
        model = self._build(strong_feature="abs_delta")
        # abs_delta=3 × coef=2 = z=6 → sigmoid(6) ≈ 0.998
        p = model.predict_probability({"abs_delta": 3.0})
        assert p > 0.9

    def test_strong_feature_pushes_probability_down_when_negative(self) -> None:
        model = self._build(strong_feature="abs_delta")
        p = model.predict_probability({"abs_delta": -3.0})
        assert p < 0.1

    def test_missing_features_use_mean(self) -> None:
        # With means=0, stds=1, missing features contribute 0 to z
        model = self._build()
        # Empty dict is entirely imputed to zeros
        p_empty = model.predict_probability({})
        p_partial = model.predict_probability({"entry_delta": 0.0})
        assert math.isclose(p_empty, p_partial, abs_tol=0.001)

    def test_overflow_handled(self) -> None:
        # Huge z should not crash; clamped via try/except
        d = _fixture_linear_model("hr200")
        d["coef"] = [1000.0] * len(FEATURE_NAMES)
        model = GBMModel(
            target=d["target"],
            feature_names=d["feature_names"],
            feature_means=d["feature_means"],
            feature_stds=d["feature_stds"],
            intercept=d["intercept"],
            coef=d["coef"],
            calibration_xs=[p[0] for p in d["calibration"]],
            calibration_ys=[p[1] for p in d["calibration"]],
            auc_calibrated=d["auc_calibrated"],
            n_train=d["n_train"],
            version=d["version"],
        )
        # With coef=1000 for 20 features at 1.0 each, z is huge
        p = model.predict_probability({f: 1.0 for f in FEATURE_NAMES})
        assert p == 1.0


# ============================================================================
# score_trade — module-level facade
# ============================================================================


class TestScoreTrade:
    def test_real_bundled_models_load_and_score(self) -> None:
        """The real trained models must load from app/v5/models/ and produce
        reasonable scores for a canonical UV_LOTTERY_CALL-like trade."""
        reload_models()  # Pick up the real models in the package
        ctx_features = {
            "entry_delta": 0.20,
            "abs_delta": 0.20,
            "dte_at_entry": 18,
            "entry_iv": 0.45,
            "entry_iv_percentile": 25,
            "entry_iv_rv_ratio": 0.9,
            "adx_14": 18,
            "plus_di": 25,
            "minus_di": 15,
            "rs_20d": 1.02,
            "atr14_pct": 5.0,
            "theta_pct": None,
            "pillar_dc": 60,
            "pillar_mp": 55,
            "pillar_ts": 80,
            "scanner_is_uv": 1.0,
            "scanner_is_cheap": 0.0,
            "scanner_is_breakdown": 0.0,
            "scanner_is_revalidation": 0.0,
            "option_is_call": 1.0,
        }
        result = score_trade(ctx_features)
        assert isinstance(result, GBMScoreResult)
        # Models should be available when bundled correctly
        assert result.hr_available
        assert result.p_available
        assert 0.0 <= result.hr_score <= 100.0
        assert 0.0 <= result.p_score <= 100.0

    def test_missing_models_return_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When model files don't exist, scorer returns 0 scores + available=False."""
        def _no_model(target: str) -> Path:
            return tmp_path / f"missing_{target}.json"

        monkeypatch.setattr(gbm_scorer, "_model_path", _no_model)
        reload_models()
        result = score_trade({})
        assert result.hr_score == 0.0
        assert result.p_score == 0.0
        assert not result.hr_available
        assert not result.p_available

    def test_fixture_models_produce_expected_direction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With fixture models (coef=+2 on abs_delta, calibration=identity),
        a trade with abs_delta=3 should score near 99.8 on both axes."""
        _install_fixture_models(tmp_path, monkeypatch)
        result = score_trade({"abs_delta": 3.0})
        assert result.hr_score > 90.0
        assert result.p_score > 90.0

    def test_scores_clamped_to_100(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even with extreme inputs, scores must stay in [0, 100]."""
        _install_fixture_models(tmp_path, monkeypatch)
        result = score_trade({"abs_delta": 1e10})  # Absurd input
        assert 0.0 <= result.hr_score <= 100.0
        assert 0.0 <= result.p_score <= 100.0


class TestBuildFeatureDictFromContext:
    def test_from_minimal_context(self) -> None:
        # Use a simple namespace as a stand-in for ScoringContext
        class _Ctx:
            scanner_source = "UNUSUAL_VOLUME"
            option_type = "CALL"
            delta = 0.22
            dte = 18
            iv = 0.5
            iv_percentile = None
            iv_rv_ratio = None
            adx_14 = 22.0
            plus_di = None
            minus_di = None
            rs_20d = None
            atr14_pct = None
            theta_pct = None

        features = build_feature_dict_from_context(
            _Ctx(), pillar_scores={"DC": 70.0, "MP": 50.0, "TS": 80.0},
        )
        assert features["entry_delta"] == 0.22
        assert features["abs_delta"] == 0.22
        assert features["dte_at_entry"] == 18
        assert features["scanner_is_uv"] == 1.0
        assert features["scanner_is_cheap"] == 0.0
        assert features["scanner_is_breakdown"] == 0.0
        assert features["option_is_call"] == 1.0
        assert features["pillar_dc"] == 70.0
        assert features["pillar_mp"] == 50.0
        assert features["pillar_ts"] == 80.0
        # Missing context attrs should be None (not KeyError)
        assert features["plus_di"] is None

    def test_put_and_cheap(self) -> None:
        class _Ctx:
            scanner_source = "CHEAP_OPTIONS"
            option_type = "PUT"
            delta = -0.35
            dte = 30
            iv = 0.55
            iv_percentile = 40
            iv_rv_ratio = None
            adx_14 = None
            plus_di = None
            minus_di = None
            rs_20d = None
            atr14_pct = None
            theta_pct = None

        features = build_feature_dict_from_context(_Ctx())
        assert features["scanner_is_uv"] == 0.0
        assert features["scanner_is_cheap"] == 1.0
        assert features["option_is_call"] == 0.0
        assert features["entry_delta"] == -0.35
        assert features["abs_delta"] == 0.35


# ============================================================================
# Real-model sanity checks
# ============================================================================


class TestRealModelsSanity:
    """Asserts the real trained models exist and meet minimum quality bars."""

    def _load_real_model(self, target: str) -> dict[str, Any]:
        path = (
            Path(__file__).parent.parent
            / "app" / "v5" / "models" / f"v5_gbm_{target}.json"
        )
        assert path.exists(), f"Real v5 GBM {target} model missing at {path}"
        with open(path) as fp:
            return json.load(fp)

    def test_hr_model_meets_auc_bar(self) -> None:
        m = self._load_real_model("hr")
        assert m["auc_calibrated"] >= 0.60, (
            f"HR GBM AUC={m['auc_calibrated']:.4f} below 0.60 floor"
        )

    def test_hr_model_has_20_features(self) -> None:
        m = self._load_real_model("hr")
        assert len(m["feature_names"]) == 20
        assert len(m["coef"]) == 20

    def test_p_model_exists(self) -> None:
        # P model is weak (AUC ~0.50) — we still ship it but Phase 5
        # may set v5_gbm_p_weight=0 in policy until a better model trains
        m = self._load_real_model("p")
        assert m["target"] == "profit"
        assert len(m["coef"]) == 20

    def test_both_models_use_same_features(self) -> None:
        hr = self._load_real_model("hr")
        p = self._load_real_model("p")
        assert hr["feature_names"] == p["feature_names"] == FEATURE_NAMES
