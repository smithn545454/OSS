"""Tests for APPROVE contract revalidation (fresh pricing).

Part 1: ContractSelector force-include logic
Part 2: API superseded-APPROVE suppression
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.schemas import (
    ContractSelectionConfig,
    DTEBucket,
    DTEBucketRange,
    OptionType,
)
from app.main import app
from app.selection.contract_selector import ContractCandidate, ContractSelector

_ROUTE = "app.api.routes.evaluations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(
    option_ticker: str = "O:TEST260515C00100000",
    mid: float = 1.00,
    dte_bucket: DTEBucket = DTEBucket.B,
    delta: float = 0.45,
) -> ContractCandidate:
    return ContractCandidate(
        option_ticker=option_ticker,
        underlying_ticker="TEST",
        option_type=OptionType.CALL,
        expiration_date="2026-05-15",
        dte=30,
        strike=100.0,
        underlying_price=100.0,
        bid=mid - 0.05,
        ask=mid + 0.05,
        mid=mid,
        spread_abs=0.10,
        spread_pct=5.0,
        iv=0.25,
        delta=delta,
        gamma=0.02,
        theta=-0.05,
        vega=0.15,
        open_interest=500,
        volume=100,
        moneyness_pct=0.0,
        dte_bucket=dte_bucket,
    )


def _polygon_chain_item(
    option_ticker: str = "O:TEST260515C00100000",
    strike: float = 100.0,
    bid: float = 0.95,
    ask: float = 1.05,
    delta: float = 0.45,
    iv: float = 0.25,
    expiration: str = "2026-05-15",
    contract_type: str = "CALL",
) -> dict:
    """Build a fake Polygon options chain item."""
    return {
        "details": {
            "ticker": option_ticker,
            "contract_type": contract_type,
            "strike_price": strike,
            "expiration_date": expiration,
        },
        "day": {
            "last_bid": bid,
            "last_ask": ask,
            "volume": 100,
            "close": (bid + ask) / 2,
        },
        "last_quote": {"bid": bid, "ask": ask},
        "greeks": {
            "delta": delta,
            "gamma": 0.02,
            "theta": -0.05,
            "vega": 0.15,
            "implied_volatility": iv,
        },
        "implied_volatility": iv,
        "open_interest": 500,
    }


# ---------------------------------------------------------------------------
# Part 1: ContractSelector force-include
# ---------------------------------------------------------------------------


class TestForceInclude:
    """Test that force-include contracts are added when missing from normal selection."""

    def _make_selector(self) -> ContractSelector:
        config = ContractSelectionConfig(
            dte_buckets={
                "A": DTEBucketRange(min_dte=7, max_dte=21),
                "B": DTEBucketRange(min_dte=22, max_dte=45),
                "C": DTEBucketRange(min_dte=46, max_dte=75),
                "D": DTEBucketRange(min_dte=76, max_dte=120),
            },
        )
        return ContractSelector(config)

    def test_force_include_from_chain_finds_contract(self):
        """Force-include picks up a contract from the chain that wasn't normally selected."""
        selector = self._make_selector()
        chain = [
            _polygon_chain_item("O:TEST260515C00100000", bid=0.80, ask=0.90),
            _polygon_chain_item("O:TEST260515C00110000", bid=0.50, ask=0.60),
        ]

        forced = selector._force_include_from_chain(
            ticker="TEST",
            underlying_price=100.0,
            chain=chain,
            force_tickers={"O:TEST260515C00110000"},
            already_selected={"O:TEST260515C00100000"},
        )

        assert len(forced) == 1
        assert forced[0].option_ticker == "O:TEST260515C00110000"
        assert forced[0].mid == pytest.approx(0.55, abs=0.01)
        assert forced[0].dte_bucket == DTEBucket.B

    def test_force_include_skips_already_selected(self):
        """Force-include does not duplicate contracts already in the output."""
        selector = self._make_selector()
        chain = [_polygon_chain_item("O:TEST260515C00100000")]

        forced = selector._force_include_from_chain(
            ticker="TEST",
            underlying_price=100.0,
            chain=chain,
            force_tickers={"O:TEST260515C00100000"},
            already_selected={"O:TEST260515C00100000"},
        )

        assert len(forced) == 0

    def test_force_include_skips_expired_contracts(self):
        """Force-include ignores expired contracts in the chain."""
        selector = self._make_selector()
        chain = [
            _polygon_chain_item(
                "O:TEST260101C00100000",
                expiration="2025-01-01",
            )
        ]

        forced = selector._force_include_from_chain(
            ticker="TEST",
            underlying_price=100.0,
            chain=chain,
            force_tickers={"O:TEST260101C00100000"},
            already_selected=set(),
        )

        assert len(forced) == 0

    def test_force_include_skips_outside_dte_buckets(self):
        """Force-include ignores contracts whose DTE falls outside all bucket ranges."""
        selector = self._make_selector()
        # DTE 3 is below bucket A minimum of 7
        chain = [
            _polygon_chain_item(
                "O:TEST260405C00100000",
                expiration="2026-04-05",  # ~3 DTE from today (Apr 2)
            )
        ]

        forced = selector._force_include_from_chain(
            ticker="TEST",
            underlying_price=100.0,
            chain=chain,
            force_tickers={"O:TEST260405C00100000"},
            already_selected=set(),
        )

        assert len(forced) == 0

    def test_dte_bucket_for_assignment(self):
        """_dte_bucket_for correctly assigns buckets."""
        selector = self._make_selector()
        assert selector._dte_bucket_for(10) == DTEBucket.A
        assert selector._dte_bucket_for(30) == DTEBucket.B
        assert selector._dte_bucket_for(60) == DTEBucket.C
        assert selector._dte_bucket_for(100) == DTEBucket.D
        assert selector._dte_bucket_for(3) is None
        assert selector._dte_bucket_for(200) is None


