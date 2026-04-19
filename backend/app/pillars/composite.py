"""Composite formula dispatch for v3 (weighted arithmetic sum) and v4
(weighted geometric mean) pillar regimes.

This module also provides the shared helper that v4 pillar compute functions
call after building their subscores — enforcing the two v4-specific rules:

- **Insufficient-data rule:** a pillar with fewer than ``V4_MIN_SUBSCORES``
  available subscores returns score = 0. Because the composite is a
  geometric mean, a 0 on any pillar zeroes the composite — the correct
  outcome when we don't have enough data to score.

- **Floor rule:** a pillar with enough subscores returns
  ``max(V4_PILLAR_FLOOR, weighted_average)``. The floor prevents a single
  weak pillar from collapsing the composite to 0 when the other two
  pillars are strong and all data was present.

v3 regime keeps its existing arithmetic-sum path via `_weighted_sum`.
v4 regime uses `_weighted_geometric_mean`. PillarConfig carries the
``composite_formula`` literal the dispatcher reads.
"""

from __future__ import annotations

import logging
import math
from typing import Sequence

from app.core.schemas import PillarConfig, PillarId, PillarWeights
from app.pillars.models import PillarResult, Subscore

logger = logging.getLogger(__name__)

# Minimum number of available subscores for a v4 pillar to score.
# Below this, the pillar returns 0 which auto-REJECTs via geometric mean.
V4_MIN_SUBSCORES: int = 3

# Floor applied to each v4 pillar score when it does have enough data.
# Prevents one weak pillar from zero-collapsing the composite.
V4_PILLAR_FLOOR: float = 1.0


def count_available(subscores: Sequence[Subscore]) -> int:
    """Count subscores marked available by the scoring engine.

    The scoring engine attaches ``_available`` via setattr when a subscore's
    feature was present at scoring time. Treat missing flag as available
    (back-compat with subscores not built through the engine).
    """
    return sum(1 for s in subscores if getattr(s, "_available", True))


def apply_v4_rules(
    weighted_score: float,
    subscores: Sequence[Subscore],
    min_subscores: int = V4_MIN_SUBSCORES,
    floor: float = V4_PILLAR_FLOOR,
) -> float:
    """Apply the v4 insufficient-data and floor rules to a pillar score.

    Args:
        weighted_score: The raw weighted average from the scoring engine.
        subscores: Subscore list with ``_available`` flags attached.
        min_subscores: Required number of available subscores (default 3).
        floor: Minimum non-zero score (default 1.0).

    Returns:
        ``0.0`` when fewer than ``min_subscores`` are available; otherwise
        ``max(floor, weighted_score)`` clamped to [0, 100].
    """
    if count_available(subscores) < min_subscores:
        return 0.0
    return max(floor, min(100.0, weighted_score))


# ============================================================================
# Composite formulas
# ============================================================================


def compute_composite_score(
    pillar_results: Sequence[PillarResult],
    config: PillarConfig,
    scanner_source: str | None = None,
) -> float:
    """Combine pillar scores into a single composite score [0, 100].

    Dispatches on ``config.composite_formula``:

    - ``"weighted_sum"`` — v3 arithmetic weighted average.
    - ``"weighted_geometric_mean"`` — v4 weighted geometric mean, with
      exponents taken from :class:`PillarWeights`.

    The formula for v4::

        composite = prod(score_i ** weight_i)

    where ``weight_i`` sums to 1.0 across the three pillars. A pillar
    score of 0 (insufficient data per :func:`apply_v4_rules`) collapses
    the composite to 0 — the correct auto-REJECT behaviour.
    """
    weights = config.get_weights(scanner_source)

    if config.composite_formula == "weighted_max":
        return compute_weighted_max(pillar_results, weights)
    if config.composite_formula == "weighted_geometric_mean":
        return weighted_geometric_mean(pillar_results, weights)
    return weighted_sum(pillar_results, weights)


