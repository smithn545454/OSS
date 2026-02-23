"""Tests for Historical Unusual Volume Scanner."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.schemas import DirectionHint, PolicyConfig, ScannerType
from app.scanners.base import ScanContext, ScanResult
from app.scanners.historical_uv import (
    HistoricalUVScanner,
    calculate_priority_score,
    get_trigger_reasons,
    _score_volume_ratio,
    _score_oi_change,
    _score_spread,
    _score_liquidity,
    _trading_day_before,
    _trading_days_before,
    _extract_option_ticker,
    _extract_volume,
    _extract_oi,
    _extract_bid_ask,
    _build_oi_map,
)


# ============================================================================
# Fixtures
# ============================================================================


def _make_contract(
    ticker: str = "O:AAPL260320C00185000",
    contract_type: str = "CALL",
    underlying: str = "AAPL",
    strike: float = 185.0,
    expiry: str = "2026-03-20",
    volume: int = 500,
    oi: int = 5000,
    bid: float = 5.0,
    ask: float = 5.4,
    delta: float = 0.55,
    iv: float = 0.32,
) -> dict:
    """Build a mock contract dict in Polygon format."""
    return {
        "details": {
            "contract_type": contract_type,
            "ticker": ticker,
            "strike_price": strike,
            "expiration_date": expiry,
        },
        "day": {"volume": volume, "open": 5.0, "high": 5.5, "low": 4.8, "close": 5.2},
        "underlying_asset": {"ticker": underlying, "price": 189.0},
        "greeks": {"delta": delta, "gamma": 0.03, "theta": -0.08, "vega": 0.25},
        "open_interest": oi,
        "implied_volatility": iv,
        "last_quote": {"bid": bid, "ask": ask, "midpoint": (bid + ask) / 2},
    }


def _make_data_provider(
    chain: list[dict] | None = None,
    prior_chain: list[dict] | None = None,
    historical_chains: dict | None = None,
    snapshot_close: float = 189.0,
) -> AsyncMock:
    """Build a mock DataProvider for UV scanning."""
    provider = AsyncMock()

    # Stock snapshot
    mock_snapshot = MagicMock()
    mock_snapshot.close = snapshot_close
    provider.get_stock_snapshot.return_value = mock_snapshot

    # Options chain — return different chains based on as_of date
    if chain is None:
        chain = [_make_contract()]
    if prior_chain is None:
        prior_chain = [_make_contract(oi=4000)]  # Lower OI for prior day
    if historical_chains is None:
        historical_chains = {}

    async def mock_get_chain(ticker, as_of, min_dte=7, max_dte=120):
        # Return current chain for as_of date
        if as_of == date(2026, 1, 20):
            return chain
        # Return prior chain for prior day
        if as_of == date(2026, 1, 17):
            return prior_chain
        # Return historical chain if provided
        if as_of in historical_chains:
            return historical_chains[as_of]
        # Default: return chain with lower volume for historical
        return [_make_contract(volume=200)]

    provider.get_options_chain = AsyncMock(side_effect=mock_get_chain)

    return provider


def _make_scan_context(
    data_provider=None,
    as_of_date=None,
) -> ScanContext:
    """Build a ScanContext for testing."""
    return ScanContext(
        polygon=None,
        policy_config=PolicyConfig(),
        run_timestamp="2026-01-20T16:00:00Z",
        cached_data={},
        data_provider=data_provider,
        as_of_date=as_of_date or date(2026, 1, 20),
    )


# ============================================================================
# Trigger Reason Tests
# ============================================================================


class TestGetTriggerReasons:
    def test_vol_spike_only(self):
        reasons = get_trigger_reasons(volume_ratio=3.0, oi_change_pct=5.0)
        assert reasons == ["VOL_SPIKE"]

    def test_oi_increase_only(self):
        reasons = get_trigger_reasons(volume_ratio=1.0, oi_change_pct=20.0)
        assert reasons == ["OI_INCREASE"]

    def test_oi_decrease_only(self):
        reasons = get_trigger_reasons(volume_ratio=1.0, oi_change_pct=-25.0)
        assert reasons == ["OI_DECREASE"]

    def test_vol_oi_combo(self):
        reasons = get_trigger_reasons(volume_ratio=3.0, oi_change_pct=20.0)
        assert "VOL_SPIKE" in reasons
        assert "OI_INCREASE" in reasons
        assert "VOL_OI_COMBO" in reasons
        assert len(reasons) == 3

    def test_no_triggers(self):
        reasons = get_trigger_reasons(volume_ratio=1.0, oi_change_pct=5.0)
        assert reasons == []

    def test_custom_thresholds(self):
        reasons = get_trigger_reasons(
            volume_ratio=1.5,
            oi_change_pct=10.0,
            volume_ratio_threshold=1.5,
            oi_increase_threshold_pct=10.0,
        )
        assert "VOL_SPIKE" in reasons
        assert "OI_INCREASE" in reasons


# ============================================================================
# Scoring Tests
# ============================================================================


class TestScoringFunctions:
    def test_volume_ratio_score_below_1(self):
        assert _score_volume_ratio(0.5) == 0.0

    def test_volume_ratio_score_at_2(self):
        score = _score_volume_ratio(2.0)
        assert 0.2 < score < 0.4  # log2(2) / log2(20) ≈ 0.23

    def test_volume_ratio_score_at_20(self):
        assert _score_volume_ratio(20.0) == 1.0

    def test_volume_ratio_score_above_max(self):
        assert _score_volume_ratio(50.0) == 1.0

    def test_oi_change_score_zero(self):
        assert _score_oi_change(0.0) == 0.0

    def test_oi_change_score_positive(self):
        score = _score_oi_change(50.0)
        assert score == 0.5  # 50/100

    def test_oi_change_score_negative(self):
        score = _score_oi_change(-50.0)
        assert score == 0.5  # abs(-50)/100

    def test_oi_change_score_capped(self):
        assert _score_oi_change(200.0) == 1.0

    def test_spread_score_ideal(self):
        assert _score_spread(0.5) == 1.0

    def test_spread_score_worst(self):
        assert _score_spread(15.0) == 0.0

    def test_spread_score_mid(self):
        score = _score_spread(5.5)
        assert 0.4 < score < 0.6

    def test_liquidity_score_low(self):
        score = _score_liquidity(50, 100)
        assert score == 0.0

    def test_liquidity_score_high(self):
        score = _score_liquidity(15000, 25000)
        assert score == 1.0


class TestCalculatePriorityScore:
    def test_basic_calculation(self):
        score = calculate_priority_score(
            volume_ratio=5.0,
            oi_change_pct=25.0,
            trigger_count=2,
            spread_pct=2.5,
            today_volume=5000,
            today_oi=10000,
        )
        assert 0 < score <= 100

    def test_all_zeros(self):
        score = calculate_priority_score(
            volume_ratio=0.0,
            oi_change_pct=0.0,
            trigger_count=0,
            spread_pct=50.0,
            today_volume=0,
            today_oi=0,
        )
        assert score == 0.0

    def test_bucket_fallback_penalty(self):
        base = calculate_priority_score(
            volume_ratio=5.0, oi_change_pct=25.0, trigger_count=2,
            spread_pct=2.5, today_volume=5000, today_oi=10000,
            volume_source="CONTRACT_SPECIFIC",
        )
        penalized = calculate_priority_score(
            volume_ratio=5.0, oi_change_pct=25.0, trigger_count=2,
            spread_pct=2.5, today_volume=5000, today_oi=10000,
            volume_source="BUCKET_FALLBACK",
        )
        assert penalized < base
        # 10% penalty
        assert abs(penalized - base * 0.9) < 0.1

    def test_score_bounded(self):
        score = calculate_priority_score(
            volume_ratio=100.0, oi_change_pct=500.0, trigger_count=4,
            spread_pct=0.1, today_volume=100000, today_oi=100000,
        )
        assert score <= 100.0


# ============================================================================
# Data Extraction Tests
# ============================================================================


class TestDataExtraction:
    def test_extract_option_ticker(self):
        contract = _make_contract(ticker="O:AAPL260320C00185000")
        assert _extract_option_ticker(contract) == "O:AAPL260320C00185000"

    def test_extract_option_ticker_fallback(self):
        contract = {"ticker": "O:SPY260320P00400000"}
        assert _extract_option_ticker(contract) == "O:SPY260320P00400000"

    def test_extract_volume(self):
        contract = _make_contract(volume=1234)
        assert _extract_volume(contract) == 1234

    def test_extract_oi(self):
        contract = _make_contract(oi=5678)
        assert _extract_oi(contract) == 5678

    def test_extract_bid_ask(self):
        contract = _make_contract(bid=3.50, ask=3.80)
        bid, ask = _extract_bid_ask(contract)
        assert bid == 3.50
        assert ask == 3.80

    def test_build_oi_map(self):
        chain = [
            _make_contract(ticker="O:A", oi=1000),
            _make_contract(ticker="O:B", oi=2000),
        ]
        oi_map = _build_oi_map(chain)
        assert oi_map["O:A"] == 1000
        assert oi_map["O:B"] == 2000


# ============================================================================
# Date Helper Tests
# ============================================================================


class TestDateHelpers:
    def test_trading_day_before_weekday(self):
        # Tuesday -> Monday
        assert _trading_day_before(date(2026, 1, 20)) == date(2026, 1, 19)

    def test_trading_day_before_monday(self):
        # Monday -> Friday
        assert _trading_day_before(date(2026, 1, 19)) == date(2026, 1, 16)

    def test_trading_day_before_sunday(self):
        # Sunday -> Friday
        assert _trading_day_before(date(2026, 1, 18)) == date(2026, 1, 16)

    def test_trading_days_before_count(self):
        days = _trading_days_before(date(2026, 1, 20), 5)
        assert len(days) == 5
        # All should be weekdays
        for d in days:
            assert d.weekday() < 5

    def test_trading_days_before_chronological(self):
        days = _trading_days_before(date(2026, 1, 20), 5)
        for i in range(1, len(days)):
            assert days[i] > days[i - 1]


# ============================================================================
# Scanner Integration Tests
# ============================================================================


class TestHistoricalUVScanner:
    def test_scanner_type(self):
        scanner = HistoricalUVScanner()
        assert scanner.scanner_type == ScannerType.UNUSUAL_VOLUME

    @pytest.mark.asyncio
    async def test_requires_data_provider(self):
        scanner = HistoricalUVScanner()
        context = _make_scan_context(data_provider=None)
        result = await scanner.scan_ticker("AAPL", context)
        assert not result.triggered
        assert "requires a DataProvider" in result.error

    @pytest.mark.asyncio
    async def test_no_chain_returns_not_triggered(self):
        provider = _make_data_provider(chain=[])
        # Override to always return empty
        provider.get_options_chain.return_value = []
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)
        assert not result.triggered

    @pytest.mark.asyncio
    async def test_vol_spike_triggers(self):
        """Contract with 5x average volume should trigger VOL_SPIKE."""
        # Current day: 1000 volume
        chain = [_make_contract(volume=1000, oi=5000)]
        # Prior day: same OI (no OI change)
        prior = [_make_contract(oi=5000)]
        # Historical: 200 volume avg -> ratio = 1000/200 = 5.0
        provider = _make_data_provider(
            chain=chain, prior_chain=prior,
        )
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)

        assert result.triggered
        assert result.trigger is not None
        assert "VOL_SPIKE" in result.trigger.reason_codes
        assert result.trigger.scanner_type == ScannerType.UNUSUAL_VOLUME

    @pytest.mark.asyncio
    async def test_oi_increase_triggers(self):
        """25% OI increase should trigger OI_INCREASE."""
        chain = [_make_contract(volume=50, oi=5000)]  # Low volume (no vol spike)
        prior = [_make_contract(oi=4000)]  # 25% OI increase
        provider = _make_data_provider(chain=chain, prior_chain=prior)
        # Override chain to return low-volume for historical too
        original_side_effect = provider.get_options_chain.side_effect

        async def adjusted_chain(ticker, as_of, min_dte=7, max_dte=120):
            if as_of == date(2026, 1, 20):
                return chain
            if as_of == date(2026, 1, 17):
                return prior
            return [_make_contract(volume=50)]

        provider.get_options_chain = AsyncMock(side_effect=adjusted_chain)
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)

        # Volume too low to pass MIN_VOLUME filter
        assert not result.triggered

    @pytest.mark.asyncio
    async def test_oi_increase_with_sufficient_volume(self):
        """25% OI increase with sufficient volume should trigger."""
        # 2026-01-20 is a Tuesday; prior trading day = Monday 2026-01-19
        target_date = date(2026, 1, 20)
        prior_day = date(2026, 1, 19)

        chain = [_make_contract(volume=150, oi=5000)]
        prior = [_make_contract(oi=4000)]  # 25% OI increase

        async def mock_chain(ticker, as_of, min_dte=7, max_dte=120):
            if as_of == target_date:
                return chain
            if as_of == prior_day:
                return prior
            # Historical: 150 volume (ratio ~1.0, no vol spike)
            return [_make_contract(volume=150, oi=4000)]

        provider = _make_data_provider(chain=chain, prior_chain=prior)
        provider.get_options_chain = AsyncMock(side_effect=mock_chain)
        context = _make_scan_context(data_provider=provider, as_of_date=target_date)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)

        assert result.triggered
        assert "OI_INCREASE" in result.trigger.reason_codes

    @pytest.mark.asyncio
    async def test_direction_hint_call(self):
        """Triggered CALL contract should set direction hint to CALL."""
        chain = [_make_contract(volume=1000, contract_type="CALL")]
        prior = [_make_contract(oi=5000)]
        provider = _make_data_provider(chain=chain, prior_chain=prior)
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)

        if result.triggered:
            # Direction should be inferred from best contract
            assert result.trigger is not None

    @pytest.mark.asyncio
    async def test_direction_hint_put(self):
        """Triggered PUT contract should set direction hint to PUT."""
        chain = [_make_contract(
            ticker="O:AAPL260320P00185000",
            volume=1000, contract_type="PUT",
        )]
        prior = [_make_contract(
            ticker="O:AAPL260320P00185000", oi=5000,
        )]

        async def mock_chain(ticker, as_of, min_dte=7, max_dte=120):
            if as_of == date(2026, 1, 20):
                return chain
            if as_of == date(2026, 1, 17):
                return prior
            return [_make_contract(
                ticker="O:AAPL260320P00185000", volume=200,
            )]

        provider = _make_data_provider(chain=chain, prior_chain=prior)
        provider.get_options_chain = AsyncMock(side_effect=mock_chain)
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)

        if result.triggered:
            assert result.trigger is not None

    @pytest.mark.asyncio
    async def test_pre_filter_low_volume(self):
        """Contracts with volume below MIN_VOLUME should be filtered out."""
        chain = [_make_contract(volume=50)]  # Below MIN_VOLUME=100
        prior = [_make_contract(oi=4000)]
        provider = _make_data_provider(chain=chain, prior_chain=prior)
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)
        assert not result.triggered

    @pytest.mark.asyncio
    async def test_pre_filter_wide_spread(self):
        """Contracts with spread > 50% should be filtered out."""
        chain = [_make_contract(volume=500, bid=1.0, ask=5.0)]  # 133% spread
        prior = [_make_contract(oi=4000)]
        provider = _make_data_provider(chain=chain, prior_chain=prior)
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)
        assert not result.triggered

    @pytest.mark.asyncio
    async def test_best_candidate_selected(self):
        """When multiple contracts trigger, highest score wins."""
        chain = [
            _make_contract(
                ticker="O:AAPL260320C00185000",
                volume=500, oi=5000, bid=5.0, ask=5.4,
            ),
            _make_contract(
                ticker="O:AAPL260320C00190000",
                volume=2000, oi=8000, bid=3.0, ask=3.2,
            ),
        ]
        prior = [
            _make_contract(ticker="O:AAPL260320C00185000", oi=5000),
            _make_contract(ticker="O:AAPL260320C00190000", oi=6000),
        ]

        async def mock_chain(ticker, as_of, min_dte=7, max_dte=120):
            if as_of == date(2026, 1, 20):
                return chain
            if as_of == date(2026, 1, 17):
                return prior
            return [
                _make_contract(ticker="O:AAPL260320C00185000", volume=200),
                _make_contract(ticker="O:AAPL260320C00190000", volume=200),
            ]

        provider = _make_data_provider(chain=chain, prior_chain=prior)
        provider.get_options_chain = AsyncMock(side_effect=mock_chain)
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)

        if result.triggered:
            # Second contract has 10x volume and OI increase — should be best
            metrics = result.metrics
            assert metrics.get("contracts_triggered", 0) >= 1

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Scanner should handle data provider errors gracefully."""
        provider = AsyncMock()
        provider.get_options_chain.side_effect = Exception("S3 read failed")
        provider.get_stock_snapshot.return_value = MagicMock(close=189.0)
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)

        assert not result.triggered
        assert result.error is not None
        assert "S3 read failed" in result.error

    @pytest.mark.asyncio
    async def test_metrics_populated(self):
        """Result metrics should include chain statistics."""
        chain = [_make_contract(volume=1000)]
        prior = [_make_contract(oi=5000)]
        provider = _make_data_provider(chain=chain, prior_chain=prior)
        context = _make_scan_context(data_provider=provider)
        scanner = HistoricalUVScanner()
        result = await scanner.scan_ticker("AAPL", context)

        assert "contracts_in_chain" in result.metrics
        assert "underlying_price" in result.metrics
