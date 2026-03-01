"""Historical Unusual Volume Scanner.

Runs UV analysis against historical options data via DataProvider.
Detects unusual volume spikes, OI increases, and OI decreases — the same
signals as the live UV Lambda, but replayed against historical parquet data.

This scanner is intended for backtesting only. In live mode, the separate
UV Lambda pipeline handles unusual volume detection.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Optional

from app.core.schemas import DirectionHint, ScannerType
from app.scanners.base import BaseScanner, ScanContext, ScanResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pre-filter thresholds (match live UV Lambda)
# ---------------------------------------------------------------------------
MIN_VOLUME = 100
MIN_OPEN_INTEREST = 1
MAX_SPREAD_PCT = 50.0
MIN_DTE = 1
MAX_DTE = 90

# ---------------------------------------------------------------------------
# Trigger thresholds
# ---------------------------------------------------------------------------
VOLUME_RATIO_THRESHOLD = 2.0
OI_INCREASE_THRESHOLD_PCT = 15.0
OI_DECREASE_THRESHOLD_PCT = -20.0

# ---------------------------------------------------------------------------
# Scoring weights (must sum to 1.0)
# ---------------------------------------------------------------------------
WEIGHT_VOLUME_RATIO = 0.40
WEIGHT_OI_CHANGE = 0.25
WEIGHT_TRIGGER_COUNT = 0.15
WEIGHT_SPREAD = 0.10
WEIGHT_LIQUIDITY = 0.10

# Scoring caps / parameters
MAX_VOLUME_RATIO_FOR_SCORE = 20.0
MAX_OI_CHANGE_FOR_SCORE = 100.0
MAX_TRIGGERS = 4
IDEAL_SPREAD_PCT = 1.0
MAX_SPREAD_FOR_SCORE = 10.0
MIN_VOLUME_FOR_SCORE = 100
MAX_VOLUME_FOR_SCORE = 10000
MIN_OI_FOR_SCORE = 500
MAX_OI_FOR_SCORE = 20000


class HistoricalUVScanner(BaseScanner):
    """Unusual Volume scanner for historical/backtest mode.

    For each ticker on a given as_of_date:
    1. Load current-day options chain from DataProvider
    2. Load 20-day historical chains to compute rolling average volume
    3. For each contract meeting pre-filters:
       - Compute volume_ratio = today_volume / avg_20d_volume
       - Compute oi_change_pct from prior day's OI
       - Check trigger conditions (VOL_SPIKE, OI_INCREASE, OI_DECREASE)
       - Score and rank triggered contracts
    4. Return the highest-scoring triggered contract as a ScanResult
    """

    @property
    def scanner_type(self) -> ScannerType:
        return ScannerType.UNUSUAL_VOLUME

    async def scan_ticker(
        self,
        ticker: str,
        context: ScanContext,
    ) -> ScanResult:
        """Scan a single ticker for unusual volume activity."""
        if not context.data_provider:
            return ScanResult(
                ticker=ticker,
                triggered=False,
                error="HistoricalUVScanner requires a DataProvider",
            )

        effective_date = context.as_of_date or date.today()

        try:
            # Get today's options chain
            chain = await context.data_provider.get_options_chain(
                ticker, as_of=effective_date, min_dte=MIN_DTE, max_dte=MAX_DTE,
            )
            if not chain:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    metrics={"reason": "no_options_chain"},
                )

            # Get underlying price for moneyness calculation
            snapshot = await context.data_provider.get_stock_snapshot(
                ticker, as_of=effective_date,
            )
            underlying_price = snapshot.close if snapshot else 0.0

            # Load prior-day chain for OI comparison
            prior_date = _trading_day_before(effective_date)
            prior_chain = await context.data_provider.get_options_chain(
                ticker, as_of=prior_date, min_dte=MIN_DTE, max_dte=MAX_DTE + 1,
            )
            prior_oi_map = _build_oi_map(prior_chain)

            # Load 20-day volume history for average calculation
            avg_volume_map = await self._compute_avg_volume(
                context, ticker, effective_date,
            )

            # Evaluate each contract
            best_candidate: Optional[dict[str, Any]] = None
            best_score = -1.0
            contracts_scanned = 0
            contracts_triggered = 0

            for contract in chain:
                result = self._evaluate_contract(
                    contract, underlying_price, prior_oi_map, avg_volume_map,
                )
                if result is None:
                    continue  # Pre-filter failed

                contracts_scanned += 1

                if result["triggered"]:
                    contracts_triggered += 1
                    if result["priority_score"] > best_score:
                        best_score = result["priority_score"]
                        best_candidate = result

            metrics = {
                "contracts_in_chain": len(chain),
                "contracts_scanned": contracts_scanned,
                "contracts_triggered": contracts_triggered,
                "underlying_price": underlying_price,
            }

            if best_candidate is None:
                return ScanResult(
                    ticker=ticker,
                    triggered=False,
                    metrics=metrics,
                )

            # Determine direction hint from best candidate
            option_type = best_candidate.get("option_type", "").upper()
            if option_type == "CALL":
                direction = DirectionHint.CALL
            elif option_type == "PUT":
                direction = DirectionHint.PUT
            else:
                direction = DirectionHint.NONE

            trigger_metrics = {
                "volume_ratio": best_candidate["volume_ratio"],
                "oi_change_pct": best_candidate["oi_change_pct"],
                "priority_score": best_candidate["priority_score"],
                "today_volume": best_candidate["today_volume"],
                "avg_volume_20d": best_candidate["avg_volume_20d"],
                "today_oi": best_candidate["today_oi"],
                "prior_oi": best_candidate["prior_oi"],
                "spread_pct": best_candidate["spread_pct"],
                "option_ticker": best_candidate["option_ticker"],
                "contracts_triggered": contracts_triggered,
            }
            metrics.update(trigger_metrics)

            trigger = self.create_trigger(
                reason_codes=best_candidate["reason_codes"],
                metrics=trigger_metrics,
                direction_hint=direction,
            )

            return ScanResult(
                ticker=ticker,
                triggered=True,
                trigger=trigger,
                metrics=metrics,
            )

        except Exception as e:
            logger.warning(f"HistoricalUV scan failed for {ticker}: {e}")
            return ScanResult(
                ticker=ticker,
                triggered=False,
                error=str(e),
            )

    async def _compute_avg_volume(
        self,
        context: ScanContext,
        ticker: str,
        as_of: date,
        lookback_days: int = 20,
    ) -> dict[str, float]:
        """Compute 20-day average volume per contract.

        Fast path: uses pre-computed UV volume index from prefetch
        (eliminates 20 x get_options_chain calls per ticker).

        Slow path fallback: loads historical chains for each of the
        prior 20 trading days and averages volume per option ticker.

        Returns:
            Dict mapping option_ticker -> average daily volume.
        """
        # Fast path: pre-computed UV volume index from prefetch
        dp = context.data_provider
        shared = getattr(dp, "shared_cache", None)
        if isinstance(shared, dict):
            uv_index = shared.get("__uv_volume_index__")
            if isinstance(uv_index, dict):
                avg_vol = uv_index.get(ticker, 0.0)
                if avg_vol > 0:
                    # Return as {ticker: avg_volume} matching the slow path format
                    return {ticker: avg_vol}
                return {}

        # Slow path: load 20 days of chains (for live mode or missing index)
        volume_totals: dict[str, list[int]] = {}

        # Sample historical dates (trading days approximation)
        dates = _trading_days_before(as_of, lookback_days)

        for hist_date in dates:
            try:
                hist_chain = await context.data_provider.get_options_chain(
                    ticker, as_of=hist_date,
                    min_dte=MIN_DTE, max_dte=MAX_DTE + lookback_days,
                )
                for contract in hist_chain:
                    opt_ticker = _extract_option_ticker(contract)
                    if not opt_ticker:
                        continue
                    vol = _extract_volume(contract)
                    if opt_ticker not in volume_totals:
                        volume_totals[opt_ticker] = []
                    volume_totals[opt_ticker].append(vol)
            except Exception as e:
                logger.debug(f"Could not load chain for {ticker} on {hist_date}: {e}")
                continue

        # Calculate averages
        avg_map: dict[str, float] = {}
        for opt_ticker, volumes in volume_totals.items():
            if volumes:
                avg_map[opt_ticker] = sum(volumes) / len(volumes)

        return avg_map

    def _evaluate_contract(
        self,
        contract: dict[str, Any],
        underlying_price: float,
        prior_oi_map: dict[str, int],
        avg_volume_map: dict[str, float],
    ) -> Optional[dict[str, Any]]:
        """Evaluate a single contract for UV triggers.

        Returns None if pre-filters fail, otherwise a dict with trigger info.
        """
        opt_ticker = _extract_option_ticker(contract)
        if not opt_ticker:
            return None

        today_volume = _extract_volume(contract)
        today_oi = _extract_oi(contract)
        bid, ask = _extract_bid_ask(contract)

        # Pre-filters
        if today_volume < MIN_VOLUME:
            return None
        if today_oi < MIN_OPEN_INTEREST:
            return None

        # Calculate spread
        mid = (bid + ask) / 2 if (bid + ask) > 0 else 0
        spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 999.0
        if spread_pct > MAX_SPREAD_PCT:
            return None

        # Volume ratio
        avg_vol = avg_volume_map.get(opt_ticker, 0)
        if avg_vol > 0:
            volume_ratio = today_volume / avg_vol
            volume_source = "CONTRACT_SPECIFIC"
        else:
            # No history — can't compute meaningful ratio
            volume_ratio = 0.0
            volume_source = "NO_HISTORY"

        # OI change
        prior_oi = prior_oi_map.get(opt_ticker, 0)
        if prior_oi > 0:
            oi_change_pct = ((today_oi - prior_oi) / prior_oi) * 100
        else:
            oi_change_pct = 0.0

        # Trigger detection
        reason_codes = get_trigger_reasons(volume_ratio, oi_change_pct)
        triggered = len(reason_codes) > 0

        # Priority score
        priority_score = calculate_priority_score(
            volume_ratio=volume_ratio,
            oi_change_pct=oi_change_pct,
            trigger_count=len(reason_codes),
            spread_pct=spread_pct,
            today_volume=today_volume,
            today_oi=today_oi,
            volume_source=volume_source,
        )

        option_type = _extract_option_type(contract)

        return {
            "option_ticker": opt_ticker,
            "option_type": option_type,
            "triggered": triggered,
            "reason_codes": reason_codes,
            "volume_ratio": round(volume_ratio, 2),
            "oi_change_pct": round(oi_change_pct, 2),
            "priority_score": priority_score,
            "today_volume": today_volume,
            "avg_volume_20d": round(avg_vol, 2),
            "today_oi": today_oi,
            "prior_oi": prior_oi,
            "spread_pct": round(spread_pct, 2),
            "bid": bid,
            "ask": ask,
        }


# ---------------------------------------------------------------------------
# Scoring functions (mirrored from lambdas/unusual_volume/utils/scoring.py)
# ---------------------------------------------------------------------------


def calculate_priority_score(
    volume_ratio: float,
    oi_change_pct: float,
    trigger_count: int,
    spread_pct: float,
    today_volume: int,
    today_oi: int,
    volume_source: str = "CONTRACT_SPECIFIC",
) -> float:
    """Calculate priority score 0-100 for an unusual volume candidate.

    Weighted components: volume ratio (40%), OI change (25%),
    trigger count (15%), spread quality (10%), liquidity (10%).
    """
    vol_score = _score_volume_ratio(volume_ratio)
    oi_score = _score_oi_change(oi_change_pct)
    trigger_score = min(1.0, trigger_count / MAX_TRIGGERS) if trigger_count > 0 else 0.0
    spread_score = _score_spread(spread_pct)
    liq_score = _score_liquidity(today_volume, today_oi)

    raw = (
        WEIGHT_VOLUME_RATIO * vol_score
        + WEIGHT_OI_CHANGE * oi_score
        + WEIGHT_TRIGGER_COUNT * trigger_score
        + WEIGHT_SPREAD * spread_score
        + WEIGHT_LIQUIDITY * liq_score
    )

    # Bucket fallback penalty
    if volume_source == "BUCKET_FALLBACK":
        raw *= 0.90

    return round(max(0.0, min(100.0, raw * 100)), 2)


def _score_volume_ratio(volume_ratio: float) -> float:
    if volume_ratio <= 1.0:
        return 0.0
    log_ratio = math.log2(volume_ratio)
    log_max = math.log2(MAX_VOLUME_RATIO_FOR_SCORE)
    return min(1.0, log_ratio / log_max)


def _score_oi_change(oi_change_pct: float) -> float:
    abs_change = abs(oi_change_pct)
    if abs_change <= 0:
        return 0.0
    return min(1.0, abs_change / MAX_OI_CHANGE_FOR_SCORE)


def _score_spread(spread_pct: float) -> float:
    if spread_pct <= IDEAL_SPREAD_PCT:
        return 1.0
    if spread_pct >= MAX_SPREAD_FOR_SCORE:
        return 0.0
    return 1.0 - (spread_pct - IDEAL_SPREAD_PCT) / (MAX_SPREAD_FOR_SCORE - IDEAL_SPREAD_PCT)


def _score_liquidity(volume: int, oi: int) -> float:
    vol_s = _normalize_log(volume, MIN_VOLUME_FOR_SCORE, MAX_VOLUME_FOR_SCORE)
    oi_s = _normalize_log(oi, MIN_OI_FOR_SCORE, MAX_OI_FOR_SCORE)
    return (vol_s + oi_s) / 2


def _normalize_log(value: int, min_val: int, max_val: int) -> float:
    if value <= min_val:
        return 0.0
    if value >= max_val:
        return 1.0
    return (math.log10(value) - math.log10(min_val)) / (math.log10(max_val) - math.log10(min_val))


def get_trigger_reasons(
    volume_ratio: float,
    oi_change_pct: float,
    volume_ratio_threshold: float = VOLUME_RATIO_THRESHOLD,
    oi_increase_threshold_pct: float = OI_INCREASE_THRESHOLD_PCT,
    oi_decrease_threshold_pct: float = OI_DECREASE_THRESHOLD_PCT,
) -> list[str]:
    """Determine which UV trigger conditions were met."""
    reasons: list[str] = []

    vol_triggered = volume_ratio >= volume_ratio_threshold
    oi_increase = oi_change_pct >= oi_increase_threshold_pct
    oi_decrease = oi_change_pct <= oi_decrease_threshold_pct
    oi_triggered = oi_increase or oi_decrease

    if vol_triggered:
        reasons.append("VOL_SPIKE")
    if oi_increase:
        reasons.append("OI_INCREASE")
    elif oi_decrease:
        reasons.append("OI_DECREASE")
    if vol_triggered and oi_triggered:
        reasons.append("VOL_OI_COMBO")

    return reasons


# ---------------------------------------------------------------------------
# Contract data extraction helpers
# ---------------------------------------------------------------------------


def _extract_option_ticker(contract: dict[str, Any]) -> Optional[str]:
    """Extract option ticker from contract dict (Polygon format)."""
    details = contract.get("details", {})
    return details.get("ticker") or contract.get("ticker")


def _extract_option_type(contract: dict[str, Any]) -> str:
    """Extract option type (CALL/PUT) from contract dict."""
    details = contract.get("details", {})
    ct = details.get("contract_type", "")
    if ct:
        return ct.upper()
    # Fallback: infer from ticker
    ticker = _extract_option_ticker(contract) or ""
    if "C0" in ticker or "c0" in ticker:
        return "CALL"
    if "P0" in ticker or "p0" in ticker:
        return "PUT"
    return ""


def _extract_volume(contract: dict[str, Any]) -> int:
    """Extract today's volume from contract dict."""
    day = contract.get("day", {})
    if isinstance(day, dict):
        return int(day.get("volume", 0) or 0)
    return int(getattr(day, "volume", 0) or 0)


