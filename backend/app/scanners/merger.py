"""Opportunity Merger (Section 10.6).

Handles merging multiple scanner triggers for the same ticker into a single
Opportunity record.

Merge Rules:
1. Create ONE Opportunity record per ticker
2. Aggregate all scanner_triggers[] from each scanner
3. Combine direction hints:
   - If all hints agree → use that direction
   - If hints conflict → use NONE
   - If some NONE + some directional → use the directional hint
4. Priority score calculation:
   base_priority = MAX(individual_scanner_priorities)
   bonus = 15 × (number_of_additional_scanners)
   priority_score = MIN(base_priority + bonus, 100)

Scanner Base Priority Values:
- Breakout/Breakdown + Volume Confirmation: 75
- Compression → Expansion: 70
- Unusual Volume (volume_ratio > 3.0): 65
- Unusual Volume (volume_ratio 2.0-3.0): 55
- Cheap Options: 50
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from app.core.schemas import (
    DirectionHint,
    Opportunity,
    ScannerTrigger,
    ScannerType,
)
from app.scanners.base import ScanResult

logger = logging.getLogger(__name__)


def get_scanner_base_priority(trigger: ScannerTrigger) -> int:
    """Get the base priority for a scanner trigger.

    Args:
        trigger: The scanner trigger

    Returns:
        Base priority score (50-75)
    """
    scanner_type = trigger.scanner_type
    metrics = trigger.metrics

    if scanner_type == ScannerType.BREAKOUT or scanner_type == ScannerType.BREAKDOWN:
        # Check for volume confirmation
        volume_ratio = metrics.get("volume_ratio", 0)
        if volume_ratio >= 1.5:
            return 75
        return 65

    elif scanner_type == ScannerType.COMPRESSION_EXPANSION:
        return 70

    elif scanner_type == ScannerType.UNUSUAL_VOLUME:
        # Higher priority for very high volume ratio
        volume_ratio = metrics.get("volume_ratio", 0)
        if volume_ratio > 3.0:
            return 65
        return 55

    elif scanner_type == ScannerType.CHEAP_OPTIONS:
        return 50

    # Default fallback
    return 50


def get_direction_from_trigger(trigger: ScannerTrigger) -> DirectionHint:
    """Extract direction hint from a trigger.

    Determines direction based on scanner type and metrics.

    Args:
        trigger: The scanner trigger

    Returns:
        DirectionHint enum value
    """
    scanner_type = trigger.scanner_type
    metrics = trigger.metrics

    if scanner_type == ScannerType.BREAKOUT:
        return DirectionHint.CALL

    elif scanner_type == ScannerType.BREAKDOWN:
        return DirectionHint.PUT

    elif scanner_type == ScannerType.COMPRESSION_EXPANSION:
        direction = metrics.get("triggered_direction", "")
        if direction == "UP":
            return DirectionHint.CALL
        elif direction == "DOWN":
            return DirectionHint.PUT
        return DirectionHint.NONE

    elif scanner_type == ScannerType.UNUSUAL_VOLUME:
        call_put_ratio = metrics.get("call_put_volume_ratio", 1.0)
        if call_put_ratio >= 1.3:
            return DirectionHint.CALL
        elif call_put_ratio <= 0.7:
            return DirectionHint.PUT
        return DirectionHint.NONE

    elif scanner_type == ScannerType.CHEAP_OPTIONS:
        # Cheap options is non-directional unless the momentum filter ran and
        # resolved a direction (stored in metrics["triggered_direction"]).
        direction = metrics.get("triggered_direction", "")
        if direction == "UP":
            return DirectionHint.CALL
        elif direction == "DOWN":
            return DirectionHint.PUT
        return DirectionHint.NONE

    return DirectionHint.NONE


def resolve_direction_hints(hints: list[DirectionHint]) -> DirectionHint:
    """Resolve multiple direction hints into one.

    Rules:
    - If all hints agree → use that direction
    - If hints conflict → use NONE
    - If some NONE + some directional → use the directional hint

    Args:
        hints: List of DirectionHint values

    Returns:
        Resolved DirectionHint
    """
    if not hints:
        return DirectionHint.NONE

    # Filter out NONE values
    directional_hints = [h for h in hints if h != DirectionHint.NONE]

    if not directional_hints:
        # All hints were NONE
        return DirectionHint.NONE

    # Check if all directional hints agree
    unique_directions = set(directional_hints)

    if len(unique_directions) == 1:
        # All directional hints agree
        return directional_hints[0]
    else:
        # Hints conflict
        return DirectionHint.NONE


def calculate_priority_score(triggers: list[ScannerTrigger]) -> int:
    """Calculate priority score from multiple triggers.

    Formula:
    base_priority = MAX(individual_scanner_priorities)
    bonus = 15 × (number_of_additional_scanners)
    priority_score = MIN(base_priority + bonus, 100)

    Args:
        triggers: List of scanner triggers

    Returns:
        Priority score (0-100)
    """
    if not triggers:
        return 0

    # Get base priorities for all triggers
    priorities = [get_scanner_base_priority(t) for t in triggers]

    # Base is the maximum priority
    base_priority = max(priorities)

    # Bonus for additional scanners (triggers beyond the first)
    num_additional = len(triggers) - 1
    bonus = 15 * num_additional

    # Final score capped at 100
    priority_score = min(base_priority + bonus, 100)

    return priority_score


def merge_triggers_to_opportunity(
    ticker: str,
    triggers: list[ScannerTrigger],
    timestamp_utc: Optional[str] = None,
) -> Opportunity:
    """Merge multiple scanner triggers into a single Opportunity.

    Args:
        ticker: The underlying ticker symbol
        triggers: List of ScannerTrigger objects from different scanners
        timestamp_utc: Optional timestamp (defaults to now)

    Returns:
        Merged Opportunity record
    """
    if timestamp_utc is None:
        timestamp_utc = datetime.now(timezone.utc).isoformat()

    # Get direction hints from all triggers
    direction_hints = [get_direction_from_trigger(t) for t in triggers]
    resolved_direction = resolve_direction_hints(direction_hints)

    # Calculate priority score
    priority_score = calculate_priority_score(triggers)

    # Create opportunity
    opportunity = Opportunity(
        underlying_ticker=ticker,
        timestamp_utc=timestamp_utc,
        scanner_triggers=triggers,
        direction_hint=resolved_direction,
        priority_score=priority_score,
    )

    return opportunity


def merge_scan_results(
    results: list[ScanResult],
    timestamp_utc: Optional[str] = None,
) -> list[Opportunity]:
    """Merge scan results from multiple scanners into Opportunities.

    Groups triggers by ticker and creates one Opportunity per ticker.

    Args:
        results: List of ScanResult objects from all scanners
        timestamp_utc: Optional timestamp for all opportunities

    Returns:
        List of merged Opportunity records
    """
    if timestamp_utc is None:
        timestamp_utc = datetime.now(timezone.utc).isoformat()

    # Group triggers by ticker
    triggers_by_ticker: dict[str, list[ScannerTrigger]] = defaultdict(list)

    for result in results:
        if result.triggered and result.trigger is not None:
            triggers_by_ticker[result.ticker].append(result.trigger)

    # Create opportunities
    opportunities = []
    for ticker, triggers in triggers_by_ticker.items():
        opportunity = merge_triggers_to_opportunity(
            ticker=ticker,
            triggers=triggers,
            timestamp_utc=timestamp_utc,
        )
        opportunities.append(opportunity)

    # Sort by priority score (highest first)
    opportunities.sort(key=lambda o: o.priority_score, reverse=True)

    logger.info(
        f"Merged {len(results)} scan results into {len(opportunities)} opportunities"
    )

    return opportunities


class OpportunityMerger:
    """Service for merging scanner results into opportunities."""

    def __init__(self) -> None:
        """Initialize the merger."""
        self._timestamp: Optional[str] = None

    def set_timestamp(self, timestamp: str) -> None:
        """Set the timestamp for all opportunities in this merge batch.

        Args:
            timestamp: ISO 8601 timestamp string
        """
        self._timestamp = timestamp

    def merge(self, results: list[ScanResult]) -> list[Opportunity]:
        """Merge scan results into opportunities.

        Args:
            results: List of ScanResult objects from all scanners

        Returns:
            List of merged Opportunity records
        """
        return merge_scan_results(results, self._timestamp)

    def get_merge_stats(
        self,
        results: list[ScanResult],
        opportunities: list[Opportunity],
    ) -> dict[str, int]:
        """Get statistics about the merge operation.

        Args:
            results: Original scan results
            opportunities: Merged opportunities

        Returns:
            Dict with merge statistics
        """
        total_triggers = sum(1 for r in results if r.triggered)
        unique_tickers = len(opportunities)
        multi_scanner_opps = sum(
            1 for o in opportunities if len(o.scanner_triggers) > 1
        )

        # Count by direction
        call_hints = sum(
            1 for o in opportunities if o.direction_hint == DirectionHint.CALL
        )
        put_hints = sum(
            1 for o in opportunities if o.direction_hint == DirectionHint.PUT
        )
        none_hints = sum(
            1 for o in opportunities if o.direction_hint == DirectionHint.NONE
        )

        return {
            "total_scan_results": len(results),
            "total_triggers": total_triggers,
            "opportunities_created": unique_tickers,
            "multi_scanner_opportunities": multi_scanner_opps,
            "direction_call": call_hints,
            "direction_put": put_hints,
            "direction_none": none_hints,
        }
