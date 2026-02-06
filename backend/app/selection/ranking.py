"""Ranking score calculations for contract selection.

Implements the ranking formula from Section 12.3 of OSS_Complete_Requirements.md:

rank_score = (0.40 * liquidity_score) + 
             (0.35 * delta_closeness_score) + 
             (0.25 * spread_tightness_score)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class RankingScores:
    """Individual score components for a contract."""

    liquidity_score: float
    delta_closeness_score: float
    spread_tightness_score: float
    rank_score: float

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary."""
        return {
            "liquidity_score": round(self.liquidity_score, 2),
            "delta_closeness_score": round(self.delta_closeness_score, 2),
            "spread_tightness_score": round(self.spread_tightness_score, 2),
            "rank_score": round(self.rank_score, 2),
        }


class RankingCalculator:
    """Calculates ranking scores for contract selection.
    
    Per Section 12.3:
    - Liquidity Score: configurable weight (default 0.40)
    - Delta Closeness Score: configurable weight (default 0.35)
    - Spread Tightness Score: configurable weight (default 0.25)
    """

    def __init__(
        self,
        target_delta_call: float = 0.45,
        target_delta_put: float = -0.45,
        weight_liquidity: float = 0.40,
        weight_delta: float = 0.35,
        weight_spread: float = 0.25,
    ) -> None:
        """Initialize the ranking calculator.
        
        Args:
            target_delta_call: Target delta for calls (default 0.45)
            target_delta_put: Target delta for puts (default -0.45)
            weight_liquidity: Weight for liquidity score (default 0.40)
            weight_delta: Weight for delta closeness score (default 0.35)
            weight_spread: Weight for spread tightness score (default 0.25)
        """
        self._target_delta_call = target_delta_call
        self._target_delta_put = target_delta_put
        self._weight_liquidity = weight_liquidity
        self._weight_delta = weight_delta
        self._weight_spread = weight_spread

    def calculate_rank_score(
        self,
        open_interest: int,
        volume: int,
        delta: float,
        spread_pct: float,
        is_call: bool,
    ) -> RankingScores:
        """Calculate the ranking score for a contract.
        
        Args:
            open_interest: Contract open interest
            volume: Today's volume
            delta: Contract delta
            spread_pct: Bid-ask spread percentage
            is_call: True if call, False if put
            
        Returns:
            RankingScores with individual components and final score
        """
        liquidity = self._calculate_liquidity_score(open_interest, volume)
        delta_closeness = self._calculate_delta_closeness_score(delta, is_call)
        spread_tightness = self._calculate_spread_tightness_score(spread_pct)

        rank_score = (
            self._weight_liquidity * liquidity +
            self._weight_delta * delta_closeness +
            self._weight_spread * spread_tightness
        )

        return RankingScores(
            liquidity_score=liquidity,
            delta_closeness_score=delta_closeness,
            spread_tightness_score=spread_tightness,
            rank_score=rank_score,
        )

    def _calculate_liquidity_score(
        self,
        open_interest: int,
        volume: int,
    ) -> float:
        """Calculate liquidity score (0-100).
        
        Per Section 12.3:
        oi_component = MIN(LOG10(open_interest) / LOG10(10000), 1) * 50
        vol_component = MIN(LOG10(volume + 1) / LOG10(1000), 1) * 50
        liquidity_score = oi_component + vol_component
        
        Args:
            open_interest: Contract open interest
            volume: Today's volume
            
        Returns:
            Liquidity score (0-100)
        """
        # Avoid log(0)
        oi = max(open_interest, 1)
        vol = max(volume, 0) + 1  # +1 to handle zero volume

        # OI component: scales from 0-50 based on log scale up to 10000
        oi_component = min(math.log10(oi) / math.log10(10000), 1.0) * 50

        # Volume component: scales from 0-50 based on log scale up to 1000
        vol_component = min(math.log10(vol) / math.log10(1000), 1.0) * 50

        return oi_component + vol_component

    def _calculate_delta_closeness_score(
        self,
        delta: float,
        is_call: bool,
    ) -> float:
        """Calculate delta closeness score (0-100).
        
        Per Section 12.3:
        target_delta = 0.45 (for CALL) or -0.45 (for PUT)
        delta_distance = ABS(delta - target_delta)
        max_distance = 0.30
        delta_closeness_score = (1 - MIN(delta_distance / max_distance, 1)) * 100
        
        Args:
            delta: Contract delta
            is_call: True if call, False if put
            
        Returns:
            Delta closeness score (0-100)
        """
        target = self._target_delta_call if is_call else self._target_delta_put
        max_distance = 0.30

        delta_distance = abs(delta - target)
        score = (1 - min(delta_distance / max_distance, 1.0)) * 100

        return score

    def _calculate_spread_tightness_score(
        self,
        spread_pct: float,
    ) -> float:
        """Calculate spread tightness score (0-100).
        
        Per Section 12.3:
        spread_tightness_score = (1 - MIN(spread_pct / 10, 1)) * 100
        
        Args:
            spread_pct: Bid-ask spread as percentage
            
        Returns:
            Spread tightness score (0-100)
        """
        # 10% is the max spread in selection filters
        score = (1 - min(spread_pct / 10.0, 1.0)) * 100
        return score
