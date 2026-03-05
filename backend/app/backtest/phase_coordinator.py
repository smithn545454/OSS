"""Backtest Phase Coordinator.

Orchestrates the three-phase backtest pipeline:
  Phase 1 (Evaluate): Rolling-dispatch with 3 parallel chains of window workers
  Phase 2 (Resolve):  N workers, each handles a ticker partition
  Phase 3 (Finalize): Single Lambda, computes metrics + AI export

Rolling-dispatch architecture:
  - Trading days are chunked into windows of DAYS_PER_WINDOW (3) days
  - The coordinator dispatches the first MAX_CONCURRENT_CHAINS (3) windows
  - Each worker, on completion, dispatches its own successor window
  - This maintains exactly 3 concurrent workers at all times

Phase transitions are triggered by the last worker of each phase
via atomic DynamoDB counter detection.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any

import boto3

from app.backtest.coordinator import (
    create_backtest_run,
    generate_trading_days,
    mark_run_failed,
)
from app.backtest.resolve_worker import TICKERS_PER_RESOLVER
from app.core.schemas import BacktestRun, BacktestRunConfig
from app.db.backtest_tables import BacktestRunTable

logger = logging.getLogger(__name__)

# Number of trading days processed per Lambda invocation.
# ~3 min/day × 3 days = ~9 min processing + ~2 min overhead = ~11 min total.
# Safe within the 15-minute Lambda timeout with 4 min headroom.
DAYS_PER_WINDOW = 3

# Number of parallel worker chains. Each chain processes windows sequentially,
# with each worker dispatching its successor. This limits total S3 concurrency
# to MAX_CONCURRENT_CHAINS × 20 threads = 60 concurrent S3 GETs.
MAX_CONCURRENT_CHAINS = 3


def _chunk_days_into_windows(
    trading_days: list[date],
) -> list[list[date]]:
    """Split trading days into windows of DAYS_PER_WINDOW days each."""
    windows: list[list[date]] = []
    for i in range(0, len(trading_days), DAYS_PER_WINDOW):
        windows.append(trading_days[i:i + DAYS_PER_WINDOW])
    return windows


def dispatch_window_worker(
    lambda_client: Any,
    function_name: str,
    run_id: str,
    window_index: int,
    window_days: list[date],
    config_json: dict[str, Any],
    s3_bucket: str,
    total_windows: int,
    total_days: int,
) -> None:
    """Dispatch a single window worker Lambda invocation."""
    payload = {
        "source": "oss.scheduler",
        "action": "backtest_evaluate_window",
        "run_id": run_id,
        "window_index": window_index,
        "window_days": [d.isoformat() for d in window_days],
        "config": config_json,
        "s3_bucket": s3_bucket,
        "total_windows": total_windows,
        "total_days": total_days,
    }
    lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps(payload),
    )


async def coordinate_phase1(
    config: BacktestRunConfig,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Phase 1 coordinator: create run, dispatch rolling-dispatch window workers.

    Chunks trading days into windows of DAYS_PER_WINDOW days each, then
    dispatches the first MAX_CONCURRENT_CHAINS windows. Each worker dispatches
    its own successor, maintaining controlled concurrency.

    Args:
        config: Backtest run configuration
        run_id: Optional pre-created run ID (from API endpoint)

    Returns:
        Summary with run_id and worker count.
    """
    # Load or create the run
    if run_id:
        run_data = await BacktestRunTable.get(run_id)
        if run_data:
            run = BacktestRun(**run_data)
        else:
            run = await create_backtest_run(config, persist=True, run_id=run_id)
    else:
        run = await create_backtest_run(config, persist=True)
    run_id = run.run_id

    # Generate trading days
    start = date.fromisoformat(config.start_date)
    end = date.fromisoformat(config.end_date)
    trading_days = generate_trading_days(start, end)

    if not trading_days:
        from app.backtest.coordinator import mark_run_completed
        await mark_run_completed(
            run_id, summary={"total_trades": 0, "note": "no trading days"}
        )
        return {"status": "success", "run_id": run_id, "workers": 0}

    # Chunk days into windows
    windows = _chunk_days_into_windows(trading_days)
    total_windows = len(windows)
    total_days = len(trading_days)

    # Update run status and progress
    await BacktestRunTable.update_status(run_id, "EVALUATING")
    await BacktestRunTable.set_total_batches(run_id, total_windows)
    await BacktestRunTable.update_progress(run_id, progress={
        "phase": "evaluating",
        "phase1_workers_total": total_windows,
        "phase1_workers_completed": 0,
        "phase2_workers_total": 0,
        "phase2_workers_completed": 0,
        "trades_found": 0,
        "trades_resolved": 0,
        "days_completed": 0,
        "days_total": total_days,
    })

    # Dispatch first wave: up to MAX_CONCURRENT_CHAINS workers
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
    s3_bucket = os.environ.get("BACKTEST_S3_BUCKET", "")
    lambda_client = boto3.client("lambda")
    config_json = config.model_dump(mode="json")

    initial_dispatch = min(MAX_CONCURRENT_CHAINS, total_windows)
    dispatched = 0
    errors: list[str] = []

    logger.info(
        f"[Coordinator] Phase 1: {total_days} trading days → "
        f"{total_windows} windows (×{DAYS_PER_WINDOW} days), "
        f"dispatching {initial_dispatch} initial workers for run {run_id}"
    )

    for idx in range(initial_dispatch):
        try:
            dispatch_window_worker(
                lambda_client=lambda_client,
                function_name=function_name,
                run_id=run_id,
                window_index=idx,
                window_days=windows[idx],
                config_json=config_json,
                s3_bucket=s3_bucket,
                total_windows=total_windows,
                total_days=total_days,
            )
            dispatched += 1
        except Exception as e:
            errors.append(f"Failed to dispatch window {idx}: {e}")

    if dispatched == 0:
        await mark_run_failed(
            run_id, error="Failed to dispatch any Phase 1 window workers"
        )

    logger.info(
        f"[Coordinator] Phase 1 dispatched {dispatched}/{initial_dispatch} "
        f"initial workers ({total_windows} total windows)"
    )

    return {
        "status": "success" if dispatched == initial_dispatch else "partial",
        "run_id": run_id,
        "phase": "evaluate",
        "workers_dispatched": dispatched,
        "workers_total": total_windows,
        "total_days": total_days,
        "errors": errors if errors else None,
    }


