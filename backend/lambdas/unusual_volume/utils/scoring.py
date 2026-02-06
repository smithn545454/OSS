"""Priority Score Calculator for Unusual Volume Candidates.

Calculates a weighted priority score (0-100) used to rank unusual volume
candidates for downstream processing. Higher scores indicate more
significant or actionable signals.

Score Components:
- Volume Ratio (40%): How many times above average is current volume
- OI Change (25%): Magnitude of open interest change
- Trigger Count (15%): Number of trigger conditions met
- Spread (10%): Bid-ask spread quality (lower is better)
- Contract Liquidity (10%): Absolute volume and OI levels
"""

from __future__ import annotations

import math
from typing import Optional


# Scoring weights (must sum to 1.0)
WEIGHT_VOLUME_RATIO = 0.40
WEIGHT_OI_CHANGE = 0.25
WEIGHT_TRIGGER_COUNT = 0.15
WEIGHT_SPREAD = 0.10
WEIGHT_LIQUIDITY = 0.10


# Scoring parameters
MAX_VOLUME_RATIO_FOR_SCORE = 20.0  # Cap volume ratio contribution
MAX_OI_CHANGE_FOR_SCORE = 100.0   # Cap OI change contribution (%)
MAX_TRIGGERS = 4                   # Maximum trigger conditions possible
IDEAL_SPREAD_PCT = 1.0            # Spread <= 1% gets full score
MAX_SPREAD_PCT = 10.0             # Spread >= 10% gets zero score

# Liquidity thresholds
MIN_VOLUME_FOR_SCORE = 100
MAX_VOLUME_FOR_SCORE = 10000
MIN_OI_FOR_SCORE = 500
MAX_OI_FOR_SCORE = 20000


def calculate_priority_score(
    volume_ratio: float,
    oi_change_pct: float,
    trigger_count: int,
    spread_pct: float,
    today_volume: int,
    today_oi: int,
    volume_source: str = "CONTRACT_SPECIFIC",
) -> float:
    """Calculate the priority score for an unusual volume candidate.

    Args:
        volume_ratio: Today's volume / 20-day average volume
        oi_change_pct: (Today's OI - Prior OI) / Prior OI * 100
        trigger_count: Number of trigger conditions met (1-4)
        spread_pct: Bid-ask spread as percentage of mid price
        today_volume: Today's trading volume
        today_oi: Today's open interest
        volume_source: "CONTRACT_SPECIFIC" or "BUCKET_FALLBACK"

    Returns:
        Priority score between 0 and 100.

    Examples:
        >>> calculate_priority_score(
        ...     volume_ratio=5.0,
        ...     oi_change_pct=25.0,
        ...     trigger_count=2,
        ...     spread_pct=2.5,
        ...     today_volume=5000,
        ...     today_oi=10000,
        ... )
        62.5  # Approximate

    Notes:
        - Bucket fallback sources receive a 10% penalty as the baseline
          is less reliable.
        - All component scores are normalized to 0-1 before weighting.
    """
    # Component 1: Volume Ratio Score (0-1)
    volume_ratio_score = _score_volume_ratio(volume_ratio)

    # Component 2: OI Change Score (0-1)
    oi_change_score = _score_oi_change(oi_change_pct)

    # Component 3: Trigger Count Score (0-1)
    trigger_score = _score_trigger_count(trigger_count)

    # Component 4: Spread Score (0-1, inverted - lower spread = higher score)
    spread_score = _score_spread(spread_pct)

    # Component 5: Liquidity Score (0-1)
    liquidity_score = _score_liquidity(today_volume, today_oi)

    # Weighted combination
    raw_score = (
        WEIGHT_VOLUME_RATIO * volume_ratio_score
        + WEIGHT_OI_CHANGE * oi_change_score
        + WEIGHT_TRIGGER_COUNT * trigger_score
        + WEIGHT_SPREAD * spread_score
        + WEIGHT_LIQUIDITY * liquidity_score
    )

    # Apply bucket fallback penalty
    if volume_source == "BUCKET_FALLBACK":
        raw_score *= 0.90  # 10% penalty

    # Convert to 0-100 scale and clamp
    final_score = max(0.0, min(100.0, raw_score * 100))

    return round(final_score, 2)


