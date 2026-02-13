"""Tests for Stage 2: Underlying Quality Filters."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.core.schemas import (
    DirectionHint,
    Opportunity,
    ScannerTrigger,
    ScannerType,
    UnderlyingFilterConfig,
)
from app.filters.underlying import (
    UnderlyingFilter,
    FilterResult,
    FILTER_FAIL_UNDERLYING_PRICE,
    FILTER_FAIL_DOLLAR_VOLUME,
    FILTER_FAIL_MISSING_BARS,
    FILTER_FAIL_EARNINGS_WINDOW,
)
from app.services.polygon import DailyBar


@pytest.fixture
def default_config() -> UnderlyingFilterConfig:
    """Default filter configuration."""
    return UnderlyingFilterConfig(
        min_price=5.00,
        min_avg_dollar_volume=20_000_000,
        max_missing_bars=2,
        exclude_earnings_within_days=0,
    )


@pytest.fixture
def sample_opportunity() -> Opportunity:
    """Sample opportunity for testing."""
    return Opportunity(
        underlying_ticker="AAPL",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        scanner_triggers=[
            ScannerTrigger(
                scanner_type=ScannerType.BREAKOUT,
                reason_codes=["BREAKOUT_CLOSE_ABOVE_RANGE"],
                metrics={"prior_N_day_high": 175.0, "today_close": 180.0},
                triggered_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
        direction_hint=DirectionHint.CALL,
        priority_score=75,
    )


@pytest.fixture
def sample_bars() -> list[DailyBar]:
    """Sample daily bars for testing.

    Dates are generated relative to today so they always fall within
    the 30-day lookback window used by _check_data_completeness.
    """
    from datetime import timedelta
    bars = []
    today = datetime.now(timezone.utc).date()
    for i in range(30):
        bar_date = today - timedelta(days=29 - i)
        bars.append(
            DailyBar(
                ticker="AAPL",
                date=bar_date.strftime("%Y-%m-%d"),
                open=175.0 + i * 0.1,
                high=176.0 + i * 0.1,
                low=174.0 + i * 0.1,
                close=175.5 + i * 0.1,
                volume=50_000_000,  # 50M volume
            )
        )
    return bars


class TestUnderlyingFilter:
    """Tests for UnderlyingFilter class."""

    def test_init_with_config(self, default_config):
        """Test initialization with configuration."""
        filter = UnderlyingFilter(default_config)
        assert filter._config == default_config

    def test_check_min_price_pass(self, default_config):
        """Test min price filter passes for valid price."""
        filter = UnderlyingFilter(default_config)
        metrics = {}
        
        result = filter._check_min_price(10.00, metrics)
        
        assert result is True
        assert metrics["min_price_passed"] is True
        assert metrics["min_price_threshold"] == 5.00

    def test_check_min_price_fail(self, default_config):
        """Test min price filter fails for low price."""
        filter = UnderlyingFilter(default_config)
        metrics = {}
        
        result = filter._check_min_price(3.50, metrics)
        
        assert result is False
        assert metrics["min_price_passed"] is False

    def test_check_min_price_boundary(self, default_config):
        """Test min price filter at boundary value."""
        filter = UnderlyingFilter(default_config)
        metrics = {}
        
        result = filter._check_min_price(5.00, metrics)
        
        assert result is True
        assert metrics["min_price_passed"] is True

    def test_check_dollar_volume_pass(self, default_config, sample_bars):
        """Test dollar volume filter passes for liquid stock."""
        filter = UnderlyingFilter(default_config)
        metrics = {}
        
        result = filter._check_dollar_volume(sample_bars, metrics)
        
        assert result is True
        assert metrics["dollar_volume_passed"] is True
        # Average: ~175.5 * 50M = ~8.775B >> $20M threshold
        assert metrics["avg_dollar_volume"] > 20_000_000

    def test_check_dollar_volume_fail(self, default_config):
        """Test dollar volume filter fails for illiquid stock."""
        filter = UnderlyingFilter(default_config)
        metrics = {}
        
        # Create low volume bars
        bars = [
            DailyBar(
                ticker="LOWVOL",
                date=f"2026-01-{(i + 1):02d}",
                open=10.0,
                high=10.5,
                low=9.5,
                close=10.0,
                volume=100_000,  # 100K volume -> $1M dollar volume
            )
            for i in range(20)
        ]
        
        result = filter._check_dollar_volume(bars, metrics)
        
        assert result is False
        assert metrics["dollar_volume_passed"] is False

    def test_check_dollar_volume_insufficient_bars(self, default_config):
        """Test dollar volume filter fails with insufficient bars."""
        filter = UnderlyingFilter(default_config)
        metrics = {}
        
        # Only 10 bars (need 20)
        bars = [
            DailyBar(
                ticker="AAPL",
                date=f"2026-01-{(i + 1):02d}",
                open=175.0,
                high=176.0,
                low=174.0,
                close=175.5,
                volume=50_000_000,
            )
            for i in range(10)
        ]
        
        result = filter._check_dollar_volume(bars, metrics)
        
        assert result is False
        assert metrics["dollar_volume_bars_available"] == 10

    def test_check_data_completeness_pass(self, default_config, sample_bars):
        """Test data completeness passes with sufficient bars."""
        filter = UnderlyingFilter(default_config)
        metrics = {}
        
        result = filter._check_data_completeness(sample_bars, metrics)
        
        assert result is True
        assert metrics["data_completeness_passed"] is True

    def test_check_data_completeness_fail(self, default_config):
        """Test data completeness fails with too many missing bars."""
        filter = UnderlyingFilter(default_config)
        metrics = {}
        
        # Only 10 bars when expecting ~21
        bars = [
            DailyBar(
                ticker="AAPL",
                date=f"2026-01-{(i + 1):02d}",
                open=175.0,
                high=176.0,
                low=174.0,
                close=175.5,
                volume=50_000_000,
            )
            for i in range(10)
        ]
        
        result = filter._check_data_completeness(bars, metrics)
        
        assert result is False
        assert metrics["data_completeness_passed"] is False
        assert metrics["missing_bars"] > 2

    def test_check_data_completeness_no_bars(self, default_config):
        """Test data completeness fails with no bars."""
        filter = UnderlyingFilter(default_config)
        metrics = {}
        
        result = filter._check_data_completeness([], metrics)
        
        assert result is False
        assert metrics["actual_bars_30d"] == 0


class TestUnderlyingFilterIntegration:
    """Integration tests for UnderlyingFilter."""

    @pytest.mark.asyncio
    async def test_filter_single_pass(self, default_config, sample_opportunity, sample_bars):
        """Test filtering a single opportunity that passes all filters."""
        filter = UnderlyingFilter(default_config)
        
        # Mock prev_close data
        prev_close = {"c": 178.50}
        
        result = await filter._filter_single(
            sample_opportunity,
            sample_bars,
            prev_close,
        )
        
        assert result.passed is True
        assert len(result.failed_filters) == 0

    @pytest.mark.asyncio
    async def test_filter_single_fail_price(self, default_config, sample_opportunity, sample_bars):
        """Test filtering fails when price is too low."""
        filter = UnderlyingFilter(default_config)
        
        # Low price
        prev_close = {"c": 3.50}
        
        result = await filter._filter_single(
            sample_opportunity,
            sample_bars,
            prev_close,
        )
        
        assert result.passed is False
        assert FILTER_FAIL_UNDERLYING_PRICE in result.failed_filters

    @pytest.mark.asyncio
    async def test_filter_single_multiple_failures(self, default_config):
        """Test filtering with multiple filter failures."""
        filter = UnderlyingFilter(default_config)
        
        # Create failing conditions
        opp = Opportunity(
            underlying_ticker="BADSTOCK",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            scanner_triggers=[],
            direction_hint=DirectionHint.NONE,
            priority_score=50,
        )
        
        # Low price and insufficient bars
        prev_close = {"c": 2.00}
        bars = []  # No bars
        
        result = await filter._filter_single(opp, bars, prev_close)
        
        assert result.passed is False
        assert FILTER_FAIL_UNDERLYING_PRICE in result.failed_filters
        assert FILTER_FAIL_DOLLAR_VOLUME in result.failed_filters
        assert FILTER_FAIL_MISSING_BARS in result.failed_filters


class TestEarningsCacheIntegration:
    """Tests for earnings cache wiring into UnderlyingFilter."""

    def test_accepts_earnings_cache_parameter(self):
        """UnderlyingFilter constructor accepts earnings_cache kwarg."""
        config = UnderlyingFilterConfig(exclude_earnings_within_days=5)
        mock_cache = AsyncMock()
        uf = UnderlyingFilter(config, earnings_cache=mock_cache)
        assert uf._earnings_cache is mock_cache

    def test_no_earnings_cache_defaults_to_none(self):
        """Without earnings_cache kwarg, _earnings_cache is None."""
        config = UnderlyingFilterConfig(exclude_earnings_within_days=5)
        uf = UnderlyingFilter(config)
        assert uf._earnings_cache is None

    @pytest.mark.asyncio
    async def test_earnings_filter_within_window(self):
        """Ticker with earnings within window should be filtered out."""
        config = UnderlyingFilterConfig(exclude_earnings_within_days=5)
        mock_cache = AsyncMock()
        mock_cache.get_days_to_earnings.return_value = 3  # 3 days <= 5 days
        uf = UnderlyingFilter(config, earnings_cache=mock_cache)

        metrics: dict = {}
        result = await uf._check_earnings_window("AAPL", metrics)

        assert result is False
        assert metrics["days_to_earnings"] == 3
        assert metrics["earnings_window_passed"] is False

    @pytest.mark.asyncio
    async def test_earnings_filter_outside_window(self):
        """Ticker with earnings outside window should pass."""
        config = UnderlyingFilterConfig(exclude_earnings_within_days=5)
        mock_cache = AsyncMock()
        mock_cache.get_days_to_earnings.return_value = 20  # 20 days > 5 days
        uf = UnderlyingFilter(config, earnings_cache=mock_cache)

        metrics: dict = {}
        result = await uf._check_earnings_window("AAPL", metrics)

        assert result is True
        assert metrics["days_to_earnings"] == 20
        assert metrics["earnings_window_passed"] is True

    @pytest.mark.asyncio
    async def test_earnings_filter_fails_open_on_error(self):
        """If earnings cache raises, ticker should pass (fail-open)."""
        config = UnderlyingFilterConfig(exclude_earnings_within_days=5)
        mock_cache = AsyncMock()
        mock_cache.get_days_to_earnings.side_effect = RuntimeError("API down")
        uf = UnderlyingFilter(config, earnings_cache=mock_cache)

        metrics: dict = {}
        result = await uf._check_earnings_window("AAPL", metrics)

        assert result is True
        assert metrics["earnings_window_passed"] is True

    @pytest.mark.asyncio
    async def test_earnings_filter_no_cache_passes(self):
        """If no earnings cache is set, ticker should pass."""
        config = UnderlyingFilterConfig(exclude_earnings_within_days=5)
        uf = UnderlyingFilter(config, earnings_cache=None)

        metrics: dict = {}
        result = await uf._check_earnings_window("AAPL", metrics)

        assert result is True
        assert metrics["earnings_window_passed"] is True