def weighted_sum(
    pillar_results: Sequence[PillarResult],
    weights: PillarWeights,
) -> float:
    """v3 composite: arithmetic weighted average of pillar scores."""
    scores_by_id = {r.pillar_id: r.score for r in pillar_results}
    total = 0.0
    for pillar_id, weight in _pillar_weights_items(weights):
        pillar_score = scores_by_id.get(pillar_id, 50.0)
        total += (weight or 0.0) * pillar_score
    return max(0.0, min(100.0, total))


def weighted_geometric_mean(
    pillar_results: Sequence[PillarResult],
    weights: PillarWeights,
) -> float:
    """v4 composite: weighted geometric mean of pillar scores.

    ``prod(score_i ** weight_i)``. When ``score_i`` is 0 the composite
    collapses to 0 — the intended auto-REJECT path for insufficient-data
    pillars (see :func:`apply_v4_rules`).
    """
    scores_by_id = {r.pillar_id: r.score for r in pillar_results}
    product = 1.0
    for pillar_id, weight in _pillar_weights_items(weights):
        w = weight or 0.0
        if w <= 0:
            continue
        pillar_score = scores_by_id.get(pillar_id, 0.0)
        if pillar_score <= 0:
            # Zero collapse — the insufficient-data signal.
            return 0.0
        product *= math.pow(pillar_score, w)
    return max(0.0, min(100.0, product))


def compute_weighted_max(
    pillar_results: Sequence[PillarResult],
    weights: PillarWeights,
    *,
    max_weight: float = 0.6,
    mean_weight: float = 0.4,
    floor_penalty_threshold: float = 25.0,
    floor_penalty_multiplier: float = 0.7,
) -> float:
    """v4.1.0 composite: ``max_weight × max(pillars) + mean_weight × weighted_mean``.

    Grand-slam trades historically have one very strong pillar (typically
    TS) with middling DC/MP. The geometric mean drags these to the middle;
    the weighted-max surfaces them while still requiring all pillars to
    clear a soft floor.

    When any pillar falls below ``floor_penalty_threshold`` (default 25),
    the composite is multiplied by ``floor_penalty_multiplier`` (default
    0.7). Softer alternative to the v4.0.0 min-subscore zero-collapse.
    """
    scores_by_id = {r.pillar_id: r.score for r in pillar_results}
    pillar_scores: list[float] = []
    weighted_sum_val = 0.0
    weight_total = 0.0
    for pillar_id, weight in _pillar_weights_items(weights):
        w = weight or 0.0
        if w <= 0:
            continue
        score = scores_by_id.get(pillar_id, 0.0)
        pillar_scores.append(score)
        weighted_sum_val += w * score
        weight_total += w
    if not pillar_scores:
        return 0.0
    max_pillar = max(pillar_scores)
    weighted_mean = weighted_sum_val / weight_total if weight_total > 0 else 0.0
    composite = max_weight * max_pillar + mean_weight * weighted_mean
    if min(pillar_scores) < floor_penalty_threshold:
        composite *= floor_penalty_multiplier
    return max(0.0, min(100.0, composite))


def _pillar_weights_items(weights: PillarWeights) -> list[tuple[PillarId, float]]:
    """Flatten :class:`PillarWeights` into (pillar_id, weight) pairs.

    Emits only fields that are populated — lets v3 and v4 instances share
    the same code path without branching on regime.
    """
    pairs: list[tuple[PillarId, float]] = []
    if weights.premium_leverage is not None:
        pairs.append((PillarId.PREMIUM_LEVERAGE, weights.premium_leverage))
    if weights.underlying_behavior is not None:
        pairs.append((PillarId.UNDERLYING_BEHAVIOR, weights.underlying_behavior))
    if weights.setup_quality is not None:
        pairs.append((PillarId.SETUP_QUALITY, weights.setup_quality))
    if weights.directional_conviction is not None:
        pairs.append((PillarId.DIRECTIONAL_CONVICTION, weights.directional_conviction))
    if weights.move_potential is not None:
        pairs.append((PillarId.MOVE_POTENTIAL, weights.move_potential))
    if weights.trade_structure is not None:
        pairs.append((PillarId.TRADE_STRUCTURE, weights.trade_structure))
    return pairs
