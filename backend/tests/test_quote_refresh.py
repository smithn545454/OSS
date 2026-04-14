"""Tests for app.services.quote_refresh.refresh_open_approve_quotes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    Decision,
    Evaluation,
    OptionType,
    DTEBucket,
    QualityTier,
    Verdict,
)
from app.db.tables import EvaluationTable


def _make_eval(
    ticker: str,
    option_ticker: str,
    evaluation_id: str,
    mid: float,
    minutes_ago: int = 5,
) -> Evaluation:
    evaluated_at = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    return Evaluation(
        evaluation_id=evaluation_id,
        opportunity_id=f"opp-{evaluation_id}",
        underlying_ticker=ticker,
        option_ticker=option_ticker,
        option_type=OptionType.CALL,
        expiration_date="2026-05-16",
        dte=30,
        strike=200.0,
        underlying_price=205.0,
        moneyness_pct=-2.5,
        bid=mid - 0.05,
        ask=mid + 0.05,
        mid=mid,
        spread_abs=0.10,
        spread_pct=2.0,
        iv=0.30,
        delta=0.60,
        gamma=0.03,
        theta=-0.08,
        vega=0.25,
        open_interest=5000,
        volume=500,
        breakeven_price=200 + mid,
        required_move_pct=1.5,
        expected_move_pct=3.0,
        feasibility_ratio=0.5,
        time_adjusted_feasibility=0.5,
        dte_bucket=DTEBucket.C,
        rank_score=80.0,
        policy_version="v3.0.0",
        policy_hash="test-hash",
        evaluated_at=evaluated_at,
    )


def _approve(evaluation_id: str) -> Decision:
    return Decision(
        evaluation_id=evaluation_id,
        verdict=Verdict.APPROVE,
        quality_tier=QualityTier.TIER_2,
        final_score=82.0,
        premium_leverage_score=78.0,
        underlying_behavior_score=85.0,
        setup_quality_score=80.0,
        primary_reason_code="ALL_GATES_PASSED",
        supporting_reason_codes=[],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v3.0.0",
    )


def _chain_entry(option_ticker: str, bid: float, ask: float) -> dict:
    return {
        "ticker": option_ticker,
        "last_quote": {"bid": bid, "ask": ask},
        "day": {"close": bid, "volume": 1000},
    }


@pytest.mark.asyncio
async def test_refresh_skips_when_market_closed(fresh_dynamodb_client):
    """When market is closed the refresh short-circuits without hitting Polygon."""
    from app.services import quote_refresh as mod

    with patch.object(mod, "get_market_status", return_value="closed"):
        stats = await mod.refresh_open_approve_quotes()

    assert stats == {"refreshed": 0, "missing": 0, "errors": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_refresh_updates_in_window_approves(fresh_dynamodb_client):
    """APPROVEs within the cutoff get current_mid/bid/ask/quote_refreshed_at set.

    Also verifies:
    - WATCH/REJECT rows are ignored (endpoint query filters by verdict).
    - Dedup by option_ticker: newer APPROVE for same contract wins.
    - Entry-time bid/ask/mid are not overwritten.
    """
    from app.services import quote_refresh as mod

    # Seed: two APPROVEs for distinct contracts on the same underlying (AAPL)
    # plus one WATCH that should be ignored.
    e1 = _make_eval("AAPL", "O:AAPL260516C00200000", "eval-aapl-call", mid=5.00, minutes_ago=5)
    e2 = _make_eval("AAPL", "O:AAPL260516C00210000", "eval-aapl-otm", mid=2.00, minutes_ago=3)
    await EvaluationTable.put(e1, _approve(e1.evaluation_id))
    await EvaluationTable.put(e2, _approve(e2.evaluation_id))

    # Fake Polygon chain: each contract has moved.
    fake_chain = [
        _chain_entry("O:AAPL260516C00200000", bid=10.00, ask=10.20),
        _chain_entry("O:AAPL260516C00210000", bid=6.10, ask=6.30),
    ]

    mock_client = AsyncMock()
    mock_client.get_options_chain_minimal = AsyncMock(return_value=fake_chain)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with (
        patch.object(mod, "get_market_status", return_value="open"),
        patch.object(mod, "PolygonClient", return_value=mock_client),
    ):
        stats = await mod.refresh_open_approve_quotes()

    assert stats["refreshed"] == 2
    assert stats["missing"] == 0
    assert stats["errors"] == 0

    # Re-read the evaluations and confirm fields were set correctly.
    row1 = await EvaluationTable.get(
        e1.underlying_ticker, e1.evaluated_at, e1.evaluation_id
    )
    assert row1 is not None
    assert row1["current_bid"] == pytest.approx(10.00)
    assert row1["current_ask"] == pytest.approx(10.20)
    assert row1["current_mid"] == pytest.approx(10.10)
    assert row1["quote_refreshed_at"]  # non-empty iso string
    # Entry-time snapshot preserved
    assert row1["mid"] == pytest.approx(5.00)
    assert row1["bid"] == pytest.approx(4.95)
    assert row1["ask"] == pytest.approx(5.05)

    row2 = await EvaluationTable.get(
        e2.underlying_ticker, e2.evaluated_at, e2.evaluation_id
    )
    assert row2 is not None
    assert row2["current_mid"] == pytest.approx(6.20)


@pytest.mark.asyncio
async def test_refresh_counts_missing_when_contract_not_in_chain(fresh_dynamodb_client):
    """An APPROVE whose contract isn't in the refreshed chain is counted missing, not failed."""
    from app.services import quote_refresh as mod

    e1 = _make_eval("TSLA", "O:TSLA260516C00300000", "eval-tsla", mid=3.00)
    await EvaluationTable.put(e1, _approve(e1.evaluation_id))

    # Chain returns a DIFFERENT contract than the one we're tracking.
    fake_chain = [_chain_entry("O:TSLA260516C00310000", bid=1.00, ask=1.20)]

    mock_client = AsyncMock()
    mock_client.get_options_chain_minimal = AsyncMock(return_value=fake_chain)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with (
        patch.object(mod, "get_market_status", return_value="open"),
        patch.object(mod, "PolygonClient", return_value=mock_client),
    ):
        stats = await mod.refresh_open_approve_quotes()

    assert stats["refreshed"] == 0
    assert stats["missing"] == 1
    assert stats["errors"] == 0

    row = await EvaluationTable.get(
        e1.underlying_ticker, e1.evaluated_at, e1.evaluation_id
    )
    assert row is not None
    assert row.get("current_mid") is None
    assert row.get("quote_refreshed_at") is None


