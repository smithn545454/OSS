"""Shared time/calendar helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def trading_days_cutoff(trading_days: int) -> str:
    """Return ISO timestamp N trading days (weekdays) ago from now.

    Counts back weekdays only (Mon-Fri), not calendar days. Used to bound
    queries against the APPROVE evaluations table to "within the last N
    trading days" — which during normal trading weeks matches "today" for
    N=1, but skips past weekends so Monday morning still finds Friday's
    evaluations.
    """
    now = datetime.now(timezone.utc)
    days_back = 0
    counted = 0
    while counted < trading_days:
        days_back += 1
        if (now - timedelta(days=days_back)).weekday() < 5:  # Mon=0..Fri=4
            counted += 1
    return (now - timedelta(days=days_back)).isoformat()
