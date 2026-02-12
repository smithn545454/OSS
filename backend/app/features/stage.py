"""Stage 4: Feature Computation Pipeline Stage.

Integrates feature computation into the pipeline orchestration.
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
    PipelineStage,
)
from app.core.pipeline import PipelineOrchestrator
from app.db.tables import IVHistoryTable, OIHistoryTable, FeatureValueTable
from app.services.polygon import PolygonClient
from app.features.calculator import FeatureComputer
from app.features.models import FeatureSet

logger = logging.getLogger(__name__)


class FeatureComputationStage:
    """Stage 4: Feature Computation.
    
    Takes Evaluations from Stage 3 and computes all features needed
    for pillar scoring in Stage 5.
    """
    
    def __init__(
        self,
        polygon_client: PolygonClient,
        orchestrator: PipelineOrchestrator,
        config: Optional[FeatureConfig] = None,
    ) -> None:
        """Initialize the feature computation stage.
        
        Args:
            polygon_client: Polygon API client for market data
            orchestrator: Pipeline orchestrator for event tracking
            config: Feature computation configuration
        """
        self._polygon = polygon_client
        self._orchestrator = orchestrator
        self._config = config or FeatureConfig()
        self._computer = FeatureComputer(polygon_client, config=config)
    
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
        """Fetch IV history for all tickers.
        
        Args:
            tickers: List of ticker symbols
            
        Returns:
            Dict mapping ticker to IV history records
        """
        iv_history_map: dict[str, list[IVHistory]] = {}
        
        for ticker in tickers:
            try:
                history = await IVHistoryTable.list_by_ticker(
                    ticker=ticker,
                    limit=self._config.iv_percentile_lookback_days,
                )
                if history:
                    iv_history_map[ticker] = history
            except Exception as e:
                logger.warning(f"Error fetching IV history for {ticker}: {e}")
        
        logger.debug(f"Fetched IV history for {len(iv_history_map)}/{len(tickers)} tickers")
        return iv_history_map
    
    async def _fetch_oi_history(
        self,
        option_tickers: list[str],
    ) -> dict[str, list[OIHistory]]:
        """Fetch OI history for all option contracts.
        
        Args:
            option_tickers: List of option ticker symbols
            
        Returns:
            Dict mapping option ticker to OI history records
        """
        oi_history_map: dict[str, list[OIHistory]] = {}
        
        for option_ticker in option_tickers:
            try:
                history = await OIHistoryTable.list_by_contract(
                    option_ticker=option_ticker,
                    limit=10,  # Need 5 days for 5-day change
                )
                if history:
                    oi_history_map[option_ticker] = history
            except Exception as e:
                logger.warning(f"Error fetching OI history for {option_ticker}: {e}")
        
        logger.debug(f"Fetched OI history for {len(oi_history_map)}/{len(option_tickers)} contracts")
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
    polygon_client: PolygonClient,
    orchestrator: PipelineOrchestrator,
    config: Optional[FeatureConfig] = None,
    persist_features: bool = True,
) -> list[FeatureSet]:
    """Convenience function to run feature computation stage.
    
    Args:
        run_id: Pipeline run ID
        evaluations: Evaluations from Stage 3
        opportunities: Opportunities for linking
        polygon_client: Polygon API client
        orchestrator: Pipeline orchestrator
        config: Feature configuration
        persist_features: Whether to save to DynamoDB
        
    Returns:
        List of computed FeatureSets
    """
    stage = FeatureComputationStage(
        polygon_client=polygon_client,
        orchestrator=orchestrator,
        config=config,
    )
    return await stage.execute(
        run_id=run_id,
        evaluations=evaluations,
        opportunities=opportunities,
        persist_features=persist_features,
    )
