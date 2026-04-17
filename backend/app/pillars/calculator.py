"""Pillar Calculator - Main orchestrator for Stage 5 (Policy v3.0.0).

Computes all three pillar scores for evaluations, coordinating
individual pillar calculations and result assembly. The pillar system
uses empirically-derived breakpoint scoring — see scoring_engine.py
and scripts/output/final_pillar_design.json.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.core.schemas import (
    Evaluation,
    Opportunity,
    PillarConfig,
    PillarId,
    PillarScore,
)
from app.features.models import FeatureSet
from app.pillars.models import PillarResult, ScoringContext
from app.pillars.premium_leverage import compute_premium_leverage_pillar
from app.pillars.setup_quality import compute_setup_quality_pillar
from app.pillars.underlying_behavior import compute_underlying_behavior_pillar

logger = logging.getLogger(__name__)


class PillarCalculator:
    """Orchestrates pillar scoring for Stage 5 of the pipeline (v3.0.0).

    This class coordinates:
    1. Building ScoringContext from Evaluation, FeatureSet, and Opportunity
    2. Computing all three pillar scores
    3. Assembling PillarScore records for storage
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

        Args:
            evaluation: Evaluation record from Stage 3 (or None if `context`
                is provided directly — used by the rescore job which builds
                ScoringContext from PaperPosition).
            feature_set: FeatureSet from Stage 4 (optional).
            opportunity: Opportunity record with scanner triggers (optional).
            context: Prebuilt ScoringContext (overrides evaluation/feature_set
                path when provided).

        Returns:
            List of three PillarResult objects in the canonical order:
            [Premium Leverage, Underlying Behavior, Setup Quality].
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

        # v3-only compute path — v4 orchestrator registered in Phase 3.
        # Assert non-None because PillarConfig validator guarantees v3 shape
        # when this method runs.
        assert self._config.premium_leverage is not None
        assert self._config.underlying_behavior is not None
        assert self._config.setup_quality is not None
        premium_leverage = compute_premium_leverage_pillar(
            context, self._config.premium_leverage
        )
        underlying_behavior = compute_underlying_behavior_pillar(
            context, self._config.underlying_behavior
        )
        setup_quality = compute_setup_quality_pillar(
            context, self._config.setup_quality
        )

        return [premium_leverage, underlying_behavior, setup_quality]

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

        # Log data availability for diagnostics
        if feature_sets:
            total = len(feature_sets)
            adx = sum(1 for fs in feature_sets.values() if fs.adx_14 is not None)
            iv_pct = sum(1 for fs in feature_sets.values() if fs.iv_percentile is not None)
            iv_rv = sum(1 for fs in feature_sets.values() if fs.iv_rv_ratio is not None)
            rv20 = sum(1 for fs in feature_sets.values() if fs.rv20 is not None)
            feas = sum(1 for fs in feature_sets.values() if fs.feasibility_ratio)
            logger.info(
                f"Pillar v3 data availability ({total} evals): "
                f"adx_14={adx}/{total} "
                f"iv_percentile={iv_pct}/{total} "
                f"iv_rv_ratio={iv_rv}/{total} "
                f"rv20={rv20}/{total} "
                f"feasibility={feas}/{total}"
            )

        logger.info(f"Computed v3 pillars for {len(results)} evaluations")
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


def compute_all_pillars(
    evaluation: Evaluation,
    feature_set: Optional[FeatureSet],
    opportunity: Optional[Opportunity],
    config: Optional[PillarConfig] = None,
) -> tuple[PillarResult, PillarResult, PillarResult]:
    """Convenience function to compute all three pillars.

    Returns:
        Tuple of (premium_leverage, underlying_behavior, setup_quality) PillarResults.
    """
    calculator = PillarCalculator(config)
    results = calculator.compute_pillars(evaluation, feature_set, opportunity)
    return results[0], results[1], results[2]


def get_pillar_scores_dict(
    pillar_results: Sequence[PillarResult],
) -> dict[str, float]:
    """Extract pillar scores as a dictionary keyed by snake_case pillar name."""
    scores = {
        "premium_leverage": 50.0,
        "underlying_behavior": 50.0,
        "setup_quality": 50.0,
    }

    for result in pillar_results:
        pid = result.pillar_id
        if pid == PillarId.PREMIUM_LEVERAGE or pid == "PREMIUM_LEVERAGE":
            scores["premium_leverage"] = result.score
        elif pid == PillarId.UNDERLYING_BEHAVIOR or pid == "UNDERLYING_BEHAVIOR":
            scores["underlying_behavior"] = result.score
        elif pid == PillarId.SETUP_QUALITY or pid == "SETUP_QUALITY":
            scores["setup_quality"] = result.score

    return scores


def compute_final_score(
    premium_leverage: float,
    underlying_behavior: float,
    setup_quality: float,
    config: Optional[PillarConfig] = None,
    scanner_source: Optional[str] = None,
) -> float:
    """Compute final weighted composite score from pillar scores.

    When ``scanner_source`` is provided and the config contains per-scanner
    weight overrides, those override the global weights. This lets each
    scanner emphasise the pillars that matter most for its signal type.

    Args:
        premium_leverage: Pillar score (0-100)
        underlying_behavior: Pillar score (0-100)
        setup_quality: Pillar score (0-100)
        config: PillarConfig with weight configuration
        scanner_source: Scanner name for per-scanner weight lookup

    Returns:
        Final weighted composite score (0-100)
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
