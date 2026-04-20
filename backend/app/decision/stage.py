"""Stage 7: Decision Logic Pipeline Stage.

Integrates decision computation into the pipeline orchestration.
Per Section 16 of OSS_Complete_Requirements.md.

Also includes Phase 10 LLM integration for thesis generation on APPROVE verdicts.
Per Section 21 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from app.core.pipeline import PipelineOrchestrator
from app.core.schemas import (
    AntiArchetypeConfig,
    ArchetypeConfig,
    Decision,
    DecisionConfig,
    Evaluation,
    PillarConfig,
    PillarWeights,
    PipelineStage,
    ScannerTrigger,
    ThesisConfig,
    TradeThesis,
    Verdict,
)
from app.db.tables import EvaluationTable, TradeThesisTable
from app.decision.calculator import DecisionCalculator
from app.decision.concentration import (
    analyze_concentration,
    check_concentration_warnings,
    update_decisions_with_warnings,
)
from app.gates.models import GateEvaluation
from app.pillars.models import PillarResult

logger = logging.getLogger(__name__)


class DecisionStage:
    """Stage 7: Decision Logic.
    
    Takes pillar scores and gate results from previous stages and produces
    final verdicts (APPROVE/WATCH/REJECT), quality tiers, and concentration warnings.
    
    This is the culmination of the evaluation pipeline - every evaluation
    receives a final decision that determines its fate.
    
    Also handles Phase 10 LLM integration: generates trade theses for APPROVE verdicts.
    """

    def __init__(
        self,
        orchestrator: PipelineOrchestrator,
        decision_config: Optional[DecisionConfig] = None,
        pillar_weights: Optional[PillarWeights] = None,
        thesis_config: Optional[ThesisConfig] = None,
        pillar_config: Optional[PillarConfig] = None,
        archetypes_config: Optional[ArchetypeConfig] = None,
        anti_archetypes_config: Optional[AntiArchetypeConfig] = None,
        v5_policy: Optional[Any] = None,
    ) -> None:
        """Initialize the decision stage.

        Args:
            orchestrator: Pipeline orchestrator for event tracking
            decision_config: Decision threshold configuration
            pillar_weights: Weights for combining pillar scores
            thesis_config: LLM thesis generation configuration
            pillar_config: Full PillarConfig for per-scanner weight lookup
            v5_policy: Full :class:`PolicyConfig` for v5 settings. When
                non-None and ``v5_policy.v5_active=True``, the stage
                computes a :class:`V5Envelope` per evaluation and
                threads it into the decision. Shadow-only unless the
                scanner is in ``v5_policy.v5_active_scanners``.
        """
        self._orchestrator = orchestrator or PipelineOrchestrator()
        self._config = decision_config or DecisionConfig()
        # v3 fallback — remove at Phase 9.
        self._weights = pillar_weights or PillarWeights.v3_default()
        self._thesis_config = thesis_config or ThesisConfig()
        self._calculator = DecisionCalculator(
            decision_config, pillar_weights, pillar_config=pillar_config
        )
        self._archetypes_config = archetypes_config
        self._anti_archetypes_config = anti_archetypes_config
        self._v5_policy = v5_policy
        self._thesis_generator = None  # Lazy init to avoid import if not needed

    def _compute_archetype_results(
        self,
        evaluations: Sequence[Evaluation],
        pillar_results: dict[str, Sequence[PillarResult]],
        feature_sets: dict[str, Any],
        opportunities: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Run archetype matcher + anti-archetype gates per evaluation.

        Returns a dict ``{eval_id: {archetype_matched, archetype_match_score,
        archetype_all_fits, anti_archetype_triggered}}``. Empty dict when
        neither archetypes nor anti-archetypes are configured on the policy
        (preserves pre-v4.1.0 behavior).
        """
        if self._archetypes_config is None and self._anti_archetypes_config is None:
            return {}

        from app.archetypes.gates import check_anti_archetypes
        from app.archetypes.matcher import compute_archetype_match
        from app.pillars.models import ScoringContext

        results: dict[str, dict[str, Any]] = {}
        for evaluation in evaluations:
            eval_id = evaluation.evaluation_id
            try:
                ctx = ScoringContext.from_evaluation_and_features(
                    evaluation=evaluation,
                    feature_set=feature_sets.get(eval_id),
                    opportunity=opportunities.get(evaluation.underlying_ticker),
                )
                pillars = pillar_results.get(eval_id, [])
                pillar_scores: dict[str, float] = {}
                for pr in pillars:
                    pid = (
                        pr.pillar_id.value
                        if hasattr(pr.pillar_id, "value")
                        else str(pr.pillar_id)
                    )
                    pillar_scores[pid] = pr.score
                    # Short aliases for archetype conditions referencing
                    # ts_score / mp_score / dc_score.
                    short = {
                        "TRADE_STRUCTURE": "TS",
                        "MOVE_POTENTIAL": "MP",
                        "DIRECTIONAL_CONVICTION": "DC",
                    }.get(pid)
                    if short:
                        pillar_scores[short] = pr.score

                entry: dict[str, Any] = {
                    "archetype_matched": None,
                    "archetype_match_score": None,
                    "archetype_all_fits": None,
                    "anti_archetype_triggered": None,
                }

                if self._anti_archetypes_config is not None:
                    aa = check_anti_archetypes(
                        ctx,
                        self._anti_archetypes_config,
                        pillar_scores=pillar_scores,
                    )
                    if aa.triggered:
                        entry["anti_archetype_triggered"] = aa.anti_archetype_id

                if self._archetypes_config is not None:
                    match = compute_archetype_match(
                        ctx, self._archetypes_config, pillar_scores=pillar_scores
                    )
                    entry["archetype_all_fits"] = match.all_fits
                    if match.best is not None:
                        entry["archetype_matched"] = match.best.archetype_id
                        entry["archetype_match_score"] = match.best_match_score

                results[eval_id] = entry
            except Exception as exc:
                logger.warning(
                    "archetype computation failed for %s: %s", eval_id, exc
                )
        return results

    def _compute_v5_envelopes(
        self,
        evaluations: Sequence[Evaluation],
        pillar_results: dict[str, Sequence[PillarResult]],
        feature_sets: dict[str, Any],
        opportunities: dict[str, Any],
    ) -> dict[str, Any]:
        """Run v5 dual-conviction scoring per evaluation.

        Returns ``{eval_id: V5Envelope}``. Empty dict when v5 is disabled
        on the policy. Failures degrade gracefully per-evaluation —
        a single bad evaluation doesn't kill the batch.

        Rate lookups are currently empty (seed-fallback), matching the
        Phase 5 scope. Phase 7 will add a rolling rate estimator that
        caches Wilson bounds per archetype at Lambda init.
        """
        if self._v5_policy is None or not getattr(self._v5_policy, "v5_active", False):
            return {}

        from app.pillars.models import ScoringContext
        from app.v5.pipeline import compute_v5_envelope

        envelopes: dict[str, Any] = {}
        for evaluation in evaluations:
            eval_id = evaluation.evaluation_id
            try:
                ctx = ScoringContext.from_evaluation_and_features(
                    evaluation=evaluation,
                    feature_set=feature_sets.get(eval_id),
                    opportunity=opportunities.get(evaluation.underlying_ticker),
                )
                pillars = pillar_results.get(eval_id, [])
                pillar_scores: dict[str, float] = {}
                for pr in pillars:
                    pid = (
                        pr.pillar_id.value
                        if hasattr(pr.pillar_id, "value")
                        else str(pr.pillar_id)
                    )
                    pillar_scores[pid] = pr.score
                    short = {
                        "TRADE_STRUCTURE": "TS",
                        "MOVE_POTENTIAL": "MP",
                        "DIRECTIONAL_CONVICTION": "DC",
                    }.get(pid)
                    if short:
                        pillar_scores[short] = pr.score

                envelope = compute_v5_envelope(
                    ctx,
                    self._v5_policy,
                    pillar_scores=pillar_scores,
                    hr_rate_lookup=None,   # Seed-fallback; Phase 7+ wires live rates
                    p_rate_lookup=None,
                    regime=None,           # Neutral; Phase 7+ wires SPY/VIX
                )
                envelopes[eval_id] = envelope
            except Exception as exc:
                logger.warning(
                    "v5 envelope computation failed for %s: %s", eval_id, exc
                )
        return envelopes

    async def execute(
        self,
        run_id: str,
        evaluations: Sequence[Evaluation],
        pillar_results: dict[str, Sequence[PillarResult]],
        gate_evaluations: dict[str, GateEvaluation],
        scanner_triggers: Optional[dict[str, Sequence[ScannerTrigger]]] = None,
        features: Optional[dict[str, dict[str, Any]]] = None,
        persist_decisions: bool = True,
        check_concentration: bool = True,
        generate_theses: bool = True,
        feature_sets: Optional[dict[str, Any]] = None,
        opportunities: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Decision], list[TradeThesis]]:
        """Execute decision logic stage.
        
        Args:
            run_id: Pipeline run ID for tracking
            evaluations: Evaluations from Stage 3
            pillar_results: Dict mapping evaluation_id to PillarResults
            gate_evaluations: Dict mapping evaluation_id to GateEvaluation
            scanner_triggers: Dict mapping evaluation_id to ScannerTriggers
            features: Dict mapping evaluation_id to feature values
            persist_decisions: If True, save decisions with evaluations
            check_concentration: If True, compute concentration warnings
            generate_theses: If True, generate LLM theses for APPROVE verdicts
            
        Returns:
            Tuple of (Dict mapping evaluation_id to Decision, List of TradeThesis)
        """
        logger.info(f"Stage 7: Computing decisions for {len(evaluations)} evaluations")

        # Update current stage
        await self._orchestrator.update_current_stage(run_id, PipelineStage.DECISION_LOGIC)

        theses: list[TradeThesis] = []

        if not evaluations:
            # Record empty stage event
            await self._orchestrator.record_stage_event(
                run_id=run_id,
                stage=PipelineStage.DECISION_LOGIC,
                items_in=0,
                items_out=0,
                metadata={
                    "approves": 0,
                    "watches": 0,
                    "rejects": 0,
                    "theses_generated": 0,
                },
            )
            return {}, theses

        # v4.1.0: compute archetype matches + anti-archetype gates per
        # evaluation when configured. Uses ScoringContext reconstituted
        # from evaluation + features. No-op when either config is None.
        archetype_results = self._compute_archetype_results(
            evaluations=evaluations,
            pillar_results=pillar_results,
            feature_sets=feature_sets or {},
            opportunities=opportunities or {},
        )

        # v5.0.0: compute dual-conviction envelopes per evaluation when
        # v5_policy.v5_active is True. No-op otherwise. When present, v5
        # fields are denormalized onto every Decision; if the scanner is
        # in v5_active_scanners, v5 drives the verdict.
        v5_envelopes = self._compute_v5_envelopes(
            evaluations=evaluations,
            pillar_results=pillar_results,
            feature_sets=feature_sets or {},
            opportunities=opportunities or {},
        )

        # Step 1: Compute initial decisions
        decisions = self._calculator.compute_decisions_batch(
            evaluations=evaluations,
            pillar_results=pillar_results,
            gate_evaluations=gate_evaluations,
            archetype_results=archetype_results,
            v5_envelopes=v5_envelopes,
            v5_policy=self._v5_policy,
        )

        # Step 2: Check concentration warnings if enabled
        if check_concentration:
            warnings = check_concentration_warnings(evaluations, decisions)
            if warnings:
                decisions = update_decisions_with_warnings(decisions, warnings)
                logger.info(
                    f"Added concentration warnings to {len(warnings)} evaluations"
                )

        # Step 3: Persist decisions with evaluations
        if persist_decisions:
            await self._persist_decisions(evaluations, decisions)

        # Step 4: Generate theses for APPROVE verdicts (Phase 10)
        if generate_theses and self._thesis_config.enabled:
            theses = await self._generate_theses(
                evaluations=evaluations,
                decisions=decisions,
                pillar_results=pillar_results,
                scanner_triggers=scanner_triggers or {},
                features=features or {},
            )

        # Compute statistics
        stats = self._compute_stats(evaluations, decisions)
        stats["theses_generated"] = len([t for t in theses if str(t.status) == "COMPLETED"])
        stats["theses_failed"] = len([t for t in theses if str(t.status) == "FAILED"])
        stats["theses_rate_limited"] = len([t for t in theses if str(t.status) == "RATE_LIMITED"])

        # Record stage event
        await self._orchestrator.record_stage_event(
            run_id=run_id,
            stage=PipelineStage.DECISION_LOGIC,
            items_in=len(evaluations),
            items_out=stats["total_actionable"],  # APPROVE + WATCH
            metadata=stats,
        )

        logger.info(
            f"Stage 7 complete: {stats['approves']} APPROVE, "
            f"{stats['watches']} WATCH, {stats['rejects']} REJECT, "
            f"{stats['theses_generated']} theses generated"
        )

        return decisions, theses

    def _get_thesis_generator(self):
        """Lazy init thesis generator to avoid import if not needed."""
        if self._thesis_generator is None:
            from app.llm.generator import ThesisGenerator
            self._thesis_generator = ThesisGenerator(config=self._thesis_config)
        return self._thesis_generator

    async def _generate_theses(
        self,
        evaluations: Sequence[Evaluation],
        decisions: dict[str, Decision],
        pillar_results: dict[str, Sequence[PillarResult]],
        scanner_triggers: dict[str, Sequence[ScannerTrigger]],
        features: dict[str, dict[str, Any]],
    ) -> list[TradeThesis]:
        """Generate LLM theses for APPROVE evaluations.
        
        Per Section 21 of OSS_Complete_Requirements.md.
        
        Args:
            evaluations: All evaluations
            decisions: Dict mapping evaluation_id to Decision
            pillar_results: Dict mapping evaluation_id to PillarResults
            scanner_triggers: Dict mapping evaluation_id to ScannerTriggers
            features: Dict mapping evaluation_id to feature values
            
        Returns:
            List of generated TradeThesis records
        """
        # Get approved evaluations
        approved = [
            e for e in evaluations
            if e.evaluation_id in decisions
            and decisions[e.evaluation_id].verdict == Verdict.APPROVE
        ]

        if not approved:
            logger.info("No APPROVE verdicts - skipping thesis generation")
            return []

        logger.info(f"Generating theses for {len(approved)} APPROVE evaluations")

        generator = self._get_thesis_generator()
        theses: list[TradeThesis] = []

        # Fetch setup rules once for all evaluations
        all_setup_rules: list[dict[str, Any]] = []
        total_active_rules = 0
        try:
            from app.paper_trading.pattern_discovery import list_setup_rules
            all_setup_rules = await list_setup_rules()
            total_active_rules = len(
                [r for r in all_setup_rules if r.get("is_active", True)]
            )
        except Exception as e:
            logger.warning(f"Failed to fetch setup rules for thesis generation: {e}")

        for evaluation in approved:
            try:
                eval_id = evaluation.evaluation_id
                decision = decisions[eval_id]

                # Get pillar results - convert to PillarScore list
                pillar_result_list = pillar_results.get(eval_id, [])
                pillar_scores = [pr.to_pillar_score() for pr in pillar_result_list]

                # Get scanner triggers
                triggers = list(scanner_triggers.get(eval_id, []))

                # Get features
                eval_features = features.get(eval_id, {})

                # Match setup rules against this evaluation
                matched_rules: list[dict[str, Any]] = []
                if all_setup_rules:
                    try:
                        from app.paper_trading.rule_matcher import (
                            format_matched_rules,
                            match_rules,
                        )
                        # Use full evaluation dict (mirrors evaluation_snapshot pattern)
                        eval_dict = evaluation.model_dump()
                        eval_dict["option_type"] = str(
                            evaluation.option_type.value
                            if hasattr(evaluation.option_type, "value")
                            else evaluation.option_type
                        )
                        # Overlay Stage 4 feature fields
                        eval_dict.update(eval_features)

                        decision_dict: dict[str, Any] = {
                            "final_score": decision.final_score,
                            **decision.pillar_score_dict(),
                        }
                        scanner_names = [
                            str(
                                t.scanner_type.value
                                if hasattr(t.scanner_type, "value")
                                else t.scanner_type
                            )
                            for t in triggers
                        ]
                        matched = match_rules(
                            all_setup_rules, eval_dict, decision_dict, scanner_names
                        )
                        matched_rules = format_matched_rules(
                            matched, include_criteria=True
                        )
                    except Exception as e:
                        logger.warning(
                            f"Setup rule matching failed for {eval_id}: {e}"
                        )

                # Generate thesis
                thesis = await generator.generate(
                    evaluation=evaluation,
                    decision=decision,
                    pillar_scores=pillar_scores,
                    scanner_triggers=triggers,
                    features=eval_features,
                    matched_rules=matched_rules,
                    total_active_rules=total_active_rules,
                )

                # Persist thesis
                await TradeThesisTable.put(thesis)
                theses.append(thesis)

                logger.debug(f"Generated thesis for {eval_id}: {thesis.status}")

            except Exception as e:
                logger.error(f"Error generating thesis for {evaluation.evaluation_id}: {e}")
                continue

        completed = len([t for t in theses if str(t.status) == "COMPLETED"])
        logger.info(f"Generated {completed}/{len(approved)} theses successfully")

        return theses

    async def _persist_decisions(
        self,
        evaluations: Sequence[Evaluation],
        decisions: dict[str, Decision],
    ) -> None:
        """Persist decisions with their evaluations.
        
        Args:
            evaluations: List of Evaluation records
            decisions: Dict mapping evaluation_id to Decision
        """
        eval_map = {e.evaluation_id: e for e in evaluations}

        for eval_id, decision in decisions.items():
            try:
                evaluation = eval_map.get(eval_id)
                if evaluation:
                    await EvaluationTable.put(evaluation, decision)
            except Exception as e:
                logger.error(f"Error persisting decision for {eval_id}: {e}")

    def _compute_stats(
        self,
        evaluations: Sequence[Evaluation],
        decisions: dict[str, Decision],
    ) -> dict:
        """Compute statistics for stage telemetry.
        
        Args:
            evaluations: List of Evaluation records
            decisions: Dict mapping evaluation_id to Decision
            
        Returns:
            Dict with statistics
        """
        # Count verdicts
        approves = 0
        watches = 0
        rejects = 0

        # Count quality tiers
        tier_1 = 0
        tier_2 = 0
        tier_3 = 0

        # Count rejection reasons
        rejected_by_gates = 0
        rejected_by_score = 0

        # Track concentration warnings
        has_ticker_warning = 0
        has_directional_warning = 0

        # Score distributions
        approve_scores: list[float] = []
        watch_scores: list[float] = []
        reject_scores: list[float] = []

        for decision in decisions.values():
            if decision.verdict == Verdict.APPROVE:
                approves += 1
                approve_scores.append(decision.final_score)

                if decision.quality_tier:
                    tier_name = str(decision.quality_tier)
                    if hasattr(decision.quality_tier, 'value'):
                        tier_name = decision.quality_tier.value

                    if tier_name == "TIER_1":
                        tier_1 += 1
                    elif tier_name == "TIER_2":
                        tier_2 += 1
                    elif tier_name == "TIER_3":
                        tier_3 += 1

            elif decision.verdict == Verdict.WATCH:
                watches += 1
                watch_scores.append(decision.final_score)

            elif decision.verdict == Verdict.REJECT:
                rejects += 1
                reject_scores.append(decision.final_score)

                if decision.primary_reason_code == "REJECTED_BY_GATES":
                    rejected_by_gates += 1
                elif decision.primary_reason_code == "REJECTED_BY_SCORE":
                    rejected_by_score += 1

            # Count warnings
            for warning in decision.concentration_warnings:
                if "SAME_TICKER" in warning:
                    has_ticker_warning += 1
                if "DIRECTIONAL" in warning:
                    has_directional_warning += 1

        # Compute averages
        avg_approve_score = (
            sum(approve_scores) / len(approve_scores) if approve_scores else 0
        )
        avg_watch_score = (
            sum(watch_scores) / len(watch_scores) if watch_scores else 0
        )
        avg_reject_score = (
            sum(reject_scores) / len(reject_scores) if reject_scores else 0
        )

        # Run concentration analysis
        analysis = analyze_concentration(evaluations, decisions)

        return {
            "approves": approves,
            "watches": watches,
            "rejects": rejects,
            "total_actionable": approves + watches,
            "quality_tiers": {
                "tier_1": tier_1,
                "tier_2": tier_2,
                "tier_3": tier_3,
            },
            "rejection_reasons": {
                "by_gates": rejected_by_gates,
                "by_score": rejected_by_score,
            },
            "concentration_warnings": {
                "ticker": has_ticker_warning,
                "directional": has_directional_warning,
            },
            "score_averages": {
                "approve": round(avg_approve_score, 2),
                "watch": round(avg_watch_score, 2),
                "reject": round(avg_reject_score, 2),
            },
            "concentration_analysis": {
                "tickers_over_limit": analysis.tickers_over_limit,
                "directional_warning": analysis.directional_warning,
                "dominant_direction": analysis.dominant_direction,
                "call_pct": round(analysis.call_pct * 100, 1),
                "put_pct": round(analysis.put_pct * 100, 1),
            },
        }


