"""Refresh live quote fields on open APPROVE evaluations.

Called once per pipeline run. For each open APPROVE evaluation within the
last trading day, fetches the current Polygon quote for the exact
option_ticker and writes current_bid/current_ask/current_mid/quote_refreshed_at
back onto the Evaluation row. Entry-time bid/ask/mid are left intact — those
are the immutable decision snapshot.

Why this exists: an APPROVE row is written once at decision time and its
`mid` value is frozen. The Opportunities page read-path displays that frozen
value, so after any material market move the displayed premium drifts away
from reality. The REVALIDATION scanner rewrites evaluations for some
APPROVEs, but only when the thesis still holds and the contract is the same
— which isn't guaranteed after a large move. This step is the cheap,
always-correct fallback: it never changes verdict or contract selection, it
just keeps the displayed prices accurate.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.api.routes.market import get_market_status
from app.core.time_utils import trading_days_cutoff
from app.db.tables import EvaluationTable
from app.paper_trading.position_manager import extract_underlying_from_option_ticker
from app.services.polygon import PolygonClient

logger = logging.getLogger(__name__)


async def refresh_open_approve_quotes(
    max_age_trading_days: int = 1,
) -> dict[str, int]:
    """Refresh current_mid/bid/ask on every open APPROVE within the window.

    Returns a small stats dict for logging and tests:
        {"refreshed": N, "missing": M, "errors": E, "skipped": S}
    - refreshed: rows successfully updated
    - missing: APPROVE whose contract wasn't found in the refreshed chain
    - errors: per-row update failures (non-fatal)
    - skipped: total APPROVE evaluated but skipped (e.g. market closed)
    """
    stats = {"refreshed": 0, "missing": 0, "errors": 0, "skipped": 0}

    # Skip entirely when market is closed — Polygon quotes won't move.
    if get_market_status() == "closed":
        logger.info("refresh_open_approve_quotes: market closed, skipping")
        return stats

    try:
        cutoff_iso = trading_days_cutoff(max_age_trading_days)
        items = await EvaluationTable.list_by_verdict_since(
            "APPROVE", cutoff_iso, limit=500
        )
    except Exception as e:
        logger.warning(f"refresh_open_approve_quotes: list_by_verdict_since failed: {e}")
        return stats

    # Dedup by option_ticker keeping the newest evaluation per contract.
    # list_by_verdict_since returns newest-first, so first occurrence wins.
    by_contract: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item.get("option_ticker", "")
        if key and key not in by_contract:
            by_contract[key] = item

    if not by_contract:
        return stats

    # Group OCC symbols by (underlying, expiration_date) for efficient batching.
    # A single expiry's chain comfortably fits in the 250-result page of
    # get_options_chain_minimal, whereas a full un-filtered chain for a popular
    # underlying spans thousands of contracts and our specific OCC symbol can
    # fall off page 1. Each eval carries its own expiration_date, so we group.
    by_underlying_expiry: dict[tuple[str, str], list[str]] = {}
    for option_ticker, item in by_contract.items():
        underlying = extract_underlying_from_option_ticker(option_ticker)
        expiry = item.get("expiration_date", "")
        if not underlying or not expiry:
            continue
        by_underlying_expiry.setdefault((underlying, expiry), []).append(option_ticker)

    now_iso = datetime.now(timezone.utc).isoformat()
    # Map option_ticker -> (bid, ask, mid); built concurrently from chain fetches.
    quotes: dict[str, tuple[float, float, float]] = {}

    try:
        async with PolygonClient() as client:
            async def fetch_chain(
                underlying: str, expiry: str, requested: list[str]
            ) -> None:
                try:
                    chain = await client.get_options_chain_minimal(
                        underlying,
                        expiration_date_gte=expiry,
                        expiration_date_lte=expiry,
                    )
                    lookup = {c.get("ticker"): c for c in chain if c.get("ticker")}
                    for option_ticker in requested:
                        contract = lookup.get(option_ticker)
                        if not contract:
                            continue
                        last_quote = contract.get("last_quote") or {}
                        day = contract.get("day") or {}
                        bid = float(last_quote.get("bid") or day.get("close") or 0) or 0.0
                        ask = float(last_quote.get("ask") or 0) or 0.0
                        if bid and ask:
                            mid = (bid + ask) / 2
                        else:
                            mid = bid or ask or 0.0
                        quotes[option_ticker] = (bid, ask, round(mid, 4))
                except Exception as e:
                    logger.warning(
                        f"refresh_open_approve_quotes: chain fetch failed for "
                        f"{underlying} {expiry}: {e}"
                    )

            await asyncio.gather(
                *(
                    fetch_chain(underlying, expiry, tickers)
                    for (underlying, expiry), tickers in by_underlying_expiry.items()
                )
            )
    except Exception as e:
        logger.warning(f"refresh_open_approve_quotes: Polygon client failed: {e}")
        return stats

    # Write quote fields back to each evaluation row.
    update_coros = []
    for option_ticker, item in by_contract.items():
        quote = quotes.get(option_ticker)
        if quote is None:
            stats["missing"] += 1
            continue
        bid, ask, mid = quote
        ticker = item.get("underlying_ticker", "")
        evaluated_at = item.get("evaluated_at", "")
        evaluation_id = item.get("evaluation_id", "")
        if not (ticker and evaluated_at and evaluation_id):
            stats["errors"] += 1
            continue
        update_coros.append(
            _safe_update(ticker, evaluated_at, evaluation_id, bid, ask, mid, now_iso, stats)
        )

    await asyncio.gather(*update_coros)
    logger.info(
        f"refresh_open_approve_quotes: refreshed={stats['refreshed']} "
        f"missing={stats['missing']} errors={stats['errors']}"
    )
    return stats


async def _safe_update(
    ticker: str,
    evaluated_at: str,
    evaluation_id: str,
    bid: float,
    ask: float,
    mid: float,
    now_iso: str,
    stats: dict[str, int],
) -> None:
    """Update one row, counting errors without raising."""
    try:
        await EvaluationTable.update_current_quote(
            ticker, evaluated_at, evaluation_id, bid, ask, mid, now_iso
        )
        stats["refreshed"] += 1
    except Exception as e:
        logger.warning(
            f"refresh_open_approve_quotes: update failed for {ticker} {evaluation_id}: {e}"
        )
        stats["errors"] += 1
