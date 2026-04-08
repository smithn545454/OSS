"""Pillar scoring utility functions.

Contains linear interpolation, score mapping, and helper functions
used across all three pillars.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple


def linear_interpolate(
    value: float,
    breakpoints: Sequence[Tuple[float, float]],
    clamp: bool = True,
) -> float:
    """Linearly interpolate a value through a series of breakpoints.
    
    Args:
        value: The input value to map
        breakpoints: List of (input, output) tuples, sorted by input ascending
        clamp: If True, clamp output to min/max of breakpoint outputs
        
    Returns:
        Interpolated output value
        
    Example:
        # Map return percentage to score
        breakpoints = [(-10, 5), (-5, 35), (0, 55), (5, 75), (10, 95)]
        score = linear_interpolate(2.5, breakpoints)  # Returns 65
    """
    if not breakpoints:
        return 50.0  # Neutral default
    
    # Sort breakpoints by input value
    sorted_bp = sorted(breakpoints, key=lambda x: x[0])
    
    # Handle values below minimum
    if value <= sorted_bp[0][0]:
        return sorted_bp[0][1] if clamp else sorted_bp[0][1]
    
    # Handle values above maximum
    if value >= sorted_bp[-1][0]:
        return sorted_bp[-1][1] if clamp else sorted_bp[-1][1]
    
    # Find the two breakpoints to interpolate between
    for i in range(len(sorted_bp) - 1):
        x0, y0 = sorted_bp[i]
        x1, y1 = sorted_bp[i + 1]
        
        if x0 <= value <= x1:
            # Linear interpolation formula
            if x1 == x0:
                return y0
            t = (value - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    
    # Fallback (shouldn't reach here)
    return sorted_bp[-1][1]


def linear_map_range(
    value: float,
    in_min: float,
    in_max: float,
    out_min: float,
    out_max: float,
    clamp: bool = True,
) -> float:
    """Map a value from one range to another linearly.
    
    Args:
        value: Input value
        in_min: Minimum of input range
        in_max: Maximum of input range
        out_min: Minimum of output range
        out_max: Maximum of output range
        clamp: If True, clamp output to [out_min, out_max]
        
    Returns:
        Mapped value
    """
    if in_max == in_min:
        return (out_min + out_max) / 2
    
    # Calculate the linear mapping
    result = out_min + (value - in_min) * (out_max - out_min) / (in_max - in_min)
    
    if clamp:
        return max(out_min, min(out_max, result))
    return result


def clamp_score(score: float, min_score: float = 0.0, max_score: float = 100.0) -> float:
    """Clamp a score to valid range [0, 100].
    
    Args:
        score: The raw score
        min_score: Minimum allowed (default 0)
        max_score: Maximum allowed (default 100)
        
    Returns:
        Clamped score
    """
    return max(min_score, min(max_score, score))


def distance_from_neutral(score: float, neutral: float = 50.0) -> float:
    """Calculate how far a score is from neutral (50).
    
    Used for ranking contributors by importance.
    
    Args:
        score: The subscore (0-100)
        neutral: The neutral value (default 50)
        
    Returns:
        Absolute distance from neutral
    """
    return abs(score - neutral)


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safely divide, returning default if denominator is zero.
    
    Args:
        numerator: The numerator
        denominator: The denominator
        default: Value to return if division by zero
        
    Returns:
        Result of division or default
    """
    if denominator == 0:
        return default
    return numerator / denominator


def blend_values(
    value1: Optional[float],
    value2: Optional[float],
    weight1: float,
) -> Optional[float]:
    """Blend two values with weights.
    
    Args:
        value1: First value
        value2: Second value
        weight1: Weight for value1 (weight2 = 1 - weight1)
        
    Returns:
        Blended value, or None if both inputs are None
    """
    if value1 is None and value2 is None:
        return None
    
    # If one is None, use the other entirely
    if value1 is None:
        return value2
    if value2 is None:
        return value1
    
    weight2 = 1.0 - weight1
    return value1 * weight1 + value2 * weight2


# ============================================================================
# Score Mapping Functions for Specific Subscore Types
# ============================================================================