async def run_decision_logic(
    run_id: str,
    evaluations: Sequence[Evaluation],
    pillar_results: dict[str, Sequence[PillarResult]],
    gate_evaluations: dict[str, GateEvaluation],
    orchestrator: PipelineOrchestrator,
    decision_config: Optional[DecisionConfig] = None,
    pillar_weights: Optional[PillarWeights] = None,
    thesis_config: Optional[ThesisConfig] = None,
    scanner_triggers: Optional[dict[str, Sequence[ScannerTrigger]]] = None,
    features: Optional[dict[str, dict[str, Any]]] = None,
    persist_decisions: bool = True,
    check_concentration: bool = True,
    generate_theses: bool = True,
    pillar_config: Optional[PillarConfig] = None,
    archetypes_config: Optional[ArchetypeConfig] = None,
    anti_archetypes_config: Optional[AntiArchetypeConfig] = None,
    feature_sets: Optional[dict[str, Any]] = None,
    opportunities: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Decision], list[TradeThesis]]:
    """Convenience function to run decision logic stage.

    Args:
        run_id: Pipeline run ID
        evaluations: Evaluations from Stage 3
        pillar_results: Dict mapping evaluation_id to PillarResults
        gate_evaluations: Dict mapping evaluation_id to GateEvaluation
        orchestrator: Pipeline orchestrator
        decision_config: Decision thresholds
        pillar_weights: Pillar weights
        thesis_config: LLM thesis generation config
        scanner_triggers: Dict mapping evaluation_id to ScannerTriggers
        features: Dict mapping evaluation_id to feature values
        persist_decisions: Whether to save to DynamoDB
        check_concentration: Whether to check concentration warnings
        generate_theses: Whether to generate LLM theses for APPROVE verdicts
        pillar_config: Full PillarConfig for per-scanner weight lookup

    Returns:
        Tuple of (Dict mapping evaluation_id to Decision, List of TradeThesis)
    """
    stage = DecisionStage(
        orchestrator=orchestrator,
        decision_config=decision_config,
        pillar_weights=pillar_weights,
        thesis_config=thesis_config,
        pillar_config=pillar_config,
        archetypes_config=archetypes_config,
        anti_archetypes_config=anti_archetypes_config,
    )
    return await stage.execute(
        run_id=run_id,
        evaluations=evaluations,
        pillar_results=pillar_results,
        gate_evaluations=gate_evaluations,
        scanner_triggers=scanner_triggers,
        features=features,
        persist_decisions=persist_decisions,
        check_concentration=check_concentration,
        generate_theses=generate_theses,
        feature_sets=feature_sets,
        opportunities=opportunities,
    )


