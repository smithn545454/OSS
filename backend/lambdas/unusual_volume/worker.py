"""Worker Lambda for Unusual Volume Scanner.

Processes a single ticker to identify options contracts with unusual volume.
Triggered by SQS messages published from the Publisher Lambda.

Processing Steps:
1. Receive ticker from SQS message
2. Fetch full options chain from Polygon API
3. Apply pre-filters (minimum volume, non-zero OI, etc.)
4. Look up expected volume from volume-stats table
5. Calculate volume ratios and OI changes
6. Identify contracts meeting trigger conditions
7. Calculate priority scores
8. Write candidates to unusual-volume-candidates table

Trigger Conditions:
- VOL_SPIKE: volume_ratio >= 2.0
- OI_INCREASE: oi_change_pct >= 15%
- OI_DECREASE: oi_change_pct <= -20%
- VOL_OI_COMBO: Both volume spike and OI change

Pre-filters (lightweight, applied early):
- Minimum volume (100)
- Non-zero open interest
- Valid bid/ask spread (< 50%)
- DTE within range (1-90 days)
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
import urllib3
from boto3.dynamodb.conditions import Key
from utils.buckets import build_bucket_key, get_dte_bucket, get_moneyness_bucket, is_scannable_dte
from utils.scoring import calculate_priority_score, get_trigger_reasons

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss")
POLYGON_SECRET_ARN = os.environ.get("POLYGON_SECRET_ARN", "")
VOLUME_RATIO_THRESHOLD = float(os.environ.get("VOLUME_RATIO_THRESHOLD", "2.0"))
OI_CHANGE_THRESHOLD_PCT = float(os.environ.get("OI_CHANGE_THRESHOLD_PCT", "15.0"))

# Pre-filter thresholds
MIN_VOLUME = 100
MIN_OPEN_INTEREST = 1
MAX_SPREAD_PCT = 50.0
MIN_DTE = 1
MAX_DTE = 90

# Constants
POLYGON_BASE_URL = "https://api.polygon.io"
TTL_HOURS = 24  # Candidates expire after 24 hours

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
secrets_client = boto3.client("secretsmanager")

volume_stats_table = dynamodb.Table(f"{TABLE_PREFIX}-volume-stats")
candidates_table = dynamodb.Table(f"{TABLE_PREFIX}-unusual-volume-candidates")
oi_history_table = dynamodb.Table(f"{TABLE_PREFIX}-oi-history")

# HTTP client for Polygon API
http = urllib3.PoolManager()

# Polygon API key (initialized lazily)
_polygon_api_key: Optional[str] = None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point for worker.

    Args:
        event: SQS event containing ticker message
        context: Lambda context

    Returns:
        Response with processing results
    """
    logger.info("Worker Lambda invoked")

    start_time = time.time()
    results = []

    # Process each SQS record (usually batch size = 1)
    for record in event.get("Records", []):
        try:
            # Parse message
            body = json.loads(record.get("body", "{}"))
            scan_id = body.get("scan_id", "unknown")
            ticker = body.get("ticker", "")

            if not ticker:
                logger.warning(f"Missing ticker in message: {body}")
                continue

            logger.info(f"Processing ticker: {ticker} (scan: {scan_id})")

            result = _process_ticker(scan_id, ticker)
            results.append(result)

        except Exception as e:
            logger.error(f"Error processing record: {str(e)}", exc_info=True)
            results.append({"error": str(e)})

    duration_ms = int((time.time() - start_time) * 1000)
    logger.info(f"Worker completed in {duration_ms}ms")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "records_processed": len(results),
            "results": results,
            "duration_ms": duration_ms,
        }),
    }


