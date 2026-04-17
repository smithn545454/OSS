"""Stage 5: Pillar Scoring Pipeline Stage.

Integrates pillar scoring into the pipeline orchestration.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.core.schemas import (
    Evaluation,
    Opportunity,
    PillarConfig,
    PillarScore,
    PipelineStage,
)
from app.core.pipeline import PipelineOrchestrator
from app.db.tables import PillarScoreTable
from app.features.models import FeatureSet
from app.pillars.calculator import PillarCalculator
from app.pillars.models import PillarResult

logger = logging.getLogger(__name__)


class PillarScoringStage:
    """Stage 5: Pillar Scoring (Policy v3.0.0).

    Takes Evaluations and FeatureSets from Stage 4 and computes three
    pillar scores (Premium Leverage, Underlying Behavior, Setup Quality)
    for each.

    Pillars never reject - they only score. Rejection is handled by
    hard gates in Stage 6.
    """

    def __init__(
        self,
        orchestrator: PipelineOrchestrator,
        config: Optional[PillarConfig] = None,
    ) -> None:
        """Initialize the pillar scoring stage.

        Args:
            orchestrator: Pipeline orchestrator for event tracking.
            config: Pillar scoring configuration. If omitted, uses
                `PillarConfig.v3_default()` which loads Policy v3.0.0
                defaults from the seed JSON. Transitional fallback —
                remove at Phase 9 alongside v3 code.
        """
        self._orchestrator = orchestrator or PipelineOrchestrator()
        self._config = config or PillarConfig.v3_default()
        self._calculator = PillarCalculator(self._config)
    
    async def execute(
        self,
        run_id: str,
        evaluations: Sequence[Evaluation],
        feature_sets: Sequence[FeatureSet],
        opportunities: Sequence[Opportunity],
        persist_scores: bool = True,
    ) -> dict[str, list[PillarResult]]:
        """Execute pillar scoring stage.
        
        Args:
            run_id: Pipeline run ID for tracking
            evaluations: Evaluations from Stage 3
            feature_sets: FeatureSets from Stage 4
            opportunities: Opportunities (for scanner triggers)
            persist_scores: If True, save PillarScores to DynamoDB
            
        Returns:
            Dict mapping evaluation_id to list of PillarResult
        """
        logger.info(f"Stage 5: Scoring pillars for {len(evaluations)} evaluations")
        
        # Update current stage
        await self._orchestrator.update_current_stage(run_id, PipelineStage.PILLAR_SCORING)
        
        if not evaluations:
            # Record empty stage event
            await self._orchestrator.record_stage_event(
                run_id=run_id,
                stage=PipelineStage.PILLAR_SCORING,
                items_in=0,
                items_out=0,
            )
            return {}
        
        # Build lookup maps
        feature_map = {fs.evaluation_id: fs for fs in feature_sets}
        opportunity_map = {opp.underlying_ticker: opp for opp in opportunities}
        
        # Compute pillar scores for all evaluations
        results = self._calculator.compute_pillars_batch(
            evaluations=evaluations,
            feature_sets=feature_map,
            opportunities=opportunity_map,
        )
        
        # Persist pillar scores if requested
        if persist_scores and results:
            await self._persist_scores(results)
        
        # Calculate stage statistics
        total_pillar_scores = sum(len(pr) for pr in results.values())
        
        # Count score distribution
        score_buckets = {"high": 0, "medium": 0, "low": 0}
        all_tags: dict[str, int] = {}
        
        for pillar_results in results.values():
            for pr in pillar_results:
                if pr.score >= 75:
                    score_buckets["high"] += 1
                elif pr.score >= 50:
                    score_buckets["medium"] += 1
                else:
                    score_buckets["low"] += 1
                
                for tag in pr.tags:
                    all_tags[tag] = all_tags.get(tag, 0) + 1
        
        # Record stage event
        await self._orchestrator.record_stage_event(
            run_id=run_id,
            stage=PipelineStage.PILLAR_SCORING,
            items_in=len(evaluations),
            items_out=len(results),  # Number of evaluations scored
            metadata={
                "total_pillar_scores": total_pillar_scores,
                "score_distribution": score_buckets,
                "top_tags": dict(sorted(all_tags.items(), key=lambda x: -x[1])[:10]),
                "feature_sets_available": len(feature_map),
                "opportunities_available": len(opportunity_map),
            },
        )
        
        logger.info(
            f"Stage 5 complete: {len(results)} evaluations scored, "
            f"{total_pillar_scores} pillar scores generated"
        )
        
        return results
    
    async def _persist_scores(
        self,
        results: dict[str, list[PillarResult]],
    ) -> None:
        """Persist pillar scores to DynamoDB.
        
        Args:
            results: Dict mapping evaluation_id to PillarResults
        """
        for evaluation_id, pillar_results in results.items():
            try:
                scores = [pr.to_pillar_score() for pr in pillar_results]
                await PillarScoreTable.put_batch(scores)
            except Exception as e:
                logger.error(f"Error persisting pillar scores for {evaluation_id}: {e}")


async def run_pillar_scoring(
    run_id: str,
    evaluations: Sequence[Evaluation],
    feature_sets: Sequence[FeatureSet],
    opportunities: Sequence[Opportunity],
    orchestrator: PipelineOrchestrator,
    config: Optional[PillarConfig] = None,
    persist_scores: bool = True,
) -> dict[str, list[PillarResult]]:
    """Convenience function to run pillar scoring stage.
    
    Args:
        run_id: Pipeline run ID
        evaluations: Evaluations from Stage 3
        feature_sets: FeatureSets from Stage 4
        opportunities: Opportunities for scanner context
        orchestrator: Pipeline orchestrator
        config: Pillar configuration
        persist_scores: Whether to save to DynamoDB
        
    Returns:
        Dict mapping evaluation_id to list of PillarResult
    """
    stage = PillarScoringStage(
        orchestrator=orchestrator,
        config=config,
    )
    return await stage.execute(
        run_id=run_id,
        evaluations=evaluations,
        feature_sets=feature_sets,
        opportunities=opportunities,
        persist_scores=persist_scores,
    )


def extract_pillar_scores_for_decision(
    results: dict[str, list[PillarResult]],
) -> dict[str, dict[str, float]]:
    """Extract pillar scores in format needed for decision logic (v3.0.0).

    Args:
        results: Dict mapping evaluation_id to PillarResults

    Returns:
        Dict mapping evaluation_id to dict with new pillar keys:
        `premium_leverage`, `underlying_behavior`, `setup_quality`.
    """
    scores: dict[str, dict[str, float]] = {}

    for evaluation_id, pillar_results in results.items():
        eval_scores = {
            "premium_leverage": 50.0,
            "underlying_behavior": 50.0,
            "setup_quality": 50.0,
        }

        for pr in pillar_results:
            pillar_name = str(pr.pillar_id)
            if hasattr(pr.pillar_id, "value"):
                pillar_name = pr.pillar_id.value
            key = pillar_name.lower()
            if key in eval_scores:
                eval_scores[key] = pr.score

        scores[evaluation_id] = eval_scores

    return scores
