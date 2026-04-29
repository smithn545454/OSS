"""Convex Mode — Daily pipeline runner.

Loads the most recent kinetic-universe snapshot, instantiates the
production providers (Stage 2/3/4), runs the four-stage pipeline,
finalises tier assignment + Decision emission, and persists per-stage
event records to ``oss-dev-convex-stage-events`` for the Evaluation
Detail / debug pages.

Decision persistence to the legacy ``oss-dev-evaluations`` table is
deferred to Phase 7 (frontend integration) — the daily runner returns
``FinalisedConvexCandidate`` records that callers can persist however
they choose. For now the Lambda handler logs counts and writes per-
stage events; full Evaluation/Decision wiring lands when the UI is
ready.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.convex.pipeline import (
    ConvexPipeline,
    ConvexPipelineResult,
)
from app.convex.providers import (
    ProductionStage2InputsProvider,
    ProductionStage3InputsProvider,
    ProductionStage4InputsProvider,
    build_peer_reactions_cache,
)
from app.convex.tier import (
    FinalisedConvexCandidate,
    finalise_candidate,
)
from app.convex.uv_lookup import lookup_uv_signal
from app.convex.uv_lookup import to_dict as uv_to_dict
from app.core.schemas import (
    ConvexConfig,
    ConvexEvaluation,
    ConvexSelectedContract,
    ConvexStageEventRecord,
    ConvexUniverseSnapshot,
)
from app.db.tables import (
    ConvexEvaluationTable,
    ConvexStageEventTable,
    ConvexUniverseSnapshotTable,
)

logger = logging.getLogger(__name__)


@dataclass
class DailyRunResult:
    """Aggregate output of one daily Convex pipeline run."""

    run_id: str
    snapshot_date: Optional[str]
    universe_size: int
    stage2_advancers: int
    stage3_advancers: int
    stage4_advancers: int
    tier_a_count: int
    tier_b_count: int
    tier_c_count: int
    finalised: list[FinalisedConvexCandidate] = field(default_factory=list)
    started_at: str = ""
    completed_at: Optional[str] = None
    error: Optional[str] = None

    def summary_dict(self) -> dict[str, Any]:
        """Compact serialisable summary suitable for Lambda return values."""
        return {
            "run_id": self.run_id,
            "snapshot_date": self.snapshot_date,
            "universe_size": self.universe_size,
            "stage2_advancers": self.stage2_advancers,
            "stage3_advancers": self.stage3_advancers,
            "stage4_advancers": self.stage4_advancers,
            "tier_a": self.tier_a_count,
            "tier_b": self.tier_b_count,
            "tier_c": self.tier_c_count,
            "finalised_count": len(self.finalised),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


async def run_daily_convex_pipeline(
    config: ConvexConfig,
    polygon_client: Any,
    policy_version: str,
    snapshot: Optional[ConvexUniverseSnapshot] = None,
    today_iso: Optional[str] = None,
) -> DailyRunResult:
    """Top-level orchestrator for one daily Convex run.

    Args:
        config: ConvexConfig from the active policy.
        polygon_client: Live Polygon client used by all three providers.
        policy_version: Active policy version recorded on emitted Decisions.
        snapshot: Optional pre-loaded universe snapshot (tests inject one;
            production loads from ConvexUniverseSnapshotTable).
        today_iso: As-of date; defaults to today UTC.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    today_iso = today_iso or datetime.now(timezone.utc).date().isoformat()
    run_id = str(uuid4())

    if not config.enabled:
        logger.info("Convex Mode disabled in policy; daily run is a no-op.")
        return DailyRunResult(
            run_id=run_id,
            snapshot_date=None,
            universe_size=0,
            stage2_advancers=0,
            stage3_advancers=0,
            stage4_advancers=0,
            tier_a_count=0,
            tier_b_count=0,
            tier_c_count=0,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    if snapshot is None:
        snapshot = await ConvexUniverseSnapshotTable.get_latest()
    if snapshot is None or not snapshot.tickers:
        logger.error(
            "Convex daily run: no universe snapshot available — "
            "kick off the monthly UniverseConstructor first."
        )
        return DailyRunResult(
            run_id=run_id,
            snapshot_date=None,
            universe_size=0,
            stage2_advancers=0,
            stage3_advancers=0,
            stage4_advancers=0,
            tier_a_count=0,
            tier_b_count=0,
            tier_c_count=0,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            error="no_universe_snapshot",
        )

    # Pre-compute the peer-reactions cache once for the run.
    peer_cache = await build_peer_reactions_cache(snapshot, today_iso)

    pipeline = ConvexPipeline(
        config=config,
        universe_snapshot=snapshot,
        stage2_inputs_provider=ProductionStage2InputsProvider(
            polygon_client, peer_cache
        ),
        stage3_inputs_provider=ProductionStage3InputsProvider(polygon_client),
        stage4_inputs_provider=ProductionStage4InputsProvider(polygon_client),
        as_of_date=today_iso,
    )

    pipeline_result: ConvexPipelineResult = await pipeline.run(run_id=run_id)

    finalised = await _finalise_and_persist(
        pipeline_result, config, policy_version, run_id
    )

    # Downstream consumers — paper trading, LLM thesis, Slack alerts.
    # Each call is isolated so a failure in one consumer does not break
    # the run or block the others. Slack is gated on
    # ``config.alerts_enabled`` so legacy alerts continue uninterrupted
    # until cutover; positions and theses run unconditionally so they
    # exist for inspection in pre-cutover dry runs.
    if finalised:
        await _emit_downstream_artifacts(finalised, config)

    completed_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "Convex daily run %s done: u=%d s2=%d s3=%d s4=%d tiers a=%d b=%d c=%d "
        "finalised=%d",
        run_id,
        pipeline_result.universe_size,
        pipeline_result.stage2_advancers,
        pipeline_result.stage3_advancers,
        pipeline_result.stage4_advancers,
        pipeline_result.tier_a_count,
        pipeline_result.tier_b_count,
        pipeline_result.tier_c_count,
        len(finalised),
    )

    return DailyRunResult(
        run_id=run_id,
        snapshot_date=snapshot.snapshot_date,
        universe_size=pipeline_result.universe_size,
        stage2_advancers=pipeline_result.stage2_advancers,
        stage3_advancers=pipeline_result.stage3_advancers,
        stage4_advancers=pipeline_result.stage4_advancers,
        tier_a_count=pipeline_result.tier_a_count,
        tier_b_count=pipeline_result.tier_b_count,
        tier_c_count=pipeline_result.tier_c_count,
        finalised=finalised,
        started_at=started_at,
        completed_at=completed_at,
    )


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def _finalise_and_persist(
    pipeline_result: ConvexPipelineResult,
    config: ConvexConfig,
    policy_version: str,
    run_id: str,
) -> list[FinalisedConvexCandidate]:
    """Finalise tier + emit Decisions; persist per-stage events.

    Phase 6.5: persists ``ConvexStageEventRecord`` entries for every
    candidate (PASS or FAIL), so the failed-candidates debug page can
    answer "why didn't ticker XYZ make it?". Decisions are returned in
    the result for the caller to persist; full EvaluationTable wiring
    lands in Phase 7 when the frontend integration ships.
    """
    finalised: list[FinalisedConvexCandidate] = []
    stage_events: list[ConvexStageEventRecord] = []

    for candidate in pipeline_result.candidates:
        # Persist every populated stage payload so the UI can replay the
        # candidate's journey, including failures.
        for stage_num in (1, 2, 3, 4):
            payload = getattr(candidate.stages, f"stage_{stage_num}", None)
            if payload is None:
                continue
            stage_events.append(
                ConvexStageEventRecord(
                    run_id=run_id,
                    ticker=candidate.ticker,
                    stage=stage_num,
                    payload=payload,
                )
            )

        # Tier + Decision only for fully-advanced candidates.
        if candidate.advanced_to_stage == 4:
            evaluation_id = f"convex-{run_id[:8]}-{candidate.ticker}"
            # Look up the UV signal for this ticker BEFORE finalising so
            # smart_money_confirmation reflects real UV scanner detections.
            uv_signal = await lookup_uv_signal(candidate.ticker)
            if uv_signal is not None and candidate.direction:
                candidate.smart_money_confirmation = (
                    uv_signal.is_unusual
                    and uv_signal.aligns_with(candidate.direction)
                )
            result = finalise_candidate(
                candidate, evaluation_id, policy_version, config
            )
            if result is not None:
                # Attach UV signal data to the Decision payload for UI.
                # Decision is a frozen Pydantic model, so we must rebuild
                # the FinalisedConvexCandidate with an updated decision.
                updated_decision = result.decision.model_copy(
                    update={"convex_uv_signal": uv_to_dict(uv_signal)}
                )
                finalised.append(
                    FinalisedConvexCandidate(
                        candidate=result.candidate,
                        tier=result.tier,
                        composite=result.composite,
                        decision=updated_decision,
                    )
                )

    if stage_events:
        try:
            await ConvexStageEventTable.put_batch(stage_events)
        except Exception as e:
            logger.warning("ConvexStageEvent persistence failed: %s", e)

    if finalised:
        try:
            await ConvexEvaluationTable.put_batch(
                [_to_convex_evaluation(f, run_id) for f in finalised]
            )
        except Exception as e:
            logger.warning("ConvexEvaluation persistence failed: %s", e)

    return finalised


