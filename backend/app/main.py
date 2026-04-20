"""FastAPI application entry point.

Supports three Lambda invocation modes:
1. API Gateway: HTTP requests via Mangum
2. Scheduled Scan (Coordinator): EventBridge triggers, splits tickers into chunks
3. Worker Scan: Processes a chunk of tickers (invoked by coordinator)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.api.routes import alerts as alerts_routes
from app.api.routes import backtest as backtest_routes
from app.api.routes import calibration as calibration_routes
from app.api.routes import evaluations as evaluations_routes
from app.api.routes import health as health_routes
from app.api.routes import llm as llm_routes
from app.api.routes import market as market_routes
from app.api.routes import observability as observability_routes
from app.api.routes import paper_trading as paper_trading_routes
from app.api.routes import pipeline as pipeline_routes
from app.api.routes import policies as policies_routes
from app.api.routes import real_trades as real_trades_routes
from app.api.routes import scanners as scanners_routes
from app.config import get_settings

# Configure logging (force=True required in Lambda where runtime pre-configures handlers)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    settings = get_settings()
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    yield
    logger.info("Shutting down application")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Option Scanner System - Deterministic options trade evaluation",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health_routes.router, tags=["Health"])
    app.include_router(policies_routes.router, prefix="/api/policies", tags=["Policies"])
    app.include_router(pipeline_routes.router, prefix="/api/pipeline", tags=["Pipeline"])
    app.include_router(evaluations_routes.router, prefix="/api/evaluations", tags=["Evaluations"])
    app.include_router(scanners_routes.router, prefix="/api/scanners", tags=["Scanners"])
    app.include_router(paper_trading_routes.router, prefix="/api/paper-trading", tags=["Paper Trading"])
    app.include_router(calibration_routes.router, prefix="/api/calibration", tags=["Calibration"])
    app.include_router(llm_routes.router, prefix="/api/llm", tags=["LLM"])
    app.include_router(llm_routes.thesis_router, prefix="/api/thesis", tags=["Thesis"])
    app.include_router(llm_routes.stock_summary_router, prefix="/api/stock-summary", tags=["Stock Summary"])
    app.include_router(observability_routes.router, prefix="/api/observability", tags=["Observability"])
    app.include_router(market_routes.router, prefix="/api/market", tags=["Market"])
    app.include_router(alerts_routes.router, prefix="/api/alerts", tags=["Alerts"])
    app.include_router(backtest_routes.router, prefix="/api/backtest", tags=["Backtest"])
    app.include_router(real_trades_routes.router, prefix="/api/trades", tags=["Real Trades"])

    return app


app = create_app()

# Mangum handler for API Gateway requests
_mangum_handler = Mangum(app, lifespan="off")


# Configuration for chunked processing
CHUNK_SIZE = int(os.environ.get("SCANNER_CHUNK_SIZE", "100"))
USE_CHUNKED_PROCESSING = os.environ.get("USE_CHUNKED_PROCESSING", "true").lower() == "true"


def _chunk_list(items: list, chunk_size: int) -> list[list]:
    """Split a list into chunks of specified size."""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


async def _run_coordinator_scan() -> dict[str, Any]:
    """Coordinator: Split tickers into chunks and invoke worker Lambdas asynchronously.

    Uses fire-and-forget (Event) invocations so the coordinator completes in
    seconds instead of holding Lambda concurrency slots for 10+ minutes.
    Workers self-report completion via atomic counter; the last worker to
    finish marks the pipeline run as completed.

    Returns:
        Summary of dispatched work (does not wait for workers)
    """
    import boto3

    from app.core.pipeline import PipelineOrchestrator
    from app.core.watchlist import WatchlistManager
    from app.db.tables import PipelineRunTable, PolicyTable

    logger.info("Starting COORDINATOR scan - will distribute to workers")

    try:
        # Load policy and get tickers
        policy = await PolicyTable.get_active()
        if policy is None:
            raise ValueError("No active policy found")

        watchlist = await WatchlistManager.from_policy_async(policy.config)
        tickers = watchlist.tickers

        logger.info(f"Coordinator: {len(tickers)} total tickers, chunk_size={CHUNK_SIZE}")

        if len(tickers) <= CHUNK_SIZE:
            # Small enough to process directly — let orchestrator create its own run
            logger.info("Ticker count below threshold, processing directly")
            return await _run_worker_scan(tickers)

        # Multiple chunks: create a shared PipelineRun for all workers
        pipeline = PipelineOrchestrator()
        pipeline_run = await pipeline.start_run(policy.version)
        coordinator_run_id = pipeline_run.run_id

        # Split into chunks
        chunks = _chunk_list(tickers, CHUNK_SIZE)
        total_chunks = len(chunks)
        logger.info(f"Coordinator created run {coordinator_run_id}, {total_chunks} chunks")

        # Store total_chunks on the pipeline run so workers know the target
        await PipelineRunTable.update(
            coordinator_run_id,
            pipeline_run.started_at,
            {"total_chunks": total_chunks},
        )

        # Get this Lambda's function name to invoke workers
        function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
        lambda_client = boto3.client("lambda")

        # Fire-and-forget: invoke workers asynchronously
        dispatched = 0
        errors: list[str] = []

        for idx, chunk in enumerate(chunks):
            if idx > 0:
                time.sleep(3)  # Stagger workers to spread Phase 3 API load

            payload = {
                "source": "oss.scheduler",
                "action": "worker_scan",
                "tickers": chunk,
                "chunk_index": idx,
                "run_id": coordinator_run_id,
                "total_chunks": total_chunks,
                "started_at": pipeline_run.started_at,
            }

            try:
                lambda_client.invoke(
                    FunctionName=function_name,
                    InvocationType="Event",
                    Payload=json.dumps(payload),
                )
                dispatched += 1
                logger.info(f"Dispatched worker chunk {idx} ({len(chunk)} tickers)")
            except Exception as e:
                error_msg = f"Failed to dispatch chunk {idx}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        logger.info(
            f"Coordinator done: dispatched {dispatched}/{total_chunks} workers "
            f"for run {coordinator_run_id}"
        )

        return {
            "status": "success" if not errors else "partial_success",
            "mode": "coordinator",
            "run_id": coordinator_run_id,
            "chunks_dispatched": dispatched,
            "chunks_failed_to_dispatch": len(errors),
            "total_tickers": len(tickers),
            "errors": errors if errors else None,
        }

    except Exception as e:
        logger.error(f"Coordinator scan failed: {e}")
        return {
            "status": "error",
            "mode": "coordinator",
            "error": str(e),
        }


async def _run_worker_scan(
    tickers: list[str] | None = None,
    run_id: str | None = None,
    total_chunks: int = 0,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Worker: Process a specific chunk of tickers.

    Args:
        tickers: List of tickers to scan (uses watchlist if None)
        run_id: Optional coordinator-provided run_id. If None, orchestrator
                creates its own PipelineRun (used for single-worker mode).
        total_chunks: Total number of chunks dispatched by coordinator.
        started_at: Pipeline run started_at timestamp (for DB lookups).

    Returns:
        Scan result summary for this chunk
    """
    from app.core.pipeline import PipelineOrchestrator
    from app.db.tables import PipelineRunTable
    from app.scanners.orchestrator import ScannerOrchestrator

    ticker_count = len(tickers) if tickers else "all"
    logger.info(f"Starting WORKER scan for {ticker_count} tickers, run_id={run_id or 'auto'}")

    try:
        orchestrator = ScannerOrchestrator()
        result = await orchestrator.run_scan(
            tickers=tickers,
            run_id=run_id,  # None → orchestrator creates PipelineRun; set → reuse coordinator's
        )

        logger.info(
            f"Worker scan complete: {result.tickers_scanned} tickers, "
            f"{result.opportunities_created} opportunities"
        )

        # Run UV bridge to process PENDING UV evaluations through Stages 4-7
        uv_result: dict[str, Any] = {}
        try:
            uv_result = await _run_uv_bridge(run_id=result.run_id)
        except Exception as e:
            logger.error(f"UV Bridge error (non-fatal): {e}")
            uv_result = {"status": "error", "error": str(e)}

        # Track whether this worker is the final one in the run. In direct
        # (single-worker) mode, total_chunks==0 and we're always the last one.
        is_final_worker = (total_chunks == 0)

        # Self-report completion if this is a coordinator-dispatched worker
        if run_id and total_chunks > 0 and started_at:
            try:
                completed = await PipelineRunTable.increment_chunks_completed(
                    run_id, started_at
                )
                logger.info(
                    f"Worker chunk done: {completed}/{total_chunks} chunks completed "
                    f"for run {run_id}"
                )
                if completed >= total_chunks:
                    # Last worker — finalize the pipeline run
                    is_final_worker = True
                    logger.info(f"Last worker finished, completing run {run_id}")
                    pipeline = PipelineOrchestrator()
                    await pipeline.complete_run(run_id, status="completed")
            except Exception as e:
                logger.error(f"Worker self-report failed (non-fatal): {e}")

        # After the last worker of the run finishes, refresh displayed quote
        # prices on every open APPROVE so the Opportunities page reflects
        # current market. Runs once per pipeline run; non-fatal on error.
        if is_final_worker:
            try:
                from app.services.quote_refresh import refresh_open_approve_quotes
                await refresh_open_approve_quotes()
            except Exception as e:
                logger.warning(f"APPROVE quote refresh failed (non-fatal): {e}")

        return {
            "status": "success",
            "mode": "worker",
            "run_id": result.run_id,
            "tickers_scanned": result.tickers_scanned,
            "opportunities_created": result.opportunities_created,
            "evaluations_created": result.evaluations_created,
            "duration_ms": result.duration_ms,
            "uv_bridge": uv_result,
        }
    except Exception as e:
        logger.error(f"Worker scan failed: {e}")
        return {
            "status": "error",
            "mode": "worker",
            "error": str(e),
        }


