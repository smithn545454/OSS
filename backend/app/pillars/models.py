"""Pillar scoring models and data structures.

Contains dataclasses for pillar scoring results, subscores,
and contributor tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.schemas import PillarId, PillarContributor, PillarScore


@dataclass
class Subscore:
    """Individual subscore within a pillar.
    
    Tracks the raw value, computed score, weight, and contribution
    to the final pillar score.
    """
    
    name: str
    raw_value: Any
    score: float  # 0-100
    weight: float  # 0.0-1.0
    
    @property
    def weighted_contribution(self) -> float:
        """Calculate weighted contribution to pillar score."""
        return self.score * self.weight
    
    @property
    def distance_from_neutral(self) -> float:
        """Calculate distance from neutral (50) for ranking."""
        return abs(self.score - 50.0)
    
    def to_contributor(self) -> PillarContributor:
        """Convert to PillarContributor schema."""
        return PillarContributor(
            feature_name=self.name,
            subscore=self.score,
            weight=self.weight,
            weighted_contribution=self.weighted_contribution,
            raw_value=self.raw_value,
            distance_from_neutral=self.distance_from_neutral,
        )


@dataclass
class PillarResult:
    """Result of pillar scoring computation.
    
    Contains the final score, all subscores, and generated tags.
    """
    
    pillar_id: PillarId
    evaluation_id: str
    score: float  # 0-100, the final weighted pillar score
    subscores: list[Subscore] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    
    @property
    def top_contributors(self) -> list[PillarContributor]:
        """Get all contributors sorted by distance from neutral (most impactful first)."""
        sorted_subscores = sorted(
            self.subscores,
            key=lambda s: s.distance_from_neutral,
            reverse=True,
        )
        return [s.to_contributor() for s in sorted_subscores]
    
    def to_pillar_score(self) -> PillarScore:
        """Convert to PillarScore schema for storage."""
        return PillarScore(
            evaluation_id=self.evaluation_id,
            pillar_id=self.pillar_id,
            score=int(round(self.score)),
            contributors=self.top_contributors,
            tags=self.tags,
        )


@dataclass
class ScoringContext:
    """Context for pillar scoring.
    
    Contains all the data needed to score an evaluation,
    including the evaluation itself, features, and opportunity.
    """
    
    evaluation_id: str
    underlying_ticker: str
    option_type: str  # "CALL" or "PUT"
    dte_bucket: str  # "A", "B", "C", or "D"
    
    # Scanner trigger info (for signal confirmation)
    scanner_triggers: list[str] = field(default_factory=list)
    direction_hint: str = "NONE"  # "CALL", "PUT", or "NONE"
    
    # Category A: Underlying Technical Features
    close: Optional[float] = None
    sma20: Optional[float] = None
    sma50: Optional[float] = None
    return_5d: Optional[float] = None
    return_20d: Optional[float] = None
    trend_aligned_bullish: bool = False
    trend_aligned_bearish: bool = False
    
    # Category B: Relative Strength Features
    rs_5d: Optional[float] = None
    rs_20d: Optional[float] = None
    
    # Category C: Volatility Features
    rv20: Optional[float] = None
    iv: float = 0.0
    iv_rv_ratio: Optional[float] = None
    iv_percentile: Optional[float] = None
    iv_regime: str = "IV_NEUTRAL_REGIME"
    
    # Category D: Contract Features
    mid: float = 0.0
    spread_pct: float = 0.0
    theta_pct: float = 0.0
    theta_adjusted_edge: Optional[float] = None
    delta: float = 0.0
    dte: int = 0
    
    # Category E: Liquidity Features
    open_interest: int = 0
    volume: int = 0
    oi_5d_change_pct: Optional[float] = None
    
    # Category F: Catalyst Features
    days_to_earnings: Optional[int] = None
    recent_sec_filing: bool = False

    # Category G: Technical Indicators
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    ema_alignment: Optional[str] = None
    rsi_14: Optional[float] = None
    macd_histogram: Optional[float] = None
    adx_14: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None
    obv_trend: Optional[str] = None
    
    @property
    def is_call(self) -> bool:
        """Check if this is a CALL contract."""
        return self.option_type == "CALL"
    
    @classmethod
    def from_position_and_features(
        cls,
        position: Any,
        feature_set: Any,
    ) -> "ScoringContext":
        """Create ScoringContext from a PaperPosition and FeatureSet.

        Used by the batch rescore job to avoid fetching Evaluation/Opportunity
        records — PaperPosition has all the contract data snapshotted at entry.

        Args:
            position: PaperPosition with denormalized evaluation fields.
            feature_set: FeatureSet reconstructed from FeatureValueTable.

        Returns:
            ScoringContext populated with all data.
        """
        scanner_triggers = position.scanner_list or []

        return cls(
            evaluation_id=position.evaluation_id,
            underlying_ticker=position.underlying_ticker or "",
            option_type=str(position.option_type or "CALL"),
            dte_bucket=str(position.dte_bucket or "B"),
            scanner_triggers=scanner_triggers,
            direction_hint="NONE",
            # Category A
            close=feature_set.close if feature_set else (position.entry_underlying_price or 0.0),
            sma20=feature_set.sma20 if feature_set else None,
            sma50=feature_set.sma50 if feature_set else None,
            return_5d=feature_set.return_5d if feature_set else None,
            return_20d=feature_set.return_20d if feature_set else None,
            trend_aligned_bullish=feature_set.trend_aligned_bullish if feature_set else False,
            trend_aligned_bearish=feature_set.trend_aligned_bearish if feature_set else False,
            # Category B
            rs_5d=feature_set.rs_5d if feature_set else None,
            rs_20d=feature_set.rs_20d if feature_set else None,
            # Category C
            rv20=feature_set.rv20 if feature_set else None,
            iv=feature_set.iv if feature_set else (position.entry_iv or 0.0),
            iv_rv_ratio=feature_set.iv_rv_ratio if feature_set else None,
            iv_percentile=feature_set.iv_percentile if feature_set else None,
            iv_regime=(
                str(feature_set.iv_regime) if feature_set and feature_set.iv_regime
                else "IV_NEUTRAL_REGIME"
            ),
            # Category D
            mid=feature_set.mid if feature_set else (position.entry_price or 0.0),
            spread_pct=feature_set.spread_pct if feature_set else (position.entry_spread_pct or 0.0),
            theta_pct=feature_set.theta_pct if feature_set else 0.0,
            theta_adjusted_edge=feature_set.theta_adjusted_edge if feature_set else None,
            delta=position.entry_delta or 0.0,
            dte=position.dte_at_entry or 0,
            # Category E
            open_interest=feature_set.open_interest if feature_set else (position.entry_open_interest or 0),
            volume=feature_set.volume if feature_set else (position.entry_volume or 0),
            oi_5d_change_pct=feature_set.oi_5d_change_pct if feature_set else None,
            # Category F
            days_to_earnings=feature_set.days_to_earnings if feature_set else None,
            recent_sec_filing=feature_set.recent_sec_filing if feature_set else False,
            # Category G
            ema_9=feature_set.ema_9 if feature_set else None,
            ema_21=feature_set.ema_21 if feature_set else None,
            ema_50=feature_set.ema_50 if feature_set else None,
            ema_200=feature_set.ema_200 if feature_set else None,
            ema_alignment=feature_set.ema_alignment if feature_set else None,
            rsi_14=feature_set.rsi_14 if feature_set else None,
            macd_histogram=feature_set.macd_histogram if feature_set else None,
            adx_14=feature_set.adx_14 if feature_set else None,
            plus_di=feature_set.plus_di if feature_set else None,
            minus_di=feature_set.minus_di if feature_set else None,
            obv_trend=feature_set.obv_trend if feature_set else None,
        )

    @classmethod
    def from_evaluation_and_features(
        cls,
        evaluation: Any,
        feature_set: Any,
        opportunity: Any,
    ) -> "ScoringContext":
        """Create ScoringContext from Evaluation, FeatureSet, and Opportunity.
        
        Args:
            evaluation: Evaluation record from Stage 3
            feature_set: FeatureSet from Stage 4
            opportunity: Opportunity record with scanner triggers
            
        Returns:
            ScoringContext populated with all data
        """
        # Extract scanner types from triggers
        scanner_triggers = []
        direction_hint = "NONE"
        
        if opportunity:
            for trigger in opportunity.scanner_triggers:
                scanner_triggers.append(trigger.scanner_type)
            direction_hint = opportunity.direction_hint
        
        return cls(
            evaluation_id=evaluation.evaluation_id,
            underlying_ticker=evaluation.underlying_ticker,
            option_type=evaluation.option_type,
            dte_bucket=evaluation.dte_bucket,
            scanner_triggers=scanner_triggers,
            direction_hint=str(direction_hint),
            # Category A
            close=feature_set.close if feature_set else evaluation.underlying_price,
            sma20=feature_set.sma20 if feature_set else None,
            sma50=feature_set.sma50 if feature_set else None,
            return_5d=feature_set.return_5d if feature_set else None,
            return_20d=feature_set.return_20d if feature_set else None,
            trend_aligned_bullish=feature_set.trend_aligned_bullish if feature_set else False,
            trend_aligned_bearish=feature_set.trend_aligned_bearish if feature_set else False,
            # Category B
            rs_5d=feature_set.rs_5d if feature_set else None,
            rs_20d=feature_set.rs_20d if feature_set else None,
            # Category C
            rv20=feature_set.rv20 if feature_set else None,
            iv=feature_set.iv if feature_set else evaluation.iv,
            iv_rv_ratio=feature_set.iv_rv_ratio if feature_set else None,
            iv_percentile=feature_set.iv_percentile if feature_set else None,
            iv_regime=str(feature_set.iv_regime) if feature_set and feature_set.iv_regime else "IV_NEUTRAL_REGIME",
            # Category D
            mid=feature_set.mid if feature_set else evaluation.mid,
            spread_pct=feature_set.spread_pct if feature_set else evaluation.spread_pct,
            theta_pct=feature_set.theta_pct if feature_set else 0.0,
            theta_adjusted_edge=feature_set.theta_adjusted_edge if feature_set else None,
            delta=evaluation.delta,
            dte=evaluation.dte,
            # Category E
            open_interest=feature_set.open_interest if feature_set else evaluation.open_interest,
            volume=feature_set.volume if feature_set else evaluation.volume,
            oi_5d_change_pct=feature_set.oi_5d_change_pct if feature_set else None,
            # Category F
            days_to_earnings=feature_set.days_to_earnings if feature_set else None,
            recent_sec_filing=feature_set.recent_sec_filing if feature_set else False,
            # Category G
            ema_9=feature_set.ema_9 if feature_set else None,
            ema_21=feature_set.ema_21 if feature_set else None,
            ema_50=feature_set.ema_50 if feature_set else None,
            ema_200=feature_set.ema_200 if feature_set else None,
            ema_alignment=feature_set.ema_alignment if feature_set else None,
            rsi_14=feature_set.rsi_14 if feature_set else None,
            macd_histogram=feature_set.macd_histogram if feature_set else None,
            adx_14=feature_set.adx_14 if feature_set else None,
            plus_di=feature_set.plus_di if feature_set else None,
            minus_di=feature_set.minus_di if feature_set else None,
            obv_trend=feature_set.obv_trend if feature_set else None,
        )
