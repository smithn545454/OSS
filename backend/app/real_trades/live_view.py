"""Live Active-Trades view.

Powers the Active Trades dashboard on the My Trades page.

Enriches each open RealTrade with:
- Live option quote (intraday via Polygon, cached per (underlying, expiry)).
- Thesis TP/SL thresholds from the paired PaperPosition (open or closed).
- Derived fields: $ P&L against the user's fill, TP/SL progress, attention flag.

Every open RealTrade is guaranteed to have a matching PaperPosition (enforced
at POST /api/trades). The join uses PaperPositionTable.get_by_evaluation_id,
which queries GSI1 and returns either the OPEN or CLOSED partition — we
prefer open, but fall back to closed with a `paper_position_status` flag so
the UI can warn when the system has auto-closed the paper position.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.schemas import PaperPosition
from app.paper_trading.live_view import (
    LiveQuote,
    attention_flag,
    dollar_pnl_open,
    premium_at_risk,
    sl_progress_pct,
    tp_progress_pct,
)
from app.paper_trading.position_manager import (
    extract_expiration_from_option_ticker,
    extract_underlying_from_option_ticker,
)

logger = logging.getLogger(__name__)


def _days_held(tracked_at: str) -> int:
    """Number of calendar days since the user tracked this trade."""
    try:
        tracked = datetime.fromisoformat(tracked_at.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return 0
    now = datetime.now(timezone.utc)
    return max(0, (now - tracked).days)


def _current_dte(expiration_date: Optional[str]) -> Optional[int]:
    if not expiration_date:
        return None
    try:
        from app.paper_trading.exit_checker import calculate_dte_from_expiration
        return calculate_dte_from_expiration(expiration_date)
    except Exception:  # noqa: BLE001
        return None


def _normalize_scanner(scanner: Optional[str]) -> Optional[str]:
    if scanner and scanner.endswith("_SCANNER"):
        return scanner[: -len("_SCANNER")]
    return scanner


def enrich_trade(
    real_trade: dict[str, Any],
    paper: Optional[PaperPosition],
    live_quote: Optional[LiveQuote],
) -> dict[str, Any]:
    """Build a dashboard row from a RealTrade + optional PaperPosition + optional quote."""
    snapshot = real_trade.get("snapshot") or {}
    entry_price = float(real_trade.get("entry_price") or 0)
    quantity = int(real_trade.get("quantity") or 1)

    # Current price precedence: live intraday quote > paper daily-batch > snapshot mid.
    if live_quote is not None:
        current_price = live_quote.mid
        quote_source = "intraday"
        last_quote_at = live_quote.fetched_at
    elif paper is not None and paper.current_price > 0:
        current_price = paper.current_price
        quote_source = "daily_batch"
        last_quote_at = paper.last_updated
    else:
        current_price = float(snapshot.get("mid") or snapshot.get("ask") or entry_price)
        quote_source = "snapshot"
        last_quote_at = snapshot.get("evaluated_at") or real_trade.get("tracked_at")

    # P&L against the user's fill (not the paper position's entry).
    if entry_price > 0:
        current_pnl_pct = (current_price - entry_price) / entry_price * 100
    else:
        current_pnl_pct = 0.0

    dollar_pnl = dollar_pnl_open(entry_price, current_price, quantity)
    risk = premium_at_risk(entry_price, quantity)

    # Thesis thresholds come from the paired paper position. None triggers
    # "thesis pending" in the UI; the attention_flag handles null safely.
    thesis_tp1 = paper.thesis_tp1_pct if paper else None
    thesis_sl = paper.thesis_sl_pct if paper else None
    thesis_time_exit_dte = paper.thesis_time_exit_dte if paper else None

    flag = attention_flag(current_pnl_pct, thesis_tp1, thesis_sl)

    option_ticker = snapshot.get("option_ticker") or ""
    underlying = snapshot.get("underlying_ticker") or (
        extract_underlying_from_option_ticker(option_ticker) if option_ticker else None
    )
    expiration = snapshot.get("expiration_date") or (
        extract_expiration_from_option_ticker(option_ticker) if option_ticker else None
    )

    # If paper is CLOSED, surface the system's exit — user hasn't closed
    # the real trade yet, so there's an inconsistency worth flagging in the UI.
    paper_status = None
    paper_exit_price = None
    paper_exit_reason = None
    paper_exit_date = None
    if paper is not None:
        paper_status = (
            paper.status.value if hasattr(paper.status, "value") else str(paper.status)
        )
        if paper_status == "CLOSED":
            paper_exit_price = paper.exit_price
            paper_exit_reason = (
                paper.exit_reason.value
                if paper.exit_reason and hasattr(paper.exit_reason, "value")
                else paper.exit_reason
            )
            paper_exit_date = paper.exit_date

    return {
        "trade_id": real_trade.get("trade_id"),
        "tracked_at": real_trade.get("tracked_at"),
        "trader": real_trade.get("trader"),
        "entry_notes": real_trade.get("entry_notes"),
        "evaluation_id": snapshot.get("evaluation_id"),
        "option_ticker": option_ticker,
        "underlying_ticker": underlying,
        "option_type": snapshot.get("option_type"),
        "strike": snapshot.get("strike"),
        "expiration_date": expiration,
        "dte": _current_dte(expiration),
        "days_held": _days_held(real_trade.get("tracked_at") or ""),
        "quantity": quantity,
        "entry_price": entry_price,
        "current_price": round(current_price, 4),
        "current_pnl_pct": round(current_pnl_pct, 2),
        "dollar_pnl_open": round(dollar_pnl, 2),
        "premium_at_risk": round(risk, 2),
        "max_favorable_excursion": round(paper.max_favorable_excursion, 2) if paper else None,
        "max_adverse_excursion": round(paper.max_adverse_excursion, 2) if paper else None,
        "scanner_source": _normalize_scanner(snapshot.get("scanner_source")),
        "verdict_at_entry": snapshot.get("verdict"),
        "conviction_score": snapshot.get("final_score"),
        "thesis_tp1_pct": thesis_tp1,
        "thesis_sl_pct": thesis_sl,
        "thesis_time_exit_dte": thesis_time_exit_dte,
        "tp_progress_pct": tp_progress_pct(current_pnl_pct, thesis_tp1),
        "sl_progress_pct": sl_progress_pct(current_pnl_pct, thesis_sl),
        "attention_flag": flag,
        "last_quote_at": last_quote_at,
        "quote_source": quote_source,
        "paper_position_id": paper.position_id if paper else None,
        "paper_position_status": paper_status,
        "paper_exit_price": paper_exit_price,
        "paper_exit_reason": paper_exit_reason,
        "paper_exit_date": paper_exit_date,
    }


def compute_summary(enriched: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate enriched trades into the portfolio header."""
    if not enriched:
        return {
            "open_count": 0,
            "dollar_pnl_open_total": 0.0,
            "premium_at_risk_total": 0.0,
            "pnl_pct_weighted": 0.0,
            "attention_count": 0,
            "near_tp_count": 0,
            "near_sl_count": 0,
            "paper_closed_count": 0,
            "last_updated": None,
            "quote_sources": {"intraday": 0, "daily_batch": 0, "snapshot": 0},
        }

    dollar_pnl_total = sum(float(p["dollar_pnl_open"]) for p in enriched)
    risk_total = sum(float(p["premium_at_risk"]) for p in enriched)
    near_tp = sum(1 for p in enriched if p["attention_flag"] == "near_tp")
    near_sl = sum(1 for p in enriched if p["attention_flag"] == "near_sl")
    paper_closed = sum(1 for p in enriched if p["paper_position_status"] == "CLOSED")

    pnl_pct_weighted = (
        dollar_pnl_total / risk_total * 100 if risk_total > 0 else 0.0
    )

    last_updated = max(
        (p.get("last_quote_at") for p in enriched if p.get("last_quote_at")),
        default=None,
    )

    sources: dict[str, int] = {"intraday": 0, "daily_batch": 0, "snapshot": 0}
    for p in enriched:
        src = p.get("quote_source") or "snapshot"
        sources[src] = sources.get(src, 0) + 1

    return {
        "open_count": len(enriched),
        "dollar_pnl_open_total": round(dollar_pnl_total, 2),
        "premium_at_risk_total": round(risk_total, 2),
        "pnl_pct_weighted": round(pnl_pct_weighted, 2),
        "attention_count": near_tp + near_sl,
        "near_tp_count": near_tp,
        "near_sl_count": near_sl,
        "paper_closed_count": paper_closed,
        "last_updated": last_updated,
        "quote_sources": sources,
    }