def _synthesize_uv_opportunity(evaluation: Any) -> Any:
    """Create a minimal Opportunity for a UV evaluation so Stages 4-7 can run.

    Args:
        evaluation: Evaluation Pydantic model from a UV handoff

    Returns:
        Opportunity model with scanner trigger metadata
    """
    from app.core.schemas import (
        DirectionHint,
        Opportunity,
        ScannerTrigger,
        ScannerType,
    )

    trigger = ScannerTrigger(
        scanner_type=ScannerType.UNUSUAL_VOLUME,
        reason_codes=evaluation.trigger_reasons or [],
        metrics=evaluation.scanner_metrics or {},
        triggered_at=evaluation.evaluated_at,
    )
    return Opportunity(
        opportunity_id=evaluation.opportunity_id,
        underlying_ticker=evaluation.underlying_ticker,
        timestamp_utc=evaluation.evaluated_at,
        scanner_triggers=[trigger],
        direction_hint=DirectionHint.NONE,
        priority_score=max(0, min(int(evaluation.rank_score), 100)),
        created_at=evaluation.evaluated_at,
    )


def _convert_decimals(obj: Any) -> Any:
    """Recursively convert Decimal values to float for Pydantic model construction."""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_decimals(item) for item in obj]
    return obj


async def _run_uv_bridge(run_id: str) -> dict[str, Any]:
    """Process PENDING UV evaluations through Stages 4-7.

    Picks up evaluations written by the UV handoff (GSI1PK=VERDICT#PENDING)
    and runs them through Feature Computation, Pillar Scoring, Hard Gates,
    Decision Logic, and Paper Trading using the same stage functions as the
    main pipeline.

    Args:
        run_id: Pipeline run ID for telemetry (reuses the main scan's run)

    Returns:
        Summary dict with counts of processed/skipped/errored evaluations
    """
    from app.core.pipeline import PipelineOrchestrator
    from app.core.schemas import Evaluation, Verdict
    from app.db.tables import EvaluationTable, PolicyTable
    from app.decision.stage import run_decision_logic
    from app.features.stage import run_feature_computation
    from app.gates.stage import run_hard_gates
    from app.paper_trading.stage import run_paper_trading
    from app.pillars.stage import run_pillar_scoring
    from app.services.polygon import PolygonClient

    logger.info("UV Bridge: Checking for PENDING evaluations")

    try:
        # 1. Query PENDING evaluations
        raw_items = await EvaluationTable.list_by_verdict("PENDING", limit=200)
        if not raw_items:
            logger.info("UV Bridge: No PENDING evaluations found")
            return {"status": "skipped", "reason": "no_pending_evaluations"}

        # 2. Filter to recent evaluations only (last 24 hours)
        cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=24)
        cutoff_iso = cutoff_dt.isoformat()

        fresh_items = []
        stale_count = 0
        for item in raw_items:
            evaluated_at = item.get("evaluated_at", "")
            if evaluated_at >= cutoff_iso:
                fresh_items.append(item)
            else:
                stale_count += 1

        if stale_count > 0:
            logger.info(
                f"UV Bridge: Skipped {stale_count} stale PENDING evals (>24h old)"
            )

        if not fresh_items:
            logger.info("UV Bridge: All PENDING evaluations are stale, nothing to process")
            return {
                "status": "skipped",
                "reason": "all_stale",
                "total_pending": len(raw_items),
                "stale_skipped": stale_count,
            }

        # 3. Reconstruct Evaluation models (handle Decimal → float from DynamoDB)
        evaluations: list[Evaluation] = []
        parse_errors = 0
        for item in fresh_items:
            try:
                converted = _convert_decimals(item)
                evaluations.append(Evaluation(**converted))
            except Exception as e:
                parse_errors += 1
                ticker = item.get("underlying_ticker", "?")
                logger.warning(f"UV Bridge: Failed to parse evaluation for {ticker}: {e}")

        if not evaluations:
            logger.warning(f"UV Bridge: All {len(fresh_items)} evaluations failed to parse")
            return {"status": "error", "reason": "all_parse_failures", "parse_errors": parse_errors}

        logger.info(f"UV Bridge: Processing {len(evaluations)} PENDING UV evaluations")

        # 4. Synthesize Opportunity objects (one per unique ticker)
        seen_tickers: set[str] = set()
        opportunities = []
        for evaluation in evaluations:
            if evaluation.underlying_ticker not in seen_tickers:
                seen_tickers.add(evaluation.underlying_ticker)
                opportunities.append(_synthesize_uv_opportunity(evaluation))

        # 5. Load policy config
        policy = await PolicyTable.get_active()
        if policy is None:
            logger.error("UV Bridge: No active policy found")
            return {"status": "error", "reason": "no_active_policy"}
        policy_config = policy.config

        pipeline = PipelineOrchestrator()

        # 6. Run Stages 4-7 within a PolygonClient context
        async with PolygonClient() as polygon:
            # Pillar v4 wiring — same services the main orchestrator uses
            from app.db.tables import SP500TickerTable
            from app.services.earnings_calendar import EarningsCalendarService
            from app.services.price_history import PriceHistoryService
            try:
                uv_sector_map = await SP500TickerTable.get_sector_map()
            except Exception:
                uv_sector_map = {}

            # Stage 4: Feature Computation
            feature_sets = await run_feature_computation(
                run_id=run_id,
                evaluations=evaluations,
                opportunities=opportunities,
                polygon_client=polygon,
                orchestrator=pipeline,
                config=policy_config.features,
                persist_features=True,
                earnings_calendar_service=EarningsCalendarService(),
                sector_map=uv_sector_map,
                price_history_service=PriceHistoryService(polygon_client=polygon),
            )
            logger.info(f"UV Bridge Stage 4: {len(feature_sets)} feature sets computed")

            if not feature_sets:
                logger.warning("UV Bridge: No feature sets produced, cannot continue")
                return {
                    "status": "partial",
                    "evaluations_attempted": len(evaluations),
                    "feature_sets": 0,
                }

            # Stage 5: Pillar Scoring
            pillar_results = await run_pillar_scoring(
                run_id=run_id,
                evaluations=evaluations,
                feature_sets=feature_sets,
                opportunities=opportunities,
                orchestrator=pipeline,
                config=policy_config.pillars,
                persist_scores=True,
            )
            logger.info(f"UV Bridge Stage 5: {len(pillar_results)} evaluations scored")

            # Stage 6: Hard Gates
            gate_evaluations = await run_hard_gates(
                run_id=run_id,
                evaluations=evaluations,
                feature_sets=feature_sets,
                opportunities=opportunities,
                orchestrator=pipeline,
                config=policy_config.gates,
                persist_results=True,
            )
            passed_gates = sum(1 for ge in gate_evaluations.values() if ge.all_passed)
            logger.info(f"UV Bridge Stage 6: {passed_gates}/{len(gate_evaluations)} passed gates")

            # Stage 7: Decision Logic
            decisions, theses = await run_decision_logic(
                run_id=run_id,
                evaluations=evaluations,
                pillar_results=pillar_results,
                gate_evaluations=gate_evaluations,
                orchestrator=pipeline,
                decision_config=policy_config.decision,
                pillar_weights=policy_config.pillars.weights,
                thesis_config=policy_config.thesis,
                persist_decisions=True,
                check_concentration=True,
                pillar_config=policy_config.pillars,
                generate_theses=False,
                archetypes_config=policy_config.archetypes,
                anti_archetypes_config=policy_config.anti_archetypes,
                feature_sets=feature_sets,
                opportunities=opportunities,
                # v5 dual-conviction: no-op when policy_config.v5_active=False.
                v5_policy=policy_config,
            )

            approve_count = sum(1 for d in decisions.values() if d.verdict == Verdict.APPROVE)
            watch_count = sum(1 for d in decisions.values() if d.verdict == Verdict.WATCH)
            reject_count = sum(1 for d in decisions.values() if d.verdict == Verdict.REJECT)
            logger.info(
                f"UV Bridge Stage 7: {approve_count} APPROVE, "
                f"{watch_count} WATCH, {reject_count} REJECT"
            )

            # Stage 8: Paper Trading
            paper_results = {}
            if decisions:
                paper_results = await run_paper_trading(
                    run_id=run_id,
                    evaluations=evaluations,
                    decisions=decisions,
                    gate_evaluations=gate_evaluations,
                    orchestrator=pipeline,
                    config=policy_config.tracking,
                    create_positions=True,
                    track_shadows=True,
                )
                logger.info(
                    f"UV Bridge Stage 8: {paper_results.get('positions_created', 0)} positions"
                )

        summary = {
            "status": "success",
            "evaluations_processed": len(evaluations),
            "stale_skipped": stale_count,
            "parse_errors": parse_errors,
            "feature_sets": len(feature_sets),
            "pillar_scored": len(pillar_results),
            "gates_passed": passed_gates,
            "approve": approve_count,
            "watch": watch_count,
            "reject": reject_count,
            "positions_created": paper_results.get("positions_created", 0),
            "theses_generated": len([t for t in theses if t.status == "COMPLETED"]),
        }
        # Clean up stale PENDINGs (>48h old) to prevent them accumulating
        try:
            stale_cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
            expired_count = await EvaluationTable.expire_stale_pending(stale_cutoff)
            if expired_count > 0:
                logger.info(f"UV Bridge: Expired {expired_count} stale PENDING evals (>48h)")
                summary["stale_expired"] = expired_count
        except Exception as cleanup_err:
            logger.warning(f"UV Bridge: Stale cleanup failed: {cleanup_err}")

        logger.info(f"UV Bridge complete: {summary}")
        return summary

    except Exception as e:
        logger.error(f"UV Bridge failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def _run_backtest_coordinator(event: dict[str, Any]) -> dict[str, Any]:
    """Backtest coordinator: create run, fan out Phase 1 evaluate workers.

    Uses three-phase architecture:
      Phase 1 (Evaluate): 1 worker per trading day, all tickers
      Phase 2 (Resolve):  N workers, each handles ~50 tickers
      Phase 3 (Finalize): Single Lambda, computes metrics + AI export

    Args:
        event: Must contain "config" dict with BacktestRunConfig fields.

    Returns:
        Summary with run_id and worker count.
    """
    from app.backtest.phase_coordinator import coordinate_phase1
    from app.core.schemas import BacktestRunConfig

    logger.info("Backtest coordinator (phase-aware) starting")

    try:
        config_data = event.get("config", {})
        config = BacktestRunConfig(**config_data)
        existing_run_id = event.get("run_id")

        result = await coordinate_phase1(config, run_id=existing_run_id)
        return result

    except Exception as e:
        logger.error(f"Backtest coordinator failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def _run_backtest_evaluate(event: dict[str, Any]) -> dict[str, Any]:
    """Phase 1 worker: evaluate a single trading day, write pending trades.

    After processing, atomically increments Phase 1 counter. If this is the
    last Phase 1 worker, triggers Phase 2 (resolve exits).

    Args:
        event: Must contain run_id, date, config, s3_bucket, phase1_total.
    """
    from app.backtest.evaluate_worker import run_phase1_worker
    from app.core.schemas import BacktestRunConfig

    run_id = event.get("run_id", "")
    date_str = event.get("date", "")
    config_data = event.get("config", {})
    s3_bucket = event.get("s3_bucket", os.environ.get("BACKTEST_S3_BUCKET", ""))
    phase1_total = event.get("phase1_total", 1)

    logger.info(f"Phase 1 evaluate worker: run={run_id}, date={date_str}")

    try:
        from datetime import date as date_type
        config = BacktestRunConfig(**config_data)
        as_of_date = date_type.fromisoformat(date_str)

        result = await run_phase1_worker(
            run_id=run_id,
            as_of_date=as_of_date,
            config=config,
            s3_bucket=s3_bucket,
            phase1_total=phase1_total,
        )

        # If this was the last Phase 1 worker, trigger Phase 2
        if result.get("trigger_phase2"):
            logger.info(f"Phase 1 complete for run {run_id}, triggering Phase 2")
            from app.backtest.phase_coordinator import coordinate_phase2
            await coordinate_phase2(run_id, config)

        return result

    except Exception as e:
        logger.error(f"Phase 1 evaluate worker failed: {e}", exc_info=True)
        return {"status": "error", "run_id": run_id, "date": date_str, "error": str(e)}


async def _run_backtest_evaluate_window(event: dict[str, Any]) -> dict[str, Any]:
    """Phase 1 window worker: evaluate multiple consecutive days in one invocation.

    Part of the rolling-dispatch architecture. Processes a window of days,
    then dispatches its own successor window. This maintains controlled
    concurrency (MAX_CONCURRENT_CHAINS workers at a time).

    After processing, atomically increments the Phase 1 counter. If this is
    the last window, triggers Phase 2 (resolve exits).

    Args:
        event: Must contain run_id, window_index, window_days, config,
               s3_bucket, total_windows, total_days.
    """
    from app.backtest.evaluate_worker import run_phase1_window
    from app.core.schemas import BacktestRunConfig

    run_id = event.get("run_id", "")
    window_index = event.get("window_index", 0)
    window_days_str = event.get("window_days", [])
    config_data = event.get("config", {})
    s3_bucket = event.get("s3_bucket", os.environ.get("BACKTEST_S3_BUCKET", ""))
    total_windows = event.get("total_windows", 1)
    total_days = event.get("total_days", 0)

    logger.info(
        f"Phase 1 window worker: run={run_id}, window={window_index}, "
        f"days={len(window_days_str)}"
    )

    try:
        from datetime import date as date_type
        config = BacktestRunConfig(**config_data)
        window_days = [date_type.fromisoformat(d) for d in window_days_str]

        result = await run_phase1_window(
            run_id=run_id,
            window_index=window_index,
            window_days=window_days,
            config=config,
            s3_bucket=s3_bucket,
            total_windows=total_windows,
            total_days=total_days,
        )

        # If this was the last window, trigger Phase 2
        if result.get("trigger_phase2"):
            logger.info(f"Phase 1 complete for run {run_id}, triggering Phase 2")
            from app.backtest.phase_coordinator import coordinate_phase2
            await coordinate_phase2(run_id, config)

        return result

    except Exception as e:
        logger.error(
            f"Phase 1 window worker failed: {e}", exc_info=True
        )
        return {
            "status": "error",
            "run_id": run_id,
            "window_index": window_index,
            "error": str(e),
        }


async def _run_backtest_resolve(event: dict[str, Any]) -> dict[str, Any]:
    """Phase 2 worker: resolve exits for a ticker partition.

    After processing, atomically increments Phase 2 counter. If this is the
    last Phase 2 worker, triggers Phase 3 (finalize).

    Args:
        event: Must contain run_id, tickers, config, s3_bucket, end_date, phase2_total.
    """
    from app.backtest.resolve_worker import run_phase2_worker
    from app.core.schemas import BacktestRunConfig

    run_id = event.get("run_id", "")
    tickers = event.get("tickers", [])
    config_data = event.get("config", {})
    s3_bucket = event.get("s3_bucket", os.environ.get("BACKTEST_S3_BUCKET", ""))
    end_date = event.get("end_date", "")
    phase2_total = event.get("phase2_total", 1)

    logger.info(
        f"Phase 2 resolve worker: run={run_id}, "
        f"{len(tickers)} tickers, end_date={end_date}"
    )

    try:
        config = BacktestRunConfig(**config_data)

        result = await run_phase2_worker(
            run_id=run_id,
            tickers=tickers,
            s3_bucket=s3_bucket,
            end_date=end_date,
            phase2_total=phase2_total,
        )

        # If this was the last Phase 2 worker, trigger Phase 3
        if result.get("trigger_phase3"):
            logger.info(f"Phase 2 complete for run {run_id}, triggering Phase 3")
            from app.backtest.phase_coordinator import coordinate_phase3
            await coordinate_phase3(run_id, config)

        return result

    except Exception as e:
        logger.error(f"Phase 2 resolve worker failed: {e}", exc_info=True)
        return {"status": "error", "run_id": run_id, "error": str(e)}


async def _run_backtest_finalize(event: dict[str, Any]) -> dict[str, Any]:
    """Phase 3 worker: compute metrics, export to S3, mark run COMPLETED.

    Single Lambda invocation — no coordination needed.

    Args:
        event: Must contain run_id, config, s3_bucket.
    """
    from app.backtest.finalize_worker import run_phase3_finalize
    from app.core.schemas import BacktestRunConfig

    run_id = event.get("run_id", "")
    config_data = event.get("config", {})
    s3_bucket = event.get("s3_bucket", os.environ.get("BACKTEST_S3_BUCKET", ""))

    logger.info(f"Phase 3 finalize worker: run={run_id}")

    try:
        config = BacktestRunConfig(**config_data)
        result = await run_phase3_finalize(
            run_id=run_id,
            config=config,
            s3_bucket=s3_bucket,
        )
        return result

    except Exception as e:
        logger.error(f"Phase 3 finalize worker failed: {e}", exc_info=True)
        return {"status": "error", "run_id": run_id, "error": str(e)}


async def _run_scheduled_scan() -> dict[str, Any]:
    """Run a scheduled scan - routes to coordinator or direct worker.

    Returns:
        Scan result summary
    """
    if USE_CHUNKED_PROCESSING:
        return await _run_coordinator_scan()
    else:
        return await _run_worker_scan()


async def _run_paper_update() -> dict[str, Any]:
    """Coordinator: fan out paper trading position updates to worker chunks.

    If the number of open positions exceeds CHUNK_SIZE, dispatches workers
    via async Lambda invocations. Otherwise processes directly.
    """
    import boto3

    from app.db.tables import PaperPositionTable

    logger.info("Paper update coordinator starting")

    try:
        # Collect all open position IDs
        open_positions = await PaperPositionTable.list_open()
        if not open_positions:
            logger.info("No open positions to update")
            return {"status": "success", "positions_updated": 0}

        position_ids = [p.position_id for p in open_positions]
        total = len(position_ids)
        logger.info(f"Paper update: {total} open positions")

        chunk_size = 50  # Positions per worker
        if total <= chunk_size:
            # Small enough to process directly
            return await _run_paper_update_worker(position_ids)

        # Fan out to workers
        chunks = _chunk_list(position_ids, chunk_size)
        function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
        lambda_client = boto3.client("lambda")

        dispatched = 0
        errors: list[str] = []

        for idx, chunk in enumerate(chunks):
            if idx > 0:
                time.sleep(3)  # Stagger workers

            payload = {
                "source": "oss.scheduler",
                "action": "paper_update_worker",
                "position_ids": chunk,
                "chunk_index": idx,
            }

            try:
                lambda_client.invoke(
                    FunctionName=function_name,
                    InvocationType="Event",
                    Payload=json.dumps(payload),
                )
                dispatched += 1
            except Exception as e:
                errors.append(f"Failed to dispatch chunk {idx}: {e}")

        return {
            "status": "success",
            "mode": "coordinator",
            "total_positions": total,
            "chunks_dispatched": dispatched,
            "errors": errors if errors else None,
        }

    except Exception as e:
        logger.error(f"Paper update coordinator failed: {e}")
        return {"status": "error", "error": str(e)}


async def _run_paper_update_worker(
    position_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Worker: update a chunk of positions with current prices."""
    from app.paper_trading.batch_updater import update_position_chunk
    from app.services.polygon import PolygonClient

    if not position_ids:
        # Fallback: update all open positions (legacy mode)
        from app.paper_trading.position_manager import update_open_positions
        async with PolygonClient() as polygon:
            results = await update_open_positions(polygon)
        exits = sum(1 for r in results if r.exit_triggered)
        errors = sum(1 for r in results if r.error)
        return {
            "status": "success",
            "positions_updated": len(results),
            "exits_triggered": exits,
            "errors": errors,
        }

    logger.info(f"Paper update worker processing {len(position_ids)} positions")

    async with PolygonClient() as polygon:
        result = await update_position_chunk(position_ids, polygon)

    return {
        "status": "success",
        "mode": "worker",
        "positions_processed": result.positions_processed,
        "exits_triggered": result.exits_triggered,
        "errors": result.errors,
    }


async def _run_earnings_refresh() -> dict[str, Any]:
    """Run daily bulk earnings refresh from Finnhub.

    Makes one API call to get all upcoming earnings in the next 10 days
    and caches them in DynamoDB. Pipeline runs then read from cache only.
    """
    from app.services.earnings_cache import EarningsCacheService
    from app.services.finnhub import FinnhubClient

    settings = get_settings()

    if not settings.finnhub_api_key:
        logger.warning("Finnhub API key not configured, skipping earnings refresh")
        return {"status": "skipped", "reason": "no_finnhub_api_key"}

    async with FinnhubClient(api_key=settings.finnhub_api_key) as finnhub:
        earnings_cache = EarningsCacheService(finnhub_client=finnhub)
        result = await earnings_cache.refresh_all(lookforward_days=10)

    logger.info(f"Earnings refresh result: {result}")
    return {"status": "success", **result}


async def _resolve_backfill_universe() -> list[str]:
    """Return the combined S&P 500 + Russell 1000 active-ticker list.

    Used by the Pillar v4 daily refresh hooks so they stay aligned with
    the same universe the pipeline scans.
    """
    from app.db.tables import SP500TickerTable

    tickers: set[str] = set()
    for universe in ("sp500", "russell1000"):
        try:
            tickers.update(
                await SP500TickerTable.get_tickers_by_universe(universe)
            )
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(f"Failed to load {universe} tickers: {e}")
    return sorted(tickers)


async def _run_price_history_refresh() -> dict[str, Any]:
    """Append yesterday's bar to ``oss-dev-price-history`` for every ticker.

    Uses Polygon's grouped-daily endpoint (one API call for all US
    equities) and a SPDR sector ETF list to keep the Pillar v4 cache
    warm. Triggered by EventBridge Tue-Sat at ~5am UTC so Friday's
    close lands before the next scan.
    """
    from app.features.relative_strength import SECTOR_ETF_MAP
    from app.services.polygon import PolygonClient
    from app.services.price_history import PriceHistoryService

    tickers = await _resolve_backfill_universe()
    if not tickers:
        logger.warning("No tickers resolved for price-history refresh")
        return {"status": "skipped", "reason": "empty_universe"}

    # Sector ETFs must be refreshed alongside underlyings so sector_rs_20d
    # stays current.
    sector_etfs = sorted(set(SECTOR_ETF_MAP.values()) | {"SPY"})
    all_tickers = sorted(set(tickers) | set(sector_etfs))

    async with PolygonClient() as polygon:
        service = PriceHistoryService(polygon_client=polygon)
        report = await service.refresh_daily(all_tickers)

    return {
        "status": "success",
        "tickers_attempted": report.tickers_attempted,
        "tickers_succeeded": report.tickers_succeeded,
        "tickers_failed": report.tickers_failed,
        "bars_written": report.bars_written,
    }


async def _run_earnings_history_refresh() -> dict[str, Any]:
    """Recompute 1-day post-event moves for earnings that just concluded.

    Finds events dated within the last 2 days whose ``one_day_move_pct``
    is unset (i.e., couldn't be computed at backfill time because price
    history hadn't accumulated) and fills them in from the now-fresh
    price cache. Triggered by EventBridge Tue-Sat at ~6am UTC after the
    price-history refresh.
    """
    from app.services.earnings_calendar import EarningsCalendarService
    from app.services.price_history import PriceHistoryService

    tickers = await _resolve_backfill_universe()
    if not tickers:
        return {"status": "skipped", "reason": "empty_universe"}

    price_svc = PriceHistoryService()  # read-only
    earnings_svc = EarningsCalendarService(price_history_service=price_svc)
    report = await earnings_svc.refresh_completed_events(tickers)

    return {
        "status": "success",
        "tickers_attempted": report.tickers_attempted,
        "tickers_succeeded": report.tickers_succeeded,
        "tickers_failed": report.tickers_failed,
        "events_written": report.events_written,
        "moves_computed": report.moves_computed,
    }


async def _run_daily_data_capture(
    capture_date: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run daily market data capture for backtesting.

    Captures stock OHLCV, options chains, IV history, and market context
    for a single trading day.
    """
    from app.data_capture.daily_capture import run_daily_capture

    try:
        result = await run_daily_capture(capture_date=capture_date, force=force)
        logger.info(f"Daily data capture result: {result}")
        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"Daily data capture failed: {e}")
        return {"status": "error", "error": str(e)}


async def _run_pattern_discovery_worker(event: dict[str, Any]) -> dict[str, Any]:
    """Run pattern discovery analysis asynchronously (no API Gateway timeout).

    Invoked by fire-and-forget dispatch from the POST endpoint.
    Updates the pre-created analysis stub with results or error.
    """
    from app.paper_trading.pattern_discovery import run_pattern_analysis

    analysis_id = event.get("analysis_id", "")
    try:
        result = await run_pattern_analysis(
            analysis_id=analysis_id,
            period=event.get("period"),
            verdict=event.get("verdict"),
            scanner=event.get("scanner"),
            min_sample=event.get("min_sample", 5),
            min_win_rate=event.get("min_win_rate", 0.55),
        )
        return {"status": "success", "analysis_id": analysis_id, "result": result.get("status")}
    except Exception as e:
        logger.error(f"Pattern discovery worker failed: {e}")
        return {"status": "error", "analysis_id": analysis_id, "error": str(e)}


async def _run_custom_analysis_worker(event: dict[str, Any]) -> dict[str, Any]:
    """Run custom analysis asynchronously (no API Gateway timeout).

    Invoked by fire-and-forget dispatch from the POST endpoint.
    Updates the pre-created analysis stub with results or error.
    """
    from app.paper_trading.custom_analysis import run_custom_analysis

    analysis_id = event.get("analysis_id", "")
    try:
        result = await run_custom_analysis(
            analysis_id=analysis_id,
            prompt=event.get("prompt", ""),
            period=event.get("period"),
            verdict=event.get("verdict"),
            scanner=event.get("scanner"),
            min_return=event.get("min_return"),
        )
        return {"status": "success", "analysis_id": analysis_id, "result": result.get("status")}
    except Exception as e:
        logger.error(f"Custom analysis worker failed: {e}")
        return {"status": "error", "analysis_id": analysis_id, "error": str(e)}


async def _run_thesis_worker(event: dict[str, Any]) -> dict[str, Any]:
    """Generate a trade thesis asynchronously (invoked by fire-and-forget dispatch).

    The /api/thesis/generate endpoint writes a GENERATING stub and invokes this
    worker asynchronously. The worker does the actual LLM call with no API Gateway
    timeout pressure, then overwrites the stub with the COMPLETED/FAILED result.
    """
    from app.core.schemas import (
        Decision,
        Evaluation,
        ExitPlanThesis,
        LLMProvider as LLMProviderEnum,
        ThesisStatus,
        TradeThesis,
    )
    from app.db.tables import (
        EvaluationTable,
        FeatureValueTable,
        OpportunityTable,
        PaperPositionTable,
        PillarScoreTable,
        TradeThesisTable,
    )
    from app.llm.generator import ThesisGenerator

    evaluation_id = event.get("evaluation_id", "")
    thesis_id = event.get("thesis_id", "")
    ticker = event.get("ticker", "")

    try:
        # Fetch evaluation
        eval_dict = await EvaluationTable.get_by_id(ticker, evaluation_id)
        if not eval_dict:
            raise ValueError(f"Evaluation not found: {evaluation_id}")

        decision_data = eval_dict.pop("decision", None)
        if not decision_data:
            raise ValueError("Evaluation has no decision data")

        evaluation = Evaluation(**eval_dict)
        decision = Decision(**decision_data)

        # Fetch prerequisites in parallel
        pillar_scores, features, opportunities = await asyncio.gather(
            PillarScoreTable.list_by_evaluation(evaluation_id),
            FeatureValueTable.list_by_evaluation(evaluation_id),
            OpportunityTable.list_by_ticker(ticker, limit=50),
        )

        # Extract scanner triggers from the matching opportunity
        scanner_triggers: list[str] = []
        for opp in opportunities:
            if opp.opportunity_id == evaluation.opportunity_id:
                scanner_triggers = list(opp.scanner_triggers)
                break

        features_dict = {f.feature_name: f.value for f in features}

        # Match setup rules against this evaluation
        matched_rules: list[dict[str, Any]] = []
        total_active_rules = 0
        try:
            from app.paper_trading.pattern_discovery import list_setup_rules
            from app.paper_trading.rule_matcher import format_matched_rules, match_rules

            all_rules = await list_setup_rules()
            total_active_rules = len(
                [r for r in all_rules if r.get("is_active", True)]
            )
            if all_rules:
                eval_match_dict = evaluation.model_dump()
                eval_match_dict["option_type"] = str(
                    evaluation.option_type.value
                    if hasattr(evaluation.option_type, "value")
                    else evaluation.option_type
                )
                eval_match_dict.update(features_dict)
                # Enrich with sector for sector-aware rule matching
                try:
                    from app.db.tables import SP500TickerTable
                    sector_map = await SP500TickerTable.get_sector_map()
                    ticker = evaluation.underlying_ticker or ""
                    if ticker in sector_map:
                        eval_match_dict["sector"] = sector_map[ticker]
                except Exception:
                    pass
                decision_dict = {
                    "final_score": decision.final_score,
                    **decision.pillar_score_dict(),
                }
                scanner_names = [
                    str(
                        st.scanner_type.value
                        if hasattr(st.scanner_type, "value")
                        else st.scanner_type
                    )
                    for st in scanner_triggers
                ]
                # Fallback: use evaluation's scanner_source if opportunity wasn't found
                if not scanner_names:
                    eval_scanner_source = getattr(evaluation, "scanner_source", None) or ""
                    if eval_scanner_source:
                        scanner_names = [eval_scanner_source]
                matched = match_rules(
                    all_rules, eval_match_dict, decision_dict, scanner_names
                )
                matched_rules = format_matched_rules(matched, include_criteria=True)
        except Exception as e:
            logger.warning(f"Setup rule matching failed for thesis worker: {e}")

        # Generate thesis (the LLM call — no timeout pressure here)
        generator = ThesisGenerator()
        thesis = await generator.generate(
            evaluation=evaluation,
            decision=decision,
            pillar_scores=pillar_scores,
            scanner_triggers=scanner_triggers,
            features=features_dict,
            matched_rules=matched_rules,
            total_active_rules=total_active_rules,
        )

        # Overwrite the GENERATING stub with the same thesis_id (same PK+SK)
        thesis = thesis.model_copy(update={"thesis_id": thesis_id})
        await TradeThesisTable.put(thesis)

        thesis_status = str(thesis.status.value) if hasattr(thesis.status, "value") else str(
            thesis.status
        )
        logger.info(f"Thesis worker completed: {evaluation_id} status={thesis_status}")

        # Apply exit levels to paper position
        if thesis_status == "COMPLETED" and thesis.exit_plan.take_profits:
            try:
                position = await PaperPositionTable.get_by_evaluation_id(evaluation_id)
                if position:
                    pos_status = str(getattr(position.status, "value", position.status))
                    if pos_status == "OPEN":
                        updates: dict[str, Any] = {}
                        tp1 = thesis.exit_plan.take_profits[0]
                        updates["thesis_tp1_pct"] = tp1.option_pnl_pct
                        if thesis.exit_plan.stop_loss_level:
                            updates["thesis_sl_pct"] = abs(
                                thesis.exit_plan.stop_loss_level.option_pnl_pct
                            )
                        if thesis.exit_plan.time_exit_level:
                            updates["thesis_time_exit_dte"] = (
                                thesis.exit_plan.time_exit_level.dte_threshold
                            )
                        await PaperPositionTable.update(position, updates)
                        logger.info(
                            f"Applied thesis exit levels to position {position.position_id}"
                        )
            except Exception as e:
                logger.warning(f"Failed to apply thesis exit levels: {e}")

        return {"status": "success", "evaluation_id": evaluation_id, "thesis_status": thesis_status}

    except Exception as e:
        logger.exception(f"Thesis worker failed for {evaluation_id}: {e}")
        # Mark thesis as FAILED so the frontend can show a retry button
        try:
            failed_thesis = TradeThesis(
                thesis_id=thesis_id,
                evaluation_id=evaluation_id,
                setup_summary="",
                thesis="",
                supporting_evidence=[],
                risks=[],
                invalidation_conditions=[],
                exit_plan=ExitPlanThesis(),
                llm_provider=LLMProviderEnum.ANTHROPIC,
                model_used="",
                tokens_used=0,
                status=ThesisStatus.FAILED,
                error_message=str(e),
            )
            await TradeThesisTable.put(failed_thesis)
        except Exception as put_err:
            logger.error(f"Failed to persist FAILED thesis: {put_err}")
        return {"status": "error", "evaluation_id": evaluation_id, "error": str(e)}


async def _run_stock_summary_worker(event: dict[str, Any]) -> dict[str, Any]:
    """Generate a stock summary asynchronously (invoked by fire-and-forget dispatch).

    Fetches company data, news, and SEC filings, then calls the LLM to
    generate a fundamental context summary for the underlying stock.
    """
    from app.core.schemas import StockSummary, StockSummaryStatus
    from app.db.tables import EvaluationTable, StockSummaryTable
    from app.llm.stock_summary import StockSummaryInput
    from app.llm.stock_summary_generator import (
        StockSummaryGenerator,
        fetch_news_context,
    )
    from app.services.catalyst import CatalystDataService
    from app.services.polygon import PolygonClient

    ticker = event.get("ticker", "")
    summary_id = event.get("summary_id", "")
    evaluation_id = event.get("evaluation_id", "")

    try:
        # Fetch evaluation context for option details
        option_type = ""
        strike = None
        expiration = ""
        dte = None
        mid = None
        verdict = ""
        final_score = None
        price = None

        if evaluation_id:
            eval_dict = await EvaluationTable.get_by_id(ticker, evaluation_id)
            if eval_dict:
                option_type = str(eval_dict.get("option_type", ""))
                strike = eval_dict.get("strike")
                expiration = str(eval_dict.get("expiration_date", ""))
                dte = eval_dict.get("dte")
                mid = eval_dict.get("mid")
                price = eval_dict.get("underlying_price")
                decision = eval_dict.get("decision", {})
                if isinstance(decision, dict):
                    verdict = decision.get("verdict", "")
                    final_score = decision.get("final_score")

        # Fetch company data and news in parallel
        async with PolygonClient() as polygon:
            # Gather company details and news
            ticker_details_task = polygon.get_ticker_details(ticker)
            news_task = fetch_news_context(ticker, polygon)
            ticker_details, news_items = await asyncio.gather(
                ticker_details_task, news_task
            )

        # Fetch SEC 8-K filings
        sec_filings: list[dict[str, str]] = []
        try:
            catalyst = CatalystDataService()
            sec_filings = await catalyst.get_recent_8k_filings(ticker, days=30)
        except Exception as e:
            logger.warning(f"Failed to fetch SEC filings for {ticker}: {e}")

        # Extract company info from ticker details
        company_name = ""
        company_description = ""
        sic_description = ""
        market_cap = None
        if ticker_details:
            company_name = ticker_details.get("name", "")
            company_description = ticker_details.get("description", "")
            sic_description = ticker_details.get("sic_description", "")
            market_cap = ticker_details.get("market_cap")
            if price is None:
                # Use previous close if we don't have eval price
                async with PolygonClient() as polygon:
                    prev = await polygon.get_previous_close(ticker)
                    if prev:
                        price = prev.get("c")

        # Build input
        input_data = StockSummaryInput(
            ticker=ticker,
            company_name=company_name,
            company_description=company_description,
            sic_description=sic_description,
            market_cap=market_cap,
            price=price,
            news_items=news_items,
            sec_filings=sec_filings,
            option_type=option_type,
            strike=strike,
            expiration=expiration,
            dte=dte,
            mid=mid,
            verdict=verdict,
            final_score=final_score,
        )

        # Generate summary
        generator = StockSummaryGenerator()
        summary = await generator.generate(input_data)

        # Overwrite the GENERATING stub with same summary_id
        summary = summary.model_copy(update={"summary_id": summary_id})
        await StockSummaryTable.put(summary)

        summary_status = str(
            summary.status.value
            if hasattr(summary.status, "value")
            else summary.status
        )
        logger.info(f"Stock summary worker completed: {ticker} status={summary_status}")
        return {"status": "success", "ticker": ticker, "summary_status": summary_status}

    except Exception as e:
        logger.exception(f"Stock summary worker failed for {ticker}: {e}")
        try:
            failed = StockSummary(
                summary_id=summary_id,
                ticker=ticker,
                status=StockSummaryStatus.FAILED,
                error_message=str(e),
            )
            await StockSummaryTable.put(failed)
        except Exception as put_err:
            logger.error(f"Failed to persist FAILED stock summary: {put_err}")
        return {"status": "error", "ticker": ticker, "error": str(e)}


def handler(event: dict[str, Any], context: Any) -> Any:
    """Lambda handler that routes between API requests and scheduled events.

    Supported event types:
    1. API Gateway request (via Mangum)
    2. Scheduled scan from EventBridge: {"source": "oss.scheduler", "action": "run_scan"}
    3. Worker scan (chunk): {"source": "oss.scheduler", "action": "worker_scan", ...}
    4. Paper trading update: {"source": "oss.scheduler", "action": "paper_update"}
    5. Backtest coordinator: {"source": "oss.scheduler", "action": "backtest_coordinator", ...}
    6. Backtest Phase 1 (legacy): {"source": "oss.scheduler", "action": "backtest_evaluate", ...}
    6b. Backtest Phase 1 window: {"source": "oss.scheduler", "action": "backtest_evaluate_window", ...}
    7. Backtest Phase 2: {"source": "oss.scheduler", "action": "backtest_resolve", ...}
    8. Backtest Phase 3: {"source": "oss.scheduler", "action": "backtest_finalize", ...}
    9. Earnings refresh: {"source": "oss.scheduler", "action": "earnings_refresh"}
    10. Daily data capture: {"source": "oss.scheduler", "action": "daily_data_capture"}
    11. Thesis worker: {"source": "oss.scheduler", "action": "thesis_worker", ...}
    12. Pattern discovery: {"source": "oss.scheduler", "action": "pattern_discovery_worker", ...}
    13. Stock summary worker: {"source": "oss.scheduler", "action": "stock_summary_worker", ...}

    Args:
        event: Lambda event (API Gateway, EventBridge, or worker invocation)
        context: Lambda context

    Returns:
        Response appropriate to the event type
    """
    # Check if this is an OSS scheduler event
    if event.get("source") == "oss.scheduler":
        action = event.get("action")

        if action == "run_scan":
            # Coordinator: triggered by EventBridge
            logger.info("Received scheduled scan event from EventBridge")
            return asyncio.run(_run_scheduled_scan())

        elif action == "worker_scan":
            # Worker: process specific chunk of tickers
            tickers = event.get("tickers")
            run_id = event.get("run_id")
            total_chunks = event.get("total_chunks", 0)
            started_at = event.get("started_at")
            chunk_idx = event.get("chunk_index", 0)
            logger.info(
                f"Received worker_scan event for chunk {chunk_idx} "
                f"({len(tickers) if tickers else 0} tickers)"
            )
            return asyncio.run(_run_worker_scan(
                tickers, run_id=run_id,
                total_chunks=total_chunks, started_at=started_at,
            ))

        elif action == "paper_update":
            # Paper trading daily update: coordinator dispatches worker chunks
            logger.info("Received paper_update event from EventBridge")
            return asyncio.run(_run_paper_update())

        elif action == "paper_update_worker":
            # Worker: process a chunk of position IDs
            position_ids = event.get("position_ids", [])
            chunk_idx = event.get("chunk_index", 0)
            logger.info(
                f"Paper update worker chunk {chunk_idx} "
                f"({len(position_ids)} positions)"
            )
            return asyncio.run(_run_paper_update_worker(position_ids))

        elif action == "paper_trading_update":
            # Alias for paper_update (used by new EventBridge rule)
            logger.info("Received paper_trading_update event from EventBridge")
            return asyncio.run(_run_paper_update())

        elif action == "backtest_coordinator":
            # Backtest coordinator: Phase 1 fan-out (1 worker per trading day)
            logger.info("Received backtest_coordinator event")
            return asyncio.run(_run_backtest_coordinator(event))

        elif action == "backtest_evaluate":
            # Phase 1 worker: evaluate a single trading day (legacy)
            date_str = event.get("date", "")
            logger.info(f"Received backtest_evaluate event (date={date_str})")
            return asyncio.run(_run_backtest_evaluate(event))

        elif action == "backtest_evaluate_window":
            # Phase 1 window worker: evaluate multiple days, chain to successor
            window_idx = event.get("window_index", 0)
            window_days = event.get("window_days", [])
            logger.info(
                f"Received backtest_evaluate_window event "
                f"(window={window_idx}, days={len(window_days)})"
            )
            return asyncio.run(_run_backtest_evaluate_window(event))

        elif action == "backtest_resolve":
            # Phase 2 worker: resolve exits for a ticker partition
            partition_idx = event.get("partition_index", 0)
            logger.info(f"Received backtest_resolve event (partition {partition_idx})")
            return asyncio.run(_run_backtest_resolve(event))

        elif action == "backtest_finalize":
            # Phase 3 worker: compute metrics, export, mark COMPLETED
            logger.info("Received backtest_finalize event")
            return asyncio.run(_run_backtest_finalize(event))

        elif action == "earnings_refresh":
            # Daily earnings cache refresh from Finnhub
            logger.info("Received earnings_refresh event from EventBridge")
            return asyncio.run(_run_earnings_refresh())

        elif action == "price_history_refresh":
            # Pillar v4: append yesterday's bar to oss-dev-price-history.
            logger.info("Received price_history_refresh event from EventBridge")
            return asyncio.run(_run_price_history_refresh())

        elif action == "earnings_history_refresh":
            # Pillar v4: recompute 1-day moves for recently-concluded events.
            logger.info("Received earnings_history_refresh event from EventBridge")
            return asyncio.run(_run_earnings_history_refresh())

        elif action == "daily_data_capture":
            # Daily market data capture for backtesting
            capture_date = event.get("capture_date")
            force = event.get("force", False)
            logger.info(
                f"Received daily_data_capture event "
                f"(date={capture_date or 'auto'}, force={force})"
            )
            return asyncio.run(_run_daily_data_capture(capture_date, force))

        elif action == "pattern_discovery_worker":
            # Async pattern discovery (dispatched by /api/paper-trading/pattern-discovery)
            analysis_id = event.get("analysis_id", "")
            logger.info(f"Received pattern_discovery_worker event (analysis={analysis_id})")
            return asyncio.run(_run_pattern_discovery_worker(event))

        elif action == "custom_analysis_worker":
            # Async custom analysis (dispatched by /api/paper-trading/custom-analysis)
            analysis_id = event.get("analysis_id", "")
            logger.info(f"Received custom_analysis_worker event (analysis={analysis_id})")
            return asyncio.run(_run_custom_analysis_worker(event))

        elif action == "thesis_worker":
            # Async thesis generation (dispatched by /api/thesis/generate)
            evaluation_id = event.get("evaluation_id", "")
            logger.info(f"Received thesis_worker event (eval={evaluation_id})")
            return asyncio.run(_run_thesis_worker(event))

        elif action == "stock_summary_worker":
            # Async stock summary generation (dispatched by /api/stock-summary/generate)
            ticker = event.get("ticker", "")
            logger.info(f"Received stock_summary_worker event (ticker={ticker})")
            return asyncio.run(_run_stock_summary_worker(event))

        else:
            logger.warning(f"Unknown scheduler action: {action}")
            return {"status": "error", "error": f"Unknown action: {action}"}

    # Ensure an event loop exists for Mangum (Python 3.12+ requires this)
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    # Handle as API Gateway request via Mangum
    return _mangum_handler(event, context)
