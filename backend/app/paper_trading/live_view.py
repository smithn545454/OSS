"""Pure helpers shared by the live-trades dashboard.

The Active Trades dashboard (``app/real_trades/live_view.py``) is the sole
consumer of these helpers. They live here because they depend only on
PaperPosition and PolygonClient — both of which belong conceptually to the
paper-trading module — and keeping them here avoids a circular import.

What's here:
- ``fetch_live_quotes(positions)`` — per-(underlying, expiry) Polygon quote
  refresh with a 5-minute in-memory cache.
- ``dollar_pnl_open`` / ``premium_at_risk`` — dollars from price × qty × 100.
- ``tp_progress_pct`` / ``sl_progress_pct`` — 0–100 scalars for UI bars.
- ``attention_flag`` — near_tp / near_sl / None classifier.

The daily batch updater in ``batch_updater.py`` still owns the persistent
price/PnL/MFE updates and auto-close logic. Nothing here writes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.schemas import PaperPosition
from app.paper_trading.position_manager import (
    extract_expiration_from_option_ticker,
    extract_underlying_from_option_ticker,
)
from app.services.polygon import PolygonClient

logger = logging.getLogger(__name__)

# Urgency thresholds (see docs/active_positions_dashboard.md / plan Phase 3).
# Near-TP: once current P&L reaches 80% of the thesis TP target.
# Near-SL: once current P&L has consumed 75% of the thesis SL cushion.
NEAR_TP_FRACTION = 0.8
NEAR_SL_FRACTION = 0.75

# One option contract = 100 shares. Dollar P&L on a long option:
# (current - entry) * quantity * 100.
CONTRACT_MULTIPLIER = 100

# In-memory per-Lambda-container cache of (underlying, expiry) -> (ts, chain map).
# Lambda warm-start preserves this; cold starts repopulate on demand.
_CHAIN_CACHE_TTL_SECONDS = 300  # 5 minutes
_chain_cache: dict[tuple[str, str], tuple[float, dict[str, dict[str, Any]]]] = {}


@dataclass(frozen=True)
class LiveQuote:
    """Intraday quote for a single option contract."""

    bid: float
    ask: float
    mid: float
    fetched_at: str  # ISO timestamp


# ---------------------------------------------------------------------------
# Quote fetching with a 5-minute per-(underlying, expiry) cache
# ---------------------------------------------------------------------------


def _cache_get(
    underlying: str, expiry: str, now: float
) -> Optional[dict[str, dict[str, Any]]]:
    entry = _chain_cache.get((underlying, expiry))
    if not entry:
        return None
    ts, lookup = entry
    if now - ts > _CHAIN_CACHE_TTL_SECONDS:
        return None
    return lookup


def _cache_put(
    underlying: str, expiry: str, lookup: dict[str, dict[str, Any]], now: float
) -> None:
    _chain_cache[(underlying, expiry)] = (now, lookup)


def _clear_cache_for_tests() -> None:
    """Drop the in-memory chain cache. Only used from tests."""
    _chain_cache.clear()


def _chain_to_lookup(chain: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Polygon returns the OCC symbol under details.ticker (top-level ticker
    is only on stock snapshots). Build a flat OCC-symbol keyed lookup."""
    lookup: dict[str, dict[str, Any]] = {}
    for c in chain:
        details = c.get("details") or {}
        t = details.get("ticker") or c.get("ticker")
        if t:
            lookup[t] = c
    return lookup


def _quote_from_contract(
    contract: dict[str, Any], fetched_at: str
) -> Optional[LiveQuote]:
    last_quote = contract.get("last_quote") or {}
    day = contract.get("day") or {}
    bid = float(last_quote.get("bid") or day.get("close") or 0) or 0.0
    ask = float(last_quote.get("ask") or 0) or 0.0
    if bid and ask:
        mid = (bid + ask) / 2
    else:
        mid = bid or ask or 0.0
    if mid <= 0:
        return None
    return LiveQuote(bid=bid, ask=ask, mid=round(mid, 4), fetched_at=fetched_at)


