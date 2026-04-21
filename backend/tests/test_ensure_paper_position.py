"""Tests for ensure_paper_position_for_real_trade — the invariant that every
RealTrade has a matching PaperPosition reachable by evaluation_id."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import PaperPosition, PositionStatus, Verdict
from app.paper_trading.position_manager import (
    _build_paper_position_from_snapshot,
    ensure_paper_position_for_real_trade,
)


def _snapshot(
    *,
    evaluation_id: str = "eval-1",
    option_ticker: str = "O:AAPL260320C00185000",
    ask: float = 4.50,
    mid: float = 4.25,
    verdict: str = "APPROVE",
    scanner_source: str = "UNUSUAL_VOLUME",
) -> dict:
    return {
        "evaluation_id": evaluation_id,
        "opportunity_id": "opp-1",
        "underlying_ticker": "AAPL",
        "option_ticker": option_ticker,
        "option_type": "CALL",
        "strike": 185.0,
        "expiration_date": "2026-03-20",
        "dte": 30,
        "dte_bucket": "B",
        "underlying_price": 180.0,
        "moneyness_pct": 2.78,
        "bid": 4.0, "ask": ask, "mid": mid,
        "spread_abs": 0.5, "spread_pct": 11.0,
        "delta": 0.50, "gamma": 0.03, "theta": -0.08, "vega": 0.25,
        "iv": 0.30,
        "open_interest": 500, "volume": 200,
        "breakeven_price": 189.25,
        "required_move_pct": 5.14, "expected_move_pct": 8.0,
        "feasibility_ratio": 0.64, "time_adjusted_feasibility": 0.80,
        "rank_score": 75.0,
        "policy_version": "v4.1.1", "policy_hash": "hash",
        "scanner_source": scanner_source,
        "evaluated_at": "2026-04-21T12:00:00Z",
        "verdict": verdict,
        "quality_tier": "TIER_1",
        "final_score": 85.0,
        "directional_conviction_score": 80.0,
        "move_potential_score": 75.0,
        "trade_structure_score": 82.0,
        "primary_reason_code": "APPROVED",
        "supporting_reason_codes": [],
        "failed_gates": [],
        "concentration_warnings": [],
    }


def _real_trade(evaluation_id: str = "eval-1") -> dict:
    return {
        "trade_id": "trade-1",
        "entry_price": 4.25,
        "quantity": 1,
        "trader": "Nick",
        "status": "OPEN",
        "tracked_at": "2026-04-21T12:05:00Z",
        "snapshot": _snapshot(evaluation_id=evaluation_id),
    }


class TestBuildFromSnapshot:
    def test_uses_ask_as_entry_price(self):
        snap = _snapshot(ask=4.50, mid=4.25)
        pos = _build_paper_position_from_snapshot(snap)
        assert pos.entry_price == pytest.approx(4.50)

    def test_falls_back_to_mid_when_ask_missing(self):
        snap = _snapshot(ask=0, mid=4.25)
        pos = _build_paper_position_from_snapshot(snap)
        assert pos.entry_price == pytest.approx(4.25)

    def test_normalizes_scanner_suffix(self):
        snap = _snapshot(scanner_source="UNUSUAL_VOLUME_SCANNER")
        pos = _build_paper_position_from_snapshot(snap)
        assert pos.scanner_source == "UNUSUAL_VOLUME"

    def test_accepts_reject_verdict(self):
        """Pipeline enrolment skips REJECTs but users can track them —
        the synth path must not gate on verdict."""
        snap = _snapshot(verdict="REJECT")
        pos = _build_paper_position_from_snapshot(snap)
        assert pos.verdict_at_entry == Verdict.REJECT

    def test_carries_v4_pillar_scores(self):
        snap = _snapshot()
        pos = _build_paper_position_from_snapshot(snap)
        assert pos.pillar_directional_conviction == pytest.approx(80.0)
        assert pos.pillar_move_potential == pytest.approx(75.0)
        assert pos.pillar_trade_structure == pytest.approx(82.0)

    def test_carries_greeks(self):
        snap = _snapshot()
        pos = _build_paper_position_from_snapshot(snap)
        assert pos.entry_delta == pytest.approx(0.50)
        assert pos.entry_iv == pytest.approx(0.30)
        assert pos.entry_theta == pytest.approx(-0.08)

    def test_starts_open_with_zero_pnl(self):
        snap = _snapshot()
        pos = _build_paper_position_from_snapshot(snap)
        assert pos.status == PositionStatus.OPEN
        assert pos.current_pnl_pct == 0.0
        assert pos.max_favorable_excursion == 0.0
        assert pos.days_held == 0


class TestEnsurePaperPosition:
    @pytest.mark.asyncio
    async def test_returns_existing_open_position_unchanged(self):
        existing = PaperPosition(
            evaluation_id="eval-1",
            option_ticker="O:AAPL260320C00185000",
            entry_price=4.50,
            entry_date="2026-04-01",
            quantity=1,
            verdict_at_entry=Verdict.APPROVE,
            current_price=5.10,
            current_pnl_pct=13.33,
            status=PositionStatus.OPEN,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
        with patch(
            "app.paper_trading.position_manager.PaperPositionTable.get_by_evaluation_id",
            new=AsyncMock(return_value=existing),
        ), patch(
            "app.paper_trading.position_manager.PaperPositionTable.put",
            new=AsyncMock(),
        ) as put_mock:
            result = await ensure_paper_position_for_real_trade(_real_trade())

        assert result is existing
        put_mock.assert_not_awaited()  # didn't write; already existed

    @pytest.mark.asyncio
    async def test_synthesizes_when_missing_with_fresh_quote(self):
        mock_client = MagicMock()
        mock_client.get_options_chain_minimal = AsyncMock(
            return_value=[
                {
                    "details": {"ticker": "O:AAPL260320C00185000"},
                    "last_quote": {"bid": 5.00, "ask": 5.20},
                }
            ]
        )

        with patch(
            "app.paper_trading.position_manager.PaperPositionTable.get_by_evaluation_id",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.paper_trading.position_manager.PaperPositionTable.put",
            new=AsyncMock(),
        ) as put_mock:
            result = await ensure_paper_position_for_real_trade(
                _real_trade(), polygon_client=mock_client
            )

        assert result is not None
        # Entry price = snapshot.ask (not the Polygon quote, which seeds current)
        assert result.entry_price == pytest.approx(4.50)
        # Current price = mid of fresh Polygon quote = (5.00 + 5.20) / 2
        assert result.current_price == pytest.approx(5.10)
        # P&L derived from entry vs refreshed current
        assert result.current_pnl_pct == pytest.approx(
            (5.10 - 4.50) / 4.50 * 100, abs=0.01
        )
        put_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_keeps_seed_current_when_polygon_fails(self):
        mock_client = MagicMock()
        mock_client.get_options_chain_minimal = AsyncMock(return_value=[])  # no match

        with patch(
            "app.paper_trading.position_manager.PaperPositionTable.get_by_evaluation_id",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.paper_trading.position_manager.PaperPositionTable.put",
            new=AsyncMock(),
        ):
            result = await ensure_paper_position_for_real_trade(
                _real_trade(), polygon_client=mock_client
            )

        assert result is not None
        # Falls back to seed (entry_price) since no quote was found
        assert result.current_price == pytest.approx(4.50)
        assert result.current_pnl_pct == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_no_op_when_snapshot_lacks_evaluation_id(self):
        trade = _real_trade()
        trade["snapshot"]["evaluation_id"] = ""
        with patch(
            "app.paper_trading.position_manager.PaperPositionTable.get_by_evaluation_id",
            new=AsyncMock(return_value=None),
        ) as lookup:
            result = await ensure_paper_position_for_real_trade(trade)
        assert result is None
        lookup.assert_not_awaited()  # bailed before lookup

    @pytest.mark.asyncio
    async def test_works_for_reject_verdict(self):
        """Tracking a REJECT evaluation should still synth a paper position."""
        trade = _real_trade()
        trade["snapshot"]["verdict"] = "REJECT"
        mock_client = MagicMock()
        mock_client.get_options_chain_minimal = AsyncMock(return_value=[])

        with patch(
            "app.paper_trading.position_manager.PaperPositionTable.get_by_evaluation_id",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.paper_trading.position_manager.PaperPositionTable.put",
            new=AsyncMock(),
        ) as put_mock:
            result = await ensure_paper_position_for_real_trade(
                trade, polygon_client=mock_client
            )

        assert result is not None
        assert result.verdict_at_entry == Verdict.REJECT
        put_mock.assert_awaited_once()
