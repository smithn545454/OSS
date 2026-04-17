"""Decision Calculator - Stage 7: Decision Logic (Policy v3.0.0).

Computes final verdicts, quality tiers, and reason codes based on
pillar scores and gate results. Uses the new three-pillar system:
Premium Leverage, Underlying Behavior, Setup Quality.
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
    PillarId,
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

    Aggregates all inputs needed to produce a Decision (v3.0.0 pillars).
    """

    evaluation_id: str
    underlying_ticker: str
    option_type: str  # "CALL" or "PUT"
    spread_pct: float
    policy_version: str

    # Pillar scores (0-100) — Policy v3.0.0 naming
    premium_leverage_score: float = 50.0
    underlying_behavior_score: float = 50.0
    setup_quality_score: float = 50.0

    # Gate results
    all_gates_passed: bool = True
    failed_gates: list[str] = field(default_factory=list)

    # Scanner source for per-scanner weight lookup
    scanner_source: Optional[str] = None

    @classmethod
    def from_evaluation_and_results(
        cls,
        evaluation: Evaluation,
        pillar_results: Sequence[PillarResult],
        gate_evaluation: Optional[GateEvaluation] = None,
    ) -> "DecisionContext":
        """Build DecisionContext from evaluation, pillars, and gates."""
        pl_score = 50.0
        ub_score = 50.0
        sq_score = 50.0

        for pr in pillar_results:
            pillar_id = str(pr.pillar_id)
            if hasattr(pr.pillar_id, "value"):
                pillar_id = pr.pillar_id.value

            if pillar_id == PillarId.PREMIUM_LEVERAGE.value or pillar_id == "PREMIUM_LEVERAGE":
                pl_score = pr.score
            elif (
                pillar_id == PillarId.UNDERLYING_BEHAVIOR.value
                or pillar_id == "UNDERLYING_BEHAVIOR"
            ):
                ub_score = pr.score
            elif pillar_id == PillarId.SETUP_QUALITY.value or pillar_id == "SETUP_QUALITY":
                sq_score = pr.score

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
            premium_leverage_score=pl_score,
            underlying_behavior_score=ub_score,
            setup_quality_score=sq_score,
            all_gates_passed=all_gates_passed,
            failed_gates=failed_gates,
            scanner_source=evaluation.scanner_source,
        )


