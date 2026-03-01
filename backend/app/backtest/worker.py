"""Backtest Worker.

Processes a batch of trading days for a backtest run:
1. For each day, instantiate a HistoricalDataProvider
2. Run pipeline stages 1-7 (suppress side effects)
3. Batch-resolve exits for all APPROVE/WATCH evaluations using date-ordered
   forward scan (O(dates) S3 reads, not O(trades * dates))
4. Write BacktestTrades to DynamoDB
5. Update run progress
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.backtest.exit_resolver import (
    PendingTrade,
    _make_trade,
    _next_trading_day,
    apply_entry_slippage,
    create_pending_trade,
    resolve_exits_batch,
)
from app.core.schemas import (
    BacktestRunConfig,
    BacktestTrade,
    ExitReason,
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
    tickers: list[str] | None = None,
) -> list[BacktestTrade]:
    """Process a single trading day through the full pipeline + exit resolution.

    Args:
        run_id: Backtest run ID
        as_of_date: The date to evaluate
        config: Run configuration
        data_provider: HistoricalDataProvider for this date
        persist: Whether to persist results to DynamoDB
        tickers: Optional ticker subset to process (None = all from config)

    Returns:
        List of BacktestTrade records generated for this day.
    """
    from app.core.pipeline import PipelineOrchestrator
    from app.scanners.orchestrator import ScannerOrchestrator

    ticker_list = tickers or config.policy_snapshot.watchlist.tickers or None
    logger.info(
        f"Processing day {as_of_date} for run {run_id} "
        f"({len(ticker_list) if ticker_list else 'all'} tickers)"
    )

    trades: list[BacktestTrade] = []

    try:
        # Create pipeline orchestrator (suppress persistence)
        pipeline_orchestrator = PipelineOrchestrator()

        # Create scanner orchestrator with DataProvider (no Polygon)
        orchestrator = ScannerOrchestrator(
            data_provider=data_provider,
            pipeline_orchestrator=pipeline_orchestrator,
            scanners_enabled=config.scanners_enabled,
        )

        # Run the full pipeline (stages 1-7)
        result = await orchestrator.run_scan(
            policy_config=config.policy_snapshot,
            tickers=ticker_list,
            run_full_pipeline=True,
            as_of_date=as_of_date,
        )

        # Build opportunity_id → scanner type lookup
        opp_scanner_map: dict[str, str] = {}
        for opp in result.opportunities + result.filtered_opportunities:
            if opp.scanner_triggers:
                st = opp.scanner_triggers[0].scanner_type
                opp_scanner_map[opp.opportunity_id] = (
                    st.value if hasattr(st, "value") else str(st)
                )

        # Collect pending trades for batch exit resolution
        pending_trades: list[PendingTrade] = []

        for evaluation in result.evaluations:
            decision = result.decisions.get(evaluation.evaluation_id)
            if not decision:
                continue
            verdict = decision.verdict
            if verdict not in (Verdict.APPROVE, Verdict.WATCH):
                continue

            option_ticker = evaluation.option_ticker
            underlying_ticker = evaluation.underlying_ticker
            ot = evaluation.option_type
            option_type = ot.value if hasattr(ot, "value") else str(ot)
            strike = evaluation.strike
            expiration_date = evaluation.expiration_date
            scanner_type = (
                evaluation.scanner_source
                or opp_scanner_map.get(evaluation.opportunity_id, "UNKNOWN")
            )
            combined_score = decision.final_score

            ask = evaluation.ask
            mid = evaluation.mid

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

            verdict_str = verdict.value if hasattr(verdict, "value") else str(verdict)

            pt = create_pending_trade(
                run_id=run_id,
                entry_date=as_of_date,
                entry_price=entry_price,
                underlying_ticker=underlying_ticker,
                option_ticker=option_ticker,
                option_type=option_type,
                strike=strike,
                expiration_date=expiration_date,
                exit_config=config.exit_rules,
                scanner_type=scanner_type,
                verdict=verdict_str,
                combined_score=combined_score,
                slippage_model=config.slippage_model,
                slippage_pct=config.slippage_pct,
            )

            if pt is not None:
                pending_trades.append(pt)
            else:
                # Unparseable expiration — immediate exit
                import uuid

                trades.append(_make_trade(
                    trade_id=str(uuid.uuid4()), run_id=run_id,
                    entry_date=as_of_date,
                    exit_date=_next_trading_day(as_of_date),
                    ticker=underlying_ticker, option_ticker=option_ticker,
                    option_type=option_type, strike=strike,
                    expiration_date=expiration_date,
                    scanner_type=scanner_type, verdict=verdict_str,
                    combined_score=combined_score, entry_price=entry_price,
                    exit_price=0.01,
                    exit_reason=ExitReason.EXPIRATION.value,
                    days_held=1,
                ))

        # Batch-resolve all exits using date-ordered forward scan
        if pending_trades:
            resolved = await resolve_exits_batch(data_provider, pending_trades)
            trades.extend(resolved)

        logger.info(
            f"Day {as_of_date}: {len(result.evaluations)} evaluations, "
            f"{len(trades)} trades generated"
        )

    except Exception as e:
        import traceback
        logger.error(
            f"Error processing day {as_of_date}: {e}\n{traceback.format_exc()}"
        )

    return trades


async def process_batch(
    run_id: str,
    days: list[date],
    config: BacktestRunConfig,
    data_provider_factory: Any,
    persist: bool = True,
    shared_cache: dict | None = None,
    tickers: list[str] | None = None,
) -> list[BacktestTrade]:
    """Process a batch of trading days.

    Args:
        run_id: Backtest run ID
        days: List of trading dates to process
        config: Run configuration
        data_provider_factory: Callable(as_of_date, shared_cache=None) -> DataProvider
        persist: Whether to persist results
        shared_cache: Optional pre-populated S3 cache dict. If None and
                      BACKTEST_S3_BUCKET is set, prefetch is called automatically.
        tickers: Optional ticker subset to process (None = all from config)

    Returns:
        All trades from this batch.
    """
    import os

    # Pre-materialize all S3 data for this batch if no cache provided
    if shared_cache is None:
        s3_bucket = os.environ.get("BACKTEST_S3_BUCKET", "")
        if s3_bucket and days:
            from app.backtest.prefetch import prefetch_batch_data

            logger.info(f"Prefetching S3 data for {len(days)} days...")
            shared_cache = prefetch_batch_data(
                s3_bucket=s3_bucket,
                batch_days=days,
            )

    all_trades: list[BacktestTrade] = []

    for day in days:
        try:
            provider = data_provider_factory(day, shared_cache=shared_cache)

            day_trades = await process_day(
                run_id=run_id,
                as_of_date=day,
                config=config,
                data_provider=provider,
                persist=persist,
                tickers=tickers,
            )

            all_trades.extend(day_trades)

            # Persist trades
            if persist and day_trades:
                trade_dicts = [t.model_dump() for t in day_trades]
                await BacktestTradeTable.put_batch(trade_dicts)

            # Update progress per-day (atomic for safe concurrent fan-out)
            if persist:
                await BacktestRunTable.atomic_increment_progress(
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
