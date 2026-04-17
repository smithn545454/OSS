"""Stage 4: Feature Computation Pipeline Stage.

Integrates feature computation into the pipeline orchestration.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Optional, Sequence

from app.core.pipeline import PipelineOrchestrator
from app.core.schemas import (
    Evaluation,
    FeatureConfig,
    IVHistory,
    OIHistory,
    Opportunity,
    PipelineStage,
)
from app.db.tables import FeatureValueTable, IVHistoryTable, OIHistoryTable
from app.features.calculator import FeatureComputer
from app.features.models import FeatureSet
from app.services.polygon import PolygonClient

logger = logging.getLogger(__name__)


class FeatureComputationStage:
    """Stage 4: Feature Computation.

    Takes Evaluations from Stage 3 and computes all features needed
    for pillar scoring in Stage 5.
    """

    def __init__(
        self,
        polygon_client: Optional[PolygonClient] = None,
        orchestrator: Optional[PipelineOrchestrator] = None,
        config: Optional[FeatureConfig] = None,
        catalyst_service: Optional[Any] = None,
        data_provider: Optional[Any] = None,
        as_of_date: Optional[date] = None,
        sector_map: Optional[dict[str, str]] = None,
        earnings_calendar_service: Optional[Any] = None,
    ) -> None:
        """Initialize the feature computation stage.

        Args:
            polygon_client: Polygon API client for market data (live mode)
            orchestrator: Pipeline orchestrator for event tracking
            config: Feature computation configuration
            catalyst_service: CatalystDataService for earnings/SEC filing data
            data_provider: Optional DataProvider for unified data access (backtest mode)
            as_of_date: Target date for backtesting (defaults to today)
            sector_map: Pillar v4 — pre-fetched {ticker: sector} mapping.
                Required for sector_rs_20d feature.
            earnings_calendar_service: Pillar v4 — service that provides
                historical_move_magnitude from oss-dev-earnings-history.
        """
        self._polygon = polygon_client
        self._orchestrator = orchestrator or PipelineOrchestrator()
        self._config = config or FeatureConfig()
        self._data_provider = data_provider
        self._as_of_date = as_of_date
        self._computer = FeatureComputer(
            polygon_client=polygon_client,
            catalyst_service=catalyst_service,
            config=config,
            data_provider=data_provider,
            as_of_date=as_of_date,
            sector_map=sector_map,
            earnings_calendar_service=earnings_calendar_service,
        )

    async def execute(
        self,
        run_id: str,
        evaluations: Sequence[Evaluation],
        opportunities: Sequence[Opportunity],
        persist_features: bool = True,
    ) -> list[FeatureSet]:
        """Execute feature computation stage.

        Args:
            run_id: Pipeline run ID for tracking
            evaluations: Evaluations from Stage 3
            opportunities: Opportunities (for linking and scanner triggers)
            persist_features: If True, save FeatureValues to DynamoDB

        Returns:
            List of computed FeatureSets
        """
        logger.info(f"Stage 4: Computing features for {len(evaluations)} evaluations")

        # Update current stage
        await self._orchestrator.update_current_stage(run_id, PipelineStage.FEATURE_COMPUTATION)

        if not evaluations:
            # Record empty stage event
            await self._orchestrator.record_stage_event(
                run_id=run_id,
                stage=PipelineStage.FEATURE_COMPUTATION,
                items_in=0,
                items_out=0,
            )
            return []

        # Fetch IV history for all unique tickers
        tickers = list(set(e.underlying_ticker for e in evaluations))
        iv_history_map = await self._fetch_iv_history(tickers)

        # Fetch OI history for all option contracts
        option_tickers = [e.option_ticker for e in evaluations]
        oi_history_map = await self._fetch_oi_history(option_tickers)

        # Compute features
        feature_sets = await self._computer.compute_features_batch(
            evaluations=evaluations,
            opportunities=opportunities,
            iv_history_map=iv_history_map,
            oi_history_map=oi_history_map,
        )

        # Persist feature values if requested
        if persist_features and feature_sets:
            await self._persist_features(feature_sets)

        # Record stage event
        await self._orchestrator.record_stage_event(
            run_id=run_id,
            stage=PipelineStage.FEATURE_COMPUTATION,
            items_in=len(evaluations),
            items_out=len(feature_sets),
            metadata={
                "unique_tickers": len(tickers),
                "iv_history_available": len(iv_history_map),
                "oi_history_available": len(oi_history_map),
            },
        )

        logger.info(f"Stage 4 complete: {len(feature_sets)} feature sets computed")
        return feature_sets

    async def _fetch_iv_history(
        self,
        tickers: list[str],
    ) -> dict[str, list[IVHistory]]:
        """Fetch IV history for all tickers in parallel.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dict mapping ticker to IV history records
        """
        if not tickers:
            return {}

        effective_date = self._as_of_date or date.today()

        async def fetch_one(ticker: str) -> tuple[str, list[IVHistory]]:
            try:
                if self._data_provider:
                    history = await self._data_provider.get_iv_history(
                        ticker, as_of=effective_date,
                        lookback_days=self._config.iv_percentile_lookback_days,
                    )
                else:
                    history = await IVHistoryTable.list_by_ticker(
                        ticker=ticker,
                        limit=self._config.iv_percentile_lookback_days,
                    )
                return ticker, history or []
            except Exception as e:
                logger.warning(f"Error fetching IV history for {ticker}: {e}")
                return ticker, []

        results = await asyncio.gather(*[fetch_one(t) for t in tickers])
        iv_history_map = {ticker: history for ticker, history in results if history}

        logger.debug(f"Fetched IV history for {len(iv_history_map)}/{len(tickers)} tickers")
        return iv_history_map

    async def _fetch_oi_history(
        self,
        option_tickers: list[str],
    ) -> dict[str, list[OIHistory]]:
        """Fetch OI history for all option contracts in parallel.

        Args:
            option_tickers: List of option ticker symbols

        Returns:
            Dict mapping option ticker to OI history records
        """
        if not option_tickers:
            return {}

        effective_date = self._as_of_date or date.today()

        async def fetch_one(option_ticker: str) -> tuple[str, list[OIHistory]]:
            try:
                if self._data_provider:
                    history = await self._data_provider.get_oi_history(
                        option_ticker, as_of=effective_date, lookback_days=5,
                    )
                else:
                    history = await OIHistoryTable.list_by_contract(
                        option_ticker=option_ticker,
                        limit=10,  # Need 5 days for 5-day change
                    )
                return option_ticker, history or []
            except Exception as e:
                logger.warning(f"Error fetching OI history for {option_ticker}: {e}")
                return option_ticker, []

        results = await asyncio.gather(*[fetch_one(ot) for ot in option_tickers])
        oi_history_map = {ticker: history for ticker, history in results if history}

        logger.debug(
            f"Fetched OI history for {len(oi_history_map)}/{len(option_tickers)} contracts"
        )
        return oi_history_map

    async def _persist_features(self, feature_sets: list[FeatureSet]) -> None:
        """Persist feature values to DynamoDB.

        Args:
            feature_sets: List of computed FeatureSets
        """
        for feature_set in feature_sets:
            try:
                feature_values = feature_set.to_feature_values()
                await FeatureValueTable.put_batch(feature_values)
            except Exception as e:
                logger.error(f"Error persisting features for {feature_set.evaluation_id}: {e}")


async def run_feature_computation(
    run_id: str,
    evaluations: Sequence[Evaluation],
    opportunities: Sequence[Opportunity],
    polygon_client: Optional[PolygonClient] = None,
    orchestrator: Optional[PipelineOrchestrator] = None,
    config: Optional[FeatureConfig] = None,
    catalyst_service: Optional[Any] = None,
    persist_features: bool = True,
    data_provider: Optional[Any] = None,
    as_of_date: Optional[date] = None,
    sector_map: Optional[dict[str, str]] = None,
    earnings_calendar_service: Optional[Any] = None,
) -> list[FeatureSet]:
    """Convenience function to run feature computation stage.

    Args:
        run_id: Pipeline run ID
        evaluations: Evaluations from Stage 3
        opportunities: Opportunities for linking
        polygon_client: Polygon API client (live mode)
        orchestrator: Pipeline orchestrator
        config: Feature configuration
        catalyst_service: CatalystDataService for earnings/SEC filing data
        persist_features: Whether to save to DynamoDB
        data_provider: Optional DataProvider for unified data access (backtest mode)
        as_of_date: Target date for backtesting (defaults to today)

    Returns:
        List of computed FeatureSets
    """
    stage = FeatureComputationStage(
        polygon_client=polygon_client,
        orchestrator=orchestrator,
        config=config,
        catalyst_service=catalyst_service,
        data_provider=data_provider,
        as_of_date=as_of_date,
        sector_map=sector_map,
        earnings_calendar_service=earnings_calendar_service,
    )
    return await stage.execute(
        run_id=run_id,
        evaluations=evaluations,
        opportunities=opportunities,
        persist_features=persist_features,
    )
