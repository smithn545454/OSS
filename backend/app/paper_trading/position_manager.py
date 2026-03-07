"""Position management for paper trading.

Per Section 17.1-17.2 of OSS_Complete_Requirements.md.

Handles:
- Position creation for APPROVE/WATCH decisions
- Daily position updates with current prices
- P&L, MFE/MAE calculation
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.core.schemas import (
    Decision,
    Evaluation,
    ExitReason,
    PaperPosition,
    PositionStatus,
    QualityTier,
    TrackingConfig,
    Verdict,
)
from app.db.tables import PaperPositionTable, PaperSnapshotTable
from app.paper_trading.exit_checker import (
    calculate_dte_from_expiration,
    check_exit_conditions,
)
from app.paper_trading.models import UpdateResult
from app.services.polygon import PolygonClient

logger = logging.getLogger(__name__)


async def create_position_from_evaluation(
    evaluation: Evaluation,
    decision: Decision,
) -> Optional[PaperPosition]:
    """Create a paper position from an evaluation and decision.
    
    Per Section 17.1:
    - Entry price = ask at evaluation time (realistic buy price)
    - Quantity = 1 contract

    Args:
        evaluation: The evaluation record
        decision: The decision for this evaluation

    Returns:
        Created PaperPosition or None if position already exists
    """
    # Check for duplicate by evaluation_id
    existing = await PaperPositionTable.get_by_evaluation_id(evaluation.evaluation_id)
    if existing:
        logger.warning(
            f"Skipping enrollment: position already exists for evaluation "
            f"{evaluation.evaluation_id} ({evaluation.option_ticker})"
        )
        return None

    # Check for duplicate by option_ticker (same contract already has an open position)
    has_open = await PaperPositionTable.has_open_position(evaluation.option_ticker)
    if has_open:
        logger.warning(
            f"Skipping enrollment: open position already exists for contract "
            f"{evaluation.option_ticker} (eval={evaluation.evaluation_id})"
        )
        return None
    
    # Determine quality tier
    quality_tier: Optional[QualityTier] = None
    if decision.quality_tier:
        if isinstance(decision.quality_tier, str):
            quality_tier = QualityTier(decision.quality_tier)
        else:
            quality_tier = decision.quality_tier
    
    # Create position with denormalized enrichment fields
    now = datetime.now(timezone.utc).isoformat()
    entry_date = now[:10]  # YYYY-MM-DD

    # Extract scanner source from evaluation
    scanner_source = getattr(evaluation, "scanner_source", None)

    # Count convergence (number of scanner triggers from the opportunity)
    convergence_count = 1  # At least the primary scanner

    # Entry price uses ask (what you'd actually pay to buy a long option)
    entry_price = evaluation.ask if evaluation.ask > 0 else evaluation.mid
    position = PaperPosition(
        evaluation_id=evaluation.evaluation_id,
        option_ticker=evaluation.option_ticker,
        entry_price=entry_price,
        entry_date=entry_date,
        quantity=1,
        verdict_at_entry=decision.verdict,
        quality_tier_at_entry=quality_tier,
        current_price=entry_price,
        current_pnl_pct=0.0,
        max_favorable_excursion=0.0,
        max_adverse_excursion=0.0,
        days_held=0,
        status=PositionStatus.OPEN,
        last_updated=now,
        # Denormalized enrichment fields
        underlying_ticker=evaluation.underlying_ticker,
        scanner_source=scanner_source,
        convergence_count=convergence_count,
        conviction_score=decision.final_score,
        pillar_directional=decision.directional_score,
        pillar_volatility=decision.volatility_score,
        pillar_structure=decision.structure_score,
        strike=evaluation.strike,
        option_type=evaluation.option_type,
        expiration_date=evaluation.expiration_date,
        dte_at_entry=evaluation.dte,
        dte_bucket=evaluation.dte_bucket,
        entry_delta=evaluation.delta,
        entry_iv=evaluation.iv,
        entry_theta=evaluation.theta,
    )
    
    # Persist position
    try:
        await PaperPositionTable.put(position)
    except Exception as e:
        logger.error(
            f"Failed to persist paper position for {evaluation.option_ticker} "
            f"(eval={evaluation.evaluation_id}): {e}"
        )
        return None
    logger.info(
        f"Created paper position for {evaluation.option_ticker} "
        f"(verdict={decision.verdict}, tier={quality_tier})"
    )

    # Update pre-aggregated metrics
    try:
        from app.paper_trading.metrics_aggregator import MetricsAggregator
        await MetricsAggregator.on_position_opened(position)
    except Exception as e:
        logger.warning(f"Failed to update metrics on open: {e}")

    return position


async def create_positions_from_decisions(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
    config: Optional[TrackingConfig] = None,
) -> list[PaperPosition]:
    """Create paper positions for APPROVE and WATCH decisions.
    
    Per Section 17.1:
    - Trigger: Verdict = APPROVE or WATCH
    - Entry price = ask at evaluation time (realistic buy price)
    - Quantity = 1 contract
    
    Args:
        evaluations: List of evaluations
        decisions: Dict mapping evaluation_id to Decision
        config: Optional tracking configuration
        
    Returns:
        List of created PaperPositions
    """
    created_positions: list[PaperPosition] = []
    eligible_count = 0
    skipped_count = 0

    for evaluation in evaluations:
        decision = decisions.get(evaluation.evaluation_id)
        if not decision:
            continue

        # Only create positions for APPROVE and WATCH
        if decision.verdict not in (Verdict.APPROVE, Verdict.WATCH):
            continue

        eligible_count += 1
        position = await create_position_from_evaluation(evaluation, decision)
        if position:
            created_positions.append(position)
        else:
            skipped_count += 1

    logger.info(
        f"Paper trading enrollment: {len(created_positions)}/{eligible_count} created, "
        f"{skipped_count} skipped (dedup or error)"
    )
    return created_positions


async def update_position(
    position: PaperPosition,
    current_price: float,
    expiration_date: str,
    config: Optional[TrackingConfig] = None,
) -> UpdateResult:
    """Update a single position with current price.
    
    Per Section 17.2:
    1. Calculate P&L %
    2. Update MFE/MAE
    3. Increment days_held
    4. Check exit conditions
    
    Args:
        position: The position to update
        current_price: Current mid price of the option
        expiration_date: Contract expiration date (YYYY-MM-DD)
        config: Optional tracking configuration
        
    Returns:
        UpdateResult with details of the update
    """
    if config is None:
        config = TrackingConfig()
    
    # Calculate current P&L
    previous_price = position.current_price
    previous_pnl = position.current_pnl_pct
    
    if position.entry_price > 0:
        current_pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
    else:
        current_pnl_pct = 0.0
    
    # Update MFE/MAE
    mfe_updated = False
    mae_updated = False
    new_mfe = position.max_favorable_excursion
    new_mae = position.max_adverse_excursion
    
    if current_pnl_pct > 0 and current_pnl_pct > position.max_favorable_excursion:
        new_mfe = current_pnl_pct
        mfe_updated = True
    
    if current_pnl_pct < 0 and current_pnl_pct < position.max_adverse_excursion:
        new_mae = current_pnl_pct
        mae_updated = True
    
    # Increment days held
    new_days_held = position.days_held + 1
    
    # Check exit conditions
    current_dte = calculate_dte_from_expiration(expiration_date)
    exit_reason = check_exit_conditions(position, current_price, current_dte, config)
    
    if exit_reason:
        # Close the position
        closed_position = await PaperPositionTable.close(
            position=position,
            exit_price=current_price,
            exit_reason=exit_reason.value,
        )
        
        logger.info(
            f"Closed position {position.position_id} ({position.option_ticker}): "
            f"{exit_reason.value} at {current_pnl_pct:.1f}%"
        )

        # Update pre-aggregated metrics on close
        try:
            from app.paper_trading.metrics_aggregator import MetricsAggregator
            # Set the final PnL on position for metric tracking
            position.current_pnl_pct = current_pnl_pct
            await MetricsAggregator.on_position_closed(position)
        except Exception as e:
            logger.warning(f"Failed to update metrics on close: {e}")

        return UpdateResult(
            position_id=position.position_id,
            option_ticker=position.option_ticker,
            previous_price=previous_price,
            current_price=current_price,
            previous_pnl_pct=previous_pnl,
            current_pnl_pct=current_pnl_pct,
            mfe_updated=mfe_updated,
            mae_updated=mae_updated,
            exit_triggered=True,
            exit_reason=exit_reason,
        )
    
    # Update position (no exit triggered)
    updates = {
        "current_price": current_price,
        "current_pnl_pct": current_pnl_pct,
        "max_favorable_excursion": new_mfe,
        "max_adverse_excursion": new_mae,
        "days_held": new_days_held,
    }
    
    await PaperPositionTable.update(position, updates)
    
    return UpdateResult(
        position_id=position.position_id,
        option_ticker=position.option_ticker,
        previous_price=previous_price,
        current_price=current_price,
        previous_pnl_pct=previous_pnl,
        current_pnl_pct=current_pnl_pct,
        mfe_updated=mfe_updated,
        mae_updated=mae_updated,
        exit_triggered=False,
    )


async def update_open_positions(
    polygon_client: PolygonClient,
    config: Optional[TrackingConfig] = None,
) -> list[UpdateResult]:
    """Update all open positions with current prices.
    
    Per Section 17.2 - Daily Updates:
    - Fetch current prices
    - Calculate P&L
    - Update MFE/MAE
    - Check exit conditions
    
    Args:
        polygon_client: Polygon client for fetching prices
        config: Optional tracking configuration
        
    Returns:
        List of UpdateResults for each position
    """
    results: list[UpdateResult] = []
    
    # Get all open positions
    open_positions = await PaperPositionTable.list_open()
    if not open_positions:
        logger.info("No open positions to update")
        return results
    
    logger.info(f"Updating {len(open_positions)} open positions")
    
    # Group positions by underlying ticker for efficient fetching
    positions_by_underlying: dict[str, list[PaperPosition]] = {}
    for position in open_positions:
        # Extract underlying from option ticker (e.g., O:AAPL240315C00170000 -> AAPL)
        underlying = extract_underlying_from_option_ticker(position.option_ticker)
        if underlying not in positions_by_underlying:
            positions_by_underlying[underlying] = []
        positions_by_underlying[underlying].append(position)
    
    # Fetch options chains and update positions
    for underlying, positions in positions_by_underlying.items():
        try:
            # Fetch options chain for underlying
            chain = await polygon_client.get_options_chain(underlying)
            
            # Build lookup by option ticker
            # Polygon returns ticker at top level, not nested in details
            contract_data: dict[str, dict] = {}
            for contract in chain:
                ticker = contract.get("ticker") or contract.get("details", {}).get("ticker")
                if ticker:
                    contract_data[ticker] = contract
            
            # Update each position
            for position in positions:
                result = await _update_position_from_chain(
                    position, contract_data, config
                )
                results.append(result)
                
        except Exception as e:
            logger.error(f"Error fetching chain for {underlying}: {e}")
            # Record errors for positions
            for position in positions:
                results.append(
                    UpdateResult(
                        position_id=position.position_id,
                        option_ticker=position.option_ticker,
                        previous_price=position.current_price,
                        current_price=position.current_price,
                        previous_pnl_pct=position.current_pnl_pct,
                        current_pnl_pct=position.current_pnl_pct,
                        mfe_updated=False,
                        mae_updated=False,
                        exit_triggered=False,
                        error=str(e),
                    )
                )
    
    # Log summary
    exits = sum(1 for r in results if r.exit_triggered)
    errors = sum(1 for r in results if r.error)
    logger.info(
        f"Position update complete: {len(results)} updated, "
        f"{exits} exits, {errors} errors"
    )
    
    return results


async def _update_position_from_chain(
    position: PaperPosition,
    contract_data: dict[str, dict],
    config: Optional[TrackingConfig] = None,
) -> UpdateResult:
    """Update a position using pre-fetched chain data.
    
    Args:
        position: The position to update
        contract_data: Dict mapping option ticker to contract snapshot
        config: Optional tracking configuration
        
    Returns:
        UpdateResult
    """
    contract = contract_data.get(position.option_ticker)
    
    if not contract:
        # Contract not found - may be expired or delisted
        logger.warning(f"Contract not found in chain: {position.option_ticker}")
        
        # Check if likely expired based on option ticker
        expiration = extract_expiration_from_option_ticker(position.option_ticker)
        current_dte = calculate_dte_from_expiration(expiration)
        
        if current_dte <= 0:
            # Expired - close at intrinsic value estimate (use 0.01)
            closed = await PaperPositionTable.close(
                position=position,
                exit_price=0.01,
                exit_reason=ExitReason.EXPIRATION.value,
            )
            
            return UpdateResult(
                position_id=position.position_id,
                option_ticker=position.option_ticker,
                previous_price=position.current_price,
                current_price=0.01,
                previous_pnl_pct=position.current_pnl_pct,
                current_pnl_pct=-99.0,  # Near total loss
                mfe_updated=False,
                mae_updated=True,
                exit_triggered=True,
                exit_reason=ExitReason.EXPIRATION,
            )
        
        # Not expired but missing — still increment days_held so position
        # doesn't appear "never updated". Price stays at last known value.
        new_days_held = position.days_held + 1
        now = datetime.now(timezone.utc).isoformat()
        await PaperPositionTable.update(position, {
            "days_held": new_days_held,
            "last_updated": now,
        })
        logger.info(
            f"Contract not in chain for {position.option_ticker} "
            f"(days_held={new_days_held}), keeping last price"
        )
        return UpdateResult(
            position_id=position.position_id,
            option_ticker=position.option_ticker,
            previous_price=position.current_price,
            current_price=position.current_price,
            previous_pnl_pct=position.current_pnl_pct,
            current_pnl_pct=position.current_pnl_pct,
            mfe_updated=False,
            mae_updated=False,
            exit_triggered=False,
            error="Contract not found in chain",
        )
    
    # Extract current price (bid for long positions — what you'd get selling)
    day = contract.get("day", {})
    underlying = contract.get("underlying_asset", {})
    
    last_quote = contract.get("last_quote", {})
    bid = last_quote.get("bid", 0) or 0
    ask = last_quote.get("ask", 0) or 0
    
    # Use bid for current price (long positions exit at bid, not mid)
    if bid > 0:
        current_price = bid
    elif day.get("close"):
        current_price = day["close"]
    else:
        # Fallback to previous price
        current_price = position.current_price
    
    # Extract expiration date
    details = contract.get("details", {})
    expiration_date = details.get("expiration_date", "2099-12-31")

    # Write daily snapshot for historical tracking
    try:
        greeks = contract.get("greeks", {})
        underlying_asset = contract.get("underlying_asset", {})
        snapshot_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await PaperSnapshotTable.put_snapshot(
            position.position_id,
            snapshot_date,
            {
                "price": current_price,
                "delta": greeks.get("delta"),
                "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"),
                "vega": greeks.get("vega"),
                "iv": contract.get("implied_volatility"),
                "underlying_price": underlying_asset.get("price"),
                "volume": day.get("volume"),
            },
        )
    except Exception as e:
        logger.warning(f"Failed to write snapshot for {position.position_id}: {e}")

    return await update_position(position, current_price, expiration_date, config)


def extract_underlying_from_option_ticker(option_ticker: str) -> str:
    """Extract underlying ticker from option ticker.
    
    Option ticker format: O:AAPL240315C00170000
    - O: prefix
    - AAPL: underlying ticker (variable length, 1-5 chars typically)
    - 240315: expiration date YYMMDD
    - C/P: call/put
    - 00170000: strike price
    
    Args:
        option_ticker: Full option ticker (e.g., O:AAPL240315C00170000)
        
    Returns:
        Underlying ticker (e.g., AAPL)
    """
    # Remove O: prefix if present
    ticker = option_ticker
    if ticker.startswith("O:"):
        ticker = ticker[2:]
    
    # Find where the date portion starts (6 digits followed by C/P)
    # The underlying is everything before that
    for i in range(len(ticker) - 7, 0, -1):
        # Check if this position starts a date + option type pattern
        remaining = ticker[i:]
        if len(remaining) >= 7:
            date_part = remaining[:6]
            option_type = remaining[6]
            if date_part.isdigit() and option_type in ("C", "P"):
                return ticker[:i]
    
    # Fallback: assume first 4 characters or until first digit
    for i, c in enumerate(ticker):
        if c.isdigit():
            return ticker[:i] if i > 0 else ticker[:4]
    
    return ticker[:4]


def extract_expiration_from_option_ticker(option_ticker: str) -> str:
    """Extract expiration date from option ticker.
    
    Args:
        option_ticker: Full option ticker (e.g., O:AAPL240315C00170000)
        
    Returns:
        Expiration date in YYYY-MM-DD format
    """
    # Remove O: prefix if present
    ticker = option_ticker
    if ticker.startswith("O:"):
        ticker = ticker[2:]
    
    # Find where the date portion starts
    for i in range(len(ticker) - 7, 0, -1):
        remaining = ticker[i:]
        if len(remaining) >= 7:
            date_part = remaining[:6]
            option_type = remaining[6]
            if date_part.isdigit() and option_type in ("C", "P"):
                # Parse YYMMDD
                try:
                    year = int(date_part[:2]) + 2000
                    month = int(date_part[2:4])
                    day = int(date_part[4:6])
                    return f"{year:04d}-{month:02d}-{day:02d}"
                except ValueError:
                    pass
    
    # Fallback: far future
    return "2099-12-31"


async def close_position_manually(
    position_id: str,
    exit_price: Optional[float] = None,
) -> Optional[PaperPosition]:
    """Manually close a position.
    
    Args:
        position_id: The position ID to close
        exit_price: Optional exit price (uses current_price if not provided)
        
    Returns:
        The closed position or None if not found
    """
    # Find the position in open positions
    open_positions = await PaperPositionTable.list_open()
    position = None
    for p in open_positions:
        if p.position_id == position_id:
            position = p
            break
    
    if not position:
        logger.warning(f"Position not found for manual close: {position_id}")
        return None
    
    # Use provided price or current price
    final_exit_price = exit_price if exit_price is not None else position.current_price
    
    closed = await PaperPositionTable.close(
        position=position,
        exit_price=final_exit_price,
        exit_reason=ExitReason.MANUAL.value,
    )
    
    logger.info(f"Manually closed position {position_id} at {final_exit_price}")
    return closed
