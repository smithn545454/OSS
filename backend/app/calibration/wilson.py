"""Wilson score interval for binomial proportions.

Used by v5 dual-conviction scoring to express the uncertainty in a
historical archetype's success rate (HR200 hit rate, win rate, etc.).
We use the Wilson lower bound as the conviction input rather than the
point estimate so that small-sample archetypes are penalized — protecting
against in-sample overfit.

The Wilson interval has better small-sample behavior than the normal
approximation and never produces nonsensical bounds (e.g., negative
probabilities or > 1).

Reference: Wilson, E. B. (1927). "Probable inference, the law of
succession, and statistical inference". JASA 22(158), 209-212.
"""

from __future__ import annotations

import math

# Z-scores for common confidence levels (two-sided).
_Z_BY_CONFIDENCE: dict[float, float] = {
    0.80: 1.2816,
    0.90: 1.6449,
    0.95: 1.9600,
    0.975: 2.2414,
    0.99: 2.5758,
}


def wilson_ci(
    successes: int,
    n: int,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Wilson score confidence interval for a binomial proportion.

    Args:
        successes: Number of observed successes (e.g., HR200 trades).
        n: Total number of trials (cohort size).
        confidence: Two-sided confidence level. Defaults to 0.95.

    Returns:
        ``(point_estimate, lower_bound, upper_bound)`` — all in [0, 1].
        When ``n == 0`` returns ``(0.0, 0.0, 0.0)`` rather than dividing
        by zero. When ``successes > n`` raises ValueError.

    Examples:
        >>> point, lo, hi = wilson_ci(20, 100)
        >>> round(point, 4)
        0.2
        >>> round(lo, 4)
        0.131
        >>> round(hi, 4)
        0.2917

        >>> # Tiny sample → wide interval
        >>> point, lo, hi = wilson_ci(2, 5)
        >>> round(lo, 3) < 0.1 and round(hi, 3) > 0.7
        True

        >>> # Empty cohort
        >>> wilson_ci(0, 0)
        (0.0, 0.0, 0.0)
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    if successes < 0:
        raise ValueError(f"successes must be non-negative, got {successes}")
    if successes > n:
        raise ValueError(f"successes ({successes}) cannot exceed n ({n})")
    if n == 0:
        return (0.0, 0.0, 0.0)

    z = _z_for_confidence(confidence)
    p_hat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n)) / denom

    lower = max(0.0, center - half)
    upper = min(1.0, center + half)
    return (p_hat, lower, upper)


def _z_for_confidence(confidence: float) -> float:
    """Return the two-sided z-score for a given confidence level.

    Falls back to the closest tabulated value if a non-standard confidence
    is requested. We avoid scipy to keep the Lambda cold-start light.
    """
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if confidence in _Z_BY_CONFIDENCE:
        return _Z_BY_CONFIDENCE[confidence]
    # Pick the closest tabulated value.
    closest = min(_Z_BY_CONFIDENCE.keys(), key=lambda c: abs(c - confidence))
    return _Z_BY_CONFIDENCE[closest]
