"""Trade Thesis Generator - Main orchestrator for LLM thesis generation.

Per Section 21 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Sequence
from uuid import uuid4

from app.core.schemas import (
    Decision,
    Evaluation,
    ExitPlanThesis,
    LLMProvider as LLMProviderEnum,
    PillarScore,
    ScannerTrigger,
    StopLossTarget,
    TakeProfitTarget,
    ThesisConfig,
    ThesisStatus,
    TimeExitTarget,
    TradeThesis,
    Verdict,
)
from app.llm.models import (
    ContractData,
    PillarContributorData,
    ScannerTriggerData,
    ScoresData,
    ThesisInput,
    ThesisOutput,
    UnderlyingData,
)
from app.llm.prompt import build_thesis_prompt, parse_thesis_response
from app.llm.provider import LLMProvider, get_provider
from app.llm.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class ThesisGenerator:
    """Orchestrates trade thesis generation for APPROVE evaluations.

    Handles:
    - Building input packets from evaluation data
    - Rate limiting (max 50 calls/day)
    - Provider selection and fallback
    - Error handling and status tracking
    """

    def __init__(
        self,
        config: Optional[ThesisConfig] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        """Initialize thesis generator.

        Args:
            config: Thesis generation configuration
            rate_limiter: Rate limiter for daily call limits
        """
        self._config = config or ThesisConfig()
        self._rate_limiter = rate_limiter or RateLimiter(
            max_daily_calls=self._config.max_daily_calls
        )

        # Initialize providers
        self._providers: dict[str, LLMProvider] = {}
        if self._config.enabled:
            self._providers["anthropic"] = get_provider("anthropic")
            self._providers["openai"] = get_provider("openai")

    def _get_provider(self) -> Optional[LLMProvider]:
        """Get the preferred available provider.

        Returns:
            LLMProvider instance or None if none available
        """
        # Try preferred provider first
        preferred = self._config.preferred_provider
        if isinstance(preferred, LLMProviderEnum):
            preferred = preferred.value

        if preferred in self._providers:
            provider = self._providers[preferred]
            if provider.is_available():
                return provider

        # Try fallback if enabled
        if self._config.fallback_enabled:
            for name, provider in self._providers.items():
                if name != preferred and provider.is_available():
                    logger.info(f"Using fallback provider: {name}")
                    return provider

        return None

    def build_input(
        self,
        evaluation: Evaluation,
        decision: Decision,
        pillar_scores: Sequence[PillarScore],
        scanner_triggers: Sequence[ScannerTrigger],
        features: Optional[dict[str, Any]] = None,
        matched_rules: Optional[list[dict[str, Any]]] = None,
        total_active_rules: int = 0,
    ) -> ThesisInput:
        """Build ThesisInput from evaluation data.

        Args:
            evaluation: Evaluation record
            decision: Decision record
            pillar_scores: List of PillarScore records
            scanner_triggers: List of ScannerTrigger records
            features: Optional feature values dict

        Returns:
            ThesisInput ready for prompt generation
        """
        features = features or {}

        # Build underlying data
        underlying = UnderlyingData(
            ticker=evaluation.underlying_ticker,
            price=evaluation.underlying_price,
            sma20=features.get("sma20"),
            sma50=features.get("sma50"),
            return_5d=features.get("return_5d"),
            return_20d=features.get("return_20d"),
            atr14=features.get("atr14"),
            atr14_pct=features.get("atr14_pct"),
        )

        # Compute theta_pct from evaluation data
        theta_pct = None
        if evaluation.mid and evaluation.mid > 0 and evaluation.theta:
            theta_pct = abs(evaluation.theta) / evaluation.mid * 100

        # Build contract data with exit-relevant fields
        contract = ContractData(
            option_type=str(evaluation.option_type.value) if hasattr(evaluation.option_type, 'value') else str(evaluation.option_type),
            strike=evaluation.strike,
            expiration=evaluation.expiration_date,
            dte=evaluation.dte,
            mid=evaluation.mid,
            iv=evaluation.iv,
            delta=evaluation.delta,
            theta=evaluation.theta,
            gamma=evaluation.gamma,
            vega=evaluation.vega,
            open_interest=evaluation.open_interest,
            volume=evaluation.volume,
            spread_pct=evaluation.spread_pct,
            breakeven_price=getattr(evaluation, "breakeven_price", None),
            expected_move_pct=getattr(evaluation, "expected_move_pct", None),
            feasibility_ratio=getattr(evaluation, "feasibility_ratio", None),
            theta_pct=theta_pct,
        )

        # Build scores data
        scores = ScoresData(
            final=decision.final_score,
            premium_leverage=decision.premium_leverage_score,
            underlying_behavior=decision.underlying_behavior_score,
            setup_quality=decision.setup_quality_score,
        )

        # Build pillar contributors
        pillar_contributors: dict[str, list[PillarContributorData]] = {}
        for ps in pillar_scores:
            pillar_id = str(ps.pillar_id.value) if hasattr(ps.pillar_id, 'value') else str(ps.pillar_id)
            pillar_contributors[pillar_id.lower()] = [
                PillarContributorData(
                    feature_name=c.feature_name,
                    subscore=c.subscore,
                    weight=c.weight,
                    weighted_contribution=c.weighted_contribution,
                    raw_value=c.raw_value,
                )
                for c in ps.contributors[:3]  # Top 3 contributors
            ]

        # Build scanner triggers
        triggers = [
            ScannerTriggerData(
                scanner_type=str(t.scanner_type.value) if hasattr(t.scanner_type, 'value') else str(t.scanner_type),
                reason_codes=t.reason_codes,
                metrics=t.metrics,
            )
            for t in scanner_triggers
        ]

        # Get quality tier
        quality_tier = None
        if decision.quality_tier:
            quality_tier = str(decision.quality_tier.value) if hasattr(decision.quality_tier, 'value') else str(decision.quality_tier)

        # Enrich matched rules with top-level performance fields for prompt formatting
        enriched_rules: list[dict[str, Any]] = []
        for rule in (matched_rules or []):
            enriched: dict[str, Any] = {
                "name": rule.get("name", "Unknown Rule"),
                "mode": rule.get("mode", "production"),
                "source": rule.get("source", "ai"),
                "matched_criteria": rule.get("criteria", {}),
            }
            # Extract performance from performance_at_creation if available
            perf = rule.get("performance_at_creation") or {}
            if perf:
                enriched["win_rate"] = perf.get("win_rate")
                enriched["avg_return"] = perf.get("avg_return")
                enriched["sample_size"] = perf.get("sample_size")
            enriched_rules.append(enriched)

        return ThesisInput(
            underlying=underlying,
            contract=contract,
            scores=scores,
            pillar_contributors=pillar_contributors,
            scanner_triggers=triggers,
            policy_version=evaluation.policy_version,
            quality_tier=quality_tier,
            evaluation_id=evaluation.evaluation_id,
            setup_rule_matches=enriched_rules,
            total_active_rules=total_active_rules,
        )

    async def generate(
        self,
        evaluation: Evaluation,
        decision: Decision,
        pillar_scores: Sequence[PillarScore],
        scanner_triggers: Sequence[ScannerTrigger],
        features: Optional[dict[str, Any]] = None,
        matched_rules: Optional[list[dict[str, Any]]] = None,
        total_active_rules: int = 0,
    ) -> TradeThesis:
        """Generate trade thesis for an APPROVE evaluation.

        Args:
            evaluation: Evaluation record
            decision: Decision record (must be APPROVE)
            pillar_scores: List of PillarScore records
            scanner_triggers: List of ScannerTrigger records
            features: Optional feature values dict

        Returns:
            TradeThesis record (may have FAILED or RATE_LIMITED status)
        """
        # Validate this is an APPROVE verdict
        verdict = decision.verdict
        if isinstance(verdict, str):
            verdict = Verdict(verdict)
        if verdict != Verdict.APPROVE:
            return self._create_failed_thesis(
                evaluation.evaluation_id,
                f"Thesis only generated for APPROVE verdicts, got {verdict}",
            )

        # Check if thesis generation is enabled
        if not self._config.enabled:
            return self._create_failed_thesis(
                evaluation.evaluation_id,
                "Thesis generation is disabled",
                status=ThesisStatus.PENDING,
            )

        # Check rate limit
        if not await self._rate_limiter.can_make_call():
            remaining = await self._rate_limiter.get_remaining_calls()
            logger.warning(f"Rate limit exceeded, {remaining} calls remaining")
            return self._create_failed_thesis(
                evaluation.evaluation_id,
                f"Daily rate limit reached ({self._config.max_daily_calls} calls/day)",
                status=ThesisStatus.RATE_LIMITED,
            )

        # Get available provider
        provider = self._get_provider()
        if not provider:
            return self._create_failed_thesis(
                evaluation.evaluation_id,
                "No LLM provider available (check API keys)",
            )

        try:
            # Build input and prompt
            input_data = self.build_input(
                evaluation=evaluation,
                decision=decision,
                pillar_scores=pillar_scores,
                scanner_triggers=scanner_triggers,
                features=features,
                matched_rules=matched_rules,
                total_active_rules=total_active_rules,
            )
            prompt = build_thesis_prompt(input_data)

            # Call LLM
            logger.info(f"Generating thesis for {evaluation.evaluation_id} using {provider.name}")
            response = await provider.generate(
                prompt=prompt,
                max_tokens=self._config.output_token_limit,
            )

            if not response.success:
                return self._create_failed_thesis(
                    evaluation.evaluation_id,
                    f"LLM call failed: {response.error}",
                )

            # Parse response
            try:
                thesis_data = parse_thesis_response(response.content)
                output = ThesisOutput.from_dict(thesis_data)
            except ValueError as e:
                return self._create_failed_thesis(
                    evaluation.evaluation_id,
                    f"Failed to parse LLM response: {e}",
                )

            # Record the call
            await self._rate_limiter.record_call(response.tokens_used)

            # Build structured exit plan
            take_profits = [
                TakeProfitTarget(
                    tier=tp.tier,
                    option_pnl_pct=tp.option_pnl_pct,
                    underlying_price=tp.underlying_price,
                    rationale=tp.rationale,
                )
                for tp in output.exit_plan.take_profits
            ]
            stop_loss_level = None
            if output.exit_plan.stop_loss_level:
                stop_loss_level = StopLossTarget(
                    option_pnl_pct=output.exit_plan.stop_loss_level.option_pnl_pct,
                    underlying_price=output.exit_plan.stop_loss_level.underlying_price,
                    rationale=output.exit_plan.stop_loss_level.rationale,
                )
            time_exit_level = None
            if output.exit_plan.time_exit_level:
                time_exit_level = TimeExitTarget(
                    dte_threshold=output.exit_plan.time_exit_level.dte_threshold,
                    rationale=output.exit_plan.time_exit_level.rationale,
                )

            # Build TradeThesis
            return TradeThesis(
                thesis_id=str(uuid4()),
                evaluation_id=evaluation.evaluation_id,
                setup_summary=output.setup_summary,
                thesis=output.thesis,
                supporting_evidence=output.supporting_evidence,
                risks=output.risks,
                invalidation_conditions=output.invalidation_conditions,
                exit_plan=ExitPlanThesis(
                    profit_target=output.exit_plan.profit_target,
                    stop_loss=output.exit_plan.stop_loss,
                    time_exit=output.exit_plan.time_exit,
                    take_profits=take_profits,
                    stop_loss_level=stop_loss_level,
                    time_exit_level=time_exit_level,
                ),
                llm_provider=LLMProviderEnum(provider.name),
                model_used=response.model,
                tokens_used=response.tokens_used,
                status=ThesisStatus.COMPLETED,
                generated_at=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            logger.exception(f"Unexpected error generating thesis: {e}")
            return self._create_failed_thesis(
                evaluation.evaluation_id,
                f"Unexpected error: {str(e)}",
            )

    def _create_failed_thesis(
        self,
        evaluation_id: str,
        error_message: str,
        status: ThesisStatus = ThesisStatus.FAILED,
    ) -> TradeThesis:
        """Create a failed/pending thesis record.

        Args:
            evaluation_id: Evaluation ID
            error_message: Error message
            status: Thesis status (FAILED, RATE_LIMITED, or PENDING)

        Returns:
            TradeThesis with error status
        """
        return TradeThesis(
            thesis_id=str(uuid4()),
            evaluation_id=evaluation_id,
            setup_summary="",
            thesis="",
            supporting_evidence=[],
            risks=[],
            invalidation_conditions=[],
            exit_plan=ExitPlanThesis(
                profit_target="",
                stop_loss="",
                time_exit="",
            ),
            llm_provider=LLMProviderEnum.ANTHROPIC,  # Default
            model_used="",
            tokens_used=0,
            status=status,
            error_message=error_message,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    async def generate_batch(
        self,
        items: Sequence[tuple[Evaluation, Decision, Sequence[PillarScore], Sequence[ScannerTrigger]]],
        features_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[TradeThesis]:
        """Generate theses for multiple evaluations.

        Args:
            items: List of (evaluation, decision, pillar_scores, scanner_triggers) tuples
            features_map: Optional dict mapping evaluation_id to features

        Returns:
            List of TradeThesis records
        """
        results: list[TradeThesis] = []
        features_map = features_map or {}

        for evaluation, decision, pillar_scores, scanner_triggers in items:
            # Only process APPROVE verdicts
            verdict = decision.verdict
            if isinstance(verdict, str):
                verdict = Verdict(verdict)
            if verdict != Verdict.APPROVE:
                continue

            features = features_map.get(evaluation.evaluation_id, {})
            thesis = await self.generate(
                evaluation=evaluation,
                decision=decision,
                pillar_scores=pillar_scores,
                scanner_triggers=scanner_triggers,
                features=features,
            )
            results.append(thesis)

        logger.info(f"Generated {len(results)} theses")
        return results
