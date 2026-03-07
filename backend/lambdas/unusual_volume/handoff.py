"""Handoff Lambda for Unusual Volume Scanner.

Processes unusual volume candidates from DynamoDB Streams and:
1. Applies Stage 2 filters (underlying quality checks)
2. Creates Evaluation records for candidates that pass
3. Updates candidate status in unusual-volume-candidates table

Triggered by DynamoDB Streams on the unusual-volume-candidates table.

Stage 2 Filters Applied:
- PRICE_TOO_LOW: Underlying price < $5
- LOW_DOLLAR_VOLUME: 20-day avg dollar volume < $20M
- MISSING_DATA: > 2 missing price bars in last 30 days

Filter codes are stored with filtered candidates for observability.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss")
MIN_UNDERLYING_PRICE = float(os.environ.get("MIN_UNDERLYING_PRICE", "5.0"))
MIN_AVG_DOLLAR_VOLUME = float(os.environ.get("MIN_AVG_DOLLAR_VOLUME", "20000000"))
MAX_MISSING_BARS = int(os.environ.get("MAX_MISSING_BARS", "2"))

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")

candidates_table = dynamodb.Table(f"{TABLE_PREFIX}-unusual-volume-candidates")
underlying_stats_table = dynamodb.Table(f"{TABLE_PREFIX}-underlying-stats")
evaluations_table = dynamodb.Table(f"{TABLE_PREFIX}-evaluations")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point for handoff processing.

    Args:
        event: DynamoDB Streams event
        context: Lambda context

    Returns:
        Response with processing results
    """
    logger.info("Handoff Lambda invoked")
    start_time = time.time()

    processed = 0
    passed = 0
    filtered = 0
    errors = 0

    # Process DynamoDB Stream records
    for record in event.get("Records", []):
        try:
            # Only process INSERT events (new candidates)
            if record.get("eventName") != "INSERT":
                continue

            # Extract new image
            new_image = record.get("dynamodb", {}).get("NewImage", {})
            if not new_image:
                continue

            # Convert DynamoDB format to Python dict
            candidate = _deserialize_dynamodb_item(new_image)

            # Skip if already processed
            if candidate.get("handoff_status") != "PENDING":
                continue

            result = _process_candidate(candidate)
            processed += 1

            if result["status"] == "PROCESSED":
                passed += 1
            else:
                filtered += 1

        except Exception as e:
            logger.error(f"Error processing record: {str(e)}", exc_info=True)
            errors += 1

    duration_ms = int((time.time() - start_time) * 1000)
    logger.info(
        f"Handoff completed: {processed} processed, "
        f"{passed} passed, {filtered} filtered, "
        f"{errors} errors, {duration_ms}ms"
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "records_processed": processed,
            "passed": passed,
            "filtered": filtered,
            "errors": errors,
            "duration_ms": duration_ms,
        }),
    }


def _deserialize_dynamodb_item(item: dict) -> dict:
    """Convert DynamoDB attribute format to Python dict.

    Args:
        item: DynamoDB item in wire format ({"S": "value"}, {"N": "123"}, etc.)

    Returns:
        Plain Python dict
    """
    result = {}
    for key, value in item.items():
        if "S" in value:
            result[key] = value["S"]
        elif "N" in value:
            result[key] = float(value["N"])
        elif "BOOL" in value:
            result[key] = value["BOOL"]
        elif "L" in value:
            result[key] = [_deserialize_value(v) for v in value["L"]]
        elif "M" in value:
            result[key] = _deserialize_dynamodb_item(value["M"])
        elif "NULL" in value:
            result[key] = None
    return result


def _deserialize_value(value: dict) -> Any:
    """Deserialize a single DynamoDB value."""
    if "S" in value:
        return value["S"]
    elif "N" in value:
        return float(value["N"])
    elif "BOOL" in value:
        return value["BOOL"]
    elif "NULL" in value:
        return None
    return str(value)


