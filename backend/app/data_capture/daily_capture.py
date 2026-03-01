"""Daily market data capture from Polygon to S3 for backtesting.

Captures four data types after each market close:
1. Stock OHLCV — all tickers via grouped daily endpoint (1 API call)
2. Options chains — watchlist tickers via snapshot endpoint (~500 tickers)
3. IV history — derived from captured options data (no API calls)
4. Market context — SPY from OHLCV + VIX from Polygon (1 API call)

Output paths match existing historical data layout:
    s3://{bucket}/stock-ohlcv/date={date}/data.parquet
    s3://{bucket}/options-chains/date={date}/data.parquet
    s3://{bucket}/iv-history/date={date}/data.parquet
    s3://{bucket}/market-context/data.parquet  (append + rewrite)
"""

from __future__ import annotations

import io
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Reuse schemas from ingestion scripts
OPTIONS_SCHEMA = pa.schema([
    ("ticker", pa.string()),
    ("trade_date", pa.string()),
    ("strike", pa.float64()),
    ("expiry_date", pa.string()),
    ("option_type", pa.string()),
    ("last_price", pa.float64()),
    ("bid", pa.float64()),
    ("ask", pa.float64()),
    ("bid_iv", pa.float64()),
    ("ask_iv", pa.float64()),
    ("open_interest", pa.int64()),
    ("volume", pa.int64()),
    ("delta", pa.float64()),
    ("gamma", pa.float64()),
    ("vega", pa.float64()),
    ("theta", pa.float64()),
])

OHLCV_SCHEMA = pa.schema([
    ("ticker", pa.string()),
    ("date", pa.string()),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.int64()),
    ("vwap", pa.float64()),
])

IV_HISTORY_SCHEMA = pa.schema([
    ("ticker", pa.string()),
    ("date", pa.string()),
    ("atm_iv", pa.float64()),
])

MARKET_CONTEXT_SCHEMA = pa.schema([
    ("date", pa.string()),
    ("spy_close", pa.float64()),
    ("spy_change_pct", pa.float64()),
    ("vix_close", pa.float64()),
])


def _get_s3_client():
    import boto3
    return boto3.client("s3")


def _s3_key_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def _write_parquet_to_s3(s3, bucket: str, key: str, table: pa.Table) -> None:
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())


def _resolve_capture_date(capture_date: Optional[str]) -> str:
    """Determine the capture date, defaulting to today or last trading day."""
    if capture_date:
        return capture_date

    now = datetime.now(timezone.utc)
    # If before 21:00 UTC (4 PM ET), use yesterday's date
    # After market close, use today's date
    if now.hour < 21:
        target = now.date() - timedelta(days=1)
    else:
        target = now.date()

    # Skip weekends
    while target.weekday() >= 5:
        target -= timedelta(days=1)

    return target.isoformat()


async def capture_stock_ohlcv(
    polygon_client: Any,
    s3: Any,
    bucket: str,
    capture_date: str,
    force: bool = False,
) -> dict[str, Any]:
    """Capture stock OHLCV for all tickers via grouped daily endpoint."""
    s3_key = f"stock-ohlcv/date={capture_date}/data.parquet"

    if not force and _s3_key_exists(s3, bucket, s3_key):
        logger.info(f"Stock OHLCV already exists for {capture_date}, skipping")
        return {"status": "skipped", "reason": "already_exists"}

    bars = await polygon_client.get_grouped_daily_bars(capture_date)
    if not bars:
        logger.warning(f"No stock OHLCV data returned for {capture_date}")
        return {"status": "no_data", "tickers": 0}

    records = []
    for ticker, bar in bars.items():
        records.append({
            "ticker": bar.ticker,
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": int(bar.volume),
            "vwap": bar.vwap or 0.0,
        })

    table = pa.table(
        {col: pa.array([r[col] for r in records], type=OHLCV_SCHEMA.field(col).type)
         for col in OHLCV_SCHEMA.names},
        schema=OHLCV_SCHEMA,
    )
    _write_parquet_to_s3(s3, bucket, s3_key, table)

    logger.info(f"Stock OHLCV: wrote {len(records)} tickers for {capture_date}")
    return {"status": "ok", "tickers": len(records)}