def _process_ticker(scan_id: str, ticker: str) -> dict:
    """Process a single ticker for unusual volume detection.

    Args:
        scan_id: Scan identifier
        ticker: Underlying ticker symbol

    Returns:
        Processing result summary
    """
    # Fetch options chain from Polygon
    options_chain = _fetch_options_chain(ticker)

    if not options_chain:
        logger.warning(f"No options chain found for {ticker}")
        return {
            "ticker": ticker,
            "contracts_scanned": 0,
            "candidates_found": 0,
            "status": "NO_OPTIONS",
        }

    logger.info(f"Fetched {len(options_chain)} contracts for {ticker}")

    # Fetch underlying price via previous close (Basic tier has no underlying_asset.price)
    underlying_price = _get_previous_close(ticker)
    logger.info(f"{ticker}: underlying_price from previous close = {underlying_price}")

    # Get today's date for stats lookup
    as_of_date = date.today().isoformat()
    ttl = int((datetime.now(timezone.utc) + timedelta(hours=TTL_HOURS)).timestamp())

    candidates_found = 0
    contracts_passed_prefilter = 0
    had_baseline = 0
    no_baseline = 0
    max_ratio = 0.0

    for contract in options_chain:
        # Apply pre-filters
        if not _passes_prefilter(contract):
            continue

        contracts_passed_prefilter += 1

        # Get expected volume
        expected_volume = _get_expected_volume(contract, ticker, as_of_date, underlying_price)

        if expected_volume is None or expected_volume < 1:
            no_baseline += 1
            continue

        had_baseline += 1

        # Calculate metrics
        today_volume = contract.get("day", {}).get("volume", 0)
        volume_ratio = today_volume / expected_volume
        max_ratio = max(max_ratio, volume_ratio)

        # Get OI change
        today_oi = contract.get("open_interest", 0)
        prior_oi = _get_prior_oi(contract.get("details", {}).get("ticker", ""))
        oi_change_pct = _calculate_oi_change_pct(today_oi, prior_oi)

        # Check trigger conditions
        trigger_reasons = get_trigger_reasons(
            volume_ratio=volume_ratio,
            oi_change_pct=oi_change_pct,
            volume_ratio_threshold=VOLUME_RATIO_THRESHOLD,
            oi_increase_threshold_pct=OI_CHANGE_THRESHOLD_PCT,
            oi_decrease_threshold_pct=-20.0,
        )

        if not trigger_reasons:
            continue

        # Calculate priority score
        last_quote = contract.get("last_quote", {})

        bid = last_quote.get("bid", 0)
        ask = last_quote.get("ask", 0)

        # Fallback: use day close as mid proxy when no bid/ask available
        # Polygon basic tier may not include last_quote in snapshot data
        if bid <= 0 or ask <= 0:
            day_close = contract.get("day", {}).get("close", 0) or 0
            if day_close > 0:
                half_spread = day_close * 0.025
                bid = day_close - half_spread
                ask = day_close + half_spread

        mid = (bid + ask) / 2 if (bid + ask) > 0 else 0
        spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 100

        volume_source = contract.get("_volume_source", "CONTRACT_SPECIFIC")

        priority_score = calculate_priority_score(
            volume_ratio=volume_ratio,
            oi_change_pct=oi_change_pct,
            trigger_count=len(trigger_reasons),
            spread_pct=spread_pct,
            today_volume=today_volume,
            today_oi=today_oi,
            volume_source=volume_source,
        )

        # Write candidate to DynamoDB
        _write_candidate(
            scan_id=scan_id,
            contract=contract,
            ticker=ticker,
            underlying_price=underlying_price,
            volume_ratio=volume_ratio,
            expected_volume=expected_volume,
            today_oi=today_oi,
            prior_oi=prior_oi,
            oi_change_pct=oi_change_pct,
            trigger_reasons=trigger_reasons,
            priority_score=priority_score,
            volume_source=volume_source,
            ttl=ttl,
        )

        candidates_found += 1

    logger.info(
        f"{ticker}: {contracts_passed_prefilter} passed prefilter, "
        f"{had_baseline} had baseline, {no_baseline} no baseline, "
        f"max_ratio={max_ratio:.2f}, {candidates_found} candidates found"
    )

    return {
        "ticker": ticker,
        "contracts_scanned": len(options_chain),
        "contracts_passed_prefilter": contracts_passed_prefilter,
        "candidates_found": candidates_found,
        "status": "OK",
    }


def _fetch_options_chain(ticker: str) -> list[dict]:
    """Fetch full options chain from Polygon API.

    Args:
        ticker: Underlying ticker symbol

    Returns:
        List of option contract snapshots
    """
    api_key = _get_polygon_api_key()

    url = f"{POLYGON_BASE_URL}/v3/snapshot/options/{ticker}"
    params = {
        "apiKey": api_key,
        "limit": 250,  # Max per request
    }

    all_results = []

    while url:
        try:
            # Build full URL with params
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            full_url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

            response = http.request("GET", full_url, timeout=30.0)

            if response.status != 200:
                logger.error(f"Polygon API error: {response.status}")
                break

            data = json.loads(response.data.decode("utf-8"))
            results = data.get("results", [])
            all_results.extend(results)

            # Check for pagination
            next_url = data.get("next_url")
            if next_url:
                url = next_url
                params = {"apiKey": api_key}  # Reset params for next page
            else:
                break

        except Exception as e:
            logger.error(f"Error fetching options chain for {ticker}: {str(e)}")
            break

    return all_results


