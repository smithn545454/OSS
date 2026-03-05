"""Backtest API routes.

Phase 1 endpoints (Data Store):
- GET  /data-store/status   — S3 bucket inventory
- POST /data-store/validate — Run integrity checks

Phase 2 endpoints (Replay Engine):
- POST /runs               — Create and start a backtest run
- GET  /runs               — List all runs (with status filter)
- GET  /runs/{run_id}      — Get run details + progress
- DELETE /runs/{run_id}    — Cancel/delete a run
- GET  /runs/{run_id}/trades — List trades for a run

Phase 3 endpoints (Results):
- GET  /runs/{run_id}/summary       — Core metrics summary
- GET  /runs/{run_id}/equity-curve  — Daily equity data
- GET  /runs/{run_id}/monthly-pnl   — Monthly P&L breakdown
- GET  /runs/{run_id}/segments/{type} — Segmented analysis

Phase 4 endpoints (AI Advisor):
- GET  /runs/{run_id}/readiness     — Go-live readiness assessment
- GET  /runs/{run_id}/insights      — AI-generated insights
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.schemas import BacktestRunConfig, PolicyConfig
from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================


class DatasetStatus(BaseModel):
    """Status of a single dataset in S3."""

    name: str
    prefix: str
    file_count: int
    date_count: int
    earliest_date: Optional[str] = None
    latest_date: Optional[str] = None
    total_size_mb: float = 0.0
    status: str = "unknown"  # "complete" | "partial" | "missing"


class DataStoreStatus(BaseModel):
    """Overall data store status response."""

    bucket: str
    datasets: list[DatasetStatus]
    overall_status: str  # "ready" | "incomplete" | "empty"
    total_size_mb: float = 0.0
    timestamp: str


class ValidationResult(BaseModel):
    """Result of a data validation check."""

    check_name: str
    passed: bool
    message: str
    details: Optional[dict[str, Any]] = None


class ValidationResponse(BaseModel):
    """Response from the validate endpoint."""

    passed: bool
    checks: list[ValidationResult]
    timestamp: str


# ============================================================================
# Data Store Endpoints
# ============================================================================


def _get_s3_client():
    """Get an S3 client configured for the application region."""
    settings = get_settings()
    return boto3.client("s3", region_name=settings.aws_region)


def _get_bucket_name() -> str:
    """Get the backtest S3 bucket name from settings."""
    settings = get_settings()
    if settings.backtest_s3_bucket:
        return settings.backtest_s3_bucket
    # Fallback to convention
    prefix = settings.dynamodb_table_prefix
    try:
        sts = boto3.client("sts", region_name=settings.aws_region)
        account_id = sts.get_caller_identity()["Account"]
        return f"{prefix}-backtest-{account_id}"
    except Exception:
        return f"{prefix}-backtest"


def _list_date_partitions(
    s3_client, bucket: str, prefix: str
) -> tuple[list[str], int, float]:
    """List date partitions under a prefix.

    Returns:
        Tuple of (sorted date strings, file count, total size in MB)
    """
    dates: set[str] = set()
    file_count = 0
    total_size = 0

    paginator = s3_client.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                size = obj.get("Size", 0)
                total_size += size
                file_count += 1

                # Extract date from path like "prefix/date=2024-01-02/data.parquet"
                parts = key.split("/")
                for part in parts:
                    if part.startswith("date="):
                        dates.add(part.split("=")[1])
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchBucket":
            return [], 0, 0.0
        raise

    sorted_dates = sorted(dates)
    return sorted_dates, file_count, total_size / (1024 * 1024)


@router.get("/data-store/status", response_model=DataStoreStatus)
async def get_data_store_status() -> DataStoreStatus:
    """Get the inventory status of the backtest data store.

    Returns dataset counts, date ranges, and completeness for each
    data type (options chains, stock OHLCV, IV history, market context).
    """
    bucket = _get_bucket_name()
    s3 = _get_s3_client()

    datasets: list[DatasetStatus] = []
    total_size = 0.0

    # Define dataset prefixes to check
    dataset_configs = [
        ("Options Chains", "options-chains/"),
        ("Stock OHLCV", "stock-ohlcv/"),
        ("IV History", "iv-history/"),
        ("Market Context", "market-context/"),
    ]

    for name, prefix in dataset_configs:
        try:
            if prefix == "market-context/":
                # Market context is a single file, not date-partitioned
                try:
                    response = s3.head_object(Bucket=bucket, Key="market-context/data.parquet")
                    size_mb = response.get("ContentLength", 0) / (1024 * 1024)
                    datasets.append(DatasetStatus(
                        name=name,
                        prefix=prefix,
                        file_count=1,
                        date_count=0,
                        status="complete",
                        total_size_mb=round(size_mb, 2),
                    ))
                    total_size += size_mb
                except ClientError:
                    datasets.append(DatasetStatus(
                        name=name,
                        prefix=prefix,
                        file_count=0,
                        date_count=0,
                        status="missing",
                    ))
            else:
                dates, file_count, size_mb = _list_date_partitions(s3, bucket, prefix)
                status = "complete" if len(dates) > 100 else "partial" if dates else "missing"
                datasets.append(DatasetStatus(
                    name=name,
                    prefix=prefix,
                    file_count=file_count,
                    date_count=len(dates),
                    earliest_date=dates[0] if dates else None,
                    latest_date=dates[-1] if dates else None,
                    total_size_mb=round(size_mb, 2),
                    status=status,
                ))
                total_size += size_mb
        except ClientError as e:
            logger.warning(f"Error checking dataset {name}: {e}")
            datasets.append(DatasetStatus(
                name=name,
                prefix=prefix,
                file_count=0,
                date_count=0,
                status="error",
            ))

    # Determine overall status
    statuses = {d.status for d in datasets}
    if all(s == "complete" for s in statuses):
        overall = "ready"
    elif "missing" in statuses or "error" in statuses:
        overall = "incomplete"
    else:
        overall = "incomplete"

    return DataStoreStatus(
        bucket=bucket,
        datasets=datasets,
        overall_status=overall,
        total_size_mb=round(total_size, 2),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/data-store/validate", response_model=ValidationResponse)
async def validate_data_store() -> ValidationResponse:
    """Run integrity checks on the backtest data store.

    Checks:
    1. Bucket exists and is accessible
    2. Options chains have data
    3. Stock OHLCV has data
    4. IV history has data
    5. Market context file exists
    6. Date coverage alignment between datasets
    """
    bucket = _get_bucket_name()
    s3 = _get_s3_client()
    checks: list[ValidationResult] = []

    # Check 1: Bucket exists
    try:
        s3.head_bucket(Bucket=bucket)
        checks.append(ValidationResult(
            check_name="bucket_exists",
            passed=True,
            message=f"Bucket '{bucket}' exists and is accessible",
        ))
    except ClientError:
        checks.append(ValidationResult(
            check_name="bucket_exists",
            passed=False,
            message=f"Bucket '{bucket}' does not exist or is not accessible",
        ))
        return ValidationResponse(
            passed=False,
            checks=checks,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # Check 2-4: Dataset presence
    dataset_dates: dict[str, list[str]] = {}
    for name, prefix, min_expected in [
        ("options_chains", "options-chains/", 100),
        ("stock_ohlcv", "stock-ohlcv/", 100),
        ("iv_history", "iv-history/", 50),
    ]:
        dates, file_count, _ = _list_date_partitions(s3, bucket, prefix)
        dataset_dates[name] = dates

        if len(dates) >= min_expected:
            checks.append(ValidationResult(
                check_name=f"{name}_coverage",
                passed=True,
                message=f"{name}: {len(dates)} dates ({dates[0]} to {dates[-1]})",
                details={"date_count": len(dates), "file_count": file_count},
            ))
        elif dates:
            checks.append(ValidationResult(
                check_name=f"{name}_coverage",
                passed=False,
                message=f"{name}: only {len(dates)} dates (expected >= {min_expected})",
                details={"date_count": len(dates), "file_count": file_count},
            ))
        else:
            checks.append(ValidationResult(
                check_name=f"{name}_coverage",
                passed=False,
                message=f"{name}: no data found",
            ))

    # Check 5: Market context
    try:
        s3.head_object(Bucket=bucket, Key="market-context/data.parquet")
        checks.append(ValidationResult(
            check_name="market_context",
            passed=True,
            message="Market context file exists",
        ))
    except ClientError:
        checks.append(ValidationResult(
            check_name="market_context",
            passed=False,
            message="Market context file missing",
        ))

    # Check 6: Date alignment
    ohlcv_dates = set(dataset_dates.get("stock_ohlcv", []))
    options_dates = set(dataset_dates.get("options_chains", []))
    if ohlcv_dates and options_dates:
        overlap = ohlcv_dates & options_dates
        ohlcv_only = ohlcv_dates - options_dates
        options_only = options_dates - ohlcv_dates

        if len(overlap) > 0.9 * max(len(ohlcv_dates), len(options_dates)):
            checks.append(ValidationResult(
                check_name="date_alignment",
                passed=True,
                message=f"{len(overlap)} overlapping dates between OHLCV and options",
                details={
                    "overlap": len(overlap),
                    "ohlcv_only": len(ohlcv_only),
                    "options_only": len(options_only),
                },
            ))
        else:
            checks.append(ValidationResult(
                check_name="date_alignment",
                passed=False,
                message=f"Poor date alignment: {len(overlap)} overlap, "
                        f"{len(ohlcv_only)} OHLCV-only, {len(options_only)} options-only",
                details={
                    "overlap": len(overlap),
                    "ohlcv_only": len(ohlcv_only),
                    "options_only": len(options_only),
                },
            ))
    else:
        checks.append(ValidationResult(
            check_name="date_alignment",
            passed=False,
            message="Cannot check alignment — one or both datasets are empty",
        ))

    all_passed = all(c.passed for c in checks)
    return ValidationResponse(
        passed=all_passed,
        checks=checks,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ============================================================================
# Replay Engine Endpoints (Phase 2)
# ============================================================================


class CreateRunRequest(BaseModel):
    """Request body for creating a new backtest run."""

    name: str
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    tickers: list[str] = Field(default_factory=list)
    policy_snapshot: Optional[dict[str, Any]] = None
    scanners_enabled: list[str] = Field(
        default_factory=lambda: [
            "breakout", "compression", "cheap_options", "unusual_volume",
        ]
    )
    slippage_model: str = "ask_plus_pct"
    slippage_pct: float = 0.05
    exit_rules: Optional[dict[str, Any]] = None
    starting_capital: float = 10_000.0
    gate_overrides: Optional[dict[str, Any]] = None


async def _run_backtest_inline(
    config: BacktestRunConfig,
    run_id: Optional[str] = None,
) -> None:
    """Run a backtest inline (used as background task for local dev).

    Processes all days sequentially and marks complete/failed.
    """
    from app.backtest.coordinator import (
        create_backtest_run,
        mark_run_completed,
        mark_run_failed,
        start_backtest_run,
    )
    from app.backtest.worker import process_batch
    from app.core.historical_data_provider import HistoricalDataProvider

    run = None
    try:
        run = await create_backtest_run(config, persist=True, run_id=run_id)
        batches = await start_backtest_run(run, persist=True)

        if not batches:
            await mark_run_completed(
                run.run_id, summary={"total_trades": 0, "note": "no trading days"}
            )
            return

        s3_bucket = os.environ.get("BACKTEST_S3_BUCKET", "")

        def data_provider_factory(as_of_date, shared_cache=None):
            return HistoricalDataProvider(
                as_of_date=as_of_date,
                s3_bucket=s3_bucket,
                shared_cache=shared_cache,
            )

        all_trades = []
        for batch in batches:
            batch_trades = await process_batch(
                run_id=run.run_id,
                days=batch,
                config=config,
                data_provider_factory=data_provider_factory,
                persist=True,
            )
            all_trades.extend(batch_trades)

        # Compute full metrics
        from app.backtest.metrics import calculate_metrics

        metrics = calculate_metrics(
            all_trades,
            starting_capital=config.starting_capital,
        )
        metrics["total_days"] = sum(len(b) for b in batches)
        await mark_run_completed(run.run_id, summary=metrics)

    except Exception as e:
        logger.error(f"Backtest run failed: {e}", exc_info=True)
        if run:
            await mark_run_failed(run.run_id, error=str(e))


@router.post("/capture")
async def trigger_data_capture(
    capture_date: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Trigger daily market data capture for a specific date.

    Captures stock OHLCV, options chains, IV history, and market context
    from Polygon and writes to S3 in the standard backtest data format.

    Args:
        capture_date: Date to capture (YYYY-MM-DD). Defaults to today/last trading day.
        force: If True, overwrite existing data for this date.
    """
    from app.data_capture.daily_capture import run_daily_capture

    try:
        result = await run_daily_capture(capture_date=capture_date, force=force)
        return result
    except Exception as e:
        logger.error(f"Data capture failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


@router.post("/runs")
async def create_backtest_run_endpoint(
    request: CreateRunRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Create and start a new backtest run.

    The run is created immediately and processing happens in the background.
    Poll GET /runs/{run_id} for progress updates.
    """
    from datetime import date

    from app.backtest.coordinator import (
        create_backtest_run,
        generate_trading_days,
    )

    try:
        # Validate dates
        try:
            start = date.fromisoformat(request.start_date)
            end = date.fromisoformat(request.end_date)
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail=f"Invalid date format: {e}"
            )

        if start > end:
            raise HTTPException(
                status_code=400, detail="start_date must be before end_date"
            )

        trading_days = generate_trading_days(start, end)
        if not trading_days:
            raise HTTPException(
                status_code=400, detail="No trading days in the specified range"
            )

        # Build policy snapshot — use provided or defaults, inject tickers
        if request.policy_snapshot:
            snapshot_data = dict(request.policy_snapshot)
        else:
            snapshot_data = {}

        if request.tickers:
            watchlist_data = snapshot_data.get("watchlist", {})
            if isinstance(watchlist_data, dict):
                watchlist_data["tickers"] = request.tickers
            else:
                watchlist_data = {"tickers": request.tickers}
            snapshot_data["watchlist"] = watchlist_data

        # If no tickers in the config, resolve from active watchlist or defaults.
        # This mirrors the live pipeline's WatchlistManager fallback chain:
        # 1. Policy override → 2. S&P 500 from DynamoDB → 3. DEFAULT_WATCHLIST
        # Must inject into snapshot_data BEFORE building the frozen PolicyConfig.
        watchlist_data = snapshot_data.get("watchlist", {})
        has_tickers = (
            isinstance(watchlist_data, dict) and watchlist_data.get("tickers")
        )
        if not has_tickers:
            from app.core.watchlist import WatchlistManager

            # Build temporary PolicyConfig to resolve tickers via WatchlistManager
            temp_policy = PolicyConfig(**snapshot_data)
            wm = await WatchlistManager.from_policy_async(temp_policy)
            resolved_tickers = wm.tickers
            logger.info(
                f"No tickers in request; resolved {len(resolved_tickers)} "
                f"tickers from active watchlist"
            )
            if isinstance(watchlist_data, dict):
                watchlist_data["tickers"] = resolved_tickers
            else:
                watchlist_data = {"tickers": resolved_tickers}
            snapshot_data["watchlist"] = watchlist_data

        policy_snapshot = PolicyConfig(**snapshot_data)

        # Build run config
        config_kwargs: dict[str, Any] = {
            "name": request.name,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "policy_snapshot": policy_snapshot,
            "scanners_enabled": request.scanners_enabled,
            "slippage_model": request.slippage_model,
            "slippage_pct": request.slippage_pct,
            "starting_capital": request.starting_capital,
        }
        if request.exit_rules:
            from app.core.schemas import BacktestExitConfig
            config_kwargs["exit_rules"] = BacktestExitConfig(**request.exit_rules)
        if request.gate_overrides:
            from app.core.schemas import BacktestGateOverrides
            config_kwargs["gate_overrides"] = BacktestGateOverrides(
                **request.gate_overrides
            )

        config = BacktestRunConfig(**config_kwargs)

        # Try Lambda invocation first (production), fall back to inline
        s3_bucket = os.environ.get("BACKTEST_S3_BUCKET", "")
        function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")

        if function_name and s3_bucket:
            # Production: create run first, then invoke Lambda coordinator with run_id
            try:
                run = await create_backtest_run(config, persist=True)
                lambda_client = boto3.client("lambda")
                payload = {
                    "source": "oss.scheduler",
                    "action": "backtest_coordinator",
                    "config": config.model_dump(mode="json"),
                    "run_id": run.run_id,
                }
                lambda_client.invoke(
                    FunctionName=function_name,
                    InvocationType="Event",
                    Payload=json.dumps(payload),
                )
                return {
                    "status": "started",
                    "run_id": run.run_id,
                    "mode": "lambda",
                    "trading_days": len(trading_days),
                }
            except Exception as e:
                logger.warning(f"Lambda invocation failed, falling back to inline: {e}")

        # Fallback: create run and process inline via background task (local dev)
        run = await create_backtest_run(config, persist=True)
        background_tasks.add_task(_run_backtest_inline, config, run.run_id)

        return {
            "status": "started",
            "run_id": run.run_id,
            "mode": "inline",
            "trading_days": len(trading_days),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create backtest run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Minutes with no progress before a run is considered stale (chain may have broken).
# For EVALUATING runs: re-dispatch stuck chains. For others: auto-finalize.
STALE_PROGRESS_TIMEOUT_MINUTES = 30

# Minutes with zero total progress before a run is force-finalized (genuinely dead).
STALE_FORCE_FINALIZE_MINUTES = 240


ACTIVE_RUN_STATUSES = {"RUNNING", "EVALUATING", "RESOLVING", "FINALIZING"}


async def _try_redispatch_stuck_chains(run: dict[str, Any]) -> bool:
    """Attempt to re-dispatch broken chains for an EVALUATING run.

    Uses the rolling-dispatch architecture: recomputes which window indices
    haven't completed, and dispatches workers for the first missing window
    in each chain.

    Returns True if any workers were re-dispatched.
    """
    import os

    import boto3

    from app.backtest.coordinator import generate_trading_days
    from app.backtest.phase_coordinator import (
        MAX_CONCURRENT_CHAINS,
        _chunk_days_into_windows,
        dispatch_window_worker,
    )

    run_id = run.get("run_id", "")
    config_data = run.get("config", {})
    if not config_data:
        return False

    try:
        from app.core.schemas import BacktestRunConfig
        config = BacktestRunConfig(**config_data)
    except Exception:
        return False

    # Recompute the full window schedule
    start = date.fromisoformat(config.start_date)
    end = date.fromisoformat(config.end_date)
    trading_days = generate_trading_days(start, end)
    windows = _chunk_days_into_windows(trading_days)
    total_windows = len(windows)
    total_days = len(trading_days)

    batches_completed = int(run.get("batches_completed", 0))
    if batches_completed >= total_windows:
        # All windows done but Phase 2 wasn't triggered — trigger it now
        logger.info(f"[SelfHeal] All windows done for {run_id}, triggering Phase 2")
        from app.backtest.phase_coordinator import coordinate_phase2
        await coordinate_phase2(run_id, config)
        return True

    # The rolling dispatch has MAX_CONCURRENT_CHAINS independent chains.
    # If a chain broke, its "next" window was never dispatched.
    # We can't easily know WHICH windows completed, but we know how many.
    # Re-dispatch by sending fresh workers for the next expected windows.
    # If a window was already completed, the duplicate worker just increments
    # the counter again — this is acceptable (counter may overshoot, but
    # the >= check in Phase 2 triggering handles it).
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "oss-dev-backend")
    s3_bucket = os.environ.get("BACKTEST_S3_BUCKET", "")
    lambda_client = boto3.client("lambda")
    config_json = config.model_dump(mode="json")

    # Dispatch up to MAX_CONCURRENT_CHAINS workers starting from where we estimate
    # the chains should be. Use batches_completed as a rough position estimate.
    dispatched = 0
    for chain_offset in range(MAX_CONCURRENT_CHAINS):
        # Each chain processes windows at indices: chain_offset, chain_offset+3, chain_offset+6, ...
        # Find the first window in this chain that's beyond batches_completed
        window_idx = chain_offset
        while window_idx < batches_completed and window_idx < total_windows:
            window_idx += MAX_CONCURRENT_CHAINS
        if window_idx >= total_windows:
            continue
        try:
            dispatch_window_worker(
                lambda_client=lambda_client,
                function_name=function_name,
                run_id=run_id,
                window_index=window_idx,
                window_days=windows[window_idx],
                config_json=config_json,
                s3_bucket=s3_bucket,
                total_windows=total_windows,
                total_days=total_days,
            )
            dispatched += 1
            logger.info(
                f"[SelfHeal] Re-dispatched window {window_idx} "
                f"for stuck run {run_id}"
            )
        except Exception as e:
            logger.error(
                f"[SelfHeal] Failed to re-dispatch window {window_idx}: {e}"
            )

    return dispatched > 0


async def _finalize_stale_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check for active runs that have stalled and attempt recovery.

    Uses progress-based staleness detection:
    - If progress.last_progress_at hasn't updated in STALE_PROGRESS_TIMEOUT_MINUTES:
      For EVALUATING runs, re-dispatch stuck chains (self-healing).
    - If no progress at all for STALE_FORCE_FINALIZE_MINUTES:
      Auto-finalize with whatever trades exist (genuinely dead run).

    This supports long backtests (hours) that legitimately take a long time,
    by checking progress freshness rather than creation age.
    """
    from app.backtest.metrics import calculate_metrics
    from app.core.schemas import BacktestTrade

    now = datetime.now(timezone.utc)
    updated = []

    for run in runs:
        if run.get("status") not in ACTIVE_RUN_STATUSES:
            updated.append(run)
            continue

        # Check progress freshness (preferred) or fall back to creation time
        progress = run.get("progress", {})
        last_progress_str = progress.get("last_progress_at", "")
        created_str = run.get("created_at", "")

        reference_time = None
        try:
            if last_progress_str:
                reference_time = datetime.fromisoformat(last_progress_str)
            elif created_str:
                reference_time = datetime.fromisoformat(created_str)
        except (ValueError, TypeError):
            pass

        if reference_time is None:
            updated.append(run)
            continue

        staleness_minutes = (now - reference_time).total_seconds() / 60

        # Not stale yet — keep as-is
        if staleness_minutes < STALE_PROGRESS_TIMEOUT_MINUTES:
            updated.append(run)
            continue

        run_id = run.get("run_id", "")

        # For EVALUATING runs with partial progress, try to re-dispatch stuck chains
        if (
            run.get("status") == "EVALUATING"
            and staleness_minutes < STALE_FORCE_FINALIZE_MINUTES
        ):
            logger.info(
                f"[SelfHeal] Run {run_id} stale for {staleness_minutes:.0f} min "
                f"in EVALUATING — attempting chain re-dispatch"
            )
            try:
                redispatched = await _try_redispatch_stuck_chains(run)
                if redispatched:
                    logger.info(
                        f"[SelfHeal] Re-dispatched workers for run {run_id}"
                    )
                    # Reset staleness clock so the NEXT poll doesn't
                    # immediately re-dispatch again (thundering herd fix).
                    now_iso = datetime.now(timezone.utc).isoformat()
                    updated_progress = {**progress}
                    updated_progress["last_progress_at"] = now_iso
                    updated_progress["last_redispatch_at"] = now_iso
                    await BacktestRunTable.update_progress(
                        run_id, progress=updated_progress,
                    )
                    updated.append(run)
                    continue
            except Exception as e:
                logger.error(f"[SelfHeal] Re-dispatch failed for {run_id}: {e}")

        # Force-finalize only after STALE_FORCE_FINALIZE_MINUTES (4 hours)
        if staleness_minutes < STALE_FORCE_FINALIZE_MINUTES:
            updated.append(run)
            continue

        # Genuinely dead run — auto-finalize with whatever trades exist
        logger.info(
            f"Auto-finalizing stale backtest run {run_id} "
            f"(no progress for {staleness_minutes:.0f} min)"
        )
        try:
            trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=10000)
            trades = []
            for td in trade_dicts:
                try:
                    trades.append(BacktestTrade(**td))
                except Exception:
                    pass

            starting_capital = 10_000.0
            config = run.get("config")
            if isinstance(config, dict):
                starting_capital = config.get("starting_capital", 10_000.0)

            metrics = calculate_metrics(trades, starting_capital=starting_capital)
            metrics["total_days"] = int(progress.get("days_completed", 0))
            metrics["total_batches"] = int(run.get("total_batches", 0))
            metrics["auto_finalized"] = True
            metrics["batches_completed"] = int(run.get("batches_completed", 0))

            from app.backtest.coordinator import mark_run_completed

            await mark_run_completed(run_id, summary=metrics)
            run["status"] = "COMPLETED"
            run["summary"] = metrics
            logger.info(
                f"Auto-finalized run {run_id}: {len(trades)} trades, "
                f"win_rate={metrics.get('win_rate', 0):.1f}%"
            )
        except Exception as e:
            logger.error(f"Failed to auto-finalize run {run_id}: {e}")

        updated.append(run)

    return updated


@router.get("/runs")
async def list_backtest_runs(
    status: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List backtest runs with optional status filter.

    Args:
        status: Filter by status (PENDING, RUNNING, COMPLETED, FAILED)
        limit: Maximum results
    """
    try:
        runs = await BacktestRunTable.list_runs(status=status, limit=limit)
        runs = await _finalize_stale_runs(runs)
        return {"runs": runs, "count": len(runs)}
    except Exception as e:
        logger.error(f"Error listing backtest runs: {e}")
        return {"runs": [], "count": 0}


@router.get("/runs/{run_id}")
async def get_backtest_run(run_id: str) -> dict[str, Any]:
    """Get a specific backtest run by ID."""
    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")
    return run


@router.delete("/runs/{run_id}")
async def delete_backtest_run(run_id: str) -> dict[str, Any]:
    """Delete a backtest run and all associated trades.

    If the run is RUNNING, it will be marked as FAILED first.
    """
    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    try:
        # If active, mark as failed first
        if run.get("status") in ACTIVE_RUN_STATUSES:
            await BacktestRunTable.update_status(run_id, "FAILED", error="Cancelled by user")

        # Delete associated trades
        trades_deleted = await BacktestTradeTable.delete_by_run(run_id)

        # Clean up pending trades (Phase 1 → Phase 2 handoff table)
        try:
            from app.db.backtest_pending_table import BacktestPendingTradeTable
            await BacktestPendingTradeTable.delete_by_run(run_id)
        except Exception:
            pass  # Non-blocking: table may not have data

        # Delete the run itself
        await BacktestRunTable.delete(run_id)

        return {
            "status": "deleted",
            "run_id": run_id,
            "trades_deleted": trades_deleted,
        }
    except Exception as e:
        logger.error(f"Failed to delete backtest run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/trades")
async def list_backtest_trades(
    run_id: str,
    scanner: Optional[str] = None,
    verdict: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List trades for a backtest run.

    Args:
        run_id: Backtest run ID
        scanner: Filter by scanner type
        verdict: Filter by verdict
        limit: Maximum results
    """
    # Verify run exists
    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    try:
        trades = await BacktestTradeTable.list_by_run(run_id, limit=limit)

        # Apply client-side filters if needed
        if scanner:
            trades = [t for t in trades if t.get("scanner_type") == scanner]
        if verdict:
            trades = [t for t in trades if t.get("verdict") == verdict]

        return {
            "run_id": run_id,
            "trades": trades,
            "count": len(trades),
            "total_in_run": run.get("progress", {}).get("trades_found", 0),
        }
    except Exception as e:
        logger.error(f"Error listing trades for run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Results Endpoints (Phase 3)
# ============================================================================


@router.get("/runs/{run_id}/summary")
async def get_run_summary(run_id: str) -> dict[str, Any]:
    """Get computed metrics summary for a completed backtest run.

    Returns the summary dict stored on the BacktestRun record.
    If the run is still running or has no summary, returns partial info.
    """
    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    summary = run.get("summary")
    if summary:
        return {
            "run_id": run_id,
            "status": run.get("status"),
            "summary": summary,
        }

    # If no summary yet, compute from trades on the fly
    if run.get("status") == "COMPLETED":
        try:
            from app.backtest.metrics import calculate_metrics
            from app.core.schemas import BacktestTrade

            trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=10000)
            trades = [BacktestTrade(**t) for t in trade_dicts]

            config = run.get("config", {})
            starting_capital = config.get("starting_capital", 10_000.0)
            summary = calculate_metrics(trades, starting_capital=starting_capital)

            # Persist the computed summary for future requests
            await BacktestRunTable.update_progress(run_id, summary=summary)

            return {
                "run_id": run_id,
                "status": "COMPLETED",
                "summary": summary,
            }
        except Exception as e:
            logger.error(f"Error computing summary for {run_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return {
        "run_id": run_id,
        "status": run.get("status"),
        "summary": None,
        "message": f"Run is {run.get('status')} — summary not yet available",
    }


@router.get("/runs/{run_id}/equity-curve")
async def get_equity_curve(run_id: str) -> dict[str, Any]:
    """Get the daily equity curve for a completed backtest run."""
    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    if run.get("status") != "COMPLETED":
        return {
            "run_id": run_id,
            "status": run.get("status"),
            "curve": [],
            "message": f"Run is {run.get('status')} — equity curve not yet available",
        }

    try:
        from app.backtest.equity_curve import generate_equity_curve
        from app.core.schemas import BacktestTrade

        trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=10000)
        trades = [BacktestTrade(**t) for t in trade_dicts]

        config = run.get("config", {})
        starting_capital = config.get("starting_capital", 10_000.0)
        start_date = config.get("start_date")
        end_date = config.get("end_date")

        curve = generate_equity_curve(
            trades,
            starting_capital=starting_capital,
            start_date=start_date,
            end_date=end_date,
        )

        return {
            "run_id": run_id,
            "starting_capital": starting_capital,
            "curve": curve,
            "data_points": len(curve),
        }
    except Exception as e:
        logger.error(f"Error generating equity curve for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/monthly-pnl")
async def get_monthly_pnl(run_id: str) -> dict[str, Any]:
    """Get monthly P&L breakdown for a completed backtest run."""
    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    if run.get("status") != "COMPLETED":
        return {
            "run_id": run_id,
            "status": run.get("status"),
            "months": [],
        }

    try:
        from app.backtest.equity_curve import generate_monthly_pnl
        from app.core.schemas import BacktestTrade

        trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=10000)
        trades = [BacktestTrade(**t) for t in trade_dicts]

        months = generate_monthly_pnl(trades)

        return {
            "run_id": run_id,
            "months": months,
            "total_months": len(months),
        }
    except Exception as e:
        logger.error(f"Error generating monthly P&L for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/segments/{segment_type}")
