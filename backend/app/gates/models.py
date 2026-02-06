"""Gate models and data structures.

Contains the GateContext dataclass that holds all inputs needed
for gate evaluation, and GateEvaluation for tracking results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.core.schemas import (
    Evaluation,
    GateConfig,
    GateResult,
    Opportunity,
    ScannerType,
)
from app.features.models import FeatureSet


@dataclass
class GateContext:
    """Context for gate evaluation containing all required inputs.
    
    Built from Evaluation, FeatureSet, and Opportunity to provide
    a unified interface for all gate checks.
    """
    
    # Identification
    evaluation_id: str
    underlying_ticker: str
    option_ticker: str
    option_type: str  # "CALL" or "PUT"
    
    # Contract basics
    dte: int
    strike: float
    underlying_price: float
    
    # Pricing
    bid: float
    ask: float
    mid: float
    spread_pct: float
    
    # Greeks
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float
    
    # Liquidity
    open_interest: int
    volume: int
    
    # Calculated values from evaluation builder
    time_adjusted_feasibility: float
    
    # Feature values (may be None if FeatureSet not available)
    iv_percentile: Optional[float] = None
    theta_pct: Optional[float] = None  # abs(theta) / mid * 100
    
    # Scanner context
    scanner_triggers: list[str] = field(default_factory=list)
    volume_ratio: Optional[float] = None  # From breakout scanner metrics
    
    @classmethod
    def from_evaluation_and_features(
        cls,
        evaluation: Evaluation,
        feature_set: Optional[FeatureSet] = None,
        opportunity: Optional[Opportunity] = None,
    ) -> GateContext:
        """Build GateContext from evaluation, features, and opportunity.
        
        Args:
            evaluation: Evaluation record from Stage 3
            feature_set: FeatureSet from Stage 4 (optional)
            opportunity: Opportunity with scanner triggers (optional)
            
        Returns:
            GateContext with all values populated
        """
        # Extract scanner triggers and volume ratio from opportunity
        scanner_triggers: list[str] = []
        volume_ratio: Optional[float] = None
        
        if opportunity:
            for trigger in opportunity.scanner_triggers:
                scanner_triggers.append(trigger.scanner_type)
                # Extract volume_ratio from breakout/breakdown scanner metrics
                if trigger.scanner_type in (
                    ScannerType.BREAKOUT.value,
                    ScannerType.BREAKDOWN.value,
                    "BREAKOUT",
                    "BREAKDOWN",
                ):
                    volume_ratio = trigger.metrics.get("volume_ratio")
        
        # Calculate theta_pct from evaluation data
        theta_pct = None
        if evaluation.mid > 0:
            theta_pct = abs(evaluation.theta) / evaluation.mid * 100
        
        # Get IV percentile from feature set if available
        iv_percentile = None
        if feature_set and feature_set.iv_percentile is not None:
            iv_percentile = feature_set.iv_percentile
        
        return cls(
            evaluation_id=evaluation.evaluation_id,
            underlying_ticker=evaluation.underlying_ticker,
            option_ticker=evaluation.option_ticker,
            option_type=evaluation.option_type,
            dte=evaluation.dte,
            strike=evaluation.strike,
            underlying_price=evaluation.underlying_price,
            bid=evaluation.bid,
            ask=evaluation.ask,
            mid=evaluation.mid,
            spread_pct=evaluation.spread_pct,
            delta=evaluation.delta,
            gamma=evaluation.gamma,
            theta=evaluation.theta,
            vega=evaluation.vega,
            iv=evaluation.iv,
            open_interest=evaluation.open_interest,
            volume=evaluation.volume,
            time_adjusted_feasibility=evaluation.time_adjusted_feasibility,
            iv_percentile=iv_percentile,
            theta_pct=theta_pct,
            scanner_triggers=scanner_triggers,
            volume_ratio=volume_ratio,
        )


@dataclass
class GateEvaluation:
    """Result of evaluating all gates for a single evaluation.
    
    Aggregates individual GateResults and provides summary statistics.
    """
    
    evaluation_id: str
    gate_results: list[GateResult] = field(default_factory=list)
    
    @property
    def all_passed(self) -> bool:
        """Check if all enabled gates passed."""
        return all(
            result.passed 
            for result in self.gate_results 
            if result.enabled
        )
    
    @property
    def failed_gates(self) -> list[str]:
        """Get list of failed gate IDs."""
        return [
            result.gate_id
            for result in self.gate_results
            if result.enabled and not result.passed
        ]
    
    @property
    def failed_reason_codes(self) -> list[str]:
        """Get reason codes for all failed gates."""
        return [
            result.reason_code
            for result in self.gate_results
            if result.enabled and not result.passed
        ]
    
    @property
    def passed_count(self) -> int:
        """Count of passed gates."""
        return sum(1 for r in self.gate_results if r.enabled and r.passed)
    
    @property
    def failed_count(self) -> int:
        """Count of failed gates."""
        return sum(1 for r in self.gate_results if r.enabled and not r.passed)
    
    @property
    def skipped_count(self) -> int:
        """Count of disabled/skipped gates."""
        return sum(1 for r in self.gate_results if not r.enabled)
    
    def get_result(self, gate_id: str) -> Optional[GateResult]:
        """Get result for a specific gate."""
        for result in self.gate_results:
            if result.gate_id == gate_id:
                return result
        return None