def _passes_prefilter(contract: dict) -> bool:
    """Apply lightweight pre-filters to a contract.

    Args:
        contract: Polygon option snapshot

    Returns:
        True if contract passes all pre-filters
    """
    # Volume filter
    day_data = contract.get("day", {})
    volume = day_data.get("volume", 0)
    if volume < MIN_VOLUME:
        return False

    # Open interest filter
    oi = contract.get("open_interest", 0)
    if oi < MIN_OPEN_INTEREST:
        return False

    # Spread filter
    last_quote = contract.get("last_quote", {})
    bid = last_quote.get("bid", 0)
    ask = last_quote.get("ask", 0)
    mid = (bid + ask) / 2

    if mid > 0:
        spread_pct = (ask - bid) / mid * 100
        if spread_pct > MAX_SPREAD_PCT:
            return False

    # DTE filter
    details = contract.get("details", {})
    expiration_str = details.get("expiration_date", "")
    if expiration_str:
        try:
            expiration = date.fromisoformat(expiration_str)
            dte = (expiration - date.today()).days
            if dte < MIN_DTE or dte > MAX_DTE:
                return False
            if not is_scannable_dte(dte):
                return False
        except ValueError:
            return False

    return True


def _get_expected_volume(
    contract: dict, underlying: str, as_of_date: str, underlying_price: float = 0
) -> Optional[float]:
    """Look up expected volume from volume-stats table.

    Uses contract-specific stats when available, falls back to bucket stats.

    Args:
        contract: Polygon option snapshot
        underlying: Underlying ticker
        as_of_date: Date for stats lookup
        underlying_price: Underlying stock price (from previous close API)

    Returns:
        Expected average volume, or None if not found
    """
    option_ticker = contract.get("details", {}).get("ticker", "").replace("O:", "")

    # Try contract-specific stats first
    pk = f"CONTRACT#{option_ticker}"
    try:
        response = volume_stats_table.query(
            KeyConditionExpression=(
                Key("PK").eq(pk) &
                Key("SK").begins_with("STATS#")
            ),
            ScanIndexForward=False,  # Get most recent first
            Limit=1,
        )
    except Exception as e:
        logger.error(f"DynamoDB query failed for {pk}: {e}")
        return None

    items = response.get("Items", [])
    if items:
        contract["_volume_source"] = "CONTRACT_SPECIFIC"
        return float(items[0].get("avg_volume_20d", 0))

    # Fall back to bucket stats
    details = contract.get("details", {})
    option_type = "CALL" if details.get("contract_type") == "call" else "PUT"

    # Calculate moneyness bucket (use passed underlying_price from previous close API)
    strike = details.get("strike_price", 0)

    if underlying_price > 0 and strike > 0:
        moneyness_bucket = get_moneyness_bucket(strike, underlying_price, option_type)
    else:
        moneyness_bucket = "ATM"  # Default

    # Calculate DTE bucket
    expiration_str = details.get("expiration_date", "")
    if expiration_str:
        expiration = date.fromisoformat(expiration_str)
        dte = (expiration - date.today()).days
        dte_bucket = get_dte_bucket(dte)
    else:
        dte_bucket = "C"  # Default

    # Build bucket key and query
    bucket_key = build_bucket_key(underlying, option_type, moneyness_bucket, dte_bucket)

    response = volume_stats_table.query(
        KeyConditionExpression=(
            Key("PK").eq(bucket_key) &
            Key("SK").begins_with("STATS#")
        ),
        ScanIndexForward=False,
        Limit=1,
    )

    items = response.get("Items", [])
    if items:
        contract["_volume_source"] = "BUCKET_FALLBACK"
        return float(items[0].get("avg_volume_20d", 0))

    return None


def _get_prior_oi(option_ticker: str) -> float:
    """Get prior day's open interest from oi-history table.

    Args:
        option_ticker: OCC option symbol

    Returns:
        Prior open interest, or 0 if not found
    """
    clean_ticker = option_ticker.replace("O:", "")
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    response = oi_history_table.query(
        KeyConditionExpression=(
            Key("PK").eq(f"CONTRACT#{clean_ticker}") &
            Key("SK").eq(f"DATE#{yesterday}")
        ),
        Limit=1,
    )

    items = response.get("Items", [])
    if items:
        return float(items[0].get("open_interest", 0))

    return 0


def _calculate_oi_change_pct(today_oi: int, prior_oi: float) -> float:
    """Calculate OI change percentage.

    Args:
        today_oi: Today's open interest
        prior_oi: Prior day's open interest

    Returns:
        OI change as percentage
    """
    if prior_oi <= 0:
        return 0.0

    return ((today_oi - prior_oi) / prior_oi) * 100