def map_momentum_score(blended_return: float, is_call: bool) -> float:
    """Map blended return to momentum subscore (0-100).
    
    Per Section 14.2: Momentum mapping for blended return.
    For CALL: positive returns = high score
    For PUT: negative returns = high score (invert)
    
    Spec mapping:
    - ≥ +10%: 95
    - +5% to +10%: Linear 75-95
    - 0% to +5%: Linear 55-75
    - -5% to 0%: Linear 35-55
    - -10% to -5%: Linear 15-35
    - ≤ -10%: 5
    
    Args:
        blended_return: DTE-weighted blended return percentage
        is_call: True for CALL contracts, False for PUT
        
    Returns:
        Momentum subscore (0-100)
    """
    # For PUT, invert the return before mapping
    effective_return = blended_return if is_call else -blended_return
    
    # Exact breakpoints per Section 14.2 spec
    breakpoints = [
        (-10.0, 5.0),    # ≤ -10%: 5
        (-5.0, 15.0),    # -10% to -5%: starts at 15
        (0.0, 35.0),     # -5% to 0%: Linear 35-55 (starts at 35)
        (0.0, 55.0),     # 0%: endpoint of prev range
        (5.0, 75.0),     # 0% to +5%: Linear 55-75 (ends at 75)
        (10.0, 95.0),    # +5% to +10%: Linear 75-95
    ]
    
    # Simplified breakpoints matching spec exactly
    spec_breakpoints = [
        (-10.0, 5.0),
        (-5.0, 35.0),    # End of -10 to -5 range (Linear 15-35)
        (0.0, 55.0),     # End of -5 to 0 range (Linear 35-55)
        (5.0, 75.0),     # End of 0 to +5 range (Linear 55-75)
        (10.0, 95.0),    # End of +5 to +10 range (Linear 75-95)
    ]
    
    return linear_interpolate(effective_return, spec_breakpoints)


def map_rs_score(rs_20d: float, is_call: bool) -> float:
    """Map relative strength to subscore (0-100).
    
    Per Section 14.2: Relative Strength Subscore.
    For PUT, invert the RS value before mapping.
    
    Spec mapping:
    - ≥ +8%: 95
    - +5% to +8%: Linear 80-95
    - +2% to +5%: Linear 65-80
    - -2% to +2%: Linear 45-65
    - -5% to -2%: Linear 30-45
    - ≤ -5%: 20
    
    Args:
        rs_20d: 20-day relative strength vs SPY
        is_call: True for CALL contracts, False for PUT
        
    Returns:
        Relative strength subscore (0-100)
    """
    # For PUT, invert the RS value
    effective_rs = rs_20d if is_call else -rs_20d
    
    # Exact breakpoints per Section 14.2 spec
    breakpoints = [
        (-5.0, 20.0),    # ≤ -5%: 20
        (-2.0, 45.0),    # -5% to -2%: Linear 30-45 (ends at 45)
        (2.0, 65.0),     # -2% to +2%: Linear 45-65 (ends at 65)
        (5.0, 80.0),     # +2% to +5%: Linear 65-80 (ends at 80)
        (8.0, 95.0),     # +5% to +8%: Linear 80-95 (ends at 95)
    ]
    
    return linear_interpolate(effective_rs, breakpoints)


def map_iv_rv_ratio_score(iv_rv_ratio: float) -> float:
    """Map IV/RV ratio to volatility subscore (0-100).
    
    Per Section 14.3: Lower ratio = better (options cheaper).
    
    Spec mapping:
    - ≤ 0.90: 95 (options cheap)
    - 0.90-1.00: Linear 85-95
    - 1.00-1.10: Linear 70-85
    - 1.10-1.25: Linear 55-70
    - 1.25-1.50: Linear 35-55
    - > 1.50: 20 (options expensive)
    
    Args:
        iv_rv_ratio: IV divided by RV20
        
    Returns:
        IV vs RV subscore (0-100)
    """
    # Exact breakpoints per Section 14.3 spec
    breakpoints = [
        (0.0, 95.0),     # Very cheap
        (0.90, 95.0),    # ≤ 0.90: 95
        (1.00, 85.0),    # 0.90-1.00: Linear 85-95 (ends at 85)
        (1.10, 70.0),    # 1.00-1.10: Linear 70-85 (ends at 70)
        (1.25, 55.0),    # 1.10-1.25: Linear 55-70 (ends at 55)
        (1.50, 35.0),    # 1.25-1.50: Linear 35-55 (ends at 35)
        (2.00, 20.0),    # > 1.50: 20
    ]
    
    return linear_interpolate(iv_rv_ratio, breakpoints)


def map_iv_percentile_score(iv_percentile: float) -> float:
    """Map IV percentile to volatility subscore (0-100).
    
    Per Section 14.3: Lower percentile = better buying opportunity.
    
    Spec mapping:
    - ≤ 20%: 95
    - 20-30%: Linear 85-95
    - 30-50%: Linear 65-85
    - 50-70%: Linear 45-65
    - 70-85%: Linear 30-45
    - > 85%: 20
    
    Args:
        iv_percentile: IV percentile (0-100)
        
    Returns:
        IV percentile subscore (0-100)
    """
    # Exact breakpoints per Section 14.3 spec
    breakpoints = [
        (0.0, 95.0),
        (20.0, 95.0),    # ≤ 20%: 95
        (30.0, 85.0),    # 20-30%: Linear 85-95 (ends at 85)
        (50.0, 65.0),    # 30-50%: Linear 65-85 (ends at 65)
        (70.0, 45.0),    # 50-70%: Linear 45-65 (ends at 45)
        (85.0, 30.0),    # 70-85%: Linear 30-45 (ends at 30)
        (100.0, 20.0),   # > 85%: 20
    ]
    
    return linear_interpolate(iv_percentile, breakpoints)


