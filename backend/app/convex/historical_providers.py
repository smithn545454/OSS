"""Historical data providers for the Convex backtest harness.

Each class wraps an existing data source and enforces strict as-of-date
filtering: no row dated > as_of_iso may flow into Stage 2 / 3 / 4
evaluation. Production implementations live in app.convex.providers; this
module is the historical mirror used solely by app.convex.backtest.

The five classes structurally implement the protocols defined at
backend/app/convex/backtest.py lines 50-107. Sympathy detection and
historical UV reconstruction are intentionally disabled in this phase —
Stage 2 fires on date-known + compression catalysts only. See the
``peer_reactions`` and options-volume notes inline.

Data sources:
    - Price history → S3 parquet via ``HistoricalDataProvider``
      (PriceHistoryTable is TTL'd to ~280 days and cannot service a
      12-month backtest)
    - IV history → ``IVHistoryTable`` in DynamoDB (backfilled in Phase 0.5)
    - Catalyst calendar → ``CatalystCalendarTable`` in DynamoDB
    - Options chain → S3 parquet via ``HistoricalDataProvider``
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta
from typing import Any, Optional

from app.convex.providers import _chain_to_contract_candidates
from app.convex.stage1_universe import calculate_realized_volatility
from app.convex.stage2_catalyst import Stage2Inputs
from app.convex.stage3_volatility import Stage3Inputs
from app.convex.stage4_contract import Stage4Inputs
from app.core.historical_data_provider import HistoricalDataProvider
from app.core.schemas import PriceHistory
from app.db.tables import (
    CatalystCalendarTable,
    IVHistoryTable,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OCC option-ticker parsing (small, local)
# ---------------------------------------------------------------------------

_OCC_PATTERN = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def _parse_occ(option_ticker: str) -> Optional[tuple[str, str, str, float]]:
    """Parse Polygon/OCC ticker into (underlying, expiry_iso, type, strike).

    Returns (underlying, "YYYY-MM-DD", "CALL"|"PUT", strike) or None.
    """
    raw = option_ticker[2:] if option_ticker.startswith("O:") else option_ticker
    m = _OCC_PATTERN.match(raw)
    if not m:
        return None
    underlying, ymd, type_char, strike_str = m.groups()
    try:
        year = 2000 + int(ymd[0:2])
        month = int(ymd[2:4])
        day = int(ymd[4:6])
        expiry = _date(year, month, day).isoformat()
        strike = int(strike_str) / 1000.0
    except (ValueError, IndexError):
        return None
    option_type = "CALL" if type_char == "C" else "PUT"
    return underlying, expiry, option_type, strike


def _build_occ_ticker(
    underlying: str, expiry_iso: str, option_type: str, strike: float
) -> Optional[str]:
    """Build an OCC-style option ticker, e.g. 'O:NVDA260620C00145000'."""
    try:
        exp = _date.fromisoformat(expiry_iso)
    except ValueError:
        return None
    type_char = "C" if option_type.upper().startswith("C") else "P"
    strike_int = int(round(strike * 1000))
    return f"O:{underlying}{exp.strftime('%y%m%d')}{type_char}{strike_int:08d}"


# ---------------------------------------------------------------------------
# Stage 2 historical provider
# ---------------------------------------------------------------------------


def _bars_from_hdp(daily_bars: list, ticker: str) -> list[PriceHistory]:
    """Convert HistoricalDataProvider DailyBar objects to PriceHistory schema."""
    return [
        PriceHistory(
            ticker=ticker,
            date=b.date,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=int(b.volume) if b.volume is not None else 0,
            vwap=b.vwap,
        )
        for b in daily_bars
    ]


class HistoricalStage2InputsProvider:
    """Reads price history (S3) + CatalystCalendar (DynamoDB) as-of as_of_iso.

    Mirrors ``ProductionStage2InputsProvider`` but with strict date filters.
    Price history is read from S3 stock-ohlcv parquet (PriceHistoryTable
    is TTL'd to ~280 days; can't service a 12-month backtest).
    UV is not reconstructed historically (no aggregated options-volume
    history pre-Phase 0.5); Stage 2 fires on date-known + compression only.
    Sympathy is disabled — peer reactions need an as-of EarningsHistory
    snapshot which is out of scope for Phase 8.
    """

    def __init__(
        self,
        hdp: HistoricalDataProvider,
        compression_window_bars: int = 252,
    ) -> None:
        self._hdp = hdp
        self._window = compression_window_bars

    async def fetch(
        self, ticker: str, sector: Optional[str], as_of_iso: str
    ) -> Optional[Stage2Inputs]:
        try:
            as_of = _date.fromisoformat(as_of_iso)
        except ValueError:
            return None

        try:
            daily_bars = await self._hdp.get_daily_bars(
                ticker, as_of, lookback_days=self._window
            )
        except Exception as e:
            logger.warning("S3 price history fetch failed for %s: %s", ticker, e)
            return None
        if len(daily_bars) < 60:
            return None

        closes = [b.close for b in daily_bars]
        highs = [b.high for b in daily_bars]
        lows = [b.low for b in daily_bars]
        volumes = [float(b.volume) for b in daily_bars]

        try:
            calendar = await CatalystCalendarTable.list_for_ticker(
                ticker,
                start_date=as_of_iso,
                end_date=(as_of + timedelta(days=60)).isoformat(),
            )
        except Exception as e:
            logger.debug("CatalystCalendar fetch failed for %s: %s", ticker, e)
            calendar = []

        return Stage2Inputs(
            ticker=ticker,
            sector=sector,
            closes=closes,
            highs=highs,
            lows=lows,
            volumes=volumes,
            nearest_significant_level_pct=None,
            calendar_entries=calendar,
            today_total_options_volume=None,
            avg_options_volume_30d=None,
            today_call_options_volume=None,
            today_put_options_volume=None,
            peer_reactions=[],
        )


# ---------------------------------------------------------------------------
# Stage 3 historical provider
# ---------------------------------------------------------------------------


class HistoricalStage3InputsProvider:
    """Reads IVHistory (DynamoDB, ≤ as_of_iso) + price history (S3) for HV20.

    Mirrors ``ProductionStage3InputsProvider`` minus the live Polygon
    fallback — historical mode never queries today's chain. If the as-of
    IVHistory row is missing iv_30d/iv_60d/iv_25d_put/iv_25d_call,
    Stage 3 will simply lack those metrics for that ticker on that day.
    """

    def __init__(
        self,
        hdp: HistoricalDataProvider,
        history_window: int = 252,
        hv_lookback: int = 25,
    ) -> None:
        self._hdp = hdp
        self._window = history_window
        self._hv_lookback = hv_lookback

    async def fetch(
        self, ticker: str, as_of_iso: str
    ) -> Optional[Stage3Inputs]:
        try:
            as_of = _date.fromisoformat(as_of_iso)
        except ValueError:
            return None

        try:
            history = await IVHistoryTable.list_by_ticker(
                ticker, limit=self._window, end_date=as_of_iso
            )
        except Exception as e:
            logger.warning("IVHistory fetch failed for %s: %s", ticker, e)
            history = []

        latest = history[0] if history else None

        try:
            daily_bars = await self._hdp.get_daily_bars(
                ticker, as_of, lookback_days=60
            )
        except Exception as e:
            logger.debug("S3 price history fetch failed for %s: %s", ticker, e)
            daily_bars = []
        bars = _bars_from_hdp(daily_bars, ticker)
        rv20 = (
            calculate_realized_volatility(
                [b.close for b in bars[-self._hv_lookback:]], 20
            )
            if len(bars) >= 21
            else None
        )

        iv_30d = getattr(latest, "iv_30d", None) if latest else None
        if iv_30d is None and latest is not None:
            iv_30d = latest.atm_iv

        return Stage3Inputs(
            ticker=ticker,
            current_iv_30d=iv_30d,
            iv_history=history,
            rv20=rv20,
        )


# ---------------------------------------------------------------------------
# Stage 4 historical provider
# ---------------------------------------------------------------------------


@dataclass
class _StageContext:
    """Per-day Stage 2 detection memo for Stage 4 measured-move plumbing.

    The backtest harness does not currently thread Stage 2 detector context
    into Stage 4 (pipeline.py owns that wiring internally). For Phase 8 we
    accept that ``measured_move_pct`` and ``historical_event_move_pct``
    will be None and Stage 4 will use its own price-history fallback.
    """

    measured_move_pct: Optional[float] = None
    historical_event_move_pct: Optional[float] = None


class HistoricalStage4InputsProvider:
    """Reads historical chain parquet + as-of stock snapshot from S3."""

    def __init__(
        self,
        hdp: HistoricalDataProvider,
        min_dte: int = 5,
        max_dte: int = 120,
    ) -> None:
        self._hdp = hdp
        self._min_dte = min_dte
        self._max_dte = max_dte

    async def fetch(  # noqa: PLR0913
        self,
        ticker: str,
        direction: str,
        catalyst_type: Optional[str],
        catalyst_date_iso: Optional[str],
        uv_directional_skew: Optional[str],
        as_of_iso: str,
    ) -> Optional[Stage4Inputs]:
        try:
            as_of = _date.fromisoformat(as_of_iso)
        except ValueError:
            return None

        chain = await self._hdp.get_options_chain(
            ticker, as_of, min_dte=self._min_dte, max_dte=self._max_dte
        )
        if not chain:
            return None

        snapshot = await self._hdp.get_stock_snapshot(ticker, as_of)
        if snapshot is None or snapshot.close is None or snapshot.close <= 0:
            return None

        candidates = _chain_to_contract_candidates(chain, as_of_iso)
        if not candidates:
            return None

        # The historical chain parquet stores ``ticker`` as the underlying
        # symbol (e.g. "SLB"), not the OCC option ticker. The production
        # helper preserves whatever it finds, so candidates inherit the
        # underlying as their option_ticker. Rebuild a proper OCC ticker
        # so HistoricalOptionPriceProvider can resolve forward-walk prices.
        rebuilt: list = []
        for c in candidates:
            occ = _build_occ_ticker(ticker, c.expiry, c.option_type, c.strike)
            if occ is None:
                continue
            rebuilt.append(
                c.__class__(
                    option_ticker=occ,
                    option_type=c.option_type,
                    strike=c.strike,
                    expiry=c.expiry,
                    dte=c.dte,
                    delta=c.delta,
                    bid=c.bid,
                    ask=c.ask,
                    open_interest=c.open_interest,
                    volume=c.volume,
                    iv=getattr(c, "iv", None),
                )
            )
        candidates = rebuilt
        if not candidates:
            return None

        # measured_move / historical_event_move are populated upstream from
        # Stage 2 detector context in production; not threaded through the
        # backtest harness yet (see _StageContext docstring). Pass None and
        # let Stage 4 use its own bar-derived fallback.
        return Stage4Inputs(
            ticker=ticker,
            underlying_price=float(snapshot.close),
            direction=direction,
            catalyst_type=catalyst_type,
            catalyst_date_iso=catalyst_date_iso,
            measured_move_pct=None,
            historical_event_move_pct=None,
            available_contracts=candidates,
            uv_directional_skew=uv_directional_skew,
            today_iso=as_of_iso,
        )


# ---------------------------------------------------------------------------
# Future price history (forward-walking)
# ---------------------------------------------------------------------------


class HistoricalFuturePriceHistoryProvider:
    """Forward-walks S3 stock-ohlcv parquet from an entry date.

    Used by ``resolve_trade_outcome`` to walk forward day-by-day looking
    for profit-target / stop-loss / time-exit triggers. Reads one parquet
    per trading day (cached via the shared HistoricalDataProvider).
    """

    def __init__(self, hdp: HistoricalDataProvider) -> None:
        self._hdp = hdp

    async def fetch(
        self, ticker: str, start_date_iso: str, days: int
    ) -> list[PriceHistory]:
        try:
            start = _date.fromisoformat(start_date_iso)
        except ValueError:
            return []

        # Use HDP's get_daily_bars with an end_date past start+days; bars come
        # back oldest-first (per get_daily_bars contract; double-checked by
        # filtering on date in the loop). Pull a generous window then slice.
        end_window = start + timedelta(days=int(days * 1.6) + 14)
        try:
            daily = await self._hdp.get_daily_bars(
                ticker, end_window, lookback_days=int(days * 1.6) + 14
            )
        except Exception as e:
            logger.warning(
                "FuturePriceHistory S3 fetch failed for %s @ %s: %s",
                ticker, start_date_iso, e,
            )
            return []

        # Sort oldest-first (HDP sometimes returns reverse order).
        daily_sorted = sorted(daily, key=lambda b: b.date)
        forward = [b for b in daily_sorted if b.date >= start_date_iso]
        return _bars_from_hdp(forward[:days], ticker)


# ---------------------------------------------------------------------------
# Historical option price (for trade exit resolution)
# ---------------------------------------------------------------------------


class HistoricalOptionPriceProvider:
    """Looks up a single option's mid price on a target date from S3 parquet."""

    def __init__(self, hdp: HistoricalDataProvider) -> None:
        self._hdp = hdp

    async def fetch(
        self, option_ticker: str, target_date_iso: str
    ) -> Optional[float]:
        parsed = _parse_occ(option_ticker)
        if parsed is None:
            return None
        underlying, expiry_iso, option_type, strike = parsed
        try:
            target = _date.fromisoformat(target_date_iso)
        except ValueError:
            return None
        return await self._hdp.get_contract_price(
            ticker=underlying,
            strike=strike,
            expiration_date=expiry_iso,
            option_type=option_type,
            as_of=target,
        )


