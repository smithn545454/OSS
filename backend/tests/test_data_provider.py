"""Tests for DataProvider Protocol and LiveDataProvider.

Verifies:
1. Both providers satisfy the DataProvider Protocol at import time.
2. LiveDataProvider delegates correctly to underlying services.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.data_provider import DataProvider, MarketContextData, StockSnapshot
from app.core.live_data_provider import LiveDataProvider
from app.services.polygon import AggregatedOptionsVolume, DailyBar


# ============================================================================
# Protocol conformance
# ============================================================================


class TestProtocolConformance:
    """Verify that LiveDataProvider satisfies the DataProvider Protocol."""

    def test_live_data_provider_has_all_protocol_methods(self):
        """LiveDataProvider must implement every method on DataProvider."""
        required_methods = [
            "get_daily_bars",
            "get_daily_bars_batch",
            "get_stock_snapshot",
            "get_stock_snapshots_batch",
            "get_options_chain",
            "get_options_chain_minimal",
            "get_aggregated_options_volume",
            "get_iv_history",
            "get_oi_history",
            "get_market_context",
            "get_days_to_earnings",
            "get_recent_sec_filing",
        ]
        for method_name in required_methods:
            assert hasattr(LiveDataProvider, method_name), (
                f"LiveDataProvider missing method: {method_name}"
            )

    def test_market_context_data_defaults(self):
        """MarketContextData has sensible defaults."""
        ctx = MarketContextData(date="2026-01-17")
        assert ctx.spy_close == 0.0
        assert ctx.spy_change_pct == 0.0
        assert ctx.vix_close == 0.0

    def test_stock_snapshot_fields(self):
        """StockSnapshot has all required fields."""
        snap = StockSnapshot(
            ticker="AAPL",
            date="2026-01-17",
            close=189.0,
            volume=50_000_000,
            open=187.0,
            high=190.0,
            low=186.0,
            vwap=188.5,
        )
        assert snap.ticker == "AAPL"
        assert snap.close == 189.0
        assert snap.volume == 50_000_000
        assert snap.vwap == 188.5


# ============================================================================
# LiveDataProvider unit tests
# ============================================================================


@pytest.fixture
def mock_polygon():
    """Mock PolygonClient for LiveDataProvider tests."""
    client = AsyncMock()
    return client


@pytest.fixture
def mock_catalyst():
    """Mock CatalystDataService."""
    service = AsyncMock()
    return service


@pytest.fixture
def mock_earnings():
    """Mock EarningsCacheService."""
    service = AsyncMock()
    return service


@pytest.fixture
def live_provider(mock_polygon, mock_catalyst, mock_earnings):
    """LiveDataProvider wired with all mocked services."""
    return LiveDataProvider(
        polygon_client=mock_polygon,
        catalyst_service=mock_catalyst,
        earnings_cache=mock_earnings,
    )


class TestLiveDataProviderDailyBars:
    """Tests for get_daily_bars and get_daily_bars_batch."""

    async def test_get_daily_bars_delegates_to_polygon(self, live_provider, mock_polygon):
        """get_daily_bars calls polygon.get_daily_bars_parsed with correct dates."""
        mock_polygon.get_daily_bars_parsed.return_value = [
            DailyBar("AAPL", "2026-01-15", 180.0, 185.0, 179.0, 184.0, 50_000_000),
        ]

        result = await live_provider.get_daily_bars("AAPL", date(2026, 1, 17), lookback_days=60)

        mock_polygon.get_daily_bars_parsed.assert_called_once_with(
            "AAPL",
            "2025-11-18",  # 2026-01-17 - 60 days
            "2026-01-17",  # end_date passed through
        )
        assert len(result) == 1
        assert result[0].ticker == "AAPL"

    async def test_get_daily_bars_batch_delegates(self, live_provider, mock_polygon):
        """get_daily_bars_batch calls polygon.get_daily_bars_batch."""
        mock_polygon.get_daily_bars_batch.return_value = {
            "AAPL": [DailyBar("AAPL", "2026-01-15", 180.0, 185.0, 179.0, 184.0, 50_000_000)],
            "MSFT": [],
        }

        result = await live_provider.get_daily_bars_batch(
            ["AAPL", "MSFT"], date(2026, 1, 17), lookback_days=60
        )

        mock_polygon.get_daily_bars_batch.assert_called_once_with(
            ["AAPL", "MSFT"], days=60
        )
        assert "AAPL" in result
        assert "MSFT" in result


class TestLiveDataProviderSnapshots:
    """Tests for get_stock_snapshot and get_stock_snapshots_batch."""

    async def test_get_stock_snapshot_converts_dict(self, live_provider, mock_polygon):
        """get_stock_snapshot wraps Polygon dict into StockSnapshot."""
        mock_polygon.get_previous_close.return_value = {
            "T": "AAPL",
            "c": 189.0,
            "o": 187.0,
            "h": 190.0,
            "l": 186.0,
            "v": 50_000_000,
            "vw": 188.5,
        }

        result = await live_provider.get_stock_snapshot("AAPL", date(2026, 1, 17))

        assert isinstance(result, StockSnapshot)
        assert result.ticker == "AAPL"
        assert result.close == 189.0
        assert result.volume == 50_000_000
        assert result.vwap == 188.5
        assert result.open == 187.0
        assert result.high == 190.0
        assert result.low == 186.0

    async def test_get_stock_snapshot_returns_none(self, live_provider, mock_polygon):
        """get_stock_snapshot returns None when Polygon returns None."""
        mock_polygon.get_previous_close.return_value = None

        result = await live_provider.get_stock_snapshot("UNKNOWN", date(2026, 1, 17))
        assert result is None

    async def test_get_stock_snapshots_batch(self, live_provider, mock_polygon):
        """get_stock_snapshots_batch converts batch dict."""
        mock_polygon.get_previous_close_batch.return_value = {
            "AAPL": {"c": 189.0, "o": 187.0, "h": 190.0, "l": 186.0, "v": 50_000_000},
            "MSFT": {"c": 405.0, "o": 403.0, "h": 407.0, "l": 402.0, "v": 30_000_000},
        }

        result = await live_provider.get_stock_snapshots_batch(
            ["AAPL", "MSFT"], date(2026, 1, 17)
        )

        assert len(result) == 2
        assert isinstance(result["AAPL"], StockSnapshot)
        assert result["MSFT"].close == 405.0


class TestLiveDataProviderOptionsChain:
    """Tests for options chain methods."""

    async def test_get_options_chain_computes_exp_dates(self, live_provider, mock_polygon):
        """get_options_chain translates DTE to expiration date bounds."""
        mock_polygon.get_options_chain.return_value = [{"details": {}}]

        await live_provider.get_options_chain(
            "AAPL", date(2026, 1, 17), min_dte=7, max_dte=120
        )

        mock_polygon.get_options_chain.assert_called_once_with(
            "AAPL",
            expiration_date_gte="2026-01-24",  # 2026-01-17 + 7
            expiration_date_lte="2026-05-17",  # 2026-01-17 + 120
        )

    async def test_get_options_chain_minimal_delegates(self, live_provider, mock_polygon):
        """get_options_chain_minimal delegates to polygon."""
        mock_polygon.get_options_chain_minimal.return_value = []

        await live_provider.get_options_chain_minimal(
            "AAPL", date(2026, 1, 17), min_dte=7, max_dte=90
        )

        mock_polygon.get_options_chain_minimal.assert_called_once()

    async def test_get_aggregated_options_volume(self, live_provider, mock_polygon):
        """get_aggregated_options_volume delegates and converts."""
        mock_vol = MagicMock()
        mock_vol.ticker = "AAPL"
        mock_vol.total_call_volume = 100_000
        mock_vol.total_put_volume = 50_000
        mock_vol.total_call_oi = 500_000
        mock_vol.total_put_oi = 300_000
        mock_vol.call_put_volume_ratio = 2.0
        mock_vol.timestamp = "2026-01-17T16:00:00Z"
        mock_polygon.get_aggregated_options_volume.return_value = mock_vol

        result = await live_provider.get_aggregated_options_volume("AAPL", date(2026, 1, 17))
        assert result is not None
        assert result.ticker == "AAPL"
        assert result.total_call_volume == 100_000

    async def test_get_aggregated_options_volume_none(self, live_provider, mock_polygon):
        """get_aggregated_options_volume returns None when Polygon returns None."""
        mock_polygon.get_aggregated_options_volume.return_value = None

        result = await live_provider.get_aggregated_options_volume("AAPL", date(2026, 1, 17))
        assert result is None


class TestLiveDataProviderHistory:
    """Tests for IV and OI history methods."""

    async def test_get_iv_history_delegates_to_table(self, live_provider):
        """get_iv_history queries IVHistoryTable and returns IVHistoryRecord objects."""
        mock_record = MagicMock()
        mock_record.ticker = "AAPL"
        mock_record.date = "2026-01-15"
        mock_record.atm_iv = 0.32
        mock_record.atm_call_iv = None
        mock_record.atm_put_iv = None
        mock_record.rv20 = None
        mock_record.iv_rv_ratio = None

        with patch(
            "app.db.tables.IVHistoryTable.list_by_ticker",
            new_callable=AsyncMock,
            return_value=[mock_record],
        ):
            result = await live_provider.get_iv_history(
                "AAPL", date(2026, 1, 17), lookback_days=252
            )

        assert len(result) == 1
        assert result[0].ticker == "AAPL"
        assert result[0].atm_iv == 0.32

    async def test_get_oi_history_delegates_to_table(self, live_provider):
        """get_oi_history queries OIHistoryTable and returns OIHistoryRecord objects."""
        mock_record = MagicMock()
        mock_record.option_ticker = "O:AAPL260320C00185000"
        mock_record.date = "2026-01-15"
        mock_record.open_interest = 5000
        mock_record.volume = 500

        with patch(
            "app.db.tables.OIHistoryTable.list_by_contract",
            new_callable=AsyncMock,
            return_value=[mock_record],
        ):
            result = await live_provider.get_oi_history(
                "O:AAPL260320C00185000", date(2026, 1, 17), lookback_days=5
            )

        assert len(result) == 1
        assert result[0].option_ticker == "O:AAPL260320C00185000"
        assert result[0].open_interest == 5000


class TestLiveDataProviderMarketContext:
    """Tests for market context."""

    async def test_get_market_context(self, live_provider, mock_polygon):
        """get_market_context builds context from SPY previous close."""
        mock_polygon.get_previous_close.return_value = {"c": 584.0}

        result = await live_provider.get_market_context(date(2026, 1, 17))

        assert isinstance(result, MarketContextData)
        assert result.spy_close == 584.0
        assert result.date == "2026-01-17"

    async def test_get_market_context_none_prev_close(self, live_provider, mock_polygon):
        """get_market_context handles None from Polygon."""
        mock_polygon.get_previous_close.return_value = None

        result = await live_provider.get_market_context(date(2026, 1, 17))
        assert result is not None
        assert result.spy_close == 0.0

    async def test_get_market_context_exception(self, live_provider, mock_polygon):
        """get_market_context returns None on exception."""
        mock_polygon.get_previous_close.side_effect = Exception("API error")

        result = await live_provider.get_market_context(date(2026, 1, 17))
        assert result is None


class TestLiveDataProviderCatalyst:
    """Tests for catalyst (earnings, SEC) methods."""

    async def test_get_days_to_earnings_from_cache(self, live_provider, mock_earnings):
        """get_days_to_earnings delegates to earnings_cache first."""
        mock_earnings.get_days_to_earnings.return_value = 30

        result = await live_provider.get_days_to_earnings("AAPL", date(2026, 1, 17))
        assert result == 30

    async def test_get_days_to_earnings_fallback_to_catalyst(self, mock_polygon, mock_catalyst):
        """Falls back to catalyst_service when earnings_cache is None."""
        mock_catalyst.get_days_to_earnings.return_value = 45
        provider = LiveDataProvider(
            polygon_client=mock_polygon,
            catalyst_service=mock_catalyst,
            earnings_cache=None,
        )

        result = await provider.get_days_to_earnings("AAPL", date(2026, 1, 17))
        assert result == 45

    async def test_get_days_to_earnings_returns_none_without_services(self, mock_polygon):
        """Returns None when no catalyst or earnings service is available."""
        provider = LiveDataProvider(polygon_client=mock_polygon)

        result = await provider.get_days_to_earnings("AAPL", date(2026, 1, 17))
        assert result is None

    async def test_get_recent_sec_filing(self, live_provider, mock_catalyst):
        """get_recent_sec_filing delegates to catalyst_service."""
        mock_catalyst.get_recent_sec_filing.return_value = True

        result = await live_provider.get_recent_sec_filing("AAPL", date(2026, 1, 17))
        assert result is True

    async def test_get_recent_sec_filing_returns_false_without_service(self, mock_polygon):
        """Returns False when no catalyst service is available."""
        provider = LiveDataProvider(polygon_client=mock_polygon)

        result = await provider.get_recent_sec_filing("AAPL", date(2026, 1, 17))
        assert result is False
