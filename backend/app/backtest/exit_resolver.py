"""Backtest Exit Resolver.

Forward-scans through historical options data to determine when and how
a backtest trade would have been closed. Applies exit rules (stop loss,
profit target, time exit, max holding, trailing stop) on each trading day
from entry forward.

Two resolution modes:
- resolve_exit(): Single-trade resolution (for testing / small runs)
- resolve_exits_batch(): Date-ordered batch resolution (production path)
  Loads each forward-date parquet ONCE and resolves ALL open trades per date,
  reducing S3 reads from O(trades * dates) to O(dates).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

import pyarrow.compute as pc

from app.core.schemas import (
    BacktestExitConfig,
    BacktestTrade,
    ExitReason,
    PaperPosition,
)
from app.paper_trading.exit_checker import check_exit_conditions

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TradeLifecycle:
    """Intermediate state tracked during single-trade forward-scan."""

    entry_date: date
    entry_price: float
    option_ticker: str
    expiration_date: str
    exit_config: BacktestExitConfig

    peak_price: float = 0.0
    trough_price: float = float("inf")
    days_held: int = 0

    def __post_init__(self):
        self.peak_price = self.entry_price
        self.trough_price = self.entry_price


@dataclass
class PendingTrade:
    """Mutable state for a trade being resolved in batch mode."""

    trade_id: str
    run_id: str
    entry_date: date
    entry_price: float
    underlying_ticker: str
    option_ticker: str
    option_type: str  # normalized: "c" or "p"
    strike: float
    expiration_date: str
    exp_date: date
    max_scan_date: date
    exit_config: BacktestExitConfig
    scanner_type: str
    verdict: str
    combined_score: float
    slippage_model: str
    slippage_pct: float
    market_regime: Optional[str]
    mock_position: PaperPosition = field(repr=False, default=None)  # type: ignore[assignment]
    peak_price: float = 0.0
    trough_price: float = float("inf")
    days_held: int = 0

    def __post_init__(self) -> None:
        self.peak_price = self.entry_price
        self.trough_price = self.entry_price
        if self.mock_position is None:
            self.mock_position = PaperPosition(
                position_id=self.trade_id,
                evaluation_id=self.trade_id,
                option_ticker=self.option_ticker,
                underlying_ticker=self.underlying_ticker,
                entry_price=self.entry_price,
                entry_date=self.entry_date.isoformat(),
                status="OPEN",
                verdict_at_entry=self.verdict,
                current_price=self.entry_price,
                current_pnl_pct=0.0,
            )


def create_pending_trade(
    run_id: str,
    entry_date: date,
    entry_price: float,
    underlying_ticker: str,
    option_ticker: str,
    option_type: str,
    strike: float,
    expiration_date: str,
    exit_config: BacktestExitConfig,
    scanner_type: str,
    verdict: str,
    combined_score: float,
    slippage_model: str = "ask_plus_pct",
    slippage_pct: float = 0.05,
    market_regime: Optional[str] = None,
) -> Optional[PendingTrade]:
    """Create a PendingTrade for batch resolution.

    Returns None if expiration_date is unparseable.
    """
    try:
        exp_date = date.fromisoformat(expiration_date)
    except (ValueError, TypeError):
        return None

    otype = option_type.lower() if option_type else ""
    otype = "c" if otype in ("call", "c") else "p"

    max_scan_date = min(
        exp_date, entry_date + timedelta(days=exit_config.max_holding_days + 30)
    )

    return PendingTrade(
        trade_id=str(uuid.uuid4()),
        run_id=run_id,
        entry_date=entry_date,
        entry_price=entry_price,
        underlying_ticker=underlying_ticker,
        option_ticker=option_ticker,
        option_type=otype,
        strike=strike,
        expiration_date=expiration_date,
        exp_date=exp_date,
        max_scan_date=max_scan_date,
        exit_config=exit_config,
        scanner_type=scanner_type,
        verdict=verdict,
        combined_score=combined_score,
        slippage_model=slippage_model,
        slippage_pct=slippage_pct,
        market_regime=market_regime,
    )


# ---------------------------------------------------------------------------
# Batch exit resolution (production path)
# ---------------------------------------------------------------------------


async def resolve_exits_batch(
    data_provider: Any,
    pending_trades: list[PendingTrade],
) -> list[BacktestTrade]:
    """Resolve exits for all trades using date-ordered forward scan.

    Instead of resolving each trade independently (which loads the same
    forward-date parquet N times via cache thrashing), this iterates
    forward dates chronologically and resolves ALL open trades per date.

    Each forward-date parquet is loaded exactly ONCE. A price index is
    built from the filtered PyArrow table for O(1) per-trade lookups.
    This reduces S3 reads from O(trades * forward_dates) to O(forward_dates)
    — typically ~21 reads regardless of trade count.

    Args:
        data_provider: HistoricalDataProvider with S3 access.
        pending_trades: Trades to resolve (mutated in place).

    Returns:
        List of completed BacktestTrade records.
    """
    if not pending_trades:
        return []

    completed: list[BacktestTrade] = []

    # Compute the full forward date window
    earliest_entry = min(t.entry_date for t in pending_trades)
    latest_max_scan = max(t.max_scan_date for t in pending_trades)

    forward_dates: list[date] = []
    current = _next_trading_day(earliest_entry)
    while current <= latest_max_scan:
        forward_dates.append(current)
        current = _next_trading_day(current)

    logger.info(
        f"Batch exit resolution: {len(pending_trades)} trades, "
        f"{len(forward_dates)} forward dates to scan"
    )

    open_trades = list(pending_trades)
    needed_tickers = {t.underlying_ticker for t in open_trades}

    for fwd_date in forward_dates:
        if not open_trades:
            break

        # Load the lite options table for this forward date
        table = _load_lite_table(data_provider, fwd_date)
        if table is None:
            continue

        # Build price index: (ticker, strike_str, expiry, type) -> mid_price
        price_index = _build_price_index(table, needed_tickers)
        del table  # Free PyArrow table; index is a plain dict

        still_open: list[PendingTrade] = []

        for trade in open_trades:
            # Not yet active — this date is before trade's first check day
            if fwd_date <= trade.entry_date:
                still_open.append(trade)
                continue

            # Past max scan date — force exit
            if fwd_date > trade.max_scan_date:
                completed.append(_force_exit_trade(trade, data_provider))
                continue

            trade.days_held += 1

            # Look up price in the pre-built index
            price = _lookup_price(
                price_index,
                trade.underlying_ticker,
                trade.strike,
                trade.expiration_date,
                trade.option_type,
            )

            if price is None:
                still_open.append(trade)
                continue

            # Update MFE/MAE
            trade.peak_price = max(trade.peak_price, price)
            trade.trough_price = min(trade.trough_price, price)

            # Check exit conditions
            current_dte = (trade.exp_date - fwd_date).days
            exit_reason = check_exit_conditions(
                position=trade.mock_position,
                current_price=price,
                current_dte=current_dte,
                days_held=trade.days_held,
                peak_price=trade.peak_price,
                backtest_exit_config=trade.exit_config,
            )

            if exit_reason is not None:
                exit_price = _apply_exit_slippage(
                    price, trade.slippage_model, trade.slippage_pct,
                )
                completed.append(_make_trade(
                    trade_id=trade.trade_id, run_id=trade.run_id,
                    entry_date=trade.entry_date, exit_date=fwd_date,
                    ticker=trade.underlying_ticker,
                    option_ticker=trade.option_ticker,
                    option_type=trade.option_type, strike=trade.strike,
                    expiration_date=trade.expiration_date,
                    scanner_type=trade.scanner_type, verdict=trade.verdict,
                    combined_score=trade.combined_score,
                    entry_price=trade.entry_price, exit_price=exit_price,
                    exit_reason=exit_reason.value,
                    days_held=trade.days_held,
                    peak_price=trade.peak_price,
                    trough_price=trade.trough_price,
                    market_regime=trade.market_regime,
                ))
            else:
                still_open.append(trade)

        open_trades = still_open
        needed_tickers = {t.underlying_ticker for t in open_trades}
        del price_index

    # Remaining open trades — force exit at their max_scan_date
    for trade in open_trades:
        completed.append(_force_exit_trade(trade, data_provider))

    logger.info(
        f"Batch exit resolution complete: {len(completed)} trades resolved"
    )

    return completed


# ---------------------------------------------------------------------------
# Single-trade exit resolution (testing / backward compat)
# ---------------------------------------------------------------------------


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
    """Resolve exit for a single backtest trade by forward-scanning.

    For production use, prefer resolve_exits_batch() which is O(dates) instead
    of O(trades * dates) for S3 reads. This function is kept for testing and
    small-scale runs.
    """
    trade_id = str(uuid.uuid4())

    try:
        exp_date = date.fromisoformat(expiration_date)
    except (ValueError, TypeError):
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

    current = _next_trading_day(entry_date)
    max_scan_date = min(exp_date, entry_date + timedelta(days=exit_config.max_holding_days + 30))

    while current <= max_scan_date:
        lifecycle.days_held += 1

        current_price = await _get_contract_price(
            data_provider, underlying_ticker, option_ticker, current,
            strike=strike, expiration_date=expiration_date,
            option_type=option_type,
        )

        if current_price is None:
            current = _next_trading_day(current)
            continue

        lifecycle.peak_price = max(lifecycle.peak_price, current_price)
        lifecycle.trough_price = min(lifecycle.trough_price, current_price)

        current_dte = (exp_date - current).days

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
    """Calculate entry price after slippage."""
    if slippage_model == "mid":
        return mid_price
    elif slippage_model == "ask":
        return ask_price
    elif slippage_model == "ask_plus_pct":
        return ask_price * (1 + slippage_pct)
    else:
        return ask_price


# ---------------------------------------------------------------------------
# Shared helpers
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
    """Get contract mid price from historical data (single-trade path)."""
    try:
        if hasattr(data_provider, "get_contract_price"):
            return await data_provider.get_contract_price(
                ticker=underlying_ticker,
                strike=strike,
                expiration_date=expiration_date,
                option_type=option_type,
                as_of=as_of,
            )

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


# ---------------------------------------------------------------------------
# Batch resolution helpers
# ---------------------------------------------------------------------------


def _load_lite_table(data_provider: Any, as_of: date) -> Any:
    """Load column-filtered options chain table for a date.

    Uses the data provider's _read_options_chain_lite() which reads only
    price-relevant columns (~5 MB vs ~100 MB for full table).
    """
    if hasattr(data_provider, "_read_options_chain_lite"):
        return data_provider._read_options_chain_lite(as_of.isoformat())
    return None


def _build_price_index(
    table: Any,
    needed_tickers: set[str],
) -> dict[tuple[str, str, str, str], float]:
    """Build (ticker, strike_str, expiry, type) -> mid price lookup dict.

    Filters the PyArrow table to only the tickers we need (reduces 100K+ rows
    to ~2-3K), then converts to a Python dict for O(1) per-trade lookups.
    """
    if table is None or table.num_rows == 0:
        return {}

    # PyArrow push-down filter to relevant tickers only
    if needed_tickers and "ticker" in table.column_names:
        filtered = table.filter(pc.field("ticker").isin(list(needed_tickers)))
    else:
        filtered = table

    if filtered.num_rows == 0:
        return {}

    # Extract columns as Python lists (fast for small filtered result)
    tickers = filtered.column("ticker").to_pylist()
    strikes = filtered.column("strike").to_pylist()
    expiries = filtered.column("expiry_date").to_pylist()
    types = filtered.column("option_type").to_pylist()

    has_bid = "bid" in filtered.column_names
    has_ask = "ask" in filtered.column_names
    has_last = "last_price" in filtered.column_names

    bids = filtered.column("bid").to_pylist() if has_bid else [0.0] * len(tickers)
    asks = filtered.column("ask").to_pylist() if has_ask else [0.0] * len(tickers)
    lasts = filtered.column("last_price").to_pylist() if has_last else [0.0] * len(tickers)

    index: dict[tuple[str, str, str, str], float] = {}
    for i in range(len(tickers)):
        bid = float(bids[i] or 0)
        ask = float(asks[i] or 0)
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        elif float(lasts[i] or 0) > 0:
            mid = float(lasts[i])
        else:
            continue

        strike_str = f"{float(strikes[i] or 0):.2f}"
        otype = str(types[i] or "").lower()
        if otype in ("call", "c"):
            otype = "c"
        elif otype in ("put", "p"):
            otype = "p"

        index[(str(tickers[i] or ""), strike_str, str(expiries[i] or ""), otype)] = mid

    return index


def _lookup_price(
    index: dict[tuple[str, str, str, str], float],
    underlying_ticker: str,
    strike: float,
    expiration_date: str,
    option_type: str,
) -> Optional[float]:
    """Look up a contract price in the pre-built index."""
    strike_str = f"{strike:.2f}"
    return index.get((underlying_ticker, strike_str, expiration_date, option_type))


def _force_exit_trade(
    trade: PendingTrade,
    data_provider: Any,
) -> BacktestTrade:
    """Create a BacktestTrade for a trade that hit max scan or expiration."""
    exit_date = trade.max_scan_date

    # Try to get final price on the exit date
    final_price = None
    table = _load_lite_table(data_provider, exit_date)
    if table is not None:
        idx = _build_price_index(table, {trade.underlying_ticker})
        final_price = _lookup_price(
            idx, trade.underlying_ticker, trade.strike,
            trade.expiration_date, trade.option_type,
        )

    if final_price is None:
        final_price = 0.01

    return _make_trade(
        trade_id=trade.trade_id, run_id=trade.run_id,
        entry_date=trade.entry_date, exit_date=exit_date,
        ticker=trade.underlying_ticker,
        option_ticker=trade.option_ticker,
        option_type=trade.option_type, strike=trade.strike,
        expiration_date=trade.expiration_date,
        scanner_type=trade.scanner_type, verdict=trade.verdict,
        combined_score=trade.combined_score,
        entry_price=trade.entry_price, exit_price=final_price,
        exit_reason=ExitReason.EXPIRATION.value,
        days_held=trade.days_held,
        peak_price=trade.peak_price,
        trough_price=trade.trough_price,
        market_regime=trade.market_regime,
    )
