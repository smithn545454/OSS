"""Backtest API routes.

Phase 1 endpoints (Data Store):
- GET  /data-store/status   — S3 bucket inventory
- POST /data-store/validate — Run integrity checks

Phase 2+ endpoints (Replay Engine, Results, AI Advisor) will be added later.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.db.backtest_tables import BacktestRunTable

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
# Backtest Runs (stub for Phase 2 — list runs is needed by Data Store tab)
# ============================================================================


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
