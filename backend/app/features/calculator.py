"""Feature Calculator - Main orchestrator for Stage 4.

Computes all features for evaluations, coordinating data fetching
and individual feature calculations.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.core.schemas import (
    Evaluation,
    Opportunity,
    IVHistory,
    OIHistory,
    FeatureConfig,
)
from app.services.polygon import DailyBar, PolygonClient
from app.services.catalyst import CatalystDataService
from app.features.models import FeatureSet
from app.features.underlying import compute_underlying_features, UnderlyingFeatures
from app.features.relative_strength import compute_relative_strength_features
from app.features.volatility import compute_volatility_features
from app.features.contract import compute_contract_features
from app.features.liquidity import compute_liquidity_features
from app.features.catalyst import compute_catalyst_features

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
        polygon_client: PolygonClient,
        catalyst_service: Optional[CatalystDataService] = None,
        config: Optional[FeatureConfig] = None,
    ) -> None:
        """Initialize the feature computer.
        
        Args:
            polygon_client: Polygon API client for market data
            catalyst_service: Service for earnings/SEC filing data (optional)
            config: Feature computation configuration
        """
        self._polygon = polygon_client
        self._catalyst = catalyst_service
        self._config = config or FeatureConfig()
        
        # Cache for underlying bars (ticker -> bars)
        self._underlying_bars_cache: dict[str, list[DailyBar]] = {}
        
        # Cache for SPY bars
        self._spy_bars: Optional[list[DailyBar]] = None
    
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
        
        # Get underlying bars if not provided
        if underlying_bars is None:
            underlying_bars = self._underlying_bars_cache.get(ticker)
            if underlying_bars is None:
                underlying_bars = await self._polygon.get_daily_bars_parsed(
                    ticker=ticker,
                    from_date=self._get_lookback_date(60),
                    to_date=self._get_today(),
                )
                self._underlying_bars_cache[ticker] = underlying_bars
        
        # Get SPY bars if not provided
        if spy_bars is None:
            if self._spy_bars is None:
                self._spy_bars = await self._polygon.get_daily_bars_parsed(
                    ticker=self._config.rs_benchmark_ticker,
                    from_date=self._get_lookback_date(60),
                    to_date=self._get_today(),
                )
            spy_bars = self._spy_bars
        
        # =========================================================================
        # Category F: Catalyst Features (fetch early for IV regime classification)
        # =========================================================================
        # Fetch catalyst data if not provided and service is available
        if self._catalyst is not None:
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
        # Category B: Relative Strength Features
        # =========================================================================
        rs_features = compute_relative_strength_features(
            underlying_return_5d=underlying_features.return_5d,
            underlying_return_20d=underlying_features.return_20d,
            spy_bars=spy_bars,
        )
        
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
        
        # Get unique tickers for batch fetching
        tickers = list(set(e.underlying_ticker for e in evaluations))
        tickers.append(self._config.rs_benchmark_ticker)  # Add SPY
        
        # Batch fetch underlying bars
        logger.info(f"Fetching daily bars for {len(tickers)} tickers")
        bars_by_ticker = await self._polygon.get_daily_bars_batch(tickers, days=60)
        
        # Cache bars
        self._underlying_bars_cache.update(bars_by_ticker)
        self._spy_bars = bars_by_ticker.get(self._config.rs_benchmark_ticker)
        
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