def _process_candidate(candidate: dict) -> dict:
    """Process a single unusual volume candidate.

    Args:
        candidate: Candidate record from DynamoDB

    Returns:
        Processing result with status and details
    """
    scan_id = candidate.get("scan_id", "")
    option_ticker = candidate.get("option_ticker", "")
    underlying_ticker = candidate.get("underlying_ticker", "")

    logger.info(f"Processing candidate: {option_ticker} (scan: {scan_id})")

    # Apply Stage 2 filters
    filter_reasons = _apply_stage2_filters(candidate)

    if filter_reasons:
        # Candidate filtered out
        _update_candidate_status(
            scan_id=scan_id,
            option_ticker=option_ticker,
            status="FILTERED",
            filter_reason=",".join(filter_reasons),
        )

        logger.info(f"Candidate {option_ticker} filtered: {filter_reasons}")

        return {
            "status": "FILTERED",
            "option_ticker": option_ticker,
            "filter_reasons": filter_reasons,
        }

    # Apply contract quality validation
    contract_rejections = _validate_contract_quality(candidate)
    if contract_rejections:
        _update_candidate_status(
            scan_id=scan_id,
            option_ticker=option_ticker,
            status="FILTERED",
            filter_reason=",".join(contract_rejections),
        )
        logger.info(f"Candidate {option_ticker} rejected (contract quality): {contract_rejections}")
        return {
            "status": "FILTERED",
            "option_ticker": option_ticker,
            "filter_reasons": contract_rejections,
        }

    # Candidate passed - create evaluation
    evaluation_id = _create_evaluation(candidate)

    # Update candidate status
    _update_candidate_status(
        scan_id=scan_id,
        option_ticker=option_ticker,
        status="PROCESSED",
        evaluation_id=evaluation_id,
    )

    logger.info(f"Candidate {option_ticker} passed -> evaluation {evaluation_id}")

    return {
        "status": "PROCESSED",
        "option_ticker": option_ticker,
        "evaluation_id": evaluation_id,
    }


def _apply_stage2_filters(candidate: dict) -> list[str]:
    """Apply Stage 2 underlying quality filters.

    Args:
        candidate: Candidate record

    Returns:
        List of filter reason codes (empty if passed)
    """
    filter_reasons = []
    underlying_ticker = candidate.get("underlying_ticker", "")
    underlying_price = float(candidate.get("underlying_price", 0))

    # Filter 1: Minimum underlying price
    if underlying_price < MIN_UNDERLYING_PRICE:
        filter_reasons.append("PRICE_TOO_LOW")

    # Filter 2: Dollar volume (from underlying-stats table)
    underlying_stats = _get_underlying_stats(underlying_ticker)

    if underlying_stats:
        avg_dollar_volume = underlying_stats.get("avg_dollar_volume_20d", 0)
        if avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
            filter_reasons.append("LOW_DOLLAR_VOLUME")

        missing_bars = underlying_stats.get("missing_bars_30d", 0)
        if missing_bars > MAX_MISSING_BARS:
            filter_reasons.append("MISSING_DATA")
    else:
        # No stats available - could be missing data
        logger.warning(f"No underlying stats for {underlying_ticker}")
        # Don't filter on missing stats, let it pass

    return filter_reasons


def _validate_contract_quality(candidate: dict) -> list[str]:
    """Validate contract quality before creating an evaluation.

    Rejects contracts with insufficient DTE, deep OTM delta, or penny premiums
    that are unlikely to be tradeable at recorded prices.

    Args:
        candidate: Candidate record

    Returns:
        List of rejection reason codes (empty if passed)
    """
    rejections = []

    dte = int(candidate.get("dte", 0))
    delta = float(candidate.get("delta", 0))
    ask = float(candidate.get("ask", 0))

    # Minimum DTE — matches policy DTE_RANGE gate (7 days minimum)
    if dte < 7:
        rejections.append("DTE_TOO_SHORT")

    # Minimum delta — deep OTM options have low probability of profit
    if 0 < abs(delta) < 0.15:
        rejections.append("DELTA_TOO_LOW")

    # Minimum premium — penny options have wide spreads
    if 0 < ask < 0.10:
        rejections.append("PREMIUM_TOO_LOW")

    # Missing data — if key fields are zero/missing, reject
    if ask <= 0 and float(candidate.get("mid", 0)) <= 0:
        rejections.append("MISSING_PRICE_DATA")

    return rejections


