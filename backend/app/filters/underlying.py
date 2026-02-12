"""Stage 2: Underlying Quality Filters.

Applies cheap filters to remove obviously unsuitable underlyings before
the expensive contract selection phase.

Filters implemented per Section 11 of OSS_Complete_Requirements.md:
1. Minimum Underlying Price ($5.00)
2. Minimum Average Dollar Volume ($20M)
3. Data Completeness (max 2 missing bars in 30 days)
4. Earnings Window (optional, default OFF)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.schemas import Opportunity, PipelineStage, UnderlyingFilterConfig
from app.services.polygon import DailyBar, PolygonClient

logger = logging.getLogger(__name__)


# Reason codes per Appendix A
FILTER_FAIL_UNDERLYING_PRICE = "FILTER_FAIL_UNDERLYING_PRICE"
FILTER_FAIL_DOLLAR_VOLUME = "FILTER_FAIL_DOLLAR_VOLUME"
FILTER_FAIL_MISSING_BARS = "FILTER_FAIL_MISSING_BARS"
FILTER_FAIL_EARNINGS_WINDOW = "FILTER_FAIL_EARNINGS_WINDOW"


@dataclass
class FilterResult:
    """Result of filtering a single opportunity."""

    opportunity: Opportunity
    passed: bool
    failed_filters: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class UnderlyingFilterStats:
    """Statistics from the underlying filter stage."""

    opportunities_in: int = 0
    opportunities_passed: int = 0
    opportunities_failed: int = 0
    failure_counts: dict[str, int] = field(default_factory=dict)
    filter_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for telemetry."""
        return {
            "opportunities_in": self.opportunities_in,
            "opportunities_passed": self.opportunities_passed,
            "opportunities_failed": self.opportunities_failed,
            "failure_counts": self.failure_counts,
            "filter_metrics": self.filter_metrics,
        }