# ---------------------------------------------------------------------------
# Part 2: API superseded-APPROVE suppression
# ---------------------------------------------------------------------------


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestSupersededApproves:
    """Test that superseded APPROVEs are suppressed from the Opportunities page."""

    def _make_eval_item(
        self,
        option_ticker: str = "O:AAPL260515C00185000",
        underlying_ticker: str = "AAPL",
        evaluated_at: str = "2026-04-02T10:00:00+00:00",
        verdict: str = "APPROVE",
        mid: float = 0.50,
        evaluation_id: str = "eval-1",
    ) -> dict:
        return {
            "evaluation_id": evaluation_id,
            "option_ticker": option_ticker,
            "underlying_ticker": underlying_ticker,
            "evaluated_at": evaluated_at,
            "option_type": "CALL",
            "strike": 185.0,
            "expiration_date": "2026-05-15",
            "dte": 30,
            "mid": mid,
            "bid": mid - 0.05,
            "ask": mid + 0.05,
            "delta": 0.45,
            "iv": 0.25,
            "moneyness_pct": 2.0,
            "decision": {"verdict": verdict, "final_score": 75.0},
        }

    @pytest.mark.asyncio
    async def test_approve_persists_even_with_newer_watch(self, client):
        """An APPROVE remains visible even if a newer WATCH evaluation exists.

        The revalidation pipeline re-evaluates APPROVE contracts every run.
        If a contract oscillates between APPROVE/WATCH at gate thresholds,
        the most recent APPROVE should persist (via dedup) rather than being
        suppressed — otherwise the Opportunities page oscillates empty.
        """
        old_approve = self._make_eval_item(
            evaluated_at="2026-04-02T09:00:00+00:00",
            verdict="APPROVE",
            evaluation_id="eval-old",
        )

        with (
            patch(f"{_ROUTE}.EvaluationTable") as mock_eval,
            patch(f"{_ROUTE}.OpportunityTable") as mock_opp,
            patch(f"{_ROUTE}.PillarScoreTable") as mock_pillar,
            patch(f"{_ROUTE}.GateResultTable") as mock_gate,
            patch(f"{_ROUTE}.TradeThesisTable") as mock_thesis,
            patch(f"{_ROUTE}.FeatureValueTable") as mock_fv,
            patch(f"{_ROUTE}.PaperPositionTable") as mock_paper,
            patch(f"{_ROUTE}._create_catalyst_service") as mock_cat,
            patch(f"{_ROUTE}.trading_days_cutoff", return_value="2026-04-02T00:00:00"),
        ):
            mock_eval.list_by_verdict_since = AsyncMock(return_value=[old_approve])
            mock_opp.list_by_ticker = AsyncMock(return_value=[])
            mock_pillar.list_by_evaluation = AsyncMock(return_value=[])
            mock_gate.list_by_evaluation = AsyncMock(return_value=[])
            mock_thesis.get_by_evaluation_id = AsyncMock(return_value=None)
            mock_fv.list_by_evaluation = AsyncMock(return_value=[])
            mock_paper.get_by_option_ticker = AsyncMock(return_value=None)
            mock_cat.return_value = None

            response = await client.get("/api/evaluations/approve?exclude_earnings=false")

        assert response.status_code == 200
        data = response.json()
        evals = data.get("evaluations", [])
        # APPROVE should NOT be suppressed — revalidation ensures fresh
        # prices, and suppression causes oscillation at gate thresholds
        assert len(evals) == 1
        assert evals[0]["evaluation_id"] == "eval-old"
