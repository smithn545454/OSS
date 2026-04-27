"""Polygon-backed adapter for the UniverseConstructor metadata fetcher.

Wraps PolygonClient to satisfy the TickerMetadataFetcher protocol used by
the monthly Convex universe construction job. Phase 2 simplifications:
    - ``avg_options_volume_30d`` is computed from today's chain snapshot
      (sum of contract-level day volumes), used as a proxy for the
      30-day average.
    - ``avg_atm_spread_pct`` is the spread on the ATM monthly contract
      pulled from today's snapshot.
    - ``market_cap`` comes from Polygon ticker reference data.

Refining these to true 30-day averages is fast-follow (would require
historical chain snapshots; the impact report flags it).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.convex.universe_builder import TickerMetadata

logger = logging.getLogger(__name__)


class PolygonMetadataFetcher:
    """Production adapter satisfying the TickerMetadataFetcher protocol."""

    def __init__(self, polygon_client: Any) -> None:
        self._polygon = polygon_client

    async def fetch(self, ticker: str) -> Optional[TickerMetadata]:
        market_cap = await self._fetch_market_cap(ticker)
        avg_volume, avg_spread = await self._fetch_options_metrics(ticker)
        return TickerMetadata(
            market_cap=market_cap,
            avg_options_volume_30d=avg_volume,
            avg_atm_spread_pct=avg_spread,
        )

    async def _fetch_market_cap(self, ticker: str) -> Optional[float]:
        try:
            details = await self._polygon.get_ticker_details(ticker)
        except Exception as e:
            logger.warning("get_ticker_details(%s) failed: %s", ticker, e)
            return None
        if not details:
            return None
        cap = details.get("market_cap")
        if cap is None:
            return None
        try:
            return float(cap)
        except (TypeError, ValueError):
            return None

    async def _fetch_options_metrics(
        self, ticker: str
    ) -> tuple[Optional[float], Optional[float]]:
        """Pull a chain snapshot and derive aggregate volume + ATM spread.

        Returns (avg_volume_30d_proxy, avg_atm_spread_pct).
        """
        try:
            contracts = await self._polygon.get_options_chain_minimal(ticker)
        except Exception as e:
            logger.warning("get_options_chain_minimal(%s) failed: %s", ticker, e)
            return None, None

        if not contracts:
            return None, None

        total_volume = 0.0
        underlying_price: Optional[float] = None
        atm_spread_pct: Optional[float] = None
        atm_distance: Optional[float] = None

        for c in contracts:
            day = c.get("day", {}) or {}
            vol = day.get("volume") or 0
            try:
                total_volume += float(vol)
            except (TypeError, ValueError):
                pass

            # Underlying price for ATM selection — present on each contract row.
            if underlying_price is None:
                ua = c.get("underlying_asset", {}) or {}
                price = ua.get("price")
                if isinstance(price, (int, float)) and price > 0:
                    underlying_price = float(price)

            # ATM-monthly spread pct.
            details = c.get("details", {}) or {}
            strike = details.get("strike_price")
            quote = c.get("last_quote", {}) or {}
            bid = quote.get("bid") or 0
            ask = quote.get("ask") or 0
            if (
                underlying_price is not None
                and isinstance(strike, (int, float))
                and isinstance(bid, (int, float))
                and isinstance(ask, (int, float))
                and bid > 0
                and ask > 0
            ):
                mid = (bid + ask) / 2
                if mid <= 0:
                    continue
                distance = abs(strike - underlying_price)
                spread_pct = ((ask - bid) / mid) * 100.0
                if atm_distance is None or distance < atm_distance:
                    atm_distance = distance
                    atm_spread_pct = spread_pct

        return (
            total_volume if total_volume > 0 else None,
            atm_spread_pct,
        )
