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


def apply_backtest_overrides(config: BacktestRunConfig) -> BacktestRunConfig:
    """Return a config copy with gate AND contract selection overrides for backtesting.

    Applies two categories of overrides:
    1. Gate threshold overrides + disabled gates (Stage 6)
    2. Contract selection filter relaxation (Stage 3)

    Historical data often has lower liquidity/volume than live markets,
    so both filter layers must be relaxed for realistic backtest results.

    All models are frozen (OSSBaseModel), so we use model_copy(update=...)
    to produce new immutable instances rather than mutating in place.
    """
    overrides = config.gate_overrides
    effective_config = config

    # 1. Gate threshold overrides (Stage 6)
    if overrides.threshold_overrides or overrides.disabled_gates:
        gate_dict = {}
        if config.policy_snapshot.gates:
            gate_dict = config.policy_snapshot.gates.model_dump()
        else:
            gate_dict = GateConfig().model_dump()

        for key, value in overrides.threshold_overrides.items():
            if key in gate_dict:
                gate_dict[key] = value

        # Wire disabled_gates into GateConfig
        gate_dict["disabled_gates"] = list(overrides.disabled_gates)

        new_gates = GateConfig(**gate_dict)
        new_policy = config.policy_snapshot.model_copy(update={"gates": new_gates})
        effective_config = config.model_copy(update={"policy_snapshot": new_policy})

    # 2. Contract selection filter relaxation (Stage 3)
    # The policy's ContractSelectionConfig has its own min_oi, min_volume,
    # max_spread_pct that are SEPARATE from GateConfig. These must also be
    # relaxed for historical data, otherwise Stage 3 drops all contracts.
    cs = effective_config.policy_snapshot.contract_selection
    backtest_min_oi = config.min_open_interest  # BacktestRunConfig default: 200
    backtest_min_vol = config.min_option_volume  # BacktestRunConfig default: 50
    backtest_max_spread = overrides.threshold_overrides.get("max_spread_pct", 15.0)

    # Use the MORE permissive value between policy config and backtest config
    new_cs = cs.model_copy(update={
        "min_open_interest": min(cs.min_open_interest, backtest_min_oi,
                                 int(overrides.threshold_overrides.get("min_open_interest",
                                                                       cs.min_open_interest))),
        "min_volume": min(cs.min_volume, backtest_min_vol,
                          int(overrides.threshold_overrides.get("min_volume", cs.min_volume))),
        "max_spread_pct": max(cs.max_spread_pct, backtest_max_spread),
        "min_mid_price": 0.05,  # Lower floor for historical data
    })
    new_policy = effective_config.policy_snapshot.model_copy(
        update={"contract_selection": new_cs}
    )
    return effective_config.model_copy(update={"policy_snapshot": new_policy})


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
        # Apply backtest overrides (gates + contract selection) into the policy snapshot
        effective_config = apply_backtest_overrides(config)

        # Create pipeline orchestrator (suppress persistence — backtest only)
        pipeline_orchestrator = PipelineOrchestrator()

        # Create scanner orchestrator with DataProvider
        orchestrator = ScannerOrchestrator(
            data_provider=data_provider,
            pipeline_orchestrator=pipeline_orchestrator,
            scanners_enabled=effective_config.scanners_enabled,
        )

        # Run the full pipeline (stages 1-7) with overridden thresholds
        # streaming=True: process evaluations one at a time for O(1) memory
        result = await orchestrator.run_scan(
            policy_config=effective_config.policy_snapshot,
            tickers=ticker_list,
            run_full_pipeline=True,
            as_of_date=as_of_date,
            streaming=True,
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
    # ohlcv_lookback=70 ensures feature computation has enough history for SMA50/SMA20
    logger.info(f"[Phase1] Prefetching data for {as_of_date}...")
    shared_cache = prefetch_batch_data(
        s3_bucket=s3_bucket,
        batch_days=[as_of_date],
        ohlcv_lookback=70,
        iv_lookback=260,
    )

    provider = HistoricalDataProvider(
        as_of_date=as_of_date,
        s3_bucket=s3_bucket,
        shared_cache=shared_cache,
    )

    # Evaluate the day
    pending_trades: list[dict[str, Any]] = []
    phase1_completed = 0
    is_last = False

    try:
        pending_trades = await evaluate_day(
            run_id=run_id,
            as_of_date=as_of_date,
            config=config,
            data_provider=provider,
        )

        # Write pending trades to DynamoDB
        if pending_trades:
            await BacktestPendingTradeTable.put_batch(pending_trades)

    except Exception as e:
        import traceback
        logger.error(
            f"[Phase1] Worker error for {as_of_date}: {e}\n{traceback.format_exc()}"
        )

    finally:
        # ALWAYS increment counters — even on failure, so the run doesn't get stuck
        await BacktestRunTable.atomic_increment_progress(
            run_id, days_increment=1, trades_increment=len(pending_trades),
        )
        phase1_completed = await BacktestRunTable.atomic_increment_batches_completed(run_id)
        is_last = phase1_completed >= phase1_total

    logger.info(
        f"[Phase1] Worker done: {as_of_date}, {len(pending_trades)} trades, "
        f"{phase1_completed}/{phase1_total} workers complete"
        f"{' — TRIGGERING PHASE 2' if is_last else ''}"
    )

    return {
        "status": "success" if pending_trades or not is_last else "no_trades",
        "phase": "evaluate",
        "date": as_of_date.isoformat(),
        "trades_found": len(pending_trades),
        "phase1_completed": phase1_completed,
        "phase1_total": phase1_total,
        "trigger_phase2": is_last,
    }
