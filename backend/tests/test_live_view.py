"""Tests for app/paper_trading/live_view.py — Active Positions dashboard helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.schemas import PaperPosition, PositionStatus, Verdict
from app.paper_trading import live_view
from app.paper_trading.live_view import (
    LiveQuote,
    _clear_cache_for_tests,
    attention_flag,
    compute_summary,
    dollar_pnl_open,
    enrich_position,
    fetch_live_quotes,
    premium_at_risk,
    sl_progress_pct,
    tp_progress_pct,
)


def _position(
    *,
    pid: str = "pos-1",
    entry: float = 2.00,
    current: float = 2.50,
    qty: int = 1,
    tp: float | None = 50.0,
    sl: float | None = 25.0,
    scanner: str | None = "UNUSUAL_VOLUME",
    option_ticker: str = "O:AAPL260320C00185000",
    underlying: str = "AAPL",
    expiration: str = "2026-03-20",
) -> PaperPosition:
    return PaperPosition(
        position_id=pid,
        evaluation_id=f"eval-{pid}",
        option_ticker=option_ticker,
        entry_price=entry,
        entry_date="2026-04-01",
        quantity=qty,
        verdict_at_entry=Verdict.APPROVE,
        current_price=current,
        current_pnl_pct=round((current - entry) / entry * 100, 2) if entry else 0.0,
        max_favorable_excursion=0.0,
        max_adverse_excursion=0.0,
        days_held=5,
        status=PositionStatus.OPEN,
        last_updated=datetime.now(timezone.utc).isoformat(),
        underlying_ticker=underlying,
        scanner_source=scanner,
        expiration_date=expiration,
        thesis_tp1_pct=tp,
        thesis_sl_pct=sl,
    )


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------


class TestDollarPnl:
    def test_gain(self):
        assert dollar_pnl_open(2.00, 2.50, 1) == pytest.approx(50.0)

    def test_loss(self):
        assert dollar_pnl_open(3.00, 2.50, 2) == pytest.approx(-100.0)

    def test_zero(self):
        assert dollar_pnl_open(2.00, 2.00, 5) == pytest.approx(0.0)


class TestPremiumAtRisk:
    def test_basic(self):
        assert premium_at_risk(2.75, 2) == pytest.approx(550.0)


class TestTpProgress:
    def test_halfway(self):
        assert tp_progress_pct(25.0, 50.0) == pytest.approx(50.0)

    def test_at_target(self):
        assert tp_progress_pct(50.0, 50.0) == pytest.approx(100.0)

    def test_clamped_above_target(self):
        # Past target clamps to 100 (display-only; auto-close handles the actual exit)
        assert tp_progress_pct(80.0, 50.0) == pytest.approx(100.0)

    def test_negative_pnl_clamps_to_zero(self):
        assert tp_progress_pct(-10.0, 50.0) == pytest.approx(0.0)

    def test_none_when_no_thesis(self):
        assert tp_progress_pct(25.0, None) is None

    def test_none_when_zero_thesis(self):
        assert tp_progress_pct(25.0, 0.0) is None


class TestSlProgress:
    def test_halfway_to_stop(self):
        assert sl_progress_pct(-10.0, 20.0) == pytest.approx(50.0)

    def test_at_stop(self):
        assert sl_progress_pct(-20.0, 20.0) == pytest.approx(100.0)

    def test_positive_pnl_is_zero(self):
        assert sl_progress_pct(5.0, 20.0) == pytest.approx(0.0)

    def test_none_when_no_thesis(self):
        assert sl_progress_pct(-10.0, None) is None


# ---------------------------------------------------------------------------
# attention_flag — boundary cases matter, these are the UI triggers
# ---------------------------------------------------------------------------


class TestAttentionFlag:
    def test_nothing_when_pnl_neutral(self):
        assert attention_flag(5.0, 50.0, 25.0) is None

    def test_near_tp_at_threshold(self):
        # Exactly 80% of 50 == 40 → flag
        assert attention_flag(40.0, 50.0, 25.0) == "near_tp"

    def test_near_tp_above_threshold(self):
        assert attention_flag(45.0, 50.0, 25.0) == "near_tp"

    def test_near_tp_just_below_threshold(self):
        # 39.9 < 0.8 * 50 (= 40) → no flag
        assert attention_flag(39.9, 50.0, 25.0) is None

    def test_near_sl_at_threshold(self):
        # Exactly 75% of -25 == -18.75 → flag
        assert attention_flag(-18.75, 50.0, 25.0) == "near_sl"

    def test_near_sl_below_threshold(self):
        assert attention_flag(-22.0, 50.0, 25.0) == "near_sl"

    def test_near_sl_just_above_threshold(self):
        # -18.7 > -18.75 → no flag
        assert attention_flag(-18.7, 50.0, 25.0) is None

    def test_no_flag_when_thesis_missing(self):
        assert attention_flag(30.0, None, None) is None

    def test_tp_only_thesis(self):
        assert attention_flag(40.0, 50.0, None) == "near_tp"
        assert attention_flag(-40.0, 50.0, None) is None

    def test_sl_only_thesis(self):
        assert attention_flag(-20.0, None, 25.0) == "near_sl"
        assert attention_flag(40.0, None, 25.0) is None


# ---------------------------------------------------------------------------
# enrich_position
# ---------------------------------------------------------------------------


class TestEnrichPosition:
    def test_uses_live_quote_when_present(self):
        pos = _position(entry=2.00, current=2.40)
        quote = LiveQuote(bid=2.70, ask=2.90, mid=2.80, fetched_at="2026-04-21T15:00:00Z")
        row = enrich_position(pos, quote)
        assert row["current_price"] == pytest.approx(2.80)
        assert row["current_pnl_pct"] == pytest.approx(40.0)  # (2.80-2.00)/2.00
        assert row["dollar_pnl_open"] == pytest.approx(80.0)
        assert row["quote_source"] == "intraday"
        assert row["last_quote_at"] == "2026-04-21T15:00:00Z"

    def test_falls_back_to_persisted_price(self):
        pos = _position(entry=2.00, current=2.40)
        row = enrich_position(pos, None)
        assert row["current_price"] == pytest.approx(2.40)
        assert row["quote_source"] == "daily_batch"

    def test_near_tp_flag_flows_through(self):
        pos = _position(entry=2.00, current=3.00, tp=50.0, sl=25.0)  # +50% == at TP
        row = enrich_position(pos, None)
        assert row["attention_flag"] == "near_tp"
        assert row["tp_progress_pct"] == pytest.approx(100.0)

    def test_near_sl_flag_flows_through(self):
        pos = _position(entry=2.00, current=1.60, tp=50.0, sl=25.0)  # -20% (80% to SL)
        row = enrich_position(pos, None)
        assert row["attention_flag"] == "near_sl"
        assert row["sl_progress_pct"] == pytest.approx(80.0)

    def test_thesis_missing_no_progress(self):
        pos = _position(tp=None, sl=None)
        row = enrich_position(pos, None)
        assert row["tp_progress_pct"] is None
        assert row["sl_progress_pct"] is None
        assert row["attention_flag"] is None

    def test_scanner_suffix_normalized(self):
        pos = _position(scanner="UNUSUAL_VOLUME_SCANNER")
        row = enrich_position(pos, None)
        assert row["scanner_source"] == "UNUSUAL_VOLUME"

    def test_premium_at_risk_scales_with_qty(self):
        pos = _position(entry=2.00, qty=3)
        row = enrich_position(pos, None)
        assert row["premium_at_risk"] == pytest.approx(600.0)


# ---------------------------------------------------------------------------
# compute_summary
# ---------------------------------------------------------------------------


class TestComputeSummary:
    def test_empty(self):
        s = compute_summary([])
        assert s["open_count"] == 0
        assert s["dollar_pnl_open_total"] == 0.0
        assert s["attention_count"] == 0
        assert s["last_updated"] is None

    def test_aggregates_pnl_and_risk(self):
        winner = enrich_position(_position(pid="w", entry=2.00, current=3.00), None)
        loser = enrich_position(_position(pid="l", entry=4.00, current=3.00), None)
        s = compute_summary([winner, loser])
        assert s["open_count"] == 2
        # winner: +$100, loser: -$100 → book is flat
        assert s["dollar_pnl_open_total"] == pytest.approx(0.0)
        assert s["premium_at_risk_total"] == pytest.approx(600.0)  # 200 + 400

    def test_counts_attention(self):
        near_tp = enrich_position(_position(pid="t", entry=2.00, current=3.00), None)
        near_sl = enrich_position(_position(pid="s", entry=2.00, current=1.60), None)
        quiet = enrich_position(_position(pid="q", entry=2.00, current=2.10), None)
        s = compute_summary([near_tp, near_sl, quiet])
        assert s["near_tp_count"] == 1
        assert s["near_sl_count"] == 1
        assert s["attention_count"] == 2

    def test_weighted_pnl_pct_uses_premium_weight(self):
        # Small losing, large winning → weighted % should favor the large one.
        small_loss = enrich_position(
            _position(pid="a", entry=1.00, current=0.50, qty=1), None
        )  # premium 100, pnl -50
        big_win = enrich_position(
            _position(pid="b", entry=10.00, current=11.00, qty=1), None
        )  # premium 1000, pnl +100
        s = compute_summary([small_loss, big_win])
        # Total pnl = +50, total premium at risk = 1100 → weighted ≈ +4.55%
        assert s["pnl_pct_weighted"] == pytest.approx(50 / 1100 * 100, abs=0.01)


# ---------------------------------------------------------------------------
# fetch_live_quotes — cache behavior + one fetch path
# ---------------------------------------------------------------------------


class TestFetchLiveQuotes:
    def setup_method(self):
        _clear_cache_for_tests()

    @pytest.mark.asyncio
    async def test_empty_positions_short_circuits(self):
        quotes = await fetch_live_quotes([])
        assert quotes == {}

    @pytest.mark.asyncio
    async def test_fetches_and_caches(self):
        pos = _position()
        # One contract in the chain that matches the position's option_ticker.
        fake_chain = [
            {
                "details": {"ticker": pos.option_ticker},
                "last_quote": {"bid": 2.70, "ask": 2.90},
                "day": {"close": 2.75},
            }
        ]
        mock_client = AsyncMock()
        mock_client.get_options_chain_minimal = AsyncMock(return_value=fake_chain)

        class _Ctx:
            async def __aenter__(self):
                return mock_client

            async def __aexit__(self, *args):
                return False

        with patch.object(live_view, "PolygonClient", return_value=_Ctx()):
            first = await fetch_live_quotes([pos])
            assert pos.option_ticker in first
            assert first[pos.option_ticker].mid == pytest.approx(2.80)

            # Second call should hit the in-memory cache — no second API call.
            second = await fetch_live_quotes([pos])
            assert second[pos.option_ticker].mid == pytest.approx(2.80)
            assert mock_client.get_options_chain_minimal.await_count == 1

    @pytest.mark.asyncio
    async def test_missing_contract_returns_no_quote(self):
        pos = _position()
        mock_client = AsyncMock()
        mock_client.get_options_chain_minimal = AsyncMock(return_value=[])

        class _Ctx:
            async def __aenter__(self):
                return mock_client

            async def __aexit__(self, *args):
                return False

        with patch.object(live_view, "PolygonClient", return_value=_Ctx()):
            quotes = await fetch_live_quotes([pos])
        assert quotes == {}