def _score_volume_ratio(volume_ratio: float) -> float:
    """Score volume ratio component (0-1).

    Uses logarithmic scaling to reward high ratios with diminishing returns.
    A ratio of 2x = ~0.3, 5x = ~0.7, 10x = ~0.9, 20x+ = 1.0
    """
    if volume_ratio <= 1.0:
        return 0.0

    # Log scale: log2(ratio) / log2(max_ratio)
    log_ratio = math.log2(volume_ratio)
    log_max = math.log2(MAX_VOLUME_RATIO_FOR_SCORE)

    return min(1.0, log_ratio / log_max)


def _score_oi_change(oi_change_pct: float) -> float:
    """Score OI change component (0-1).

    Uses absolute value - both large positive and negative changes
    are significant signals.
    """
    abs_change = abs(oi_change_pct)

    if abs_change <= 0:
        return 0.0

    # Linear scaling with cap
    return min(1.0, abs_change / MAX_OI_CHANGE_FOR_SCORE)


def _score_trigger_count(trigger_count: int) -> float:
    """Score trigger count component (0-1).

    Linear scaling: 1 trigger = 0.25, 2 = 0.5, 3 = 0.75, 4 = 1.0
    """
    if trigger_count <= 0:
        return 0.0

    return min(1.0, trigger_count / MAX_TRIGGERS)


def _score_spread(spread_pct: float) -> float:
    """Score spread component (0-1, inverted).

    Lower spreads = higher scores.
    <= 1% spread = 1.0
    >= 10% spread = 0.0
    Linear interpolation between.
    """
    if spread_pct <= IDEAL_SPREAD_PCT:
        return 1.0
    if spread_pct >= MAX_SPREAD_PCT:
        return 0.0

    # Linear interpolation
    return 1.0 - (spread_pct - IDEAL_SPREAD_PCT) / (MAX_SPREAD_PCT - IDEAL_SPREAD_PCT)


def _score_liquidity(volume: int, oi: int) -> float:
    """Score liquidity component (0-1).

    Combines volume and OI scores with equal weight.
    """
    volume_score = _normalize_log(volume, MIN_VOLUME_FOR_SCORE, MAX_VOLUME_FOR_SCORE)
    oi_score = _normalize_log(oi, MIN_OI_FOR_SCORE, MAX_OI_FOR_SCORE)

    return (volume_score + oi_score) / 2


def _normalize_log(value: int, min_val: int, max_val: int) -> float:
    """Normalize a value to 0-1 using logarithmic scaling."""
    if value <= min_val:
        return 0.0
    if value >= max_val:
        return 1.0

    # Log scale
    log_val = math.log10(value)
    log_min = math.log10(min_val)
    log_max = math.log10(max_val)

    return (log_val - log_min) / (log_max - log_min)


def get_trigger_reasons(
    volume_ratio: float,
    oi_change_pct: float,
    volume_ratio_threshold: float = 2.0,
    oi_increase_threshold_pct: float = 15.0,
    oi_decrease_threshold_pct: float = -20.0,
) -> list[str]:
    """Determine which trigger conditions were met.

    Args:
        volume_ratio: Today's volume / 20-day average
        oi_change_pct: OI change percentage
        volume_ratio_threshold: Minimum volume ratio for trigger
        oi_increase_threshold_pct: Minimum OI increase % for trigger
        oi_decrease_threshold_pct: Maximum OI decrease % for trigger

    Returns:
        List of trigger reason codes.

    Trigger Codes:
        - VOL_SPIKE: Volume ratio exceeds threshold
        - OI_INCREASE: OI increased by threshold percentage
        - OI_DECREASE: OI decreased by threshold percentage
        - VOL_OI_COMBO: Both volume spike and OI change present
    """
    reasons = []

    vol_triggered = volume_ratio >= volume_ratio_threshold
    oi_increase_triggered = oi_change_pct >= oi_increase_threshold_pct
    oi_decrease_triggered = oi_change_pct <= oi_decrease_threshold_pct
    oi_triggered = oi_increase_triggered or oi_decrease_triggered

    if vol_triggered:
        reasons.append("VOL_SPIKE")

    if oi_increase_triggered:
        reasons.append("OI_INCREASE")
    elif oi_decrease_triggered:
        reasons.append("OI_DECREASE")

    if vol_triggered and oi_triggered:
        reasons.append("VOL_OI_COMBO")

    return reasons
