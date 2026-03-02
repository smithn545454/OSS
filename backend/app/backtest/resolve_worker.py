"""Backtest Phase 2: Resolve Worker.

Reads pending trades from DynamoDB for an assigned partition of tickers,
then forward-scans through historical options data to resolve exits.

Key design:
- Ticker-partitioned: each worker handles all trades for ~50 tickers
- Date-ordered forward scan: load each date's parquet ONCE for all open trades
- Column-filtered reads: only extract assigned tickers' options data (~5-15MB vs 150MB)
- Extends past backtest end_date if S3 data exists (user preference)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional

from app.backtest.exit_resolver import (
    PendingTrade,
    _apply_exit_slippage,
    _build_price_index,
    _lookup_price,
    _make_trade,
    _next_trading_day,
)
from app.core.schemas import (
    BacktestExitConfig,
    BacktestTrade,
    ExitReason,
)
from app.db.backtest_pending_table import BacktestPendingTradeTable
from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable
from app.paper_trading.exit_checker import check_exit_conditions

logger = logging.getLogger(__name__)

# How many S3 date files to read in parallel
PARALLEL_S3_READS = 20

# Tickers per resolver worker
TICKERS_PER_RESOLVER = 50


def _pending_dict_to_pending_trade(d: dict[str, Any]) -> Optional[PendingTrade]:
    """Convert a DynamoDB pending trade dict to a PendingTrade dataclass."""
    try:
        entry_date = date.fromisoformat(d["entry_date"])
        exp_date = date.fromisoformat(d["expiration_date"])

        exit_rules_data = d.get("exit_rules", {})
        exit_config = BacktestExitConfig(**exit_rules_data)

        max_scan_date = min(
            exp_date,
            entry_date + timedelta(days=exit_config.max_holding_days + 30),
        )

        otype = d.get("option_type", "c").lower()
        if otype in ("call", "c"):
            otype = "c"
        elif otype in ("put", "p"):
            otype = "p"

        pt = PendingTrade(
            trade_id=d["trade_id"],
            run_id=d["run_id"],
            entry_date=entry_date,
            entry_price=float(d["entry_price"]),
            underlying_ticker=d["underlying_ticker"],
            option_ticker=d.get("option_ticker", ""),
            option_type=otype,
            strike=float(d["strike"]),
            expiration_date=d["expiration_date"],
            exp_date=exp_date,
            max_scan_date=max_scan_date,
            exit_config=exit_config,
            scanner_type=d.get("scanner_type", "UNKNOWN"),
            verdict=d.get("verdict", "APPROVE"),
            combined_score=float(d.get("combined_score", 0)),
            slippage_model=d.get("slippage_model", "ask_plus_pct"),
            slippage_pct=float(d.get("slippage_pct", 0.05)),
            market_regime=d.get("market_regime"),
        )
        return pt
    except Exception as e:
        logger.warning(f"Failed to parse pending trade {d.get('trade_id', '?')}: {e}")
        return None


def _load_lite_table(data_provider: Any, as_of: date) -> Any:
    """Load column-filtered options chain table for a date."""
    if hasattr(data_provider, "_read_options_chain_lite"):
        return data_provider._read_options_chain_lite(as_of.isoformat())
    return None


async def resolve_ticker_partition(
    run_id: str,
    tickers: list[str],
    s3_bucket: str,
    end_date: Optional[str] = None,
) -> list[BacktestTrade]:
    """Resolve exits for all pending trades belonging to the given tickers.

    Forward-scans through dates chronologically, loading each date's
    parquet once and resolving all open trades per date.

    Args:
        run_id: Backtest run ID
        tickers: Tickers this worker is responsible for
        s3_bucket: S3 bucket with historical data
        end_date: Backtest end date (extends past if data exists)

    Returns:
        List of completed BacktestTrade records.
    """
    from app.core.historical_data_provider import HistoricalDataProvider

    # Load pending trades for this ticker partition
    pending_dicts = await BacktestPendingTradeTable.list_by_tickers(
        tickers, run_id=run_id
    )

    if not pending_dicts:
        logger.info(f"[Phase2] No pending trades for {len(tickers)} tickers")
        return []

    # Convert to PendingTrade objects
    pending_trades: list[PendingTrade] = []
    for d in pending_dicts:
        pt = _pending_dict_to_pending_trade(d)
        if pt is not None:
            pending_trades.append(pt)

    if not pending_trades:
        return []

    logger.info(
        f"[Phase2] Resolving {len(pending_trades)} trades for "
        f"{len(tickers)} tickers (run {run_id})"
    )

    # Compute forward date window
    earliest_entry = min(t.entry_date for t in pending_trades)
    latest_max_scan = max(t.max_scan_date for t in pending_trades)

    # Extend past backtest end_date if data exists
    if end_date:
        try:
            backtest_end = date.fromisoformat(end_date)
            # Extend by max holding days + buffer to resolve trades opening near end
            latest_max_scan = max(latest_max_scan, backtest_end + timedelta(days=60))
        except ValueError:
            pass

    # Generate forward dates to scan
    forward_dates: list[date] = []
    current = _next_trading_day(earliest_entry)
    while current <= latest_max_scan:
        forward_dates.append(current)
        current = _next_trading_day(current)

    logger.info(
        f"[Phase2] Forward-scanning {len(forward_dates)} dates "
        f"({forward_dates[0]} to {forward_dates[-1] if forward_dates else '?'})"
    )

    # Create a data provider for reading historical parquets
    provider = HistoricalDataProvider(
        s3_bucket=s3_bucket,
        as_of_date=earliest_entry,
    )

    # Forward-scan dates and resolve exits
    completed: list[BacktestTrade] = []
    open_trades = list(pending_trades)
    needed_tickers = set(tickers)

    for fwd_date in forward_dates:
        if not open_trades:
            break

        # Load lite options table for this date
        table = _load_lite_table(provider, fwd_date)
        if table is None:
            continue

        # Build price index filtered to our tickers
        price_index = _build_price_index(table, needed_tickers)
        del table

        still_open: list[PendingTrade] = []

        for trade in open_trades:
            if fwd_date <= trade.entry_date:
                still_open.append(trade)
                continue

            if fwd_date > trade.max_scan_date:
                completed.append(_force_exit(trade, provider))
                continue

            trade.days_held += 1

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

            trade.peak_price = max(trade.peak_price, price)
            trade.trough_price = min(trade.trough_price, price)

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

    # Remaining open trades — force exit at last available price
    for trade in open_trades:
        completed.append(_force_exit(trade, provider))

    logger.info(
        f"[Phase2] Resolved {len(completed)} trades for {len(tickers)} tickers"
    )

    return completed


def _force_exit(trade: PendingTrade, data_provider: Any) -> BacktestTrade:
    """Force-exit a trade at the max scan date with best available price."""
    exit_date = trade.max_scan_date
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


async def run_phase2_worker(
    run_id: str,
    tickers: list[str],
    s3_bucket: str,
    end_date: str,
    phase2_total: int,
) -> dict[str, Any]:
    """Full Phase 2 worker: resolve exits, write trades, report progress.

    Args:
        run_id: Backtest run ID
        tickers: Ticker partition for this worker
        s3_bucket: S3 bucket for historical data
        end_date: Backtest end date
        phase2_total: Total Phase 2 workers

    Returns:
        Summary with trade counts and phase transition info.
    """
    # Resolve exits for this ticker partition
    completed_trades = await resolve_ticker_partition(
        run_id=run_id,
        tickers=tickers,
        s3_bucket=s3_bucket,
        end_date=end_date,
    )

    # Write completed trades to DynamoDB
    if completed_trades:
        trade_dicts = [t.model_dump() for t in completed_trades]
        await BacktestTradeTable.put_batch(trade_dicts)

    # Atomically increment Phase 2 completion counter
    # Reuse batches_completed (reset to 0 at phase transition by coordinator)
    phase2_completed = await BacktestRunTable.atomic_increment_batches_completed(run_id)
    is_last = phase2_completed >= phase2_total

    logger.info(
        f"[Phase2] Worker done: {len(tickers)} tickers, "
        f"{len(completed_trades)} trades resolved, "
        f"{phase2_completed}/{phase2_total} workers complete"
        f"{' — TRIGGERING PHASE 3' if is_last else ''}"
    )

    return {
        "status": "success",
        "phase": "resolve",
        "tickers_count": len(tickers),
        "trades_resolved": len(completed_trades),
        "phase2_completed": phase2_completed,
        "phase2_total": phase2_total,
        "trigger_phase3": is_last,
    }
