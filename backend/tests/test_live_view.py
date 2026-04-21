"""Tests for pure helpers in app/paper_trading/live_view.py.

Dashboard enrichment tests live in test_real_trades_live_view.py — this
file only exercises the math helpers and the Polygon quote cache.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.schemas import PaperPosition, PositionStatus, Verdict
from app.paper_trading import live_view
from app.paper_trading.live_view import (
    _clear_cache_for_tests,
    attention_flag,
    dollar_pnl_open,
    fetch_live_quotes,
    premium_at_risk,
    sl_progress_pct,
    tp_progress_pct,
)


def _position(
    *,
    option_ticker: str = "O:AAPL260320C00185000",
    underlying: str = "AAPL",
    expiration: str = "2026-03-20",
) -> PaperPosition:
    return PaperPosition(
        evaluation_id="eval-1",
        option_ticker=option_ticker,
        entry_price=2.00,
        entry_date="2026-04-01",
        quantity=1,
        verdict_at_entry=Verdict.APPROVE,
        current_price=2.50,
        current_pnl_pct=25.0,
        max_favorable_excursion=30.0,
        max_adverse_excursion=-5.0,
        days_held=5,
        status=PositionStatus.OPEN,
        last_updated=datetime.now(timezone.utc).isoformat(),
        underlying_ticker=underlying,
        expiration_date=expiration,
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
        assert tp_progress_pct(80.0, 50.0) == pytest.approx(100.0)

    def test_negative_pnl_clamps_to_zero(self):
        assert tp_progress_pct(-10.0, 50.0) == pytest.approx(0.0)

    def test_none_when_no_thesis(self):
        assert tp_progress_pct(25.0, None) is None


class TestSlProgress:
    def test_halfway_to_stop(self):
        assert sl_progress_pct(-10.0, 20.0) == pytest.approx(50.0)

    def test_at_stop(self):
        assert sl_progress_pct(-20.0, 20.0) == pytest.approx(100.0)

    def test_positive_pnl_is_zero(self):
        assert sl_progress_pct(5.0, 20.0) == pytest.approx(0.0)

    def test_none_when_no_thesis(self):
        assert sl_progress_pct(-10.0, None) is None


class TestAttentionFlag:
    def test_nothing_when_pnl_neutral(self):
        assert attention_flag(5.0, 50.0, 25.0) is None

    def test_near_tp_at_threshold(self):
        assert attention_flag(40.0, 50.0, 25.0) == "near_tp"

    def test_near_tp_just_below_threshold(self):
        assert attention_flag(39.9, 50.0, 25.0) is None

    def test_near_sl_at_threshold(self):
        assert attention_flag(-18.75, 50.0, 25.0) == "near_sl"

    def test_near_sl_just_above_threshold(self):
        assert attention_flag(-18.7, 50.0, 25.0) is None

    def test_no_flag_when_thesis_missing(self):
        assert attention_flag(30.0, None, None) is None


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

            # Second call hits the in-memory cache — no second API call.
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
