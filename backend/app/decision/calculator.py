"""Decision Calculator - Core logic for Stage 7: Decision Logic.

Computes final verdicts, quality tiers, and reason codes based on
pillar scores and gate results.

Per Section 16 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.core.schemas import (
    Decision,
    DecisionConfig,
    Evaluation,
    PillarConfig,
    PillarWeights,
    QualityTier,
    Verdict,
)
from app.gates.models import GateEvaluation
from app.pillars.models import PillarResult

logger = logging.getLogger(__name__)


@dataclass
class DecisionContext:
    """Context for decision computation.
    
    Aggregates all inputs needed to produce a Decision.
    """
    
    evaluation_id: str
    underlying_ticker: str
    option_type: str  # "CALL" or "PUT"
    spread_pct: float
    policy_version: str
    
    # Pillar scores (0-100)
    directional_score: float = 50.0
    volatility_score: float = 50.0
    structure_score: float = 50.0
    
    # Gate results
    all_gates_passed: bool = True
    failed_gates: list[str] = field(default_factory=list)
    
    @classmethod
    def from_evaluation_and_results(
        cls,
        evaluation: Evaluation,
        pillar_results: Sequence[PillarResult],
        gate_evaluation: Optional[GateEvaluation] = None,
    ) -> DecisionContext:
        """Build DecisionContext from evaluation, pillars, and gates.
        
        Args:
            evaluation: Evaluation record
            pillar_results: List of PillarResult (directional, volatility, structure)
            gate_evaluation: GateEvaluation with pass/fail results
            
        Returns:
            DecisionContext with all values populated
        """
        # Extract pillar scores
        directional_score = 50.0
        volatility_score = 50.0
        structure_score = 50.0
        
        for pr in pillar_results:
            pillar_id = str(pr.pillar_id)
            if hasattr(pr.pillar_id, 'value'):
                pillar_id = pr.pillar_id.value
            
            if pillar_id == "DIRECTIONAL":
                directional_score = pr.score
            elif pillar_id == "VOLATILITY":
                volatility_score = pr.score
            elif pillar_id == "STRUCTURE":
                structure_score = pr.score
        
        # Extract gate results
        all_gates_passed = True
        failed_gates: list[str] = []
        
        if gate_evaluation:
            all_gates_passed = gate_evaluation.all_passed
            failed_gates = gate_evaluation.failed_gates
        
        return cls(
            evaluation_id=evaluation.evaluation_id,
            underlying_ticker=evaluation.underlying_ticker,
            option_type=evaluation.option_type,
            spread_pct=evaluation.spread_pct,
            policy_version=evaluation.policy_version,
            directional_score=directional_score,
            volatility_score=volatility_score,
            structure_score=structure_score,
            all_gates_passed=all_gates_passed,
            failed_gates=failed_gates,
        )


class DecisionCalculator:
    """Orchestrates decision computation for Stage 7.
    
    Combines pillar scores and gate results to produce final verdicts,
    quality tiers, and reason codes.
    """
    
    def __init__(
        self,
        decision_config: Optional[DecisionConfig] = None,
        pillar_weights: Optional[PillarWeights] = None,
    ) -> None:
        """Initialize the decision calculator.
        
        Args:
            decision_config: Decision threshold configuration
            pillar_weights: Weights for combining pillar scores
        """
        self._config = decision_config or DecisionConfig()
        self._weights = pillar_weights or PillarWeights()
    
    def compute_final_score(
        self,
        directional: float,
        volatility: float,
        structure: float,
    ) -> float:
        """Compute final weighted score from pillar scores.
        
        Per Section 16.1:
        final_score = (0.35 × directional) + (0.35 × volatility) + (0.30 × structure)
        
        Args:
            directional: Directional pillar score (0-100)
            volatility: Volatility pillar score (0-100)
            structure: Structure pillar score (0-100)
            
        Returns:
            Final weighted score (0-100)
        """
        final = (
            self._weights.directional * directional +
            self._weights.volatility * volatility +
            self._weights.structure * structure
        )
        return max(0.0, min(100.0, final))
    
    def determine_verdict(
        self,
        final_score: float,
        all_gates_passed: bool,
    ) -> tuple[Verdict, str]:
        """Determine verdict from score and gate results.
        
        Per Section 16.2:
        - Step 1: Any gate failed → REJECT with REJECTED_BY_GATES
        - Step 2: Apply score bands
          - final_score >= 75 → APPROVE
          - 65 <= final_score < 75 → WATCH
          - final_score < 65 → REJECT
        
        Args:
            final_score: Final weighted score (0-100)
            all_gates_passed: True if all enabled gates passed
            
        Returns:
            Tuple of (Verdict, primary_reason_code)
        """
        # Step 1: Gate failures override score
        if not all_gates_passed:
            return Verdict.REJECT, "REJECTED_BY_GATES"
        
        # Step 2: Apply score bands
        if final_score >= self._config.approve_threshold:
            return Verdict.APPROVE, "APPROVED_BY_SCORE"
        elif final_score >= self._config.watch_threshold:
            return Verdict.WATCH, "WATCH_BY_SCORE"
        else:
            return Verdict.REJECT, "REJECTED_BY_SCORE"
    
    def assign_quality_tier(
        self,
        final_score: float,
        directional: float,
        volatility: float,
        structure: float,
        spread_pct: float,
    ) -> Optional[QualityTier]:
        """Assign quality tier for APPROVE verdicts.
        
        Per Section 16.3:
        - TIER_1: score ≥ 85, all pillars ≥ 70, spread ≤ 5%
        - TIER_2: score ≥ 75, all pillars ≥ 55
        - TIER_3: APPROVE but one pillar < 55
        
        Args:
            final_score: Final weighted score
            directional: Directional pillar score
            volatility: Volatility pillar score
            structure: Structure pillar score
            spread_pct: Contract spread percentage
            
        Returns:
            QualityTier or None if not APPROVE-worthy
        """
        # Must be at least APPROVE threshold to have a tier
        if final_score < self._config.approve_threshold:
            return None
        
        min_pillar = min(directional, volatility, structure)
        
        # TIER_1: score ≥ 85, all pillars ≥ 70, spread ≤ 5%
        if (
            final_score >= self._config.tier_1_min_score
            and min_pillar >= self._config.tier_1_min_pillar
            and spread_pct <= self._config.tier_1_max_spread
        ):
            return QualityTier.TIER_1
        
        # TIER_2: score ≥ 75, all pillars ≥ 55
        if min_pillar >= self._config.tier_2_min_pillar:
            return QualityTier.TIER_2
        
        # TIER_3: APPROVE but one pillar < 55
        return QualityTier.TIER_3
    
    def generate_supporting_reasons(
        self,
        ctx: DecisionContext,
        verdict: Verdict,
        quality_tier: Optional[QualityTier],
    ) -> list[str]:
        """Generate supporting reason codes for the decision.
        
        Args:
            ctx: DecisionContext with all inputs
            verdict: The determined verdict
            quality_tier: Quality tier (for APPROVE only)
            
        Returns:
            List of supporting reason codes
        """
        reasons: list[str] = []
        
        # Add gate failure reasons
        if ctx.failed_gates:
            for gate_id in ctx.failed_gates[:3]:  # Limit to top 3
                reasons.append(f"FAILED_{gate_id}")
        
        # Add score-based reasons
        if verdict == Verdict.APPROVE:
            if ctx.directional_score >= 80:
                reasons.append("STRONG_DIRECTIONAL")
            if ctx.volatility_score >= 80:
                reasons.append("STRONG_VOLATILITY")
            if ctx.structure_score >= 80:
                reasons.append("STRONG_STRUCTURE")
            
            # Quality tier reasons
            if quality_tier == QualityTier.TIER_1:
                reasons.append("EXCEPTIONAL_SETUP")
            elif quality_tier == QualityTier.TIER_3:
                # Identify weak pillar
                if ctx.directional_score < 55:
                    reasons.append("WEAK_DIRECTIONAL")
                if ctx.volatility_score < 55:
                    reasons.append("WEAK_VOLATILITY")
                if ctx.structure_score < 55:
                    reasons.append("WEAK_STRUCTURE")
        
        elif verdict == Verdict.WATCH:
            # Near-approve indicators
            if ctx.directional_score >= 70:
                reasons.append("DECENT_DIRECTIONAL")
            if ctx.volatility_score >= 70:
                reasons.append("DECENT_VOLATILITY")
            if ctx.structure_score >= 70:
                reasons.append("DECENT_STRUCTURE")
            
            # What's holding it back
            if ctx.directional_score < 55:
                reasons.append("WEAK_DIRECTIONAL")
            if ctx.volatility_score < 55:
                reasons.append("WEAK_VOLATILITY")
            if ctx.structure_score < 55:
                reasons.append("WEAK_STRUCTURE")
        
        elif verdict == Verdict.REJECT:
            # Identify rejection reasons beyond gates
            if not ctx.failed_gates:
                if ctx.directional_score < 50:
                    reasons.append("POOR_DIRECTIONAL")
                if ctx.volatility_score < 50:
                    reasons.append("POOR_VOLATILITY")
                if ctx.structure_score < 50:
                    reasons.append("POOR_STRUCTURE")
        
        return reasons
    
    def compute_decision(
        self,
        ctx: DecisionContext,
        concentration_warnings: Optional[list[str]] = None,
    ) -> Decision:
        """Compute final Decision from DecisionContext.
        
        Args:
            ctx: DecisionContext with all inputs
            concentration_warnings: Optional list of concentration warnings
            
        Returns:
            Decision record ready for storage
        """
        # Compute final score
        final_score = self.compute_final_score(
            ctx.directional_score,
            ctx.volatility_score,
            ctx.structure_score,
        )
        
        # Determine verdict
        verdict, primary_reason = self.determine_verdict(
            final_score,
            ctx.all_gates_passed,
        )
        
        # Assign quality tier (only for APPROVE)
        quality_tier = None
        if verdict == Verdict.APPROVE:
            quality_tier = self.assign_quality_tier(
                final_score,
                ctx.directional_score,
                ctx.volatility_score,
                ctx.structure_score,
                ctx.spread_pct,
            )
        
        # Generate supporting reasons
        supporting_reasons = self.generate_supporting_reasons(
            ctx, verdict, quality_tier
        )
        
        return Decision(
            evaluation_id=ctx.evaluation_id,
            verdict=verdict,
            quality_tier=quality_tier,
            final_score=round(final_score, 2),
            directional_score=round(ctx.directional_score, 2),
            volatility_score=round(ctx.volatility_score, 2),
            structure_score=round(ctx.structure_score, 2),
            primary_reason_code=primary_reason,
            supporting_reason_codes=supporting_reasons,
            failed_gates=ctx.failed_gates,
            concentration_warnings=concentration_warnings or [],
            policy_version=ctx.policy_version,
            decided_at=datetime.now(timezone.utc).isoformat(),
        )
    
    def compute_decision_from_evaluation(
        self,
        evaluation: Evaluation,
        pillar_results: Sequence[PillarResult],
        gate_evaluation: Optional[GateEvaluation] = None,
        concentration_warnings: Optional[list[str]] = None,
    ) -> Decision:
        """Convenience method to compute Decision from raw inputs.
        
        Args:
            evaluation: Evaluation record
            pillar_results: List of PillarResult
            gate_evaluation: GateEvaluation (optional)
            concentration_warnings: List of warnings (optional)
            
        Returns:
            Decision record
        """
        ctx = DecisionContext.from_evaluation_and_results(
            evaluation=evaluation,
            pillar_results=pillar_results,
            gate_evaluation=gate_evaluation,
        )
        return self.compute_decision(ctx, concentration_warnings)
    
    def compute_decisions_batch(
        self,
        evaluations: Sequence[Evaluation],
        pillar_results: dict[str, Sequence[PillarResult]],
        gate_evaluations: dict[str, GateEvaluation],
        concentration_warnings: Optional[dict[str, list[str]]] = None,
    ) -> dict[str, Decision]:
        """Compute decisions for multiple evaluations.
        
        Args:
            evaluations: List of Evaluation records
            pillar_results: Dict mapping evaluation_id to PillarResults
            gate_evaluations: Dict mapping evaluation_id to GateEvaluation
            concentration_warnings: Dict mapping evaluation_id to warnings
            
        Returns:
            Dict mapping evaluation_id to Decision
        """
        decisions: dict[str, Decision] = {}
        warnings = concentration_warnings or {}
        
        for evaluation in evaluations:
            try:
                eval_id = evaluation.evaluation_id
                
                pillars = pillar_results.get(eval_id, [])
                gate_eval = gate_evaluations.get(eval_id)
                eval_warnings = warnings.get(eval_id, [])
                
                decision = self.compute_decision_from_evaluation(
                    evaluation=evaluation,
                    pillar_results=pillars,
                    gate_evaluation=gate_eval,
                    concentration_warnings=eval_warnings,
                )
                decisions[eval_id] = decision
                
            except Exception as e:
                logger.error(f"Error computing decision for {evaluation.evaluation_id}: {e}")
                continue
        
        # Log summary
        approves = sum(1 for d in decisions.values() if d.verdict == Verdict.APPROVE)
        watches = sum(1 for d in decisions.values() if d.verdict == Verdict.WATCH)
        rejects = sum(1 for d in decisions.values() if d.verdict == Verdict.REJECT)
        
        logger.info(
            f"Computed {len(decisions)} decisions: "
            f"{approves} APPROVE, {watches} WATCH, {rejects} REJECT"
        )
        
        return decisions


# Convenience functions


def compute_decision(
    evaluation: Evaluation,
    pillar_results: Sequence[PillarResult],
    gate_evaluation: Optional[GateEvaluation] = None,
    decision_config: Optional[DecisionConfig] = None,
    pillar_weights: Optional[PillarWeights] = None,
    concentration_warnings: Optional[list[str]] = None,
) -> Decision:
    """Convenience function to compute a single decision.
    
    Args:
        evaluation: Evaluation record
        pillar_results: List of PillarResult
        gate_evaluation: GateEvaluation (optional)
        decision_config: Decision thresholds (optional)
        pillar_weights: Pillar weights (optional)
        concentration_warnings: Concentration warnings (optional)
        
    Returns:
        Decision record
    """
    calculator = DecisionCalculator(decision_config, pillar_weights)
    return calculator.compute_decision_from_evaluation(
        evaluation=evaluation,
        pillar_results=pillar_results,
        gate_evaluation=gate_evaluation,
        concentration_warnings=concentration_warnings,
    )


def determine_verdict(
    final_score: float,
    all_gates_passed: bool,
    decision_config: Optional[DecisionConfig] = None,
) -> tuple[Verdict, str]:
    """Convenience function to determine verdict.
    
    Args:
        final_score: Final weighted score
        all_gates_passed: True if all gates passed
        decision_config: Decision thresholds (optional)
        
    Returns:
        Tuple of (Verdict, primary_reason_code)
    """
    calculator = DecisionCalculator(decision_config)
    return calculator.determine_verdict(final_score, all_gates_passed)


def assign_quality_tier(
    final_score: float,
    directional: float,
    volatility: float,
    structure: float,
    spread_pct: float,
    decision_config: Optional[DecisionConfig] = None,
) -> Optional[QualityTier]:
    """Convenience function to assign quality tier.
    
    Args:
        final_score: Final weighted score
        directional: Directional pillar score
        volatility: Volatility pillar score
        structure: Structure pillar score
        spread_pct: Contract spread percentage
        decision_config: Decision thresholds (optional)
        
    Returns:
        QualityTier or None
    """
    calculator = DecisionCalculator(decision_config)
    return calculator.assign_quality_tier(
        final_score, directional, volatility, structure, spread_pct
    )
