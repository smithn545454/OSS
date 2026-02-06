"""Threshold sensitivity simulator.

Per Section 20.2 of OSS_Complete_Requirements.md.

Simulates the effect of threshold adjustments:
- +/- 10-20% adjustments to each gate threshold
- Estimates additional approvals/rejections
- Estimates impact on win rate
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.calibration.models import (
    EstimatedImpact,
    GateAnalysis,
    RecommendationType,
    ThresholdSuggestion,
)
from app.core.schemas import Evaluation, GateResult, Policy

logger = logging.getLogger(__name__)


# Gate threshold field mappings
GATE_FIELD_MAPPINGS = {
    "GATE_MIN_OPEN_INTEREST": "gates.min_open_interest",
    "GATE_MIN_VOLUME": "gates.min_volume",
    "GATE_MAX_SPREAD_PCT": "gates.max_spread_pct",
    "GATE_DTE_RANGE": None,  # Two values - handled specially
    "GATE_MOVE_SUFFICIENCY": "gates.move_sufficiency_max",
    "GATE_IV_PERCENTILE_MAX": "gates.iv_percentile_max",
    "GATE_BREAKOUT_VOLUME": "gates.breakout_volume_min",
    "GATE_GREEKS_COHERENCE": None,  # No single threshold
    "GATE_THETA_BURDEN_MAX": "gates.theta_burden_max",
}

# Gate threshold direction
# True = higher threshold is stricter (LOOSEN means decrease)
# False = lower threshold is stricter (LOOSEN means increase)
GATE_HIGHER_IS_STRICTER = {
    "GATE_MIN_OPEN_INTEREST": True,  # Higher min OI = more restrictive
    "GATE_MIN_VOLUME": True,  # Higher min vol = more restrictive
    "GATE_MAX_SPREAD_PCT": False,  # Lower max spread = more restrictive
    "GATE_MOVE_SUFFICIENCY": False,  # Lower max = more restrictive
    "GATE_IV_PERCENTILE_MAX": False,  # Lower max = more restrictive
    "GATE_BREAKOUT_VOLUME": True,  # Higher min = more restrictive
    "GATE_THETA_BURDEN_MAX": False,  # Lower max = more restrictive
}


@dataclass
class SimulationResult:
    """Result of simulating a threshold change."""
    gate_id: str
    current_value: float
    new_value: float
    change_pct: float
    additional_passes: int
    additional_failures: int
    estimated_win_rate_change: float


class ThresholdSimulator:
    """Simulates threshold changes to estimate impact.
    
    Per Section 20.2 - Threshold Sensitivity Analysis:
    - Simulate +/-10-20% adjustments
    - Estimate additional approvals
    - Estimate win rate impact
    """
    
    def __init__(
        self,
        policy: Policy,
        evaluations: list[Evaluation],
        gate_results: list[list[GateResult]],
    ):
        """Initialize simulator with data.
        
        Args:
            policy: Current active policy
            evaluations: List of recent evaluations
            gate_results: Gate results for each evaluation (parallel lists)
        """
        self.policy = policy
        self.evaluations = evaluations
        self.gate_results = gate_results
        
        # Build lookup for gate results by evaluation
        self._gate_results_map: dict[str, list[GateResult]] = {}
        for i, eval in enumerate(evaluations):
            if i < len(gate_results):
                self._gate_results_map[eval.evaluation_id] = gate_results[i]
    
    def simulate_threshold_change(
        self,
        gate_id: str,
        change_pct: float,
    ) -> SimulationResult:
        """Simulate changing a gate threshold.
        
        Args:
            gate_id: The gate to adjust
            change_pct: Percentage change (-20 to +20)
            
        Returns:
            SimulationResult with estimated impact
        """
        field_path = GATE_FIELD_MAPPINGS.get(gate_id)
        if not field_path:
            # Gate doesn't have a single threshold to adjust
            return SimulationResult(
                gate_id=gate_id,
                current_value=0,
                new_value=0,
                change_pct=change_pct,
                additional_passes=0,
                additional_failures=0,
                estimated_win_rate_change=0,
            )
        
        # Get current threshold value
        current_value = self._get_threshold_value(field_path)
        new_value = current_value * (1 + change_pct / 100)
        
        # Count how many evaluations would change outcome
        additional_passes = 0
        additional_failures = 0
        
        for eval_id, gates in self._gate_results_map.items():
            gate_result = next((g for g in gates if g.gate_id == gate_id), None)
            if not gate_result or not gate_result.enabled:
                continue
            
            # Determine if outcome would change with new threshold
            would_pass = self._would_pass_with_threshold(
                gate_result,
                new_value,
                gate_id,
            )
            
            if gate_result.passed and not would_pass:
                additional_failures += 1
            elif not gate_result.passed and would_pass:
                additional_passes += 1
        
        # Estimate win rate impact
        # This is a simplified heuristic - real implementation would use
        # historical correlation between gate passage and winning trades
        estimated_win_rate_change = 0.0
        
        if additional_passes > 0:
            # Adding more evaluations generally slightly decreases win rate
            # as we're including more marginal cases
            estimated_win_rate_change = -0.5 * (additional_passes / len(self.evaluations)) * 100
        elif additional_failures > 0:
            # Removing evaluations generally slightly increases win rate
            # as we're removing marginal cases
            estimated_win_rate_change = 0.3 * (additional_failures / len(self.evaluations)) * 100
        
        return SimulationResult(
            gate_id=gate_id,
            current_value=current_value,
            new_value=new_value,
            change_pct=change_pct,
            additional_passes=additional_passes,
            additional_failures=additional_failures,
            estimated_win_rate_change=estimated_win_rate_change,
        )
    
    def generate_suggestions(
        self,
        gate_analyses: list[GateAnalysis],
    ) -> list[ThresholdSuggestion]:
        """Generate threshold suggestions based on gate analysis.
        
        Args:
            gate_analyses: Analysis results for each gate
            
        Returns:
            List of threshold suggestions
        """
        suggestions = []
        
        for analysis in gate_analyses:
            if analysis.recommendation == RecommendationType.NO_CHANGE:
                continue
            
            field_path = GATE_FIELD_MAPPINGS.get(analysis.gate_id)
            if not field_path:
                continue
            
            # Determine adjustment direction and magnitude
            higher_is_stricter = GATE_HIGHER_IS_STRICTER.get(analysis.gate_id, True)
            
            if analysis.recommendation == RecommendationType.LOOSEN:
                # Make gate less strict
                change_pct = -15 if higher_is_stricter else 15
                reason = f"High false negative rate ({analysis.false_negative_rate:.1f}%) suggests gate is too strict"
            else:  # TIGHTEN
                # Make gate more strict
                change_pct = 15 if higher_is_stricter else -15
                reason = f"Low rejection rate ({analysis.rejection_rate:.1f}%) with good precision allows tightening"
            
            # Simulate the change
            sim_result = self.simulate_threshold_change(analysis.gate_id, change_pct)
            
            # Only suggest if it would have meaningful impact
            if abs(sim_result.additional_passes) + abs(sim_result.additional_failures) < 1:
                continue
            
            suggestion = ThresholdSuggestion.create(
                gate_id=analysis.gate_id,
                field_path=field_path,
                current_value=sim_result.current_value,
                suggested_value=sim_result.new_value,
                estimated_impact=EstimatedImpact(
                    additional_approvals=sim_result.additional_passes,
                    estimated_win_rate_change=sim_result.estimated_win_rate_change,
                ),
                reason=reason,
            )
            suggestions.append(suggestion)
        
        return suggestions
    
    def _get_threshold_value(self, field_path: str) -> float:
        """Get threshold value from policy config.
        
        Args:
            field_path: Dot-separated path (e.g., "gates.min_open_interest")
            
        Returns:
            Current threshold value
        """
        parts = field_path.split(".")
        value = self.policy.config
        
        for part in parts:
            if hasattr(value, part):
                value = getattr(value, part)
            else:
                return 0.0
        
        return float(value) if isinstance(value, (int, float)) else 0.0
    
    def _would_pass_with_threshold(
        self,
        gate_result: GateResult,
        new_threshold: float,
        gate_id: str,
    ) -> bool:
        """Determine if a gate would pass with a new threshold.
        
        Args:
            gate_result: The original gate result
            new_threshold: The new threshold value
            gate_id: The gate ID
            
        Returns:
            True if would pass with new threshold
        """
        measured = gate_result.measured_value
        operator = gate_result.operator
        
        # Apply operator logic
        if operator.value == "gte":
            return measured >= new_threshold
        elif operator.value == "lte":
            return measured <= new_threshold
        elif operator.value == "between":
            # For between, we'd need both min and max
            # Simplify to checking against the modified bound
            return True  # Skip complex between logic
        
        return gate_result.passed
