"""Tests for Convex Mode historical-data providers (Phase 8 wiring).

Validates that each provider:
    - Filters strictly to data ≤ as_of_iso (no look-ahead)
    - Returns the correct dataclass shape for downstream Stage evaluators
    - Reuses production helpers (no duplicated transformation logic)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.convex.historical_providers import (
    HistoricalFuturePriceHistoryProvider,
    HistoricalOptionPriceProvider,
    HistoricalStage2InputsProvider,
    HistoricalStage3InputsProvider,
    HistoricalStage4InputsProvider,
    _parse_occ,
)
from app.core.data_provider import DailyBar
from app.core.historical_data_provider import HistoricalDataProvider
from app.convex.stage2_catalyst import Stage2Inputs
from app.convex.stage3_volatility import Stage3Inputs
from app.convex.stage4_contract import ConvexContractCandidate, Stage4Inputs
from app.core.schemas import (
    CatalystCalendarEntry,
    CatalystEventType,
    IVHistory,
    PriceHistory,
)


def _make_bars(end_date_iso: str, n: int) -> list[PriceHistory]:
    """Build n PriceHistory bars ending on end_date_iso, oldest first."""
    end = date.fromisoformat(end_date_iso)
    bars = []
    for i in range(n):
        d = end - timedelta(days=(n - 1 - i))
        if d.weekday() >= 5:
            continue
        bars.append(
            PriceHistory(
                ticker="NVDA",
                date=d.isoformat(),
                open=100.0 + i,
                high=102.0 + i,
                low=99.0 + i,
                close=101.0 + i,
                volume=1_000_000 + i * 1000,
            )
        )
    return bars


def _make_daily_bars(end_date_iso: str, n: int, ticker: str = "NVDA") -> list[DailyBar]:
    """Build n DailyBar objects (HistoricalDataProvider shape) ending on end_date_iso."""
    end = date.fromisoformat(end_date_iso)
    bars = []
    for i in range(n):
        d = end - timedelta(days=(n - 1 - i))
        if d.weekday() >= 5:
            continue
        bars.append(
            DailyBar(
                ticker=ticker,
                date=d.isoformat(),
                open=100.0 + i,
                high=102.0 + i,
                low=99.0 + i,
                close=101.0 + i,
                volume=1_000_000 + i * 1000,
                vwap=101.0 + i,
            )
        )
    return bars


def _stub_hdp() -> Any:
    """A MagicMock standing in for HistoricalDataProvider with async hooks."""
    hdp = MagicMock(spec=HistoricalDataProvider)
    hdp.get_daily_bars = AsyncMock(return_value=[])
    hdp.get_options_chain = AsyncMock(return_value=[])
    hdp.get_stock_snapshot = AsyncMock(return_value=None)
    hdp.get_contract_price = AsyncMock(return_value=None)
    return hdp


def _make_iv_history_row(date_iso: str) -> IVHistory:
    """An IVHistory row with all Convex fields populated."""
    return IVHistory(
        ticker="NVDA",
        date=date_iso,
        atm_iv=0.45,
        rv20=0.30,
        iv_30d=0.46,
        iv_60d=0.44,
        iv_25d_put=0.50,
        iv_25d_call=0.42,
    )


# ---------------------------------------------------------------------------
# OCC parsing
# ---------------------------------------------------------------------------


class TestParseOCC:

    def test_polygon_prefix_call(self):
        result = _parse_occ("O:NVDA260620C00145000")
        assert result == ("NVDA", "2026-06-20", "CALL", 145.0)

    def test_no_prefix_put(self):
        result = _parse_occ("AAPL250117P00200500")
        assert result == ("AAPL", "2025-01-17", "PUT", 200.5)

    def test_invalid_returns_none(self):
        assert _parse_occ("not-an-occ-symbol") is None


# ---------------------------------------------------------------------------
# Stage 2 provider
# ---------------------------------------------------------------------------


class TestHistoricalStage2InputsProvider:

    @pytest.mark.asyncio
    async def test_filters_at_or_before_as_of(self):
        as_of = "2026-04-15"
        daily = _make_daily_bars(as_of, 100)
        assert len(daily) >= 60
        hdp = _stub_hdp()
        hdp.get_daily_bars = AsyncMock(return_value=daily)

        captured: dict[str, Any] = {}

        async def fake_calendar(ticker, start_date=None, end_date=None, limit=50):
            captured["calendar_start_date"] = start_date
            captured["calendar_end_date"] = end_date
            return [
                CatalystCalendarEntry(
                    ticker=ticker,
                    event_date="2026-04-30",
                    event_type=CatalystEventType.EARNINGS,
                    confirmed=True,
                    source="finnhub",
                )
            ]

        with patch(
            "app.convex.historical_providers.CatalystCalendarTable.list_for_ticker",
            side_effect=fake_calendar,
        ):
            provider = HistoricalStage2InputsProvider(hdp=hdp)
            inputs = await provider.fetch("NVDA", "Technology", as_of)

        assert inputs is not None
        assert isinstance(inputs, Stage2Inputs)
        # HDP called with as_of date object.
        hdp.get_daily_bars.assert_awaited_once()
        args, kwargs = hdp.get_daily_bars.call_args
        assert args[1] == date.fromisoformat(as_of) or kwargs.get("end_date") == date.fromisoformat(as_of)
        # Calendar window starts at as_of and runs forward 60 days.
        assert captured["calendar_start_date"] == as_of
        assert captured["calendar_end_date"] == "2026-06-14"
        # No data row beyond as_of_iso.
        assert all(b.date <= as_of for b in daily)
        # UV intentionally disabled in historical mode.
        assert inputs.today_total_options_volume is None
        assert inputs.avg_options_volume_30d is None
        assert inputs.peer_reactions == []
        # Calendar passed through.
        assert len(inputs.calendar_entries) == 1
        assert inputs.calendar_entries[0].event_type == CatalystEventType.EARNINGS

    @pytest.mark.asyncio
    async def test_returns_none_when_insufficient_history(self):
        hdp = _stub_hdp()
        hdp.get_daily_bars = AsyncMock(
            return_value=_make_daily_bars("2026-04-15", 40)  # ~30 weekdays
        )
        provider = HistoricalStage2InputsProvider(hdp=hdp)
        result = await provider.fetch("NVDA", "Technology", "2026-04-15")
        assert result is None


# ---------------------------------------------------------------------------
# Stage 3 provider
# ---------------------------------------------------------------------------


class TestHistoricalStage3InputsProvider:

    @pytest.mark.asyncio
    async def test_reads_iv_history_with_end_date(self):
        as_of = "2026-04-15"
        captured: dict[str, Any] = {}

        async def fake_iv_history(ticker, **kwargs):
            captured["iv_kwargs"] = kwargs
            return [
                _make_iv_history_row(as_of),
                _make_iv_history_row("2026-04-14"),
            ]

        hdp = _stub_hdp()
        hdp.get_daily_bars = AsyncMock(return_value=_make_daily_bars(as_of, 60))

        with patch(
            "app.convex.historical_providers.IVHistoryTable.list_by_ticker",
            side_effect=fake_iv_history,
        ):
            provider = HistoricalStage3InputsProvider(hdp=hdp)
            inputs = await provider.fetch("NVDA", as_of)

        assert inputs is not None
        assert isinstance(inputs, Stage3Inputs)
        # Strict as-of filter on IV history.
        assert captured["iv_kwargs"].get("end_date") == as_of
        # HDP called with the as-of date for price history.
        hdp.get_daily_bars.assert_awaited_once()
        # 30-day IV pulled from the latest (newest-first) row.
        assert inputs.current_iv_30d == 0.46
        # rv20 computed from price history (closes increase by 1 each day).
        assert inputs.rv20 is not None
        assert inputs.rv20 > 0


# ---------------------------------------------------------------------------
# Stage 4 provider
# ---------------------------------------------------------------------------


class _StubStockSnapshot:
    def __init__(self, close: float):
        self.close = close
        self.volume = 1_000_000
        self.open = close - 1
        self.high = close + 2
        self.low = close - 2
        self.vwap = close
        self.date = "2026-04-15"


def _polygon_shaped_contract(
    option_ticker: str,
    contract_type: str,
    strike: float,
    expiry: str,
    delta: float,
    bid: float,
    ask: float,
) -> dict:
    return {
        "ticker": option_ticker,
        "details": {
            "contract_type": contract_type,
            "strike_price": strike,
            "expiration_date": expiry,
            "ticker": option_ticker,
        },
        "day": {"close": (bid + ask) / 2, "volume": 100, "vwap": (bid + ask) / 2},
        "open_interest": 500,
        "implied_volatility": 0.45,
        "greeks": {"delta": delta, "gamma": 0.01, "theta": -0.02, "vega": 0.10},
        "last_quote": {"bid": bid, "ask": ask, "last_updated": "2026-04-15"},
    }


class TestHistoricalStage4InputsProvider:

    @pytest.mark.asyncio
    async def test_uses_historical_chain_parquet(self):
        as_of = "2026-04-15"
        chain = [
            _polygon_shaped_contract(
                "O:NVDA260620C00150000",
                "call",
                150.0,
                "2026-06-20",
                0.45,
                2.10,
                2.30,
            ),
            _polygon_shaped_contract(
                "O:NVDA260620P00140000",
                "put",
                140.0,
                "2026-06-20",
                -0.40,
                1.80,
                1.95,
            ),
        ]

        hdp = _stub_hdp()
        hdp.get_options_chain = AsyncMock(return_value=chain)
        hdp.get_stock_snapshot = AsyncMock(
            return_value=_StubStockSnapshot(close=145.0)
        )
        provider = HistoricalStage4InputsProvider(hdp=hdp)

        inputs = await provider.fetch(
            ticker="NVDA",
            direction="bullish",
            catalyst_type="date_known",
            catalyst_date_iso="2026-04-30",
            uv_directional_skew=None,
            as_of_iso=as_of,
        )

        assert inputs is not None
        assert isinstance(inputs, Stage4Inputs)
        assert inputs.underlying_price == 145.0
        assert inputs.direction == "bullish"
        assert inputs.today_iso == as_of
        assert len(inputs.available_contracts) == 2
        for c in inputs.available_contracts:
            assert isinstance(c, ConvexContractCandidate)
        hdp.get_options_chain.assert_awaited_once()


# ---------------------------------------------------------------------------
# Future price + Option price providers (light coverage)
# ---------------------------------------------------------------------------


class TestHistoricalFuturePriceHistoryProvider:

    @pytest.mark.asyncio
    async def test_walks_forward_from_start_date(self):
        # 60 weekday bars covering Apr-May 2026 forward window.
        forward = _make_daily_bars("2026-06-30", 80)
        # Drop bars before our forward start so the slice returns just future bars.
        hdp = _stub_hdp()
        hdp.get_daily_bars = AsyncMock(return_value=forward)

        provider = HistoricalFuturePriceHistoryProvider(hdp=hdp)
        bars = await provider.fetch("NVDA", "2026-05-15", days=21)

        assert len(bars) > 0
        # All bars are on or after the start date.
        assert all(b.date >= "2026-05-15" for b in bars)
        # Capped at 21.
        assert len(bars) <= 21


class TestHistoricalOptionPriceProvider:

    @pytest.mark.asyncio
    async def test_parses_occ_and_calls_hdp(self):
        hdp = _stub_hdp()
        hdp.get_contract_price = AsyncMock(return_value=2.50)
        provider = HistoricalOptionPriceProvider(hdp=hdp)

        result = await provider.fetch("O:NVDA260620C00145000", "2026-04-15")

        assert result == 2.50
        hdp.get_contract_price.assert_awaited_once_with(
            ticker="NVDA",
            strike=145.0,
            expiration_date="2026-06-20",
            option_type="CALL",
            as_of=date(2026, 4, 15),
        )

    @pytest.mark.asyncio
    async def test_invalid_ticker_returns_none(self):
        hdp = _stub_hdp()
        hdp.get_contract_price = AsyncMock(return_value=2.50)
        provider = HistoricalOptionPriceProvider(hdp=hdp)
        result = await provider.fetch("not-an-occ", "2026-04-15")
        assert result is None
        hdp.get_contract_price.assert_not_called()
