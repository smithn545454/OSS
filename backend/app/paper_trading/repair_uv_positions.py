"""Repair corrupted UV paper trading positions.

UV positions were created with option tickers missing the O: prefix, so the
batch updater could never find them in the Polygon chain. They were all
eventually closed at $0.01 (EXPIRATION) when DTE reached 0.

This module fetches historical daily prices from Polygon and replays exit
conditions to determine what *should* have happened for each position.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.schemas import ExitReason, PaperPosition, TrackingConfig
from app.db.tables import PaperPositionTable
from app.paper_trading.exit_checker import check_exit_conditions
from app.services.polygon import PolygonClient

logger = logging.getLogger(__name__)


@dataclass
class RepairResult:
    """Summary of a UV position repair run."""

    total_corrupted: int = 0
    repaired: int = 0
    no_historical_data: int = 0
    still_correct: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)
    sample_repairs: list[dict] = field(default_factory=list)


def _is_corrupted_uv_position(position: PaperPosition) -> bool:
    """Check if a position is a corrupted UV position.

    Corrupted UV positions have:
    - scanner_source containing UNUSUAL_VOLUME
    - exit_reason = EXPIRATION
    - exit_price = 0.01 (the fallback for expired contracts not found in chain)
    """
    scanner = position.scanner_source or ""
    if "UNUSUAL_VOLUME" not in scanner.upper():
        return False

    exit_reason = getattr(position.exit_reason, "value", str(position.exit_reason or ""))
    if exit_reason != "EXPIRATION":
        return False

    if position.exit_price is None or abs(position.exit_price - 0.01) > 0.001:
        return False

    return True


def _normalize_ticker(option_ticker: str) -> str:
    """Ensure option ticker has the O: prefix for Polygon API calls."""
    if option_ticker and not option_ticker.startswith("O:"):
        return f"O:{option_ticker}"
    return option_ticker


async def _fetch_historical_bars(
    polygon_client: PolygonClient,
    option_ticker: str,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """Fetch historical daily bars for an option contract.

    Args:
        polygon_client: Initialized Polygon client
        option_ticker: Option ticker with O: prefix
        from_date: Start date YYYY-MM-DD
        to_date: End date YYYY-MM-DD

    Returns:
        List of daily bar dicts sorted by date ascending.
        Each bar has: t (timestamp ms), o, h, l, c, v, vw
    """
    try:
        bars = await polygon_client.get_daily_bars(option_ticker, from_date, to_date)
        return sorted(bars, key=lambda b: b.get("t", 0))
    except Exception as e:
        logger.warning(f"Failed to fetch historical bars for {option_ticker}: {e}")
        return []


def _replay_exit_conditions(
    position: PaperPosition,
    bars: list[dict],
    expiration_date: str,
    config: TrackingConfig,
) -> Optional[dict]:
    """Replay daily prices through exit checker to find the correct exit.

    Args:
        position: The paper position (for entry_price and thesis overrides)
        bars: Historical daily bars sorted chronologically
        expiration_date: Contract expiration date YYYY-MM-DD
        config: Tracking config with exit thresholds

    Returns:
        Dict with corrected exit info, or None if no exit found in bars
    """
    try:
        exp_date = datetime.strptime(expiration_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

    entry_date_str = position.entry_date
    try:
        entry_dt = datetime.fromisoformat(entry_date_str.replace("Z", "+00:00"))
        entry_date = entry_dt.date()
    except (ValueError, TypeError, AttributeError):
        entry_date = None

    mfe = 0.0
    mae = 0.0
    days_held = 0

    for bar in bars:
        # Parse bar date
        ts_ms = bar.get("t", 0)
        bar_date = datetime.utcfromtimestamp(ts_ms / 1000).date() if ts_ms else None
        if bar_date is None:
            continue

        # Skip bars before entry date
        if entry_date and bar_date < entry_date:
            continue

        # Use close price as daily settlement proxy
        current_price = bar.get("c", 0)
        if current_price <= 0:
            continue

        days_held += 1
        current_dte = (exp_date - bar_date).days

        # Calculate P&L
        if position.entry_price > 0:
            pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
        else:
            pnl_pct = 0.0

        # Track MFE/MAE
        if pnl_pct > 0 and pnl_pct > mfe:
            mfe = pnl_pct
        if pnl_pct < 0 and pnl_pct < mae:
            mae = pnl_pct

        # Check exit conditions
        exit_reason = check_exit_conditions(
            position, current_price, current_dte, config
        )

        if exit_reason:
            return {
                "exit_price": current_price,
                "exit_date": bar_date.isoformat(),
                "exit_reason": exit_reason,
                "current_pnl_pct": round(pnl_pct, 2),
                "dollar_pnl": round(
                    (current_price - position.entry_price) * position.quantity * 100, 2
                ),
                "days_held": days_held,
                "max_favorable_excursion": round(mfe, 2),
                "max_adverse_excursion": round(mae, 2),
            }

    # No exit triggered in bar history — the contract likely expired
    # Use the last bar's close as the exit price at expiration
    if bars:
        last_bar = bars[-1]
        last_price = last_bar.get("c", 0.01) or 0.01
        if position.entry_price > 0:
            pnl_pct = ((last_price - position.entry_price) / position.entry_price) * 100
        else:
            pnl_pct = 0.0
        return {
            "exit_price": last_price,
            "exit_date": expiration_date,
            "exit_reason": ExitReason.EXPIRATION,
            "current_pnl_pct": round(pnl_pct, 2),
            "dollar_pnl": round(
                (last_price - position.entry_price) * position.quantity * 100, 2
            ),
            "days_held": days_held,
            "max_favorable_excursion": round(mfe, 2),
            "max_adverse_excursion": round(mae, 2),
        }

    return None


async def repair_corrupted_uv_positions(
    polygon_client: PolygonClient,
    config: Optional[TrackingConfig] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> RepairResult:
    """Find and repair corrupted UV paper trading positions.

    Fetches historical option prices from Polygon and replays exit conditions
    to determine the correct exit price, reason, and P&L.

    Args:
        polygon_client: Initialized Polygon client
        config: Optional tracking config (uses defaults if None)
        dry_run: If True, report what would change without writing to DynamoDB
        limit: Max number of positions to repair (for testing)

    Returns:
        RepairResult with summary counts and sample repairs
    """
    if config is None:
        config = TrackingConfig()

    result = RepairResult()

    # Query all closed positions and filter for corrupted UV ones
    logger.info("Querying closed positions to find corrupted UV trades...")
    all_closed = await PaperPositionTable.list_closed()
    corrupted = [p for p in all_closed if _is_corrupted_uv_position(p)]
    result.total_corrupted = len(corrupted)

    if limit:
        corrupted = corrupted[:limit]

    logger.info(
        f"Found {result.total_corrupted} corrupted UV positions"
        f" (processing {len(corrupted)})"
    )

    for i, position in enumerate(corrupted):
        try:
            # Normalize the ticker for Polygon API
            corrected_ticker = _normalize_ticker(position.option_ticker)
            expiration_date = position.expiration_date or ""

            if not expiration_date:
                # Try to extract from option ticker
                from app.paper_trading.position_manager import (
                    extract_expiration_from_option_ticker,
                )
                expiration_date = extract_expiration_from_option_ticker(
                    position.option_ticker
                )

            # Fetch historical daily bars
            entry_date = position.entry_date or ""
            if entry_date and "T" in entry_date:
                entry_date = entry_date[:10]

            bars = await _fetch_historical_bars(
                polygon_client,
                corrected_ticker,
                entry_date,
                expiration_date,
            )

            if not bars:
                result.no_historical_data += 1
                logger.debug(
                    f"[{i+1}/{len(corrupted)}] No historical data for "
                    f"{corrected_ticker} ({entry_date} to {expiration_date})"
                )
                continue

            # Replay exit conditions
            corrected = _replay_exit_conditions(position, bars, expiration_date, config)

            if not corrected:
                result.no_historical_data += 1
                continue

            exit_reason = corrected["exit_reason"]
            exit_reason_val = (
                exit_reason.value if hasattr(exit_reason, "value") else str(exit_reason)
            )

            # Build update dict
            updates = {
                "exit_price": corrected["exit_price"],
                "exit_date": corrected["exit_date"],
                "exit_reason": exit_reason_val,
                "current_price": corrected["exit_price"],
                "current_pnl_pct": corrected["current_pnl_pct"],
                "dollar_pnl": corrected["dollar_pnl"],
                "days_held": corrected["days_held"],
                "max_favorable_excursion": corrected["max_favorable_excursion"],
                "max_adverse_excursion": corrected["max_adverse_excursion"],
                "option_ticker": corrected_ticker,
            }

            if not dry_run:
                await PaperPositionTable.update(position, updates)

            result.repaired += 1

            # Track sample repairs for reporting
            if len(result.sample_repairs) < 10:
                result.sample_repairs.append({
                    "position_id": position.position_id,
                    "ticker": corrected_ticker,
                    "old_exit": "EXPIRATION@$0.01",
                    "new_exit": f"{exit_reason_val}@${corrected['exit_price']:.2f}",
                    "old_pnl": f"{position.current_pnl_pct:.1f}%",
                    "new_pnl": f"{corrected['current_pnl_pct']:.1f}%",
                    "days_held": corrected["days_held"],
                    "bars_available": len(bars),
                })

            if (i + 1) % 50 == 0:
                logger.info(
                    f"[{i+1}/{len(corrupted)}] Progress: "
                    f"{result.repaired} repaired, "
                    f"{result.no_historical_data} no data, "
                    f"{result.errors} errors"
                )

        except Exception as e:
            result.errors += 1
            result.error_details.append(
                f"{position.option_ticker}: {e}"
            )
            if len(result.error_details) <= 5:
                logger.error(
                    f"Error repairing {position.option_ticker}: {e}", exc_info=True
                )

    logger.info(
        f"UV repair complete: {result.repaired} repaired, "
        f"{result.no_historical_data} no data, "
        f"{result.errors} errors out of {result.total_corrupted} corrupted"
    )

    return result
