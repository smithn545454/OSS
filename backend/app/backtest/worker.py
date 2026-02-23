"""Backtest Worker.

Processes a batch of trading days for a backtest run:
1. For each day, instantiate a HistoricalDataProvider
2. Run pipeline stages 1-7 (suppress side effects)
3. For each APPROVE/WATCH evaluation, resolve exit via exit_resolver
4. Write BacktestTrades to DynamoDB
5. Update run progress
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.backtest.exit_resolver import apply_entry_slippage, resolve_exit
from app.core.schemas import (
    BacktestRunConfig,
    BacktestTrade,
    Verdict,
)
from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

logger = logging.getLogger(__name__)


async def process_day(
    run_id: str,
    as_of_date: date,
    config: BacktestRunConfig,
    data_provider: Any,
    persist: bool = True,
) -> list[BacktestTrade]:
    """Process a single trading day through the full pipeline.

    Args:
        run_id: Backtest run ID
        as_of_date: The date to evaluate
        config: Run configuration
        data_provider: HistoricalDataProvider for this date
        persist: Whether to persist results to DynamoDB

    Returns:
        List of BacktestTrade records generated for this day.
    """
    from app.core.pipeline import PipelineOrchestrator
    from app.scanners.orchestrator import ScannerOrchestrator

    logger.info(f"Processing day {as_of_date} for run {run_id}")

    trades: list[BacktestTrade] = []

    try:
        # Create pipeline orchestrator (suppress persistence)
        pipeline_orchestrator = PipelineOrchestrator()

        # Create scanner orchestrator with DataProvider (no Polygon)
        orchestrator = ScannerOrchestrator(
            data_provider=data_provider,
            pipeline_orchestrator=pipeline_orchestrator,
        )

        # Run the full pipeline (stages 1-7)
        # Side effects suppressed: no Slack, no LLM, no live DDB writes
        result = await orchestrator.run_scan(
            tickers=config.policy_snapshot.watchlist.tickers,
            run_full_pipeline=True,
            persist_results=False,
            generate_theses=False,
            create_positions=False,
        )

        # Process each evaluation that received APPROVE or WATCH
        for eval_record in result.evaluations:
            verdict = eval_record.get("verdict") or eval_record.get("final_verdict")
            if verdict not in (Verdict.APPROVE.value, Verdict.WATCH.value, "APPROVE", "WATCH"):
                continue

            # Extract trade entry data
            option_ticker = eval_record.get("option_ticker", "")
            underlying_ticker = eval_record.get("underlying_ticker", "")
            option_type = eval_record.get("option_type", "CALL")
            strike = float(eval_record.get("strike", 0) or 0)
            expiration_date = eval_record.get("expiration_date", "")
            scanner_type = eval_record.get("scanner_type", "UNKNOWN")
            combined_score = float(eval_record.get("combined_score", 0) or 0)

            # Get entry price with slippage
            ask = float(eval_record.get("ask", 0) or 0)
            bid = float(eval_record.get("bid", 0) or 0)
            mid = (ask + bid) / 2 if (ask + bid) > 0 else float(eval_record.get("mid", 0) or 0)

            if mid <= 0 and ask <= 0:
                logger.debug(f"Skipping {option_ticker}: no valid price data")
                continue

            entry_price = apply_entry_slippage(
                ask_price=ask if ask > 0 else mid,
                mid_price=mid if mid > 0 else ask,
                slippage_model=config.slippage_model,
                slippage_pct=config.slippage_pct,
            )

            if entry_price <= 0:
                continue

            # Determine market regime (placeholder — Phase 3 will add regime detection)
            market_regime = None

            # Resolve exit by forward-scanning historical data
            trade = await resolve_exit(
                data_provider=data_provider,
                entry_date=as_of_date,
                entry_price=entry_price,
                option_ticker=option_ticker,
                underlying_ticker=underlying_ticker,
                option_type=option_type,
                strike=strike,
                expiration_date=expiration_date,
                exit_config=config.exit_rules,
                scanner_type=scanner_type,
                verdict=str(verdict),
                combined_score=combined_score,
                run_id=run_id,
                slippage_model=config.slippage_model,
                slippage_pct=config.slippage_pct,
                market_regime=market_regime,
            )

            trades.append(trade)

        logger.info(
            f"Day {as_of_date}: {len(result.evaluations)} evaluations, "
            f"{len(trades)} trades generated"
        )

    except Exception as e:
        logger.error(f"Error processing day {as_of_date}: {e}")

    return trades


async def process_batch(
    run_id: str,
    days: list[date],
    config: BacktestRunConfig,
    data_provider_factory: Any,
    persist: bool = True,
) -> list[BacktestTrade]:
    """Process a batch of trading days.

    Args:
        run_id: Backtest run ID
        days: List of trading dates to process
        config: Run configuration
        data_provider_factory: Callable(as_of_date) -> DataProvider
        persist: Whether to persist results

    Returns:
        All trades from this batch.
    """
    all_trades: list[BacktestTrade] = []

    for day in days:
        try:
            # Create a DataProvider for this specific date
            provider = data_provider_factory(day)

            day_trades = await process_day(
                run_id=run_id,
                as_of_date=day,
                config=config,
                data_provider=provider,
                persist=persist,
            )

            all_trades.extend(day_trades)

            # Persist trades
            if persist and day_trades:
                trade_dicts = [t.model_dump() for t in day_trades]
                await BacktestTradeTable.put_batch(trade_dicts)

            # Update progress
            if persist:
                await BacktestRunTable.update_progress(
                    run_id,
                    days_increment=1,
                    trades_increment=len(day_trades),
                )

        except Exception as e:
            logger.error(f"Failed to process {day} in batch: {e}")
            continue

    logger.info(
        f"Batch complete: {len(days)} days, {len(all_trades)} trades"
    )

    return all_trades
