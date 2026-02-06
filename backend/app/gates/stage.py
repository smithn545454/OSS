"""Stage 6: Hard Gates Pipeline Stage.

Integrates gate evaluation into the pipeline orchestration.
Per Section 15 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.core.schemas import (
    Evaluation,
    GateConfig,
    GateResult,
    Opportunity,
    PipelineStage,
)
from app.core.pipeline import PipelineOrchestrator
from app.db.tables import GateResultTable
from app.features.models import FeatureSet
from app.gates.calculator import GateCalculator
from app.gates.models import GateEvaluation

logger = logging.getLogger(__name__)


class HardGatesStage:
    """Stage 6: Hard Gates.
    
    Takes Evaluations, FeatureSets, and Opportunities from previous stages
    and evaluates all configured gates for each evaluation.
    
    Any failed enabled gate results in REJECT regardless of pillar scores.
    Gate results are passed to Stage 7 (Decision Logic) for final verdict.
    """
    
    def __init__(
        self,
        orchestrator: PipelineOrchestrator,
        config: Optional[GateConfig] = None,
    ) -> None:
        """Initialize the hard gates stage.
        
        Args:
            orchestrator: Pipeline orchestrator for event tracking
            config: Gate configuration with thresholds
        """
        self._orchestrator = orchestrator
        self._config = config or GateConfig()
        self._calculator = GateCalculator(config)
    
    async def execute(
        self,
        run_id: str,
        evaluations: Sequence[Evaluation],
        feature_sets: Sequence[FeatureSet],
        opportunities: Sequence[Opportunity],
        persist_results: bool = True,
    ) -> dict[str, GateEvaluation]:
        """Execute hard gates stage.
        
        Args:
            run_id: Pipeline run ID for tracking
            evaluations: Evaluations from Stage 3
            feature_sets: FeatureSets from Stage 4
            opportunities: Opportunities (for scanner triggers)
            persist_results: If True, save GateResults to DynamoDB
            
        Returns:
            Dict mapping evaluation_id to GateEvaluation
        """
        logger.info(f"Stage 6: Evaluating gates for {len(evaluations)} evaluations")
        
        # Update current stage
        await self._orchestrator.update_current_stage(run_id, PipelineStage.HARD_GATES)
        
        if not evaluations:
            # Record empty stage event
            await self._orchestrator.record_stage_event(
                run_id=run_id,
                stage=PipelineStage.HARD_GATES,
                items_in=0,
                items_out=0,
            )
            return {}
        
        # Build lookup maps
        feature_map = {fs.evaluation_id: fs for fs in feature_sets}
        opportunity_map = {opp.underlying_ticker: opp for opp in opportunities}
        
        # Evaluate gates for all evaluations
        results = self._calculator.evaluate_gates_batch(
            evaluations=evaluations,
            feature_sets=feature_map,
            opportunities=opportunity_map,
        )
        
        # Persist gate results if requested
        if persist_results and results:
            await self._persist_results(results)
        
        # Calculate stage statistics
        passed_count = sum(1 for ge in results.values() if ge.all_passed)
        failed_count = len(results) - passed_count
        
        # Get gate failure breakdown
        failure_summary = self._calculator.get_failure_summary(results)
        
        # Get drop reasons (failure reason codes)
        drop_reasons: dict[str, int] = {}
        for gate_eval in results.values():
            for reason_code in gate_eval.failed_reason_codes:
                drop_reasons[reason_code] = drop_reasons.get(reason_code, 0) + 1
        
        # Total gate results created
        total_gate_results = sum(len(ge.gate_results) for ge in results.values())
        
        # Record stage event
        await self._orchestrator.record_stage_event(
            run_id=run_id,
            stage=PipelineStage.HARD_GATES,
            items_in=len(evaluations),
            items_out=passed_count,  # Only passed evaluations continue
            drop_reasons=drop_reasons,
            metadata={
                "total_gate_results": total_gate_results,
                "passed_all_gates": passed_count,
                "failed_any_gate": failed_count,
                "failure_by_gate": failure_summary,
                "feature_sets_available": len(feature_map),
                "opportunities_available": len(opportunity_map),
            },
        )
        
        logger.info(
            f"Stage 6 complete: {passed_count}/{len(results)} evaluations passed all gates, "
            f"{failed_count} failed"
        )
        
        return results
    
    async def _persist_results(
        self,
        results: dict[str, GateEvaluation],
    ) -> None:
        """Persist gate results to DynamoDB.
        
        Args:
            results: Dict mapping evaluation_id to GateEvaluation
        """
        for evaluation_id, gate_eval in results.items():
            try:
                await GateResultTable.put_batch(gate_eval.gate_results)
            except Exception as e:
                logger.error(f"Error persisting gate results for {evaluation_id}: {e}")


async def run_hard_gates(
    run_id: str,
    evaluations: Sequence[Evaluation],
    feature_sets: Sequence[FeatureSet],
    opportunities: Sequence[Opportunity],
    orchestrator: PipelineOrchestrator,
    config: Optional[GateConfig] = None,
    persist_results: bool = True,
) -> dict[str, GateEvaluation]:
    """Convenience function to run hard gates stage.
    
    Args:
        run_id: Pipeline run ID
        evaluations: Evaluations from Stage 3
        feature_sets: FeatureSets from Stage 4
        opportunities: Opportunities for scanner context
        orchestrator: Pipeline orchestrator
        config: Gate configuration
        persist_results: Whether to save to DynamoDB
        
    Returns:
        Dict mapping evaluation_id to GateEvaluation
    """
    stage = HardGatesStage(
        orchestrator=orchestrator,
        config=config,
    )
    return await stage.execute(
        run_id=run_id,
        evaluations=evaluations,
        feature_sets=feature_sets,
        opportunities=opportunities,
        persist_results=persist_results,
    )


def extract_gate_results_for_decision(
    results: dict[str, GateEvaluation],
) -> dict[str, dict]:
    """Extract gate results in format needed for decision logic.
    
    Args:
        results: Dict mapping evaluation_id to GateEvaluation
        
    Returns:
        Dict mapping evaluation_id to dict with:
            - all_passed: bool
            - failed_gates: list[str]
            - failed_reason_codes: list[str]
    """
    extracted: dict[str, dict] = {}
    
    for evaluation_id, gate_eval in results.items():
        extracted[evaluation_id] = {
            "all_passed": gate_eval.all_passed,
            "failed_gates": gate_eval.failed_gates,
            "failed_reason_codes": gate_eval.failed_reason_codes,
            "passed_count": gate_eval.passed_count,
            "failed_count": gate_eval.failed_count,
        }
    
    return extracted


def get_evaluations_passing_gates(
    evaluations: Sequence[Evaluation],
    gate_results: dict[str, GateEvaluation],
) -> list[Evaluation]:
    """Filter evaluations to only those that passed all gates.
    
    Args:
        evaluations: List of Evaluation records
        gate_results: Gate evaluation results
        
    Returns:
        List of Evaluations that passed all enabled gates
    """
    return [
        eval for eval in evaluations
        if eval.evaluation_id in gate_results
        and gate_results[eval.evaluation_id].all_passed
    ]


def get_evaluations_failing_gates(
    evaluations: Sequence[Evaluation],
    gate_results: dict[str, GateEvaluation],
) -> list[Evaluation]:
    """Filter evaluations to only those that failed at least one gate.
    
    Args:
        evaluations: List of Evaluation records
        gate_results: Gate evaluation results
        
    Returns:
        List of Evaluations that failed at least one enabled gate
    """
    return [
        eval for eval in evaluations
        if eval.evaluation_id in gate_results
        and not gate_results[eval.evaluation_id].all_passed
    ]
