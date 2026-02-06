"""Cheap Options Scanner (IV vs RV) - Section 10.5.

Finds underlyings where options are reasonably priced relative to
realized volatility.

Trigger Conditions (configurable):
- iv_rv_ratio <= 1.10: Options cheap relative to realized moves
- iv_percentile <= 40: IV in lower 40% of 252-day range

Trigger Logic: Fire if iv_rv_ratio <= threshold OR iv_percentile <= threshold

Direction Hint: Always NONE (volatility-based, not directional)

Calculations:
- Realized Volatility (RV20):
  1. Fetch 21 daily closes (to get 20 returns)
  2. daily_returns[i] = LN(close[i] / close[i-1])
  3. std_dev = STDEV(daily_returns)
  4. RV20 = std_dev × SQRT(252)

- IV Proxy:
  1. Filter chain to contracts with DTE between 30 and 45
  2. For each side (CALL, PUT), find contract closest to ATM
  3. iv_proxy = AVERAGE(atm_call_iv, atm_put_iv)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from app.core.schemas import DirectionHint, IVHistory, ScannerType
from app.db.tables import IVHistoryTable
from app.scanners.base import BaseScanner, ScanContext, ScanResult
from app.scanners.utils import calculate_iv_proxy, calculate_rv

logger = logging.getLogger(__name__)


class CheapOptionsScanner(BaseScanner):
    """Scanner for cheap options (IV vs RV analysis)."""

    @property
    def scanner_type(self) -> ScannerType:
        return ScannerType.CHEAP_OPTIONS

    async def scan_ticker(
        self,
        ticker: str,
        context: ScanContext,
    ) -> ScanResult:
        """Scan a ticker for cheap options conditions.

        Args:
            ticker: The ticker symbol to scan
            context: Scan context with config and shared resources

        Returns:
            ScanResult with trigger if cheap options detected
        """
        config = context.policy_config.scanner.cheap_options
        iv_rv_ratio_max = config.iv_rv_ratio_max  # Default 1.10
        iv_percentile_max = config.iv_percentile_max  # Default 40

        try:
            # Step 1: Get underlying price data for RV calculation
            # Use prefetched data from cache if available (much faster)
            cached_bars = context.cached_data.get("daily_bars", {})
            if ticker in cached_bars:
                bars = cached_bars[ticker]
            else:
                # Fallback: fetch if not in cache
                fetch_days = 30  # Need 21 closes minimum
                to_date = datetime.now().strftime("%Y-%m-%d")
                from_date = (datetime.now() - timedelta(days=fetch_days)).strftime("%Y-%m-%d")
                bars = await context.polygon.get_daily_bars_parsed(ticker, from_date, to_date)

            if len(bars) < 21:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    error=f"Insufficient price data: {len(bars)} bars, need 21",
                )

            # Calculate RV20
            closes = [bar.close for bar in bars]
            rv20 = calculate_rv(closes, window=20)

            if rv20 is None or rv20 <= 0:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    error="Could not calculate RV20",
                )

            # Step 2: Get options chain for IV proxy
            underlying_price = closes[-1]

            # Calculate expiration date range (30-45 DTE) for filtering cached chain
            min_dte = 30
            max_dte = 45
            min_exp = (datetime.now() + timedelta(days=min_dte)).strftime("%Y-%m-%d")
            max_exp = (datetime.now() + timedelta(days=max_dte)).strftime("%Y-%m-%d")

            # Use cached options chain from Phase 3
            # The orchestrator now fetches MINIMAL chains (30-45 DTE, ATM ±10%)
            # via get_options_chain_minimal() - single API call, no pagination
            cached_chain = context.cached_data.get("options_chains", {}).get(ticker)
            
            if cached_chain:
                # Cached chain is already ATM-filtered by orchestrator
                # Just filter to exact 30-45 DTE range for IV proxy
                chain = [
                    c for c in cached_chain
                    if min_exp <= c.get("details", {}).get("expiration_date", "") <= max_exp
                ]
            else:
                # Fallback: use minimal fetch for standalone scanner runs
                # This uses a SINGLE API call with strike + DTE filters
                strike_gte = underlying_price * 0.90
                strike_lte = underlying_price * 1.10
                chain = await context.polygon.get_options_chain_minimal(
                    ticker,
                    expiration_date_gte=min_exp,
                    expiration_date_lte=max_exp,
                    strike_price_gte=strike_gte,
                    strike_price_lte=strike_lte,
                )

            if not chain:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    error="No options chain data in 30-45 DTE range",
                )

            # Calculate IV proxy from ATM options
            iv_result = calculate_iv_proxy(chain, underlying_price, min_dte=30, max_dte=45)

            if iv_result is None:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    error="Could not find ATM options for IV proxy",
                )

            # Calculate IV/RV ratio
            iv_rv_ratio = iv_result.iv_proxy / rv20 if rv20 > 0 else float("inf")

            # Step 3: Get IV percentile from history
            iv_percentile = await IVHistoryTable.calculate_percentile(ticker, iv_result.iv_proxy)

            # Store current IV data for future percentile calculations
            await self._store_iv_history(
                ticker=ticker,
                atm_iv=iv_result.iv_proxy,
                atm_call_iv=iv_result.atm_call_iv,
                atm_put_iv=iv_result.atm_put_iv,
                rv20=rv20,
                iv_rv_ratio=iv_rv_ratio,
            )

            # Store metrics (per Section 10.5)
            metrics = {
                "rv20": round(rv20, 4),
                "iv_proxy": round(iv_result.iv_proxy, 4),
                "iv_rv_ratio": round(iv_rv_ratio, 4),
                "iv_percentile": round(iv_percentile, 2) if iv_percentile is not None else None,
                "atm_call_iv": round(iv_result.atm_call_iv, 4),
                "atm_put_iv": round(iv_result.atm_put_iv, 4),
                "atm_call_strike": round(iv_result.atm_call_strike, 2),
                "atm_put_strike": round(iv_result.atm_put_strike, 2),
                "atm_dte": iv_result.atm_dte,
                "underlying_price": round(underlying_price, 2),
            }

            # Check trigger conditions
            iv_rv_triggered = iv_rv_ratio <= iv_rv_ratio_max
            iv_percentile_triggered = (
                iv_percentile is not None and iv_percentile <= iv_percentile_max
            )

            if not (iv_rv_triggered or iv_percentile_triggered):
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    metrics=metrics,
                )

            # Determine reason codes
            reason_codes = []
            if iv_rv_triggered and iv_percentile_triggered:
                reason_codes.append("CHEAP_OPTIONS_BOTH_CONDITIONS")
            elif iv_rv_triggered:
                reason_codes.append("CHEAP_OPTIONS_IV_RV_LOW")
            else:
                reason_codes.append("CHEAP_OPTIONS_IV_PERCENTILE_LOW")

            # Create trigger (always NONE direction for volatility scanner)
            trigger = self.create_trigger(
                reason_codes=reason_codes,
                metrics=metrics,
                direction_hint=DirectionHint.NONE,
            )

            return ScanResult(
                ticker=ticker,
                triggered=True,
                trigger=trigger,
                metrics=metrics,
            )

        except Exception as e:
            logger.error(f"Error scanning {ticker} for cheap options: {e}")
            return ScanResult(
                ticker=ticker,
                triggered=False,
                error=str(e),
            )

    async def _store_iv_history(
        self,
        ticker: str,
        atm_iv: float,
        atm_call_iv: float,
        atm_put_iv: float,
        rv20: float,
        iv_rv_ratio: float,
    ) -> None:
        """Store IV data for future percentile calculations.

        Args:
            ticker: Ticker symbol
            atm_iv: ATM IV proxy
            atm_call_iv: ATM call IV
            atm_put_iv: ATM put IV
            rv20: 20-day realized volatility
            iv_rv_ratio: IV/RV ratio
        """
        try:
            today = datetime.now().strftime("%Y-%m-%d")

            iv_history = IVHistory(
                ticker=ticker,
                date=today,
                atm_iv=atm_iv,
                atm_call_iv=atm_call_iv,
                atm_put_iv=atm_put_iv,
                rv20=rv20,
                iv_rv_ratio=iv_rv_ratio,
            )

            await IVHistoryTable.put(iv_history)
            logger.debug(f"Stored IV history for {ticker} on {today}")

        except Exception as e:
            # Don't fail the scan if we can't store history
            logger.warning(f"Failed to store IV history for {ticker}: {e}")

    def get_base_priority(self) -> int:
        """Get base priority score.

        Per Section 10.6:
        - Cheap Options: 50

        Returns:
            Base priority score
        """
        return 50
