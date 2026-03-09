"""Batch position updater for fan-out Lambda workers.

Processes a bounded chunk of position IDs, updating prices and checking exits.
Used by the paper_trading_update coordinator/worker pattern in main.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from app.core.schemas import PaperPosition, TrackingConfig
from app.db.tables import PaperPositionTable
from app.paper_trading.metrics_aggregator import MetricsAggregator
from app.paper_trading.position_manager import (
    _update_position_from_chain,
    extract_underlying_from_option_ticker,
)
from app.services.polygon import PolygonClient

logger = logging.getLogger(__name__)


@dataclass
class ChunkUpdateResult:
    """Result of updating a chunk of positions."""

    positions_processed: int = 0
    exits_triggered: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)


async def update_position_chunk(
    position_ids: list[str],
    polygon_client: PolygonClient,
    config: Optional[TrackingConfig] = None,
) -> ChunkUpdateResult:
    """Update a chunk of positions by their IDs.

    Args:
        position_ids: List of position_id values to process
        polygon_client: Polygon client for fetching prices
        config: Optional tracking configuration

    Returns:
        ChunkUpdateResult with counts
    """
    result = ChunkUpdateResult()

    # Fetch the actual position objects
    positions: list[PaperPosition] = []
    open_positions = await PaperPositionTable.list_open()

    # Build lookup by position_id
    pos_map = {p.position_id: p for p in open_positions}
    for pid in position_ids:
        pos = pos_map.get(pid)
        if pos:
            positions.append(pos)
        else:
            result.errors += 1
            result.error_details.append(f"Position not found: {pid}")

    if not positions:
        logger.info("No positions found for chunk")
        return result

    # Group by underlying for efficient API calls
    by_underlying: dict[str, list[PaperPosition]] = {}
    for pos in positions:
        underlying = extract_underlying_from_option_ticker(pos.option_ticker)
        by_underlying.setdefault(underlying, []).append(pos)

    # Process each underlying group
    for underlying, group in by_underlying.items():
        try:
            chain = await polygon_client.get_options_chain(underlying)

            # Build lookup
            contract_data: dict[str, dict] = {}
            for contract in chain:
                ticker = contract.get("ticker") or contract.get("details", {}).get(
                    "ticker"
                )
                if ticker:
                    contract_data[ticker] = contract

            for pos in group:
                try:
                    update_result = await _update_position_from_chain(
                        pos, contract_data, config
                    )
                    result.positions_processed += 1

                    if update_result.exit_triggered:
                        result.exits_triggered += 1

                    # Write daily equity point (dollar P&L change, not percentage)
                    from datetime import datetime, timezone

                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    # Dollar change = price delta * 100 (option multiplier); quantity=1
                    dollar_change = (
                        (update_result.current_price - update_result.previous_price) * 100
                    )
                    if abs(dollar_change) > 0.01:
                        try:
                            await MetricsAggregator.write_daily_equity(
                                today, dollar_change
                            )
                        except Exception as e:
                            logger.warning(f"Failed to write equity point: {e}")

                except Exception as e:
                    result.errors += 1
                    result.error_details.append(
                        f"Error updating {pos.option_ticker}: {e}"
                    )

        except Exception as e:
            result.errors += len(group)
            result.error_details.append(
                f"Error fetching chain for {underlying}: {e}"
            )

    logger.info(
        f"Chunk update complete: {result.positions_processed} processed, "
        f"{result.exits_triggered} exits, {result.errors} errors"
    )
    return result