async def get_segments(
    run_id: str,
    segment_type: str,
) -> dict[str, Any]:
    """Get segmented analysis for a completed backtest run.

    Args:
        run_id: Backtest run ID
        segment_type: One of: scanner, verdict, score_bucket, regime,
                      month, holding_period, exit_reason
    """
    from app.backtest.segments import SEGMENT_TYPES

    if segment_type not in SEGMENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid segment type '{segment_type}'. "
                   f"Must be one of: {SEGMENT_TYPES}",
        )

    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    if run.get("status") != "COMPLETED":
        return {
            "run_id": run_id,
            "status": run.get("status"),
            "segment_type": segment_type,
            "segments": [],
        }

    try:
        from app.backtest.segments import analyze_segment
        from app.core.schemas import BacktestTrade

        trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=10000)
        trades = [BacktestTrade(**t) for t in trade_dicts]

        segments = analyze_segment(trades, segment_type)

        return {
            "run_id": run_id,
            "segment_type": segment_type,
            "segments": segments,
            "total_segments": len(segments),
        }
    except Exception as e:
        logger.error(f"Error computing segments for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# AI Advisor Endpoints (Phase 4)
# ============================================================================


@router.get("/runs/{run_id}/readiness")
async def get_readiness_assessment(
    run_id: str,
) -> dict[str, Any]:
    """Get go-live readiness assessment for a completed backtest run.

    Evaluates backtest results against configurable readiness criteria
    (Sharpe, profit factor, drawdown, trade count, monthly/scanner/regime
    profitability) and returns a structured pass/fail verdict.
    """
    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    if run.get("status") != "COMPLETED":
        return {
            "run_id": run_id,
            "status": run.get("status"),
            "readiness": None,
            "message": f"Run is {run.get('status')} — readiness not available",
        }

    try:
        from app.backtest.readiness import assess_readiness
        from app.core.schemas import BacktestTrade

        # Get summary (compute if needed)
        summary = run.get("summary")
        if not summary:
            from app.backtest.metrics import calculate_metrics

            trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=10000)
            trades = [BacktestTrade(**t) for t in trade_dicts]
            config = run.get("config", {})
            summary = calculate_metrics(
                trades, starting_capital=config.get("starting_capital", 10_000.0)
            )
        else:
            trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=10000)
            trades = [BacktestTrade(**t) for t in trade_dicts]

        readiness = assess_readiness(summary, trades)

        return {
            "run_id": run_id,
            "status": "COMPLETED",
            "readiness": readiness,
        }
    except Exception as e:
        logger.error(f"Error computing readiness for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ChatRequest(BaseModel):
    """Request body for AI chat about a backtest run."""
    message: str
    conversation_history: list[dict[str, str]] = Field(default_factory=list)


@router.post("/runs/{run_id}/chat")
async def chat_about_run(
    run_id: str,
    request: ChatRequest,
) -> dict[str, Any]:
    """Interactive AI chat about a completed backtest run.

    Send a question and get an AI-powered analysis response that references
    the actual trade data and metrics from the run.
    """
    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    if run.get("status") != "COMPLETED":
        return {
            "run_id": run_id,
            "response": f"Run is {run.get('status')} — chat is only available for completed runs.",
            "error": "run_not_completed",
        }

    try:
        from app.backtest.ai_chat import chat_with_backtest

        s3_bucket = os.environ.get("BACKTEST_S3_BUCKET", "")
        result = await chat_with_backtest(
            run_id=run_id,
            message=request.message,
            conversation_history=request.conversation_history,
            s3_bucket=s3_bucket,
        )
        return {"run_id": run_id, **result}

    except Exception as e:
        logger.error(f"AI chat failed for run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/insights")
async def get_insights(
    run_id: str,
) -> dict[str, Any]:
    """Get all AI-generated insights for a backtest run."""
    from app.db.backtest_tables import BacktestInsightTable

    run = await BacktestRunTable.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    try:
        insights = await BacktestInsightTable.list_by_run(run_id)
        return {
            "run_id": run_id,
            "insights": insights,
            "count": len(insights),
        }
    except Exception as e:
        logger.error(f"Error fetching insights for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
