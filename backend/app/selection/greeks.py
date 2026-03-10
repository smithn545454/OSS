"""Black-Scholes greeks computation.

Fallback greeks calculator for when Polygon does not provide greeks/IV
in the snapshot response. Uses only Python stdlib (math module).

Polygon greeks always take priority — this module is only called when
greeks are missing from the API response.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.045


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def _d2(d1: float, sigma: float, T: float) -> float:
    return d1 - sigma * math.sqrt(T)


def bs_price(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str
) -> float:
    """Black-Scholes option price.

    Args:
        S: Underlying price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility
        option_type: "call" or "put"

    Returns:
        Theoretical option price
    """
    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(d1, sigma, T)

    if option_type == "call":
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes vega (sensitivity to volatility)."""
    d1 = _d1(S, K, T, r, sigma)
    return S * norm_pdf(d1) * math.sqrt(T)


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = RISK_FREE_RATE,
    option_type: str = "call",
    max_iterations: int = 50,
    tolerance: float = 1e-6,
) -> Optional[float]:
    """Solve for implied volatility using Newton-Raphson.

    Args:
        market_price: Observed option mid price
        S: Underlying price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        option_type: "call" or "put"
        max_iterations: Max Newton-Raphson iterations
        tolerance: Convergence tolerance

    Returns:
        Implied volatility or None if solver fails
    """
    if market_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None

    # Check intrinsic value bounds
    if option_type == "call":
        intrinsic = max(S - K * math.exp(-r * T), 0)
    else:
        intrinsic = max(K * math.exp(-r * T) - S, 0)

    if market_price < intrinsic * 0.95:
        return None  # Price below intrinsic — bad data

    sigma = 0.3  # Initial guess

    for _ in range(max_iterations):
        try:
            price = bs_price(S, K, T, r, sigma, option_type)
            vega = bs_vega(S, K, T, r, sigma)

            if vega < 1e-10:
                break  # Vega too small — can't converge

            diff = price - market_price
            if abs(diff) < tolerance:
                return sigma

            sigma = sigma - diff / vega
            sigma = max(0.01, min(sigma, 5.0))  # Clamp to [1%, 500%]
        except (ValueError, OverflowError, ZeroDivisionError):
            break

    return None


def compute_greeks(
    S: float,
    K: float,
    T: float,
    option_type: str,
    market_price: float,
    r: float = RISK_FREE_RATE,
) -> Optional[dict[str, float]]:
    """Compute full greeks from market price using Black-Scholes.

    This is a FALLBACK for when Polygon does not provide greeks.
    Polygon greeks always take priority.

    Args:
        S: Underlying price
        K: Strike price
        T: Time to expiry in years
        option_type: "call" or "put"
        market_price: Option mid price
        r: Risk-free rate

    Returns:
        Dict with iv, delta, gamma, theta, vega or None if computation fails
    """
    if S <= 0 or K <= 0 or T <= 0 or market_price <= 0:
        return None

    sigma = implied_volatility(market_price, S, K, T, r, option_type)
    if sigma is None:
        return None

    try:
        d1 = _d1(S, K, T, r, sigma)
        d2 = _d2(d1, sigma, T)
        sqrt_T = math.sqrt(T)

        # Delta
        if option_type == "call":
            delta = norm_cdf(d1)
        else:
            delta = norm_cdf(d1) - 1.0

        # Gamma (same for calls and puts)
        gamma = norm_pdf(d1) / (S * sigma * sqrt_T)

        # Theta (per day)
        first_term = -(S * norm_pdf(d1) * sigma) / (2.0 * sqrt_T)
        if option_type == "call":
            theta = (first_term - r * K * math.exp(-r * T) * norm_cdf(d2)) / 365.0
        else:
            theta = (first_term + r * K * math.exp(-r * T) * norm_cdf(-d2)) / 365.0

        # Vega (per 1% move in IV)
        vega = S * norm_pdf(d1) * sqrt_T / 100.0

        return {
            "iv": sigma,
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
        }
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