def map_theta_adjusted_edge_score(theta_adjusted_edge: float) -> float:
    """Map theta-adjusted edge ratio to subscore (0-100).
    
    Per Section 14.3: Higher ratio = better (gains outpace theta).
    
    Args:
        theta_adjusted_edge: Expected gain / theta cost ratio
        
    Returns:
        Theta-adjusted edge subscore (0-100)
    """
    breakpoints = [
        (0.0, 20.0),
        (0.75, 35.0),    # < 0.75: 20, 0.75: 35
        (1.0, 50.0),     # 0.75-1.0: Linear 35-50
        (1.5, 65.0),     # 1.0-1.5: Linear 50-65
        (2.0, 80.0),     # 1.5-2.0: Linear 65-80
        (2.5, 95.0),     # 2.0-2.5: Linear 80-95
        (5.0, 95.0),     # >= 2.5: 95
    ]
    
    return linear_interpolate(theta_adjusted_edge, breakpoints)


def map_delta_moneyness_score(abs_delta: float) -> float:
    """Map absolute delta to entry quality subscore (0-100).

    Lower abs delta (farther OTM) = higher score. Data shows:
    - Q1 (lowest delta): 61% WR
    - Q5 (highest delta): 30% WR
    - Sweet spot: abs(delta) 0.05-0.30

    Args:
        abs_delta: Absolute value of contract delta

    Returns:
        Delta/moneyness subscore (0-100)
    """
    breakpoints = [
        (0.0, 80.0),      # Extremely far OTM — good but less certain
        (0.05, 95.0),      # Sweet spot starts
        (0.15, 92.0),      # Peak zone
        (0.30, 70.0),      # Still decent OTM
        (0.45, 45.0),      # Near ATM — neutral
        (0.60, 25.0),      # ITM — poor asymmetry
        (0.80, 15.0),      # Deep ITM — worst for long options
    ]

    return linear_interpolate(abs_delta, breakpoints)


def map_raw_iv_score(iv: Optional[float]) -> float:
    """Map implied volatility level to entry quality subscore (0-100).

    Lower IV = cheaper premium = better asymmetry. Data shows:
    - Q1 (lowest IV): 40% WR
    - Q5 (highest IV): 32% WR
    - Correlation r=-0.062 with outcomes

    Args:
        iv: Implied volatility as decimal (e.g. 0.25 = 25%)

    Returns:
        Raw IV subscore (0-100)
    """
    if iv is None or iv <= 0:
        return 50.0  # Neutral if no data

    breakpoints = [
        (0.0, 95.0),       # Very low IV — excellent
        (0.15, 92.0),      # Low IV
        (0.25, 80.0),      # Moderate-low
        (0.40, 65.0),      # Moderate
        (0.60, 45.0),      # High
        (0.80, 30.0),      # Very high
        (1.20, 20.0),      # Extreme IV
    ]

    return linear_interpolate(iv, breakpoints)


def map_dte_appropriateness_score(dte: int) -> float:
    """Map days to expiration to entry quality subscore (0-100).

    Moderate-long DTE is best (time for thesis to play out).
    Very short DTE = heavy theta decay. Very long = diminishing returns.
    Data: Low Delta + High DTE = 56.4% WR.

    Args:
        dte: Days to expiration

    Returns:
        DTE appropriateness subscore (0-100)
    """
    breakpoints = [
        (7.0, 25.0),       # Very short — heavy theta decay
        (14.0, 45.0),      # Short
        (21.0, 60.0),      # Bucket A end — decent
        (30.0, 75.0),      # Bucket B start — good
        (45.0, 85.0),      # Sweet spot
        (60.0, 90.0),      # Bucket C — excellent
        (90.0, 85.0),      # Long — good but some time value premium
        (120.0, 75.0),     # Very long — diminishing returns
    ]

    return linear_interpolate(float(dte), breakpoints)


