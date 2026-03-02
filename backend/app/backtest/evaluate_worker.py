"""Backtest Phase 1: Evaluate Worker.

Processes a single trading day through the full pipeline (stages 1-7)
for ALL tickers. Writes pending trades to DynamoDB for Phase 2 resolution.

Key differences from the old worker.py:
- No exit resolution — trades stored as PENDING for Phase 2
- No ticker chunking — processes all tickers per date (one parquet read)
- Applies backtest-specific gate overrides from run config
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from app.backtest.exit_resolver import apply_entry_slippage
from app.core.schemas import (
    BacktestRunConfig,
    GateConfig,
    Verdict,
)
from app.db.backtest_pending_table import BacktestPendingTradeTable
from app.db.backtest_tables import BacktestRunTable

logger = logging.getLogger(__name__)


def apply_gate_overrides(config: BacktestRunConfig) -> BacktestRunConfig:
    """Return a config copy with gate overrides baked into the policy snapshot.

    The ScannerOrchestrator reads gate thresholds from policy_config.gates,
    so we inject overrides there before the scan runs.
    """
    overrides = config.gate_overrides
    if not overrides.threshold_overrides and not overrides.disabled_gates:
        return config

    # Deep copy so we don't mutate the original
    config = config.model_copy(deep=True)

    gate_dict = {}
    if config.policy_snapshot.gates:
        gate_dict = config.policy_snapshot.gates.model_dump()
    else:
        gate_dict = GateConfig().model_dump()

    for key, value in overrides.threshold_overrides.items():
        if key in gate_dict:
            gate_dict[key] = value

    config.policy_snapshot.gates = GateConfig(**gate_dict)
    return config


async def evaluate_day(
    run_id: str,
    as_of_date: date,
    config: BacktestRunConfig,
    data_provider: Any,
    tickers: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a single trading day through stages 1-7, return pending trades.

    Does NOT resolve exits — writes pending trades for Phase 2.

    Args:
        run_id: Backtest run ID
        as_of_date: The date to evaluate
        config: Run configuration (with gate_overrides)
        data_provider: HistoricalDataProvider for this date
        tickers: Ticker list (None = all from config)

    Returns:
        List of pending trade dicts ready for DynamoDB write.
    """
    from app.core.pipeline import PipelineOrchestrator
    from app.scanners.orchestrator import ScannerOrchestrator

    ticker_list = tickers or config.policy_snapshot.watchlist.tickers or None
    logger.info(
        f"[Phase1] Evaluating {as_of_date} for run {run_id} "
        f"({len(ticker_list) if ticker_list else 'all'} tickers)"
    )

    pending_trades: list[dict[str, Any]] = []

    try:
        # Apply gate overrides into the policy snapshot
        effective_config = apply_gate_overrides(config)

        # Create pipeline orchestrator (suppress persistence — backtest only)
        pipeline_orchestrator = PipelineOrchestrator()

        # Create scanner orchestrator with DataProvider
        orchestrator = ScannerOrchestrator(
            data_provider=data_provider,
            pipeline_orchestrator=pipeline_orchestrator,
            scanners_enabled=effective_config.scanners_enabled,
        )

        # Run the full pipeline (stages 1-7) with overridden gate thresholds
        result = await orchestrator.run_scan(
            policy_config=effective_config.policy_snapshot,
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

        # Collect pending trades for APPROVE/WATCH decisions
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

            # Build pending trade dict for DynamoDB
            trade_id = str(uuid.uuid4())
            try:
                date.fromisoformat(expiration_date)
            except (ValueError, TypeError):
                continue

            pending_trades.append({
                "trade_id": trade_id,
                "run_id": run_id,
                "entry_date": as_of_date.isoformat(),
                "entry_price": round(entry_price, 4),
                "underlying_ticker": underlying_ticker,
                "option_ticker": option_ticker,
                "option_type": option_type.lower()[:1],  # "c" or "p"
                "strike": strike,
                "expiration_date": expiration_date,
                "scanner_type": scanner_type,
                "verdict": verdict_str,
                "combined_score": combined_score,
                "slippage_model": config.slippage_model,
                "slippage_pct": config.slippage_pct,
                # Exit config stored for Phase 2
                "exit_rules": config.exit_rules.model_dump(),
            })

        logger.info(
            f"[Phase1] Day {as_of_date}: {len(result.evaluations)} evaluations, "
            f"{len(pending_trades)} pending trades"
        )

    except Exception as e:
        import traceback
        logger.error(
            f"[Phase1] Error evaluating {as_of_date}: {e}\n{traceback.format_exc()}"
        )

    return pending_trades


async def run_phase1_worker(
    run_id: str,
    as_of_date: date,
    config: BacktestRunConfig,
    s3_bucket: str,
    phase1_total: int,
) -> dict[str, Any]:
    """Full Phase 1 worker: evaluate one day, write trades, report progress.

    Args:
        run_id: Backtest run ID
        as_of_date: Trading day to evaluate
        config: Run configuration
        s3_bucket: S3 bucket for historical data
        phase1_total: Total number of Phase 1 workers

    Returns:
        Summary dict with trade counts and phase transition info.
    """
    from app.backtest.prefetch import prefetch_batch_data
    from app.core.historical_data_provider import HistoricalDataProvider

    # Prefetch options-chain parquet for this single date
    logger.info(f"[Phase1] Prefetching data for {as_of_date}...")
    shared_cache = prefetch_batch_data(
        s3_bucket=s3_bucket,
        batch_days=[as_of_date],
        ohlcv_lookback=0,
        iv_lookback=0,
    )

    provider = HistoricalDataProvider(
        as_of_date=as_of_date,
        s3_bucket=s3_bucket,
        shared_cache=shared_cache,
    )

    # Evaluate the day
    pending_trades = await evaluate_day(
        run_id=run_id,
        as_of_date=as_of_date,
        config=config,
        data_provider=provider,
    )

    # Write pending trades to DynamoDB
    if pending_trades:
        await BacktestPendingTradeTable.put_batch(pending_trades)

    # Update progress: increment trades found
    await BacktestRunTable.atomic_increment_progress(
        run_id, days_increment=1, trades_increment=len(pending_trades),
    )

    # Atomically increment Phase 1 completion counter
    phase1_completed = await BacktestRunTable.atomic_increment_batches_completed(run_id)
    is_last = phase1_completed >= phase1_total

    logger.info(
        f"[Phase1] Worker done: {as_of_date}, {len(pending_trades)} trades, "
        f"{phase1_completed}/{phase1_total} workers complete"
        f"{' — TRIGGERING PHASE 2' if is_last else ''}"
    )

    return {
        "status": "success",
        "phase": "evaluate",
        "date": as_of_date.isoformat(),
        "trades_found": len(pending_trades),
        "phase1_completed": phase1_completed,
        "phase1_total": phase1_total,
        "trigger_phase2": is_last,
    }
