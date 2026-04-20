"""v5 GBM co-scorer — non-archetype fallback probability scorer.

Serves the role the plan calls "GBM": scoring trades that don't match
any archetype, so the dual-conviction system isn't blind to non-
archetype patterns. The algorithm is logistic regression + isotonic
calibration (not gradient-boosted trees) because Lambda's 250 MB limit
makes shipping XGBoost impractical. The naming is preserved because the
ROLE (non-archetype fallback scorer) matters more than the algorithm.

Inference is pure Python + math module — no numpy, no sklearn, no scipy.
Model files are JSON (standardization + LR coefficients + isotonic
lookup table) produced by :mod:`backend.scripts.train_v5_gbm`.

Two models ship together:
  * HR model: predicts P(MFE ≥ 200%) — feeds into HR conviction
  * P model:  predicts P(profit)    — feeds into P conviction

The returned scores are calibrated probabilities × 100, in [0, 100].
Callers apply the plan's cap weights (HR=0.5, P=0.7) when combining
with archetype conviction:

    final_hr_conviction = max(archetype_hr, gbm_hr_score * 0.5)
    final_p_conviction  = max(archetype_p, gbm_p_score  * 0.7)

The caps prevent an over-confident model from dominating; archetypes
remain primary.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Feature order MUST match backend/scripts/train_v5_gbm.py FEATURE_NAMES
FEATURE_NAMES: list[str] = [
    "entry_delta",
    "abs_delta",
    "dte_at_entry",
    "entry_iv",
    "entry_iv_percentile",
    "entry_iv_rv_ratio",
    "adx_14",
    "plus_di",
    "minus_di",
    "rs_20d",
    "atr14_pct",
    "theta_pct",
    "pillar_dc",
    "pillar_mp",
    "pillar_ts",
    "scanner_is_uv",
    "scanner_is_cheap",
    "scanner_is_breakdown",
    "scanner_is_revalidation",
    "option_is_call",
]


@dataclass(frozen=True)
class GBMModel:
    """Loaded logistic-regression + isotonic-calibration model."""
    target: str                           # "hr200" or "profit"
    feature_names: list[str]
    feature_means: list[float]            # For mean-imputation of missing
    feature_stds: list[float]             # For standardization
    intercept: float
    coef: list[float]
    calibration_xs: list[float]           # Isotonic x-thresholds (monotonic)
    calibration_ys: list[float]           # Isotonic y-values
    auc_calibrated: float
    n_train: int
    version: str

    def predict_probability(self, feature_dict: dict[str, Any]) -> float:
        """Return calibrated P(target=1) in [0, 1]."""
        # 1. Extract features in canonical order, impute missing with mean
        x: list[float] = []
        for name, mean in zip(self.feature_names, self.feature_means):
            v = feature_dict.get(name)
            x.append(float(v) if v is not None else mean)

        # 2. Standardize: (x - mean) / std
        z_sum = self.intercept
        for xi, mean, std, coef in zip(
            x, self.feature_means, self.feature_stds, self.coef,
        ):
            if std <= 0:
                continue
            z_sum += coef * ((xi - mean) / std)

        # 3. Sigmoid
        try:
            p_raw = 1.0 / (1.0 + math.exp(-z_sum))
        except OverflowError:
            p_raw = 0.0 if z_sum < 0 else 1.0

        # 4. Isotonic calibration — piecewise-linear interp on (xs, ys)
        return _isotonic_interp(p_raw, self.calibration_xs, self.calibration_ys)


@dataclass(frozen=True)
class GBMScoreResult:
    """Result of scoring a trade against both GBM models."""
    hr_score: float               # P(HR200) * 100, in [0, 100]
    p_score: float                # P(profit) * 100, in [0, 100]
    hr_available: bool            # True if HR model loaded successfully
    p_available: bool             # True if P model loaded successfully


# Module-level model cache (loaded lazily at first use)
_MODELS: dict[str, Optional[GBMModel]] = {"hr": None, "p": None}
_MODEL_LOAD_ATTEMPTED: bool = False


def _model_path(target: str) -> Path:
    """Return filesystem path where Lambda deploy looks for the model.

    Convention: bundled under ``backend/app/v5/models/v5_gbm_<target>.json``
    so the model ships with the Lambda code artifact.
    """
    return Path(__file__).parent / "models" / f"v5_gbm_{target}.json"


def _load_model_from_path(path: Path) -> Optional[GBMModel]:
    """Load a model JSON. Returns None if file missing or parse failed."""
    if not path.exists():
        logger.warning("v5 GBM model not found at %s — scoring will return 0", path)
        return None
    try:
        with open(path) as fp:
            data = json.load(fp)
        xs = [float(p[0]) for p in data["calibration"]]
        ys = [float(p[1]) for p in data["calibration"]]
        return GBMModel(
            target=data["target"],
            feature_names=data["feature_names"],
            feature_means=[float(v) for v in data["feature_means"]],
            feature_stds=[float(v) for v in data["feature_stds"]],
            intercept=float(data["intercept"]),
            coef=[float(c) for c in data["coef"]],
            calibration_xs=xs,
            calibration_ys=ys,
            auc_calibrated=float(data.get("auc_calibrated", 0.0)),
            n_train=int(data.get("n_train", 0)),
            version=data.get("version", "unknown"),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to load v5 GBM model from %s: %s", path, e)
        return None


def _ensure_models_loaded() -> None:
    """Populate _MODELS on first call. Idempotent."""
    global _MODEL_LOAD_ATTEMPTED
    if _MODEL_LOAD_ATTEMPTED:
        return
    _MODEL_LOAD_ATTEMPTED = True
    _MODELS["hr"] = _load_model_from_path(_model_path("hr"))
    _MODELS["p"] = _load_model_from_path(_model_path("p"))
    loaded = [k for k, v in _MODELS.items() if v is not None]
    logger.info("v5 GBM models loaded: %s", loaded or "NONE")


def reload_models() -> None:
    """Force a reload (e.g., after a new model is written)."""
    global _MODEL_LOAD_ATTEMPTED
    _MODEL_LOAD_ATTEMPTED = False
    _MODELS["hr"] = None
    _MODELS["p"] = None
    _ensure_models_loaded()


def score_trade(feature_dict: dict[str, Any]) -> GBMScoreResult:
    """Score a trade against the HR and P GBM models.

    Args:
        feature_dict: Feature values keyed by the names in FEATURE_NAMES.
            Missing keys are mean-imputed inside predict_probability.

    Returns:
        :class:`GBMScoreResult`. When a model failed to load, the
        corresponding score is 0 and the *_available flag is False —
        callers should treat unavailable scores as missing data rather
        than evidence against the trade.
    """
    _ensure_models_loaded()
    hr_model = _MODELS.get("hr")
    p_model = _MODELS.get("p")

    hr_score = 0.0
    p_score = 0.0
    if hr_model is not None:
        try:
            hr_score = 100.0 * hr_model.predict_probability(feature_dict)
        except Exception:  # noqa: BLE001
            logger.exception("GBM HR scoring failed")
    if p_model is not None:
        try:
            p_score = 100.0 * p_model.predict_probability(feature_dict)
        except Exception:  # noqa: BLE001
            logger.exception("GBM P scoring failed")

    return GBMScoreResult(
        hr_score=max(0.0, min(100.0, hr_score)),
        p_score=max(0.0, min(100.0, p_score)),
        hr_available=hr_model is not None,
        p_available=p_model is not None,
    )


# ============================================================================
# Helpers
# ============================================================================


def _isotonic_interp(x: float, xs: list[float], ys: list[float]) -> float:
    """Piecewise-linear monotone interpolation at x.

    ``xs`` and ``ys`` are the isotonic regression thresholds from
    ``sklearn.isotonic.IsotonicRegression.f_``. Values outside the
    training range are clipped to the nearest endpoint (matches
    sklearn's out_of_bounds="clip").

    Implementation notes:
    - xs is weakly monotonic by construction (isotonic fit).
    - For exact matches or tiny cohorts we may have xs of length 1 or 2.
    - Uses bisect for O(log n) lookup.
    """
    n = len(xs)
    if n == 0:
        return max(0.0, min(1.0, x))
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    # Find the segment [xs[i], xs[i+1]] containing x
    lo, hi = 0, n - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    x_lo, x_hi = xs[lo], xs[hi]
    y_lo, y_hi = ys[lo], ys[hi]
    if x_hi == x_lo:
        return y_hi
    t = (x - x_lo) / (x_hi - x_lo)
    return y_lo + t * (y_hi - y_lo)


def build_feature_dict_from_context(
    ctx: Any,
    pillar_scores: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Construct a feature dict from a ScoringContext + optional pillar scores.

    Callers that have a :class:`app.pillars.models.ScoringContext` can pass
    it directly here instead of manually wiring features. Pillar scores
    can be passed separately (as computed by the pillar scoring engine)
    to fill ``pillar_dc`` / ``pillar_mp`` / ``pillar_ts``.

    This function does NOT import ScoringContext — uses getattr for
    loose coupling so the scorer stays dependency-light.
    """
    scanner = str(getattr(ctx, "scanner_source", "") or "").upper()
    option_type = str(getattr(ctx, "option_type", "") or "").upper()
    delta = getattr(ctx, "delta", None)
    ps = pillar_scores or {}

    return {
        "entry_delta": delta,
        "abs_delta": abs(delta) if delta is not None else None,
        "dte_at_entry": getattr(ctx, "dte", None),
        "entry_iv": getattr(ctx, "iv", None),
        "entry_iv_percentile": getattr(ctx, "iv_percentile", None),
        "entry_iv_rv_ratio": getattr(ctx, "iv_rv_ratio", None),
        "adx_14": getattr(ctx, "adx_14", None),
        "plus_di": getattr(ctx, "plus_di", None),
        "minus_di": getattr(ctx, "minus_di", None),
        "rs_20d": getattr(ctx, "rs_20d", None),
        "atr14_pct": getattr(ctx, "atr14_pct", None),
        "theta_pct": getattr(ctx, "theta_pct", None),
        "pillar_dc": ps.get("DC"),
        "pillar_mp": ps.get("MP"),
        "pillar_ts": ps.get("TS"),
        "scanner_is_uv": 1.0 if scanner == "UNUSUAL_VOLUME" else 0.0,
        "scanner_is_cheap": 1.0 if scanner == "CHEAP_OPTIONS" else 0.0,
        "scanner_is_breakdown": 1.0 if scanner == "BREAKDOWN" else 0.0,
        "scanner_is_revalidation": 1.0 if scanner == "REVALIDATION" else 0.0,
        "option_is_call": 1.0 if option_type == "CALL" else 0.0,
    }