def _map_polygon_contract(underlying_ticker: str, trade_date: str, contract: dict) -> dict | None:
    """Map a Polygon options snapshot contract to our parquet schema."""
    details = contract.get("details", {})
    day = contract.get("day", {})
    greeks = contract.get("greeks", {})
    last_quote = contract.get("last_quote", {})

    strike = details.get("strike_price")
    expiry = details.get("expiration_date")
    contract_type = details.get("contract_type", "")

    if not strike or not expiry or not contract_type:
        return None

    # Normalize contract type: CALL→c, PUT→p
    option_type = "c" if contract_type.upper() == "CALL" else "p"

    # Price: prefer last_quote bid/ask, fall back to day data
    bid = float(last_quote.get("bid", 0) or day.get("last_bid", 0) or 0)
    ask = float(last_quote.get("ask", 0) or day.get("last_ask", 0) or 0)
    last_price = float(day.get("close", 0) or 0)

    # IV: Polygon gives one value, store in both bid_iv and ask_iv
    iv = float(contract.get("implied_volatility", 0) or greeks.get("implied_volatility", 0) or 0)

    return {
        "ticker": underlying_ticker,
        "trade_date": trade_date,
        "strike": float(strike),
        "expiry_date": str(expiry),
        "option_type": option_type,
        "last_price": last_price,
        "bid": bid,
        "ask": ask,
        "bid_iv": iv,
        "ask_iv": iv,
        "open_interest": int(contract.get("open_interest", 0) or 0),
        "volume": int(day.get("volume", 0) or 0),
        "delta": float(greeks.get("delta", 0) or 0),
        "gamma": float(greeks.get("gamma", 0) or 0),
        "vega": float(greeks.get("vega", 0) or 0),
        "theta": float(greeks.get("theta", 0) or 0),
    }


async def capture_options_chains(
    polygon_client: Any,
    s3: Any,
    bucket: str,
    capture_date: str,
    tickers: list[str],
    force: bool = False,
) -> dict[str, Any]:
    """Capture options chains for watchlist tickers from Polygon snapshot."""
    s3_key = f"options-chains/date={capture_date}/data.parquet"

    if not force and _s3_key_exists(s3, bucket, s3_key):
        logger.info(f"Options chains already exist for {capture_date}, skipping")
        return {"status": "skipped", "reason": "already_exists"}

    # Expiration filter: today to 90 days out
    dt = date.fromisoformat(capture_date)
    exp_gte = capture_date
    exp_lte = (dt + timedelta(days=90)).isoformat()

    # Fetch all tickers' chains concurrently (PolygonClient handles rate limiting)
    all_chains = await polygon_client.get_options_chain_batch(
        tickers, expiration_date_gte=exp_gte, expiration_date_lte=exp_lte
    )

    # Map to our schema
    all_records: list[dict] = []
    tickers_with_data = 0

    for ticker, contracts in all_chains.items():
        ticker_records = []
        for contract in contracts:
            mapped = _map_polygon_contract(ticker, capture_date, contract)
            if mapped:
                ticker_records.append(mapped)
        if ticker_records:
            tickers_with_data += 1
            all_records.extend(ticker_records)

    if not all_records:
        logger.warning(f"No options data captured for {capture_date}")
        return {"status": "no_data", "tickers": 0, "contracts": 0}

    # Build parquet table
    table = pa.table(
        {col: pa.array([r[col] for r in all_records], type=OPTIONS_SCHEMA.field(col).type)
         for col in OPTIONS_SCHEMA.names},
        schema=OPTIONS_SCHEMA,
    )
    _write_parquet_to_s3(s3, bucket, s3_key, table)

    logger.info(
        f"Options chains: wrote {len(all_records)} contracts "
        f"from {tickers_with_data} tickers for {capture_date}"
    )
    return {"status": "ok", "tickers": tickers_with_data, "contracts": len(all_records)}


