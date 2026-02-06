"""OCC Option Symbol Parser.

Parses OCC-compliant option symbols to extract underlying, expiration,
option type, and strike price.

OCC Symbol Format:
    AAPL250117C00200000
    |  ||    ||   |
    |  ||    ||   +-- Strike price in dollars * 1000 (8 digits)
    |  ||    |+------ Option type (C=Call, P=Put)
    |  ||    +------- Expiration YYMMDD (6 digits)
    |  |+------------ Root symbol (varies, 1-6 chars typically)
    +---------------- Underlying ticker

Examples:
    AAPL250117C00200000  -> AAPL, 2025-01-17, Call, $200.00
    SPY240315P00450500   -> SPY, 2024-03-15, Put, $450.50
    TSLA250620C01200000  -> TSLA, 2025-06-20, Call, $1200.00
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional


# OCC symbol regex pattern
# Captures: underlying (1-6 chars), date (6 digits), type (C/P), strike (8 digits)
OCC_PATTERN = re.compile(
    r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$"
)


@dataclass(frozen=True)
class OCCSymbol:
    """Parsed OCC option symbol components."""

    underlying: str
    expiration: date
    option_type: str  # "CALL" or "PUT"
    strike: float
    raw_symbol: str

    @property
    def expiration_str(self) -> str:
        """Return expiration as YYYY-MM-DD string."""
        return self.expiration.isoformat()

    @property
    def dte(self) -> int:
        """Calculate days to expiration from today."""
        return (self.expiration - date.today()).days

    @property
    def moneyness_pct(self) -> Optional[float]:
        """Placeholder - requires underlying price to calculate."""
        return None

    def is_call(self) -> bool:
        """Check if this is a call option."""
        return self.option_type == "CALL"

    def is_put(self) -> bool:
        """Check if this is a put option."""
        return self.option_type == "PUT"


def parse_occ_symbol(symbol: str) -> Optional[OCCSymbol]:
    """Parse an OCC option symbol into its components.

    Args:
        symbol: OCC-compliant option symbol (e.g., "AAPL250117C00200000")
                Can optionally have "O:" prefix which will be stripped.

    Returns:
        OCCSymbol with parsed components, or None if parsing fails.

    Examples:
        >>> parse_occ_symbol("AAPL250117C00200000")
        OCCSymbol(underlying='AAPL', expiration=date(2025, 1, 17),
                  option_type='CALL', strike=200.0, raw_symbol='AAPL250117C00200000')

        >>> parse_occ_symbol("O:SPY240315P00450500")
        OCCSymbol(underlying='SPY', expiration=date(2024, 3, 15),
                  option_type='PUT', strike=450.5, raw_symbol='SPY240315P00450500')
    """
    # Strip optional "O:" prefix (Polygon format)
    clean_symbol = symbol[2:] if symbol.startswith("O:") else symbol

    # Match against OCC pattern
    match = OCC_PATTERN.match(clean_symbol)
    if not match:
        return None

    underlying, date_str, type_char, strike_str = match.groups()

    # Parse expiration date (YYMMDD)
    try:
        year = 2000 + int(date_str[0:2])  # Assumes 20XX
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        expiration = date(year, month, day)
    except (ValueError, IndexError):
        return None

    # Parse option type
    option_type = "CALL" if type_char == "C" else "PUT"

    # Parse strike (8 digits = dollars * 1000)
    # e.g., 00200000 = $200.00, 00450500 = $450.50
    try:
        strike = int(strike_str) / 1000.0
    except ValueError:
        return None

    return OCCSymbol(
        underlying=underlying,
        expiration=expiration,
        option_type=option_type,
        strike=strike,
        raw_symbol=clean_symbol,
    )


def build_occ_symbol(
    underlying: str,
    expiration: date,
    option_type: str,
    strike: float,
) -> str:
    """Build an OCC-compliant option symbol from components.

    Args:
        underlying: Underlying ticker symbol (e.g., "AAPL")
        expiration: Option expiration date
        option_type: "CALL" or "PUT"
        strike: Strike price in dollars

    Returns:
        OCC-compliant symbol string.

    Examples:
        >>> build_occ_symbol("AAPL", date(2025, 1, 17), "CALL", 200.0)
        'AAPL250117C00200000'
    """
    # Format date as YYMMDD
    date_str = expiration.strftime("%y%m%d")

    # Option type character
    type_char = "C" if option_type.upper() == "CALL" else "P"

    # Strike as 8-digit integer (dollars * 1000)
    strike_int = int(round(strike * 1000))
    strike_str = f"{strike_int:08d}"

    return f"{underlying}{date_str}{type_char}{strike_str}"