def _get_underlying_stats(ticker: str) -> Optional[dict]:
    """Fetch underlying stats from DynamoDB.

    Args:
        ticker: Underlying ticker symbol

    Returns:
        Stats record or None if not found
    """
    try:
        response = underlying_stats_table.query(
            KeyConditionExpression=(
                Key("PK").eq(f"TICKER#{ticker}") &
                Key("SK").begins_with("STATS#")
            ),
            ScanIndexForward=False,  # Get most recent first
            Limit=1,
        )

        items = response.get("Items", [])
        if items:
            return items[0]

    except Exception as e:
        logger.error(f"Error fetching underlying stats for {ticker}: {str(e)}")

    return None


def _create_evaluation(candidate: dict) -> str:
    """Create an Evaluation record from a candidate.

    Args:
        candidate: Unusual volume candidate record

    Returns:
        Generated evaluation ID
    """
    evaluation_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Extract candidate fields
    underlying_ticker = candidate.get("underlying_ticker", "")
    option_ticker = candidate.get("option_ticker", "")
    option_type = candidate.get("option_type", "CALL")
    expiration_date = candidate.get("expiration_date", "")
    dte = int(candidate.get("dte", 0))
    strike = float(candidate.get("strike", 0))
    underlying_price = float(candidate.get("underlying_price", 0))

    # Calculate moneyness
    moneyness_pct = ((strike - underlying_price) / underlying_price * 100) if underlying_price > 0 else 0

    # Quote data
    bid = float(candidate.get("bid", 0))
    ask = float(candidate.get("ask", 0))
    mid = float(candidate.get("mid", 0)) or (bid + ask) / 2
    spread_abs = ask - bid
    spread_pct = float(candidate.get("spread_pct", 0))

    # Greeks
    iv = float(candidate.get("iv", 0))
    delta = float(candidate.get("delta", 0))
    gamma = float(candidate.get("gamma", 0))
    theta = float(candidate.get("theta", 0))
    vega = float(candidate.get("vega", 0))

    # Volume/OI
    volume = int(candidate.get("today_volume", 0))
    oi = int(candidate.get("today_oi", 0))

    # Scanner metrics
    scanner_metrics = {
        "volume_ratio": Decimal(str(round(float(candidate.get("volume_ratio", 0)), 4))),
        "avg_volume_20d": Decimal(str(round(float(candidate.get("avg_volume_20d", 0)), 2))),
        "oi_change_pct": Decimal(str(round(float(candidate.get("oi_change_pct", 0)), 2))),
        "prior_oi": Decimal(str(int(float(candidate.get("prior_oi", 0))))),
        "volume_source": candidate.get("volume_source", ""),
        "priority_score": Decimal(str(round(float(candidate.get("priority_score", 0)), 2))),
        "scan_id": candidate.get("scan_id", ""),
    }

    # Determine DTE bucket
    if dte <= 7:
        dte_bucket = "A"
    elif dte <= 21:
        dte_bucket = "B"
    elif dte <= 45:
        dte_bucket = "C"
    else:
        dte_bucket = "D"

    # Build evaluation item
    eval_item = {
        "PK": f"EVAL#{underlying_ticker}",
        "SK": f"{now.isoformat()}#{evaluation_id}",
        "evaluation_id": evaluation_id,
        "opportunity_id": candidate.get("scan_id", ""),  # Link to scan
        "underlying_ticker": underlying_ticker,
        "option_ticker": option_ticker,
        "option_type": option_type,
        "expiration_date": expiration_date,
        "dte": dte,
        "strike": Decimal(str(strike)),
        "underlying_price": Decimal(str(underlying_price)),
        "moneyness_pct": Decimal(str(round(moneyness_pct, 2))),
        "bid": Decimal(str(bid)),
        "ask": Decimal(str(ask)),
        "mid": Decimal(str(round(mid, 4))),
        "spread_abs": Decimal(str(round(spread_abs, 4))),
        "spread_pct": Decimal(str(round(spread_pct, 2))),
        "iv": Decimal(str(iv)),
        "delta": Decimal(str(delta)),
        "gamma": Decimal(str(gamma)),
        "theta": Decimal(str(theta)),
        "vega": Decimal(str(vega)),
        "open_interest": oi,
        "volume": volume,
        "oi_5d_change_pct": Decimal(str(round(float(candidate.get("oi_change_pct", 0)), 2))),
        # Placeholder values for required fields
        "breakeven_price": Decimal(str(strike + mid)) if option_type == "CALL" else Decimal(str(strike - mid)),
        "required_move_pct": Decimal("0"),  # Will be calculated in Stage 4
        "expected_move_pct": Decimal("0"),  # Will be calculated in Stage 4
        "feasibility_ratio": Decimal("0"),  # Will be calculated in Stage 4
        "time_adjusted_feasibility": Decimal("0"),  # Will be calculated in Stage 4
        "dte_bucket": dte_bucket,
        "rank_score": Decimal(str(round(float(candidate.get("priority_score", 0)), 2))),
        "policy_version": "1.0.0",
        "policy_hash": "scanner-direct",
        # Scanner source fields
        "scanner_source": "UNUSUAL_VOLUME",
        "scanner_metrics": scanner_metrics,
        "trigger_reasons": candidate.get("trigger_reasons", []),
        "evaluated_at": now.isoformat(),
        # GSI keys for queries
        "GSI1PK": "VERDICT#PENDING",  # Will be updated after Stage 4
        "GSI1SK": now.isoformat(),
        "GSI2PK": f"DATE#{now.strftime('%Y-%m-%d')}",
        "GSI2SK": f"{float(candidate.get('priority_score', 0)):06.2f}",
    }

    # Write to DynamoDB
    evaluations_table.put_item(Item=eval_item)

    return evaluation_id


