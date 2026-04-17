"""Category B: Relative Strength Features.

Computes relative strength vs SPY benchmark and (Pillar v4) vs the
ticker's GICS sector ETF. Per Section 13.2 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from app.scanners.utils import calculate_returns
from app.services.polygon import DailyBar

logger = logging.getLogger(__name__)


# Map GICS-ish sector labels to SPDR sector ETF tickers. Keys cover the
# common spellings Finnhub/other sources emit so the lookup works on
# whatever sector string is stored in SP500TickerTable.
SECTOR_ETF_MAP: dict[str, str] = {
    # Canonical GICS names
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Staples": "XLP",
    "Consumer Discretionary": "XLY",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    # Common aliases
    "Technology": "XLK",
    "Financial": "XLF",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Basic Materials": "XLB",
    "Communication": "XLC",
    "Telecommunications": "XLC",
}


def sector_etf_for(sector: Optional[str]) -> Optional[str]:
    """Return the tracking ETF ticker for a sector string, or None."""
    if not sector:
        return None
    return SECTOR_ETF_MAP.get(sector.strip())


@dataclass
class RelativeStrengthFeatures:
    """Category B features for relative strength."""

    spy_return_5d: Optional[float] = None
    spy_return_20d: Optional[float] = None
    rs_5d: Optional[float] = None  # return_5d - spy_return_5d
    rs_20d: Optional[float] = None  # return_20d - spy_return_20d
    # Pillar v4 — sector ETF 20d return minus SPY 20d return.
    sector_rs_20d: Optional[float] = None


def compute_relative_strength_features(
    underlying_return_5d: Optional[float],
    underlying_return_20d: Optional[float],
    spy_bars: Sequence[DailyBar],
    *,
    sector_bars: Optional[Sequence[DailyBar]] = None,
) -> RelativeStrengthFeatures:
    """Compute relative strength features vs SPY (and optionally sector ETF).

    Args:
        underlying_return_5d: 5-day return of the underlying
        underlying_return_20d: 20-day return of the underlying
        spy_bars: Daily bars for SPY benchmark
        sector_bars: Daily bars for the ticker's sector ETF. When provided
            with at least 21 bars, ``sector_rs_20d`` is populated as the
            20-day sector ETF return minus the 20-day SPY return.

    Returns:
        RelativeStrengthFeatures dataclass
    """
    # Calculate SPY returns
    spy_return_5d = None
    spy_return_20d = None

    if spy_bars and len(spy_bars) >= 6:
        spy_closes = [bar.close for bar in spy_bars]
        spy_return_5d = calculate_returns(spy_closes, 5)

    if spy_bars and len(spy_bars) >= 21:
        spy_closes = [bar.close for bar in spy_bars]
        spy_return_20d = calculate_returns(spy_closes, 20)

    # Calculate relative strength
    rs_5d = None
    rs_20d = None

    if underlying_return_5d is not None and spy_return_5d is not None:
        rs_5d = underlying_return_5d - spy_return_5d

    if underlying_return_20d is not None and spy_return_20d is not None:
        rs_20d = underlying_return_20d - spy_return_20d

    # Sector RS (Pillar v4)
    sector_rs_20d = None
    if (
        sector_bars
        and len(sector_bars) >= 21
        and spy_return_20d is not None
    ):
        sector_closes = [bar.close for bar in sector_bars]
        sector_return_20d = calculate_returns(sector_closes, 20)
        if sector_return_20d is not None:
            sector_rs_20d = sector_return_20d - spy_return_20d

    return RelativeStrengthFeatures(
        spy_return_5d=spy_return_5d,
        spy_return_20d=spy_return_20d,
        rs_5d=rs_5d,
        rs_20d=rs_20d,
        sector_rs_20d=sector_rs_20d,
    )


def compute_sector_relative_strength_20d(
    sector_bars: Sequence[DailyBar],
    spy_return_20d: Optional[float],
) -> Optional[float]:
    """Return sector_rs_20d from pre-fetched sector ETF bars and SPY return.

    Convenience wrapper for callers that already have SPY's 20-day
    return computed (e.g., shared across many underlyings in the same
    pipeline run).
    """
    if not sector_bars or len(sector_bars) < 21 or spy_return_20d is None:
        return None
    closes = [bar.close for bar in sector_bars]
    sector_ret = calculate_returns(closes, 20)
    if sector_ret is None:
        return None
    return sector_ret - spy_return_20d
