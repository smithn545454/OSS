"""Gate Calculator - Main orchestrator for Stage 6: Hard Gates.

Evaluates all gates for evaluations and produces GateResults.
Any failed enabled gate results in REJECT regardless of pillar scores.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.core.schemas import (
    Evaluation,
    GateConfig,
    GateResult,
    Opportunity,
)
from app.features.models import FeatureSet
from app.gates.models import GateContext, GateEvaluation
from app.gates.gates import (
    ALL_GATES,
    check_min_open_interest,
    check_min_volume,
    check_max_spread_pct,
    check_dte_range,
    check_move_sufficiency,
    check_iv_percentile_max,
    check_breakout_volume,
    check_greeks_coherence,
    check_theta_burden_max,
)

logger = logging.getLogger(__name__)


class GateCalculator:
    """Orchestrates gate evaluation for Stage 6 of the pipeline.
    
    This class coordinates:
    1. Building GateContext from Evaluation, FeatureSet, and Opportunity
    2. Running all enabled gates
    3. Assembling GateEvaluation results
    """
    
    def __init__(
        self,
        config: Optional[GateConfig] = None,
    ) -> None:
        """Initialize the gate calculator.
        
        Args:
            config: Gate configuration with thresholds (optional, uses defaults)
        """
        self._config = config or GateConfig()
    
    def evaluate_gates(
        self,
        evaluation: Evaluation,
        feature_set: Optional[FeatureSet] = None,
        opportunity: Optional[Opportunity] = None,
    ) -> GateEvaluation:
        """Evaluate all gates for a single evaluation.
        
        Args:
            evaluation: Evaluation record from Stage 3
            feature_set: FeatureSet from Stage 4 (optional)
            opportunity: Opportunity with scanner triggers (optional)
            
        Returns:
            GateEvaluation with all gate results
        """
        # Build gate context
        ctx = GateContext.from_evaluation_and_features(
            evaluation=evaluation,
            feature_set=feature_set,
            opportunity=opportunity,
        )
        
        # Run all gates
        results: list[GateResult] = []
        
        disabled = set(self._config.disabled_gates) if self._config.disabled_gates else set()

        for gate_id, gate_func in ALL_GATES:
            if gate_id in disabled:
                results.append(GateResult(
                    evaluation_id=ctx.evaluation_id,
                    gate_id=gate_id,
                    enabled=False,
                    passed=True,
                    measured_value=0.0,
                    threshold_value=0.0,
                    operator="disabled",
                    units="backtest_override",
                    reason_code=f"{gate_id}_DISABLED",
                    notes="Gate disabled via backtest override",
                ))
                continue

            try:
                result = gate_func(ctx, self._config)
                results.append(result)
            except Exception as e:
                logger.error(f"Error in gate {gate_id} for {evaluation.evaluation_id}: {e}")
                # Create a failed result for the error case
                results.append(GateResult(
                    evaluation_id=ctx.evaluation_id,
                    gate_id=gate_id,
                    enabled=True,
                    passed=False,
                    measured_value=0.0,
                    threshold_value=0.0,
                    operator="equals",
                    units="error",
                    reason_code=f"GATE_ERROR_{gate_id.replace('GATE_', '')}",
                    notes=f"Gate evaluation error: {str(e)}",
                ))
        
        return GateEvaluation(
            evaluation_id=evaluation.evaluation_id,
            gate_results=results,
        )
    
    def evaluate_gates_to_results(
        self,
        evaluation: Evaluation,
        feature_set: Optional[FeatureSet] = None,
        opportunity: Optional[Opportunity] = None,
    ) -> list[GateResult]:
        """Evaluate gates and return flat list of GateResults.
        
        Args:
            evaluation: Evaluation record from Stage 3
            feature_set: FeatureSet from Stage 4 (optional)
            opportunity: Opportunity (optional)
            
        Returns:
            List of GateResult objects
        """
        gate_eval = self.evaluate_gates(evaluation, feature_set, opportunity)
        return gate_eval.gate_results
    
    def evaluate_gates_batch(
        self,
        evaluations: Sequence[Evaluation],
        feature_sets: dict[str, FeatureSet],
        opportunities: dict[str, Opportunity],
    ) -> dict[str, GateEvaluation]:
        """Evaluate gates for multiple evaluations.
        
        Args:
            evaluations: List of Evaluation records
            feature_sets: Dict mapping evaluation_id to FeatureSet
            opportunities: Dict mapping underlying_ticker to Opportunity
            
        Returns:
            Dict mapping evaluation_id to GateEvaluation
        """
        results: dict[str, GateEvaluation] = {}
        
        for evaluation in evaluations:
            try:
                feature_set = feature_sets.get(evaluation.evaluation_id)
                opportunity = opportunities.get(evaluation.underlying_ticker)
                
                gate_eval = self.evaluate_gates(
                    evaluation=evaluation,
                    feature_set=feature_set,
                    opportunity=opportunity,
                )
                results[evaluation.evaluation_id] = gate_eval
                
            except Exception as e:
                logger.error(
                    f"Error evaluating gates for {evaluation.evaluation_id}: {e}"
                )
                # Create an empty evaluation with error
                results[evaluation.evaluation_id] = GateEvaluation(
                    evaluation_id=evaluation.evaluation_id,
                    gate_results=[],
                )
        
        # Log summary
        passed_count = sum(1 for ge in results.values() if ge.all_passed)
        failed_count = len(results) - passed_count
        logger.info(
            f"Evaluated gates for {len(results)} evaluations: "
            f"{passed_count} passed all gates, {failed_count} failed"
        )
        
        return results
    
    def evaluate_gates_batch_to_results(
        self,
        evaluations: Sequence[Evaluation],
        feature_sets: dict[str, FeatureSet],
        opportunities: dict[str, Opportunity],
    ) -> list[GateResult]:
        """Evaluate gates for batch and flatten to list.
        
        Args:
            evaluations: List of Evaluation records
            feature_sets: Dict mapping evaluation_id to FeatureSet
            opportunities: Dict mapping underlying_ticker to Opportunity
            
        Returns:
            Flat list of all GateResult objects
        """
        batch_results = self.evaluate_gates_batch(evaluations, feature_sets, opportunities)
        
        all_results: list[GateResult] = []
        for gate_eval in batch_results.values():
            all_results.extend(gate_eval.gate_results)
        
        return all_results
    
    def get_failure_summary(
        self,
        batch_results: dict[str, GateEvaluation],
    ) -> dict[str, int]:
        """Get summary of gate failures across a batch.
        
        Args:
            batch_results: Dict of GateEvaluation results
            
        Returns:
            Dict mapping gate_id to failure count
        """
        failure_counts: dict[str, int] = {}
        
        for gate_eval in batch_results.values():
            for gate_id in gate_eval.failed_gates:
                failure_counts[gate_id] = failure_counts.get(gate_id, 0) + 1
        
        return failure_counts


def evaluate_all_gates(
    evaluation: Evaluation,
    feature_set: Optional[FeatureSet] = None,
    opportunity: Optional[Opportunity] = None,
    config: Optional[GateConfig] = None,
) -> GateEvaluation:
    """Convenience function to evaluate all gates.
    
    Args:
        evaluation: Evaluation record
        feature_set: FeatureSet (optional)
        opportunity: Opportunity (optional)
        config: GateConfig (optional)
        
    Returns:
        GateEvaluation with all gate results
    """
    calculator = GateCalculator(config)
    return calculator.evaluate_gates(evaluation, feature_set, opportunity)


def check_all_gates_passed(
    evaluation: Evaluation,
    feature_set: Optional[FeatureSet] = None,
    opportunity: Optional[Opportunity] = None,
    config: Optional[GateConfig] = None,
) -> bool:
    """Quick check if all gates pass for an evaluation.
    
    Args:
        evaluation: Evaluation record
        feature_set: FeatureSet (optional)
        opportunity: Opportunity (optional)
        config: GateConfig (optional)
        
    Returns:
        True if all enabled gates pass, False otherwise
    """
    gate_eval = evaluate_all_gates(evaluation, feature_set, opportunity, config)
    return gate_eval.all_passed


def get_failed_gates(
    evaluation: Evaluation,
    feature_set: Optional[FeatureSet] = None,
    opportunity: Optional[Opportunity] = None,
    config: Optional[GateConfig] = None,
) -> list[str]:
    """Get list of failed gate IDs for an evaluation.
    
    Args:
        evaluation: Evaluation record
        feature_set: FeatureSet (optional)
        opportunity: Opportunity (optional)
        config: GateConfig (optional)
        
    Returns:
        List of failed gate IDs
    """
    gate_eval = evaluate_all_gates(evaluation, feature_set, opportunity, config)
    return gate_eval.failed_gates