def _extract_oi(contract: dict[str, Any]) -> int:
    """Extract open interest from contract dict."""
    return int(contract.get("open_interest", 0) or 0)


def _extract_bid_ask(contract: dict[str, Any]) -> tuple[float, float]:
    """Extract bid and ask from contract dict."""
    quote = contract.get("last_quote", {})
    if isinstance(quote, dict):
        bid = float(quote.get("bid", 0) or 0)
        ask = float(quote.get("ask", 0) or 0)
    else:
        bid = float(getattr(quote, "bid", 0) or 0)
        ask = float(getattr(quote, "ask", 0) or 0)
    return bid, ask


def _build_oi_map(chain: list[dict[str, Any]]) -> dict[str, int]:
    """Build option_ticker -> open_interest map from a chain."""
    oi_map: dict[str, int] = {}
    for contract in chain:
        opt_ticker = _extract_option_ticker(contract)
        if opt_ticker:
            oi_map[opt_ticker] = _extract_oi(contract)
    return oi_map


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _trading_day_before(d: date) -> date:
    """Approximate the most recent trading day before d.

    Skips weekends. Does not account for holidays — acceptable
    approximation for backtesting.
    """
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:  # Saturday=5, Sunday=6
        prev -= timedelta(days=1)
    return prev


def _trading_days_before(d: date, count: int) -> list[date]:
    """Generate approximately `count` trading days before d.

    Returns dates in chronological order (oldest first).
    """
    dates: list[date] = []
    current = d - timedelta(days=1)
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
        # Safety: don't go back more than 2x calendar days
        if (d - current).days > count * 3:
            break
    dates.reverse()
    return dates