def derive_iv_from_options(
    s3: Any,
    bucket: str,
    capture_date: str,
    force: bool = False,
) -> dict[str, Any]:
    """Derive IV history from the day's options parquet (already in S3)."""
    iv_key = f"iv-history/date={capture_date}/data.parquet"

    if not force and _s3_key_exists(s3, bucket, iv_key):
        logger.info(f"IV history already exists for {capture_date}, skipping")
        return {"status": "skipped", "reason": "already_exists"}

    # Read options chains for this date
    options_key = f"options-chains/date={capture_date}/data.parquet"
    try:
        obj = s3.get_object(Bucket=bucket, Key=options_key)
        buf = io.BytesIO(obj["Body"].read())
        table = pq.read_table(buf)
    except Exception as e:
        logger.warning(f"Cannot read options for IV derivation: {e}")
        return {"status": "error", "error": str(e)}

    # ATM IV computation (same logic as derive_iv_history.py)
    tickers = table.column("ticker").to_pylist()
    deltas = table.column("delta").to_pylist()
    bid_ivs = table.column("bid_iv").to_pylist()
    ask_ivs = table.column("ask_iv").to_pylist()
    expiry_dates = table.column("expiry_date").to_pylist()

    ticker_ivs: dict[str, list[float]] = defaultdict(list)

    for i in range(len(tickers)):
        delta = deltas[i] if deltas[i] is not None else 0.0
        bid_iv = bid_ivs[i] if bid_ivs[i] is not None else 0.0
        ask_iv = ask_ivs[i] if ask_ivs[i] is not None else 0.0

        # ATM filter: |delta| between 0.35-0.65
        abs_delta = abs(delta)
        if abs_delta < 0.35 or abs_delta > 0.65:
            continue

        # DTE filter: 20-60 days
        expiry = str(expiry_dates[i]) if expiry_dates[i] else ""
        if not expiry or expiry <= capture_date:
            continue
        try:
            exp_d = date.fromisoformat(expiry)
            td_d = date.fromisoformat(capture_date)
            dte = (exp_d - td_d).days
            if dte < 20 or dte > 60:
                continue
        except (ValueError, TypeError):
            continue

        mid_iv = (bid_iv + ask_iv) / 2 if (bid_iv + ask_iv) > 0 else bid_iv or ask_iv
        if mid_iv > 0:
            ticker_ivs[tickers[i]].append(mid_iv)

    # Compute average IV per ticker
    records = []
    for ticker, ivs in ticker_ivs.items():
        if ivs:
            avg_iv = sum(ivs) / len(ivs)
            records.append({
                "ticker": ticker,
                "date": capture_date,
                "atm_iv": round(avg_iv, 6),
            })

    if not records:
        logger.warning(f"No ATM IV data derived for {capture_date}")
        return {"status": "no_data", "tickers": 0}

    table = pa.table(
        {
            "ticker": pa.array([r["ticker"] for r in records], type=pa.string()),
            "date": pa.array([r["date"] for r in records], type=pa.string()),
            "atm_iv": pa.array([r["atm_iv"] for r in records], type=pa.float64()),
        },
        schema=IV_HISTORY_SCHEMA,
    )
    _write_parquet_to_s3(s3, bucket, iv_key, table)

    logger.info(f"IV history: wrote {len(records)} tickers for {capture_date}")
    return {"status": "ok", "tickers": len(records)}


