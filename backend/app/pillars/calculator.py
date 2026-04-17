"""Pillar Calculator - Main orchestrator for Stage 5.

Dispatches to v3 pillars (Premium Leverage / Underlying Behavior / Setup
Quality) or v4 pillars (Directional Conviction / Move Potential / Trade
Structure) based on the regime carried by :class:`PillarConfig`.

The registry approach keeps v3 and v4 code reachable side-by-side until
Phase 9 removes v3. Each regime maps three positional ``PillarConfigV2``
slots on the config to three compute functions.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence

from app.core.schemas import (
    Evaluation,
    Opportunity,
    PillarConfig,
    PillarConfigV2,
    PillarId,
    PillarScore,
)
from app.features.models import FeatureSet
from app.pillars.directional_conviction import compute_directional_conviction_pillar
from app.pillars.models import PillarResult, ScoringContext
from app.pillars.move_potential import compute_move_potential_pillar
from app.pillars.premium_leverage import compute_premium_leverage_pillar
from app.pillars.setup_quality import compute_setup_quality_pillar
from app.pillars.trade_structure import compute_trade_structure_pillar
from app.pillars.underlying_behavior import compute_underlying_behavior_pillar

logger = logging.getLogger(__name__)


# ============================================================================
# Registry: pillar_id → compute_function, ordered by regime
# ============================================================================


PillarComputeFn = Callable[[ScoringContext, PillarConfigV2], PillarResult]


_V3_REGISTRY: list[tuple[PillarId, PillarComputeFn]] = [
    (PillarId.PREMIUM_LEVERAGE, compute_premium_leverage_pillar),
    (PillarId.UNDERLYING_BEHAVIOR, compute_underlying_behavior_pillar),
    (PillarId.SETUP_QUALITY, compute_setup_quality_pillar),
]

_V4_REGISTRY: list[tuple[PillarId, PillarComputeFn]] = [
    (PillarId.DIRECTIONAL_CONVICTION, compute_directional_conviction_pillar),
    (PillarId.MOVE_POTENTIAL, compute_move_potential_pillar),
    (PillarId.TRADE_STRUCTURE, compute_trade_structure_pillar),
]


def _resolve_subconfig(config: PillarConfig, pillar_id: PillarId) -> PillarConfigV2:
    """Look up the PillarConfigV2 slot on PillarConfig by pillar_id."""
    field_name = _PILLAR_ID_TO_FIELD[pillar_id]
    sub = getattr(config, field_name, None)
    if sub is None:
        raise ValueError(
            f"PillarConfig is missing the '{field_name}' sub-config required "
            f"to compute {pillar_id}"
        )
    return sub


_PILLAR_ID_TO_FIELD: dict[PillarId, str] = {
    PillarId.PREMIUM_LEVERAGE: "premium_leverage",
    PillarId.UNDERLYING_BEHAVIOR: "underlying_behavior",
    PillarId.SETUP_QUALITY: "setup_quality",
    PillarId.DIRECTIONAL_CONVICTION: "directional_conviction",
    PillarId.MOVE_POTENTIAL: "move_potential",
    PillarId.TRADE_STRUCTURE: "trade_structure",
}


# ============================================================================
# Orchestrator
# ============================================================================


class PillarCalculator:
    """Orchestrates pillar scoring for Stage 5 of the pipeline.

    Selects the v3 or v4 compute registry based on which pillar slots the
    active :class:`PillarConfig` populates. Both regimes go through the
    same batch paths — the only branch is picking the registry.
    """

    def __init__(
        self,
        config: Optional[PillarConfig] = None,
    ) -> None:
        """Initialize the pillar calculator.

        Args:
            config: Pillar scoring configuration. If omitted, uses
                `PillarConfig.v3_default()` which loads the seeded Policy
                v3.0.0 defaults. Production code should explicitly pass the
                active policy's pillar config. Transitional fallback —
                remove at Phase 9 alongside v3 code.
        """
        self._config = config or PillarConfig.v3_default()

    def compute_pillars(
        self,
        evaluation: Optional[Evaluation],
        feature_set: Optional[FeatureSet],
        opportunity: Optional[Opportunity],
        context: Optional[ScoringContext] = None,
    ) -> list[PillarResult]:
        """Compute all three pillar scores for a single evaluation.

        The three results are returned in canonical registry order for the
        active regime:

        - v3 → [Premium Leverage, Underlying Behavior, Setup Quality]
        - v4 → [Directional Conviction, Move Potential, Trade Structure]
        """
        if context is None:
            if evaluation is None:
                raise ValueError(
                    "compute_pillars requires either `context` or `evaluation`"
                )
            context = ScoringContext.from_evaluation_and_features(
                evaluation=evaluation,
                feature_set=feature_set,
                opportunity=opportunity,
            )

        registry = _V4_REGISTRY if self._config.is_v4() else _V3_REGISTRY
        results: list[PillarResult] = []
        for pillar_id, compute_fn in registry:
            sub_config = _resolve_subconfig(self._config, pillar_id)
            results.append(compute_fn(context, sub_config))
        return results

    def compute_pillars_to_scores(
        self,
        evaluation: Evaluation,
        feature_set: Optional[FeatureSet],
        opportunity: Optional[Opportunity],
    ) -> list[PillarScore]:
        """Compute pillar scores and convert to PillarScore schema."""
        results = self.compute_pillars(evaluation, feature_set, opportunity)
        return [r.to_pillar_score() for r in results]

    def compute_pillars_batch(
        self,
        evaluations: Sequence[Evaluation],
        feature_sets: dict[str, FeatureSet],
        opportunities: dict[str, Opportunity],
    ) -> dict[str, list[PillarResult]]:
        """Compute pillar scores for multiple evaluations."""
        results: dict[str, list[PillarResult]] = {}

        for evaluation in evaluations:
            try:
                feature_set = feature_sets.get(evaluation.evaluation_id)
                opportunity = opportunities.get(evaluation.underlying_ticker)

                pillar_results = self.compute_pillars(
                    evaluation=evaluation,
                    feature_set=feature_set,
                    opportunity=opportunity,
                )
                results[evaluation.evaluation_id] = pillar_results

            except Exception as e:
                logger.error(
                    f"Error computing pillars for {evaluation.evaluation_id}: {e}"
                )
                continue

        self._log_data_availability(feature_sets)
        regime = "v4" if self._config.is_v4() else "v3"
        logger.info(f"Computed {regime} pillars for {len(results)} evaluations")
        return results

    def compute_pillars_batch_to_scores(
        self,
        evaluations: Sequence[Evaluation],
        feature_sets: dict[str, FeatureSet],
        opportunities: dict[str, Opportunity],
    ) -> list[PillarScore]:
        """Compute pillar scores for batch and flatten to list."""
        results = self.compute_pillars_batch(evaluations, feature_sets, opportunities)

        all_scores: list[PillarScore] = []
        for pillar_results in results.values():
            for result in pillar_results:
                all_scores.append(result.to_pillar_score())

        return all_scores

    # --- diagnostics -------------------------------------------------

    def _log_data_availability(self, feature_sets: dict[str, FeatureSet]) -> None:
        if not feature_sets:
            return
        total = len(feature_sets)
        if self._config.is_v4():
            coverage = {
                "ma_200": sum(1 for fs in feature_sets.values() if fs.ma_200 is not None),
                "high_52w": sum(1 for fs in feature_sets.values() if fs.high_52w is not None),
                "sector_rs_20d": sum(
                    1 for fs in feature_sets.values() if fs.sector_rs_20d is not None
                ),
                "historical_move_magnitude": sum(
                    1 for fs in feature_sets.values()
                    if fs.historical_move_magnitude is not None
                ),
                "bb_width_percentile": sum(
                    1 for fs in feature_sets.values()
                    if fs.bb_width_percentile is not None
                ),
                "days_to_earnings": sum(
                    1 for fs in feature_sets.values() if fs.days_to_earnings is not None
                ),
            }
            logger.info(
                f"Pillar v4 data availability ({total} evals): "
                + " ".join(f"{k}={v}/{total}" for k, v in coverage.items())
            )
        else:
            coverage = {
                "adx_14": sum(1 for fs in feature_sets.values() if fs.adx_14 is not None),
                "iv_percentile": sum(
                    1 for fs in feature_sets.values() if fs.iv_percentile is not None
                ),
                "iv_rv_ratio": sum(
                    1 for fs in feature_sets.values() if fs.iv_rv_ratio is not None
                ),
                "rv20": sum(1 for fs in feature_sets.values() if fs.rv20 is not None),
                "feasibility": sum(
                    1 for fs in feature_sets.values() if fs.feasibility_ratio
                ),
            }
            logger.info(
                f"Pillar v3 data availability ({total} evals): "
                + " ".join(f"{k}={v}/{total}" for k, v in coverage.items())
            )


# ============================================================================
# Convenience functions
# ============================================================================


def compute_all_pillars(
    evaluation: Evaluation,
    feature_set: Optional[FeatureSet],
    opportunity: Optional[Opportunity],
    config: Optional[PillarConfig] = None,
) -> tuple[PillarResult, PillarResult, PillarResult]:
    """Convenience wrapper. Returns 3 PillarResults in registry order."""
    calculator = PillarCalculator(config)
    results = calculator.compute_pillars(evaluation, feature_set, opportunity)
    return results[0], results[1], results[2]


_SNAKE_CASE_BY_ID: dict[Any, str] = {
    # Map PillarId enums AND raw string literals (historical data) to the
    # snake_case dict key used by downstream consumers.
    PillarId.PREMIUM_LEVERAGE: "premium_leverage",
    "PREMIUM_LEVERAGE": "premium_leverage",
    PillarId.UNDERLYING_BEHAVIOR: "underlying_behavior",
    "UNDERLYING_BEHAVIOR": "underlying_behavior",
    PillarId.SETUP_QUALITY: "setup_quality",
    "SETUP_QUALITY": "setup_quality",
    PillarId.DIRECTIONAL_CONVICTION: "directional_conviction",
    "DIRECTIONAL_CONVICTION": "directional_conviction",
    PillarId.MOVE_POTENTIAL: "move_potential",
    "MOVE_POTENTIAL": "move_potential",
    PillarId.TRADE_STRUCTURE: "trade_structure",
    "TRADE_STRUCTURE": "trade_structure",
}


def get_pillar_scores_dict(
    pillar_results: Sequence[PillarResult],
) -> dict[str, float]:
    """Extract pillar scores as a snake_case-keyed dict.

    The returned dict contains only the pillars present in ``pillar_results``.
    Callers that need a specific regime's full set should rely on the
    calculator's registry order rather than defaulting unseen keys to 50.0.
    """
    scores: dict[str, float] = {}
    for result in pillar_results:
        key = _SNAKE_CASE_BY_ID.get(result.pillar_id)
        if key:
            scores[key] = result.score
    return scores


def compute_final_score(
    premium_leverage: float,
    underlying_behavior: float,
    setup_quality: float,
    config: Optional[PillarConfig] = None,
    scanner_source: Optional[str] = None,
) -> float:
    """Legacy v3 positional composite score.

    Retained with its original signature for v3 callers (rescore scripts,
    paper-trading APIs, tests). v4 callers use
    :func:`compute_final_score_from_results` which accepts PillarResult
    objects directly and honours ``composite_formula`` dispatch.
    Marked for Phase 9 removal.
    """
    if config is None:
        config = PillarConfig.v3_default()
    weights = config.get_weights(scanner_source)
    final = (
        (weights.premium_leverage or 0.0) * premium_leverage
        + (weights.underlying_behavior or 0.0) * underlying_behavior
        + (weights.setup_quality or 0.0) * setup_quality
    )
    return max(0.0, min(100.0, final))


def compute_final_score_from_results(
    pillar_results: Sequence[PillarResult],
    config: Optional[PillarConfig] = None,
    scanner_source: Optional[str] = None,
) -> float:
    """Compute the final composite from v3 or v4 pillar results.

    Dispatches on :attr:`PillarConfig.composite_formula` — arithmetic sum
    for v3, weighted geometric mean for v4. This is the preferred API
    going forward; v3-specific positional callers still use
    :func:`compute_final_score` during the transition.
    """
    if config is None:
        config = PillarConfig.v3_default()
    from app.pillars.composite import compute_composite_score  # local import avoids cycles

    return compute_composite_score(pillar_results, config, scanner_source)
