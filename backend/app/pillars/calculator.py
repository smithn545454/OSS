"""Pillar Calculator - Main orchestrator for Stage 5.

Computes all three pillar scores for evaluations, coordinating
individual pillar calculations and result assembly.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.core.schemas import (
    Evaluation,
    Opportunity,
    PillarConfig,
    PillarScore,
)
from app.features.models import FeatureSet
from app.pillars.models import PillarResult, ScoringContext
from app.pillars.directional import compute_directional_pillar
from app.pillars.volatility import compute_volatility_pillar
from app.pillars.structure import compute_structure_pillar

logger = logging.getLogger(__name__)


class PillarCalculator:
    """Orchestrates pillar scoring for Stage 5 of the pipeline.
    
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
            config: Pillar scoring configuration (optional)
        """
        self._config = config or PillarConfig()
    
    def compute_pillars(
        self,
        evaluation: Evaluation,
        feature_set: Optional[FeatureSet],
        opportunity: Optional[Opportunity],
    ) -> list[PillarResult]:
        """Compute all three pillar scores for a single evaluation.
        
        Args:
            evaluation: Evaluation record from Stage 3
            feature_set: FeatureSet from Stage 4 (optional, uses evaluation data if None)
            opportunity: Opportunity record with scanner triggers (optional)
            
        Returns:
            List of three PillarResult objects (Directional, Volatility, Structure)
        """
        # Build scoring context
        ctx = ScoringContext.from_evaluation_and_features(
            evaluation=evaluation,
            feature_set=feature_set,
            opportunity=opportunity,
        )
        
        # Compute all three pillars
        directional = compute_directional_pillar(ctx, self._config.directional)
        volatility = compute_volatility_pillar(ctx, self._config.volatility)
        structure = compute_structure_pillar(ctx, self._config.structure)
        
        return [directional, volatility, structure]
    
    def compute_pillars_to_scores(
        self,
        evaluation: Evaluation,
        feature_set: Optional[FeatureSet],
        opportunity: Optional[Opportunity],
    ) -> list[PillarScore]:
        """Compute pillar scores and convert to PillarScore schema.
        
        Args:
            evaluation: Evaluation record from Stage 3
            feature_set: FeatureSet from Stage 4 (optional)
            opportunity: Opportunity record (optional)
            
        Returns:
            List of three PillarScore objects ready for storage
        """
        results = self.compute_pillars(evaluation, feature_set, opportunity)
        return [r.to_pillar_score() for r in results]
    
    def compute_pillars_batch(
        self,
        evaluations: Sequence[Evaluation],
        feature_sets: dict[str, FeatureSet],
        opportunities: dict[str, Opportunity],
    ) -> dict[str, list[PillarResult]]:
        """Compute pillar scores for multiple evaluations.
        
        Args:
            evaluations: List of Evaluation records
            feature_sets: Dict mapping evaluation_id to FeatureSet
            opportunities: Dict mapping underlying_ticker to Opportunity
            
        Returns:
            Dict mapping evaluation_id to list of PillarResult
        """
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
            iv_rv = sum(1 for fs in feature_sets.values() if fs.iv_rv_ratio is not None)
            iv_pct = sum(1 for fs in feature_sets.values() if fs.iv_percentile is not None)
            rv20 = sum(1 for fs in feature_sets.values() if fs.rv20 is not None)
            rs = sum(1 for fs in feature_sets.values() if fs.rs_20d is not None)
            tae = sum(1 for fs in feature_sets.values() if fs.theta_adjusted_edge is not None)
            dte = sum(1 for fs in feature_sets.values() if fs.days_to_earnings is not None)
            logger.info(
                f"Pillar data availability ({total} evals): "
                f"iv_rv_ratio={iv_rv}/{total} "
                f"iv_percentile={iv_pct}/{total} "
                f"rv20={rv20}/{total} "
                f"rs_20d={rs}/{total} "
                f"theta_adj_edge={tae}/{total} "
                f"days_to_earnings={dte}/{total}"
            )

        logger.info(f"Computed pillars for {len(results)} evaluations")
        return results
    
    def compute_pillars_batch_to_scores(
        self,
        evaluations: Sequence[Evaluation],
        feature_sets: dict[str, FeatureSet],
        opportunities: dict[str, Opportunity],
    ) -> list[PillarScore]:
        """Compute pillar scores for batch and flatten to list.
        
        Args:
            evaluations: List of Evaluation records
            feature_sets: Dict mapping evaluation_id to FeatureSet
            opportunities: Dict mapping underlying_ticker to Opportunity
            
        Returns:
            Flat list of all PillarScore objects
        """
        results = self.compute_pillars_batch(evaluations, feature_sets, opportunities)
        
        all_scores: list[PillarScore] = []
        for pillar_results in results.values():
            for result in pillar_results:
                all_scores.append(result.to_pillar_score())
        
        return all_scores


def compute_all_pillars(
    evaluation: Evaluation,
    feature_set: Optional[FeatureSet] = None,
    opportunity: Optional[Opportunity] = None,
    config: Optional[PillarConfig] = None,
) -> tuple[PillarResult, PillarResult, PillarResult]:
    """Convenience function to compute all three pillars.
    
    Args:
        evaluation: Evaluation record
        feature_set: FeatureSet (optional)
        opportunity: Opportunity (optional)
        config: PillarConfig (optional)
        
    Returns:
        Tuple of (directional, volatility, structure) PillarResults
    """
    calculator = PillarCalculator(config)
    results = calculator.compute_pillars(evaluation, feature_set, opportunity)
    return results[0], results[1], results[2]


def get_pillar_scores_dict(
    pillar_results: Sequence[PillarResult],
) -> dict[str, float]:
    """Extract pillar scores as a dictionary.
    
    Args:
        pillar_results: List of PillarResult objects
        
    Returns:
        Dict with keys 'directional', 'volatility', 'structure'
    """
    scores = {
        "directional": 50.0,
        "volatility": 50.0,
        "structure": 50.0,
    }
    
    for result in pillar_results:
        if result.pillar_id == "DIRECTIONAL":
            scores["directional"] = result.score
        elif result.pillar_id == "VOLATILITY":
            scores["volatility"] = result.score
        elif result.pillar_id == "STRUCTURE":
            scores["structure"] = result.score
    
    return scores


def compute_final_score(
    directional: float,
    volatility: float,
    structure: float,
    config: Optional[PillarConfig] = None,
) -> float:
    """Compute final weighted score from pillar scores.
    
    Per Section 16.1:
    final_score = (0.35 × directional) + (0.35 × volatility) + (0.30 × structure)
    
    Args:
        directional: Directional pillar score (0-100)
        volatility: Volatility pillar score (0-100)
        structure: Structure pillar score (0-100)
        config: Optional PillarConfig with weight overrides
        
    Returns:
        Final weighted score (0-100)
    """
    if config is None:
        config = PillarConfig()
    
    weights = config.weights
    
    final = (
        weights.directional * directional +
        weights.volatility * volatility +
        weights.structure * structure
    )
    
    return max(0.0, min(100.0, final))