def _write_candidate(
    scan_id: str,
    contract: dict,
    ticker: str,
    underlying_price: float,
    volume_ratio: float,
    expected_volume: float,
    today_oi: int,
    prior_oi: float,
    oi_change_pct: float,
    trigger_reasons: list[str],
    priority_score: float,
    volume_source: str,
    ttl: int,
) -> None:
    """Write unusual volume candidate to DynamoDB.

    Args:
        scan_id: Scan identifier
        contract: Polygon option snapshot
        ticker: Underlying ticker
        underlying_price: Underlying stock price (from previous close API)
        volume_ratio: Today's volume / expected volume
        expected_volume: Average volume from stats
        today_oi: Today's open interest
        prior_oi: Prior day's open interest
        oi_change_pct: OI change percentage
        trigger_reasons: List of trigger reason codes
        priority_score: Calculated priority score
        volume_source: "CONTRACT_SPECIFIC" or "BUCKET_FALLBACK"
        ttl: TTL timestamp
    """
    details = contract.get("details", {})
    option_ticker = details.get("ticker", "").replace("O:", "")
    day_data = contract.get("day", {})
    last_quote = contract.get("last_quote", {})
    greeks = contract.get("greeks", {})

    bid = last_quote.get("bid", 0)
    ask = last_quote.get("ask", 0)

    # Fallback: use day close as mid proxy when no bid/ask available
    if bid <= 0 or ask <= 0:
        day_close = day_data.get("close", 0) or 0
        if day_close > 0:
            half_spread = day_close * 0.025
            bid = day_close - half_spread
            ask = day_close + half_spread

    mid = (bid + ask) / 2 if (bid + ask) > 0 else 0
    spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 0

    # IV: check contract top level first, then greeks (Polygon puts IV at top level)
    iv = contract.get("implied_volatility") or greeks.get("implied_volatility", 0) or 0

    # Calculate DTE
    expiration_str = details.get("expiration_date", "")
    expiration = date.fromisoformat(expiration_str) if expiration_str else date.today()
    dte = (expiration - date.today()).days

    now = datetime.now(timezone.utc)

    candidates_table.put_item(
        Item={
            "PK": f"SCAN#{scan_id}",
            "SK": f"CONTRACT#{option_ticker}",
            "scan_id": scan_id,
            "scan_timestamp": now.isoformat(),
            "option_ticker": option_ticker,
            "underlying_ticker": ticker,
            "option_type": "CALL" if details.get("contract_type") == "call" else "PUT",
            "strike": Decimal(str(details.get("strike_price", 0))),
            "expiration_date": expiration_str,
            "dte": dte,
            "today_volume": day_data.get("volume", 0),
            "avg_volume_20d": Decimal(str(round(expected_volume, 2))),
            "volume_ratio": Decimal(str(round(volume_ratio, 4))),
            "today_oi": today_oi,
            "prior_oi": Decimal(str(round(prior_oi, 2))),
            "oi_change_pct": Decimal(str(round(oi_change_pct, 2))),
            "trigger_reasons": trigger_reasons,
            "underlying_price": Decimal(str(underlying_price)),
            "bid": Decimal(str(bid)),
            "ask": Decimal(str(ask)),
            "mid": Decimal(str(round(mid, 4))),
            "spread_pct": Decimal(str(round(spread_pct, 2))),
            "iv": Decimal(str(iv)),
            "delta": Decimal(str(greeks.get("delta", 0) or 0)),
            "gamma": Decimal(str(greeks.get("gamma", 0) or 0)),
            "theta": Decimal(str(greeks.get("theta", 0) or 0)),
            "vega": Decimal(str(greeks.get("vega", 0) or 0)),
            "volume_source": volume_source,
            "priority_score": Decimal(str(priority_score)),
            "handoff_status": "PENDING",
            "created_at": now.isoformat(),
            "ttl": ttl,
        }
    )


def _get_previous_close(ticker: str) -> float:
    """Fetch underlying price via Polygon previous close endpoint.

    Args:
        ticker: Underlying ticker symbol (e.g., "AAPL")

    Returns:
        Previous close price, or 0 if unavailable
    """
    api_key = _get_polygon_api_key()
    url = f"{POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}/prev?apiKey={api_key}"

    try:
        response = http.request("GET", url, timeout=10.0)
        if response.status != 200:
            logger.warning(f"Previous close API error for {ticker}: {response.status}")
            return 0

        data = json.loads(response.data.decode("utf-8"))
        results = data.get("results", [])
        if results:
            return float(results[0].get("c", 0))
    except Exception as e:
        logger.error(f"Error fetching previous close for {ticker}: {e}")

    return 0


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
    # Mock SQS event
    test_event = {
        "Records": [
            {
                "body": json.dumps({
                    "scan_id": "test-scan-123",
                    "ticker": "AAPL",
                }),
            },
        ],
    }
    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2))
