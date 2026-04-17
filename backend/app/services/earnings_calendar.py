"""Earnings Calendar Service — historical events with 1-day post-event moves.

Purpose
-------
The Move Potential pillar (v4) needs per-ticker earnings history going
back four quarters, along with the realized 1-day price move for each
event. This service manages the ``oss-dev-earnings-history`` store and
joins it with price history to derive ``historical_move_magnitude``.

Relationship to EarningsCacheService
------------------------------------
- ``EarningsCacheService`` (``services/earnings_cache.py``) remains the
  hot-path lookup for *next earnings date only*, backed by the
  short-TTL ``oss-dev-earnings-cache`` table. Pipeline runs read it
  hundreds of times per run; its PK-only shape is intentional for speed.
- ``EarningsCalendarService`` (this file) owns the fuller picture:
  past events, realized 1-day moves, next event metadata. Writes to
  ``oss-dev-earnings-history``. The two services coexist until a later
  phase decides whether to consolidate.

Operation modes
---------------
- With a ``FinnhubClient`` and ``PriceHistoryService``: full refresh
  capability (used by backfill scripts + daily refresh hook).
- Read-only (no clients): feature computation and tests read cached
  events from DynamoDB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

from app.core.schemas import EarningsEvent
from app.db.tables import EarningsHistoryTable
from app.services.finnhub import FinnhubClient
from app.services.price_history import PriceHistoryService

logger = logging.getLogger(__name__)


@dataclass
class RefreshReport:
    """Summary of a backfill or refresh run."""

    tickers_attempted: int = 0
    tickers_succeeded: int = 0
    tickers_failed: int = 0
    events_written: int = 0
    moves_computed: int = 0
    failures: list[str] = field(default_factory=list)


class EarningsCalendarService:
    """Historical earnings store + 1-day move computation."""

    # Lookback for historical backfill: 18 months catches ~6 prior
    # earnings events per ticker, giving us headroom above the 4 we
    # actually consume in the subscore.
    _BACKFILL_LOOKBACK_DAYS = 18 * 30
    # Lookahead for fetching the next-event metadata alongside the
    # historical range (single Finnhub call covers both).
    _FUTURE_LOOKAHEAD_DAYS = 90

    def __init__(
        self,
        finnhub_client: Optional[FinnhubClient] = None,
        price_history_service: Optional[PriceHistoryService] = None,
    ) -> None:
        self._finnhub = finnhub_client
        self._price_history = price_history_service

    # ------------------------------------------------------------------
    # Reads (available in all modes)
    # ------------------------------------------------------------------

    async def get_recent_events(
        self, ticker: str, n: int = 4
    ) -> list[EarningsEvent]:
        """Return the last ``n`` events with a computed 1-day move.

        Used directly by the Move Potential subscore.
        """
        return await EarningsHistoryTable.get_recent_with_moves(ticker, n=n)

    async def get_historical_move_magnitude(
        self, ticker: str, n: int = 4
    ) -> Optional[tuple[float, int]]:
        """Compute average absolute 1-day post-event return.

        Args:
            ticker: Ticker symbol.
            n: Number of most recent events to average over.

        Returns:
            ``(mean_abs_return_pct, event_count)`` or ``None`` if no
            events have a computed move. ``event_count`` is the number
            of events actually used, never greater than ``n``.
        """
        events = await self.get_recent_events(ticker, n=n)
        moves = [
            abs(e.one_day_move_pct)
            for e in events
            if e.one_day_move_pct is not None
        ]
        if not moves:
            return None
        return (sum(moves) / len(moves), len(moves))

    async def get_next_event(self, ticker: str) -> Optional[EarningsEvent]:
        """Return the next future-dated event, if any."""
        return await EarningsHistoryTable.get_next_event(ticker)

    # ------------------------------------------------------------------
    # Writes / refresh (require clients)
    # ------------------------------------------------------------------

    async def refresh_ticker(
        self,
        ticker: str,
        *,
        as_of: Optional[date] = None,
    ) -> RefreshReport:
        """Fetch the last ~4 quarters of earnings + compute 1-day moves.

        Finnhub's free-tier ``/calendar/earnings`` endpoint only returns
        the next upcoming event regardless of the date range, so we
        use ``/stock/earnings`` which returns the past ~4 quarters with
        EPS actuals but only a quarter-end ``period`` (not the
        announcement date).

        To locate the actual announcement day we scan the 14-45 day
        window following each quarter-end in price history and pick
        the highest-volume trading day — that's the announcement with
        very high probability. From there we compute the close-to-close
        1-day post-event move.

        Future events are picked up separately via
        ``/calendar/earnings`` so the next-event lookup still works.
        """
        if self._finnhub is None:
            raise RuntimeError("refresh_ticker requires a FinnhubClient")

        report = RefreshReport(tickers_attempted=1)
        as_of = as_of or date.today()

        events: list[EarningsEvent] = []

        # ---- 1. Historical quarters via /stock/earnings ----
        try:
            eps_rows = await self._finnhub.get_earnings_history(ticker)
        except Exception as e:  # pragma: no cover — defensive
            report.tickers_failed = 1
            report.failures.append(f"{ticker}: Finnhub earnings error: {e}")
            logger.exception(f"Finnhub earnings fetch failed for {ticker}")
            return report

        for row in eps_rows:
            period_str = row.get("period")
            if not period_str:
                continue
            try:
                period_date = datetime.strptime(period_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            announcement_date: Optional[date] = None
            move_data: Optional[tuple[float, float, float]] = None
            if self._price_history is not None:
                announcement_date = await self._detect_announcement_date(
                    ticker, period_date, as_of
                )
                if announcement_date is not None and announcement_date <= as_of:
                    move_data = await self._compute_one_day_move(
                        ticker, announcement_date, time_of_day=None
                    )

            event_date = announcement_date or period_date
            fiscal_period = None
            q = row.get("quarter")
            y = row.get("year")
            if q and y:
                fiscal_period = f"Q{q} {y}"

            base = EarningsEvent(
                ticker=ticker,
                earnings_date=event_date.isoformat(),
                fiscal_period=fiscal_period,
                time_of_day=None,
                eps_estimate=_as_float(row.get("estimate")),
                eps_actual=_as_float(row.get("actual")),
            )
            if move_data is not None:
                pre_close, post_close, move_pct = move_data
                base = base.model_copy(update={
                    "pre_earnings_close": pre_close,
                    "post_earnings_close": post_close,
                    "one_day_move_pct": move_pct,
                })
                report.moves_computed += 1
            events.append(base)

        # Note: next-upcoming event lookup is intentionally NOT done
        # here. ``EarningsCacheService`` (hot-path ``/calendar/earnings``
        # daily refresh) already tracks the next event; calling Finnhub
        # twice per ticker here would double the backfill time without
        # adding signal to Pillar v4's historical_move_magnitude.

        if events:
            # Clear any stale events from prior runs before writing
            # so re-running the backfill is idempotent — earlier
            # heuristics may have stored different dates for the same
            # quarter.
            await EarningsHistoryTable.delete_all_for_ticker(ticker)
            await EarningsHistoryTable.put_batch(events)
            report.events_written = len(events)
            report.tickers_succeeded = 1
        else:
            # No events isn't necessarily a failure — ETFs and newly
            # listed tickers legitimately have no earnings history.
            report.tickers_succeeded = 1

        return report

    async def _detect_announcement_date(
        self,
        ticker: str,
        period_date: date,
        as_of: date,
    ) -> Optional[date]:
        """Return the likely earnings-announcement trading day.

        US large-caps report 3-5 weeks after quarter-end. We pull a
        15-day window [period+21, period+35] from price history and
        pick the day with the highest composite score of
        (daily volume) × (|daily return|) — the best single signal
        available from bar data. Earnings days are simultaneously
        high-volume AND high-return; either signal alone gives false
        positives from product launches or macro events, but the
        product of the two is dominated by earnings.

        Returns None when we don't have enough bars (newly-IPO'd
        tickers, missing history).
        """
        if self._price_history is None:
            return None
        window_start = period_date + timedelta(days=21)
        window_end = min(period_date + timedelta(days=35), as_of)
        if window_end <= window_start:
            return None

        # Pull a slightly larger window so we can compute prior-day
        # returns for the first candidate bar.
        bars = await self._price_history.get_bars(
            ticker,
            lookback_days=60,
            as_of=window_end,
            allow_fallback=False,
        )
        if len(bars) < 2:
            return None

        # Sort oldest-first and build (prev_close, bar) pairs so we
        # can score the window candidates.
        by_date_sorted = sorted(bars, key=lambda b: b.date)
        best_date: Optional[str] = None
        best_score = -1.0
        for i in range(1, len(by_date_sorted)):
            bar = by_date_sorted[i]
            if not (window_start.isoformat() <= bar.date <= window_end.isoformat()):
                continue
            prev_close = by_date_sorted[i - 1].close
            if prev_close <= 0:
                continue
            daily_ret = abs((bar.close - prev_close) / prev_close)
            score = daily_ret * bar.volume
            if score > best_score:
                best_score = score
                best_date = bar.date

        if best_date is None:
            return None
        try:
            return datetime.strptime(best_date, "%Y-%m-%d").date()
        except ValueError:
            return None

    async def refresh_tickers(
        self,
        tickers: list[str],
        *,
        as_of: Optional[date] = None,
    ) -> RefreshReport:
        """Refresh earnings history for a batch of tickers.

        Finnhub rate limiting (30 req/min enforced by the client)
        applies automatically; each ticker is one request.
        """
        aggregate = RefreshReport()
        for ticker in tickers:
            single = await self.refresh_ticker(ticker, as_of=as_of)
            aggregate.tickers_attempted += single.tickers_attempted
            aggregate.tickers_succeeded += single.tickers_succeeded
            aggregate.tickers_failed += single.tickers_failed
            aggregate.events_written += single.events_written
            aggregate.moves_computed += single.moves_computed
            aggregate.failures.extend(single.failures)
        logger.info(
            f"refresh_tickers complete: "
            f"{aggregate.tickers_succeeded}/{aggregate.tickers_attempted} tickers, "
            f"{aggregate.events_written} events, "
            f"{aggregate.moves_computed} moves computed"
        )
        return aggregate

    async def refresh_completed_events(
        self,
        tickers: list[str],
        *,
        as_of: Optional[date] = None,
    ) -> RefreshReport:
        """Recompute 1-day moves for events whose date just passed.

        Called by the daily refresh hook. For each ticker we:
        1. Find events within the last 2 days that lack ``one_day_move_pct``.
        2. Compute the move from freshly-updated price history.
        3. Persist the update.
        """
        if self._price_history is None:
            raise RuntimeError(
                "refresh_completed_events requires a PriceHistoryService"
            )

        as_of = as_of or date.today()
        cutoff = as_of - timedelta(days=2)
        report = RefreshReport(tickers_attempted=len(tickers))

        for ticker in tickers:
            try:
                recent = await EarningsHistoryTable.list_by_ticker(
                    ticker, limit=4
                )
                for event in recent:
                    event_date = datetime.strptime(
                        event.earnings_date, "%Y-%m-%d"
                    ).date()
                    if event_date < cutoff or event_date > as_of:
                        continue
                    if event.one_day_move_pct is not None:
                        continue

                    move_data = await self._compute_one_day_move(
                        ticker, event_date, event.time_of_day
                    )
                    if move_data is None:
                        continue
                    pre_close, post_close, move_pct = move_data
                    updated = event.model_copy(
                        update={
                            "pre_earnings_close": pre_close,
                            "post_earnings_close": post_close,
                            "one_day_move_pct": move_pct,
                        }
                    )
                    await EarningsHistoryTable.put(updated)
                    report.moves_computed += 1
                    report.events_written += 1
                report.tickers_succeeded += 1
            except Exception as e:  # pragma: no cover — defensive
                report.tickers_failed += 1
                report.failures.append(f"{ticker}: {e}")
                logger.exception(f"refresh_completed_events failed for {ticker}")

        logger.info(
            f"refresh_completed_events: {report.moves_computed} moves computed"
        )
        return report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_raw_event(
        self, ticker: str, raw: dict[str, Any]
    ) -> Optional[EarningsEvent]:
        """Parse a Finnhub calendar row into an EarningsEvent.

        Returns None if the row lacks a date.
        """
        date_str = raw.get("date")
        if not date_str:
            return None
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            logger.debug(f"{ticker}: invalid earnings date {date_str!r}")
            return None

        hour = raw.get("hour", "") or ""
        time_of_day = hour if hour in ("bmo", "amc") else None

        quarter = raw.get("quarter")
        year = raw.get("year")
        fiscal_period: Optional[str] = None
        if quarter and year:
            fiscal_period = f"Q{quarter} {year}"

        return EarningsEvent(
            ticker=ticker,
            earnings_date=date_str,
            fiscal_period=fiscal_period,
            time_of_day=time_of_day,
            eps_estimate=_as_float(raw.get("epsEstimate")),
            eps_actual=_as_float(raw.get("epsActual")),
            revenue_estimate=_as_float(raw.get("revenueEstimate")),
            revenue_actual=_as_float(raw.get("revenueActual")),
        )

    async def _compute_one_day_move(
        self,
        ticker: str,
        event_date: date,
        time_of_day: Optional[str],
    ) -> Optional[tuple[float, float, float]]:
        """Return ``(pre_close, post_close, move_pct)`` or None.

        Rule (per design): 1-day move is close-to-close across the
        announcement. ``time_of_day`` determines which trading days
        bracket the announcement:

        - ``bmo`` (before market open): pre = previous-day close,
          post = event-day close.
        - ``amc`` (after market close) or unknown: pre = event-day close,
          post = next-trading-day close.
        """
        if self._price_history is None:
            return None

        # We need a small window around the event. Ask for 5 trading
        # days ending at event_date + 5 cal days so we have both sides.
        window_start = event_date - timedelta(days=7)
        window_end = event_date + timedelta(days=7)
        # Fetch enough bars and then pick out the two we need.
        bars = await self._price_history.get_bars(
            ticker,
            lookback_days=20,
            as_of=window_end,
            allow_fallback=False,
        )
        # Filter to the window; the cache may contain older rows.
        in_window = [
            b for b in bars
            if window_start.isoformat() <= b.date <= window_end.isoformat()
        ]
        if len(in_window) < 2:
            return None

        event_iso = event_date.isoformat()
        # Index bars by date for constant-time lookup.
        by_date = {b.date: b for b in in_window}
        sorted_dates = sorted(by_date.keys())

        if time_of_day == "bmo":
            # Pre = prior trading day's close; post = event day's close.
            post_on_event = by_date.get(event_iso)
            if post_on_event is None:
                return None
            pre_date = _latest_before(sorted_dates, event_iso)
            if pre_date is None:
                return None
            pre_close = by_date[pre_date].close
            post_close = post_on_event.close
        else:
            # amc / unknown: pre = event day's close; post = next day's close.
            pre_on_event = by_date.get(event_iso)
            if pre_on_event is None:
                # Event day was a non-trading day (rare). Bracket with
                # the nearest trading days on either side.
                prior = _latest_before(sorted_dates, event_iso)
                nxt = _earliest_after(sorted_dates, event_iso)
                if prior is None or nxt is None:
                    return None
                pre_close = by_date[prior].close
                post_close = by_date[nxt].close
            else:
                post_date = _earliest_after(sorted_dates, event_iso)
                if post_date is None:
                    return None
                pre_close = pre_on_event.close
                post_close = by_date[post_date].close

        if pre_close == 0:
            return None
        move_pct = ((post_close - pre_close) / pre_close) * 100
        return (pre_close, post_close, round(move_pct, 4))


def _as_float(v: object) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _latest_before(sorted_dates: list[str], target: str) -> Optional[str]:
    candidates = [d for d in sorted_dates if d < target]
    return candidates[-1] if candidates else None


def _earliest_after(sorted_dates: list[str], target: str) -> Optional[str]:
    candidates = [d for d in sorted_dates if d > target]
    return candidates[0] if candidates else None