class UnderlyingFilter:
    """Applies underlying quality filters to opportunities.
    
    Per Section 11 of requirements:
    - Filter 1: Minimum Underlying Price (>= $5.00)
    - Filter 2: Minimum Average Dollar Volume (>= $20M)
    - Filter 3: Data Completeness (<= 2 missing bars in 30 days)
    - Filter 4: Earnings Window (optional, default OFF)
    """

    def __init__(
        self,
        config: UnderlyingFilterConfig,
        polygon_client: Optional[PolygonClient] = None,
        earnings_cache: Optional[Any] = None,
    ) -> None:
        """Initialize the underlying filter.

        Args:
            config: Filter configuration from policy
            polygon_client: Optional Polygon client (will be set externally if not provided)
            earnings_cache: Optional EarningsCacheService for earnings window filter
        """
        self._config = config
        self._polygon: Optional[PolygonClient] = polygon_client
        self._earnings_cache = earnings_cache
        self._stats = UnderlyingFilterStats()

    def set_polygon_client(self, client: PolygonClient) -> None:
        """Set the Polygon client for data fetching."""
        self._polygon = client

    async def filter_opportunities(
        self,
        opportunities: list[Opportunity],
    ) -> tuple[list[Opportunity], UnderlyingFilterStats]:
        """Filter opportunities based on underlying quality.
        
        Args:
            opportunities: List of opportunities from Stage 1
            
        Returns:
            Tuple of (filtered opportunities, statistics)
        """
        if not self._polygon:
            raise RuntimeError("Polygon client not set")

        self._stats = UnderlyingFilterStats(opportunities_in=len(opportunities))
        passed_opportunities: list[Opportunity] = []

        # Get unique tickers
        tickers = list(set(opp.underlying_ticker for opp in opportunities))
        logger.info(f"Filtering {len(opportunities)} opportunities across {len(tickers)} tickers")

        # Batch fetch daily bars for all tickers (need 60+ days for calculations)
        daily_bars = await self._polygon.get_daily_bars_batch(tickers, days=60)

        # Get current prices (previous close)
        prev_closes = await self._polygon.get_previous_close_batch(tickers)

        # Process each opportunity
        for opp in opportunities:
            result = await self._filter_single(
                opp,
                daily_bars.get(opp.underlying_ticker, []),
                prev_closes.get(opp.underlying_ticker),
            )

            if result.passed:
                passed_opportunities.append(opp)
                self._stats.opportunities_passed += 1
            else:
                self._stats.opportunities_failed += 1
                for failure in result.failed_filters:
                    self._stats.failure_counts[failure] = (
                        self._stats.failure_counts.get(failure, 0) + 1
                    )

            # Store per-ticker metrics
            self._stats.filter_metrics[opp.underlying_ticker] = result.metrics

        logger.info(
            f"Underlying filters: {self._stats.opportunities_in} in, "
            f"{self._stats.opportunities_passed} passed, "
            f"{self._stats.opportunities_failed} failed"
        )

        return passed_opportunities, self._stats

    async def _filter_single(
        self,
        opportunity: Opportunity,
        bars: list[DailyBar],
        prev_close: Optional[dict[str, Any]],
    ) -> FilterResult:
        """Apply all filters to a single opportunity.
        
        Args:
            opportunity: The opportunity to filter
            bars: Daily bars for the underlying
            prev_close: Previous close data
            
        Returns:
            FilterResult with pass/fail status and metrics
        """
        ticker = opportunity.underlying_ticker
        failed_filters: list[str] = []
        metrics: dict[str, Any] = {"ticker": ticker}

        # Get current price from prev_close
        if prev_close:
            current_price = prev_close.get("c", 0.0)
        elif bars:
            current_price = bars[-1].close if bars else 0.0
        else:
            current_price = 0.0

        metrics["underlying_price"] = current_price

        # Filter 1: Minimum Underlying Price
        if not self._check_min_price(current_price, metrics):
            failed_filters.append(FILTER_FAIL_UNDERLYING_PRICE)

        # Filter 2: Minimum Average Dollar Volume
        if not self._check_dollar_volume(bars, metrics):
            failed_filters.append(FILTER_FAIL_DOLLAR_VOLUME)

        # Filter 3: Data Completeness
        if not self._check_data_completeness(bars, metrics):
            failed_filters.append(FILTER_FAIL_MISSING_BARS)

        # Filter 4: Earnings Window (if enabled)
        if self._config.exclude_earnings_within_days > 0:
            if not await self._check_earnings_window(ticker, metrics):
                failed_filters.append(FILTER_FAIL_EARNINGS_WINDOW)

        return FilterResult(
            opportunity=opportunity,
            passed=len(failed_filters) == 0,
            failed_filters=failed_filters,
            metrics=metrics,
        )

    def _check_min_price(
        self,
        current_price: float,
        metrics: dict[str, Any],
    ) -> bool:
        """Check minimum underlying price filter.
        
        Args:
            current_price: Current underlying price
            metrics: Dict to store filter metrics
            
        Returns:
            True if passes filter, False otherwise
        """
        min_price = self._config.min_price
        passed = current_price >= min_price

        metrics["min_price_threshold"] = min_price
        metrics["min_price_passed"] = passed

        if not passed:
            logger.debug(
                f"Filter fail: price ${current_price:.2f} < ${min_price:.2f}"
            )

        return passed

    def _check_dollar_volume(
        self,
        bars: list[DailyBar],
        metrics: dict[str, Any],
    ) -> bool:
        """Check minimum average dollar volume filter.
        
        Calculation per Section 11.2:
        For each of the last 20 trading days:
            daily_dollar_volume[i] = close[i] * volume[i]
        avg_dollar_volume = AVERAGE(daily_dollar_volume)
        
        Args:
            bars: Daily bars for the underlying
            metrics: Dict to store filter metrics
            
        Returns:
            True if passes filter, False otherwise
        """
        min_dollar_volume = self._config.min_avg_dollar_volume

        # Need at least 20 bars
        if len(bars) < 20:
            metrics["avg_dollar_volume"] = 0
            metrics["dollar_volume_threshold"] = min_dollar_volume
            metrics["dollar_volume_passed"] = False
            metrics["dollar_volume_bars_available"] = len(bars)
            logger.debug(f"Filter fail: insufficient bars ({len(bars)} < 20)")
            return False

        # Calculate 20-day average dollar volume using most recent 20 bars
        recent_bars = bars[-20:]
        dollar_volumes = [bar.close * bar.volume for bar in recent_bars]
        avg_dollar_volume = sum(dollar_volumes) / len(dollar_volumes)

        passed = avg_dollar_volume >= min_dollar_volume

        metrics["avg_dollar_volume"] = avg_dollar_volume
        metrics["dollar_volume_threshold"] = min_dollar_volume
        metrics["dollar_volume_passed"] = passed

        if not passed:
            logger.debug(
                f"Filter fail: avg dollar volume ${avg_dollar_volume:,.0f} < ${min_dollar_volume:,.0f}"
            )

        return passed

    def _check_data_completeness(
        self,
        bars: list[DailyBar],
        metrics: dict[str, Any],
    ) -> bool:
        """Check data completeness filter.
        
        Count missing trading day bars in 30-day lookback.
        Fail if > max_missing_bars missing.
        
        Args:
            bars: Daily bars for the underlying
            metrics: Dict to store filter metrics
            
        Returns:
            True if passes filter, False otherwise
        """
        max_missing = self._config.max_missing_bars

        # Calculate expected trading days in last 30 calendar days
        # Approximate: ~21 trading days per 30 calendar days
        expected_bars = 21

        # Count actual bars in the 30-day window
        if not bars:
            actual_bars = 0
        else:
            # Get bars from last 30 calendar days
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
            recent_bars = [b for b in bars if b.date >= cutoff_date]
            actual_bars = len(recent_bars)

        missing_bars = max(0, expected_bars - actual_bars)
        passed = missing_bars <= max_missing

        metrics["expected_bars_30d"] = expected_bars
        metrics["actual_bars_30d"] = actual_bars
        metrics["missing_bars"] = missing_bars
        metrics["max_missing_bars"] = max_missing
        metrics["data_completeness_passed"] = passed

        if not passed:
            logger.debug(
                f"Filter fail: {missing_bars} missing bars > {max_missing} allowed"
            )

        return passed

    async def _check_earnings_window(
        self,
        ticker: str,
        metrics: dict[str, Any],
    ) -> bool:
        """Check earnings window filter.

        If enabled, exclude tickers with earnings within X days.
        Fails open: if earnings data is unavailable, the ticker passes.

        Args:
            ticker: The ticker symbol
            metrics: Dict to store filter metrics

        Returns:
            True if passes filter (or data not available), False otherwise
        """
        exclude_days = self._config.exclude_earnings_within_days
        metrics["earnings_window_days"] = exclude_days

        if not self._earnings_cache:
            metrics["days_to_earnings"] = None
            metrics["earnings_window_passed"] = True
            metrics["earnings_window_note"] = "Earnings cache not available"
            logger.debug(f"Earnings filter skipped for {ticker} - no earnings cache")
            return True

        try:
            days_to_earnings = await self._earnings_cache.get_days_to_earnings(ticker)
            metrics["days_to_earnings"] = days_to_earnings

            if days_to_earnings is None:
                metrics["earnings_window_passed"] = True
                metrics["earnings_window_note"] = "No earnings date found"
                logger.debug(f"Earnings filter pass for {ticker} - no earnings date")
                return True

            passed = days_to_earnings > exclude_days
            metrics["earnings_window_passed"] = passed

            if not passed:
                logger.debug(
                    f"Filter fail: {ticker} earnings in {days_to_earnings} days "
                    f"<= {exclude_days} day window"
                )

            return passed

        except Exception as e:
            logger.warning(f"Earnings check failed for {ticker}, failing open: {e}")
            metrics["days_to_earnings"] = None
            metrics["earnings_window_passed"] = True
            metrics["earnings_window_note"] = f"Error: {e}"
            return True

    def get_drop_reasons(self) -> dict[str, int]:
        """Get drop reasons for telemetry.
        
        Returns:
            Dict mapping reason codes to counts
        """
        return self._stats.failure_counts.copy()

    def get_stats(self) -> UnderlyingFilterStats:
        """Get filter statistics.
        
        Returns:
            UnderlyingFilterStats object
        """
        return self._stats