class DecisionCalculator:
    """Orchestrates decision computation for Stage 7 (Policy v3.0.0)."""

    def __init__(
        self,
        decision_config: Optional[DecisionConfig] = None,
        pillar_weights: Optional[PillarWeights] = None,
        pillar_config: Optional["PillarConfig"] = None,
    ) -> None:
        """Initialize the decision calculator.

        Args:
            decision_config: Decision threshold configuration
            pillar_weights: Weights for combining pillar scores. If None,
                uses PillarWeights.v3_default() (v3 baseline). Transitional
                fallback — remove at Phase 9 alongside v3 code.
            pillar_config: Full PillarConfig for per-scanner weight lookup.
                When provided, scanner_source on DecisionContext selects
                the appropriate weights. Falls back to pillar_weights/global.
        """
        self._config = decision_config or DecisionConfig()
        self._weights = pillar_weights or PillarWeights.v3_default()
        self._pillar_config = pillar_config

    def compute_final_score(
        self,
        premium_leverage: float,
        underlying_behavior: float,
        setup_quality: float,
        scanner_source: Optional[str] = None,
    ) -> float:
        """Compute final weighted composite score.

        When pillar_config has per-scanner overrides and scanner_source is
        provided, uses scanner-specific weights. Otherwise falls back to
        the global weights passed at init.
        """
        if self._pillar_config:
            weights = self._pillar_config.get_weights(scanner_source)
        else:
            weights = self._weights
        final = (
            (weights.premium_leverage or 0.0) * premium_leverage
            + (weights.underlying_behavior or 0.0) * underlying_behavior
            + (weights.setup_quality or 0.0) * setup_quality
        )
        return max(0.0, min(100.0, final))

    def determine_verdict(
        self,
        final_score: float,
        all_gates_passed: bool,
    ) -> tuple[Verdict, str]:
        """Determine verdict from score and gate results.

        - Any gate failed → REJECT with REJECTED_BY_GATES
        - final_score >= approve_threshold → APPROVE
        - final_score >= watch_threshold → WATCH
        - otherwise → REJECT
        """
        if not all_gates_passed:
            return Verdict.REJECT, "REJECTED_BY_GATES"

        if final_score >= self._config.approve_threshold:
            return Verdict.APPROVE, "APPROVED_BY_SCORE"
        if final_score >= self._config.watch_threshold:
            return Verdict.WATCH, "WATCH_BY_SCORE"
        return Verdict.REJECT, "REJECTED_BY_SCORE"

    def assign_quality_tier(
        self,
        final_score: float,
        premium_leverage: float,
        underlying_behavior: float,
        setup_quality: float,
        spread_pct: float,
    ) -> Optional[QualityTier]:
        """Assign quality tier for APPROVE verdicts.

        - TIER_1: score ≥ tier_1_min_score, all pillars ≥ tier_1_min_pillar, spread ≤ tier_1_max_spread
        - TIER_2: all pillars ≥ tier_2_min_pillar
        - TIER_3: APPROVE but one pillar below tier_2_min_pillar
        """
        if final_score < self._config.approve_threshold:
            return None

        min_pillar = min(premium_leverage, underlying_behavior, setup_quality)

        if (
            final_score >= self._config.tier_1_min_score
            and min_pillar >= self._config.tier_1_min_pillar
            and spread_pct <= self._config.tier_1_max_spread
        ):
            return QualityTier.TIER_1

        if min_pillar >= self._config.tier_2_min_pillar:
            return QualityTier.TIER_2

        return QualityTier.TIER_3

    def generate_supporting_reasons(
        self,
        ctx: DecisionContext,
        verdict: Verdict,
        quality_tier: Optional[QualityTier],
    ) -> list[str]:
        """Generate supporting reason codes for the decision (v3.0.0)."""
        reasons: list[str] = []

        if ctx.failed_gates:
            for gate_id in ctx.failed_gates[:3]:
                reasons.append(f"FAILED_{gate_id}")

        if verdict == Verdict.APPROVE:
            if ctx.premium_leverage_score >= 80:
                reasons.append("STRONG_PREMIUM_LEVERAGE")
            if ctx.underlying_behavior_score >= 80:
                reasons.append("STRONG_UNDERLYING_BEHAVIOR")
            if ctx.setup_quality_score >= 80:
                reasons.append("STRONG_SETUP_QUALITY")

            if quality_tier == QualityTier.TIER_1:
                reasons.append("EXCEPTIONAL_SETUP")
            elif quality_tier == QualityTier.TIER_3:
                if ctx.premium_leverage_score < 55:
                    reasons.append("WEAK_PREMIUM_LEVERAGE")
                if ctx.underlying_behavior_score < 55:
                    reasons.append("WEAK_UNDERLYING_BEHAVIOR")
                if ctx.setup_quality_score < 55:
                    reasons.append("WEAK_SETUP_QUALITY")

        elif verdict == Verdict.WATCH:
            if ctx.premium_leverage_score >= 70:
                reasons.append("DECENT_PREMIUM_LEVERAGE")
            if ctx.underlying_behavior_score >= 70:
                reasons.append("DECENT_UNDERLYING_BEHAVIOR")
            if ctx.setup_quality_score >= 70:
                reasons.append("DECENT_SETUP_QUALITY")

            if ctx.premium_leverage_score < 55:
                reasons.append("WEAK_PREMIUM_LEVERAGE")
            if ctx.underlying_behavior_score < 55:
                reasons.append("WEAK_UNDERLYING_BEHAVIOR")
            if ctx.setup_quality_score < 55:
                reasons.append("WEAK_SETUP_QUALITY")

        elif verdict == Verdict.REJECT:
            if not ctx.failed_gates:
                if ctx.premium_leverage_score < 50:
                    reasons.append("POOR_PREMIUM_LEVERAGE")
                if ctx.underlying_behavior_score < 50:
                    reasons.append("POOR_UNDERLYING_BEHAVIOR")
                if ctx.setup_quality_score < 50:
                    reasons.append("POOR_SETUP_QUALITY")

        return reasons

    def compute_decision(
        self,
        ctx: DecisionContext,
        concentration_warnings: Optional[list[str]] = None,
    ) -> Decision:
        """Compute final Decision from DecisionContext."""
        final_score = self.compute_final_score(
            ctx.premium_leverage_score,
            ctx.underlying_behavior_score,
            ctx.setup_quality_score,
            scanner_source=ctx.scanner_source,
        )

        verdict, primary_reason = self.determine_verdict(
            final_score,
            ctx.all_gates_passed,
        )

        quality_tier = None
        if verdict == Verdict.APPROVE:
            quality_tier = self.assign_quality_tier(
                final_score,
                ctx.premium_leverage_score,
                ctx.underlying_behavior_score,
                ctx.setup_quality_score,
                ctx.spread_pct,
            )

        supporting_reasons = self.generate_supporting_reasons(
            ctx, verdict, quality_tier
        )

        return Decision(
            evaluation_id=ctx.evaluation_id,
            verdict=verdict,
            quality_tier=quality_tier,
            final_score=round(final_score, 2),
            premium_leverage_score=round(ctx.premium_leverage_score, 2),
            underlying_behavior_score=round(ctx.underlying_behavior_score, 2),
            setup_quality_score=round(ctx.setup_quality_score, 2),
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

        approves = sum(1 for d in decisions.values() if d.verdict == Verdict.APPROVE)
        watches = sum(1 for d in decisions.values() if d.verdict == Verdict.WATCH)
        rejects = sum(1 for d in decisions.values() if d.verdict == Verdict.REJECT)

        logger.info(
            f"Computed {len(decisions)} decisions: "
            f"{approves} APPROVE, {watches} WATCH, {rejects} REJECT"
        )

        return decisions


# ============================================================================
# Convenience functions
# ============================================================================


def compute_decision(
    evaluation: Evaluation,
    pillar_results: Sequence[PillarResult],
    gate_evaluation: Optional[GateEvaluation] = None,
    decision_config: Optional[DecisionConfig] = None,
    pillar_weights: Optional[PillarWeights] = None,
    concentration_warnings: Optional[list[str]] = None,
) -> Decision:
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
    calculator = DecisionCalculator(decision_config)
    return calculator.determine_verdict(final_score, all_gates_passed)


def assign_quality_tier(
    final_score: float,
    premium_leverage: float,
    underlying_behavior: float,
    setup_quality: float,
    spread_pct: float,
    decision_config: Optional[DecisionConfig] = None,
) -> Optional[QualityTier]:
    calculator = DecisionCalculator(decision_config)
    return calculator.assign_quality_tier(
        final_score, premium_leverage, underlying_behavior, setup_quality, spread_pct
    )