def get_approved_evaluations(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
) -> list[Evaluation]:
    """Filter evaluations to only those with APPROVE verdict.
    
    Args:
        evaluations: List of Evaluation records
        decisions: Dict mapping evaluation_id to Decision
        
    Returns:
        List of Evaluations with APPROVE verdict
    """
    return [
        e for e in evaluations
        if e.evaluation_id in decisions
        and decisions[e.evaluation_id].verdict == Verdict.APPROVE
    ]


def get_watch_evaluations(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
) -> list[Evaluation]:
    """Filter evaluations to only those with WATCH verdict.
    
    Args:
        evaluations: List of Evaluation records
        decisions: Dict mapping evaluation_id to Decision
        
    Returns:
        List of Evaluations with WATCH verdict
    """
    return [
        e for e in evaluations
        if e.evaluation_id in decisions
        and decisions[e.evaluation_id].verdict == Verdict.WATCH
    ]


def get_rejected_evaluations(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
) -> list[Evaluation]:
    """Filter evaluations to only those with REJECT verdict.
    
    Args:
        evaluations: List of Evaluation records
        decisions: Dict mapping evaluation_id to Decision
        
    Returns:
        List of Evaluations with REJECT verdict
    """
    return [
        e for e in evaluations
        if e.evaluation_id in decisions
        and decisions[e.evaluation_id].verdict == Verdict.REJECT
    ]