async def update_market_context(
    polygon_client: Any,
    s3: Any,
    bucket: str,
    capture_date: str,
    force: bool = False,
) -> dict[str, Any]:
    """Update market context parquet with today's SPY + VIX data."""
    mc_key = "market-context/data.parquet"

    # Read existing market context
    existing_records: list[dict] = []
    try:
        obj = s3.get_object(Bucket=bucket, Key=mc_key)
        buf = io.BytesIO(obj["Body"].read())
        existing_table = pq.read_table(buf)
        for i in range(existing_table.num_rows):
            existing_records.append({
                "date": existing_table.column("date")[i].as_py(),
                "spy_close": existing_table.column("spy_close")[i].as_py(),
                "spy_change_pct": existing_table.column("spy_change_pct")[i].as_py(),
                "vix_close": existing_table.column("vix_close")[i].as_py(),
            })
    except Exception as e:
        logger.warning(f"Could not read existing market context: {e}")

    # Check if date already present
    existing_dates = {r["date"] for r in existing_records}
    if capture_date in existing_dates and not force:
        logger.info(f"Market context already has {capture_date}, skipping")
        return {"status": "skipped", "reason": "already_exists"}

    # Get SPY close from today's stock OHLCV
    ohlcv_key = f"stock-ohlcv/date={capture_date}/data.parquet"
    spy_close = 0.0
    try:
        obj = s3.get_object(Bucket=bucket, Key=ohlcv_key)
        buf = io.BytesIO(obj["Body"].read())
        ohlcv_table = pq.read_table(buf, columns=["ticker", "close"])
        import pyarrow.compute as pc
        spy_rows = ohlcv_table.filter(pc.field("ticker") == "SPY")
        if spy_rows.num_rows > 0:
            spy_close = spy_rows.column("close")[0].as_py()
    except Exception as e:
        logger.warning(f"Could not read SPY close: {e}")

    # Get VIX close from Polygon
    vix_close = 0.0
    try:
        vix_bars = await polygon_client.get_daily_bars("I:VIX", capture_date, capture_date)
        if vix_bars:
            vix_close = vix_bars[0].get("c", 0.0)
    except Exception as e:
        logger.warning(f"Could not fetch VIX: {e}")

    # Compute SPY change %
    spy_change_pct = 0.0
    if existing_records and spy_close > 0:
        prev = existing_records[-1]
        prev_close = prev.get("spy_close", 0)
        if prev_close > 0:
            spy_change_pct = round(((spy_close - prev_close) / prev_close) * 100, 4)

    # Add new record (or replace if force)
    if capture_date in existing_dates:
        existing_records = [r for r in existing_records if r["date"] != capture_date]
    existing_records.append({
        "date": capture_date,
        "spy_close": spy_close,
        "spy_change_pct": spy_change_pct,
        "vix_close": vix_close,
    })
    existing_records.sort(key=lambda r: r["date"])

    # Write updated parquet
    table = pa.table(
        {
            "date": pa.array([r["date"] for r in existing_records], type=pa.string()),
            "spy_close": pa.array([r["spy_close"] for r in existing_records], type=pa.float64()),
            "spy_change_pct": pa.array(
                [r["spy_change_pct"] for r in existing_records], type=pa.float64()
            ),
            "vix_close": pa.array([r["vix_close"] for r in existing_records], type=pa.float64()),
        },
        schema=MARKET_CONTEXT_SCHEMA,
    )
    _write_parquet_to_s3(s3, bucket, mc_key, table)

    logger.info(f"Market context: updated with {capture_date} (SPY={spy_close}, VIX={vix_close})")
    return {"status": "ok", "spy_close": spy_close, "vix_close": vix_close}


async def run_daily_capture(
    capture_date: Optional[str] = None, force: bool = False,
) -> dict[str, Any]:
    """Capture all market data for a single trading day.

    Args:
        capture_date: Date to capture (YYYY-MM-DD). Defaults to today/last trading day.
        force: If True, overwrite existing data.

    Returns:
        Summary of capture results for each data type.
    """
    from app.core.watchlist import WatchlistManager
    from app.db.tables import PolicyTable
    from app.services.polygon import PolygonClient

    resolved_date = _resolve_capture_date(capture_date)
    logger.info(f"Starting daily data capture for {resolved_date} (force={force})")

    bucket = os.environ.get("BACKTEST_S3_BUCKET", "")
    if not bucket:
        return {"status": "error", "error": "BACKTEST_S3_BUCKET not configured"}

    s3 = _get_s3_client()
    results: dict[str, Any] = {"capture_date": resolved_date}

    async with PolygonClient() as polygon:
        # Step 1: Stock OHLCV
        logger.info("Step 1/4: Capturing stock OHLCV...")
        results["stock_ohlcv"] = await capture_stock_ohlcv(
            polygon, s3, bucket, resolved_date, force=force
        )

        # Step 2: Options chains for watchlist tickers
        logger.info("Step 2/4: Capturing options chains...")
        try:
            policy = await PolicyTable.get_active()
            if policy:
                watchlist = await WatchlistManager.from_policy_async(policy.config)
                tickers = watchlist.tickers
            else:
                watchlist = WatchlistManager()
                tickers = watchlist.tickers
        except Exception as e:
            logger.warning(f"Could not load watchlist, using defaults: {e}")
            watchlist = WatchlistManager()
            tickers = watchlist.tickers

        logger.info(f"Capturing options for {len(tickers)} watchlist tickers")
        results["options_chains"] = await capture_options_chains(
            polygon, s3, bucket, resolved_date, tickers, force=force
        )

        # Step 3: Derive IV history from captured options
        logger.info("Step 3/4: Deriving IV history...")
        results["iv_history"] = derive_iv_from_options(
            s3, bucket, resolved_date, force=force
        )

        # Step 4: Update market context
        logger.info("Step 4/4: Updating market context...")
        results["market_context"] = await update_market_context(
            polygon, s3, bucket, resolved_date, force=force
        )

    logger.info(f"Daily capture complete for {resolved_date}: {results}")
    return results
