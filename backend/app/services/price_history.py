"""Price History Service — cached daily OHLCV bars in DynamoDB.

Backs Pillar v4 features that need a 252-day lookback:
- Stage 2 Minervini trend template (150MA, 200MA, 52-week high/low)
- BB width percentile
- Sector relative strength
- Historical move magnitude (join with earnings-history)

The service is the single point of access for daily bars across the
pipeline. Pipeline code should call ``PriceHistoryService.get_bars(...)``
rather than hitting Polygon directly so we only pay the Polygon cost
once per (ticker, date).

Operation modes:
- With a ``PolygonClient`` (pipeline runs): cache-miss triggers a
  write-through Polygon fetch.
- Without a ``PolygonClient`` (tests, offline tools): read-only from
  DynamoDB. Missing data returns an empty list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.core.schemas import PriceHistory
from app.db.tables import PriceHistoryTable
from app.services.polygon import DailyBar, PolygonClient

logger = logging.getLogger(__name__)


def _to_daily_bar(record: PriceHistory) -> DailyBar:
    """Convert a ``PriceHistory`` row to the in-memory ``DailyBar`` used
    by downstream feature computation."""
    return DailyBar(
        ticker=record.ticker,
        date=record.date,
        open=record.open,
        high=record.high,
        low=record.low,
        close=record.close,
        volume=record.volume,
        vwap=record.vwap,
    )


def _to_price_history(bar: DailyBar) -> PriceHistory:
    """Convert Polygon's in-memory ``DailyBar`` to the Pydantic schema.

    Polygon returns split-adjusted volume as a float (e.g. 57130909.65).
    ``PriceHistory.volume`` is declared ``int``; Pydantic v2 rejects
    floats with a fractional part, so we round at the boundary rather
    than relax the schema.
    """
    volume = bar.volume
    if not isinstance(volume, int):
        volume = int(round(float(volume)))
    return PriceHistory(
        ticker=bar.ticker,
        date=bar.date,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=volume,
        vwap=bar.vwap,
    )


@dataclass
class CoverageReport:
    """Result summary for a backfill or refresh run."""

    tickers_attempted: int = 0
    tickers_succeeded: int = 0
    tickers_failed: int = 0
    bars_written: int = 0
    failures: list[str] = field(default_factory=list)


class PriceHistoryService:
    """Persistent daily-bar store with Polygon write-through fallback."""

    # Calendar days we fetch for a 252-trading-day window, with buffer
    # for weekends and holidays.
    _CALENDAR_DAYS_FOR_252_TRADING = 380

    def __init__(self, polygon_client: Optional[PolygonClient] = None) -> None:
        self._polygon = polygon_client

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_bars(
        self,
        ticker: str,
        lookback_days: int = 252,
        *,
        as_of: Optional[date] = None,
        allow_fallback: bool = True,
    ) -> list[DailyBar]:
        """Return daily bars for a ticker, oldest first.

        Args:
            ticker: Ticker symbol.
            lookback_days: Target number of trading-day bars (default 252).
            as_of: Reference date (defaults to today). Bars returned have
                date <= as_of.
            allow_fallback: When True and the cache is sparse, call
                Polygon to fetch the missing range and write through.

        Returns:
            List of ``DailyBar`` sorted oldest first. May be shorter
            than ``lookback_days`` for newly-listed tickers; callers
            should degrade gracefully.
        """
        as_of = as_of or date.today()

        # Query the N most-recent bars ending at as_of. We descend on SK
        # (scan_forward=False) and reverse to oldest-first afterwards so
        # the returned list matches downstream expectations (feature
        # computers iterate chronologically).
        records = await PriceHistoryTable.list_by_ticker(
            ticker,
            limit=lookback_days,
            end_date=as_of.isoformat(),
            scan_forward=False,
        )
        records.reverse()
        bars = [_to_daily_bar(r) for r in records]

        # If cache is warm enough, we're done.
        if len(bars) >= lookback_days:
            return bars

        if not (allow_fallback and self._polygon is not None):
            # Degrade gracefully — return whatever we have.
            logger.debug(
                f"{ticker}: returning {len(bars)} cached bars "
                f"(target {lookback_days}, no fallback)"
            )
            return bars

        # Cache miss (or newly-listed ticker): pull the full range from
        # Polygon and write through. Buffer calendar days to guarantee
        # we cover weekends/holidays within the trading-day target.
        calendar_days = max(
            lookback_days + 14,
            int(lookback_days * self._CALENDAR_DAYS_FOR_252_TRADING / 252),
        )
        start_date = (as_of - timedelta(days=calendar_days)).isoformat()
        end_date = as_of.isoformat()
        logger.info(
            f"{ticker}: price-history cache under-warm ({len(bars)}/"
            f"{lookback_days}); backfilling from Polygon"
        )
        fresh = await self._polygon.get_daily_bars_parsed(
            ticker, start_date, end_date
        )
        if fresh:
            await PriceHistoryTable.put_batch(
                [_to_price_history(b) for b in fresh]
            )
            return fresh[-lookback_days:]

        logger.warning(
            f"{ticker}: Polygon returned no bars; using {len(bars)} cached"
        )
        return bars

    async def get_latest_close(
        self, ticker: str, as_of: Optional[date] = None
    ) -> Optional[float]:
        """Return the most recent close at or before ``as_of``."""
        as_of = as_of or date.today()
        records = await PriceHistoryTable.list_by_ticker(
            ticker,
            limit=1,
            end_date=as_of.isoformat(),
            scan_forward=False,
        )
        if not records:
            return None
        return records[0].close

    async def get_close_on(
        self, ticker: str, target: date
    ) -> Optional[float]:
        """Return the close on a specific trading date, or None."""
        record = await PriceHistoryTable.get(ticker, target.isoformat())
        return record.close if record else None

    # ------------------------------------------------------------------
    # Writes / backfills
    # ------------------------------------------------------------------

    async def warm_ticker(
        self,
        ticker: str,
        lookback_days: int = 280,
        *,
        as_of: Optional[date] = None,
    ) -> int:
        """Fetch ``lookback_days`` of bars for a ticker and cache them.

        Used by the backfill script. Returns the count of bars written.
        """
        if self._polygon is None:
            raise RuntimeError("warm_ticker requires a PolygonClient")
        as_of = as_of or date.today()
        start_date = (as_of - timedelta(days=lookback_days)).isoformat()
        end_date = as_of.isoformat()
        fresh = await self._polygon.get_daily_bars_parsed(
            ticker, start_date, end_date
        )
        if not fresh:
            return 0
        await PriceHistoryTable.put_batch(
            [_to_price_history(b) for b in fresh]
        )
        return len(fresh)

    async def warm_tickers(
        self,
        tickers: list[str],
        lookback_days: int = 280,
        *,
        as_of: Optional[date] = None,
    ) -> CoverageReport:
        """Backfill many tickers sequentially.

        The Polygon client manages its own concurrency via the
        semaphore set at construction time. We call per-ticker here
        rather than ``get_daily_bars_batch`` because the batch variant
        doesn't guarantee ordering and we want clean per-ticker
        success/failure reporting.
        """
        report = CoverageReport()
        for ticker in tickers:
            report.tickers_attempted += 1
            try:
                bars_written = await self.warm_ticker(
                    ticker, lookback_days=lookback_days, as_of=as_of
                )
                if bars_written > 0:
                    report.tickers_succeeded += 1
                    report.bars_written += bars_written
                else:
                    report.tickers_failed += 1
                    report.failures.append(f"{ticker}: no bars returned")
            except Exception as e:  # pragma: no cover — defensive
                report.tickers_failed += 1
                report.failures.append(f"{ticker}: {e}")
                logger.exception(f"warm_ticker failed for {ticker}")
        logger.info(
            f"warm_tickers complete: "
            f"{report.tickers_succeeded}/{report.tickers_attempted} succeeded, "
            f"{report.bars_written} bars written"
        )
        return report

    async def refresh_daily(
        self, tickers: list[str], target_date: Optional[date] = None
    ) -> CoverageReport:
        """Append yesterday's (or ``target_date``'s) bar for each ticker.

        Uses the Polygon grouped-daily endpoint (1 API call for all
        US stocks) rather than per-ticker calls. Only records for
        tickers in ``tickers`` are persisted.

        Called by the EventBridge daily refresh hook at 5am UTC.
        """
        if self._polygon is None:
            raise RuntimeError("refresh_daily requires a PolygonClient")
        target_date = target_date or (date.today() - timedelta(days=1))

        logger.info(
            f"refresh_daily: fetching {target_date.isoformat()} for "
            f"{len(tickers)} tickers via grouped endpoint"
        )
        grouped = await self._polygon.get_grouped_daily_bars(
            target_date.isoformat()
        )

        report = CoverageReport()
        ticker_set = set(tickers)
        records: list[PriceHistory] = []
        for ticker, bar in grouped.items():
            if ticker not in ticker_set:
                continue
            report.tickers_attempted += 1
            records.append(_to_price_history(bar))

        if records:
            await PriceHistoryTable.put_batch(records)
            report.tickers_succeeded = len(records)
            report.bars_written = len(records)

        report.tickers_failed = report.tickers_attempted - report.tickers_succeeded
        missing = ticker_set - {r.ticker for r in records}
        if missing:
            report.failures = [f"{t}: no bar on {target_date}" for t in sorted(missing)]

        logger.info(
            f"refresh_daily complete: {report.tickers_succeeded} bars written, "
            f"{len(missing)} tickers missing (likely non-trading or delisted)"
        )
        return report