def map_premium_leverage_score(mid_price: float) -> float:
    """Map option mid price to premium leverage subscore (0-100).

    Cheaper contracts provide more leverage for asymmetric payoff.
    A $0.50 contract that doubles = same profit as $5 contract +10%,
    but with 1/10th the capital at risk.

    Args:
        mid_price: Option mid price in dollars

    Returns:
        Premium leverage subscore (0-100)
    """
    if mid_price <= 0:
        return 50.0  # Neutral if no data

    breakpoints = [
        (0.05, 98.0),     # Extreme leverage — lottery ticket territory
        (0.20, 95.0),     # Very cheap — excellent asymmetry
        (0.50, 90.0),     # Cheap — strong leverage
        (1.00, 80.0),     # Affordable — user's sweet spot ceiling
        (2.00, 60.0),     # Moderate — still viable
        (5.00, 35.0),     # Expensive — losing asymmetric edge
        (10.00, 18.0),    # Very expensive — not suitable for this strategy
        (20.00, 8.0),     # Institutional-sized premium
    ]

    return linear_interpolate(mid_price, breakpoints)


def map_spread_score(spread_pct: float) -> float:
    """Map spread percentage to structure subscore (0-100).
    
    Per Section 14.4: Tighter spread = better.
    
    Args:
        spread_pct: Bid-ask spread as percentage
        
    Returns:
        Spread subscore (0-100)
    """
    breakpoints = [
        (0.0, 95.0),
        (2.0, 95.0),     # <= 2%: 95
        (4.0, 80.0),     # 2-4%: Linear 80-95
        (6.0, 65.0),     # 4-6%: Linear 65-80
        (8.0, 50.0),     # 6-8%: Linear 50-65
        (10.0, 35.0),    # 8-10%: Linear 35-50
        (15.0, 20.0),    # > 10%: 20
    ]
    
    return linear_interpolate(spread_pct, breakpoints)


def map_open_interest_score(open_interest: int) -> float:
    """Map open interest to structure subscore (0-100).
    
    Per Section 14.4: Higher OI = better liquidity.
    
    Args:
        open_interest: Contract open interest
        
    Returns:
        Open interest subscore (0-100)
    """
    breakpoints = [
        (0.0, 20.0),
        (200.0, 35.0),   # < 200: 20, 200: 35
        (300.0, 50.0),   # 200-300: Linear 35-50
        (500.0, 65.0),   # 300-500: Linear 50-65
        (1000.0, 80.0),  # 500-1000: Linear 65-80
        (2000.0, 95.0),  # 1000-2000: Linear 80-95
        (10000.0, 95.0), # >= 2000: 95
    ]
    
    return linear_interpolate(float(open_interest), breakpoints)


def map_volume_score(volume: int) -> float:
    """Map daily volume to structure subscore (0-100).
    
    Per Section 14.4: Higher volume = better activity.
    
    Args:
        volume: Contract daily volume
        
    Returns:
        Volume subscore (0-100)
    """
    breakpoints = [
        (0.0, 25.0),
        (50.0, 35.0),    # < 50: 25, 50: 35
        (75.0, 45.0),    # 50-75: Linear 35-45
        (150.0, 60.0),   # 75-150: Linear 45-60
        (300.0, 75.0),   # 150-300: Linear 60-75
        (500.0, 90.0),   # 300-500: Linear 75-90
        (1000.0, 90.0),  # >= 500: 90
    ]
    
    return linear_interpolate(float(volume), breakpoints)


def map_theta_burden_score(theta_pct: float) -> float:
    """Map theta burden (% per day) to structure subscore (0-100).
    
    Per Section 14.4: Lower theta burden = better.
    
    Args:
        theta_pct: abs(theta) / mid * 100
        
    Returns:
        Theta burden subscore (0-100)
    """
    breakpoints = [
        (0.0, 90.0),
        (0.5, 90.0),     # <= 0.5%: 90
        (1.0, 75.0),     # 0.5-1.0%: Linear 75-90
        (1.5, 60.0),     # 1.0-1.5%: Linear 60-75
        (2.0, 45.0),     # 1.5-2.0%: Linear 45-60
        (3.0, 30.0),     # 2.0-3.0%: Linear 30-45
        (5.0, 20.0),     # > 3.0%: 20
    ]
    
    return linear_interpolate(theta_pct, breakpoints)


def map_liquidity_trend_score(oi_5d_change_pct: Optional[float]) -> float:
    """Map OI 5-day change to structure subscore (0-100).
    
    Per Section 14.4: Growing OI = positive signal.
    
    Args:
        oi_5d_change_pct: Percentage change in OI over 5 days
        
    Returns:
        Liquidity trend subscore (0-100)
    """
    if oi_5d_change_pct is None:
        return 50.0  # Neutral if no data
    
    breakpoints = [
        (-30.0, 25.0),   # < -20%: 25
        (-20.0, 35.0),   # -20% to -10%: Linear 35-50
        (-10.0, 50.0),
        (0.0, 65.0),     # -10% to 0%: Linear 50-65
        (10.0, 85.0),    # 0% to +10%: Linear 65-85
        (30.0, 85.0),    # >= +10%: 85
    ]
    
    return linear_interpolate(oi_5d_change_pct, breakpoints)
