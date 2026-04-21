"""Tests for app/real_trades/live_view.py — Active Trades dashboard enrichment."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.schemas import ExitReason, PaperPosition, PositionStatus, Verdict
from app.paper_trading.live_view import LiveQuote
from app.real_trades.live_view import compute_summary, enrich_trade


def _snapshot(
    *,
    evaluation_id: str = "eval-1",
    option_ticker: str = "O:AAPL260320C00185000",
    underlying: str = "AAPL",
    option_type: str = "CALL",
    strike: float = 185.0,
    expiration: str = "2026-03-20",
    scanner_source: str = "UNUSUAL_VOLUME",
    verdict: str = "APPROVE",
    final_score: float = 85.0,
    mid: float = 4.25,
    ask: float = 4.50,
) -> dict:
    return {
        "evaluation_id": evaluation_id,
        "option_ticker": option_ticker,
        "underlying_ticker": underlying,
        "option_type": option_type,
        "strike": strike,
        "expiration_date": expiration,
        "dte": 30,
        "mid": mid,
        "ask": ask,
        "scanner_source": scanner_source,
        "verdict": verdict,
        "final_score": final_score,
    }


def _real_trade(
    *,
    trade_id: str = "trade-1",
    entry: float = 4.50,
    qty: int = 1,
    tracked_at: str = "2026-04-21T12:00:00Z",
    snapshot: dict | None = None,
) -> dict:
    return {
        "trade_id": trade_id,
        "entry_price": entry,
        "quantity": qty,
        "trader": "Nick",
        "entry_notes": None,
        "status": "OPEN",
        "tracked_at": tracked_at,
        "snapshot": snapshot or _snapshot(),
    }


def _paper(
    *,
    tp: float | None = 50.0,
    sl: float | None = 25.0,
    current_price: float = 5.20,
    status: PositionStatus = PositionStatus.OPEN,
    exit_price: float | None = None,
    exit_reason: ExitReason | None = None,
    exit_date: str | None = None,
) -> PaperPosition:
    return PaperPosition(
        evaluation_id="eval-1",
        option_ticker="O:AAPL260320C00185000",
        entry_price=4.50,
        entry_date="2026-04-21",
        quantity=1,
        verdict_at_entry=Verdict.APPROVE,
        current_price=current_price,
        current_pnl_pct=round((current_price - 4.50) / 4.50 * 100, 2),
        max_favorable_excursion=18.0,
        max_adverse_excursion=-3.0,
        days_held=1,
        status=status,
        last_updated=datetime.now(timezone.utc).isoformat(),
        underlying_ticker="AAPL",
        scanner_source="UNUSUAL_VOLUME",
        expiration_date="2026-03-20",
        thesis_tp1_pct=tp,
        thesis_sl_pct=sl,
        thesis_time_exit_dte=7,
        exit_price=exit_price,
        exit_reason=exit_reason,
        exit_date=exit_date,
    )


class TestEnrichTrade:
    def test_dollar_pnl_uses_real_trade_entry_not_paper(self):
        """P&L must reflect the user's fill price, not the paper position's."""
        trade = _real_trade(entry=4.00, qty=2)  # user got a better fill than eval.ask
        paper = _paper(current_price=5.00)  # paper has entry=4.50
        row = enrich_trade(trade, paper, None)
        # (5.00 - 4.00) * 2 * 100 = 200
        assert row["dollar_pnl_open"] == pytest.approx(200.0)
        # (5.00 - 4.00) / 4.00 * 100 = 25.0
        assert row["current_pnl_pct"] == pytest.approx(25.0)
        # Premium at risk from user's fill, not the paper's
        assert row["premium_at_risk"] == pytest.approx(800.0)  # 4.00 * 2 * 100

    def test_live_quote_overrides_paper_current_price(self):
        trade = _real_trade(entry=4.50)
        paper = _paper(current_price=5.00)
        quote = LiveQuote(bid=5.80, ask=6.00, mid=5.90, fetched_at="2026-04-21T15:00Z")
        row = enrich_trade(trade, paper, quote)
        assert row["current_price"] == pytest.approx(5.90)
        assert row["quote_source"] == "intraday"

    def test_falls_back_to_paper_when_no_live_quote(self):
        trade = _real_trade(entry=4.50)
        paper = _paper(current_price=5.00)
        row = enrich_trade(trade, paper, None)
        assert row["current_price"] == pytest.approx(5.00)
        assert row["quote_source"] == "daily_batch"

    def test_falls_back_to_snapshot_when_no_paper(self):
        trade = _real_trade(entry=4.50, snapshot=_snapshot(mid=4.60))
        row = enrich_trade(trade, None, None)
        assert row["current_price"] == pytest.approx(4.60)
        assert row["quote_source"] == "snapshot"
        # Without paper, no thesis thresholds
        assert row["thesis_tp1_pct"] is None
        assert row["attention_flag"] is None

    def test_attention_flag_uses_paper_thesis(self):
        # User fill at 4.50, current 6.75 → +50% (at TP)
        trade = _real_trade(entry=4.50)
        paper = _paper(tp=50.0, sl=25.0, current_price=6.75)
        row = enrich_trade(trade, paper, None)
        assert row["attention_flag"] == "near_tp"

    def test_near_sl_when_user_fill_different_from_paper(self):
        # User fill $4.00 vs paper entry $4.50; current 3.10 → -22.5% from user fill
        trade = _real_trade(entry=4.00)
        paper = _paper(tp=50.0, sl=25.0, current_price=3.10)
        row = enrich_trade(trade, paper, None)
        # -22.5% vs -0.75 * 25 = -18.75 → should trigger near_sl
        assert row["attention_flag"] == "near_sl"

    def test_surfaces_paper_closed_fields(self):
        trade = _real_trade(entry=4.50)
        paper = _paper(
            current_price=0.10,
            status=PositionStatus.CLOSED,
            exit_price=0.10,
            exit_reason=ExitReason.STOP_LOSS,
            exit_date="2026-04-20",
        )
        row = enrich_trade(trade, paper, None)
        assert row["paper_position_status"] == "CLOSED"
        assert row["paper_exit_price"] == pytest.approx(0.10)
        assert row["paper_exit_reason"] == "STOP_LOSS"
        assert row["paper_exit_date"] == "2026-04-20"

    def test_normalizes_scanner_suffix(self):
        trade = _real_trade(snapshot=_snapshot(scanner_source="UNUSUAL_VOLUME_SCANNER"))
        row = enrich_trade(trade, None, None)
        assert row["scanner_source"] == "UNUSUAL_VOLUME"

    def test_preserves_trade_identifier_fields(self):
        trade = _real_trade(trade_id="t-xyz", tracked_at="2026-04-21T12:00:00Z")
        row = enrich_trade(trade, None, None)
        assert row["trade_id"] == "t-xyz"
        assert row["tracked_at"] == "2026-04-21T12:00:00Z"
        assert row["trader"] == "Nick"


class TestComputeSummary:
    def test_empty(self):
        s = compute_summary([])
        assert s["open_count"] == 0
        assert s["dollar_pnl_open_total"] == 0.0
        assert s["attention_count"] == 0
        assert s["paper_closed_count"] == 0

    def test_aggregates_pnl(self):
        winner = enrich_trade(
            _real_trade(trade_id="w", entry=4.00),
            _paper(current_price=5.00),
            None,
        )
        loser = enrich_trade(
            _real_trade(trade_id="l", entry=4.00),
            _paper(current_price=3.00),
            None,
        )
        s = compute_summary([winner, loser])
        assert s["open_count"] == 2
        # +100 - 100 = 0
        assert s["dollar_pnl_open_total"] == pytest.approx(0.0)
        assert s["premium_at_risk_total"] == pytest.approx(800.0)

    def test_counts_paper_closed(self):
        closed = enrich_trade(
            _real_trade(),
            _paper(status=PositionStatus.CLOSED, exit_price=0.10),
            None,
        )
        open_ = enrich_trade(_real_trade(trade_id="o"), _paper(), None)
        s = compute_summary([closed, open_])
        assert s["paper_closed_count"] == 1
