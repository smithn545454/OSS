"""FastAPI application entry point.

Supports two Lambda invocation modes:
1. API Gateway: HTTP requests via Mangum
2. Scheduled events from EventBridge (Convex daily run, paper-trading
   updates, data refresh jobs, async LLM workers).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.api.routes import alerts as alerts_routes
from app.api.routes import convex as convex_routes
from app.api.routes import health as health_routes
from app.api.routes import llm as llm_routes
from app.api.routes import market as market_routes
from app.api.routes import observability as observability_routes
from app.api.routes import paper_trading as paper_trading_routes
from app.api.routes import policies as policies_routes
from app.api.routes import real_trades as real_trades_routes
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
    app.include_router(paper_trading_routes.router, prefix="/api/paper-trading", tags=["Paper Trading"])
    app.include_router(llm_routes.router, prefix="/api/llm", tags=["LLM"])
    app.include_router(llm_routes.thesis_router, prefix="/api/thesis", tags=["Thesis"])
    app.include_router(llm_routes.stock_summary_router, prefix="/api/stock-summary", tags=["Stock Summary"])
    app.include_router(observability_routes.router, prefix="/api/observability", tags=["Observability"])
    app.include_router(market_routes.router, prefix="/api/market", tags=["Market"])
    app.include_router(alerts_routes.router, prefix="/api/alerts", tags=["Alerts"])
    app.include_router(real_trades_routes.router, prefix="/api/trades", tags=["Real Trades"])
    app.include_router(convex_routes.router, prefix="/api/convex", tags=["Convex Mode"])

    return app


app = create_app()

# Mangum handler for API Gateway requests
_mangum_handler = Mangum(app, lifespan="off")


def _chunk_list(items: list, chunk_size: int) -> list[list]:
    """Split a list into chunks of specified size."""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


async def _run_convex_universe_refresh() -> dict[str, Any]:
    """Convex Mode kinetic-universe construction (daily refresh, runs ~3am PT).

    Reads the optionable, active ticker list from ``oss-dev-sp500-tickers``
    (with authoritative sector data), fetches live market metadata via
    Polygon, runs the Stage 1 gates, and persists a versioned
    ``ConvexUniverseSnapshot``.
    """
    import boto3

    from app.convex.polygon_fetcher import PolygonMetadataFetcher
    from app.convex.universe_builder import UniverseConstructor
    from app.core.policy import PolicyTable
    from app.services.polygon import PolygonClient

    logger.info("Convex universe refresh starting")
    policy = await PolicyTable.get_active()
    if policy is None:
        logger.error("No active policy; aborting Convex universe refresh.")
        return {"status": "error", "error": "no_active_policy"}

    convex_cfg = policy.config.convex
    if not convex_cfg.enabled:
        logger.info(
            "Convex Mode disabled in policy; building universe anyway "
            "(snapshot is harmless until pipeline activated)."
        )

    # Pull candidate tickers + sectors directly from oss-dev-sp500-tickers
    # (filtered to optionable + active). This is the authoritative sector
    # source; StockSummaryTable is a cache populated only for tickers that
    # have already been evaluated and is unreliable for fresh universes.
    settings = get_settings()
    table_name = f"{settings.dynamodb_table_prefix}-sp500-tickers"
    region = settings.aws_region
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    items: list[dict] = []
    last_key = None
    while True:
        kwargs = {
            "FilterExpression": "has_options = :t AND is_active = :t",
            "ExpressionAttributeValues": {":t": True},
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    tickers = [it["ticker"] for it in items]
    sectors: dict[str, str] = {
        it["ticker"]: it["sector"] for it in items if it.get("sector")
    }
    logger.info(
        "Convex universe refresh: %d candidate tickers (%d with sector) from %s",
        len(tickers), len(sectors), table_name,
    )

    # Stash the build result on the constructor for telemetry exposure.
    async with PolygonClient() as polygon:
        fetcher = PolygonMetadataFetcher(polygon)
        constructor = UniverseConstructor(convex_cfg, fetcher)
        snapshot = await constructor.build_snapshot(
            tickers=tickers, policy_version=policy.version, sectors=sectors,
        )
    rejection_breakdown = getattr(constructor, "_last_rejection_breakdown", {})

    return {
        "status": "ok",
        "snapshot_date": snapshot.snapshot_date,
        "total_count": snapshot.total_count,
        "sector_distribution": snapshot.sector_distribution,
        "rejection_breakdown": rejection_breakdown,
    }


async def _run_convex_daily_run() -> dict[str, Any]:
    """Daily Convex Mode pipeline run.

    Triggered by EventBridge at 22:30 UTC weekdays (after the 22:00
    daily data capture settles). Loads the most recent kinetic-universe
    snapshot, runs the four-stage pipeline against live data via the
    production providers, persists per-stage events, and returns a
    summary suitable for CloudWatch logging.
    """
    from app.convex.daily_runner import run_daily_convex_pipeline
    from app.core.policy import PolicyTable
    from app.services.polygon import PolygonClient

    logger.info("Convex daily pipeline starting")
    policy = await PolicyTable.get_active()
    if policy is None:
        logger.error("No active policy; aborting Convex daily run.")
        return {"status": "error", "error": "no_active_policy"}

    convex_cfg = policy.config.convex
    if not convex_cfg.enabled:
        logger.info("Convex Mode disabled in policy; daily run is a no-op.")
        return {"status": "ok", "skipped": True, "reason": "convex_disabled"}

    async with PolygonClient() as polygon:
        result = await run_daily_convex_pipeline(
            config=convex_cfg,
            polygon_client=polygon,
            policy_version=policy.version,
        )

    return {"status": "ok", **result.summary_dict()}


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


_SECTOR_ETFS: tuple[str, ...] = (
    "SPY",
    "XLK", "XLF", "XLV", "XLE", "XLI",
    "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC",
)


async def _run_price_history_refresh() -> dict[str, Any]:
    """Append yesterday's bar to ``oss-dev-price-history`` for every ticker.

    Uses Polygon's grouped-daily endpoint (one API call for all US
    equities) and the SPDR sector ETF list so the Convex Stage 1
    relative-strength inputs stay current. Triggered by EventBridge
    Tue-Sat at ~5am UTC so Friday's close lands before the next scan.
    """
    from app.services.polygon import PolygonClient
    from app.services.price_history import PriceHistoryService

    tickers = await _resolve_backfill_universe()
    if not tickers:
        logger.warning("No tickers resolved for price-history refresh")
        return {"status": "skipped", "reason": "empty_universe"}

    all_tickers = sorted(set(tickers) | set(_SECTOR_ETFS))

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
        ThesisStatus,
        TradeThesis,
    )
    from app.core.schemas import (
        LLMProvider as LLMProviderEnum,
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

    Supported scheduler events (action field on the EventBridge payload):
        convex_universe_refresh, convex_daily_run — Convex pipeline
        paper_update, paper_update_worker, paper_trading_update — paper trading
        earnings_refresh, price_history_refresh, earnings_history_refresh — data refresh
        daily_data_capture — daily market snapshot
        thesis_worker, stock_summary_worker — async LLM jobs
        pattern_discovery_worker, custom_analysis_worker — paper trading insights

    All other events are treated as API Gateway requests via Mangum.
    """
    # Check if this is an OSS scheduler event
    if event.get("source") == "oss.scheduler":
        action = event.get("action")

        if action == "paper_update":
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

        elif action == "convex_universe_refresh":
            # Convex Mode: monthly kinetic-universe construction job.
            logger.info("Received convex_universe_refresh event")
            return asyncio.run(_run_convex_universe_refresh())

        elif action == "convex_daily_run":
            # Convex Mode: daily four-stage pipeline (post EOD data settle).
            logger.info("Received convex_daily_run event")
            return asyncio.run(_run_convex_daily_run())

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
