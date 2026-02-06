"""Category C: Volatility Features.

Computes IV percentile, IV regime, and related volatility metrics.
Per Section 13.2 and 13.3 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from app.core.schemas import IVHistory, IVRegime, FeatureConfig

logger = logging.getLogger(__name__)


@dataclass
class VolatilityFeatures:
    """Category C volatility features."""
    
    rv20: Optional[float] = None  # From underlying features
    iv: float = 0.0  # From contract
    iv_rv_ratio: Optional[float] = None
    iv_percentile: Optional[float] = None  # 0-100
    iv_10d_change: Optional[float] = None  # percent
    iv_regime: IVRegime = IVRegime.IV_NEUTRAL_REGIME


def calculate_iv_percentile(
    current_iv: float,
    iv_history: Sequence[IVHistory],
    min_days: int = 20,
) -> Optional[float]:
    """Calculate IV percentile from historical data.
    
    IV Percentile = (count of days where historical_iv < current_iv) / total_days * 100
    
    Args:
        current_iv: Current ATM IV value
        iv_history: Historical IV records (most recent first)
        min_days: Minimum days required for valid percentile
        
    Returns:
        Percentile (0-100) or None if insufficient data
    """
    if len(iv_history) < min_days:
        logger.debug(f"Insufficient IV history: {len(iv_history)} days < {min_days} required")
        return None
    
    historical_ivs = [h.atm_iv for h in iv_history if h.atm_iv is not None]
    
    if len(historical_ivs) < min_days:
        return None
    
    # Count how many historical values are below current IV
    below_count = sum(1 for iv in historical_ivs if iv < current_iv)
    percentile = (below_count / len(historical_ivs)) * 100
    
    return round(percentile, 2)


def calculate_iv_10d_change(iv_history: Sequence[IVHistory]) -> Optional[float]:
    """Calculate 10-day IV change percentage.
    
    Args:
        iv_history: Historical IV records (most recent first)
        
    Returns:
        Percentage change from 10 days ago, or None if insufficient data
    """
    if len(iv_history) < 10:
        return None
    
    current_iv = iv_history[0].atm_iv
    iv_10d_ago = iv_history[9].atm_iv if len(iv_history) > 9 else iv_history[-1].atm_iv
    
    if current_iv is None or iv_10d_ago is None or iv_10d_ago == 0:
        return None
    
    change_pct = ((current_iv - iv_10d_ago) / iv_10d_ago) * 100
    return round(change_pct, 2)


def classify_iv_regime(
    iv_percentile: Optional[float],
    days_to_earnings: Optional[int],
    iv_10d_change: Optional[float],
    config: Optional[FeatureConfig] = None,
) -> IVRegime:
    """Classify IV regime based on percentile, catalyst, and trend.
    
    Per Section 13.3 of requirements:
    
    1. Pre-catalyst checks (earnings within 14 days)
    2. Trending checks (iv_10d_change > 10 or < -10)
    3. Percentile-based regimes
    
    Args:
        iv_percentile: IV percentile (0-100) or None
        days_to_earnings: Days until next earnings, or None if unknown
        iv_10d_change: 10-day IV change percentage, or None
        config: Feature configuration for thresholds
        
    Returns:
        IVRegime enum value
    """
    # Use defaults if no config provided
    low_threshold = config.iv_regime_low_percentile if config else 30
    high_threshold = config.iv_regime_high_percentile if config else 70
    trending_threshold = config.iv_trending_change_threshold if config else 10.0
    
    # 1. Pre-catalyst checks
    if days_to_earnings is not None and days_to_earnings <= 14:
        if iv_percentile is not None and iv_percentile < 50:
            return IVRegime.IV_COMPRESSED_PRE_CATALYST
        return IVRegime.IV_ELEVATED_PRE_CATALYST
    
    # 2. Trending checks (require iv_10d_change data)
    if iv_10d_change is not None:
        if iv_10d_change > trending_threshold:
            return IVRegime.IV_TRENDING_UP
        elif iv_10d_change < -trending_threshold:
            return IVRegime.IV_TRENDING_DOWN
    
    # 3. Percentile-based regimes (require iv_percentile data)
    if iv_percentile is not None:
        if iv_percentile < low_threshold:
            return IVRegime.IV_LOW_REGIME
        elif iv_percentile > high_threshold:
            return IVRegime.IV_HIGH_REGIME
    
    # Default
    return IVRegime.IV_NEUTRAL_REGIME


def compute_volatility_features(
    iv: float,
    rv20: Optional[float],
    iv_history: Optional[Sequence[IVHistory]],
    days_to_earnings: Optional[int],
    config: Optional[FeatureConfig] = None,
) -> VolatilityFeatures:
    """Compute all volatility features.
    
    Args:
        iv: Current implied volatility from contract
        rv20: 20-day realized volatility from underlying
        iv_history: Historical IV records for percentile calculation
        days_to_earnings: Days to next earnings (for regime classification)
        config: Feature configuration
        
    Returns:
        VolatilityFeatures dataclass
    """
    # IV/RV ratio
    iv_rv_ratio = None
    if rv20 is not None and rv20 > 0:
        iv_rv_ratio = iv / rv20
    
    # IV percentile
    iv_percentile = None
    if iv_history:
        min_days = config.iv_percentile_min_days if config else 20
        iv_percentile = calculate_iv_percentile(iv, iv_history, min_days)
    
    # IV 10-day change
    iv_10d_change = None
    if iv_history:
        iv_10d_change = calculate_iv_10d_change(iv_history)
    
    # IV regime classification
    iv_regime = classify_iv_regime(
        iv_percentile=iv_percentile,
        days_to_earnings=days_to_earnings,
        iv_10d_change=iv_10d_change,
        config=config,
    )
    
    return VolatilityFeatures(
        rv20=rv20,
        iv=iv,
        iv_rv_ratio=iv_rv_ratio,
        iv_percentile=iv_percentile,
        iv_10d_change=iv_10d_change,
        iv_regime=iv_regime,
    )
