"""Backtest Exit Resolver.

Forward-scans through historical options data to determine when and how
a backtest trade would have been closed. Applies exit rules (stop loss,
profit target, time exit, max holding, trailing stop) on each trading day
from entry forward.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from app.core.schemas import (
    BacktestExitConfig,
    BacktestTrade,
    ExitReason,
    PaperPosition,
)
from app.paper_trading.exit_checker import check_exit_conditions

logger = logging.getLogger(__name__)


@dataclass
class TradeLifecycle:
    """Intermediate state tracked during forward-scan exit resolution."""

    entry_date: date
    entry_price: float
    option_ticker: str
    expiration_date: str
    exit_config: BacktestExitConfig

    # Tracking state updated each day
    peak_price: float = 0.0
    trough_price: float = float("inf")
    days_held: int = 0

    def __post_init__(self):
        self.peak_price = self.entry_price
        self.trough_price = self.entry_price


async def resolve_exit(
    data_provider: Any,
    entry_date: date,
    entry_price: float,
    option_ticker: str,
    underlying_ticker: str,
    option_type: str,
    strike: float,
    expiration_date: str,
    exit_config: BacktestExitConfig,
    scanner_type: str,
    verdict: str,
    combined_score: float,
    run_id: str,
    slippage_model: str = "ask_plus_pct",
    slippage_pct: float = 0.05,
    market_regime: Optional[str] = None,
) -> BacktestTrade:
    """Resolve exit for a single backtest trade by forward-scanning historical data.

    Starting from entry_date + 1, loads daily options data for the contract
    and checks exit conditions. Continues until an exit triggers or the
    contract expires.

    Args:
        data_provider: DataProvider for historical options data
        entry_date: Date the trade was entered
        entry_price: Entry price (post-slippage)
        option_ticker: The option contract ticker
        underlying_ticker: Underlying stock ticker
        option_type: CALL or PUT
        strike: Strike price
        expiration_date: Contract expiration (YYYY-MM-DD)
        exit_config: Exit rule configuration
        scanner_type: Which scanner generated this opportunity
        verdict: APPROVE or WATCH
        combined_score: Pipeline combined score
        run_id: Backtest run ID
        slippage_model: How to model exit slippage
        slippage_pct: Exit slippage percentage
        market_regime: Optional market regime classification

    Returns:
        BacktestTrade with complete lifecycle data.
    """
    trade_id = str(uuid.uuid4())

    # Parse expiration
    try:
        exp_date = date.fromisoformat(expiration_date)
    except (ValueError, TypeError):
        # Can't resolve without expiration — mark as expired on entry+1
        return _make_trade(
            trade_id=trade_id, run_id=run_id, entry_date=entry_date,
            exit_date=entry_date + timedelta(days=1),
            ticker=underlying_ticker, option_ticker=option_ticker,
            option_type=option_type, strike=strike,
            expiration_date=expiration_date, scanner_type=scanner_type,
            verdict=verdict, combined_score=combined_score,
            entry_price=entry_price, exit_price=0.01,
            exit_reason=ExitReason.EXPIRATION.value,
            days_held=1, market_regime=market_regime,
        )

    lifecycle = TradeLifecycle(
        entry_date=entry_date,
        entry_price=entry_price,
        option_ticker=option_ticker,
        expiration_date=expiration_date,
        exit_config=exit_config,
    )

    # Build a mock PaperPosition for exit checker compatibility
    mock_position = PaperPosition(
        position_id=trade_id,
        evaluation_id=trade_id,
        option_ticker=option_ticker,
        underlying_ticker=underlying_ticker,
        entry_price=entry_price,
        entry_date=entry_date.isoformat(),
        status="OPEN",
        verdict_at_entry=verdict,
        current_price=entry_price,
        current_pnl_pct=0.0,
    )

    # Forward-scan from entry_date+1 through expiration
    current = _next_trading_day(entry_date)
    max_scan_date = min(exp_date, entry_date + timedelta(days=exit_config.max_holding_days + 30))

    while current <= max_scan_date:
        lifecycle.days_held += 1

        # Get contract price for this day
        current_price = await _get_contract_price(
            data_provider, underlying_ticker, option_ticker, current,
            strike=strike, expiration_date=expiration_date,
            option_type=option_type,
        )

        if current_price is None:
            # No data for this day — skip (holiday/weekend gap)
            current = _next_trading_day(current)
            continue

        # Update MFE/MAE tracking
        lifecycle.peak_price = max(lifecycle.peak_price, current_price)
        lifecycle.trough_price = min(lifecycle.trough_price, current_price)

        # Calculate DTE
        current_dte = (exp_date - current).days

        # Check exit conditions
        exit_reason = check_exit_conditions(
            position=mock_position,
            current_price=current_price,
            current_dte=current_dte,
            days_held=lifecycle.days_held,
            peak_price=lifecycle.peak_price,
            backtest_exit_config=exit_config,
        )

        if exit_reason is not None:
            exit_price = _apply_exit_slippage(
                current_price, slippage_model, slippage_pct,
            )
            return _make_trade(
                trade_id=trade_id, run_id=run_id, entry_date=entry_date,
                exit_date=current, ticker=underlying_ticker,
                option_ticker=option_ticker, option_type=option_type,
                strike=strike, expiration_date=expiration_date,
                scanner_type=scanner_type, verdict=verdict,
                combined_score=combined_score, entry_price=entry_price,
                exit_price=exit_price, exit_reason=exit_reason.value,
                days_held=lifecycle.days_held,
                peak_price=lifecycle.peak_price,
                trough_price=lifecycle.trough_price,
                market_regime=market_regime,
            )

        current = _next_trading_day(current)

    # Reached expiration or max scan — force exit
    final_price = await _get_contract_price(
        data_provider, underlying_ticker, option_ticker, exp_date,
        strike=strike, expiration_date=expiration_date,
        option_type=option_type,
    )
    if final_price is None:
        # Contract expired worthless or no data
        final_price = 0.01

    return _make_trade(
        trade_id=trade_id, run_id=run_id, entry_date=entry_date,
        exit_date=min(exp_date, max_scan_date),
        ticker=underlying_ticker, option_ticker=option_ticker,
        option_type=option_type, strike=strike,
        expiration_date=expiration_date, scanner_type=scanner_type,
        verdict=verdict, combined_score=combined_score,
        entry_price=entry_price, exit_price=final_price,
        exit_reason=ExitReason.EXPIRATION.value,
        days_held=lifecycle.days_held,
        peak_price=lifecycle.peak_price,
        trough_price=lifecycle.trough_price,
        market_regime=market_regime,
    )


def apply_entry_slippage(
    ask_price: float,
    mid_price: float,
    slippage_model: str,
    slippage_pct: float,
) -> float:
    """Calculate entry price after slippage.

    Args:
        ask_price: Contract ask price
        mid_price: Contract mid price
        slippage_model: "mid" | "ask" | "ask_plus_pct"
        slippage_pct: Additional slippage percentage

    Returns:
        Entry price after slippage applied.
    """
    if slippage_model == "mid":
        return mid_price
    elif slippage_model == "ask":
        return ask_price
    elif slippage_model == "ask_plus_pct":
        return ask_price * (1 + slippage_pct)
    else:
        return ask_price


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_contract_price(
    data_provider: Any,
    underlying_ticker: str,
    option_ticker: str,
    as_of: date,
    strike: float = 0.0,
    expiration_date: str = "",
    option_type: str = "",
) -> Optional[float]:
    """Get contract mid price from historical data.

    Uses the lightweight get_contract_price() path when available (column-filtered
    parquet reads, ~33MB vs ~75MB per file). Falls back to full get_options_chain()
    for providers that don't support it.
    """
    try:
        # Prefer column-filtered reads (HistoricalDataProvider)
        if hasattr(data_provider, "get_contract_price"):
            return await data_provider.get_contract_price(
                ticker=underlying_ticker,
                strike=strike,
                expiration_date=expiration_date,
                option_type=option_type,
                as_of=as_of,
            )

        # Fallback: load full options chain
        chain = await data_provider.get_options_chain(
            underlying_ticker, as_of=as_of, min_dte=0, max_dte=365,
        )
        ot_lower = option_type.lower() if option_type else ""
        target_type = "call" if ot_lower in ("call", "c") else "put"

        for contract in chain:
            details = contract.get("details", {})
            c_strike = float(details.get("strike_price", 0) or 0)
            c_expiry = str(details.get("expiration_date", ""))
            c_type = str(details.get("contract_type", "")).lower()

            if (
                abs(c_strike - strike) < 0.01
                and c_expiry == expiration_date
                and c_type == target_type
            ):
                quote = contract.get("last_quote", {})
                if isinstance(quote, dict):
                    bid = float(quote.get("bid", 0) or 0)
                    ask = float(quote.get("ask", 0) or 0)
                else:
                    bid = float(getattr(quote, "bid", 0) or 0)
                    ask = float(getattr(quote, "ask", 0) or 0)

                if bid > 0 and ask > 0:
                    return (bid + ask) / 2

                day = contract.get("day", {})
                if isinstance(day, dict):
                    close = float(day.get("close", 0) or 0)
                else:
                    close = float(getattr(day, "close", 0) or 0)
                if close > 0:
                    return close

        return None

    except Exception as e:
        logger.debug(
            f"Could not get price for {underlying_ticker} "
            f"{strike} {option_type} {expiration_date} on {as_of}: {e}"
        )
        return None


def _apply_exit_slippage(
    price: float,
    slippage_model: str,
    slippage_pct: float,
) -> float:
    """Apply exit slippage (selling at bid side)."""
    # Exit at slightly worse than current price
    return price * (1 - slippage_pct)


def _next_trading_day(d: date) -> date:
    """Get the next trading day (skip weekends)."""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _make_trade(
    trade_id: str,
    run_id: str,
    entry_date: date,
    exit_date: date,
    ticker: str,
    option_ticker: str,
    option_type: str,
    strike: float,
    expiration_date: str,
    scanner_type: str,
    verdict: str,
    combined_score: float,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    days_held: int,
    peak_price: float = 0.0,
    trough_price: float = float("inf"),
    market_regime: Optional[str] = None,
) -> BacktestTrade:
    """Build a BacktestTrade with calculated P&L and MFE/MAE."""
    pnl_dollars = exit_price - entry_price
    pnl_pct = (pnl_dollars / entry_price * 100) if entry_price > 0 else 0.0

    # MFE/MAE as percentage of entry price
    mfe_pct = ((peak_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
    mae_pct = ((entry_price - trough_price) / entry_price * 100) if entry_price > 0 else 0.0

    return BacktestTrade(
        trade_id=trade_id,
        run_id=run_id,
        entry_date=entry_date.isoformat(),
        exit_date=exit_date.isoformat(),
        ticker=ticker,
        option_ticker=option_ticker,
        option_type=option_type,
        strike=strike,
        expiration_date=expiration_date,
        scanner_type=scanner_type,
        verdict=verdict,
        combined_score=combined_score,
        entry_price=round(entry_price, 4),
        exit_price=round(exit_price, 4),
        exit_reason=exit_reason,
        pnl_dollars=round(pnl_dollars, 4),
        pnl_pct=round(pnl_pct, 2),
        days_held=days_held,
        mfe_pct=round(mfe_pct, 2),
        mae_pct=round(mae_pct, 2),
        peak_price=round(peak_price, 4),
        market_regime=market_regime,
    )
