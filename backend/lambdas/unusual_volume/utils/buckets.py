"""Moneyness and DTE bucket calculators.

Used to categorize options contracts for bucket-level volume averaging
when contract-specific history is insufficient.

Moneyness Buckets (based on % from ATM):
- DEEP_ITM: < -15% (calls) or > +15% (puts)
- ITM: -15% to -5% (calls) or +5% to +15% (puts)
- ATM: -5% to +5%
- OTM: +5% to +15% (calls) or -5% to -15% (puts)
- DEEP_OTM: > +15% (calls) or < -15% (puts)

DTE Buckets:
- A: 0-7 DTE (weekly)
- B: 8-21 DTE (2-3 weeks)
- C: 22-45 DTE (monthly)
- D: 46-90 DTE (quarterly)
- X: 91+ DTE (LEAPS/long-dated, excluded from scanning)
"""

from __future__ import annotations

from typing import Literal


# Type definitions
MoneynessBucket = Literal["DEEP_ITM", "ITM", "ATM", "OTM", "DEEP_OTM"]
DTEBucket = Literal["A", "B", "C", "D", "X"]


# Bucket boundaries
MONEYNESS_BUCKET_BOUNDS = {
    "DEEP_ITM": (-float("inf"), -15.0),  # < -15%
    "ITM": (-15.0, -5.0),                # -15% to -5%
    "ATM": (-5.0, 5.0),                  # -5% to +5%
    "OTM": (5.0, 15.0),                  # +5% to +15%
    "DEEP_OTM": (15.0, float("inf")),    # > +15%
}

DTE_BUCKET_BOUNDS = {
    "A": (0, 7),     # Weekly
    "B": (8, 21),    # 2-3 weeks
    "C": (22, 45),   # Monthly
    "D": (46, 90),   # Quarterly
    "X": (91, 9999), # LEAPS (excluded)
}


def get_moneyness_bucket(
    strike: float,
    underlying_price: float,
    option_type: str,
) -> MoneynessBucket:
    """Calculate the moneyness bucket for an option.

    Moneyness is calculated as:
        (strike - underlying_price) / underlying_price * 100

    For CALL options:
        - Negative moneyness = In The Money (strike < price)
        - Positive moneyness = Out of The Money (strike > price)

    For PUT options:
        - Positive moneyness = In The Money (strike > price)
        - Negative moneyness = Out of The Money (strike < price)

    The bucket assignment inverts for puts to maintain intuitive naming.

    Args:
        strike: Strike price of the option
        underlying_price: Current price of the underlying asset
        option_type: "CALL" or "PUT"

    Returns:
        Moneyness bucket category.

    Examples:
        >>> get_moneyness_bucket(200.0, 220.0, "CALL")  # -9.1%, ITM call
        'ITM'

        >>> get_moneyness_bucket(200.0, 220.0, "PUT")   # -9.1% becomes +9.1% for put
        'OTM'

        >>> get_moneyness_bucket(200.0, 195.0, "CALL")  # +2.6%, ATM
        'ATM'
    """
    if underlying_price <= 0:
        return "ATM"  # Fallback for invalid price

    # Calculate raw moneyness percentage
    moneyness_pct = ((strike - underlying_price) / underlying_price) * 100

    # Invert for puts (positive moneyness = ITM for puts)
    if option_type.upper() == "PUT":
        moneyness_pct = -moneyness_pct

    # Determine bucket
    if moneyness_pct < -15.0:
        return "DEEP_ITM"
    elif moneyness_pct < -5.0:
        return "ITM"
    elif moneyness_pct <= 5.0:
        return "ATM"
    elif moneyness_pct <= 15.0:
        return "OTM"
    else:
        return "DEEP_OTM"


def get_dte_bucket(dte: int) -> DTEBucket:
    """Calculate the DTE bucket for an option.

    Args:
        dte: Days to expiration

    Returns:
        DTE bucket category.

    Examples:
        >>> get_dte_bucket(5)
        'A'

        >>> get_dte_bucket(14)
        'B'

        >>> get_dte_bucket(30)
        'C'

        >>> get_dte_bucket(60)
        'D'

        >>> get_dte_bucket(180)
        'X'
    """
    if dte < 0:
        return "A"  # Expired options assigned to A for error handling

    if dte <= 7:
        return "A"
    elif dte <= 21:
        return "B"
    elif dte <= 45:
        return "C"
    elif dte <= 90:
        return "D"
    else:
        return "X"


def is_scannable_dte(dte: int) -> bool:
    """Check if a contract is within scannable DTE range.

    Contracts in bucket X (91+ DTE) are excluded from scanning
    as they typically have lower volume and less short-term relevance.

    Args:
        dte: Days to expiration

    Returns:
        True if contract should be included in scanning.
    """
    return get_dte_bucket(dte) != "X"


def build_bucket_key(
    underlying: str,
    option_type: str,
    moneyness_bucket: MoneynessBucket,
    dte_bucket: DTEBucket,
) -> str:
    """Build a bucket key for volume stats lookup.

    Args:
        underlying: Underlying ticker
        option_type: "CALL" or "PUT"
        moneyness_bucket: Moneyness bucket category
        dte_bucket: DTE bucket category

    Returns:
        Bucket key in format: BUCKET#{underlying}#{option_type}#{moneyness}#{dte}

    Example:
        >>> build_bucket_key("AAPL", "CALL", "ATM", "C")
        'BUCKET#AAPL#CALL#ATM#C'
    """
    return f"BUCKET#{underlying}#{option_type.upper()}#{moneyness_bucket}#{dte_bucket}"
