"""Category F: Catalyst Features.

Computes earnings proximity and SEC filing detection.
Per Section 13.2 of OSS_Complete_Requirements.md.

NOTE: These features are currently stubbed as they require external data
sources (earnings calendar, SEC filing metadata) that are not yet integrated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CatalystFeatures:
    """Category F catalyst features."""
    
    days_to_earnings: Optional[int] = None  # Days until next earnings (null if unknown)
    recent_sec_filing: bool = False  # 1 if 8-K/10-Q/10-K in last 10 trading days


def compute_catalyst_features(
    days_to_earnings: Optional[int] = None,
    recent_sec_filing: bool = False,
) -> CatalystFeatures:
    """Compute catalyst features.
    
    Currently a passthrough as these features require external data.
    When Polygon's corporate events API is integrated, this will
    calculate actual values.
    
    Args:
        days_to_earnings: Days to next earnings (from external source)
        recent_sec_filing: Whether there was a recent SEC filing (from external source)
        
    Returns:
        CatalystFeatures dataclass
    """
    # TODO: Integrate with Polygon corporate events API when available
    # For now, return provided values or defaults
    
    return CatalystFeatures(
        days_to_earnings=days_to_earnings,
        recent_sec_filing=recent_sec_filing,
    )
