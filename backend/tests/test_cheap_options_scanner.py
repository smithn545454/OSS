"""Tests for the Cheap Options Scanner.

Covers CheapOptionsScanner.scan_ticker() with mocked Polygon client,
IV history storage, and edge cases.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import PolicyConfig, ScannerType
from app.scanners.cheap_options import CheapOptionsScanner


@pytest.fixture
def scanner():
    return CheapOptionsScanner()


@pytest.fixture
def scan_context():
    """Build a ScanContext-like object with mock polygon and config."""
    ctx = MagicMock()
    ctx.polygon = AsyncMock()
    ctx.policy_config = PolicyConfig()
    ctx.run_timestamp = "2026-01-17T16:00:00+00:00"
    ctx.cached_data = {}
    return ctx


class TestCheapOptionsScanner:
    """Tests for CheapOptionsScanner."""

    def test_scanner_type(self, scanner):
        """Scanner type should be CHEAP_OPTIONS."""
        assert scanner.scanner_type == ScannerType.CHEAP_OPTIONS

    def test_base_priority(self, scanner):
        """Base priority should be 50."""
        assert scanner.get_base_priority() == 50

    @pytest.mark.asyncio
    async def test_scan_ticker_with_valid_data(self, scanner, scan_context):
        """scan_ticker should return ScanResult with valid data."""
        bars = [
            MagicMock(close=180.0 + i, high=185.0 + i, low=178.0 + i, volume=50_000_000)
            for i in range(30)
        ]
        scan_context.cached_data = {
            "daily_bars": {"AAPL": bars},
            "options_chains": {"AAPL": [
                {
                    "details": {
                        "contract_type": "CALL",
                        "ticker": "O:AAPL260320C00185000",
                        "strike_price": 185.0,
                        "expiration_date": "2026-03-20",
                    },
                    "implied_volatility": 0.32,
                    "open_interest": 5000,
                    "day": {"volume": 500},
                    "greeks": {"delta": 0.55, "gamma": 0.03, "theta": -0.08, "vega": 0.25},
                    "last_quote": {"bid": 5.0, "ask": 5.4, "midpoint": 5.2},
                    "underlying_asset": {"price": 189.0},
                }
            ]},
        }
        scan_context.polygon.get_daily_bars_parsed.return_value = bars

        with patch("app.scanners.cheap_options.IVHistoryTable") as mock_iv:
            mock_iv.list_by_ticker.return_value = []
            result = await scanner.scan_ticker("AAPL", scan_context)

        assert result is not None
        assert result.ticker == "AAPL"

    @pytest.mark.asyncio
    async def test_scan_ticker_no_bars(self, scanner, scan_context):
        """No daily bars should return non-triggered result."""
        scan_context.cached_data = {
            "daily_bars": {},
            "options_chains": {},
        }
        scan_context.polygon.get_daily_bars_parsed.return_value = []

        with patch("app.scanners.cheap_options.IVHistoryTable") as mock_iv:
            mock_iv.list_by_ticker.return_value = []
            result = await scanner.scan_ticker("EMPTY", scan_context)

        assert result is not None
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_scan_ticker_empty_options_chain(self, scanner, scan_context):
        """Empty options chain should not trigger."""
        bars = [MagicMock(close=100.0, high=105.0, low=95.0, volume=1_000_000) for _ in range(30)]
        scan_context.cached_data = {
            "daily_bars": {"TEST": bars},
            "options_chains": {"TEST": []},
        }
        scan_context.polygon.get_daily_bars_parsed.return_value = bars

        with patch("app.scanners.cheap_options.IVHistoryTable") as mock_iv:
            mock_iv.list_by_ticker.return_value = []
            result = await scanner.scan_ticker("TEST", scan_context)

        assert result is not None
        assert result.triggered is False
