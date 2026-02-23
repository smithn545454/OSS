"""Compression → Expansion Scanner (Section 10.4).

Detects volatility compression (coiled spring) followed by early expansion
(breakout beginning).

Calculation Rules (Exact):
1. Compute ATR(14) series for all available bars
2. atr_floor = MIN(ATR14) over the 20 bars prior to today (not including today)
3. compression_condition = ATR14_today <= atr_floor × 1.10
4. range_bars = the 10 bars prior to today (not including today)
5. prior_range_high = MAX(high) over range_bars
6. prior_range_low = MIN(low) over range_bars
7. break_up = today_close >= prior_range_high × 1.02
8. break_down = today_close <= prior_range_low × 0.98
9. TRIGGER if compression_condition AND (break_up OR break_down)

Direction Hint:
- break_up → CALL
- break_down → PUT
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from app.core.schemas import DirectionHint, ScannerType
from app.scanners.base import BaseScanner, ScanContext, ScanResult
from app.scanners.utils import calculate_atr_series

logger = logging.getLogger(__name__)


class CompressionScanner(BaseScanner):
    """Scanner for compression → expansion patterns."""

    @property
    def scanner_type(self) -> ScannerType:
        return ScannerType.COMPRESSION_EXPANSION

    async def scan_ticker(
        self,
        ticker: str,
        context: ScanContext,
    ) -> ScanResult:
        """Scan a ticker for compression → expansion patterns.

        Args:
            ticker: The ticker symbol to scan
            context: Scan context with config and shared resources

        Returns:
            ScanResult with trigger if compression + breakout detected
        """
        config = context.policy_config.scanner.compression
        atr_period = config.atr_period  # Default 14
        compression_multiplier = config.compression_multiplier  # Default 1.10
        break_pct = config.break_pct  # Default 2.0

        # Per requirements:
        # - Need 20 bars for ATR floor (compression_lookback)
        # - Need 10 bars for range check (range_lookback)
        # - Need ATR period bars for ATR calculation
        compression_lookback = 20
        range_lookback = 10

        try:
            # Use prefetched data from cache if available (much faster)
            cached_bars = context.cached_data.get("daily_bars", {})
            if ticker in cached_bars:
                bars = cached_bars[ticker]
            elif context.data_provider:
                # Use DataProvider for unified access (live or backtest)
                end = context.as_of_date or date.today()
                lookback = atr_period + compression_lookback + 15
                bars = await context.data_provider.get_daily_bars(
                    ticker, end, lookback_days=lookback,
                )
            elif context.polygon:
                # Legacy fallback for standalone/test use
                fetch_days = atr_period + compression_lookback + 15
                to_date = datetime.now().strftime("%Y-%m-%d")
                from_date = (
                    datetime.now() - timedelta(days=fetch_days + 10)
                ).strftime("%Y-%m-%d")
                bars = await context.polygon.get_daily_bars_parsed(
                    ticker, from_date, to_date,
                )
            else:
                return ScanResult(
                    ticker=ticker, triggered=False,
                    error="No data source available",
                )

            # Need enough bars for full calculation
            min_bars_needed = atr_period + compression_lookback + 1
            if len(bars) < min_bars_needed:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    error=f"Insufficient data: {len(bars)} bars, need {min_bars_needed}",
                )

            # Today is the last bar
            today_bar = bars[-1]
            today_close = today_bar.close

            # Calculate ATR series
            atr_series = calculate_atr_series(bars, atr_period)

            # Get ATR for today (last value in series)
            atr_today = atr_series[-1]
            if atr_today is None:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    error="Could not calculate ATR for today",
                )

            # Get ATR values for the 20 bars prior to today (for atr_floor)
            # These are the ATR values at indices [-21] to [-2] (excluding today)
            atr_lookback_values = [
                v for v in atr_series[-(compression_lookback + 1):-1]
                if v is not None
            ]

            if len(atr_lookback_values) < 10:  # Need reasonable sample
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    error=f"Insufficient ATR history: {len(atr_lookback_values)} values",
                )

            # Calculate ATR floor (minimum ATR in the lookback period)
            atr_floor = min(atr_lookback_values)

            # Check compression condition: ATR today <= ATR floor × multiplier
            compression_threshold = atr_floor * compression_multiplier
            is_compressed = atr_today <= compression_threshold

            # Get range bars (10 bars prior to today)
            range_bars = bars[-(range_lookback + 1):-1]
            prior_range_high = max(bar.high for bar in range_bars)
            prior_range_low = min(bar.low for bar in range_bars)

            # Calculate break thresholds
            break_up_threshold = prior_range_high * (1 + break_pct / 100)
            break_down_threshold = prior_range_low * (1 - break_pct / 100)

            # Check for breaks
            break_up = today_close >= break_up_threshold
            break_down = today_close <= break_down_threshold

            # Store metrics
            metrics = {
                "atr_period": atr_period,
                "atr_today": round(atr_today, 4),
                "atr_floor": round(atr_floor, 4),
                "compression_multiplier": compression_multiplier,
                "compression_threshold": round(compression_threshold, 4),
                "is_compressed": is_compressed,
                "prior_range_high": round(prior_range_high, 2),
                "prior_range_low": round(prior_range_low, 2),
                "today_close": round(today_close, 2),
                "break_pct": break_pct,
                "break_up_threshold": round(break_up_threshold, 2),
                "break_down_threshold": round(break_down_threshold, 2),
                "break_up": break_up,
                "break_down": break_down,
            }

            # Diagnostic: log how close each ticker is to triggering
            # comp_ratio < 1.0 = compressed; gap > 0 = broke out
            if compression_threshold > 0:
                comp_ratio = atr_today / compression_threshold
            else:
                comp_ratio = 999.0
            if break_up_threshold > 0:
                up_gap = (today_close - break_up_threshold) / break_up_threshold
            else:
                up_gap = -999.0
            if break_down_threshold > 0:
                dn_gap = (break_down_threshold - today_close) / break_down_threshold
            else:
                dn_gap = -999.0
            logger.info(
                f"[COMPRESSION_DIAG] {ticker}: "
                f"comp_ratio={comp_ratio:.3f} (need ≤1.0), "
                f"up_gap={up_gap:.4f}, dn_gap={dn_gap:.4f} "
                f"(need ≥0.0), atr={atr_today:.4f}, "
                f"floor={atr_floor:.4f}"
            )

            # Trigger requires BOTH compression AND a break
            if not is_compressed:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    metrics=metrics,
                )

            if not (break_up or break_down):
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    metrics=metrics,
                )

            # Determine direction
            if break_up:
                triggered_direction = "UP"
                reason_code = "COMPRESSION_EXPANSION_UP"
                direction_hint = DirectionHint.CALL
            else:
                triggered_direction = "DOWN"
                reason_code = "COMPRESSION_EXPANSION_DOWN"
                direction_hint = DirectionHint.PUT

            metrics["triggered_direction"] = triggered_direction

            # Create trigger
            trigger = self.create_trigger(
                reason_codes=[reason_code],
                metrics=metrics,
                direction_hint=direction_hint,
            )

            return ScanResult(
                ticker=ticker,
                triggered=True,
                trigger=trigger,
                metrics=metrics,
            )

        except Exception as e:
            logger.error(f"Error scanning {ticker} for compression/expansion: {e}")
            return ScanResult(
                ticker=ticker,
                triggered=False,
                error=str(e),
            )

    def get_base_priority(self) -> int:
        """Get base priority score.

        Per Section 10.6:
        - Compression → Expansion: 70

        Returns:
            Base priority score
        """
        return 70
