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
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

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
    app.include_router(observability_routes.router, prefix="/api/observability", tags=["Observability"])
    app.include_router(market_routes.router, prefix="/api/market", tags=["Market"])

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
    """Coordinator: Split tickers into chunks and invoke worker Lambdas in parallel.

    This enables scanning 1000+ tickers within Lambda timeout limits by
    distributing work across multiple Lambda invocations.

    Returns:
        Aggregated scan result summary
    """
    import boto3
    from app.core.watchlist import WatchlistManager
    from app.core.pipeline import PipelineOrchestrator
    from app.db.tables import PolicyTable

    logger.info("Starting COORDINATOR scan - will distribute to workers")

    try:
        # Load policy and get tickers
        policy = await PolicyTable.get_active()
        if policy is None:
            raise ValueError("No active policy found")

        watchlist = WatchlistManager.from_policy(policy.config)
        tickers = watchlist.tickers

        logger.info(f"Coordinator: {len(tickers)} total tickers, chunk_size={CHUNK_SIZE}")

        if len(tickers) <= CHUNK_SIZE:
            # Small enough to process directly — let orchestrator create its own run
            logger.info("Ticker count below threshold, processing directly")
            return await _run_worker_scan(tickers)

        # Multiple chunks: create a shared PipelineRun for all workers
        pipeline = PipelineOrchestrator()
        pipeline_run = await pipeline.start_run(policy.config_version)
        coordinator_run_id = pipeline_run.run_id
        logger.info(f"Coordinator created run {coordinator_run_id}")

        # Split into chunks
        chunks = _chunk_list(tickers, CHUNK_SIZE)
        logger.info(f"Split into {len(chunks)} chunks for parallel processing")

        # Get this Lambda's function name to invoke workers
        function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")

        # Invoke worker Lambdas in parallel
        lambda_client = boto3.client("lambda")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: list[dict[str, Any]] = []
        errors: list[str] = []

        def invoke_worker_sync(chunk_tickers: list[str], chunk_idx: int) -> dict[str, Any]:
            """Synchronously invoke worker Lambda."""
            payload = {
                "source": "oss.scheduler",
                "action": "worker_scan",
                "tickers": chunk_tickers,
                "chunk_index": chunk_idx,
                "run_id": coordinator_run_id,
            }

            response = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload),
            )

            result_payload = json.loads(response["Payload"].read())
            return result_payload

        with ThreadPoolExecutor(max_workers=min(len(chunks), 10)) as executor:
            future_to_chunk = {
                executor.submit(invoke_worker_sync, chunk, idx): idx
                for idx, chunk in enumerate(chunks)
            }

            for future in as_completed(future_to_chunk):
                chunk_idx = future_to_chunk[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info(f"Chunk {chunk_idx} returned: {result.get('status')}")
                except Exception as e:
                    error_msg = f"Chunk {chunk_idx} failed: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)

        # Complete the coordinator run with aggregated counts
        await pipeline.complete_run(
            coordinator_run_id,
            status="completed" if not errors else "failed",
        )

        # Aggregate results
        total_tickers = sum(r.get("tickers_scanned", 0) for r in results)
        total_opportunities = sum(r.get("opportunities_created", 0) for r in results)
        total_duration = max((r.get("duration_ms", 0) for r in results), default=0)

        return {
            "status": "success" if not errors else "partial_success",
            "mode": "coordinator",
            "run_id": coordinator_run_id,
            "chunks_processed": len(results),
            "chunks_failed": len(errors),
            "tickers_scanned": total_tickers,
            "opportunities_created": total_opportunities,
            "duration_ms": total_duration,
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
) -> dict[str, Any]:
    """Worker: Process a specific chunk of tickers.

    Args:
        tickers: List of tickers to scan (uses watchlist if None)
        run_id: Optional coordinator-provided run_id. If None, orchestrator
                creates its own PipelineRun (used for single-worker mode).

    Returns:
        Scan result summary for this chunk
    """
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

        return {
            "status": "success",
            "mode": "worker",
            "run_id": result.run_id,
            "tickers_scanned": result.tickers_scanned,
            "opportunities_created": result.opportunities_created,
            "evaluations_created": result.evaluations_created,
            "duration_ms": result.duration_ms,
        }
    except Exception as e:
        logger.error(f"Worker scan failed: {e}")
        return {
            "status": "error",
            "mode": "worker",
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
    """Run daily paper trading position update."""
    from app.paper_trading.position_manager import update_open_positions
    from app.services.polygon import PolygonClient

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


def handler(event: dict[str, Any], context: Any) -> Any:
    """Lambda handler that routes between API requests and scheduled events.
    
    Supported event types:
    1. API Gateway request (via Mangum)
    2. Scheduled scan from EventBridge: {"source": "oss.scheduler", "action": "run_scan"}
    3. Worker scan (chunk): {"source": "oss.scheduler", "action": "worker_scan", "tickers": [...]}
    
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
            run_id = event.get("run_id")  # Coordinator-provided run_id (if chunked)
            chunk_idx = event.get("chunk_index", 0)
            logger.info(f"Received worker_scan event for chunk {chunk_idx} ({len(tickers) if tickers else 0} tickers)")
            return asyncio.run(_run_worker_scan(tickers, run_id=run_id))

        elif action == "paper_update":
            # Paper trading daily update: fetch prices, update positions
            logger.info("Received paper_update event from EventBridge")
            return asyncio.run(_run_paper_update())

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