def _update_candidate_status(
    scan_id: str,
    option_ticker: str,
    status: str,
    filter_reason: Optional[str] = None,
    evaluation_id: Optional[str] = None,
) -> None:
    """Update candidate handoff status.

    Args:
        scan_id: Scan identifier
        option_ticker: Option contract ticker
        status: New handoff status
        filter_reason: Optional filter reason(s)
        evaluation_id: Optional evaluation ID if processed
    """
    update_expr = "SET handoff_status = :status"
    expr_values: dict[str, Any] = {":status": status}

    if filter_reason:
        update_expr += ", handoff_filter_reason = :reason"
        expr_values[":reason"] = filter_reason

    if evaluation_id:
        update_expr += ", evaluation_id = :eval_id"
        expr_values[":eval_id"] = evaluation_id

    candidates_table.update_item(
        Key={
            "PK": f"SCAN#{scan_id}",
            "SK": f"CONTRACT#{option_ticker}",
        },
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )


# For local testing
if __name__ == "__main__":
    # Mock DynamoDB Streams event
    test_event = {
        "Records": [
            {
                "eventName": "INSERT",
                "dynamodb": {
                    "NewImage": {
                        "PK": {"S": "SCAN#test-123"},
                        "SK": {"S": "CONTRACT#AAPL250117C00200000"},
                        "scan_id": {"S": "test-123"},
                        "option_ticker": {"S": "AAPL250117C00200000"},
                        "underlying_ticker": {"S": "AAPL"},
                        "option_type": {"S": "CALL"},
                        "strike": {"N": "200"},
                        "expiration_date": {"S": "2025-01-17"},
                        "dte": {"N": "30"},
                        "underlying_price": {"N": "220.50"},
                        "bid": {"N": "5.50"},
                        "ask": {"N": "5.75"},
                        "mid": {"N": "5.625"},
                        "spread_pct": {"N": "4.44"},
                        "iv": {"N": "0.35"},
                        "delta": {"N": "0.65"},
                        "gamma": {"N": "0.02"},
                        "theta": {"N": "-0.15"},
                        "vega": {"N": "0.25"},
                        "today_volume": {"N": "5000"},
                        "today_oi": {"N": "10000"},
                        "volume_ratio": {"N": "5.0"},
                        "avg_volume_20d": {"N": "1000"},
                        "oi_change_pct": {"N": "25"},
                        "prior_oi": {"N": "8000"},
                        "trigger_reasons": {"L": [{"S": "VOL_SPIKE"}, {"S": "OI_INCREASE"}]},
                        "volume_source": {"S": "CONTRACT_SPECIFIC"},
                        "priority_score": {"N": "75.5"},
                        "handoff_status": {"S": "PENDING"},
                    },
                },
            },
        ],
    }
    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2))
