#!/usr/bin/env python3
"""Populate the ticker universe table with Russell 1000 tickers.

Discovers the top ~1000 US equities by average dollar volume using Polygon's
grouped daily bars endpoint (which returns ALL US stocks in 1 API call per day).
Then checks optionability and writes to the sp500-tickers DynamoDB table with
index_membership tags.

Existing S&P 500 tickers are preserved and tagged with both ["sp500", "russell1000"].
New tickers (outside S&P 500) are tagged with ["russell1000"] only.

Prerequisites:
    - AWS credentials configured for us-west-1
    - POLYGON_API_KEY or POLYGON_SECRET_ARN environment variable set
    - Existing sp500-tickers table populated

Usage:
    # Dry run: show what would be written
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
        POLYGON_SECRET_ARN=arn:aws:secretsmanager:us-west-1:... \
        python scripts/populate_ticker_universe.py --dry-run

    # Populate Russell 1000 tickers
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
        POLYGON_SECRET_ARN=arn:aws:secretsmanager:us-west-1:... \
        python scripts/populate_ticker_universe.py

    # Skip optionability check (faster, less accurate)
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
        POLYGON_SECRET_ARN=arn:aws:secretsmanager:us-west-1:... \
        python scripts/populate_ticker_universe.py --skip-options-check
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re

import httpx

# Must set env vars before importing app modules
os.environ.setdefault("AWS_REGION", "us-west-1")
os.environ.setdefault("DYNAMODB_TABLE_PREFIX", "oss-dev")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"

# Tickers to exclude (ETFs, leveraged products, etc. — these aren't "stocks")
# We also filter via Polygon reference endpoint below, but this catches common ones early
ETF_TICKERS = {
    # Major index ETFs
    "SPY", "QQQ", "IWM", "DIA", "EEM", "EFA", "VTI", "VOO", "VEA", "VWO",
    "IVV", "IJH", "IJR", "RSP", "MDY", "SCHB", "ITOT", "VTV", "VUG", "VB",
    # Sector ETFs
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLU", "XLP", "XLY", "XLB", "XLRE",
    "XBI", "SMH", "SOXX", "KRE", "XOP", "GDX", "GDXJ", "KWEB", "XHB", "XME",
    # Bond/commodity ETFs
    "GLD", "SLV", "USO", "TLT", "IEF", "HYG", "LQD", "JNK", "BND", "AGG",
    "SHY", "TIP", "EMB", "MUB", "BNDX", "VCIT", "VCSH",
    # ARK ETFs
    "ARKK", "ARKG", "ARKW", "ARKF", "ARKQ", "ARKX",
    # Leveraged/inverse ETFs
    "SQQQ", "TQQQ", "SPXS", "SPXL", "UVXY", "VXX", "SVXY", "SOXL", "SOXS",
    "LABU", "LABD", "TNA", "TZA", "FAS", "FAZ", "UPRO", "SPXU", "QLD", "QID",
    "FNGU", "FNGD", "NUGT", "DUST", "JNUG", "JDST", "TECL", "TECS",
    # Country/region ETFs
    "EWJ", "EWZ", "EWY", "EWT", "EWG", "EWU", "EWC", "EWA", "EWH", "EWS",
    "INDA", "FXI", "MCHI", "AAXJ", "VGK", "IEMG", "IEFA",
    # Other popular ETFs
    "SCHD", "JEPI", "JEPQ", "DIVO", "PFF", "VNQ", "VNQI", "REM",
    "IYR", "IGSB", "VCLT", "GOVT",
}


async def discover_top_tickers_by_dollar_volume(
    api_key: str,
    days: int = 20,
    min_price: float = 5.0,
    top_n: int = 1500,
) -> dict[str, float]:
    """Discover top US equities by average daily dollar volume.

    Uses the grouped daily bars endpoint — 1 API call per day returns ALL
    US stocks (~10,000+). We compute avg dollar volume over `days` trading
    days and return the top_n tickers.

    Returns:
        Dict mapping ticker to avg daily dollar volume.
    """
    from datetime import datetime, timedelta

    volume_sums: dict[str, float] = {}
    volume_counts: dict[str, int] = {}
    last_close: dict[str, float] = {}

    async with httpx.AsyncClient(
        base_url=POLYGON_BASE,
        timeout=60.0,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        current = datetime.now() - timedelta(days=1)  # Start from yesterday
        days_fetched = 0

        while days_fetched < days:
            if current.weekday() >= 5:
                current -= timedelta(days=1)
                continue

            date_str = current.strftime("%Y-%m-%d")
            try:
                response = await client.get(
                    f"/v2/aggs/grouped/locale/us/market/stocks/{date_str}",
                    params={"adjusted": "true"},
                )
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    for bar in results:
                        t = bar.get("T", "")
                        if not t:
                            continue
                        close = bar.get("c", 0)
                        volume = bar.get("v", 0)
                        vwap = bar.get("vw", close)
                        if close < min_price or volume == 0:
                            continue
                        # Filter out non-stock tickers (contain digits, dots, etc.)
                        if not re.match(r"^[A-Z]{1,5}$", t):
                            continue
                        dollar_vol = volume * vwap
                        volume_sums[t] = volume_sums.get(t, 0) + dollar_vol
                        volume_counts[t] = volume_counts.get(t, 0) + 1
                        last_close[t] = close

                    days_fetched += 1
                    logger.info(
                        f"Grouped bars for {date_str}: {len(results)} tickers "
                        f"({days_fetched}/{days} days)"
                    )
                else:
                    logger.warning(f"HTTP {response.status_code} for {date_str}")
                    days_fetched += 1
            except Exception as e:
                logger.warning(f"Failed for {date_str}: {e}")
                days_fetched += 1

            current -= timedelta(days=1)
            await asyncio.sleep(0.3)

    # Calculate averages
    avg_volumes = {}
    for t in volume_sums:
        if volume_counts.get(t, 0) >= days // 2:  # Need at least half the days
            avg_volumes[t] = volume_sums[t] / volume_counts[t]

    # Sort by avg dollar volume, take top N
    sorted_tickers = sorted(avg_volumes.items(), key=lambda x: x[1], reverse=True)
    return dict(sorted_tickers[:top_n])


async def check_optionability_batch(
    api_key: str,
    tickers: list[str],
    concurrency: int = 10,
) -> set[str]:
    """Check which tickers have options chains available.

    Makes a minimal options snapshot request (limit=1) per ticker.
    Returns set of tickers with available options.
    """
    optionable = set()
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        base_url=POLYGON_BASE,
        timeout=15.0,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:

        async def check_one(ticker: str) -> None:
            async with semaphore:
                try:
                    response = await client.get(
                        f"/v3/snapshot/options/{ticker}",
                        params={"limit": "1"},
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("results"):
                            optionable.add(ticker)
                except Exception:
                    pass

        batch_size = 50
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            await asyncio.gather(*(check_one(t) for t in batch))
            logger.info(
                f"Checked optionability: {min(i + batch_size, len(tickers))}/{len(tickers)} "
                f"({len(optionable)} optionable so far)"
            )
            await asyncio.sleep(0.5)

    return optionable


async def get_existing_sp500_tickers() -> set[str]:
    """Load the current S&P 500 tickers from DynamoDB."""
    from app.db.tables import SP500TickerTable

    tickers = await SP500TickerTable.get_active_tickers()
    return set(tickers)


def resolve_api_key() -> str:
    """Resolve Polygon API key from env, app config, or Secrets Manager."""
    api_key = os.environ.get("POLYGON_API_KEY", "")
    if api_key:
        return api_key

    try:
        from app.config import get_settings

        key = get_settings().polygon_api_key
        if key:
            return key
    except Exception:
        pass

    try:
        import boto3

        secret_arn = os.environ.get("POLYGON_SECRET_ARN", "")
        if secret_arn:
            client = boto3.client(
                "secretsmanager",
                region_name=os.environ.get("AWS_REGION", "us-west-1"),
            )
            resp = client.get_secret_value(SecretId=secret_arn)
            key = resp.get("SecretString", "")
            if key:
                logger.info("Loaded Polygon API key from Secrets Manager")
                return key
    except Exception as e:
        logger.warning(f"Failed to load from Secrets Manager: {e}")

    return ""


async def main(args: argparse.Namespace) -> None:
    api_key = resolve_api_key()
    if not api_key:
        logger.error(
            "No Polygon API key found. Set POLYGON_API_KEY, POLYGON_SECRET_ARN, "
            "or configure via app settings."
        )
        return

    # Step 1: Get existing S&P 500 tickers
    logger.info("Loading existing S&P 500 tickers from DynamoDB...")
    existing_sp500 = await get_existing_sp500_tickers()
    logger.info(f"Found {len(existing_sp500)} existing S&P 500 tickers")

    # Step 2: Discover top tickers by dollar volume from grouped daily bars
    logger.info("Discovering top US equities by dollar volume (20 trading days)...")
    avg_volumes = await discover_top_tickers_by_dollar_volume(
        api_key, days=20, min_price=5.0, top_n=1500
    )
    logger.info(f"Found {len(avg_volumes)} tickers above $5 with sufficient volume")

    # Step 3: Filter out ETFs and known non-stock tickers
    candidates = {t: v for t, v in avg_volumes.items() if t not in ETF_TICKERS}
    logger.info(f"After static ETF filter: {len(candidates)} candidates")

    # Step 3b: Use Polygon reference endpoint to filter out non-CS (common stock) types
    # This catches ETFs/ETNs/warrants we didn't have in our static list
    logger.info("Verifying ticker types via Polygon reference endpoint...")
    non_stocks = set()
    async with httpx.AsyncClient(
        base_url=POLYGON_BASE,
        timeout=30.0,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        # Fetch all active CS tickers to build a whitelist
        cs_tickers: set[str] = set()
        next_url = None
        while True:
            if next_url:
                response = await client.get(next_url)
            else:
                response = await client.get(
                    "/v3/reference/tickers",
                    params={
                        "market": "stocks",
                        "active": "true",
                        "type": "CS",
                        "limit": "1000",
                    },
                )
            if response.status_code != 200:
                logger.warning(f"Reference endpoint returned {response.status_code}, skipping type filter")
                break
            data = response.json()
            for t in data.get("results", []):
                cs_tickers.add(t.get("ticker", ""))
            next_url = data.get("next_url")
            if not next_url:
                break
            next_url = f"{next_url}&apiKey={api_key}"
            await asyncio.sleep(0.2)

        if cs_tickers:
            for t in list(candidates.keys()):
                if t not in cs_tickers:
                    non_stocks.add(t)
                    del candidates[t]
            logger.info(
                f"Verified against {len(cs_tickers)} common stocks; "
                f"removed {len(non_stocks)} non-CS tickers"
            )
        else:
            logger.warning("Could not load CS ticker list, skipping type filter")

    # Step 4: Check optionability
    if not args.skip_options_check:
        candidate_list = sorted(candidates.keys())
        logger.info(
            f"Checking optionability for {len(candidate_list)} tickers "
            "(this may take a few minutes)..."
        )
        optionable = await check_optionability_batch(api_key, candidate_list)
        logger.info(
            f"Found {len(optionable)} optionable tickers "
            f"out of {len(candidate_list)}"
        )
    else:
        logger.info("Skipping optionability check (--skip-options-check)")
        optionable = set(candidates.keys())

    # Step 5: Build the final universe — top 1000 optionable by dollar volume
    ranked = sorted(
        [(t, candidates[t]) for t in optionable if t in candidates],
        key=lambda x: x[1],
        reverse=True,
    )
    ranked = ranked[:1000]

    universe: list[dict] = []
    for ticker, adv in ranked:
        membership = []
        if ticker in existing_sp500:
            membership.append("sp500")
        membership.append("russell1000")

        universe.append({
            "ticker": ticker,
            "index_membership": membership,
            "has_options": True,
            "avg_dollar_volume": adv,
        })

    sp500_count = sum(1 for u in universe if "sp500" in u["index_membership"])
    new_count = len(universe) - sp500_count
    logger.info(
        f"Final universe: {len(universe)} tickers "
        f"({sp500_count} S&P 500, {new_count} new mid-cap)"
    )

    # Step 6: Write to DynamoDB (or dry-run)
    if args.dry_run:
        logger.info("DRY RUN — not writing to DynamoDB")
        logger.info("")
        logger.info("Top 30 tickers:")
        for u in universe[:30]:
            is_sp500 = "sp500" in u["index_membership"]
            tag = "S&P+R1K" if is_sp500 else "R1K new"
            logger.info(
                f"  {u['ticker']:6s} [{tag:8s}] "
                f"avg_dollar_vol=${u['avg_dollar_volume']/1e6:,.0f}M"
            )
        logger.info("")
        logger.info("Bottom 10 tickers (edge of universe):")
        for u in universe[-10:]:
            is_sp500 = "sp500" in u["index_membership"]
            tag = "S&P+R1K" if is_sp500 else "R1K new"
            logger.info(
                f"  {u['ticker']:6s} [{tag:8s}] "
                f"avg_dollar_vol=${u['avg_dollar_volume']/1e6:,.0f}M"
            )
        logger.info("")

        # Summary stats
        new_tickers = [u for u in universe if "sp500" not in u["index_membership"]]
        if new_tickers:
            min_vol = min(u["avg_dollar_volume"] for u in new_tickers)
            max_vol = max(u["avg_dollar_volume"] for u in new_tickers)
            logger.info(
                f"New tickers volume range: ${min_vol/1e6:,.0f}M - ${max_vol/1e6:,.0f}M"
            )
        return

    from app.db.tables import SP500TickerTable

    written = 0
    for u in universe:
        await SP500TickerTable.put_ticker(
            ticker=u["ticker"],
            sector="",
            is_active=True,
            index_membership=u["index_membership"],
            has_options=u["has_options"],
            avg_dollar_volume=u["avg_dollar_volume"],
        )
        written += 1
        if written % 100 == 0:
            logger.info(f"Written {written}/{len(universe)} tickers")

    logger.info(f"Done! Wrote {written} tickers to DynamoDB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Populate ticker universe table")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without writing to DynamoDB",
    )
    parser.add_argument(
        "--skip-options-check",
        action="store_true",
        help="Skip optionability check (faster but may include non-optionable tickers)",
    )
    args = parser.parse_args()
    asyncio.run(main(args))
