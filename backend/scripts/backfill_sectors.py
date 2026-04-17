#!/usr/bin/env python3
"""Backfill GICS sector classifications on oss-dev-sp500-tickers.

Phase 1 audit found ~95% of the combined S&P 500 + Russell 1000
universe had missing or "Unknown" sector values. Pillar v4's
``sector_rs_20d`` subscore needs a real sector so it can map to
an SPDR sector ETF (XLK, XLF, XLV, ...).

This script walks the combined universe, fetches each ticker's
``finnhubIndustry`` via Finnhub ``/stock/profile2``, normalizes
it to a key ``relative_strength.SECTOR_ETF_MAP`` recognises, and
updates the ticker row in DynamoDB.

Rate limit: Finnhub free tier is 60 req/min; the client
self-throttles at 30 req/min. ~1,500 calls → ~50 minutes.

Usage
-----
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
        FINNHUB_API_KEY=... python scripts/backfill_sectors.py

    # Only refresh tickers whose sector is empty or "Unknown"
    python scripts/backfill_sectors.py --only-missing

    # Dry-run a small sample to verify mapping
    python scripts/backfill_sectors.py --dry-run --tickers AAPL,MSFT,TSLA,XOM,AMD

    # Restrict to a single universe
    python scripts/backfill_sectors.py --universe russell1000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("AWS_REGION", "us-west-1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# Map Finnhub's `finnhubIndustry` labels to the 11 canonical GICS
# sectors our SECTOR_ETF_MAP recognises. Finnhub returns granular
# industry labels (e.g. "Automobiles", "Semiconductors") rather than
# broad sectors, so this table is intentionally exhaustive — anything
# not here falls through and is logged so we can extend the map.
FINNHUB_TO_GICS: dict[str, str] = {
    # ---------- Canonical / direct matches ----------
    "Technology": "Technology",
    "Financial Services": "Financials",
    "Financials": "Financials",
    "Healthcare": "Healthcare",
    "Health Care": "Healthcare",
    "Energy": "Energy",
    "Industrials": "Industrials",
    "Consumer Defensive": "Consumer Staples",
    "Consumer Staples": "Consumer Staples",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Discretionary": "Consumer Discretionary",
    "Utilities": "Utilities",
    "Basic Materials": "Materials",
    "Materials": "Materials",
    "Real Estate": "Real Estate",
    "Communication Services": "Communication Services",

    # ---------- Technology industry-level labels ----------
    "Software": "Technology",
    "Semiconductors": "Technology",
    "Hardware": "Technology",
    "Technology Hardware": "Technology",
    "Electronic Equipment": "Technology",
    "IT Services": "Technology",
    "Information Technology Services": "Technology",
    "Computer Services": "Technology",
    "Computers": "Technology",
    "Electrical Components & Equipment": "Technology",

    # ---------- Financials ----------
    "Finance": "Financials",
    "Banking": "Financials",
    "Banks": "Financials",
    "Insurance": "Financials",
    "Diversified Financial Services": "Financials",
    "Capital Markets": "Financials",
    "Asset Management": "Financials",
    "Investment Banks & Brokerages": "Financials",
    "Consumer Finance": "Financials",
    "Mortgage Finance": "Financials",

    # ---------- Healthcare ----------
    "Pharmaceuticals": "Healthcare",
    "Biotechnology": "Healthcare",
    "Medical Devices & Supplies": "Healthcare",
    "Healthcare Equipment": "Healthcare",
    "Healthcare Providers & Services": "Healthcare",
    "Health Care Providers & Services": "Healthcare",
    "Life Sciences Tools & Services": "Healthcare",
    "Medical Diagnostics": "Healthcare",
    "Medical Care": "Healthcare",

    # ---------- Energy ----------
    "Oil & Gas": "Energy",
    "Oil & Gas - Integrated": "Energy",
    "Oil & Gas Integrated": "Energy",
    "Oil, Gas & Consumable Fuels": "Energy",
    "Oil & Gas Drilling": "Energy",
    "Oil & Gas E&P": "Energy",
    "Oil & Gas Equipment & Services": "Energy",
    "Oil & Gas Refining": "Energy",
    "Oil & Gas Refining & Marketing": "Energy",
    "Oil & Gas Midstream": "Energy",
    "Coal": "Energy",
    "Renewable Energy": "Energy",

    # ---------- Industrials ----------
    "Machinery": "Industrials",
    "Aerospace & Defense": "Industrials",
    "Airlines": "Industrials",
    "Transportation": "Industrials",
    "Road & Rail": "Industrials",
    "Trucking": "Industrials",
    "Marine": "Industrials",
    "Air Freight & Logistics": "Industrials",
    "Construction & Engineering": "Industrials",
    "Construction": "Industrials",
    "Building Products": "Industrials",
    "Industrial Conglomerates": "Industrials",
    "Commercial Services & Supplies": "Industrials",
    "Professional Services": "Industrials",
    "Electrical Equipment": "Industrials",
    "Trading Companies & Distributors": "Industrials",
    "Diversified Industrials": "Industrials",

    # ---------- Consumer Staples ----------
    "Beverages": "Consumer Staples",
    "Food Products": "Consumer Staples",
    "Food & Staples Retailing": "Consumer Staples",
    "Food Distribution": "Consumer Staples",
    "Tobacco": "Consumer Staples",
    "Household Products": "Consumer Staples",
    "Personal Products": "Consumer Staples",
    "Packaged Foods": "Consumer Staples",
    "Consumer products": "Consumer Staples",
    "Consumer Products": "Consumer Staples",
    "Agriculture": "Consumer Staples",

    # ---------- Consumer Discretionary ----------
    "Automobiles": "Consumer Discretionary",
    "Auto Components": "Consumer Discretionary",
    "Auto Parts": "Consumer Discretionary",
    "Apparel": "Consumer Discretionary",
    "Textiles, Apparel & Luxury Goods": "Consumer Discretionary",
    "Hotels": "Consumer Discretionary",
    "Hotels, Restaurants & Leisure": "Consumer Discretionary",
    "Restaurants": "Consumer Discretionary",
    "Retail": "Consumer Discretionary",
    "Specialty Retail": "Consumer Discretionary",
    "Internet Retail": "Consumer Discretionary",
    "Household Durables": "Consumer Discretionary",
    "Leisure Products": "Consumer Discretionary",
    "Leisure Facilities": "Consumer Discretionary",
    "Homebuilding": "Consumer Discretionary",
    "Distributors": "Consumer Discretionary",
    "Education Services": "Consumer Discretionary",
    "Gambling & Casinos": "Consumer Discretionary",

    # ---------- Utilities ----------
    "Electric Utilities": "Utilities",
    "Multi-Utilities": "Utilities",
    "Gas Utilities": "Utilities",
    "Water Utilities": "Utilities",
    "Independent Power Producers": "Utilities",

    # ---------- Materials ----------
    "Chemicals": "Materials",
    "Metals & Mining": "Materials",
    "Metals and Mining": "Materials",
    "Gold": "Materials",
    "Silver": "Materials",
    "Copper": "Materials",
    "Steel": "Materials",
    "Paper & Forest Products": "Materials",
    "Construction Materials": "Materials",
    "Containers & Packaging": "Materials",

    # ---------- Real Estate ----------
    "REIT": "Real Estate",
    "REIT - Residential": "Real Estate",
    "REIT - Retail": "Real Estate",
    "REIT - Office": "Real Estate",
    "REIT - Industrial": "Real Estate",
    "REIT - Healthcare": "Real Estate",
    "REIT - Hotel & Motel": "Real Estate",
    "REIT - Diversified": "Real Estate",
    "REIT - Specialty": "Real Estate",
    "Real Estate Management & Development": "Real Estate",
    "Real Estate - Residential": "Real Estate",
    "Real Estate - Commercial": "Real Estate",

    # ---------- Communication Services ----------
    "Media": "Communication Services",
    "Entertainment": "Communication Services",
    "Interactive Media": "Communication Services",
    "Interactive Media & Services": "Communication Services",
    "Internet": "Communication Services",
    "Internet Content & Information": "Communication Services",
    "Publishing": "Communication Services",
    "Broadcasting": "Communication Services",
    "Cable & Satellite": "Communication Services",
    "Telecommunication": "Communication Services",
    "Telecommunications": "Communication Services",
    "Telecommunications Services": "Communication Services",
    "Diversified Telecommunication Services": "Communication Services",
    "Wireless Telecommunication Services": "Communication Services",
}


def normalize_sector(finnhub_label: Optional[str]) -> Optional[str]:
    """Return the GICS-canonical sector for a Finnhub label, or None."""
    if not finnhub_label:
        return None
    stripped = finnhub_label.strip()
    if stripped in FINNHUB_TO_GICS:
        return FINNHUB_TO_GICS[stripped]
    # Fall back: preserve verbatim so we can spot unknown labels in the
    # coverage report. Downstream SECTOR_ETF_MAP lookup will miss, but
    # at least the data is queryable.
    return stripped or None


async def resolve_universe(
    universes: list[str], explicit_tickers: Optional[list[str]]
) -> list[str]:
    if explicit_tickers:
        return sorted({t.upper() for t in explicit_tickers})
    from app.db.tables import SP500TickerTable

    seen: set[str] = set()
    for universe in universes:
        tickers = await SP500TickerTable.get_tickers_by_universe(universe)
        logger.info(f"Universe {universe}: {len(tickers)} tickers")
        seen.update(tickers)
    return sorted(seen)


async def fetch_current_rows(tickers: list[str]) -> dict[str, dict]:
    """Load existing ticker rows so we can skip ones that already have a
    real sector (when ``--only-missing`` is set) and preserve all other
    fields on update.
    """
    from app.db.dynamodb import get_dynamodb
    from app.db.tables import SP500_TICKERS_TABLE

    db = get_dynamodb()
    all_items = await db.query(
        SP500_TICKERS_TABLE, "TICKER_LIST", limit=None, scan_forward=True
    )
    return {
        item["ticker"]: item
        for item in all_items
        if item.get("ticker") in set(tickers)
    }


async def update_sector(ticker: str, sector: str, row: dict) -> None:
    """Persist the new sector using ``SP500TickerTable.put_ticker`` so
    all existing fields (is_active, index_membership, has_options,
    avg_dollar_volume) survive the overwrite.
    """
    from app.db.tables import SP500TickerTable

    avg_dv = row.get("avg_dollar_volume")
    if avg_dv is not None:
        try:
            avg_dv = float(avg_dv)
        except (TypeError, ValueError):
            avg_dv = None

    await SP500TickerTable.put_ticker(
        ticker=ticker,
        sector=sector,
        is_active=bool(row.get("is_active", True)),
        index_membership=list(row.get("index_membership") or ["sp500"]),
        has_options=bool(row.get("has_options", True)),
        avg_dollar_volume=avg_dv,
    )


async def backfill(
    tickers: list[str], *, only_missing: bool, dry_run: bool
) -> dict[str, object]:
    from app.config import get_settings
    from app.services.finnhub import FinnhubClient

    settings = get_settings()
    api_key = os.environ.get("FINNHUB_API_KEY") or getattr(
        settings, "finnhub_api_key", None
    )
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY not set")

    rows = await fetch_current_rows(tickers)

    if only_missing:
        work = [
            t for t in tickers
            if not rows.get(t, {}).get("sector")
            or rows.get(t, {}).get("sector") in ("Unknown", "")
        ]
        logger.info(
            f"--only-missing: {len(work)}/{len(tickers)} tickers need a sector"
        )
    else:
        work = tickers

    attempted = 0
    updated = 0
    unchanged = 0
    failed: list[str] = []
    unknown_labels: dict[str, int] = {}

    async with FinnhubClient(api_key) as finnhub:
        started = time.time()
        for i, ticker in enumerate(work, 1):
            attempted += 1
            try:
                profile = await finnhub.get_company_profile(ticker)
            except Exception as e:  # pragma: no cover — defensive
                failed.append(f"{ticker}: {e}")
                continue

            raw = (profile or {}).get("finnhubIndustry")
            normalized = normalize_sector(raw)

            if not normalized:
                failed.append(f"{ticker}: no sector from Finnhub")
                continue

            # Record anything that didn't map to a known GICS key so we
            # can review the FINNHUB_TO_GICS table afterward.
            from app.features.relative_strength import SECTOR_ETF_MAP

            if normalized not in SECTOR_ETF_MAP:
                unknown_labels[normalized] = unknown_labels.get(normalized, 0) + 1

            current = rows.get(ticker, {}).get("sector")
            if current == normalized:
                unchanged += 1
                continue

            if dry_run:
                logger.info(
                    f"[{i}/{len(work)}] {ticker}: "
                    f"{current!r} -> {normalized!r} (dry-run)"
                )
            else:
                try:
                    await update_sector(ticker, normalized, rows.get(ticker, {}))
                    updated += 1
                except Exception as e:  # pragma: no cover — defensive
                    failed.append(f"{ticker}: update failed: {e}")
                    continue

            if i % 50 == 0 or i == len(work):
                elapsed = time.time() - started
                rate = i / elapsed if elapsed else 0.0
                logger.info(
                    f"[{i}/{len(work)}] updated={updated} "
                    f"unchanged={unchanged} fail={len(failed)} "
                    f"({rate:.2f}/s)"
                )

    return {
        "tickers_considered": len(tickers),
        "tickers_attempted": attempted,
        "updated": updated,
        "unchanged": unchanged,
        "failed": len(failed),
        "first_failures": failed[:20],
        "unknown_labels": unknown_labels,
    }


async def main_async(args: argparse.Namespace) -> int:
    tickers = await resolve_universe(
        universes=args.universe,
        explicit_tickers=args.tickers.split(",") if args.tickers else None,
    )
    if not tickers:
        logger.error("No tickers to backfill")
        return 2

    logger.info(
        f"Backfilling sectors for {len(tickers)} tickers "
        f"(only_missing={args.only_missing}, dry_run={args.dry_run})"
    )

    report = await backfill(
        tickers, only_missing=args.only_missing, dry_run=args.dry_run
    )

    logger.info("=" * 60)
    logger.info("SECTOR BACKFILL COMPLETE")
    logger.info(f"  tickers considered: {report['tickers_considered']}")
    logger.info(f"  tickers attempted:  {report['tickers_attempted']}")
    logger.info(f"  updated:            {report['updated']}")
    logger.info(f"  unchanged:          {report['unchanged']}")
    logger.info(f"  failed:             {report['failed']}")
    if report["first_failures"]:  # type: ignore[truthy-bool]
        logger.warning("First failures:")
        for f in report["first_failures"]:  # type: ignore[union-attr]
            logger.warning(f"  {f}")
    if report["unknown_labels"]:  # type: ignore[truthy-bool]
        logger.warning(
            "Sectors NOT in SECTOR_ETF_MAP (review FINNHUB_TO_GICS table):"
        )
        for label, n in sorted(
            report["unknown_labels"].items(),  # type: ignore[union-attr]
            key=lambda x: -x[1],
        ):
            logger.warning(f"  {label!r:40s} {n}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe",
        action="append",
        choices=["sp500", "russell1000"],
        help="Universe(s) to backfill. Repeatable. Defaults to both.",
    )
    parser.add_argument(
        "--tickers",
        help="Comma-separated ticker list. Overrides --universe when set.",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Skip tickers that already have a real (not empty/Unknown) sector.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch from Finnhub but do NOT write to DynamoDB.",
    )
    args = parser.parse_args()
    if not args.universe and not args.tickers:
        args.universe = ["sp500", "russell1000"]
    elif args.universe is None:
        args.universe = []
    return args


if __name__ == "__main__":
    args = parse_args()
    sys.exit(asyncio.run(main_async(args)))