def get_tier_1_evaluations(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
) -> list[Evaluation]:
    """Filter evaluations to only TIER_1 quality approvals.
    
    Args:
        evaluations: List of Evaluation records
        decisions: Dict mapping evaluation_id to Decision
        
    Returns:
        List of Evaluations with APPROVE verdict and TIER_1 quality
    """
    from app.core.schemas import QualityTier

    return [
        e for e in evaluations
        if e.evaluation_id in decisions
        and decisions[e.evaluation_id].verdict == Verdict.APPROVE
        and decisions[e.evaluation_id].quality_tier == QualityTier.TIER_1
    ]


def extract_decisions_for_paper_trading(
    decisions: dict[str, Decision],
) -> dict[str, dict]:
    """Extract decision data in format needed for paper trading.
    
    Args:
        decisions: Dict mapping evaluation_id to Decision
        
    Returns:
        Dict mapping evaluation_id to paper trading data
    """
    extracted: dict[str, dict] = {}

    for eval_id, decision in decisions.items():
        # Only include APPROVE and WATCH for paper trading
        if decision.verdict not in (Verdict.APPROVE, Verdict.WATCH):
            continue

        tier_value = None
        if decision.quality_tier:
            tier_value = str(decision.quality_tier)
            if hasattr(decision.quality_tier, 'value'):
                tier_value = decision.quality_tier.value

        extracted[eval_id] = {
            "verdict": str(decision.verdict.value) if hasattr(decision.verdict, 'value') else str(decision.verdict),
            "quality_tier": tier_value,
            "final_score": decision.final_score,
            **decision.pillar_score_dict(),
            "concentration_warnings": decision.concentration_warnings,
        }

    return extracted
