"""Feature Calculator - Main orchestrator for Stage 4.

Computes all features for evaluations, coordinating data fetching
and individual feature calculations.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional, Sequence

from app.core.schemas import (
    Evaluation,
    FeatureConfig,
    IVHistory,
    OIHistory,
    Opportunity,
)
from app.features.catalyst import compute_catalyst_features
from app.features.contract import compute_contract_features
from app.features.liquidity import compute_liquidity_features
from app.features.models import FeatureSet
from app.features.relative_strength import (
    compute_relative_strength_features,
    sector_etf_for,
)
from app.features.underlying import UnderlyingFeatures, compute_underlying_features
from app.features.volatility import compute_volatility_features
from app.services.catalyst import CatalystDataService
from app.services.earnings_calendar import EarningsCalendarService
from app.services.polygon import DailyBar, PolygonClient
from app.services.price_history import PriceHistoryService

logger = logging.getLogger(__name__)


class FeatureComputer:
    """Orchestrates feature computation for Stage 4 of the pipeline.

    This class coordinates:
    1. Fetching required market data (underlying bars, SPY bars)
    2. Retrieving historical IV and OI data from DynamoDB
    3. Fetching catalyst data (earnings, SEC filings)
    4. Computing all feature categories
    5. Assembling FeatureSet records
    """

    def __init__(
        self,
        polygon_client: Optional[PolygonClient] = None,
        catalyst_service: Optional[CatalystDataService] = None,
        config: Optional[FeatureConfig] = None,
        data_provider: Optional[Any] = None,
        as_of_date: Optional[date] = None,
        earnings_calendar_service: Optional[EarningsCalendarService] = None,
        sector_map: Optional[dict[str, str]] = None,
        price_history_service: Optional[PriceHistoryService] = None,
    ) -> None:
        """Initialize the feature computer.

        Args:
            polygon_client: Polygon API client for market data (live mode)
            catalyst_service: Service for earnings/SEC filing data (optional)
            config: Feature computation configuration
            data_provider: Optional DataProvider for unified data access (backtest mode)
            as_of_date: Target date for backtesting (defaults to today)
            earnings_calendar_service: Pillar v4 service — provides
                historical_move_magnitude from ``oss-dev-earnings-history``.
                When omitted, the feature is left as None.
            sector_map: Pre-fetched {ticker: sector} mapping. Enables
                sector_rs_20d feature when paired with pre-fetched
                sector ETF bars. When omitted, sector_rs_20d is None.
            price_history_service: Pillar v4 service — reads 252-day
                daily bars from ``oss-dev-price-history`` with Polygon
                write-through fallback. When provided, bar fetches
                route through it (guarantees ma_200 / high_52w have
                enough data) instead of hitting Polygon's grouped
                daily API, which returns fewer than 252 bars after
                market-holiday days are excluded.
        """
        self._polygon = polygon_client
        self._catalyst = catalyst_service
        self._config = config or FeatureConfig()
        self._data_provider = data_provider
        self._as_of_date = as_of_date
        self._earnings_calendar = earnings_calendar_service
        self._sector_map: dict[str, str] = sector_map or {}
        self._price_history = price_history_service

        # Cache for underlying bars (ticker -> bars)
        self._underlying_bars_cache: dict[str, list[DailyBar]] = {}

        # Cache for SPY bars
        self._spy_bars: Optional[list[DailyBar]] = None

        # Cache for sector ETF bars (etf_ticker -> bars)
        self._sector_bars_cache: dict[str, list[DailyBar]] = {}

    async def compute_features(
        self,
        evaluation: Evaluation,
        opportunity: Opportunity,
        iv_history: Optional[Sequence[IVHistory]] = None,
        oi_history: Optional[Sequence[OIHistory]] = None,
        underlying_bars: Optional[Sequence[DailyBar]] = None,
        spy_bars: Optional[Sequence[DailyBar]] = None,
        days_to_earnings: Optional[int] = None,
        recent_sec_filing: Optional[bool] = None,
    ) -> FeatureSet:
        """Compute all features for a single evaluation.

        Args:
            evaluation: Evaluation record from Stage 3
            opportunity: Parent opportunity (for scanner triggers)
            iv_history: Historical IV records (optional, will be fetched if None)
            oi_history: Historical OI records (optional, will be fetched if None)
            underlying_bars: Daily bars for underlying (optional, will be fetched if None)
            spy_bars: Daily bars for SPY (optional, will be fetched if None)
            days_to_earnings: Pre-fetched days to earnings (optional)
            recent_sec_filing: Pre-fetched SEC filing status (optional)

        Returns:
            FeatureSet with all computed features
        """
        ticker = evaluation.underlying_ticker

        effective_date = self._as_of_date or date.today()

        # Get underlying bars if not provided
        if underlying_bars is None:
            underlying_bars = self._underlying_bars_cache.get(ticker)
            if underlying_bars is None:
                if self._price_history is not None:
                    underlying_bars = await self._price_history.get_bars(
                        ticker, lookback_days=252, as_of=effective_date,
                    )
                elif self._data_provider:
                    underlying_bars = await self._data_provider.get_daily_bars(
                        ticker, end_date=effective_date, lookback_days=252,
                    )
                elif self._polygon:
                    underlying_bars = await self._polygon.get_daily_bars_parsed(
                        ticker=ticker,
                        from_date=self._get_lookback_date(252),
                        to_date=self._get_today(),
                    )
                else:
                    underlying_bars = []
                self._underlying_bars_cache[ticker] = underlying_bars

        # Get SPY bars if not provided
        if spy_bars is None:
            if self._spy_bars is None:
                if self._price_history is not None:
                    self._spy_bars = await self._price_history.get_bars(
                        self._config.rs_benchmark_ticker,
                        lookback_days=252,
                        as_of=effective_date,
                    )
                elif self._data_provider:
                    self._spy_bars = await self._data_provider.get_daily_bars(
                        self._config.rs_benchmark_ticker,
                        end_date=effective_date, lookback_days=252,
                    )
                elif self._polygon:
                    self._spy_bars = await self._polygon.get_daily_bars_parsed(
                        ticker=self._config.rs_benchmark_ticker,
                        from_date=self._get_lookback_date(252),
                        to_date=self._get_today(),
                    )
                else:
                    self._spy_bars = []
            spy_bars = self._spy_bars

        # =========================================================================
        # Category F: Catalyst Features (fetch early for IV regime classification)
        # =========================================================================
        # Fetch catalyst data from DataProvider or CatalystDataService
        if self._data_provider:
            if days_to_earnings is None:
                days_to_earnings = await self._data_provider.get_days_to_earnings(
                    ticker, as_of=effective_date,
                )
            if recent_sec_filing is None:
                recent_sec_filing = await self._data_provider.get_recent_sec_filing(
                    ticker, as_of=effective_date,
                )
        elif self._catalyst is not None:
            if days_to_earnings is None:
                days_to_earnings = await self._catalyst.get_days_to_earnings(ticker)
            if recent_sec_filing is None:
                recent_sec_filing = await self._catalyst.get_recent_sec_filing(ticker)

        # Default to None/False if still not available
        if recent_sec_filing is None:
            recent_sec_filing = False

        catalyst_features = compute_catalyst_features(
            days_to_earnings=days_to_earnings,
            recent_sec_filing=recent_sec_filing,
        )

        # =========================================================================
        # Category A: Underlying Technical Features
        # =========================================================================
        underlying_features = compute_underlying_features(underlying_bars)

        if underlying_features is None:
            # Fallback with minimal data from evaluation
            underlying_features = UnderlyingFeatures(close=evaluation.underlying_price)

        # =========================================================================
        # Category B: Relative Strength Features (+ Pillar v4 sector RS)
        # =========================================================================
        sector = self._sector_map.get(ticker)
        sector_etf = sector_etf_for(sector)
        sector_bars: Optional[list[DailyBar]] = None
        if sector_etf:
            sector_bars = self._sector_bars_cache.get(sector_etf)
            if sector_bars is None and self._price_history is not None:
                sector_bars = await self._price_history.get_bars(
                    sector_etf, lookback_days=252, as_of=effective_date,
                )
                self._sector_bars_cache[sector_etf] = sector_bars

        rs_features = compute_relative_strength_features(
            underlying_return_5d=underlying_features.return_5d,
            underlying_return_20d=underlying_features.return_20d,
            spy_bars=spy_bars,
            sector_bars=sector_bars,
        )

        # =========================================================================
        # Pillar v4: historical_move_magnitude from EarningsHistoryTable
        # =========================================================================
        historical_move_magnitude: Optional[float] = None
        historical_move_confidence: Optional[int] = None
        if self._earnings_calendar is not None:
            result = await self._earnings_calendar.get_historical_move_magnitude(
                ticker
            )
            if result is not None:
                historical_move_magnitude, historical_move_confidence = result

        # =========================================================================
        # Category C: Volatility Features (uses days_to_earnings for IV regime)
        # =========================================================================
        vol_features = compute_volatility_features(
            iv=evaluation.iv,
            rv20=underlying_features.rv20,
            iv_history=iv_history,
            days_to_earnings=days_to_earnings,  # Now passed from catalyst data
            config=self._config,
        )

        # =========================================================================
        # Category D: Contract-Specific Features
        # =========================================================================
        contract_features = compute_contract_features(evaluation)

        # =========================================================================
        # Category E: Liquidity Features
        # =========================================================================
        liquidity_features = compute_liquidity_features(
            open_interest=evaluation.open_interest,
            volume=evaluation.volume,
            oi_history=oi_history,
        )

        # =========================================================================
        # Assemble FeatureSet
        # =========================================================================
        return FeatureSet(
            evaluation_id=evaluation.evaluation_id,
            # Category A
            close=underlying_features.close,
            sma20=underlying_features.sma20,
            sma50=underlying_features.sma50,
            return_5d=underlying_features.return_5d,
            return_20d=underlying_features.return_20d,
            trend_aligned_bullish=underlying_features.trend_aligned_bullish,
            trend_aligned_bearish=underlying_features.trend_aligned_bearish,
            atr14=underlying_features.atr14,
            atr14_pct=underlying_features.atr14_pct,
            ema_9=underlying_features.ema_9,
            ema_21=underlying_features.ema_21,
            ema_50=underlying_features.ema_50,
            ema_200=underlying_features.ema_200,
            ema_alignment=underlying_features.ema_alignment,
            rsi_14=underlying_features.rsi_14,
            macd_histogram=underlying_features.macd_histogram,
            adx_14=underlying_features.adx_14,
            plus_di=underlying_features.plus_di,
            minus_di=underlying_features.minus_di,
            obv_trend=underlying_features.obv_trend,
            # Category B
            spy_return_5d=rs_features.spy_return_5d,
            spy_return_20d=rs_features.spy_return_20d,
            rs_5d=rs_features.rs_5d,
            rs_20d=rs_features.rs_20d,
            # Category C
            rv20=vol_features.rv20,
            iv=vol_features.iv,
            iv_rv_ratio=vol_features.iv_rv_ratio,
            iv_percentile=vol_features.iv_percentile,
            iv_10d_change=vol_features.iv_10d_change,
            iv_regime=vol_features.iv_regime,
            # Category D
            mid=contract_features.mid,
            spread_pct=contract_features.spread_pct,
            theta_pct=contract_features.theta_pct,
            breakeven_price=contract_features.breakeven_price,
            required_move_pct=contract_features.required_move_pct,
            expected_move_pct=contract_features.expected_move_pct,
            feasibility_ratio=contract_features.feasibility_ratio,
            time_adjusted_feasibility=contract_features.time_adjusted_feasibility,
            theta_adjusted_edge=contract_features.theta_adjusted_edge,
            # Category E
            open_interest=liquidity_features.open_interest,
            volume=liquidity_features.volume,
            oi_5d_change_pct=liquidity_features.oi_5d_change_pct,
            # Category F
            days_to_earnings=catalyst_features.days_to_earnings,
            recent_sec_filing=catalyst_features.recent_sec_filing,
            # Category H — Pillar v4 long-range technicals + sector context
            ma_150=underlying_features.ma_150,
            ma_200=underlying_features.ma_200,
            high_52w=underlying_features.high_52w,
            low_52w=underlying_features.low_52w,
            dist_to_52w_high_pct=underlying_features.dist_to_52w_high_pct,
            dist_to_52w_low_pct=underlying_features.dist_to_52w_low_pct,
            bb_width=underlying_features.bb_width,
            bb_width_percentile=underlying_features.bb_width_percentile,
            sector=sector,
            sector_rs_20d=rs_features.sector_rs_20d,
            # Category I — Pillar v4 catalyst extensions
            historical_move_magnitude=historical_move_magnitude,
            historical_move_confidence=historical_move_confidence,
        )

    async def compute_features_batch(
        self,
        evaluations: Sequence[Evaluation],
        opportunities: Sequence[Opportunity],
        iv_history_map: Optional[dict[str, Sequence[IVHistory]]] = None,
        oi_history_map: Optional[dict[str, Sequence[OIHistory]]] = None,
    ) -> list[FeatureSet]:
        """Compute features for multiple evaluations.

        Optimizes data fetching by batching API calls.

        Args:
            evaluations: List of Evaluation records
            opportunities: List of Opportunity records (for linking)
            iv_history_map: Pre-fetched IV history by ticker
            oi_history_map: Pre-fetched OI history by option_ticker

        Returns:
            List of FeatureSet records
        """
        if not evaluations:
            return []

        # Build opportunity lookup
        opp_by_ticker = {opp.underlying_ticker: opp for opp in opportunities}

        # Get unique tickers for batch fetching — include SPY and every
        # sector ETF whose sector is represented by any underlying in
        # this batch, so per-ticker compute can read from cache.
        underlying_tickers = list(set(e.underlying_ticker for e in evaluations))
        sector_etfs_needed: set[str] = set()
        if self._sector_map:
            for t in underlying_tickers:
                etf = sector_etf_for(self._sector_map.get(t))
                if etf:
                    sector_etfs_needed.add(etf)

        tickers = list(
            set(underlying_tickers)
            | {self._config.rs_benchmark_ticker}
            | sector_etfs_needed
        )

        effective_date = self._as_of_date or date.today()

        # Batch fetch underlying bars
        logger.info(
            f"Fetching daily bars for {len(tickers)} tickers "
            f"({len(underlying_tickers)} underlyings, 1 benchmark, "
            f"{len(sector_etfs_needed)} sector ETFs)"
        )
        if self._price_history is not None:
            # Pillar v4 path: read 252 trading-day bars from DynamoDB
            # (oss-dev-price-history). The table is kept warm by the
            # nightly refresh hook plus the Phase 1 backfill, so this
            # returns ≥252 bars for covered tickers — enough for both
            # ma_200 and high_52w. Fallback to Polygon is built into
            # the service when the cache is sparse.
            import asyncio as _asyncio

            async def _fetch_one(t: str) -> tuple[str, list[DailyBar]]:
                try:
                    bars = await self._price_history.get_bars(
                        t, lookback_days=252, as_of=effective_date
                    )
                    return t, bars
                except Exception as e:  # defensive: never fail the whole batch
                    logger.warning(f"price-history fetch failed for {t}: {e}")
                    return t, []

            results = await _asyncio.gather(*[_fetch_one(t) for t in tickers])
            bars_by_ticker = {t: bars for t, bars in results}
        elif self._data_provider:
            bars_by_ticker = await self._data_provider.get_daily_bars_batch(
                tickers, end_date=effective_date, lookback_days=252,
            )
        elif self._polygon:
            bars_by_ticker = await self._polygon.get_daily_bars_batch(tickers, days=252)
        else:
            bars_by_ticker = {}

        # Cache bars — split into underlying vs sector ETF caches for clarity.
        self._underlying_bars_cache.update(bars_by_ticker)
        self._spy_bars = bars_by_ticker.get(self._config.rs_benchmark_ticker)
        for etf in sector_etfs_needed:
            bars = bars_by_ticker.get(etf)
            if bars:
                self._sector_bars_cache[etf] = bars

        # Prefetch catalyst data for all tickers (if service available)
        catalyst_tickers = [t for t in tickers if t != self._config.rs_benchmark_ticker]
        if self._catalyst is not None and catalyst_tickers:
            logger.info(f"Prefetching catalyst data for {len(catalyst_tickers)} tickers")
            await self._catalyst.prefetch_batch(catalyst_tickers)

        # Compute features for each evaluation
        feature_sets: list[FeatureSet] = []

        for evaluation in evaluations:
            ticker = evaluation.underlying_ticker
            opportunity = opp_by_ticker.get(ticker)

            if not opportunity:
                logger.warning(f"No opportunity found for ticker {ticker}")
                continue

            # Get historical data
            iv_history = None
            if iv_history_map:
                iv_history = iv_history_map.get(ticker)

            oi_history = None
            if oi_history_map:
                oi_history = oi_history_map.get(evaluation.option_ticker)

            underlying_bars = bars_by_ticker.get(ticker)
            spy_bars = self._spy_bars

            try:
                feature_set = await self.compute_features(
                    evaluation=evaluation,
                    opportunity=opportunity,
                    iv_history=iv_history,
                    oi_history=oi_history,
                    underlying_bars=underlying_bars,
                    spy_bars=spy_bars,
                    # Catalyst data will be fetched from cache by compute_features
                )
                feature_sets.append(feature_set)
            except Exception as e:
                logger.error(f"Error computing features for {evaluation.option_ticker}: {e}")
                continue

        logger.info(f"Computed features for {len(feature_sets)} evaluations")
        return feature_sets

    def clear_cache(self) -> None:
        """Clear cached data."""
        self._underlying_bars_cache.clear()
        self._spy_bars = None
        self._sector_bars_cache.clear()
        if self._catalyst is not None:
            self._catalyst.clear_cache()

    @staticmethod
    def _get_today() -> str:
        """Get today's date as YYYY-MM-DD."""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _get_lookback_date(days: int) -> str:
        """Get date N days ago as YYYY-MM-DD."""
        from datetime import datetime, timedelta
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
