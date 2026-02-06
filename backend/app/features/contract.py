"""Category D: Contract-Specific Features.

Computes theta-adjusted edge and other contract features.
Per Section 13.2 and 13.3 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass
from typing import Optional

from app.core.schemas import Evaluation

logger = logging.getLogger(__name__)


@dataclass
class ContractFeatures:
    """Category D contract-specific features."""
    
    mid: float
    spread_pct: float
    theta_pct: float  # abs(theta) / mid * 100
    breakeven_price: float
    required_move_pct: float
    expected_move_pct: float
    feasibility_ratio: float
    time_adjusted_feasibility: float
    theta_adjusted_edge: Optional[float] = None


def calculate_theta_pct(theta: float, mid: float) -> float:
    """Calculate daily theta as percentage of option price.
    
    Args:
        theta: Theta value (negative for long options)
        mid: Mid price of option
        
    Returns:
        Theta percentage (positive value)
    """
    if mid <= 0:
        return 0.0
    return abs(theta) / mid * 100


def calculate_theta_adjusted_edge(
    underlying_price: float,
    iv: float,
    delta: float,
    theta: float,
) -> Optional[float]:
    """Calculate theta-adjusted edge ratio.
    
    Per Section 13.3:
    
    daily_expected_move = underlying_price * (iv / sqrt(252))
    daily_theta_cost = abs(theta)
    daily_expected_gain = daily_expected_move * abs(delta)
    theta_adjusted_edge = daily_expected_gain / daily_theta_cost
    
    Interpretation:
    - > 1.0: Expected delta gains exceed theta costs
    - < 1.0: Theta decay likely to outpace gains
    - > 2.0: Strong edge
    
    Args:
        underlying_price: Current underlying price
        iv: Implied volatility (decimal, e.g., 0.25 for 25%)
        delta: Option delta
        theta: Option theta (negative for long options)
        
    Returns:
        Theta-adjusted edge ratio, or None if calculation not possible
    """
    if theta == 0 or iv <= 0 or underlying_price <= 0:
        return None
    
    # Daily expected move of underlying (1-sigma)
    daily_expected_move = underlying_price * (iv / math.sqrt(252))
    
    # Daily theta cost
    daily_theta_cost = abs(theta)
    
    # Daily expected gain from delta (if stock moves expected amount)
    daily_expected_gain = daily_expected_move * abs(delta)
    
    # Edge ratio
    if daily_theta_cost == 0:
        return None
        
    return daily_expected_gain / daily_theta_cost


def compute_contract_features(evaluation: Evaluation) -> ContractFeatures:
    """Compute contract-specific features from an Evaluation.
    
    Most contract features are already computed in the Evaluation,
    so we primarily extract them and calculate theta_pct and theta_adjusted_edge.
    
    Args:
        evaluation: Evaluation record from Stage 3
        
    Returns:
        ContractFeatures dataclass
    """
    # Calculate theta percentage
    theta_pct = calculate_theta_pct(evaluation.theta, evaluation.mid)
    
    # Calculate theta-adjusted edge
    theta_adjusted_edge = calculate_theta_adjusted_edge(
        underlying_price=evaluation.underlying_price,
        iv=evaluation.iv,
        delta=evaluation.delta,
        theta=evaluation.theta,
    )
    
    return ContractFeatures(
        mid=evaluation.mid,
        spread_pct=evaluation.spread_pct,
        theta_pct=theta_pct,
        breakeven_price=evaluation.breakeven_price,
        required_move_pct=evaluation.required_move_pct,
        expected_move_pct=evaluation.expected_move_pct,
        feasibility_ratio=evaluation.feasibility_ratio,
        time_adjusted_feasibility=evaluation.time_adjusted_feasibility,
        theta_adjusted_edge=theta_adjusted_edge,
    )
