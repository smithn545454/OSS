"""Publisher Lambda for Unusual Volume Scanner.

Orchestrates scan runs by publishing ticker messages to SNS for fan-out
processing by Worker Lambdas.

Triggered by EventBridge every 15 minutes during market hours.

Responsibilities:
1. Generate unique scan_id
2. Create scan-run record in DynamoDB
3. Fetch all active S&P 500 tickers
4. Publish one SNS message per ticker
5. Track metrics (tickers queued, errors)
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")

# Constants
TTL_DAYS = 7  # Scan run records expire after 7 days

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
sns_client = boto3.client("sns")

sp500_table = dynamodb.Table(f"{TABLE_PREFIX}-sp500-tickers")
scan_runs_table = dynamodb.Table(f"{TABLE_PREFIX}-scan-runs")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point for publisher.

    Args:
        event: EventBridge scheduled event or manual trigger
        context: Lambda context

    Returns:
        Response with scan initiation details
    """
    logger.info("Publisher Lambda invoked")
    logger.info(f"Event: {json.dumps(event)}")

    start_time = time.time()

    try:
        # Generate unique scan ID
        scan_id = _generate_scan_id()
        logger.info(f"Starting scan: {scan_id}")

        # Fetch all active tickers
        tickers = _get_sp500_tickers()
        logger.info(f"Found {len(tickers)} active tickers")

        if not tickers:
            logger.warning("No active tickers found")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "scan_id": scan_id,
                    "message": "No active tickers to scan",
                }),
            }

        # Create scan run record
        _create_scan_run(scan_id, len(tickers))

        # Publish messages to SNS
        success_count, error_count = _publish_ticker_messages(scan_id, tickers)

        # Update scan run with queue counts
        _update_scan_run_queued(scan_id, success_count, error_count)

        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"Publisher completed: {success_count} published, "
            f"{error_count} errors, {duration_ms}ms"
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "scan_id": scan_id,
                "tickers_queued": success_count,
                "errors": error_count,
                "duration_ms": duration_ms,
            }),
        }

    except Exception as e:
        logger.error(f"Publisher failed: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


def _generate_scan_id() -> str:
    """Generate a unique scan identifier.

    Format: YYYYMMDD-HHMM-{uuid4_short}
    Example: 20260205-1430-a1b2c3d4
    """
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y%m%d-%H%M")
    uuid_part = str(uuid.uuid4())[:8]
    return f"{date_part}-{uuid_part}"


def _get_sp500_tickers() -> list[str]:
    """Fetch all active S&P 500 tickers from DynamoDB.

    Returns:
        Sorted list of active ticker symbols
    """
    response = sp500_table.query(
        KeyConditionExpression=Key("PK").eq("TICKER_LIST"),
        FilterExpression="is_active = :active",
        ExpressionAttributeValues={":active": True},
    )

    tickers = [item["ticker"] for item in response.get("Items", [])]
    return sorted(tickers)


def _create_scan_run(scan_id: str, ticker_count: int) -> None:
    """Create initial scan run record.

    Args:
        scan_id: Unique scan identifier
        ticker_count: Number of tickers to be scanned
    """
    now = datetime.now(timezone.utc)
    ttl = int((now + timedelta(days=TTL_DAYS)).timestamp())

    scan_runs_table.put_item(
        Item={
            "PK": f"SCAN#{scan_id}",
            "SK": "METADATA",
            "scan_id": scan_id,
            "started_at": now.isoformat(),
            "status": "IN_PROGRESS",
            "tickers_total": ticker_count,
            "tickers_queued": 0,
            "tickers_processed": 0,
            "tickers_failed": 0,
            "candidates_found": 0,
            "candidates_passed_handoff": 0,
            "ttl": ttl,
        }
    )

    logger.info(f"Created scan run record for {scan_id}")


def _publish_ticker_messages(scan_id: str, tickers: list[str]) -> tuple[int, int]:
    """Publish SNS messages for each ticker.

    Args:
        scan_id: Scan identifier to include in messages
        tickers: List of tickers to publish

    Returns:
        Tuple of (success_count, error_count)
    """
    if not SNS_TOPIC_ARN:
        raise ValueError("SNS_TOPIC_ARN not configured")

    success_count = 0
    error_count = 0

    for ticker in tickers:
        try:
            message = {
                "scan_id": scan_id,
                "ticker": ticker,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=json.dumps(message),
                MessageAttributes={
                    "ticker": {
                        "DataType": "String",
                        "StringValue": ticker,
                    },
                    "scan_id": {
                        "DataType": "String",
                        "StringValue": scan_id,
                    },
                },
            )

            success_count += 1

        except Exception as e:
            logger.error(f"Failed to publish message for {ticker}: {str(e)}")
            error_count += 1

    return success_count, error_count


def _update_scan_run_queued(
    scan_id: str,
    success_count: int,
    error_count: int,
) -> None:
    """Update scan run record with queue counts.

    Args:
        scan_id: Scan identifier
        success_count: Number of successfully queued tickers
        error_count: Number of failed queue operations
    """
    scan_runs_table.update_item(
        Key={
            "PK": f"SCAN#{scan_id}",
            "SK": "METADATA",
        },
        UpdateExpression=(
            "SET tickers_queued = :queued, "
            "tickers_failed = :failed"
        ),
        ExpressionAttributeValues={
            ":queued": success_count,
            ":failed": error_count,
        },
    )


# For local testing
if __name__ == "__main__":
    result = lambda_handler({}, None)
    print(json.dumps(result, indent=2))