async def coordinate_phase2(
    run_id: str,
    config: BacktestRunConfig,
) -> dict[str, Any]:
    """Phase 2 coordinator: partition tickers, fan out resolver workers.

    Called by the last Phase 1 worker when it detects all Phase 1 work is done.

    Args:
        run_id: Backtest run ID
        config: Run configuration

    Returns:
        Summary with resolver count.
    """
    all_tickers = config.policy_snapshot.watchlist.tickers or []

    if not all_tickers:
        logger.warning(f"[Coordinator] No tickers for Phase 2 in run {run_id}")
        # Skip to Phase 3
        return await coordinate_phase3(run_id, config)

    # Partition tickers into resolver chunks
    ticker_partitions: list[list[str]] = []
    for i in range(0, len(all_tickers), TICKERS_PER_RESOLVER):
        ticker_partitions.append(all_tickers[i:i + TICKERS_PER_RESOLVER])

    phase2_total = len(ticker_partitions)

    # Reset batches_completed for Phase 2 counting
    await BacktestRunTable.update_status(run_id, "RESOLVING")
    await BacktestRunTable.set_total_batches(run_id, phase2_total)

    # Update progress
    run_data = await BacktestRunTable.get(run_id)
    current_progress = (run_data or {}).get("progress", {})
    await BacktestRunTable.update_progress(run_id, progress={
        **current_progress,
        "phase": "resolving",
        "phase2_workers_total": phase2_total,
        "phase2_workers_completed": 0,
    })

    # Fan out resolver workers
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
    s3_bucket = os.environ.get("BACKTEST_S3_BUCKET", "")
    lambda_client = boto3.client("lambda")

    dispatched = 0
    errors: list[str] = []

    logger.info(
        f"[Coordinator] Phase 2: dispatching {phase2_total} resolver workers "
        f"for run {run_id} ({len(all_tickers)} tickers, "
        f"{TICKERS_PER_RESOLVER}/partition)"
    )

    for idx, partition in enumerate(ticker_partitions):
        payload = {
            "source": "oss.scheduler",
            "action": "backtest_resolve",
            "run_id": run_id,
            "tickers": partition,
            "config": config.model_dump(mode="json"),
            "s3_bucket": s3_bucket,
            "end_date": config.end_date,
            "phase2_total": phase2_total,
            "partition_index": idx,
        }
        try:
            lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="Event",
                Payload=json.dumps(payload),
            )
            dispatched += 1
        except Exception as e:
            errors.append(f"Failed to dispatch partition {idx}: {e}")

    if dispatched == 0:
        await mark_run_failed(run_id, error="Failed to dispatch any Phase 2 workers")

    logger.info(
        f"[Coordinator] Phase 2 dispatched {dispatched}/{phase2_total} resolvers"
    )

    return {
        "status": "success" if dispatched == phase2_total else "partial",
        "run_id": run_id,
        "phase": "resolve",
        "workers_dispatched": dispatched,
        "workers_total": phase2_total,
        "errors": errors if errors else None,
    }


async def coordinate_phase3(
    run_id: str,
    config: BacktestRunConfig,
) -> dict[str, Any]:
    """Phase 3 coordinator: invoke single finalize Lambda.

    Called by the last Phase 2 resolver when it detects all exits are resolved.
    """
    # Update status
    await BacktestRunTable.update_status(run_id, "FINALIZING")

    run_data = await BacktestRunTable.get(run_id)
    current_progress = (run_data or {}).get("progress", {})
    await BacktestRunTable.update_progress(run_id, progress={
        **current_progress,
        "phase": "finalizing",
    })

    # Invoke finalize Lambda
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
    s3_bucket = os.environ.get("BACKTEST_S3_BUCKET", "")
    lambda_client = boto3.client("lambda")

    payload = {
        "source": "oss.scheduler",
        "action": "backtest_finalize",
        "run_id": run_id,
        "config": config.model_dump(mode="json"),
        "s3_bucket": s3_bucket,
    }

    logger.info(f"[Coordinator] Phase 3: invoking finalize for run {run_id}")

    try:
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(payload),
        )
        return {
            "status": "success",
            "run_id": run_id,
            "phase": "finalize",
        }
    except Exception as e:
        await mark_run_failed(run_id, error=f"Failed to invoke Phase 3: {e}")
        return {"status": "error", "run_id": run_id, "error": str(e)}