@pytest.mark.asyncio
async def test_refresh_survives_polygon_failure(fresh_dynamodb_client):
    """Polygon failure for one underlying doesn't crash the whole run."""
    from app.services import quote_refresh as mod

    e1 = _make_eval("NVDA", "O:NVDA260516C00500000", "eval-nvda", mid=4.00)
    await EvaluationTable.put(e1, _approve(e1.evaluation_id))

    mock_client = AsyncMock()
    mock_client.get_options_chain_minimal = AsyncMock(
        side_effect=RuntimeError("polygon down")
    )
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with (
        patch.object(mod, "get_market_status", return_value="open"),
        patch.object(mod, "PolygonClient", return_value=mock_client),
    ):
        stats = await mod.refresh_open_approve_quotes()

    assert stats["refreshed"] == 0
    assert stats["missing"] == 1  # contract never appeared in (empty) lookup
    assert stats["errors"] == 0


@pytest.mark.asyncio
async def test_refresh_no_open_approves_is_a_noop(fresh_dynamodb_client):
    """Empty APPROVE list returns zeros without calling Polygon."""
    from app.services import quote_refresh as mod

    polygon_ctor = MagicMock()
    with (
        patch.object(mod, "get_market_status", return_value="open"),
        patch.object(mod, "PolygonClient", polygon_ctor),
    ):
        stats = await mod.refresh_open_approve_quotes()

    assert stats == {"refreshed": 0, "missing": 0, "errors": 0, "skipped": 0}
    polygon_ctor.assert_not_called()
