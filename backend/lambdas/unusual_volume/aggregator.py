"""Aggregator Lambda for Unusual Volume Scanner.

Updates scan-run metrics after all workers have completed processing.
Triggered by EventBridge 2 minutes after each scan starts.

Responsibilities:
- Query scan-runs table to find in-progress scans
- Count candidates found per scan
- Update scan-run status to COMPLETED
- Calculate and store duration metrics
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point for aggregator.

    Args:
        event: EventBridge scheduled event
        context: Lambda context

    Returns:
        Response with aggregation results
    """
    logger.info("Aggregator Lambda invoked")
    logger.info(f"Event: {json.dumps(event)}")

    start_time = time.time()

    try:
        # Find in-progress scans to aggregate
        in_progress_scans = _find_in_progress_scans()

        if not in_progress_scans:
            logger.info("No in-progress scans to aggregate")
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "No scans to aggregate"}),
            }

        logger.info(f"Found {len(in_progress_scans)} in-progress scan(s)")

        results = []
        for scan_run in in_progress_scans:
            scan_id = scan_run["scan_id"]
            logger.info(f"Aggregating scan: {scan_id}")

            result = _aggregate_scan(scan_run)
            results.append(result)

        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(f"Aggregation completed in {duration_ms}ms")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Aggregation complete",
                "scans_aggregated": len(results),
                "results": results,
                "duration_ms": duration_ms,
            }),
        }

    except Exception as e:
        logger.error(f"Aggregation failed: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


def _find_in_progress_scans() -> list[dict]:
    """Find all scans that are still in progress.

    Scans older than 10 minutes are considered timed out and will be
    marked as FAILED.
    """
    # Query for in-progress scans
    # We scan the table since there's no GSI on status
    # This is acceptable as scan-runs is small (max ~100 entries/day)
    response = scan_runs_table.scan(
        FilterExpression="begins_with(PK, :pk_prefix) AND #status = :status",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":pk_prefix": "SCAN#",
            ":status": "IN_PROGRESS",
        },
    )

    scans = response.get("Items", [])

    # Filter out scans that are too old (> 10 minutes)
    cutoff_time = datetime.now(timezone.utc).timestamp() - (10 * 60)
    valid_scans = []

    for scan in scans:
        started_at = scan.get("started_at", "")
        try:
            scan_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            if scan_time.timestamp() >= cutoff_time:
                valid_scans.append(scan)
            else:
                # Mark old scan as FAILED
                _mark_scan_failed(scan["scan_id"], "Timeout: Scan exceeded 10 minutes")
        except (ValueError, KeyError):
            logger.warning(f"Invalid scan record: {scan}")

    return valid_scans


def _aggregate_scan(scan_run: dict) -> dict:
    """Aggregate metrics for a single scan run.

    Args:
        scan_run: Scan run record from DynamoDB

    Returns:
        Aggregation result summary
    """
    scan_id = scan_run["scan_id"]
    started_at = scan_run.get("started_at", "")

    # Count candidates found for this scan
    candidates_response = candidates_table.query(
        KeyConditionExpression=Key("PK").eq(f"SCAN#{scan_id}"),
        Select="COUNT",
    )
    candidates_found = candidates_response.get("Count", 0)

    # Count candidates that passed handoff (status = PROCESSED)
    processed_response = candidates_table.query(
        KeyConditionExpression=Key("PK").eq(f"SCAN#{scan_id}"),
        FilterExpression="#hs = :processed",
        ExpressionAttributeNames={"#hs": "handoff_status"},
        ExpressionAttributeValues={":processed": "PROCESSED"},
        Select="COUNT",
    )
    candidates_passed = processed_response.get("Count", 0)

    # Calculate duration
    now = datetime.now(timezone.utc)
    try:
        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        duration_ms = int((now - start_dt).total_seconds() * 1000)
    except (ValueError, TypeError):
        duration_ms = 0

    # Update scan run record
    scan_runs_table.update_item(
        Key={
            "PK": f"SCAN#{scan_id}",
            "SK": "METADATA",
        },
        UpdateExpression=(
            "SET #status = :completed, "
            "completed_at = :completed_at, "
            "candidates_found = :candidates_found, "
            "candidates_passed_handoff = :candidates_passed, "
            "duration_ms = :duration_ms"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":completed": "COMPLETED",
            ":completed_at": now.isoformat(),
            ":candidates_found": candidates_found,
            ":candidates_passed": candidates_passed,
            ":duration_ms": duration_ms,
        },
    )

    logger.info(
        f"Scan {scan_id} completed: "
        f"{candidates_found} candidates found, "
        f"{candidates_passed} passed handoff, "
        f"{duration_ms}ms duration"
    )

    return {
        "scan_id": scan_id,
        "candidates_found": candidates_found,
        "candidates_passed_handoff": candidates_passed,
        "duration_ms": duration_ms,
        "status": "COMPLETED",
    }


def _mark_scan_failed(scan_id: str, error_message: str) -> None:
    """Mark a scan as failed.

    Args:
        scan_id: Scan identifier
        error_message: Reason for failure
    """
    now = datetime.now(timezone.utc)

    scan_runs_table.update_item(
        Key={
            "PK": f"SCAN#{scan_id}",
            "SK": "METADATA",
        },
        UpdateExpression=(
            "SET #status = :failed, "
            "completed_at = :completed_at, "
            "error_message = :error"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":failed": "FAILED",
            ":completed_at": now.isoformat(),
            ":error": error_message,
        },
    )

    logger.warning(f"Scan {scan_id} marked as FAILED: {error_message}")


# For local testing
if __name__ == "__main__":
    result = lambda_handler({}, None)
    print(json.dumps(result, indent=2))
