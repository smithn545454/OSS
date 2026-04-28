"""Convex Mode — Monthly universe construction orchestrator.

Pulls per-ticker inputs from existing OSS data sources (PriceHistoryTable,
StockSummaryTable for sector classification, Polygon for market cap +
options chain stats), runs them through the Stage 1 gates, applies the
sector cap, and writes a versioned ``ConvexUniverseSnapshot`` to
``oss-dev-convex-universe-snapshots``.

Invoked by the monthly EventBridge rule (added in Phase 2.5). Pure
fetching + orchestration; the gate math lives in stage1_universe.py and
stays unit-testable in isolation.

Phase 2 simplifications (documented for fast-follow):
    - ``avg_options_volume_30d`` and ``avg_atm_spread_pct`` are sourced
      from today's chain snapshot rather than a 30-day rolling average.
      Acceptable for monthly cadence; refine when historical chain data
      is available.
    - Market cap fetched live from Polygon ticker details.
    - Sector falls back to ``StockSummary.sector`` when present, else
      ``"Unknown"``. The impact report flags an authoritative sector
      source as a fast-follow.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Optional, Protocol

from app.convex.stage1_universe import (
    TickerKineticInputs,
    UniverseBuildResult,
    build_universe,
)
from app.core.schemas import (
    ConvexConfig,
    ConvexUniverseSnapshot,
)
from app.db.tables import (
    ConvexUniverseSnapshotTable,
    PriceHistoryTable,
    StockSummaryTable,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-source protocols (for dependency injection in tests)
# ---------------------------------------------------------------------------


class TickerMetadataFetcher(Protocol):
    """Provides market-cap and options chain metrics for a ticker.

    Production implementation wraps Polygon. Tests inject a stub that
    returns deterministic values without touching the network.
    """

    async def fetch(
        self, ticker: str
    ) -> Optional["TickerMetadata"]:  # noqa: F821 (forward ref OK)
        ...


class TickerMetadata:
    """Live metadata for a ticker pulled at universe-build time."""

    def __init__(
        self,
        market_cap: Optional[float] = None,
        avg_options_volume_30d: Optional[float] = None,
        avg_atm_spread_pct: Optional[float] = None,
    ) -> None:
        self.market_cap = market_cap
        self.avg_options_volume_30d = avg_options_volume_30d
        self.avg_atm_spread_pct = avg_atm_spread_pct


# ---------------------------------------------------------------------------
# Universe constructor
# ---------------------------------------------------------------------------


class UniverseConstructor:
    """Builds a ``ConvexUniverseSnapshot`` from live + historical data.

    Caller is responsible for providing the candidate ticker list (typically
    the optionable equity watchlist). The constructor handles concurrency,
    falls back gracefully when individual ticker data is missing, and
    persists the final snapshot to DynamoDB.
    """

    def __init__(
        self,
        config: ConvexConfig,
        metadata_fetcher: TickerMetadataFetcher,
        max_concurrency: int = 10,
    ) -> None:
        self.config = config
        self._fetcher = metadata_fetcher
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def build_snapshot(
        self,
        tickers: list[str],
        policy_version: str,
        as_of_date: Optional[date] = None,
        sectors: Optional[dict[str, str]] = None,
    ) -> ConvexUniverseSnapshot:
        """Construct, persist, and return a universe snapshot.

        Args:
            tickers: Candidate optionable tickers to evaluate.
            policy_version: Active policy version recorded on the snapshot.
            as_of_date: Snapshot date; defaults to today UTC.
            sectors: Optional pre-fetched ``{ticker: sector}`` map. When
                provided we skip the StockSummaryTable lookup (which is
                cache-only and unreliable for the broader universe). The
                authoritative source is ``oss-dev-sp500-tickers`` —
                callers should pass that map through.

        Returns:
            The persisted ``ConvexUniverseSnapshot``.
        """
        snapshot_date = (as_of_date or datetime.now(timezone.utc).date()).isoformat()
        sectors = sectors or {}
        logger.info(
            "UniverseConstructor: fetching inputs for %d tickers (snapshot %s, sector_map=%d)",
            len(tickers), snapshot_date, len(sectors),
        )

        inputs_results = await asyncio.gather(
            *(self._fetch_ticker_inputs(t, sectors.get(t)) for t in tickers),
            return_exceptions=False,
        )
        valid_inputs = [i for i in inputs_results if i is not None]
        skipped = len(tickers) - len(valid_inputs)
        if skipped > 0:
            logger.warning(
                "UniverseConstructor: skipped %d tickers due to missing data",
                skipped,
            )

        result: UniverseBuildResult = build_universe(valid_inputs, self.config)

        snapshot = ConvexUniverseSnapshot(
            snapshot_date=snapshot_date,
            policy_version=policy_version,
            tickers=result.entries,
            total_count=len(result.entries),
            sector_distribution=result.sector_distribution,
        )
        await ConvexUniverseSnapshotTable.put(snapshot)
        logger.info(
            "UniverseConstructor: wrote snapshot %s with %d tickers "
            "(rejected=%d, capped=%d)",
            snapshot_date,
            snapshot.total_count,
            len(result.rejected_tickers),
            len(result.capped_tickers),
        )
        return snapshot

    async def _fetch_ticker_inputs(
        self, ticker: str, sector: Optional[str] = None
    ) -> Optional[TickerKineticInputs]:
        """Fetch all inputs for a single ticker.

        Returns None when prerequisite data is missing — the ticker will
        be excluded from gate evaluation entirely (counted as skipped).
        """
        async with self._semaphore:
            try:
                bars = await PriceHistoryTable.list_by_ticker(
                    ticker, limit=260, scan_forward=True
                )
            except Exception as e:
                logger.warning("Price history fetch failed for %s: %s", ticker, e)
                return None

            if len(bars) < 61:
                # Need at least 60 days of HV + 1 for first return.
                return None
            closes = [b.close for b in bars]

            # Sector resolution: prefer sector passed by caller (sourced
            # from oss-dev-sp500-tickers); fall back to StockSummaryTable
            # cache when caller didn't supply one. Fail soft to None.
            if sector is None:
                try:
                    summary = await StockSummaryTable.get_latest_for_ticker(ticker)
                    sector = getattr(summary, "sector", None) if summary else None
                except Exception as e:
                    logger.debug("Sector lookup failed for %s: %s", ticker, e)

            # Live metadata: market cap + options stats. Best-effort.
            metadata: Optional[TickerMetadata] = None
            try:
                metadata = await self._fetcher.fetch(ticker)
            except Exception as e:
                logger.warning("Metadata fetch failed for %s: %s", ticker, e)

            return TickerKineticInputs(
                ticker=ticker,
                closes=closes,
                sector=sector,
                market_cap=metadata.market_cap if metadata else None,
                avg_options_volume_30d=(
                    metadata.avg_options_volume_30d if metadata else None
                ),
                avg_atm_spread_pct=(
                    metadata.avg_atm_spread_pct if metadata else None
                ),
            )
