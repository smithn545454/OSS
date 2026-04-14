"""Tests for the Cheap Options Scanner.

Covers CheapOptionsScanner.scan_ticker() with mocked Polygon client,
IV history storage, and edge cases.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import DirectionHint, PolicyConfig, ScannerType
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
                        "expiration_date": (date.today() + timedelta(days=30)).strftime("%Y-%m-%d"),
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


class _Bar:
    """Minimal daily-bar double with real attribute values (not MagicMock)."""

    __slots__ = ("close", "high", "low", "open", "volume")

    def __init__(self, close, high=None, low=None, volume=50_000_000):
        self.close = close
        self.high = high if high is not None else close * 1.01
        self.low = low if low is not None else close * 0.99
        self.open = close
        self.volume = volume


def _build_bars(prices):
    """Build minimal daily-bar objects from a list of closes."""
    return [_Bar(close=p) for p in prices]


def _build_chain(atm_strike: float = 100.0):
    """Build a minimal options chain with a call and put at the ATM strike."""
    return [
        {
            "details": {
                "contract_type": "CALL",
                "ticker": f"O:TST260320C{int(atm_strike * 1000):08d}",
                "strike_price": atm_strike,
                "expiration_date": (date.today() + timedelta(days=30)).strftime("%Y-%m-%d"),
            },
            "implied_volatility": 0.18,  # Low IV → triggers iv_rv_ratio
            "open_interest": 5000,
            "day": {"volume": 500},
            "greeks": {"delta": 0.55, "gamma": 0.03, "theta": -0.08, "vega": 0.25},
            "last_quote": {"bid": 2.0, "ask": 2.2, "midpoint": 2.1},
        },
        {
            "details": {
                "contract_type": "PUT",
                "ticker": f"O:TST260320P{int(atm_strike * 1000):08d}",
                "strike_price": atm_strike,
                "expiration_date": (date.today() + timedelta(days=30)).strftime("%Y-%m-%d"),
            },
            "implied_volatility": 0.18,
            "open_interest": 5000,
            "day": {"volume": 500},
            "greeks": {"delta": -0.45, "gamma": 0.03, "theta": -0.08, "vega": 0.25},
            "last_quote": {"bid": 2.0, "ask": 2.2, "midpoint": 2.1},
        },
    ]


class TestCheapOptionsMomentumFilter:
    """Tests for the directional-momentum filter on CHEAP_OPTIONS."""

    @pytest.fixture
    def rising_underlying(self):
        # ~10% gain over 5 sessions, steady trend
        return _build_bars([95.0, 96.0, 97.5, 99.0, 101.0, 102.5, 103.5, 104.0, 104.5, 105.0] * 3)

    @pytest.fixture
    def falling_underlying(self):
        return _build_bars([105.0, 104.0, 102.5, 101.0, 99.0, 97.5, 96.5, 96.0, 95.5, 95.0] * 3)

    @pytest.fixture
    def flat_underlying(self):
        # 30-bar zig-zag that starts AND ends at 100 → RV > 0, 5d return = 0.
        # Pattern: 100, 100.5, 100, 100.5, ... last close = 100.
        return _build_bars(
            [100.0 if i % 2 == 0 else 100.5 for i in range(30)]
        )

    @pytest.fixture
    def flat_spy(self):
        # Same pattern at different scale → 5d return identical → rs_5d = 0.
        return _build_bars(
            [450.0 if i % 2 == 0 else 452.25 for i in range(30)]
        )

    def _mk_context(self, underlying_bars, spy_bars, require_momentum, threshold=0.0):
        base = PolicyConfig()
        config = base.model_copy(
            update={
                "scanner": base.scanner.model_copy(
                    update={
                        "cheap_options": base.scanner.cheap_options.model_copy(
                            update={
                                "require_momentum": require_momentum,
                                "rs_5d_threshold": threshold,
                            }
                        )
                    }
                )
            }
        )

        ctx = MagicMock()
        ctx.polygon = AsyncMock()
        ctx.policy_config = config
        ctx.run_timestamp = "2026-04-14T16:00:00+00:00"
        ctx.as_of_date = None
        ctx.data_provider = None
        ctx.cached_data = {
            "daily_bars": {"TST": underlying_bars, "SPY": spy_bars},
            "options_chains": {"TST": _build_chain()},
        }
        return ctx

    @pytest.mark.asyncio
    async def test_momentum_filter_off_preserves_none_direction(
        self, scanner, flat_underlying, flat_spy
    ):
        """With require_momentum=False, CHEAP_OPTIONS behaves as before."""
        ctx = self._mk_context(flat_underlying, flat_spy, require_momentum=False)
        with patch("app.scanners.cheap_options.IVHistoryTable") as mock_iv:
            mock_iv.calculate_percentile = AsyncMock(return_value=20.0)
            result = await scanner.scan_ticker("TST", ctx)

        assert result.triggered is True
        assert "triggered_direction" not in result.metrics
        assert "rs_5d" not in result.metrics

    @pytest.mark.asyncio
    async def test_momentum_filter_rising_emits_call(
        self, scanner, rising_underlying, flat_spy
    ):
        """Positive 5d RS (underlying up, SPY flat) → CALL direction."""
        ctx = self._mk_context(rising_underlying, flat_spy, require_momentum=True)
        with patch("app.scanners.cheap_options.IVHistoryTable") as mock_iv:
            mock_iv.calculate_percentile = AsyncMock(return_value=20.0)
            result = await scanner.scan_ticker("TST", ctx)

        assert result.triggered is True
        assert result.metrics["triggered_direction"] == "UP"
        assert result.metrics["rs_5d"] > 0
        assert "CHEAP_OPTIONS_MOMENTUM_UP" in result.trigger.reason_codes

    @pytest.mark.asyncio
    async def test_momentum_filter_falling_emits_put(
        self, scanner, falling_underlying, flat_spy
    ):
        """Negative 5d RS → PUT direction."""
        ctx = self._mk_context(falling_underlying, flat_spy, require_momentum=True)
        with patch("app.scanners.cheap_options.IVHistoryTable") as mock_iv:
            mock_iv.calculate_percentile = AsyncMock(return_value=20.0)
            result = await scanner.scan_ticker("TST", ctx)

        assert result.triggered is True
        assert result.metrics["triggered_direction"] == "DOWN"
        assert result.metrics["rs_5d"] < 0
        assert "CHEAP_OPTIONS_MOMENTUM_DOWN" in result.trigger.reason_codes

    @pytest.mark.asyncio
    async def test_momentum_filter_flat_drops_signal(
        self, scanner, flat_underlying, flat_spy
    ):
        """Zero RS with zero threshold drops the signal (neither strict inequality holds)."""
        ctx = self._mk_context(flat_underlying, flat_spy, require_momentum=True)
        with patch("app.scanners.cheap_options.IVHistoryTable") as mock_iv:
            mock_iv.calculate_percentile = AsyncMock(return_value=20.0)
            result = await scanner.scan_ticker("TST", ctx)

        assert result.triggered is False
        # rs_5d should still be recorded for telemetry
        assert result.metrics.get("rs_5d") == 0.0

    @pytest.mark.asyncio
    async def test_momentum_filter_neutral_band_drops_weak_signal(
        self, scanner, flat_spy
    ):
        """RS within the neutral band (|rs| ≤ threshold) drops the signal."""
        # Underlying up ~0.5% over 5d — below a 1% threshold
        weak_rising = _build_bars([100.0] * 25 + [100.5] * 5)
        ctx = self._mk_context(weak_rising, flat_spy, require_momentum=True, threshold=1.0)
        with patch("app.scanners.cheap_options.IVHistoryTable") as mock_iv:
            mock_iv.calculate_percentile = AsyncMock(return_value=20.0)
            result = await scanner.scan_ticker("TST", ctx)

        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_momentum_filter_missing_spy_bars_drops_signal(
        self, scanner, rising_underlying
    ):
        """When SPY bars are missing, the filter fails closed rather than trigger spuriously."""
        base = PolicyConfig()
        config = base.model_copy(
            update={
                "scanner": base.scanner.model_copy(
                    update={
                        "cheap_options": base.scanner.cheap_options.model_copy(
                            update={"require_momentum": True}
                        )
                    }
                )
            }
        )

        ctx = MagicMock()
        ctx.polygon = AsyncMock()
        ctx.policy_config = config
        ctx.run_timestamp = "2026-04-14T16:00:00+00:00"
        ctx.as_of_date = None
        ctx.data_provider = None
        ctx.cached_data = {
            "daily_bars": {"TST": rising_underlying},  # No SPY
            "options_chains": {"TST": _build_chain()},
        }

        with patch("app.scanners.cheap_options.IVHistoryTable") as mock_iv:
            mock_iv.calculate_percentile = AsyncMock(return_value=20.0)
            result = await scanner.scan_ticker("TST", ctx)

        assert result.triggered is False
        assert "Insufficient bars" in (result.error or "")


class TestCheapOptionsDirectionHintAPI:
    """Verify DirectionHint import path is stable for downstream consumers."""

    def test_direction_hint_enum_values(self):
        assert DirectionHint.CALL.value
        assert DirectionHint.PUT.value
        assert DirectionHint.NONE.value
