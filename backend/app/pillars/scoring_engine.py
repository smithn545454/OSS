"""Shared scoring engine for Policy v3.0.0 pillar calculators.

This module contains the core scoring primitives used by all three pillars
(Premium Leverage, Underlying Behavior, Setup Quality):

- `score_numeric_subscore`: Piecewise linear interpolation over empirically
  derived (value, score) breakpoints. Handles non-monotonic features
  correctly because the breakpoint scores reflect actual quintile outcomes
  rather than quintile rank.

- `score_categorical_subscore`: Direct lookup in a category -> score map.

- `compute_pillar_score`: Weighted average of subscore results with
  proportional weight redistribution when individual subscores are
  unavailable (e.g. ADX is only available on ~2.5% of historical positions,
  so Pillar 2 must still produce a valid score without it).

All functions are pure — no side effects, no I/O — so they can be tested
and reused anywhere that has a `FeatureSet`/attribute-style context and a
`PillarConfigV2`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core.schemas import (
    CategoricalSubscoreConfig,
    NumericSubscoreConfig,
    PillarConfigV2,
)
from app.pillars.models import Subscore

logger = logging.getLogger(__name__)


# ============================================================================
# Numeric subscore scoring (piecewise linear interpolation)
# ============================================================================


def score_numeric_subscore(
    value: Optional[float],
    config: NumericSubscoreConfig,
) -> Optional[float]:
    """Score a numeric feature value using the subscore's breakpoints.

    Uses piecewise linear interpolation between breakpoints:
    - Below the lowest breakpoint value: clamp to that breakpoint's score
    - Above the highest breakpoint value: clamp to that breakpoint's score
    - Between two breakpoints: linear interpolation

    Returns None when the feature value is None, so the caller can
    redistribute weight to available subscores.

    Args:
        value: Raw feature value, or None if unavailable.
        config: Subscore configuration with breakpoints and weight.

    Returns:
        Score in [0, 100], or None if `value` is None.
    """
    if value is None:
        return None

    try:
        v = float(value)
    except (TypeError, ValueError):
        logger.debug("Non-numeric value for %s: %r", config.subscore_id, value)
        return None

    bps = sorted(config.breakpoints, key=lambda b: b.value)

    # Clamp below-minimum
    if v <= bps[0].value:
        return _clamp(bps[0].score)
    # Clamp above-maximum
    if v >= bps[-1].value:
        return _clamp(bps[-1].score)

    # Linear interpolation between adjacent breakpoints
    for i in range(len(bps) - 1):
        b0, b1 = bps[i], bps[i + 1]
        if b0.value <= v <= b1.value:
            if abs(b1.value - b0.value) < 1e-15:
                # Duplicate breakpoint values (rare) — use the first score
                return _clamp(b0.score)
            t = (v - b0.value) / (b1.value - b0.value)
            return _clamp(b0.score + t * (b1.score - b0.score))

    # Unreachable by construction (value is in-range and not at the ends),
    # but return a neutral score to avoid crashes if breakpoints are malformed.
    logger.warning(
        "score_numeric_subscore: value %s fell outside breakpoint segments for %s",
        v,
        config.subscore_id,
    )
    return 50.0


# ============================================================================
# Categorical subscore scoring
# ============================================================================


def score_categorical_subscore(
    value: Optional[Any],
    config: CategoricalSubscoreConfig,
) -> Optional[float]:
    """Score a categorical feature value using the category -> score map.

    Args:
        value: Raw feature value. Coerced to str for lookup.
        config: Subscore configuration with category_scores and default_score.

    Returns:
        Score in [0, 100], or None if `value` is None.
    """
    if value is None:
        return None

    # Normalize enums and other non-str values
    key = str(value.value) if hasattr(value, "value") else str(value)
    score = config.category_scores.get(key, config.default_score)
    return _clamp(score)


# ============================================================================
# Pillar-level aggregation with weight redistribution
# ============================================================================


def compute_pillar_score(
    subscore_results: list[tuple[Subscore, float]],
) -> tuple[float, float]:
    """Compute the weighted average pillar score, redistributing weight for missing subscores.

    Args:
        subscore_results: List of (Subscore, configured_weight) tuples.
            A Subscore with score=None indicates the feature was unavailable
            and its weight should be redistributed.

    Returns:
        Tuple of (pillar_score, effective_weight_sum).
        - pillar_score: weighted average of available subscore scores
          (0-100); 50.0 if no subscores have data.
        - effective_weight_sum: sum of weights that contributed (0-1);
          used by observability to report how much of the pillar was
          backed by real data.
    """
    total_score = 0.0
    total_weight = 0.0

    for subscore, weight in subscore_results:
        if subscore.score is None:
            continue
        total_score += subscore.score * weight
        total_weight += weight

    if total_weight == 0.0:
        # No subscores had data — return a neutral score so composite
        # scoring can still proceed. The caller should log this.
        return 50.0, 0.0

    return total_score / total_weight, total_weight


# ============================================================================
# High-level helper: score an entire pillar from a feature accessor
# ============================================================================


def score_pillar(
    config: PillarConfigV2,
    feature_accessor: Any,
) -> tuple[float, list[Subscore]]:
    """Score a full pillar by walking its subscore configs.

    `feature_accessor` may be a FeatureSet, a ScoringContext, or any object
    that exposes subscore `feature_field` attributes via getattr. Values
    may be None; missing attributes return None (not error).

    Args:
        config: PillarConfigV2 with numeric and/or categorical subscores.
        feature_accessor: Object with feature values accessible via getattr.

    Returns:
        Tuple of (pillar_score, subscores). `subscores` is a list of
        Subscore dataclasses with per-subscore details for observability.
    """
    subscores_with_weights: list[tuple[Subscore, float]] = []

    for num_cfg in config.numeric_subscores:
        raw = _safe_getattr(feature_accessor, num_cfg.feature_field)
        score = score_numeric_subscore(raw, num_cfg)
        sub = Subscore(
            name=num_cfg.subscore_id,
            raw_value=raw,
            score=score if score is not None else 0.0,
            weight=num_cfg.weight,
        )
        # Mark missing values by setting score to None after the fact so
        # compute_pillar_score can redistribute; the dataclass itself
        # can't hold None in its `score` field (typed as float).
        # We use a sentinel attribute on the Subscore for this purpose.
        sub = _attach_availability(sub, raw is not None and score is not None)
        subscores_with_weights.append((sub, num_cfg.weight))

    for cat_cfg in config.categorical_subscores:
        raw = _safe_getattr(feature_accessor, cat_cfg.feature_field)
        score = score_categorical_subscore(raw, cat_cfg)
        sub = Subscore(
            name=cat_cfg.subscore_id,
            raw_value=raw,
            score=score if score is not None else 0.0,
            weight=cat_cfg.weight,
        )
        sub = _attach_availability(sub, raw is not None and score is not None)
        subscores_with_weights.append((sub, cat_cfg.weight))

    pillar_score, effective_weight = _compute_pillar_score_with_availability(
        subscores_with_weights
    )

    return pillar_score, [s for s, _ in subscores_with_weights]


# ============================================================================
# Internal helpers
# ============================================================================


def _clamp(score: float) -> float:
    return max(0.0, min(100.0, float(score)))


def _safe_getattr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    return getattr(obj, name, None)


def _attach_availability(subscore: Subscore, available: bool) -> Subscore:
    """Attach an `_available` flag to the Subscore dataclass.

    Since Subscore is a `@dataclass` (not frozen), we can set extra
    attributes on instances. We use this to signal availability to
    `_compute_pillar_score_with_availability` without changing the
    dataclass shape (which would ripple across many call sites).
    """
    object.__setattr__(subscore, "_available", available)
    return subscore


def _compute_pillar_score_with_availability(
    subscores: list[tuple[Subscore, float]],
) -> tuple[float, float]:
    """Like `compute_pillar_score` but uses the _available flag attached by
    `_attach_availability` instead of checking for None (since Subscore.score
    is typed as float and can't hold None).
    """
    total_score = 0.0
    total_weight = 0.0

    for sub, weight in subscores:
        if not getattr(sub, "_available", True):
            continue
        total_score += sub.score * weight
        total_weight += weight

    if total_weight == 0.0:
        return 50.0, 0.0

    return total_score / total_weight, total_weight
