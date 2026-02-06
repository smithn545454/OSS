"""Nightly Stats Lambda for Unusual Volume Scanner.

Pre-computes volume statistics for all S&P 500 tickers at 8 PM ET daily.
Stores both contract-level and bucket-level statistics in volume-stats table.

Algorithm:
1. For each ticker in sp500-tickers table:
   a. Fetch full options chain from Polygon
   b. For each contract with >= 5 days of history in oi-history:
      - Compute 20-day average volume
      - Store contract-level stats
   c. For contracts with < 5 days history:
      - Group by (option_type, moneyness_bucket, dte_bucket)
      - Compute bucket-level averages
      - Store bucket-level stats

Statistics computed:
- avg_volume_20d: 20-day simple moving average of daily volume
- avg_volume_10d: 10-day SMA (more responsive)
- volume_stddev_20d: Standard deviation of 20-day volume
- last_volume: Most recent volume
- last_oi: Most recent open interest
- prior_oi: Open interest from previous day
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import mean, stdev
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

from utils.buckets import build_bucket_key, get_dte_bucket, get_moneyness_bucket
from utils.occ_parser import parse_occ_symbol

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss")
POLYGON_SECRET_ARN = os.environ.get("POLYGON_SECRET_ARN", "")

# Constants
MIN_HISTORY_DAYS_FOR_CONTRACT = 5
LOOKBACK_DAYS = 20
TTL_DAYS = 7  # Stats expire after 7 days

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
secrets_client = boto3.client("secretsmanager")

sp500_table = dynamodb.Table(f"{TABLE_PREFIX}-sp500-tickers")
volume_stats_table = dynamodb.Table(f"{TABLE_PREFIX}-volume-stats")
oi_history_table = dynamodb.Table(f"{TABLE_PREFIX}-oi-history")

# Polygon client (initialized lazily)
_polygon_api_key: Optional[str] = None


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
        # Get all S&P 500 tickers
        tickers = _get_sp500_tickers()
        logger.info(f"Processing {len(tickers)} tickers")

        as_of_date = date.today().isoformat()
        ttl = int((datetime.now(timezone.utc) + timedelta(days=TTL_DAYS)).timestamp())

        total_contract_stats = 0
        total_bucket_stats = 0
        errors = []

        for ticker in tickers:
            try:
                contract_count, bucket_count = _process_ticker(ticker, as_of_date, ttl)
                total_contract_stats += contract_count
                total_bucket_stats += bucket_count
            except Exception as e:
                logger.error(f"Error processing {ticker}: {str(e)}")
                errors.append({"ticker": ticker, "error": str(e)})

        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"Nightly stats completed: "
            f"{total_contract_stats} contract stats, "
            f"{total_bucket_stats} bucket stats, "
            f"{len(errors)} errors, "
            f"{duration_ms}ms"
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "tickers_processed": len(tickers),
                "contract_stats_written": total_contract_stats,
                "bucket_stats_written": total_bucket_stats,
                "errors": errors[:10],  # Limit error reporting
                "duration_ms": duration_ms,
            }),
        }

    except Exception as e:
        logger.error(f"Nightly stats failed: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


def _get_sp500_tickers() -> list[str]:
    """Fetch all active S&P 500 tickers."""
    response = sp500_table.query(
        KeyConditionExpression=Key("PK").eq("TICKER_LIST"),
        FilterExpression="is_active = :active",
        ExpressionAttributeValues={":active": True},
    )

    tickers = [item["ticker"] for item in response.get("Items", [])]
    return sorted(tickers)


def _process_ticker(ticker: str, as_of_date: str, ttl: int) -> tuple[int, int]:
    """Process a single ticker and compute volume stats.

    Args:
        ticker: Underlying ticker symbol
        as_of_date: Date for stats (YYYY-MM-DD)
        ttl: TTL timestamp for DynamoDB

    Returns:
        Tuple of (contract_stats_count, bucket_stats_count)
    """
    # Fetch contract history from oi-history table
    contract_histories = _fetch_contract_histories(ticker)

    if not contract_histories:
        logger.debug(f"No history found for {ticker}")
        return 0, 0

    # Separate contracts by history depth
    sufficient_history = {}  # contract_ticker -> [history records]
    insufficient_history = {}

    for contract_ticker, history in contract_histories.items():
        if len(history) >= MIN_HISTORY_DAYS_FOR_CONTRACT:
            sufficient_history[contract_ticker] = history
        else:
            insufficient_history[contract_ticker] = history

    # Write contract-level stats for contracts with sufficient history
    contract_count = 0
    for contract_ticker, history in sufficient_history.items():
        _write_contract_stats(contract_ticker, ticker, history, as_of_date, ttl)
        contract_count += 1

    # Compute and write bucket-level stats
    bucket_count = _compute_bucket_stats(
        ticker, insufficient_history, as_of_date, ttl
    )

    logger.debug(
        f"{ticker}: {contract_count} contract stats, {bucket_count} bucket stats"
    )

    return contract_count, bucket_count


def _fetch_contract_histories(ticker: str) -> dict[str, list[dict]]:
    """Fetch OI/volume history for all contracts of a ticker.

    Returns:
        Dict mapping contract_ticker to list of history records
    """
    # Query oi-history for all contracts of this underlying
    # Using begins_with on PK to get all contracts
    # Format: CONTRACT#{contract_ticker}
    histories: dict[str, list[dict]] = defaultdict(list)

    # Calculate date range
    end_date = date.today()
    start_date = end_date - timedelta(days=LOOKBACK_DAYS + 5)  # Buffer

    # We need to query by underlying, but oi-history is keyed by contract
    # This requires scanning with filter, which is inefficient
    # In production, consider adding a GSI on underlying_ticker
    response = oi_history_table.scan(
        FilterExpression=(
            "contains(option_ticker, :ticker) AND "
            "#date BETWEEN :start_date AND :end_date"
        ),
        ExpressionAttributeNames={"#date": "date"},
        ExpressionAttributeValues={
            ":ticker": ticker,
            ":start_date": start_date.isoformat(),
            ":end_date": end_date.isoformat(),
        },
    )

    for item in response.get("Items", []):
        contract_ticker = item.get("option_ticker", "")
        if contract_ticker:
            histories[contract_ticker].append(item)

    # Sort each contract's history by date
    for contract_ticker in histories:
        histories[contract_ticker].sort(key=lambda x: x.get("date", ""))

    return dict(histories)


def _write_contract_stats(
    contract_ticker: str,
    underlying_ticker: str,
    history: list[dict],
    as_of_date: str,
    ttl: int,
) -> None:
    """Write contract-level volume statistics."""
    # Extract volume series
    volumes = [
        int(h.get("volume", 0))
        for h in history[-LOOKBACK_DAYS:]
        if h.get("volume") is not None
    ]

    if len(volumes) < MIN_HISTORY_DAYS_FOR_CONTRACT:
        return

    # Calculate stats
    avg_volume_20d = mean(volumes) if volumes else 0
    avg_volume_10d = mean(volumes[-10:]) if len(volumes) >= 10 else avg_volume_20d
    volume_stddev_20d = stdev(volumes) if len(volumes) >= 2 else 0

    # Get latest values
    latest = history[-1] if history else {}
    prior = history[-2] if len(history) >= 2 else {}

    last_volume = int(latest.get("volume", 0))
    last_oi = int(latest.get("open_interest", 0))
    prior_oi = int(prior.get("open_interest", 0))

    # Parse contract for bucket info
    parsed = parse_occ_symbol(contract_ticker)
    if not parsed:
        return

    moneyness_bucket = "ATM"  # Would need underlying price to calculate
    dte_bucket = get_dte_bucket(parsed.dte)

    # Write to DynamoDB
    volume_stats_table.put_item(
        Item={
            "PK": f"CONTRACT#{contract_ticker}",
            "SK": f"STATS#{as_of_date}",
            "option_ticker": contract_ticker,
            "underlying_ticker": underlying_ticker,
            "as_of_date": as_of_date,
            "volume_history_days": len(volumes),
            "avg_volume_20d": Decimal(str(round(avg_volume_20d, 2))),
            "avg_volume_10d": Decimal(str(round(avg_volume_10d, 2))),
            "volume_stddev_20d": Decimal(str(round(volume_stddev_20d, 2))),
            "last_volume": last_volume,
            "last_oi": last_oi,
            "prior_oi": prior_oi,
            "moneyness_bucket": moneyness_bucket,
            "dte_bucket": dte_bucket,
            "ttl": ttl,
        }
    )


def _compute_bucket_stats(
    ticker: str,
    insufficient_history: dict[str, list[dict]],
    as_of_date: str,
    ttl: int,
) -> int:
    """Compute bucket-level statistics for contracts with sparse history.

    Args:
        ticker: Underlying ticker
        insufficient_history: Dict of contract_ticker -> history records
        as_of_date: Date for stats
        ttl: TTL timestamp

    Returns:
        Number of bucket stats written
    """
    # Group contracts by bucket
    # bucket_key -> [volumes]
    bucket_volumes: dict[str, list[float]] = defaultdict(list)
    bucket_contracts: dict[str, int] = defaultdict(int)

    for contract_ticker, history in insufficient_history.items():
        parsed = parse_occ_symbol(contract_ticker)
        if not parsed:
            continue

        # Get bucket classification
        # Note: For bucket stats, we use a fixed ATM assumption since we
        # don't have real-time underlying prices in the nightly job
        dte_bucket = get_dte_bucket(parsed.dte)

        # Skip LEAPS
        if dte_bucket == "X":
            continue

        # Use option type and DTE bucket (skip moneyness without price)
        bucket_key = f"BUCKET#{ticker}#{parsed.option_type}#ALL#{dte_bucket}"

        # Add volumes from history
        for h in history:
            vol = h.get("volume")
            if vol is not None:
                bucket_volumes[bucket_key].append(float(vol))

        bucket_contracts[bucket_key] += 1

    # Write bucket stats
    count = 0
    for bucket_key, volumes in bucket_volumes.items():
        if len(volumes) < 3:  # Need minimum data
            continue

        avg_volume = mean(volumes)

        volume_stats_table.put_item(
            Item={
                "PK": bucket_key,
                "SK": f"STATS#{as_of_date}",
                "underlying_ticker": ticker,
                "as_of_date": as_of_date,
                "avg_volume_20d": Decimal(str(round(avg_volume, 2))),
                "contract_count": bucket_contracts[bucket_key],
                "ttl": ttl,
            }
        )
        count += 1

    return count


def _get_polygon_api_key() -> str:
    """Fetch Polygon API key from Secrets Manager."""
    global _polygon_api_key

    if _polygon_api_key is None:
        if not POLYGON_SECRET_ARN:
            raise ValueError("POLYGON_SECRET_ARN not configured")

        response = secrets_client.get_secret_value(SecretId=POLYGON_SECRET_ARN)
        _polygon_api_key = response.get("SecretString", "")

    return _polygon_api_key


# For local testing
if __name__ == "__main__":
    result = lambda_handler({}, None)
    print(json.dumps(result, indent=2))
