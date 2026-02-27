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
from typing import Any, AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

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
    app.include_router(observability_routes.router, prefix="/api/observability", tags=["Observability"])
    app.include_router(market_routes.router, prefix="/api/market", tags=["Market"])
    app.include_router(backtest_routes.router, prefix="/api/backtest", tags=["Backtest"])

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
                    logger.info(f"Last worker finished, completing run {run_id}")
                    pipeline = PipelineOrchestrator()
                    await pipeline.complete_run(run_id, status="completed")
            except Exception as e:
                logger.error(f"Worker self-report failed (non-fatal): {e}")

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
            # Stage 4: Feature Computation
            feature_sets = await run_feature_computation(
                run_id=run_id,
                evaluations=evaluations,
                opportunities=opportunities,
                polygon_client=polygon,
                orchestrator=pipeline,
                config=policy_config.features,
                persist_features=True,
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
                generate_theses=True,
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
    """Backtest coordinator: create run, generate batches, fan out workers.

    Can be invoked via API endpoint or direct Lambda invocation.

    Args:
        event: Must contain "config" dict with BacktestRunConfig fields.

    Returns:
        Summary with run_id and batch count.
    """
    from app.backtest.coordinator import (
        create_backtest_run,
        mark_run_completed,
        start_backtest_run,
    )
    from app.backtest.worker import process_batch
    from app.core.historical_data_provider import HistoricalDataProvider
    from app.core.schemas import BacktestRunConfig

    logger.info("Backtest coordinator starting")

    try:
        # Parse config from event
        config_data = event.get("config", {})
        config = BacktestRunConfig(**config_data)

        # Use existing run_id if provided (created by API endpoint), else create new
        existing_run_id = event.get("run_id")
        if existing_run_id:
            # Load the existing run and update it
            run = await create_backtest_run(
                config, persist=True, run_id=existing_run_id
            )
        else:
            run = await create_backtest_run(config, persist=True)
        run_id = run.run_id

        # Generate batches
        batches = await start_backtest_run(run, persist=True)

        if not batches:
            await mark_run_completed(run_id, summary={"trades": 0, "note": "no trading days"})
            return {
                "status": "success",
                "run_id": run_id,
                "batches": 0,
                "note": "no trading days in range",
            }

        total_batches = len(batches)
        s3_bucket = os.environ.get("BACKTEST_S3_BUCKET", "")

        # For now, run inline (sequential). Phase 5 adds Lambda fan-out.
        if total_batches <= 20 or not s3_bucket:
            # Small run or no S3 bucket configured — run inline
            logger.info(
                f"Backtest {run_id}: running {total_batches} batches inline"
            )

            def data_provider_factory(as_of_date):
                return HistoricalDataProvider(
                    as_of_date=as_of_date,
                    s3_bucket=s3_bucket,
                )

            all_trades = []
            for batch_idx, batch in enumerate(batches):
                logger.info(
                    f"Backtest {run_id}: batch {batch_idx + 1}/{total_batches}"
                )
                batch_trades = await process_batch(
                    run_id=run_id,
                    days=batch,
                    config=config,
                    data_provider_factory=data_provider_factory,
                    persist=True,
                )
                all_trades.extend(batch_trades)

            # Compute full metrics and mark complete
            from app.backtest.metrics import calculate_metrics

            metrics = calculate_metrics(
                all_trades,
                starting_capital=config.starting_capital,
            )
            metrics["total_days"] = sum(len(b) for b in batches)
            metrics["total_batches"] = total_batches
            await mark_run_completed(run_id, summary=metrics)

            return {
                "status": "success",
                "run_id": run_id,
                "mode": "inline",
                "trades": len(all_trades),
                "batches_processed": total_batches,
            }

        else:
            # Large run — fan out to worker Lambdas
            import boto3

            function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
            lambda_client = boto3.client("lambda")

            dispatched = 0
            errors: list[str] = []

            for idx, batch in enumerate(batches):
                if idx > 0:
                    time.sleep(1)  # Slight stagger

                payload = {
                    "source": "oss.scheduler",
                    "action": "backtest_worker",
                    "run_id": run_id,
                    "days": [d.isoformat() for d in batch],
                    "config": config.model_dump(mode="json"),
                    "batch_index": idx,
                    "total_batches": total_batches,
                    "s3_bucket": s3_bucket,
                }

                try:
                    lambda_client.invoke(
                        FunctionName=function_name,
                        InvocationType="Event",
                        Payload=json.dumps(payload),
                    )
                    dispatched += 1
                except Exception as e:
                    errors.append(f"Failed to dispatch batch {idx}: {e}")

            logger.info(
                f"Backtest coordinator dispatched {dispatched}/{total_batches} workers "
                f"for run {run_id}"
            )

            return {
                "status": "success" if not errors else "partial_success",
                "run_id": run_id,
                "mode": "fan_out",
                "batches_dispatched": dispatched,
                "errors": errors if errors else None,
            }

    except Exception as e:
        logger.error(f"Backtest coordinator failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def _run_backtest_worker(event: dict[str, Any]) -> dict[str, Any]:
    """Backtest worker: process a batch of trading days.

    Args:
        event: Must contain run_id, days, config, s3_bucket.

    Returns:
        Summary of trades produced.
    """
    from app.backtest.worker import process_batch
    from app.core.historical_data_provider import HistoricalDataProvider
    from app.core.schemas import BacktestRunConfig

    run_id = event.get("run_id", "")
    day_strs = event.get("days", [])
    config_data = event.get("config", {})
    batch_index = event.get("batch_index", 0)
    total_batches = event.get("total_batches", 1)
    s3_bucket = event.get("s3_bucket", os.environ.get("BACKTEST_S3_BUCKET", ""))

    logger.info(
        f"Backtest worker: run={run_id}, batch {batch_index + 1}/{total_batches}, "
        f"{len(day_strs)} days"
    )

    try:
        from datetime import date as date_type
        config = BacktestRunConfig(**config_data)
        days = [date_type.fromisoformat(d) for d in day_strs]

        def data_provider_factory(as_of_date):
            return HistoricalDataProvider(
                as_of_date=as_of_date,
                s3_bucket=s3_bucket,
            )

        trades = await process_batch(
            run_id=run_id,
            days=days,
            config=config,
            data_provider_factory=data_provider_factory,
            persist=True,
        )

        logger.info(
            f"Backtest worker batch {batch_index}: {len(trades)} trades from {len(days)} days"
        )

        return {
            "status": "success",
            "run_id": run_id,
            "batch_index": batch_index,
            "days_processed": len(days),
            "trades_produced": len(trades),
        }

    except Exception as e:
        logger.error(f"Backtest worker failed: {e}", exc_info=True)
        return {
            "status": "error",
            "run_id": run_id,
            "batch_index": batch_index,
            "error": str(e),
        }


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


def handler(event: dict[str, Any], context: Any) -> Any:
    """Lambda handler that routes between API requests and scheduled events.

    Supported event types:
    1. API Gateway request (via Mangum)
    2. Scheduled scan from EventBridge: {"source": "oss.scheduler", "action": "run_scan"}
    3. Worker scan (chunk): {"source": "oss.scheduler", "action": "worker_scan", ...}
    4. Paper trading update: {"source": "oss.scheduler", "action": "paper_update"}
    5. Backtest coordinator: {"source": "oss.scheduler", "action": "backtest_coordinator", ...}
    6. Backtest worker: {"source": "oss.scheduler", "action": "backtest_worker", ...}

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
            # Backtest coordinator: create run and fan out workers
            logger.info("Received backtest_coordinator event")
            return asyncio.run(_run_backtest_coordinator(event))

        elif action == "backtest_worker":
            # Backtest worker: process a batch of trading days
            batch_idx = event.get("batch_index", 0)
            logger.info(f"Received backtest_worker event (batch {batch_idx})")
            return asyncio.run(_run_backtest_worker(event))

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
