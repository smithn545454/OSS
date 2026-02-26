"""Nightly Stats Lambda for Unusual Volume Scanner.

Computes aggregate reporting statistics for observability:
- Scan completion rates (from scan-runs table)
- Candidate volume trends (from candidates table)

Note: Contract-level baselines are now computed inline by the worker Lambda
using direct oi-history queries. This Lambda no longer pre-computes volume
stats for individual contracts or buckets.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss")

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")

scan_runs_table = dynamodb.Table(f"{TABLE_PREFIX}-scan-runs")
candidates_table = dynamodb.Table(f"{TABLE_PREFIX}-unusual-volume-candidates")
volume_stats_table = dynamodb.Table(f"{TABLE_PREFIX}-volume-stats")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point for nightly stats computation.

    Args:
        event: EventBridge scheduled event
        context: Lambda context

    Returns:
        Response with computation results
    """
    logger.info("Nightly Stats Lambda invoked")
    start_time = time.time()

    try:
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        # Compute aggregate scan metrics for today
        scan_metrics = _compute_scan_metrics(today)
        candidate_metrics = _compute_candidate_metrics(today)

        # Write daily summary to volume-stats table for observability
        _write_daily_summary(today, scan_metrics, candidate_metrics)

        duration_ms = int((time.time() - start_time) * 1000)

        result = {
            "date": today,
            "scan_metrics": scan_metrics,
            "candidate_metrics": candidate_metrics,
            "duration_ms": duration_ms,
        }

        logger.info(f"Nightly stats completed: {json.dumps(result)}")

        return {
            "statusCode": 200,
            "body": json.dumps(result),
        }

    except Exception as e:
        logger.error(f"Nightly stats failed: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


def _compute_scan_metrics(as_of_date: str) -> dict[str, Any]:
    """Compute scan run metrics for today.

    Returns:
        Dict with scan counts and completion rates
    """
    try:
        response = scan_runs_table.query(
            KeyConditionExpression=(
                Key("PK").eq("SCAN_RUNS") &
                Key("SK").begins_with(f"DATE#{as_of_date}")
            ),
        )
        items = response.get("Items", [])

        total_scans = len(items)
        completed = sum(1 for i in items if i.get("status") == "COMPLETED")
        failed = sum(1 for i in items if i.get("status") == "FAILED")
        total_tickers = sum(int(i.get("tickers_processed", 0)) for i in items)
        total_candidates = sum(int(i.get("candidates_found", 0)) for i in items)

        return {
            "total_scans": total_scans,
            "completed": completed,
            "failed": failed,
            "total_tickers_processed": total_tickers,
            "total_candidates_found": total_candidates,
        }
    except Exception as e:
        logger.error(f"Error computing scan metrics: {e}")
        return {"error": str(e)}


def _compute_candidate_metrics(as_of_date: str) -> dict[str, Any]:
    """Compute candidate metrics for today.

    Returns:
        Dict with candidate counts by handoff status
    """
    try:
        # Query candidates by handoff status
        statuses = {}
        for status in ["PENDING", "PROCESSED", "FILTERED"]:
            try:
                response = candidates_table.query(
                    IndexName="handoff-status-index",
                    KeyConditionExpression=(
                        Key("handoff_status").eq(status) &
                        Key("created_at").begins_with(as_of_date)
                    ),
                    Select="COUNT",
                )
                statuses[status.lower()] = response.get("Count", 0)
            except Exception:
                statuses[status.lower()] = 0

        return statuses
    except Exception as e:
        logger.error(f"Error computing candidate metrics: {e}")
        return {"error": str(e)}


def _write_daily_summary(
    as_of_date: str,
    scan_metrics: dict,
    candidate_metrics: dict,
) -> None:
    """Write daily summary to volume-stats table for observability."""
    try:
        ttl = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

        volume_stats_table.put_item(
            Item={
                "PK": "DAILY_SUMMARY",
                "SK": f"DATE#{as_of_date}",
                "as_of_date": as_of_date,
                "scan_metrics": scan_metrics,
                "candidate_metrics": candidate_metrics,
                "computed_at": datetime.now(timezone.utc).isoformat(),
                "ttl": ttl,
            }
        )
    except Exception as e:
        logger.error(f"Error writing daily summary: {e}")


# For local testing
if __name__ == "__main__":
    result = lambda_handler({}, None)
    print(json.dumps(result, indent=2))
