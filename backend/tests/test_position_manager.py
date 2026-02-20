"""Tests for position_manager.py.

Covers create_position_from_evaluation, create_positions_from_decisions,
extract_underlying_from_option_ticker, extract_expiration_from_option_ticker.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    Decision,
    DTEBucket,
    Evaluation,
    OptionType,
    PaperPosition,
    QualityTier,
    Verdict,
)
from app.paper_trading.position_manager import (
    create_position_from_evaluation,
    create_positions_from_decisions,
    extract_underlying_from_option_ticker,
    extract_expiration_from_option_ticker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _eval_obj(eval_id="eval-1"):
    return Evaluation(
        evaluation_id=eval_id,
        opportunity_id="opp-1",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL260320C00185000",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=30, strike=185.0, underlying_price=180.0, moneyness_pct=2.78,
        bid=4.0, ask=4.5, mid=4.25, spread_abs=0.5, spread_pct=11.76,
        iv=0.30, delta=0.50, gamma=0.03, theta=-0.08, vega=0.25,
        open_interest=500, volume=200, breakeven_price=189.25,
        required_move_pct=5.14, expected_move_pct=8.0,
        feasibility_ratio=0.64, time_adjusted_feasibility=0.80,
        dte_bucket=DTEBucket.B, rank_score=75.0,
        policy_version="v2.0.0", policy_hash="hash",
    )


def _decision(eval_id="eval-1", verdict=Verdict.APPROVE, tier=None):
    return Decision(
        evaluation_id=eval_id,
        verdict=verdict,
        final_score=85.0,
        directional_score=80.0,
        volatility_score=80.0,
        structure_score=80.0,
        primary_reason_code="APPROVED",
        supporting_reason_codes=[],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v2.0.0",
        quality_tier=tier,
    )


# ---------------------------------------------------------------------------
# Tests: create_position_from_evaluation
# ---------------------------------------------------------------------------


class TestCreatePositionFromEvaluation:

    @pytest.mark.asyncio
    async def test_creates_position(self):
        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table, \
             patch("app.paper_trading.metrics_aggregator.MetricsAggregator.on_position_opened",
                   new_callable=AsyncMock):
            mock_table.get_by_evaluation_id = AsyncMock(return_value=None)
            mock_table.has_open_position = AsyncMock(return_value=False)
            mock_table.put = AsyncMock()

            result = await create_position_from_evaluation(
                _eval_obj(), _decision()
            )

        assert result is not None
        assert isinstance(result, PaperPosition)
        assert result.entry_price == 4.25
        mock_table.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_duplicate(self):
        existing = MagicMock()
        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.get_by_evaluation_id = AsyncMock(return_value=existing)

            result = await create_position_from_evaluation(
                _eval_obj(), _decision()
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_with_quality_tier(self):
        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table, \
             patch("app.paper_trading.metrics_aggregator.MetricsAggregator.on_position_opened",
                   new_callable=AsyncMock):
            mock_table.get_by_evaluation_id = AsyncMock(return_value=None)
            mock_table.has_open_position = AsyncMock(return_value=False)
            mock_table.put = AsyncMock()

            result = await create_position_from_evaluation(
                _eval_obj(), _decision(tier=QualityTier.TIER_1)
            )

        assert result is not None


# ---------------------------------------------------------------------------
# Tests: create_positions_from_decisions
# ---------------------------------------------------------------------------


class TestCreatePositionsFromDecisions:

    @pytest.mark.asyncio
    async def test_creates_for_approve_and_watch(self):
        evals = [_eval_obj("e1"), _eval_obj("e2"), _eval_obj("e3")]
        decisions = {
            "e1": _decision("e1", Verdict.APPROVE),
            "e2": _decision("e2", Verdict.WATCH),
            "e3": _decision("e3", Verdict.REJECT),
        }

        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table, \
             patch("app.paper_trading.metrics_aggregator.MetricsAggregator.on_position_opened",
                   new_callable=AsyncMock):
            mock_table.get_by_evaluation_id = AsyncMock(return_value=None)
            mock_table.has_open_position = AsyncMock(return_value=False)
            mock_table.put = AsyncMock()

            result = await create_positions_from_decisions(evals, decisions)

        # Should create positions for APPROVE and WATCH, not REJECT
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_decisions(self):
        result = await create_positions_from_decisions([], {})
        assert result == []


# ---------------------------------------------------------------------------
# Tests: extract_underlying_from_option_ticker
# ---------------------------------------------------------------------------


class TestExtractUnderlying:

    def test_standard_ticker(self):
        result = extract_underlying_from_option_ticker("O:AAPL260320C00185000")
        assert result == "AAPL"

    def test_without_prefix(self):
        result = extract_underlying_from_option_ticker("AAPL260320C00185000")
        assert result == "AAPL"

    def test_short_ticker(self):
        result = extract_underlying_from_option_ticker("O:A260320C00050000")
        assert result is not None

    def test_empty_string(self):
        result = extract_underlying_from_option_ticker("")
        assert result is not None or result is None  # Should not crash


# ---------------------------------------------------------------------------
# Tests: extract_expiration_from_option_ticker
# ---------------------------------------------------------------------------


class TestExtractExpiration:

    def test_standard_ticker(self):
        result = extract_expiration_from_option_ticker("O:AAPL260320C00185000")
        assert result is not None
        assert "2026" in result or "26" in result

    def test_without_prefix(self):
        result = extract_expiration_from_option_ticker("AAPL260320C00185000")
        assert result is not None
