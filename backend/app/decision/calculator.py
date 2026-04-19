"""Decision Calculator - Stage 7: Decision Logic.

Computes final verdicts, quality tiers, and reason codes based on pillar
scores and gate results. Works for both v3 (Premium Leverage / Underlying
Behavior / Setup Quality) and v4 (Directional Conviction / Move Potential /
Trade Structure) regimes — selection is driven by the ``PillarConfig``
handed in by the caller.
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
from app.pillars.composite import weighted_geometric_mean, weighted_sum
from app.pillars.models import PillarResult

logger = logging.getLogger(__name__)


@dataclass
class DecisionContext:
    """Context for decision computation.

    Carries both regime's pillar scores as Optional floats so v3 and v4
    decisions flow through the same code path. The regime is determined
    by which of the two sets is populated.
    """

    evaluation_id: str
    underlying_ticker: str
    option_type: str  # "CALL" or "PUT"
    spread_pct: float
    policy_version: str

    # v3 pillar scores (Policy v3.x)
    premium_leverage_score: Optional[float] = None
    underlying_behavior_score: Optional[float] = None
    setup_quality_score: Optional[float] = None

    # v4 pillar scores (Policy v4.x)
    directional_conviction_score: Optional[float] = None
    move_potential_score: Optional[float] = None
    trade_structure_score: Optional[float] = None

    # Gate results
    all_gates_passed: bool = True
    failed_gates: list[str] = field(default_factory=list)

    # Scanner source for per-scanner weight lookup
    scanner_source: Optional[str] = None

    # v4.1.0: pre-computed archetype matcher output (orchestrator populates
    # these when PolicyConfig.archetypes/anti_archetypes is configured).
    archetype_matched: Optional[str] = None
    archetype_match_score: Optional[float] = None
    archetype_all_fits: Optional[dict[str, float]] = None
    anti_archetype_triggered: Optional[str] = None

    def is_v4(self) -> bool:
        """True when all three v4 pillar scores are populated."""
        return (
            self.directional_conviction_score is not None
            and self.move_potential_score is not None
            and self.trade_structure_score is not None
        )

    def pillar_triplet(self) -> tuple[float, float, float]:
        """Return the active regime's (p1, p2, p3) scores in registry order.

        v3 → (premium_leverage, underlying_behavior, setup_quality)
        v4 → (directional_conviction, move_potential, trade_structure)

        Missing (None) scores default to 50 (neutral). A zero score is
        preserved — v4 uses 0 as the insufficient-data sentinel that
        collapses the geometric-mean composite to 0.
        """
        def _default_if_none(x: Optional[float]) -> float:
            return 50.0 if x is None else x

        if self.is_v4():
            return (
                _default_if_none(self.directional_conviction_score),
                _default_if_none(self.move_potential_score),
                _default_if_none(self.trade_structure_score),
            )
        return (
            _default_if_none(self.premium_leverage_score),
            _default_if_none(self.underlying_behavior_score),
            _default_if_none(self.setup_quality_score),
        )

    @classmethod
    def from_evaluation_and_results(
        cls,
        evaluation: Evaluation,
        pillar_results: Sequence[PillarResult],
        gate_evaluation: Optional[GateEvaluation] = None,
    ) -> "DecisionContext":
        """Build DecisionContext from evaluation, pillars, and gates."""
        scores_by_id: dict[PillarId, float] = {}
        for pr in pillar_results:
            pid = pr.pillar_id
            if isinstance(pid, str):
                try:
                    pid = PillarId(pid)
                except ValueError:
                    continue
            scores_by_id[pid] = pr.score

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
            premium_leverage_score=scores_by_id.get(PillarId.PREMIUM_LEVERAGE),
            underlying_behavior_score=scores_by_id.get(PillarId.UNDERLYING_BEHAVIOR),
            setup_quality_score=scores_by_id.get(PillarId.SETUP_QUALITY),
            directional_conviction_score=scores_by_id.get(PillarId.DIRECTIONAL_CONVICTION),
            move_potential_score=scores_by_id.get(PillarId.MOVE_POTENTIAL),
            trade_structure_score=scores_by_id.get(PillarId.TRADE_STRUCTURE),
            all_gates_passed=all_gates_passed,
            failed_gates=failed_gates,
            scanner_source=evaluation.scanner_source,
        )


class DecisionCalculator:
    """Orchestrates decision computation for Stage 7 (v3 + v4)."""

    def __init__(
        self,
        decision_config: Optional[DecisionConfig] = None,
        pillar_weights: Optional[PillarWeights] = None,
        pillar_config: Optional["PillarConfig"] = None,
    ) -> None:
        """Initialize the decision calculator.

        Args:
            decision_config: Decision threshold configuration.
            pillar_weights: Weights for combining pillar scores. If None,
                uses PillarWeights.v3_default(). Transitional fallback —
                remove at Phase 9 alongside v3 code.
            pillar_config: Full PillarConfig for per-scanner weight lookup
                and composite-formula dispatch. When provided, it drives
                everything — both the regime detection and the formula.
                Falls back to pillar_weights / weighted_sum when absent.
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
        """Compute final weighted composite score (v3 positional API).

        Kept with the original signature so v3 callers and tests don't
        break during the v3→v4 transition. v4 callers use
        :meth:`compute_final_score_from_results`. Marked for Phase 9
        removal along with v3.
        """
        weights = self._get_weights(scanner_source)
        final = (
            (weights.premium_leverage or 0.0) * premium_leverage
            + (weights.underlying_behavior or 0.0) * underlying_behavior
            + (weights.setup_quality or 0.0) * setup_quality
        )
        return max(0.0, min(100.0, final))

    def compute_final_score_from_results(
        self,
        pillar_results: Sequence[PillarResult],
        scanner_source: Optional[str] = None,
    ) -> float:
        """Compute final composite from pillar results (v3 or v4).

        Dispatches on :attr:`PillarConfig.composite_formula` — arithmetic
        weighted sum for v3, weighted geometric mean for v4. This is the
        preferred API once the decision layer stops carrying three named
        v3 scores.
        """
        weights = self._get_weights(scanner_source)
        formula = (
            self._pillar_config.composite_formula if self._pillar_config else "weighted_sum"
        )
        if formula == "weighted_geometric_mean":
            return weighted_geometric_mean(pillar_results, weights)
        return weighted_sum(pillar_results, weights)

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
        premium_leverage: Optional[float] = None,
        underlying_behavior: Optional[float] = None,
        setup_quality: Optional[float] = None,
        spread_pct: float = 0.0,
        *,
        directional_conviction: Optional[float] = None,
        move_potential: Optional[float] = None,
        trade_structure: Optional[float] = None,
        archetype_match_score: Optional[float] = None,
    ) -> Optional[QualityTier]:
        """Assign quality tier for APPROVE verdicts.

        Accepts either the v3 positional triplet (``premium_leverage``,
        ``underlying_behavior``, ``setup_quality``) or the v4 kwargs
        (``directional_conviction``, ``move_potential``, ``trade_structure``).
        When both are provided, v4 wins. At least one regime's trio must
        be supplied; the triplet then feeds the uniform tier rules.

        - TIER_1: score ≥ tier_1_min_score, all pillars ≥ tier_1_min_pillar,
          spread ≤ tier_1_max_spread
        - TIER_2: all pillars ≥ tier_2_min_pillar
        - TIER_3: APPROVE but one pillar below tier_2_min_pillar
        """
        if final_score < self._config.approve_threshold:
            return None

        v4_trio = (directional_conviction, move_potential, trade_structure)
        v3_trio = (premium_leverage, underlying_behavior, setup_quality)
        if all(x is not None for x in v4_trio):
            p1, p2, p3 = v4_trio  # type: ignore[misc]
        elif all(x is not None for x in v3_trio):
            p1, p2, p3 = v3_trio  # type: ignore[misc]
        else:
            raise TypeError(
                "assign_quality_tier requires either the v3 trio "
                "(premium_leverage, underlying_behavior, setup_quality) "
                "or the v4 kwargs "
                "(directional_conviction, move_potential, trade_structure)."
            )
        min_pillar = min(p1, p2, p3)

        composite_tier_1 = (
            final_score >= self._config.tier_1_min_score
            and min_pillar >= self._config.tier_1_min_pillar
            and spread_pct <= self._config.tier_1_max_spread
        )
        # v4.1.0: archetype match can promote to TIER_1/TIER_2 independently
        # of the pillar composite. Only triggers when a match score is
        # provided (None on v3 or pre-v4.1.0 paths).
        archetype_tier_1 = (
            archetype_match_score is not None
            and archetype_match_score >= self._config.archetype_tier_1_threshold
        )
        archetype_tier_2 = (
            archetype_match_score is not None
            and archetype_match_score >= self._config.archetype_tier_2_threshold
        )

        if composite_tier_1 or archetype_tier_1:
            return QualityTier.TIER_1

        if min_pillar >= self._config.tier_2_min_pillar or archetype_tier_2:
            return QualityTier.TIER_2

        return QualityTier.TIER_3

    def generate_supporting_reasons(
        self,
        ctx: DecisionContext,
        verdict: Verdict,
        quality_tier: Optional[QualityTier],
    ) -> list[str]:
        """Generate supporting reason codes.

        Emits v3 or v4 reason codes based on which regime's scores are
        populated on the context. v3 uses PREMIUM_LEVERAGE / UNDERLYING_BEHAVIOR /
        SETUP_QUALITY labels; v4 uses DIRECTIONAL_CONVICTION / MOVE_POTENTIAL /
        TRADE_STRUCTURE.
        """
        reasons: list[str] = []

        if ctx.failed_gates:
            for gate_id in ctx.failed_gates[:3]:
                reasons.append(f"FAILED_{gate_id}")

        pillar_labels = _PILLAR_LABELS_V4 if ctx.is_v4() else _PILLAR_LABELS_V3
        p1, p2, p3 = ctx.pillar_triplet()

        if verdict == Verdict.APPROVE:
            for score, label in zip((p1, p2, p3), pillar_labels):
                if score >= 80:
                    reasons.append(f"STRONG_{label}")

            if quality_tier == QualityTier.TIER_1:
                reasons.append("SHARPSHOOTER_SETUP" if ctx.is_v4() else "EXCEPTIONAL_SETUP")
            elif quality_tier == QualityTier.TIER_3:
                for score, label in zip((p1, p2, p3), pillar_labels):
                    if score < 55:
                        reasons.append(f"WEAK_{label}")

        elif verdict == Verdict.WATCH:
            for score, label in zip((p1, p2, p3), pillar_labels):
                if score >= 70:
                    reasons.append(f"DECENT_{label}")
                elif score < 55:
                    reasons.append(f"WEAK_{label}")

        elif verdict == Verdict.REJECT:
            if not ctx.failed_gates:
                for score, label in zip((p1, p2, p3), pillar_labels):
                    if score < 50:
                        reasons.append(f"POOR_{label}")
                # v4 insufficient-data signal (pillar score == 0 via geometric mean).
                if ctx.is_v4():
                    for score, label in zip((p1, p2, p3), pillar_labels):
                        if score == 0:
                            reasons.append(f"INSUFFICIENT_DATA_{label}")

        return reasons

    def compute_decision(
        self,
        ctx: DecisionContext,
        concentration_warnings: Optional[list[str]] = None,
    ) -> Decision:
        """Compute final Decision from DecisionContext.

        Works for both v3 and v4 contexts. For v4, the composite uses
        weighted geometric mean; v3 uses arithmetic sum.
        """
        p1, p2, p3 = ctx.pillar_triplet()

        final_score = self._compose_final_score(
            ctx=ctx,
            pillar_triplet=(p1, p2, p3),
            scanner_source=ctx.scanner_source,
        )

        verdict, primary_reason = self.determine_verdict(
            final_score,
            ctx.all_gates_passed,
        )

        # v4.1.0: anti-archetype short-circuit. When an anti-archetype has
        # fired (populated upstream by the orchestrator), force REJECT
        # regardless of score or gate status.
        if ctx.anti_archetype_triggered:
            verdict = Verdict.REJECT
            primary_reason = f"ANTI_ARCHETYPE_{ctx.anti_archetype_triggered}"

        quality_tier = None
        if verdict == Verdict.APPROVE:
            if ctx.is_v4():
                quality_tier = self.assign_quality_tier(
                    final_score,
                    spread_pct=ctx.spread_pct,
                    directional_conviction=p1,
                    move_potential=p2,
                    trade_structure=p3,
                    archetype_match_score=ctx.archetype_match_score,
                )
            else:
                quality_tier = self.assign_quality_tier(
                    final_score,
                    premium_leverage=p1,
                    underlying_behavior=p2,
                    setup_quality=p3,
                    spread_pct=ctx.spread_pct,
                )

        supporting_reasons = self.generate_supporting_reasons(
            ctx, verdict, quality_tier
        )

        # Every pillar-score field on Decision is Optional[float]. The
        # inactive regime's scores are None; the active regime's scores
        # round normally. Consumers distinguish regimes by which trio is
        # non-None.
        return Decision(
            evaluation_id=ctx.evaluation_id,
            verdict=verdict,
            quality_tier=quality_tier,
            final_score=round(final_score, 2),
            premium_leverage_score=_round_opt(ctx.premium_leverage_score),
            underlying_behavior_score=_round_opt(ctx.underlying_behavior_score),
            setup_quality_score=_round_opt(ctx.setup_quality_score),
            directional_conviction_score=_round_opt(ctx.directional_conviction_score),
            move_potential_score=_round_opt(ctx.move_potential_score),
            trade_structure_score=_round_opt(ctx.trade_structure_score),
            primary_reason_code=primary_reason,
            supporting_reason_codes=supporting_reasons,
            failed_gates=ctx.failed_gates,
            concentration_warnings=concentration_warnings or [],
            policy_version=ctx.policy_version,
            decided_at=datetime.now(timezone.utc).isoformat(),
            archetype_matched=ctx.archetype_matched,
            archetype_match_score=_round_opt(ctx.archetype_match_score),
            archetype_all_fits=ctx.archetype_all_fits,
            anti_archetype_triggered=ctx.anti_archetype_triggered,
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

    # --- internal helpers --------------------------------------------

    def _get_weights(self, scanner_source: Optional[str]) -> PillarWeights:
        if self._pillar_config:
            return self._pillar_config.get_weights(scanner_source)
        return self._weights

    def _compose_final_score(
        self,
        ctx: DecisionContext,
        pillar_triplet: tuple[float, float, float],
        scanner_source: Optional[str],
    ) -> float:
        """Compose the final score using the active regime's formula."""
        weights = self._get_weights(scanner_source)
        formula = (
            self._pillar_config.composite_formula if self._pillar_config else "weighted_sum"
        )

        # Rebuild pseudo pillar_results carrying just the scores — enough for
        # both composite formulas. Avoids recomputing downstream.
        from app.pillars.models import PillarResult  # local import to sidestep cycles

        if ctx.is_v4():
            pillar_ids = [
                PillarId.DIRECTIONAL_CONVICTION,
                PillarId.MOVE_POTENTIAL,
                PillarId.TRADE_STRUCTURE,
            ]
        else:
            pillar_ids = [
                PillarId.PREMIUM_LEVERAGE,
                PillarId.UNDERLYING_BEHAVIOR,
                PillarId.SETUP_QUALITY,
            ]

        results = [
            PillarResult(pillar_id=pid, evaluation_id=ctx.evaluation_id, score=score)
            for pid, score in zip(pillar_ids, pillar_triplet)
        ]

        if formula == "weighted_geometric_mean":
            return weighted_geometric_mean(results, weights)
        return weighted_sum(results, weights)


# ============================================================================
# Helpers
# ============================================================================


_PILLAR_LABELS_V3: tuple[str, str, str] = (
    "PREMIUM_LEVERAGE",
    "UNDERLYING_BEHAVIOR",
    "SETUP_QUALITY",
)

_PILLAR_LABELS_V4: tuple[str, str, str] = (
    "DIRECTIONAL_CONVICTION",
    "MOVE_POTENTIAL",
    "TRADE_STRUCTURE",
)


def _round_opt(value: Optional[float], ndigits: int = 2) -> Optional[float]:
    return round(value, ndigits) if value is not None else None


# ============================================================================
# Convenience functions (module-level)
# ============================================================================


def compute_decision(
    evaluation: Evaluation,
    pillar_results: Sequence[PillarResult],
    gate_evaluation: Optional[GateEvaluation] = None,
    decision_config: Optional[DecisionConfig] = None,
    pillar_weights: Optional[PillarWeights] = None,
    pillar_config: Optional[PillarConfig] = None,
    concentration_warnings: Optional[list[str]] = None,
) -> Decision:
    calculator = DecisionCalculator(decision_config, pillar_weights, pillar_config)
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
    """Legacy v3 positional tier assignment. Marked for Phase 9 removal."""
    calculator = DecisionCalculator(decision_config)
    return calculator.assign_quality_tier(
        final_score, premium_leverage, underlying_behavior, setup_quality, spread_pct
    )