# ---------------------------------------------------------------------------
# Convenience bundle
# ---------------------------------------------------------------------------


@dataclass
class HistoricalProviderBundle:
    """Construct all 5 providers around one shared HistoricalDataProvider.

    The shared ``HistoricalDataProvider`` instance owns the parquet cache,
    so Stage 2/3 stock-OHLCV reads, Stage 4 chain reads, future-price
    forward walks, and option-price exit lookups all share one cached
    parquet table per (date, dataset).
    """

    hdp: HistoricalDataProvider
    stage2: HistoricalStage2InputsProvider
    stage3: HistoricalStage3InputsProvider
    stage4: HistoricalStage4InputsProvider
    future_prices: HistoricalFuturePriceHistoryProvider
    option_prices: HistoricalOptionPriceProvider

    @classmethod
    def build(
        cls,
        s3_bucket: str = "oss-dev-backtest-982534389101",
        s3_client: Any = None,
    ) -> "HistoricalProviderBundle":
        hdp = HistoricalDataProvider(s3_bucket=s3_bucket, s3_client=s3_client)
        return cls(
            hdp=hdp,
            stage2=HistoricalStage2InputsProvider(hdp=hdp),
            stage3=HistoricalStage3InputsProvider(hdp=hdp),
            stage4=HistoricalStage4InputsProvider(hdp=hdp),
            future_prices=HistoricalFuturePriceHistoryProvider(hdp=hdp),
            option_prices=HistoricalOptionPriceProvider(hdp=hdp),
        )
