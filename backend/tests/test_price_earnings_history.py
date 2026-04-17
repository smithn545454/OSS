"""Tests for Pillar v4 data foundation: price history + earnings history."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.core.schemas import EarningsEvent, PriceHistory
from app.db.tables import EarningsHistoryTable, PriceHistoryTable
from app.services.earnings_calendar import EarningsCalendarService
from app.services.price_history import PriceHistoryService


# ---------------------------------------------------------------------------
# PriceHistoryTable
# ---------------------------------------------------------------------------


def _bar(ticker: str, date_str: str, close: float) -> PriceHistory:
    return PriceHistory(
        ticker=ticker,
        date=date_str,
        open=close - 0.5,
        high=close + 0.5,
        low=close - 1.0,
        close=close,
        volume=1_000_000,
    )


@pytest.mark.asyncio
async def test_price_history_put_and_get_roundtrip(fresh_dynamodb_client):
    bar = _bar("AAPL", "2026-04-10", 170.25)
    await PriceHistoryTable.put(bar)
    fetched = await PriceHistoryTable.get("AAPL", "2026-04-10")
    assert fetched is not None
    assert fetched.ticker == "AAPL"
    assert fetched.close == 170.25


@pytest.mark.asyncio
async def test_price_history_batch_write_and_list(fresh_dynamodb_client):
    bars = [
        _bar("AAPL", f"2026-04-{d:02d}", 170.0 + d)
        for d in range(1, 16)  # 15 bars
    ]
    await PriceHistoryTable.put_batch(bars)

    all_bars = await PriceHistoryTable.list_by_ticker("AAPL", limit=20)
    assert len(all_bars) == 15

    # Scan forward returns oldest first.
    forward = await PriceHistoryTable.list_by_ticker(
        "AAPL", limit=20, scan_forward=True
    )
    assert forward[0].date == "2026-04-01"
    assert forward[-1].date == "2026-04-15"


@pytest.mark.asyncio
async def test_price_history_date_range_filter(fresh_dynamodb_client):
    bars = [_bar("TSLA", f"2026-03-{d:02d}", 200.0 + d) for d in range(1, 21)]
    await PriceHistoryTable.put_batch(bars)

    window = await PriceHistoryTable.list_by_ticker(
        "TSLA",
        limit=50,
        start_date="2026-03-05",
        end_date="2026-03-10",
        scan_forward=True,
    )
    assert len(window) == 6
    assert window[0].date == "2026-03-05"
    assert window[-1].date == "2026-03-10"


@pytest.mark.asyncio
async def test_price_history_service_returns_cached_bars(fresh_dynamodb_client):
    """With no Polygon client, service reads purely from cache."""
    bars = [_bar("MSFT", f"2026-04-{d:02d}", 400.0 + d) for d in range(1, 11)]
    await PriceHistoryTable.put_batch(bars)

    svc = PriceHistoryService(polygon_client=None)
    got = await svc.get_bars(
        "MSFT", lookback_days=5, as_of=date(2026, 4, 15), allow_fallback=False
    )
    # 10 cached bars; we asked for 5, service should trim to most-recent 5.
    assert len(got) == 5
    assert got[-1].date == "2026-04-10"


@pytest.mark.asyncio
async def test_price_history_service_fallback_to_polygon(fresh_dynamodb_client):
    """Cache miss invokes Polygon and writes through."""
    from app.services.polygon import DailyBar

    polygon = AsyncMock()
    polygon.get_daily_bars_parsed.return_value = [
        DailyBar(
            ticker="NVDA",
            date=f"2026-03-{d:02d}",
            open=900.0,
            high=920.0,
            low=890.0,
            close=910.0 + d,
            volume=5_000_000,
        )
        for d in range(1, 11)
    ]
    svc = PriceHistoryService(polygon_client=polygon)
    got = await svc.get_bars(
        "NVDA", lookback_days=10, as_of=date(2026, 3, 15)
    )
    assert len(got) == 10
    polygon.get_daily_bars_parsed.assert_called_once()

    # Verify write-through: next read should be cache-only.
    polygon.get_daily_bars_parsed.reset_mock()
    cached = await svc.get_bars(
        "NVDA",
        lookback_days=10,
        as_of=date(2026, 3, 15),
        allow_fallback=False,
    )
    assert len(cached) == 10
    polygon.get_daily_bars_parsed.assert_not_called()


# ---------------------------------------------------------------------------
# EarningsHistoryTable
# ---------------------------------------------------------------------------


def _event(
    ticker: str, date_str: str, *, move: float | None = None
) -> EarningsEvent:
    return EarningsEvent(
        ticker=ticker,
        earnings_date=date_str,
        fiscal_period="Q1 2026",
        time_of_day="amc",
        one_day_move_pct=move,
    )


@pytest.mark.asyncio
async def test_earnings_history_put_and_get(fresh_dynamodb_client):
    event = _event("AAPL", "2026-01-27", move=3.2)
    await EarningsHistoryTable.put(event)
    fetched = await EarningsHistoryTable.get("AAPL", "2026-01-27")
    assert fetched is not None
    assert fetched.one_day_move_pct == 3.2


@pytest.mark.asyncio
async def test_earnings_history_recent_with_moves(fresh_dynamodb_client):
    # 6 events, 2 without moves — should be skipped.
    events = [
        _event("AAPL", "2025-01-30", move=2.1),
        _event("AAPL", "2025-05-02", move=None),
        _event("AAPL", "2025-08-01", move=4.3),
        _event("AAPL", "2025-10-31", move=-1.5),
        _event("AAPL", "2026-01-30", move=None),
        _event("AAPL", "2026-04-26", move=5.8),
    ]
    await EarningsHistoryTable.put_batch(events)

    with_moves = await EarningsHistoryTable.get_recent_with_moves("AAPL", n=4)
    assert len(with_moves) == 4
    # Returned in most-recent-first order.
    assert with_moves[0].earnings_date == "2026-04-26"
    assert with_moves[-1].earnings_date == "2025-01-30"


@pytest.mark.asyncio
async def test_earnings_service_historical_move_magnitude(fresh_dynamodb_client):
    events = [
        _event("GOOGL", "2025-04-25", move=-2.0),
        _event("GOOGL", "2025-07-25", move=4.0),
        _event("GOOGL", "2025-10-25", move=-1.0),
        _event("GOOGL", "2026-01-25", move=5.0),
    ]
    await EarningsHistoryTable.put_batch(events)

    svc = EarningsCalendarService()
    result = await svc.get_historical_move_magnitude("GOOGL")
    assert result is not None
    mean_abs, count = result
    assert count == 4
    # Mean of |(-2, 4, -1, 5)| = 3.0
    assert mean_abs == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_earnings_service_historical_move_magnitude_no_data(
    fresh_dynamodb_client,
):
    svc = EarningsCalendarService()
    result = await svc.get_historical_move_magnitude("XYZ")
    assert result is None


@pytest.mark.asyncio
async def test_earnings_service_parse_raw_event():
    svc = EarningsCalendarService()
    raw = {
        "date": "2026-04-25",
        "hour": "amc",
        "quarter": 1,
        "year": 2026,
        "epsEstimate": "1.25",
        "epsActual": "1.45",
        "revenueEstimate": 1e9,
        "revenueActual": 1.1e9,
        "symbol": "MSFT",
    }
    event = svc._parse_raw_event("MSFT", raw)
    assert event is not None
    assert event.ticker == "MSFT"
    assert event.earnings_date == "2026-04-25"
    assert event.time_of_day == "amc"
    assert event.fiscal_period == "Q1 2026"
    assert event.eps_estimate == 1.25
    assert event.eps_actual == 1.45


@pytest.mark.asyncio
async def test_earnings_service_parse_raw_event_rejects_missing_date():
    svc = EarningsCalendarService()
    assert svc._parse_raw_event("X", {"hour": "bmo"}) is None
    assert svc._parse_raw_event("X", {"date": "not-a-date"}) is None


@pytest.mark.asyncio
async def test_compute_one_day_move_amc(fresh_dynamodb_client):
    """AMC announcement: move is event-day close vs next-day close."""
    # Populate price history: event on Friday, next trading day Monday.
    bars = [
        _bar("AAPL", "2026-04-23", 170.0),  # Thursday
        _bar("AAPL", "2026-04-24", 172.0),  # Friday (event day, AMC)
        _bar("AAPL", "2026-04-27", 178.36),  # Monday (post-event close)
    ]
    await PriceHistoryTable.put_batch(bars)

    svc = EarningsCalendarService(price_history_service=PriceHistoryService())
    result = await svc._compute_one_day_move("AAPL", date(2026, 4, 24), "amc")
    assert result is not None
    pre_close, post_close, move_pct = result
    assert pre_close == 172.0
    assert post_close == 178.36
    # (178.36 - 172.0) / 172.0 * 100 = 3.6977
    assert move_pct == pytest.approx(3.6977, abs=0.01)


@pytest.mark.asyncio
async def test_compute_one_day_move_bmo(fresh_dynamodb_client):
    """BMO announcement: move is prior-day close vs event-day close."""
    bars = [
        _bar("MSFT", "2026-04-22", 400.0),  # Wednesday (pre-close)
        _bar("MSFT", "2026-04-23", 412.0),  # Thursday (event day, BMO)
        _bar("MSFT", "2026-04-24", 420.0),  # Friday
    ]
    await PriceHistoryTable.put_batch(bars)

    svc = EarningsCalendarService(price_history_service=PriceHistoryService())
    result = await svc._compute_one_day_move("MSFT", date(2026, 4, 23), "bmo")
    assert result is not None
    pre_close, post_close, move_pct = result
    assert pre_close == 400.0
    assert post_close == 412.0
    assert move_pct == pytest.approx(3.0, abs=0.01)
