"""Scanner Orchestrator.

Coordinates execution of all scanners across the ticker watchlist with:
- **Phase-based execution for optimal performance**:
  - Phase 1: Daily bars prefetch (grouped API, O(days) calls)
  - Phase 2: Fast scanners (Breakout, Compression) - no options data needed
  - Phase 3: Options scanners with unified chain fetch per ticker
  - Phase 4: Contract selection using cached chains
- Batch processing for efficiency
- Concurrent execution with rate limiting (20 concurrent tickers)
- Result merging into Opportunities
- Stage 2: Underlying quality filters
- Stage 3: Contract selection and evaluation creation
- Stage 4: Feature computation
- Stage 5: Pillar scoring
- Stage 6: Hard gates
- Stage 7: Decision logic (includes LLM thesis generation)
- Stage 8: Paper trading
- DynamoDB persistence with BatchWriteItem
- Telemetry via StageEvent recording
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# Stage imports are done lazily in run_scan() to avoid circular imports
# The following TYPE_CHECKING imports are for type hints only
from typing import TYPE_CHECKING, Any, Optional, Sequence

from app.core.pipeline import PipelineOrchestrator
from app.core.schemas import (
    Decision,
    Evaluation,
    Opportunity,
    PipelineStage,
    Policy,
    PolicyConfig,
    ScannerTrigger,
    TradeThesis,
)
from app.core.watchlist import WatchlistManager
from app.db.tables import EvaluationTable, OpportunityTable, PolicyTable
from app.filters.underlying import UnderlyingFilter
from app.scanners.base import BaseScanner, ScanContext, ScanResult
from app.scanners.breakout import BreakoutScanner
from app.scanners.cheap_options import CheapOptionsScanner
from app.scanners.compression import CompressionScanner
from app.scanners.merger import OpportunityMerger
from app.selection.contract_selector import ContractSelector
from app.selection.evaluation_builder import EvaluationBuilder
from app.services.polygon import AggregatedOptionsVolume, PolygonClient

if TYPE_CHECKING:
    from app.features.models import FeatureSet
    from app.gates.models import GateEvaluation
    from app.pillars.models import PillarResult

logger = logging.getLogger(__name__)


@dataclass
class ScanRunResult:
    """Result of a complete scanner run including all pipeline stages."""

    run_id: str
    timestamp: str
    tickers_scanned: int
    total_triggers: int
    opportunities_created: int
    opportunities_filtered: int
    evaluations_created: int
    opportunities: list[Opportunity]
    filtered_opportunities: list[Opportunity]
    evaluations: list[Evaluation]
    scanner_stats: dict[str, dict[str, int]]
    filter_stats: Optional[dict[str, Any]] = None
    selection_stats: Optional[dict[str, Any]] = None
    # Stage 4-8 results (using Any to avoid circular import issues)
    feature_sets: list[Any] = field(default_factory=list)
    pillar_results: dict[str, list[Any]] = field(default_factory=dict)
    gate_evaluations: dict[str, Any] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    theses: list[TradeThesis] = field(default_factory=list)
    paper_trading_results: dict[str, Any] = field(default_factory=dict)
    # Verdict counts
    approve_count: int = 0
    watch_count: int = 0
    reject_count: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


class ScannerOrchestrator:
    """Orchestrates execution of all scanners and pipeline stages."""

    def __init__(
        self,
        data_provider: Optional[Any] = None,
        polygon_client: Optional[PolygonClient] = None,
        pipeline_orchestrator: Optional[PipelineOrchestrator] = None,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            data_provider: Optional DataProvider for unified data access.
                           When provided (backtest mode), PolygonClient is not created.
            polygon_client: Optional Polygon client (creates new if not provided)
            pipeline_orchestrator: Optional pipeline orchestrator for telemetry
        """
        self._data_provider = data_provider
        self._polygon = polygon_client
        self._pipeline = pipeline_orchestrator or PipelineOrchestrator()
        self._merger = OpportunityMerger()

        # Initialize all scanners (UnusualVolumeScanner disabled for performance)
        self._scanners: list[BaseScanner] = [
            BreakoutScanner(),
            CompressionScanner(),
            CheapOptionsScanner(),
        ]

    async def run_scan(
        self,
        policy_config: Optional[PolicyConfig] = None,
        tickers: Optional[list[str]] = None,
        run_id: Optional[str] = None,
        run_full_pipeline: bool = True,
        as_of_date: Optional[date] = None,
    ) -> ScanRunResult:
        """Run a complete scan across all scanners and pipeline stages.

        Executes:
        - Stage 1: Opportunity Discovery (Scanners)
        - Stage 2: Underlying Quality Filters
        - Stage 3: Contract Selection

        Args:
            policy_config: Policy configuration (loads active if not provided)
            tickers: Optional ticker list (uses watchlist if not provided)
            run_id: Optional run ID for telemetry (creates new if not provided)
            run_full_pipeline: If True, run all stages; if False, only run scanners
            as_of_date: Target date for backtesting (defaults to today for live mode)

        Returns:
            ScanRunResult with all opportunities, evaluations, and statistics
        """
        start_time = datetime.now(timezone.utc)
        timestamp = start_time.isoformat()
        errors: list[str] = []

        # Load policy if not provided
        if policy_config is None:
            policy = await PolicyTable.get_active()
            if policy is None:
                raise ValueError("No active policy found")
            policy_config = policy.config
            policy_version = policy.version
        else:
            policy = await PolicyTable.get_active()
            policy_version = policy.version if policy else "unknown"

        # Get tickers from watchlist if not provided
        if tickers is None:
            watchlist = WatchlistManager.from_policy(policy_config)
            tickers = watchlist.tickers

        if not tickers:
            raise ValueError("No tickers to scan")

        from app.config import get_settings
        settings = get_settings()

        logger.info(f"Starting scan of {len(tickers)} tickers with {len(self._scanners)} scanners")

        # Start or get pipeline run
        if run_id is None:
            pipeline_run = await self._pipeline.start_run(policy_version)
            run_id = pipeline_run.run_id

        # Initialize result variables
        opportunities: list[Opportunity] = []
        filtered_opportunities: list[Opportunity] = []
        evaluations: list[Evaluation] = []
        scanner_stats: dict[str, dict[str, int]] = {}
        filter_stats: Optional[dict[str, Any]] = None
        selection_stats: Optional[dict[str, Any]] = None
        approve_count: int = 0
        watch_count: int = 0
        reject_count: int = 0

        # Create scan context with increased concurrency for performance
        # Using 20 concurrent requests as Polygon allows higher limits
        effective_concurrency = max(policy_config.watchlist.max_concurrent_requests, 20)
        effective_date = as_of_date or date.today()

        async with AsyncExitStack() as stack:
            # Determine data source: DataProvider (backtest) or PolygonClient (live)
            if self._data_provider:
                data_provider = self._data_provider
                polygon = None
            else:
                # Live mode: validate Polygon API key and create clients
                if not settings.polygon_api_key:
                    logger.critical(
                        "POLYGON API KEY NOT CONFIGURED. "
                        "All scanners require market data from Polygon.io. "
                        "Set the key via: "
                        "AWS_REGION=us-west-1 POLYGON_API_KEY=<key> "
                        "./scripts/deploy.sh secret"
                    )
                    raise ValueError(
                        "Polygon API key not configured. Cannot fetch market data. "
                        "Set the secret in AWS Secrets Manager."
                    )
                polygon = await stack.enter_async_context(
                    PolygonClient(max_concurrent=effective_concurrency)
                )
                from app.core.live_data_provider import LiveDataProvider
                data_provider = LiveDataProvider(polygon)

            # ================================================================
            # STAGE 1: Opportunity Discovery (Scanners)
            # Executed in 4 phases for optimal performance:
            #   Phase 1: Daily bars prefetch (grouped API)
            #   Phase 2: Fast scanners (no options data needed)
            #   Phase 3: Options scanners (unified chain fetch per ticker)
            #   Phase 4: Results merge
            # ================================================================
            context = ScanContext.create(
                polygon, policy_config,
                data_provider=data_provider,
                as_of_date=effective_date,
            )

            # Initialize cache structures for options data
            context.cached_data["options_chains"] = {}
            context.cached_data["options_volume"] = {}

            # ================================================================
            # PHASE 1: Daily Bars Prefetch
            # Uses DataProvider for unified access (Polygon API or S3 parquet)
            # ================================================================
            logger.info(f"Phase 1: Prefetching daily bars for {len(tickers)} tickers...")
            phase1_start = datetime.now(timezone.utc)

            daily_bars_data = await data_provider.get_daily_bars_batch(
                tickers, end_date=effective_date, lookback_days=60
            )
            context.cached_data["daily_bars"] = daily_bars_data

            phase1_duration = int((datetime.now(timezone.utc) - phase1_start).total_seconds() * 1000)
            logger.info(f"Phase 1 complete: {len(daily_bars_data)} tickers in {phase1_duration}ms")

            all_results: list[ScanResult] = []

            # ================================================================
            # PHASE 2: Fast Scanners (Breakout + Compression)
            # These scanners only need daily bars (already cached)
            # Run them first to identify tickers we can skip in Phase 3
            # ================================================================
            logger.info("Phase 2: Running fast scanners (Breakout, Compression)...")
            phase2_start = datetime.now(timezone.utc)

            fast_scanners = [BreakoutScanner(), CompressionScanner()]
            phase2_results, phase2_stats, phase2_errors = await self._run_scanner_phase(
                scanners=fast_scanners,
                tickers=tickers,
                context=context,
                max_concurrent=effective_concurrency,
            )

            all_results.extend(phase2_results)
            scanner_stats.update(phase2_stats)
            errors.extend(phase2_errors)

            # Identify tickers that triggered in Phase 2 (skip options fetch for these)
            phase2_triggered = {r.ticker for r in phase2_results if r.triggered}

            phase2_duration = int((datetime.now(timezone.utc) - phase2_start).total_seconds() * 1000)
            logger.info(
                f"Phase 2 complete: {len(phase2_triggered)} triggered, "
                f"{len(tickers) - len(phase2_triggered)} remaining for Phase 3, "
                f"{phase2_duration}ms"
            )

            # ================================================================
            # PHASE 3: Options Scanners (Cheap Options + Unusual Volume)
            # Key optimizations:
            #   1. Skip tickers already triggered in Phase 2
            #   2. Fetch options chain ONCE per ticker (0-45 DTE filter)
            #   3. Compute aggregated volume from cached chain (no separate API call)
            #   4. Run both scanners on cached data
            #   5. Early exit on first trigger
            # ================================================================
            remaining_tickers = [t for t in tickers if t not in phase2_triggered]

            if remaining_tickers:
                logger.info(f"Phase 3: Running options scanners on {len(remaining_tickers)} tickers...")
                phase3_start = datetime.now(timezone.utc)

                options_scanners = [CheapOptionsScanner()]
                phase3_results, phase3_stats, phase3_errors = await self._run_options_scanner_phase(
                    scanners=options_scanners,
                    tickers=remaining_tickers,
                    context=context,
                    polygon=polygon,
                    max_concurrent=5,  # Low concurrency to avoid Polygon rate limits
                )

                all_results.extend(phase3_results)
                scanner_stats.update(phase3_stats)
                errors.extend(phase3_errors)

                phase3_duration = int((datetime.now(timezone.utc) - phase3_start).total_seconds() * 1000)
                phase3_triggered = sum(1 for r in phase3_results if r.triggered)
                logger.info(
                    f"Phase 3 complete: {phase3_triggered} triggered, {phase3_duration}ms"
                )
            else:
                logger.info("Phase 3: Skipped (all tickers triggered in Phase 2)")

            # Merge results into opportunities
            self._merger.set_timestamp(timestamp)
            opportunities = self._merger.merge(all_results)

            # Get merge stats
            merge_stats = self._merger.get_merge_stats(all_results, opportunities)

            # Persist opportunities to DynamoDB
            await self._persist_opportunities(opportunities)

            # Record Stage 1 event
            await self._record_discovery_stage_event(
                run_id=run_id,
                tickers_scanned=len(tickers),
                opportunities=opportunities,
                scanner_stats=scanner_stats,
                merge_stats=merge_stats,
            )

            # Continue with full pipeline if requested
            if run_full_pipeline and opportunities:
                # ================================================================
                # STAGE 2: Underlying Quality Filters
                # ================================================================
                try:
                    await self._pipeline.update_current_stage(
                        run_id, PipelineStage.UNDERLYING_FILTERS
                    )

                    # Initialize earnings cache for earnings window filter
                    earnings_cache = None
                    if policy_config.underlying_filter.exclude_earnings_within_days > 0:
                        try:
                            from app.services.earnings_cache import EarningsCacheService
                            from app.services.finnhub import FinnhubClient
                            if not settings.finnhub_api_key:
                                logger.warning(
                                    "Finnhub API key not configured, earnings filter disabled"
                                )
                            else:
                                finnhub = await stack.enter_async_context(
                                    FinnhubClient(api_key=settings.finnhub_api_key)
                                )
                                earnings_cache = EarningsCacheService(finnhub_client=finnhub)
                        except Exception as e:
                            logger.warning(f"Could not initialize earnings cache: {e}")

                    underlying_filter = UnderlyingFilter(
                        policy_config.underlying_filter,
                        earnings_cache=earnings_cache,
                        data_provider=data_provider,
                        as_of_date=effective_date,
                    )
                    if polygon:
                        underlying_filter.set_polygon_client(polygon)

                    filtered_opportunities, filter_result = await underlying_filter.filter_opportunities(
                        opportunities
                    )
                    filter_stats = filter_result.to_dict()

                    # Record Stage 2 event
                    await self._pipeline.record_stage_event(
                        run_id=run_id,
                        stage=PipelineStage.UNDERLYING_FILTERS,
                        items_in=len(opportunities),
                        items_out=len(filtered_opportunities),
                        drop_reasons=filter_result.failure_counts,
                        metadata=filter_stats,
                    )

                    logger.info(
                        f"Stage 2 complete: {len(opportunities)} -> {len(filtered_opportunities)} opportunities"
                    )

                except Exception as e:
                    error_msg = f"Underlying filter stage failed: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)
                    await self._pipeline.record_stage_event(
                        run_id=run_id,
                        stage=PipelineStage.UNDERLYING_FILTERS,
                        items_in=len(opportunities),
                        items_out=0,
                        metadata={"error": str(e), "status": "failed"},
                    )
                    filtered_opportunities = opportunities  # Fall through with unfiltered

                # ================================================================
                # STAGE 3: Contract Selection
                # ================================================================
                if filtered_opportunities:
                    try:
                        await self._pipeline.update_current_stage(
                            run_id, PipelineStage.CONTRACT_SELECTION
                        )

                        contract_selector = ContractSelector(
                            policy_config.contract_selection,
                            data_provider=data_provider,
                            as_of_date=effective_date,
                        )
                        if polygon:
                            contract_selector.set_polygon_client(polygon)

                        selection_result = await contract_selector.select_contracts(
                            filtered_opportunities
                        )

                        # Build evaluations from selected candidates
                        policy_hash = Policy.compute_hash(policy_config)
                        evaluation_builder = EvaluationBuilder(
                            policy_version, policy_hash, policy_snapshot_id=policy_version
                        )
                        evaluations = evaluation_builder.build_evaluations(
                            selection_result.selected_candidates,
                            filtered_opportunities,
                        )

                        # Persist evaluations to DynamoDB
                        await self._persist_evaluations(evaluations)

                        # Get selection stats
                        selection_stats = selection_result.telemetry.get_summary()

                        # Record Stage 3 event
                        await self._pipeline.record_stage_event(
                            run_id=run_id,
                            stage=PipelineStage.CONTRACT_SELECTION,
                            items_in=len(filtered_opportunities),
                            items_out=len(evaluations),
                            drop_reasons={},  # Selection doesn't drop, just ranks
                            metadata=selection_result.telemetry.to_stage_metadata(),
                        )

                        # Add selection errors
                        errors.extend(selection_result.errors)

                        logger.info(
                            f"Stage 3 complete: {len(filtered_opportunities)} opportunities -> "
                            f"{len(evaluations)} evaluations"
                        )

                    except Exception as e:
                        error_msg = f"Contract selection stage failed: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        await self._pipeline.record_stage_event(
                            run_id=run_id,
                            stage=PipelineStage.CONTRACT_SELECTION,
                            items_in=len(filtered_opportunities),
                            items_out=0,
                            metadata={"error": str(e), "status": "failed"},
                        )

                # ================================================================
                # STAGE 4: Feature Computation
                # ================================================================
                # Lazy imports to avoid circular import issues
                from app.decision.stage import run_decision_logic
                from app.features.stage import run_feature_computation
                from app.gates.stage import run_hard_gates
                from app.paper_trading.stage import run_paper_trading
                from app.pillars.stage import run_pillar_scoring

                feature_sets: list[FeatureSet] = []
                if evaluations:
                    try:
                        feature_sets = await run_feature_computation(
                            run_id=run_id,
                            evaluations=evaluations,
                            opportunities=filtered_opportunities,
                            polygon_client=polygon,
                            orchestrator=self._pipeline,
                            config=policy_config.features,
                            persist_features=True,
                            data_provider=data_provider,
                            as_of_date=effective_date,
                        )

                        logger.info(
                            f"Stage 4 complete: {len(feature_sets)} feature sets computed"
                        )

                    except Exception as e:
                        error_msg = f"Feature computation stage failed: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        await self._pipeline.record_stage_event(
                            run_id=run_id,
                            stage=PipelineStage.FEATURE_COMPUTATION,
                            items_in=len(evaluations),
                            items_out=0,
                            metadata={"error": str(e), "status": "failed"},
                        )
                else:
                    await self._pipeline.record_stage_event(
                        run_id=run_id,
                        stage=PipelineStage.FEATURE_COMPUTATION,
                        items_in=0,
                        items_out=0,
                        metadata={"status": "skipped", "reason": "no_evaluations"},
                    )

                # ================================================================
                # STAGE 5: Pillar Scoring
                # ================================================================
                pillar_results: dict[str, list[PillarResult]] = {}
                if evaluations and feature_sets:
                    try:
                        pillar_results = await run_pillar_scoring(
                            run_id=run_id,
                            evaluations=evaluations,
                            feature_sets=feature_sets,
                            opportunities=filtered_opportunities,
                            orchestrator=self._pipeline,
                            config=policy_config.pillars,
                            persist_scores=True,
                        )

                        logger.info(
                            f"Stage 5 complete: {len(pillar_results)} evaluations scored"
                        )

                    except Exception as e:
                        error_msg = f"Pillar scoring stage failed: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        await self._pipeline.record_stage_event(
                            run_id=run_id,
                            stage=PipelineStage.PILLAR_SCORING,
                            items_in=len(evaluations),
                            items_out=0,
                            metadata={"error": str(e), "status": "failed"},
                        )
                else:
                    await self._pipeline.record_stage_event(
                        run_id=run_id,
                        stage=PipelineStage.PILLAR_SCORING,
                        items_in=0,
                        items_out=0,
                        metadata={"status": "skipped", "reason": "missing_prerequisites"},
                    )

                # ================================================================
                # STAGE 6: Hard Gates
                # ================================================================
                gate_evaluations: dict[str, GateEvaluation] = {}
                logger.info(
                    f"Stage 6 guard: evaluations={len(evaluations) if evaluations else 0}, "
                    f"feature_sets={len(feature_sets) if feature_sets else 0}"
                )
                if evaluations and feature_sets:
                    try:
                        gate_evaluations = await run_hard_gates(
                            run_id=run_id,
                            evaluations=evaluations,
                            feature_sets=feature_sets,
                            opportunities=filtered_opportunities,
                            orchestrator=self._pipeline,
                            config=policy_config.gates,
                            persist_results=True,
                        )

                        passed_count = sum(
                            1 for ge in gate_evaluations.values() if ge.all_passed
                        )
                        logger.info(
                            f"Stage 6 complete: {passed_count}/{len(gate_evaluations)} passed gates"
                        )

                    except Exception as e:
                        error_msg = f"Hard gates stage failed: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        await self._pipeline.record_stage_event(
                            run_id=run_id,
                            stage=PipelineStage.HARD_GATES,
                            items_in=len(evaluations),
                            items_out=0,
                            metadata={"error": str(e), "status": "failed"},
                        )
                else:
                    await self._pipeline.record_stage_event(
                        run_id=run_id,
                        stage=PipelineStage.HARD_GATES,
                        items_in=0,
                        items_out=0,
                        metadata={"status": "skipped", "reason": "missing_prerequisites"},
                    )

                # ================================================================
                # STAGE 7: Decision Logic (includes LLM thesis generation)
                # ================================================================
                decisions: dict[str, Decision] = {}
                theses: list[TradeThesis] = []
                if evaluations and pillar_results and gate_evaluations:
                    try:
                        # Build scanner triggers map for LLM context
                        scanner_triggers_map = self._build_scanner_triggers_map(
                            evaluations, filtered_opportunities
                        )

                        # Build features map for LLM context
                        features_map = self._build_features_map(feature_sets)

                        decisions, theses = await run_decision_logic(
                            run_id=run_id,
                            evaluations=evaluations,
                            pillar_results=pillar_results,
                            gate_evaluations=gate_evaluations,
                            orchestrator=self._pipeline,
                            decision_config=policy_config.decision,
                            pillar_weights=policy_config.pillars.weights,
                            thesis_config=policy_config.thesis,
                            scanner_triggers=scanner_triggers_map,
                            features=features_map,
                            persist_decisions=True,
                            check_concentration=True,
                            generate_theses=False,
                        )

                        # Count verdicts
                        from app.core.schemas import Verdict
                        approve_count = sum(
                            1 for d in decisions.values() if d.verdict == Verdict.APPROVE
                        )
                        watch_count = sum(
                            1 for d in decisions.values() if d.verdict == Verdict.WATCH
                        )
                        reject_count = sum(
                            1 for d in decisions.values() if d.verdict == Verdict.REJECT
                        )

                        logger.info(
                            f"Stage 7 complete: {approve_count} APPROVE, "
                            f"{watch_count} WATCH, {reject_count} REJECT, "
                            f"{len([t for t in theses if t.status.value == 'COMPLETED'])} theses"
                        )

                    except Exception as e:
                        error_msg = f"Decision logic stage failed: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        await self._pipeline.record_stage_event(
                            run_id=run_id,
                            stage=PipelineStage.DECISION_LOGIC,
                            items_in=len(evaluations),
                            items_out=0,
                            metadata={"error": str(e), "status": "failed"},
                        )
                else:
                    await self._pipeline.record_stage_event(
                        run_id=run_id,
                        stage=PipelineStage.DECISION_LOGIC,
                        items_in=0,
                        items_out=0,
                        metadata={"status": "skipped", "reason": "missing_prerequisites"},
                    )

                # ================================================================
                # STAGE 8: Paper Trading
                # ================================================================
                paper_trading_results: dict[str, Any] = {}
                if evaluations and decisions:
                    try:
                        paper_trading_results = await run_paper_trading(
                            run_id=run_id,
                            evaluations=evaluations,
                            decisions=decisions,
                            gate_evaluations=gate_evaluations,
                            orchestrator=self._pipeline,
                            config=policy_config.tracking,
                            create_positions=True,
                            track_shadows=True,
                        )

                        logger.info(
                            f"Stage 8 complete: {paper_trading_results.get('positions_created', 0)} "
                            f"positions created, {paper_trading_results.get('shadow_candidates', 0)} shadows"
                        )

                    except Exception as e:
                        error_msg = f"Paper trading stage failed: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        await self._pipeline.record_stage_event(
                            run_id=run_id,
                            stage=PipelineStage.PAPER_TRADING,
                            items_in=len(evaluations),
                            items_out=0,
                            metadata={"error": str(e), "status": "failed"},
                        )
                else:
                    await self._pipeline.record_stage_event(
                        run_id=run_id,
                        stage=PipelineStage.PAPER_TRADING,
                        items_in=0,
                        items_out=0,
                        metadata={"status": "skipped", "reason": "missing_prerequisites"},
                    )

                # Mark pipeline run as complete
                await self._pipeline.complete_run(run_id, status="completed")

        # Calculate duration
        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Count total triggers
        total_triggers = sum(
            stats.get("triggered", 0) for stats in scanner_stats.values()
        )

        # Get verdict counts (initialized before try blocks so they're always defined)
        final_approve_count = approve_count
        final_watch_count = watch_count
        final_reject_count = reject_count

        result = ScanRunResult(
            run_id=run_id,
            timestamp=timestamp,
            tickers_scanned=len(tickers),
            total_triggers=total_triggers,
            opportunities_created=len(opportunities),
            opportunities_filtered=len(filtered_opportunities),
            evaluations_created=len(evaluations),
            opportunities=opportunities,
            filtered_opportunities=filtered_opportunities,
            evaluations=evaluations,
            scanner_stats=scanner_stats,
            filter_stats=filter_stats,
            selection_stats=selection_stats,
            # Stage 4-8 results
            feature_sets=feature_sets if 'feature_sets' in dir() else [],
            pillar_results=pillar_results if 'pillar_results' in dir() else {},
            gate_evaluations=gate_evaluations if 'gate_evaluations' in dir() else {},
            decisions=decisions if 'decisions' in dir() else {},
            theses=theses if 'theses' in dir() else [],
            paper_trading_results=paper_trading_results if 'paper_trading_results' in dir() else {},
            approve_count=final_approve_count,
            watch_count=final_watch_count,
            reject_count=final_reject_count,
            errors=errors,
            duration_ms=duration_ms,
        )

        logger.info(
            f"Pipeline complete: {len(tickers)} tickers, "
            f"{total_triggers} triggers, "
            f"{len(opportunities)} opportunities, "
            f"{len(filtered_opportunities)} filtered, "
            f"{len(evaluations)} evaluations, "
            f"{final_approve_count} APPROVE, {final_watch_count} WATCH, {final_reject_count} REJECT, "
            f"{duration_ms}ms"
        )

        return result

    async def _persist_opportunities(
        self,
        opportunities: list[Opportunity],
    ) -> None:
        """Persist opportunities to DynamoDB.

        Uses individual puts for now; could be optimized to batch writes.

        Args:
            opportunities: List of opportunities to persist
        """
        success_count = 0
        for opp in opportunities:
            try:
                await OpportunityTable.put(opp)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to persist opportunity {opp.opportunity_id}: {e}")

        fail_count = len(opportunities) - success_count
        if fail_count > 0:
            logger.warning(
                f"Persisted {success_count}/{len(opportunities)} opportunities "
                f"({fail_count} failures)"
            )
        else:
            logger.info(f"Persisted {success_count} opportunities to DynamoDB")

    async def _persist_evaluations(
        self,
        evaluations: list[Evaluation],
    ) -> None:
        """Persist evaluations to DynamoDB.

        Args:
            evaluations: List of evaluations to persist
        """
        success_count = 0
        for evaluation in evaluations:
            try:
                await EvaluationTable.put(evaluation)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to persist evaluation {evaluation.evaluation_id}: {e}")

        fail_count = len(evaluations) - success_count
        if fail_count > 0:
            logger.warning(
                f"Persisted {success_count}/{len(evaluations)} evaluations "
                f"({fail_count} failures)"
            )
        else:
            logger.info(f"Persisted {success_count} evaluations to DynamoDB")

    async def _record_discovery_stage_event(
        self,
        run_id: str,
        tickers_scanned: int,
        opportunities: list[Opportunity],
        scanner_stats: dict[str, dict[str, int]],
        merge_stats: dict[str, int],
    ) -> None:
        """Record stage event for Stage 1 (Opportunity Discovery).

        Args:
            run_id: Pipeline run ID
            tickers_scanned: Number of tickers scanned
            opportunities: Created opportunities
            scanner_stats: Statistics per scanner
            merge_stats: Statistics from merge operation
        """
        # Calculate drop reasons (tickers that didn't trigger any scanner)
        drop_reasons: dict[str, int] = {}

        # Count triggers per scanner for drop analysis
        for scanner_type, stats in scanner_stats.items():
            no_trigger_count = stats.get("total_scanned", 0) - stats.get("triggered", 0)
            if no_trigger_count > 0:
                drop_reasons[f"NO_TRIGGER_{scanner_type}"] = no_trigger_count

        # Metadata includes detailed stats
        metadata: dict[str, Any] = {
            "scanner_stats": scanner_stats,
            "merge_stats": merge_stats,
            "opportunities_by_direction": {
                "CALL": merge_stats.get("direction_call", 0),
                "PUT": merge_stats.get("direction_put", 0),
                "NONE": merge_stats.get("direction_none", 0),
            },
        }

        await self._pipeline.record_stage_event(
            run_id=run_id,
            stage=PipelineStage.OPPORTUNITY_DISCOVERY,
            items_in=tickers_scanned,
            items_out=len(opportunities),
            drop_reasons=drop_reasons,
            metadata=metadata,
        )

    def _build_scanner_triggers_map(
        self,
        evaluations: Sequence[Evaluation],
        opportunities: Sequence[Opportunity],
    ) -> dict[str, Sequence[ScannerTrigger]]:
        """Build a map of evaluation_id to scanner triggers.

        Links evaluations to their originating opportunity's scanner triggers
        for use in LLM thesis generation.

        Args:
            evaluations: List of evaluations
            opportunities: List of opportunities

        Returns:
            Dict mapping evaluation_id to list of ScannerTrigger
        """
        # Build opportunity lookup by ticker
        opp_by_ticker: dict[str, Opportunity] = {
            opp.underlying_ticker: opp for opp in opportunities
        }

        scanner_triggers_map: dict[str, Sequence[ScannerTrigger]] = {}

        for evaluation in evaluations:
            ticker = evaluation.underlying_ticker
            if ticker in opp_by_ticker:
                opp = opp_by_ticker[ticker]
                scanner_triggers_map[evaluation.evaluation_id] = opp.scanner_triggers
            else:
                scanner_triggers_map[evaluation.evaluation_id] = []

        return scanner_triggers_map

    def _build_features_map(
        self,
        feature_sets: Sequence[Any],  # FeatureSet (typed as Any to avoid circular import)
    ) -> dict[str, dict[str, Any]]:
        """Build a map of evaluation_id to feature values.

        Extracts feature values for use in LLM thesis generation.

        Args:
            feature_sets: List of computed FeatureSets

        Returns:
            Dict mapping evaluation_id to dict of feature_name -> value
        """
        features_map: dict[str, dict[str, Any]] = {}

        for fs in feature_sets:
            features_map[fs.evaluation_id] = {
                # Underlying technical features
                "close": fs.close,
                "sma20": fs.sma20,
                "sma50": fs.sma50,
                "return_5d": fs.return_5d,
                "return_20d": fs.return_20d,
                "trend_aligned_bullish": fs.trend_aligned_bullish,
                "trend_aligned_bearish": fs.trend_aligned_bearish,
                "atr14": fs.atr14,
                "atr14_pct": fs.atr14_pct,
                # Relative strength
                "spy_return_5d": fs.spy_return_5d,
                "spy_return_20d": fs.spy_return_20d,
                "rs_5d": fs.rs_5d,
                "rs_20d": fs.rs_20d,
                # Volatility
                "rv20": fs.rv20,
                "iv": fs.iv,
                "iv_rv_ratio": fs.iv_rv_ratio,
                "iv_percentile": fs.iv_percentile,
                "iv_10d_change": fs.iv_10d_change,
                "iv_regime": fs.iv_regime,
                # Contract
                "theta_pct": fs.theta_pct,
                "theta_adjusted_edge": fs.theta_adjusted_edge,
                # Liquidity
                "oi_5d_change_pct": fs.oi_5d_change_pct,
                # Catalyst
                "days_to_earnings": fs.days_to_earnings,
                "recent_sec_filing": fs.recent_sec_filing,
            }

        return features_map

    async def _run_scanner_phase(
        self,
        scanners: list[BaseScanner],
        tickers: list[str],
        context: ScanContext,
        max_concurrent: int,
    ) -> tuple[list[ScanResult], dict[str, dict[str, int]], list[str]]:
        """Run a group of scanners in parallel on all tickers.

        Used for Phase 2 (fast scanners that don't need options data).

        Args:
            scanners: List of scanners to run
            tickers: Tickers to scan
            context: Scan context with cached data
            max_concurrent: Max concurrent requests

        Returns:
            Tuple of (results, stats_by_scanner, errors)
        """
        all_results: list[ScanResult] = []
        stats: dict[str, dict[str, int]] = {}
        errors: list[str] = []

        async def run_scanner_with_stats(
            scanner: BaseScanner,
        ) -> tuple[list[ScanResult], dict[str, int], Optional[str]]:
            try:
                scanner_start = datetime.now(timezone.utc)
                results = await scanner.scan_batch(tickers, context, max_concurrent)
                duration_ms = int((datetime.now(timezone.utc) - scanner_start).total_seconds() * 1000)

                scanner_stats = scanner.log_scan_stats(results)
                scanner_stats["duration_ms"] = duration_ms

                return results, scanner_stats, None
            except Exception as e:
                error_msg = f"Scanner {scanner.scanner_type} failed: {e}"
                logger.error(error_msg)
                return [], {}, error_msg

        tasks = [run_scanner_with_stats(s) for s in scanners]
        outputs = await asyncio.gather(*tasks)

        for scanner, (results, scanner_stats, error) in zip(scanners, outputs):
            if error:
                errors.append(error)
            else:
                stats[scanner.scanner_type.value] = scanner_stats
                all_results.extend(results)

        return all_results, stats, errors

    async def _run_options_scanner_phase(
        self,
        scanners: list[BaseScanner],
        tickers: list[str],
        context: ScanContext,
        polygon: Optional[PolygonClient] = None,
        max_concurrent: int = 5,
    ) -> tuple[list[ScanResult], dict[str, dict[str, int]], list[str]]:
        """Run options scanners with MINIMAL data fetching.

        KEY OPTIMIZATION: Uses get_options_chain_minimal() which:
        1. Fetches ONLY first page (up to 1000 contracts, NO pagination)
        2. Filters by DTE (7-90 days) for broad coverage
        3. No server-side strike filter — scanners filter ATM programmatically

        This reduces API calls from ~24 per ticker to exactly 1 per ticker.

        In backtest mode (polygon=None), uses context.data_provider instead.

        Args:
            scanners: List of options scanners (Cheap Options, Unusual Volume)
            tickers: Tickers to scan
            context: Scan context with cached data
            polygon: Polygon client for API calls (None in backtest mode)
            max_concurrent: Max concurrent ticker processing

        Returns:
            Tuple of (results, stats_by_scanner, errors)
        """
        all_results: list[ScanResult] = []
        errors: list[str] = []

        # Initialize per-scanner stats tracking
        scanner_stats: dict[str, dict[str, int]] = {
            s.scanner_type.value: {"total_scanned": 0, "triggered": 0, "errors": 0, "duration_ms": 0}
            for s in scanners
        }

        # DTE range: broad fetch (7-90) to capture weekly + monthly expirations.
        # Scanners filter to their specific DTE range programmatically.
        effective_date = context.as_of_date or date.today()
        min_exp_date = (
            datetime.combine(effective_date, datetime.min.time()) + timedelta(days=7)
        ).strftime("%Y-%m-%d")
        max_exp_date = (
            datetime.combine(effective_date, datetime.min.time()) + timedelta(days=90)
        ).strftime("%Y-%m-%d")

        # Semaphore for concurrent ticker processing
        semaphore = asyncio.Semaphore(max_concurrent)
        phase_start = datetime.now(timezone.utc)

        async def process_ticker(ticker: str) -> list[ScanResult]:
            """Process a single ticker through all options scanners."""
            async with semaphore:
                ticker_results: list[ScanResult] = []

                try:
                    # Verify daily bars exist (scanners need them for RV calculation)
                    daily_bars = context.cached_data.get("daily_bars", {}).get(ticker, [])
                    if not daily_bars:
                        for scanner in scanners:
                            ticker_results.append(ScanResult(
                                ticker=ticker,
                                triggered=False,
                                error="No daily bars data",
                            ))
                        return ticker_results

                    # BROAD FETCH: Single API call, no pagination
                    # ATM ±10% strike filter required by Polygon Basic tier.
                    # Wider DTE range (7-90) maximizes data for IV proxy.
                    if polygon:
                        # Live mode: use Polygon API with strike filters
                        underlying_price = daily_bars[-1].close if daily_bars else None
                        strike_gte = (
                            underlying_price * 0.9 if underlying_price else None
                        )
                        strike_lte = (
                            underlying_price * 1.1 if underlying_price else None
                        )
                        chain = await polygon.get_options_chain_minimal(
                            ticker,
                            expiration_date_gte=min_exp_date,
                            expiration_date_lte=max_exp_date,
                            strike_price_gte=strike_gte,
                            strike_price_lte=strike_lte,
                        )
                    elif context.data_provider:
                        # Backtest mode: use DataProvider
                        chain = await context.data_provider.get_options_chain_minimal(
                            ticker, as_of=effective_date,
                        )
                    else:
                        chain = []

                    if not chain:
                        # No options data - return error results for all scanners
                        for scanner in scanners:
                            ticker_results.append(ScanResult(
                                ticker=ticker,
                                triggered=False,
                                error="No options chain data in 7-90 DTE range",
                            ))
                        return ticker_results

                    # Cache broad chain for scanners (NOT for Contract Selection)
                    # Contract Selection will fetch its own full chain for triggered tickers
                    context.cached_data["options_chains"][ticker] = chain

                    # Compute aggregated volume from cached chain
                    # Broader than ATM but sufficient for trigger detection
                    vol_data = self._compute_volume_from_chain(chain, ticker)
                    context.cached_data["options_volume"][ticker] = vol_data

                    # Run each scanner on cached data with early exit
                    for scanner in scanners:
                        try:
                            result = await scanner.scan_ticker(ticker, context)
                            ticker_results.append(result)

                            # Early exit: stop processing if triggered
                            if result.triggered:
                                for remaining_scanner in scanners[scanners.index(scanner) + 1:]:
                                    ticker_results.append(ScanResult(
                                        ticker=ticker,
                                        triggered=False,
                                        metrics={"skipped": "early_exit_triggered"},
                                    ))
                                break

                        except Exception as e:
                            logger.warning(f"Scanner {scanner.scanner_type} failed for {ticker}: {e}")
                            ticker_results.append(ScanResult(
                                ticker=ticker,
                                triggered=False,
                                error=str(e),
                            ))

                except Exception as e:
                    logger.error(f"Failed to process ticker {ticker}: {e}")
                    for scanner in scanners:
                        ticker_results.append(ScanResult(
                            ticker=ticker,
                            triggered=False,
                            error=str(e),
                        ))

                return ticker_results

        # Process all tickers concurrently
        tasks = [process_ticker(t) for t in tickers]
        ticker_outputs = await asyncio.gather(*tasks)

        # Flatten results and update stats
        # Results are returned in scanner order (one result per scanner per ticker)
        scanner_keys = [s.scanner_type.value for s in scanners]
        for ticker_results in ticker_outputs:
            for idx, result in enumerate(ticker_results):
                all_results.append(result)

                # Map result to scanner by index position
                if idx < len(scanner_keys):
                    scanner_key = scanner_keys[idx]
                    if scanner_key in scanner_stats:
                        scanner_stats[scanner_key]["total_scanned"] += 1
                        if result.triggered:
                            scanner_stats[scanner_key]["triggered"] += 1
                        if result.error:
                            scanner_stats[scanner_key]["errors"] += 1

        # Record duration for all scanners
        phase_duration = int((datetime.now(timezone.utc) - phase_start).total_seconds() * 1000)
        for stats in scanner_stats.values():
            stats["duration_ms"] = phase_duration

        return all_results, scanner_stats, errors

    def _compute_volume_from_chain(
        self,
        chain: list[dict[str, Any]],
        ticker: str,
    ) -> AggregatedOptionsVolume:
        """Compute aggregated options volume from cached chain data.

        This eliminates the need for a separate API call to get volume data.

        Args:
            chain: Options chain data from Polygon API
            ticker: Underlying ticker symbol

        Returns:
            AggregatedOptionsVolume with computed totals
        """
        total_call_volume = 0
        total_put_volume = 0
        total_call_oi = 0
        total_put_oi = 0

        for contract in chain:
            day_data = contract.get("day", {})
            details = contract.get("details", {})

            volume = day_data.get("volume", 0) or 0
            oi = day_data.get("open_interest", 0) or 0
            contract_type = details.get("contract_type", "").upper()

            if contract_type == "CALL":
                total_call_volume += volume
                total_call_oi += oi
            elif contract_type == "PUT":
                total_put_volume += volume
                total_put_oi += oi

        # Calculate call/put volume ratio (call volume / put volume)
        # Cap at 999.0 to avoid float("inf") which breaks JSON/DynamoDB serialization
        if total_put_volume > 0:
            call_put_ratio = min(total_call_volume / total_put_volume, 999.0)
        else:
            call_put_ratio = 999.0 if total_call_volume > 0 else 0.0

        return AggregatedOptionsVolume(
            ticker=ticker,
            total_call_volume=total_call_volume,
            total_put_volume=total_put_volume,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            call_put_volume_ratio=call_put_ratio,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    async def run_single_scanner(
        self,
        scanner_name: str,
        policy_config: Optional[PolicyConfig] = None,
        tickers: Optional[list[str]] = None,
    ) -> list[ScanResult]:
        """Run a single scanner (useful for testing/debugging).

        Note: Unusual Volume Scanner has been moved to a serverless architecture
        in backend/lambdas/unusual_volume/. Use the Lambda functions directly.

        Args:
            scanner_name: Name of scanner to run (breakout, compression, cheap_options)
            policy_config: Policy configuration
            tickers: Tickers to scan

        Returns:
            List of ScanResult objects
        """
        scanner_map = {
            "breakout": BreakoutScanner(),
            "compression": CompressionScanner(),
            "cheap_options": CheapOptionsScanner(),
        }

        scanner = scanner_map.get(scanner_name.lower())
        if scanner is None:
            raise ValueError(f"Unknown scanner: {scanner_name}")

        # Load policy if not provided
        if policy_config is None:
            policy = await PolicyTable.get_active()
            if policy is None:
                raise ValueError("No active policy found")
            policy_config = policy.config

        # Get tickers
        if tickers is None:
            watchlist = WatchlistManager.from_policy(policy_config)
            tickers = watchlist.tickers

        # Run scanner
        if self._data_provider:
            context = ScanContext.create(
                None, policy_config, data_provider=self._data_provider,
            )
            results = await scanner.scan_batch(
                tickers, context,
                max_concurrent=policy_config.watchlist.max_concurrent_requests,
            )
        else:
            async with PolygonClient(
                max_concurrent=policy_config.watchlist.max_concurrent_requests
            ) as polygon:
                context = ScanContext.create(polygon, policy_config)
                results = await scanner.scan_batch(
                    tickers, context,
                    max_concurrent=policy_config.watchlist.max_concurrent_requests,
                )

        return results


# Convenience functions for running scans
async def run_opportunity_scan(
    tickers: Optional[list[str]] = None,
    run_full_pipeline: bool = True,
) -> ScanRunResult:
    """Run a complete opportunity scan and pipeline.

    Convenience function that creates an orchestrator and runs a scan.

    Args:
        tickers: Optional list of tickers (uses watchlist if not provided)
        run_full_pipeline: If True, run all stages (filters, selection)

    Returns:
        ScanRunResult with all opportunities, evaluations, and statistics
    """
    orchestrator = ScannerOrchestrator()
    return await orchestrator.run_scan(tickers=tickers, run_full_pipeline=run_full_pipeline)


async def run_scanner_only(
    tickers: Optional[list[str]] = None,
) -> ScanRunResult:
    """Run only the scanner stage (Stage 1) without filters or selection.

    Useful for testing scanners or when you want to inspect opportunities
    before running the full pipeline.

    Args:
        tickers: Optional list of tickers (uses watchlist if not provided)

    Returns:
        ScanRunResult with opportunities only (no evaluations)
    """
    orchestrator = ScannerOrchestrator()
    return await orchestrator.run_scan(tickers=tickers, run_full_pipeline=False)