def _to_convex_evaluation(
    finalised: FinalisedConvexCandidate, run_id: str
) -> ConvexEvaluation:
    """Project a FinalisedConvexCandidate into the persisted ConvexEvaluation."""
    candidate = finalised.candidate
    return ConvexEvaluation(
        evaluation_id=finalised.decision.evaluation_id,
        run_id=run_id,
        ticker=candidate.ticker,
        direction=candidate.direction or "ambiguous",
        convex_tier=finalised.tier.value,
        composite_strength=finalised.composite,
        smart_money_confirmation=candidate.smart_money_confirmation,
        selected_call=_to_selected_contract(candidate.selected_call),
        selected_put=_to_selected_contract(candidate.selected_put),
        decision=finalised.decision,
    )


async def _emit_downstream_artifacts(
    finalised: list[FinalisedConvexCandidate],
    config: ConvexConfig,
) -> None:
    """Fan out positions, theses, and alerts for finalised candidates.

    Each consumer is wrapped so a single ticker's failure cannot block
    the rest. Positions and theses run unconditionally (they are
    isolated DynamoDB writes / LLM calls); Slack is gated on
    ``config.alerts_enabled`` so production alerting only flips at
    cutover.
    """
    # Paper trading positions — always create.
    try:
        from app.paper_trading.position_manager import (
            create_position_from_convex_candidate,
        )

        position_count = 0
        for candidate in finalised:
            try:
                position = await create_position_from_convex_candidate(candidate)
                if position is not None:
                    position_count += 1
            except Exception as e:
                logger.warning(
                    f"Convex paper position create failed for "
                    f"{candidate.candidate.ticker}: {e}"
                )
        logger.info(f"Convex paper positions created: {position_count}/{len(finalised)}")
    except Exception as e:
        logger.warning(f"Convex paper trading wiring failed: {e}")

    # LLM trade theses — always generate (rate-limited internally).
    try:
        from app.db.tables import TradeThesisTable
        from app.llm.generator import ThesisGenerator

        generator = ThesisGenerator()
        thesis_count = 0
        for candidate in finalised:
            try:
                thesis = await generator.generate_convex(candidate)
                await TradeThesisTable.put(thesis)
                thesis_count += 1
            except Exception as e:
                logger.warning(
                    f"Convex thesis generation failed for "
                    f"{candidate.candidate.ticker}: {e}"
                )
        logger.info(f"Convex theses generated: {thesis_count}/{len(finalised)}")
    except Exception as e:
        logger.warning(f"Convex thesis wiring failed: {e}")

    # Slack alerts — gated on the cutover flag.
    if not config.alerts_enabled:
        logger.info(
            "Convex Slack alerts disabled (alerts_enabled=False); skipping."
        )
        return

    try:
        from app.services.slack import get_slack_service

        service = get_slack_service()
        sent_count = 0
        for candidate in finalised:
            try:
                sent, reason = await service.send_convex_alert(candidate)
                if sent:
                    sent_count += 1
                else:
                    logger.info(
                        f"Convex alert skipped for {candidate.candidate.ticker}: {reason}"
                    )
            except Exception as e:
                logger.warning(
                    f"Convex Slack alert failed for "
                    f"{candidate.candidate.ticker}: {e}"
                )
        logger.info(f"Convex alerts sent: {sent_count}/{len(finalised)}")
    except Exception as e:
        logger.warning(f"Convex Slack wiring failed: {e}")


def _to_selected_contract(c: Optional[Any]) -> Optional[ConvexSelectedContract]:
    if c is None:
        return None
    return ConvexSelectedContract(
        option_ticker=c.option_ticker,
        option_type=c.option_type,
        strike=c.strike,
        expiry=c.expiry,
        dte=c.dte,
        delta=c.delta,
        bid=c.bid,
        ask=c.ask,
        open_interest=c.open_interest,
        volume=c.volume,
    )
