"""Breakout / Breakdown Scanner (Section 10.3).

Identifies confirmed range breaks using daily close prices.

Calculation Rules (Exact - No Ambiguity):
1. Fetch daily bars for at least N+1 trading days
2. Sort by timestamp ascending
3. Identify the most recent bar as "today"
4. range_bars = the N bars immediately preceding today (NOT including today)
5. prior_N_day_high = MAX(high) over range_bars
6. prior_N_day_low = MIN(low) over range_bars
7. Breakout triggered if: today_close > prior_N_day_high
8. Breakdown triggered if: today_close < prior_N_day_low

Direction Hint:
- Breakout → CALL
- Breakdown → PUT
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from app.core.schemas import DirectionHint, ScannerType
from app.scanners.base import BaseScanner, ScanContext, ScanResult
from app.scanners.utils import calculate_volume_ratio

logger = logging.getLogger(__name__)


class BreakoutScanner(BaseScanner):
    """Scanner for breakout and breakdown patterns."""

    @property
    def scanner_type(self) -> ScannerType:
        # Note: Returns BREAKOUT by default, will use BREAKDOWN for breakdown triggers
        return ScannerType.BREAKOUT

    async def scan_ticker(
        self,
        ticker: str,
        context: ScanContext,
    ) -> ScanResult:
        """Scan a ticker for breakout/breakdown patterns.

        Args:
            ticker: The ticker symbol to scan
            context: Scan context with config and shared resources

        Returns:
            ScanResult with trigger if breakout/breakdown detected
        """
        config = context.policy_config.scanner.breakout
        lookback_days = config.lookback_days  # N (default 20)

        try:
            # Use prefetched data from cache if available (much faster)
            cached_bars = context.cached_data.get("daily_bars", {})
            if ticker in cached_bars:
                bars = cached_bars[ticker]
            elif context.data_provider:
                # Use DataProvider for unified access (live or backtest)
                end = context.as_of_date or date.today()
                bars = await context.data_provider.get_daily_bars(
                    ticker, end, lookback_days=lookback_days + 10,
                )
            elif context.polygon:
                # Legacy fallback for standalone/test use
                fetch_days = lookback_days + 10
                to_date = datetime.now().strftime("%Y-%m-%d")
                from_date = (
                    datetime.now() - timedelta(days=fetch_days)
                ).strftime("%Y-%m-%d")
                bars = await context.polygon.get_daily_bars_parsed(
                    ticker, from_date, to_date,
                )
            else:
                return ScanResult(
                    ticker=ticker, triggered=False,
                    error="No data source available",
                )

            if len(bars) < lookback_days + 1:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    error=f"Insufficient data: {len(bars)} bars, need {lookback_days + 1}",
                )

            # Bars are sorted ascending by date
            # Today is the last bar
            today_bar = bars[-1]
            today_close = today_bar.close
            today_volume = today_bar.volume

            # Range bars are the N bars immediately preceding today
            range_bars = bars[-(lookback_days + 1):-1]

            # Calculate range high and low
            prior_n_day_high = max(bar.high for bar in range_bars)
            prior_n_day_low = min(bar.low for bar in range_bars)

            # Calculate 20-day average volume for potential volume confirmation
            all_volumes = [bar.volume for bar in bars]
            if len(all_volumes) >= 20:
                avg_20d_volume = sum(all_volumes[-21:-1]) / 20  # Exclude today
            else:
                avg_20d_volume = sum(all_volumes[:-1]) / max(len(all_volumes) - 1, 1)

            volume_ratio = calculate_volume_ratio(today_volume, avg_20d_volume)

            # Store metrics
            metrics = {
                "N": lookback_days,
                "prior_N_day_high": round(prior_n_day_high, 2),
                "prior_N_day_low": round(prior_n_day_low, 2),
                "today_close": round(today_close, 2),
                "today_volume": today_volume,
                "avg_20d_volume": round(avg_20d_volume, 2),
                "volume_ratio": round(volume_ratio, 2),
            }

            # Check for breakout or breakdown
            is_breakout = today_close > prior_n_day_high
            is_breakdown = today_close < prior_n_day_low

            if not (is_breakout or is_breakdown):
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    metrics=metrics,
                )

            # Determine triggered side and update metrics
            if is_breakout:
                triggered_side = "BREAKOUT"
                reason_code = "BREAKOUT_CLOSE_ABOVE_RANGE"
                direction_hint = DirectionHint.CALL
                scanner_type = ScannerType.BREAKOUT
            else:
                triggered_side = "BREAKDOWN"
                reason_code = "BREAKDOWN_CLOSE_BELOW_RANGE"
                direction_hint = DirectionHint.PUT
                scanner_type = ScannerType.BREAKDOWN

            metrics["triggered_side"] = triggered_side

            # Create trigger with appropriate scanner type
            trigger = self.create_trigger(
                reason_codes=[reason_code],
                metrics=metrics,
                direction_hint=direction_hint,
            )

            # Override scanner_type for breakdown
            if is_breakdown:
                trigger = trigger.model_copy(
                    update={"scanner_type": scanner_type}
                )

            return ScanResult(
                ticker=ticker,
                triggered=True,
                trigger=trigger,
                metrics=metrics,
            )

        except Exception as e:
            logger.error(f"Error scanning {ticker} for breakout/breakdown: {e}")
            return ScanResult(
                ticker=ticker,
                triggered=False,
                error=str(e),
            )

    def get_base_priority(self, has_volume_confirmation: bool) -> int:
        """Get base priority score.

        Per Section 10.6:
        - Breakout/Breakdown + Volume Confirmation: 75
        - Without volume confirmation: 65 (implied, using unusual volume priority)

        Args:
            has_volume_confirmation: True if volume ratio >= 1.5

        Returns:
            Base priority score
        """
        if has_volume_confirmation:
            return 75
        return 65


def has_volume_confirmation(volume_ratio: float, threshold: float = 1.5) -> bool:
    """Check if breakout/breakdown has volume confirmation.

    Per Section 15.2 (GATE_BREAKOUT_VOLUME), volume confirmation
    requires volume_ratio >= 1.5.

    Args:
        volume_ratio: Today's volume / 20-day average volume
        threshold: Minimum ratio for confirmation (default 1.5)

    Returns:
        True if volume confirms the breakout/breakdown
    """
    return volume_ratio >= threshold
