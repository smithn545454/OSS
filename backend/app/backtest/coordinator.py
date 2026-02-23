"""Backtest Coordinator.

Orchestrates a complete backtest run:
1. Creates a BacktestRun record (status=PENDING)
2. Generates the list of trading days in the date range
3. Batches days into worker chunks
4. Fans out Lambda invocations (or runs inline for small runs)
5. Updates run status to RUNNING

For Phase 2, the coordinator runs workers inline (sequentially).
Phase 5 will add Lambda fan-out for parallel execution.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.core.schemas import BacktestRun, BacktestRunConfig
from app.db.backtest_tables import BacktestRunTable

logger = logging.getLogger(__name__)

# Worker batch size — number of trading days per worker invocation
DAYS_PER_BATCH = 5


def generate_trading_days(start_date: date, end_date: date) -> list[date]:
    """Generate list of approximate trading days in range (weekdays only).

    Does not account for holidays — acceptable approximation for backtesting.

    Args:
        start_date: First date (inclusive)
        end_date: Last date (inclusive)

    Returns:
        Sorted list of weekday dates.
    """
    days: list[date] = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # Mon-Fri
            days.append(current)
        current += timedelta(days=1)
    return days


def batch_days(days: list[date], batch_size: int = DAYS_PER_BATCH) -> list[list[date]]:
    """Split trading days into worker batches.

    Args:
        days: List of trading dates
        batch_size: Days per batch

    Returns:
        List of batches (each a list of dates).
    """
    batches: list[list[date]] = []
    for i in range(0, len(days), batch_size):
        batches.append(days[i:i + batch_size])
    return batches


async def create_backtest_run(
    config: BacktestRunConfig,
    persist: bool = True,
) -> BacktestRun:
    """Create a new backtest run record.

    Args:
        config: Run configuration
        persist: Whether to save to DynamoDB

    Returns:
        BacktestRun record with status=PENDING.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Calculate total trading days
    start = date.fromisoformat(config.start_date)
    end = date.fromisoformat(config.end_date)
    trading_days = generate_trading_days(start, end)

    run = BacktestRun(
        run_id=run_id,
        config=config,
        status="PENDING",
        progress={
            "days_completed": 0,
            "days_total": len(trading_days),
            "trades_found": 0,
        },
        created_at=now,
    )

    if persist:
        await BacktestRunTable.put(run.model_dump())

    logger.info(
        f"Created backtest run {run_id}: {config.name}, "
        f"{len(trading_days)} trading days ({config.start_date} to {config.end_date})"
    )

    return run


async def start_backtest_run(
    run: BacktestRun,
    persist: bool = True,
) -> list[list[date]]:
    """Transition run to RUNNING and generate worker batches.

    Args:
        run: The backtest run to start
        persist: Whether to update DynamoDB

    Returns:
        List of day batches for worker processing.
    """
    start = date.fromisoformat(run.config.start_date)
    end = date.fromisoformat(run.config.end_date)
    trading_days = generate_trading_days(start, end)
    batches = batch_days(trading_days)

    run.status = "RUNNING"

    if persist:
        await BacktestRunTable.update_status(run.run_id, "RUNNING")

    logger.info(
        f"Started backtest run {run.run_id}: "
        f"{len(trading_days)} days in {len(batches)} batches"
    )

    return batches


async def mark_run_completed(
    run_id: str,
    summary: Optional[dict] = None,
    persist: bool = True,
) -> None:
    """Mark a backtest run as completed.

    Args:
        run_id: Run ID
        summary: Optional summary metrics
        persist: Whether to update DynamoDB
    """
    if persist:
        await BacktestRunTable.update_status(
            run_id, "COMPLETED",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        if summary:
            await BacktestRunTable.update_progress(
                run_id, summary=summary,
            )

    logger.info(f"Backtest run {run_id} completed")


async def mark_run_failed(
    run_id: str,
    error: str,
    persist: bool = True,
) -> None:
    """Mark a backtest run as failed.

    Args:
        run_id: Run ID
        error: Error message
        persist: Whether to update DynamoDB
    """
    if persist:
        await BacktestRunTable.update_status(run_id, "FAILED", error=error)

    logger.error(f"Backtest run {run_id} failed: {error}")