async def fetch_live_quotes(
    positions: list[PaperPosition],
    client: Optional[PolygonClient] = None,
) -> dict[str, LiveQuote]:
    """Return a {option_ticker: LiveQuote} map for the given positions.

    Uses a 5-minute in-memory cache per (underlying, expiration). If
    ``client`` is provided, it must already be entered as an async context
    manager (a fresh one is created otherwise).
    """
    if not positions:
        return {}

    now_epoch = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Group positions by (underlying, expiry) so one chain fetch covers many.
    by_key: dict[tuple[str, str], list[str]] = {}
    expiry_by_ticker: dict[str, str] = {}
    for pos in positions:
        underlying = (
            pos.underlying_ticker
            or extract_underlying_from_option_ticker(pos.option_ticker)
        )
        expiry = (
            pos.expiration_date
            or extract_expiration_from_option_ticker(pos.option_ticker)
        )
        if not underlying or not expiry:
            continue
        expiry_by_ticker[pos.option_ticker] = expiry
        by_key.setdefault((underlying, expiry), []).append(pos.option_ticker)

    # Split into cached-hit and needs-fetch groups.
    resolved: dict[str, dict[str, Any]] = {}  # option_ticker -> contract dict
    to_fetch: list[tuple[str, str]] = []
    for key in by_key.keys():
        cached = _cache_get(key[0], key[1], now_epoch)
        if cached is not None:
            for ot in by_key[key]:
                # OCC may or may not have the "O:" prefix depending on source
                contract = cached.get(ot) or cached.get(f"O:{ot}")
                if contract is not None:
                    resolved[ot] = contract
        else:
            to_fetch.append(key)

    async def _fetch_one(
        active_client: PolygonClient, underlying: str, expiry: str
    ) -> None:
        try:
            chain = await active_client.get_options_chain_minimal(
                underlying,
                expiration_date_gte=expiry,
                expiration_date_lte=expiry,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "live_view: chain fetch failed for %s %s: %s",
                underlying, expiry, e,
            )
            return
        lookup = _chain_to_lookup(chain)
        _cache_put(underlying, expiry, lookup, now_epoch)
        for ot in by_key[(underlying, expiry)]:
            contract = lookup.get(ot) or lookup.get(f"O:{ot}")
            if contract is not None:
                resolved[ot] = contract

    if to_fetch:
        if client is not None:
            await asyncio.gather(
                *(_fetch_one(client, u, e) for (u, e) in to_fetch)
            )
        else:
            async with PolygonClient() as fresh:
                await asyncio.gather(
                    *(_fetch_one(fresh, u, e) for (u, e) in to_fetch)
                )

    # Convert contract snapshots into LiveQuote objects.
    quotes: dict[str, LiveQuote] = {}
    for option_ticker, contract in resolved.items():
        quote = _quote_from_contract(contract, now_iso)
        if quote is not None:
            quotes[option_ticker] = quote
    return quotes


# ---------------------------------------------------------------------------
# Derived fields
# ---------------------------------------------------------------------------


def dollar_pnl_open(
    entry_price: float, current_price: float, quantity: int
) -> float:
    """P&L in dollars for an open long option.

    (current - entry) * qty * 100. Returned as a plain float (not rounded);
    callers round for display.
    """
    return (current_price - entry_price) * quantity * CONTRACT_MULTIPLIER


def premium_at_risk(entry_price: float, quantity: int) -> float:
    """Dollars paid at entry — the maximum a long option can lose."""
    return entry_price * quantity * CONTRACT_MULTIPLIER


def tp_progress_pct(
    current_pnl_pct: float, thesis_tp1_pct: Optional[float]
) -> Optional[float]:
    """Progress toward the thesis take-profit target, 0–100.

    Returns None when no thesis TP is set. Clamped to [0, 100]; negative
    P&L maps to 0% progress, past-target maps to 100%.
    """
    if thesis_tp1_pct is None or thesis_tp1_pct <= 0:
        return None
    ratio = current_pnl_pct / thesis_tp1_pct
    return max(0.0, min(100.0, ratio * 100.0))


def sl_progress_pct(
    current_pnl_pct: float, thesis_sl_pct: Optional[float]
) -> Optional[float]:
    """Progress from breakeven toward the thesis stop-loss, 0–100.

    `thesis_sl_pct` is stored as a positive magnitude (e.g. 15 for a -15%
    stop). Returns None when no thesis SL is set. Clamped to [0, 100];
    positive P&L maps to 0%, at-stop maps to 100%.
    """
    if thesis_sl_pct is None or thesis_sl_pct <= 0:
        return None
    ratio = -current_pnl_pct / thesis_sl_pct
    return max(0.0, min(100.0, ratio * 100.0))


def attention_flag(
    current_pnl_pct: float,
    thesis_tp1_pct: Optional[float],
    thesis_sl_pct: Optional[float],
) -> Optional[str]:
    """Classify a position as near_tp / near_sl / None.

    near_tp:  current_pnl >= 0.8 * thesis_tp1_pct (requires thesis TP set)
    near_sl:  current_pnl <= -0.75 * thesis_sl_pct (requires thesis SL set);
              if current has already crossed the stop, the batch updater
              will have auto-closed it, but we still flag as near_sl until
              that happens.

    near_sl wins over near_tp on the rare occasion both would trigger
    (which can't really happen — same position can't be in both zones
    simultaneously — but the defensive ordering matches how you'd want
    the UI to draw attention).
    """
    if thesis_sl_pct is not None and thesis_sl_pct > 0:
        if current_pnl_pct <= -NEAR_SL_FRACTION * thesis_sl_pct:
            return "near_sl"
    if thesis_tp1_pct is not None and thesis_tp1_pct > 0:
        if current_pnl_pct >= NEAR_TP_FRACTION * thesis_tp1_pct:
            return "near_tp"
    return None


