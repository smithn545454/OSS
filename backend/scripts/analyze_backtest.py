#!/usr/bin/env python3
"""Backtest Diagnostic Script — 10 analysis modules for deep trade-level validation.

Usage:
    cd backend && python scripts/analyze_backtest.py --run-id <uuid> --s3-bucket <bucket>

Modules:
    1. Trade Census — counts by scanner/verdict/exit, price distributions
    2. Entry Pricing Verification — verify entry_price ≈ ask × (1 + slippage)
    3. Exit Pricing Verification — verify exit_price ≈ mid × (1 - slippage)
    4. Contract Continuity — check contract exists each trading day entry→exit
    5. MFE/MAE Verification — replay daily prices for sample trades
    6. Slippage Impact — P&L under 4 slippage models
    7. Pipeline Funnel — unique ticker/date pairs by scanner
    8. Scanner Coverage — which scanners fired for which tickers/days
    9. Win Rate by Dimension — segmented analysis (7 standard + custom)
   10. Red Flag Checks — 10 anomaly checks
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median, quantiles
from typing import Optional

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.historical_data_provider import HistoricalDataProvider
from app.core.schemas import BacktestTrade

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ─── Helpers ────────────────────────────────────────────────────────────────

SEPARATOR = "=" * 80


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total > 0 else "0.0%"


def _stats(values: list[float]) -> dict[str, float]:
    """Compute min/max/mean/median/p25/p75 for a list of values."""
    if not values:
        return {"min": 0, "max": 0, "mean": 0, "median": 0, "p25": 0, "p75": 0}
    s = sorted(values)
    q = quantiles(s, n=4) if len(s) >= 2 else [s[0], s[0], s[0]]
    return {
        "min": s[0],
        "max": s[-1],
        "mean": sum(s) / len(s),
        "median": median(s),
        "p25": q[0],
        "p75": q[2] if len(q) > 2 else q[-1],
    }


def _next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _trading_days_between(start: date, end: date) -> list[date]:
    """Generate weekday dates from start+1 through end (inclusive)."""
    days = []
    d = _next_trading_day(start)
    while d <= end:
        days.append(d)
        d = _next_trading_day(d)
    return days


def _print_dist(label: str, st: dict[str, float]) -> None:
    print(f"  {label}: min={st['min']:.4f}  p25={st['p25']:.4f}  "
          f"median={st['median']:.4f}  p75={st['p75']:.4f}  max={st['max']:.4f}  "
          f"mean={st['mean']:.4f}")


# ─── Module 1: Trade Census ────────────────────────────────────────────────

def module_1_trade_census(trades: list[BacktestTrade]) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 1: TRADE CENSUS")
    print(SEPARATOR)

    print(f"\nTotal trades: {len(trades)}")

    # By scanner
    scanner_counts = Counter(t.scanner_type for t in trades)
    print("\nBy scanner:")
    for s, c in scanner_counts.most_common():
        print(f"  {s}: {c} ({_pct(c, len(trades))})")

    # By verdict
    verdict_counts = Counter(t.verdict for t in trades)
    print("\nBy verdict:")
    for v, c in verdict_counts.most_common():
        print(f"  {v}: {c} ({_pct(c, len(trades))})")

    # By exit reason
    exit_counts = Counter(t.exit_reason or "NONE" for t in trades)
    print("\nBy exit reason:")
    for e, c in exit_counts.most_common():
        print(f"  {e}: {c} ({_pct(c, len(trades))})")

    # By option type
    type_counts = Counter(t.option_type for t in trades)
    print("\nBy option type:")
    for ot, c in type_counts.most_common():
        print(f"  {ot}: {c} ({_pct(c, len(trades))})")

    # Distributions
    print("\nDistributions:")
    _print_dist("Entry price ($)", _stats([t.entry_price for t in trades]))
    exit_prices = [t.exit_price for t in trades if t.exit_price is not None]
    _print_dist("Exit price ($)", _stats(exit_prices))
    pnl_vals = [t.pnl_pct for t in trades if t.pnl_pct is not None]
    _print_dist("P&L (%)", _stats(pnl_vals))
    _print_dist("Combined score", _stats([t.combined_score for t in trades]))
    days_vals = [float(t.days_held) for t in trades if t.days_held is not None]
    _print_dist("Days held", _stats(days_vals))
    _print_dist("MFE (%)", _stats([t.mfe_pct for t in trades if t.mfe_pct is not None]))
    _print_dist("MAE (%)", _stats([t.mae_pct for t in trades if t.mae_pct is not None]))

    # Trades per day histogram
    day_counts = Counter(t.entry_date for t in trades)
    print("\nTrades per entry day:")
    for d in sorted(day_counts):
        bar = "#" * day_counts[d]
        print(f"  {d}: {day_counts[d]:3d} {bar}")


# ─── Module 2: Entry Pricing Verification ──────────────────────────────────

async def module_2_entry_pricing(
    trades: list[BacktestTrade],
    dp: HistoricalDataProvider,
    slippage_model: str,
    slippage_pct: float,
) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 2: ENTRY PRICING VERIFICATION")
    print(SEPARATOR)
    print(f"Slippage model: {slippage_model}, pct: {slippage_pct}")

    checked = 0
    matched = 0
    not_found = 0
    zero_bidask = 0
    mismatches: list[dict] = []

    for t in trades:
        entry_date_obj = date.fromisoformat(t.entry_date)
        chain = await dp.get_options_chain(
            t.ticker, as_of=entry_date_obj, min_dte=0, max_dte=365
        )

        target_type = "call" if t.option_type.lower() in ("call", "c") else "put"
        found = False

        for c in chain:
            details = c.get("details", {})
            c_strike = float(details.get("strike_price", 0) or 0)
            c_expiry = str(details.get("expiration_date", ""))
            c_type = str(details.get("contract_type", "")).lower()

            if (abs(c_strike - t.strike) < 0.01
                    and c_expiry == t.expiration_date
                    and c_type == target_type):
                found = True
                quote = c.get("last_quote", {})
                bid = float(quote.get("bid", 0) or 0) if isinstance(quote, dict) else 0
                ask = float(quote.get("ask", 0) or 0) if isinstance(quote, dict) else 0
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0

                if bid == 0 and ask == 0:
                    zero_bidask += 1

                # Expected entry based on slippage model
                if slippage_model == "ask_plus_pct" and ask > 0:
                    expected = ask * (1 + slippage_pct)
                elif slippage_model == "ask" and ask > 0:
                    expected = ask
                elif slippage_model == "mid" and mid > 0:
                    expected = mid
                else:
                    expected = None

                checked += 1
                if expected is not None and abs(t.entry_price - expected) < 0.02:
                    matched += 1
                elif expected is not None:
                    mismatches.append({
                        "ticker": t.ticker, "entry_date": t.entry_date,
                        "strike": t.strike, "type": t.option_type,
                        "stored_entry": t.entry_price,
                        "expected_entry": round(expected, 4),
                        "bid": bid, "ask": ask,
                        "diff": round(t.entry_price - expected, 4),
                    })
                break

        if not found:
            not_found += 1

    print(f"\nChecked: {checked}/{len(trades)}")
    print(f"Matched (within $0.02): {matched} ({_pct(matched, checked)})")
    print(f"Mismatches: {len(mismatches)}")
    print(f"Contract not found in chain: {not_found}")
    print(f"Zero bid/ask (synthetic pricing): {zero_bidask}")

    if mismatches:
        print("\nSample mismatches (first 10):")
        for m in mismatches[:10]:
            print(f"  {m['ticker']} {m['strike']} {m['type']} on {m['entry_date']}: "
                  f"stored={m['stored_entry']:.4f} expected={m['expected_entry']:.4f} "
                  f"diff={m['diff']:.4f} (bid={m['bid']:.2f} ask={m['ask']:.2f})")


# ─── Module 3: Exit Pricing Verification ───────────────────────────────────

async def module_3_exit_pricing(
    trades: list[BacktestTrade],
    dp: HistoricalDataProvider,
    slippage_pct: float,
) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 3: EXIT PRICING VERIFICATION")
    print(SEPARATOR)

    checked = 0
    price_matched = 0
    reason_matched = 0
    not_found = 0
    reason_issues: list[dict] = []

    closed = [t for t in trades if t.exit_date and t.exit_price is not None]

    for t in closed:
        exit_date_obj = date.fromisoformat(t.exit_date)
        chain = await dp.get_options_chain(
            t.ticker, as_of=exit_date_obj, min_dte=0, max_dte=365
        )

        target_type = "call" if t.option_type.lower() in ("call", "c") else "put"
        found = False

        for c in chain:
            details = c.get("details", {})
            c_strike = float(details.get("strike_price", 0) or 0)
            c_expiry = str(details.get("expiration_date", ""))
            c_type = str(details.get("contract_type", "")).lower()

            if (abs(c_strike - t.strike) < 0.01
                    and c_expiry == t.expiration_date
                    and c_type == target_type):
                found = True
                quote = c.get("last_quote", {})
                bid = float(quote.get("bid", 0) or 0) if isinstance(quote, dict) else 0
                ask = float(quote.get("ask", 0) or 0) if isinstance(quote, dict) else 0
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0

                if mid > 0:
                    expected_exit = mid * (1 - slippage_pct)
                    checked += 1
                    if abs(t.exit_price - expected_exit) < 0.02:
                        price_matched += 1
                break

        if not found:
            not_found += 1

        # Verify exit reason correctness
        if t.pnl_pct is not None and t.exit_reason:
            reason_ok = True
            if t.exit_reason == "PROFIT_TARGET" and t.pnl_pct < 45:
                reason_ok = False
            elif t.exit_reason == "STOP_LOSS" and t.pnl_pct > -45:
                reason_ok = False

            if reason_ok:
                reason_matched += 1
            else:
                reason_issues.append({
                    "ticker": t.ticker, "exit_reason": t.exit_reason,
                    "pnl_pct": t.pnl_pct, "entry": t.entry_price,
                    "exit": t.exit_price,
                })

    print(f"\nExit price checked: {checked}/{len(closed)}")
    print(f"Price matched (within $0.02): {price_matched} ({_pct(price_matched, checked)})")
    print(f"Contract not found: {not_found}")
    print(f"\nExit reason validation: {reason_matched}/{len(closed)} correct")

    if reason_issues:
        print(f"\nExit reason issues ({len(reason_issues)}):")
        for r in reason_issues[:10]:
            print(f"  {r['ticker']}: {r['exit_reason']} but pnl={r['pnl_pct']:.1f}% "
                  f"(entry={r['entry']:.4f} exit={r['exit']:.4f})")


# ─── Module 4: Contract Continuity ─────────────────────────────────────────

async def module_4_contract_continuity(
    trades: list[BacktestTrade],
    dp: HistoricalDataProvider,
) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 4: CONTRACT CONTINUITY CHECK")
    print(SEPARATOR)

    gap_trades: list[dict] = []
    checked = 0

    closed = [t for t in trades if t.exit_date]

    for t in closed:
        entry_d = date.fromisoformat(t.entry_date)
        exit_d = date.fromisoformat(t.exit_date)
        trading_days = _trading_days_between(entry_d, exit_d)

        if not trading_days:
            continue

        checked += 1
        target_type = "call" if t.option_type.lower() in ("call", "c") else "put"
        found_days = 0

        for day in trading_days:
            chain = await dp.get_options_chain(
                t.ticker, as_of=day, min_dte=0, max_dte=365
            )
            for c in chain:
                details = c.get("details", {})
                c_strike = float(details.get("strike_price", 0) or 0)
                c_expiry = str(details.get("expiration_date", ""))
                c_type = str(details.get("contract_type", "")).lower()

                if (abs(c_strike - t.strike) < 0.01
                        and c_expiry == t.expiration_date
                        and c_type == target_type):
                    found_days += 1
                    break

        total_days = len(trading_days)
        missing_pct = ((total_days - found_days) / total_days * 100) if total_days > 0 else 0

        if missing_pct > 20:
            gap_trades.append({
                "ticker": t.ticker, "strike": t.strike, "type": t.option_type,
                "entry": t.entry_date, "exit": t.exit_date,
                "total_days": total_days, "found_days": found_days,
                "missing_pct": round(missing_pct, 1),
            })

    print(f"\nChecked {checked} trades for contract continuity")
    print(f"Trades with >20% missing days: {len(gap_trades)}")

    if gap_trades:
        for g in gap_trades[:15]:
            print(f"  {g['ticker']} {g['strike']} {g['type']}: "
                  f"{g['found_days']}/{g['total_days']} days present "
                  f"({g['missing_pct']}% missing) [{g['entry']} → {g['exit']}]")


# ─── Module 5: MFE/MAE Verification ───────────────────────────────────────

async def module_5_mfe_mae_verification(
    trades: list[BacktestTrade],
    dp: HistoricalDataProvider,
    sample_size: int = 15,
) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 5: MFE/MAE VERIFICATION (sample)")
    print(SEPARATOR)

    closed = [t for t in trades if t.exit_date and t.mfe_pct is not None]
    sample = closed[:sample_size]

    checked = 0
    mfe_diffs: list[float] = []
    mae_diffs: list[float] = []

    for t in sample:
        entry_d = date.fromisoformat(t.entry_date)
        exit_d = date.fromisoformat(t.exit_date)
        trading_days = _trading_days_between(entry_d, exit_d)

        target_type = "call" if t.option_type.lower() in ("call", "c") else "put"
        peak = t.entry_price
        trough = t.entry_price

        for day in trading_days:
            chain = await dp.get_options_chain(
                t.ticker, as_of=day, min_dte=0, max_dte=365
            )
            for c in chain:
                details = c.get("details", {})
                c_strike = float(details.get("strike_price", 0) or 0)
                c_expiry = str(details.get("expiration_date", ""))
                c_type = str(details.get("contract_type", "")).lower()

                if (abs(c_strike - t.strike) < 0.01
                        and c_expiry == t.expiration_date
                        and c_type == target_type):
                    quote = c.get("last_quote", {})
                    bid = float(quote.get("bid", 0) or 0) if isinstance(quote, dict) else 0
                    ask = float(quote.get("ask", 0) or 0) if isinstance(quote, dict) else 0
                    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0

                    if mid <= 0:
                        day_data = c.get("day", {})
                        close = float(
                            day_data.get("close", 0) or 0
                        ) if isinstance(day_data, dict) else 0
                        mid = close

                    if mid > 0:
                        peak = max(peak, mid)
                        trough = min(trough, mid)
                    break

        if t.entry_price > 0:
            computed_mfe = (peak - t.entry_price) / t.entry_price * 100
            computed_mae = (t.entry_price - trough) / t.entry_price * 100
            checked += 1

            mfe_diff = abs(computed_mfe - (t.mfe_pct or 0))
            mae_diff = abs(computed_mae - (t.mae_pct or 0))
            mfe_diffs.append(mfe_diff)
            mae_diffs.append(mae_diff)

            if mfe_diff > 1.0 or mae_diff > 1.0:
                print(f"  {t.ticker} {t.strike} {t.option_type} [{t.entry_date}→{t.exit_date}]:")
                print(f"    MFE: stored={t.mfe_pct:.2f}% "
                      f"computed={computed_mfe:.2f}% diff={mfe_diff:.2f}%")
                print(f"    MAE: stored={t.mae_pct:.2f}% "
                      f"computed={computed_mae:.2f}% diff={mae_diff:.2f}%")

    print(f"\nVerified: {checked}/{len(sample)} trades")
    if mfe_diffs:
        avg_mfe_diff = sum(mfe_diffs) / len(mfe_diffs)
        avg_mae_diff = sum(mae_diffs) / len(mae_diffs)
        print(f"Avg MFE difference: {avg_mfe_diff:.2f}% (tolerance: 0.5%)")
        print(f"Avg MAE difference: {avg_mae_diff:.2f}% (tolerance: 0.5%)")
        within_tol = sum(1 for d in mfe_diffs if d <= 0.5)
        print(f"MFE within tolerance: {within_tol}/{len(mfe_diffs)}")


# ─── Module 6: Slippage Impact Analysis ───────────────────────────────────

async def module_6_slippage_impact(
    trades: list[BacktestTrade],
    dp: HistoricalDataProvider,
) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 6: SLIPPAGE IMPACT ANALYSIS")
    print(SEPARATOR)

    models = {
        "ask_plus_pct (current)": {"entry": "ask_plus_5", "exit": "mid_minus_5"},
        "mid/mid (zero slippage)": {"entry": "mid", "exit": "mid"},
        "ask/bid (spread only)": {"entry": "ask", "exit": "bid"},
        "symmetric 2%": {"entry": "mid_plus_2", "exit": "mid_minus_2"},
    }

    # Collect raw bid/ask/mid for each trade at entry and exit
    model_pnls: dict[str, list[float]] = {m: [] for m in models}

    for t in trades:
        if not t.exit_date or t.exit_price is None:
            continue

        entry_d = date.fromisoformat(t.entry_date)
        exit_d = date.fromisoformat(t.exit_date)
        target_type = "call" if t.option_type.lower() in ("call", "c") else "put"

        # Get entry-day raw prices
        entry_chain = await dp.get_options_chain(t.ticker, as_of=entry_d, min_dte=0, max_dte=365)
        entry_bid = entry_ask = entry_mid = 0.0
        for c in entry_chain:
            details = c.get("details", {})
            if (abs(float(details.get("strike_price", 0) or 0) - t.strike) < 0.01
                    and str(details.get("expiration_date", "")) == t.expiration_date
                    and str(details.get("contract_type", "")).lower() == target_type):
                quote = c.get("last_quote", {})
                entry_bid = float(quote.get("bid", 0) or 0) if isinstance(quote, dict) else 0
                entry_ask = float(quote.get("ask", 0) or 0) if isinstance(quote, dict) else 0
                entry_mid = (entry_bid + entry_ask) / 2 if entry_bid > 0 and entry_ask > 0 else 0
                break

        # Get exit-day raw prices
        exit_chain = await dp.get_options_chain(t.ticker, as_of=exit_d, min_dte=0, max_dte=365)
        exit_bid = exit_ask = exit_mid = 0.0
        for c in exit_chain:
            details = c.get("details", {})
            if (abs(float(details.get("strike_price", 0) or 0) - t.strike) < 0.01
                    and str(details.get("expiration_date", "")) == t.expiration_date
                    and str(details.get("contract_type", "")).lower() == target_type):
                quote = c.get("last_quote", {})
                exit_bid = float(quote.get("bid", 0) or 0) if isinstance(quote, dict) else 0
                exit_ask = float(quote.get("ask", 0) or 0) if isinstance(quote, dict) else 0
                exit_mid = (exit_bid + exit_ask) / 2 if exit_bid > 0 and exit_ask > 0 else 0
                break

        # Skip if no usable prices
        if entry_mid == 0 and entry_ask == 0:
            continue

        # Compute P&L for each model
        for model_name, rules in models.items():
            # Entry price
            if rules["entry"] == "ask_plus_5":
                ep = entry_ask * 1.05 if entry_ask > 0 else None
            elif rules["entry"] == "mid":
                ep = entry_mid if entry_mid > 0 else None
            elif rules["entry"] == "ask":
                ep = entry_ask if entry_ask > 0 else None
            elif rules["entry"] == "mid_plus_2":
                ep = entry_mid * 1.02 if entry_mid > 0 else None
            else:
                ep = None

            # Exit price
            if rules["exit"] == "mid_minus_5":
                xp = exit_mid * 0.95 if exit_mid > 0 else None
            elif rules["exit"] == "mid":
                xp = exit_mid if exit_mid > 0 else None
            elif rules["exit"] == "bid":
                xp = exit_bid if exit_bid > 0 else None
            elif rules["exit"] == "mid_minus_2":
                xp = exit_mid * 0.98 if exit_mid > 0 else None
            else:
                xp = None

            if ep and ep > 0 and xp is not None:
                pnl_pct = (xp - ep) / ep * 100
                model_pnls[model_name].append(pnl_pct)

    # Print results
    hdr = (f"{'Model':<30} {'Trades':>7} {'Win Rate':>10} "
           f"{'Avg P&L%':>10} {'Net P&L%':>10} {'PF':>8}")
    print(f"\n{hdr}")
    print("-" * 80)

    for model_name, pnls in model_pnls.items():
        if not pnls:
            print(f"{model_name:<30} {'N/A':>7}")
            continue
        winners = sum(1 for p in pnls if p > 0)
        wr = winners / len(pnls) * 100
        avg_pnl = sum(pnls) / len(pnls)
        total = sum(pnls)
        gross_w = sum(p for p in pnls if p > 0)
        gross_l = abs(sum(p for p in pnls if p < 0))
        pf = gross_w / gross_l if gross_l > 0 else float("inf")
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"{model_name:<30} {len(pnls):>7} {wr:>9.1f}% "
              f"{avg_pnl:>9.2f}% {total:>9.1f}% {pf_str:>8}")

    # Key question
    if model_pnls["mid/mid (zero slippage)"] and model_pnls["ask_plus_pct (current)"]:
        mid_net = sum(model_pnls["mid/mid (zero slippage)"])
        cur_net = sum(model_pnls["ask_plus_pct (current)"])
        print(f"\n>>> KEY FINDING: mid/mid net={mid_net:.1f}% vs ask_plus_pct net={cur_net:.1f}%")
        if mid_net > 0 > cur_net:
            print(">>> DIAGNOSIS: Pipeline finds edge but slippage destroys it. "
                  "Consider switching to ask/bid model.")
        elif mid_net > 0 and cur_net > 0:
            print(">>> DIAGNOSIS: Pipeline finds genuine edge even after slippage.")
        elif mid_net < 0:
            print(">>> DIAGNOSIS: Pipeline is selecting losing trades regardless of slippage.")


# ─── Module 7: Pipeline Funnel ────────────────────────────────────────────

def module_7_pipeline_funnel(trades: list[BacktestTrade]) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 7: PIPELINE FUNNEL")
    print(SEPARATOR)

    # Reconstruct from trade metadata
    ticker_day_scanner: dict[str, set] = defaultdict(set)  # scanner → set of (ticker, date)
    for t in trades:
        ticker_day_scanner[t.scanner_type].add((t.ticker, t.entry_date))

    print("\nUnique ticker/date pairs producing trades:")
    total_unique = set()
    for scanner, pairs in sorted(ticker_day_scanner.items()):
        total_unique |= pairs
        n_trades = sum(1 for t in trades if t.scanner_type == scanner)
        print(f"  {scanner}: {len(pairs)} unique ticker/date pairs "
              f"→ {n_trades} trades")

    print(f"  Total unique: {len(total_unique)} ticker/date pairs → {len(trades)} trades")

    # Trades per ticker
    ticker_counts = Counter(t.ticker for t in trades)
    print("\nTrades per ticker:")
    for ticker, count in ticker_counts.most_common():
        print(f"  {ticker}: {count}")


# ─── Module 8: Scanner Coverage ───────────────────────────────────────────

def module_8_scanner_coverage(trades: list[BacktestTrade]) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 8: SCANNER COVERAGE")
    print(SEPARATOR)

    # Scanner × ticker matrix
    scanner_tickers: dict[str, set] = defaultdict(set)
    scanner_days: dict[str, set] = defaultdict(set)
    all_tickers = set()
    all_days = set()

    for t in trades:
        scanner_tickers[t.scanner_type].add(t.ticker)
        scanner_days[t.scanner_type].add(t.entry_date)
        all_tickers.add(t.ticker)
        all_days.add(t.entry_date)

    print(f"\nTotal active tickers: {len(all_tickers)}")
    print(f"Total active days: {len(all_days)}")

    for scanner in sorted(scanner_tickers):
        tickers = scanner_tickers[scanner]
        days = scanner_days[scanner]
        print(f"\n  {scanner}:")
        print(f"    Tickers ({len(tickers)}): {', '.join(sorted(tickers))}")
        print(f"    Days ({len(days)}): {', '.join(sorted(days))}")

    # Dead tickers (in universe but zero trades)
    universe = {
        "AAPL", "MSFT", "TSLA", "AMZN", "NVDA", "META", "GOOGL", "SPY", "QQQ",
        "AMD", "NFLX", "CRM", "UBER", "BA", "JPM", "V", "MA", "WMT", "DIS", "INTC",
    }
    dead_tickers = universe - all_tickers
    if dead_tickers:
        print(f"\n  DEAD TICKERS (zero trades): {', '.join(sorted(dead_tickers))}")

    # Single-scanner dominance check
    if len(scanner_tickers) == 1:
        print("\n  WARNING: Only one scanner is producing trades!")


# ─── Module 9: Win Rate by Dimension ──────────────────────────────────────

def module_9_win_rate_by_dimension(trades: list[BacktestTrade]) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 9: WIN RATE BY DIMENSION")
    print(SEPARATOR)

    from app.backtest.segments import analyze_all_segments

    all_segments = analyze_all_segments(trades)

    for seg_type, segments in all_segments.items():
        if not segments:
            continue
        print(f"\n  {seg_type.upper()}:")
        hdr = (f"  {'Segment':<20} {'Trades':>7} {'Win%':>7} "
               f"{'Avg Ret%':>10} {'PF':>8} {'Sharpe':>8}")
        print(hdr)
        print(f"  {'-'*60}")
        for s in segments:
            pf = s.get("profit_factor")
            pf_str = f"{pf:.2f}" if pf is not None else "N/A"
            sharpe = s.get("sharpe")
            sharpe_str = f"{sharpe:.2f}" if sharpe is not None else "N/A"
            print(f"  {s['segment']:<20} {s['trades']:>7} {s['win_rate']:>6.1f}% "
                  f"{s['avg_return_pct']:>9.2f}% {pf_str:>8} {sharpe_str:>8}")

    # Custom: by option_type
    print("\n  OPTION TYPE:")
    for otype in ("CALL", "PUT"):
        subset = [t for t in trades if t.option_type == otype and t.pnl_pct is not None]
        if subset:
            winners = sum(1 for t in subset if (t.pnl_dollars or 0) > 0)
            wr = winners / len(subset) * 100
            avg_ret = sum(t.pnl_pct or 0 for t in subset) / len(subset)
            print(f"  {otype:<20} {len(subset):>7} {wr:>6.1f}% {avg_ret:>9.2f}%")

    # Custom: by entry price bucket
    print("\n  ENTRY PRICE BUCKET:")
    buckets = [
        ("$0-2", 0, 2), ("$2-5", 2, 5), ("$5-10", 5, 10),
        ("$10-25", 10, 25), ("$25+", 25, 9999),
    ]
    for label, lo, hi in buckets:
        subset = [t for t in trades if lo <= t.entry_price < hi and t.pnl_pct is not None]
        if subset:
            winners = sum(1 for t in subset if (t.pnl_dollars or 0) > 0)
            wr = winners / len(subset) * 100
            avg_ret = sum(t.pnl_pct or 0 for t in subset) / len(subset)
            print(f"  {label:<20} {len(subset):>7} {wr:>6.1f}% {avg_ret:>9.2f}%")


# ─── Module 10: Red Flag Checks ───────────────────────────────────────────

def module_10_red_flags(trades: list[BacktestTrade]) -> None:
    print(f"\n{SEPARATOR}")
    print("MODULE 10: RED FLAG CHECKS")
    print(SEPARATOR)

    flags: dict[str, list[str]] = {
        "entry_equals_exit": [],
        "zero_days_held": [],
        "exact_limit_hits": [],
        "duplicate_trades": [],
        "stock_price_entries": [],
        "negative_prices": [],
        "exit_before_entry": [],
        "mfe_negative": [],
        "mae_negative": [],
        "peak_below_entry": [],
    }

    seen = set()
    for t in trades:
        tid = f"{t.ticker}|{t.strike}|{t.expiration_date}|{t.entry_date}"
        desc = f"{t.ticker} {t.strike} {t.option_type} {t.entry_date}"

        # 1. Entry = exit price
        if t.exit_price is not None and abs(t.entry_price - t.exit_price) < 0.001:
            flags["entry_equals_exit"].append(desc)

        # 2. Zero days held
        if t.days_held is not None and t.days_held == 0:
            flags["zero_days_held"].append(desc)

        # 3. Exact limit hits (within 0.1%)
        if t.pnl_pct is not None:
            if abs(t.pnl_pct - 50.0) < 0.1 or abs(t.pnl_pct + 50.0) < 0.1:
                flags["exact_limit_hits"].append(f"{desc} pnl={t.pnl_pct:.2f}%")

        # 4. Duplicate trades
        if tid in seen:
            flags["duplicate_trades"].append(desc)
        seen.add(tid)

        # 5. Stock-price entries (>$50 is suspicious for option premiums)
        if t.entry_price > 50:
            flags["stock_price_entries"].append(f"{desc} entry=${t.entry_price:.2f}")

        # 6. Negative prices
        if t.entry_price < 0 or (t.exit_price is not None and t.exit_price < 0):
            flags["negative_prices"].append(desc)

        # 7. Exit before entry
        if t.exit_date and t.exit_date < t.entry_date:
            flags["exit_before_entry"].append(f"{desc} exit={t.exit_date}")

        # 8. MFE < 0 (impossible)
        if t.mfe_pct is not None and t.mfe_pct < 0:
            flags["mfe_negative"].append(f"{desc} mfe={t.mfe_pct:.2f}%")

        # 9. MAE < 0 (impossible)
        if t.mae_pct is not None and t.mae_pct < 0:
            flags["mae_negative"].append(f"{desc} mae={t.mae_pct:.2f}%")

        # 10. Peak price < entry price (impossible)
        if t.peak_price is not None and t.peak_price < t.entry_price - 0.001:
            flags["peak_below_entry"].append(
                f"{desc} peak={t.peak_price:.4f} entry={t.entry_price:.4f}")

    total_flags = sum(len(v) for v in flags.values())
    print(f"\nTotal red flags: {total_flags}")

    for check_name, items in flags.items():
        status = f"FAIL ({len(items)})" if items else "PASS"
        print(f"\n  {check_name}: {status}")
        for item in items[:5]:
            print(f"    - {item}")
        if len(items) > 5:
            print(f"    ... and {len(items) - 5} more")


# ─── Main ──────────────────────────────────────────────────────────────────

async def load_trades_from_api(run_id: str, api_base: str) -> tuple[list[BacktestTrade], dict]:
    """Load trades and run config from the production API."""
    import json as _json
    import urllib.request

    # Get run config
    req = urllib.request.Request(f"{api_base}/api/backtest/runs/{run_id}")
    with urllib.request.urlopen(req) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Failed to get run: {resp.status}")
        run_data = _json.loads(resp.read().decode())

    # Get trades
    req = urllib.request.Request(
        f"{api_base}/api/backtest/runs/{run_id}/trades?limit=1000"
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Failed to get trades: {resp.status}")
        data = _json.loads(resp.read().decode())
        all_trades = data.get("trades", [])

    trades = [BacktestTrade(**t) for t in all_trades]
    return trades, run_data


async def load_trades_from_dynamo(run_id: str) -> tuple[list[BacktestTrade], dict]:
    """Load trades and run config from DynamoDB directly."""
    from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

    run_data = await BacktestRunTable.get(run_id)
    if not run_data:
        raise RuntimeError(f"Run {run_id} not found in DynamoDB")

    trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=5000)
    trades = [BacktestTrade(**td) for td in trade_dicts]
    return trades, run_data


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest Diagnostic Analyzer")
    parser.add_argument("--run-id", required=True, help="Backtest run ID")
    parser.add_argument("--s3-bucket", default="oss-dev-backtest-982534389101",
                        help="S3 bucket for historical data")
    parser.add_argument("--api-base", default="https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com",
                        help="Production API base URL")
    parser.add_argument("--source", choices=["api", "dynamo"], default="api",
                        help="Data source: api (HTTP) or dynamo (direct DynamoDB)")
    parser.add_argument("--modules", type=str, default="all",
                        help="Comma-separated module numbers to run (e.g., '1,2,6,10') or 'all'")
    parser.add_argument("--skip-s3", action="store_true",
                        help="Skip modules requiring S3 access (2,3,4,5,6)")
    args = parser.parse_args()

    print(f"\n{'#' * 80}")
    print("# BACKTEST DIAGNOSTIC ANALYSIS")
    print(f"# Run ID: {args.run_id}")
    print(f"# S3 Bucket: {args.s3_bucket}")
    print(f"# Source: {args.source}")
    print(f"{'#' * 80}")

    # Load data
    print("\nLoading trades...")
    if args.source == "api":
        trades, run_data = await load_trades_from_api(args.run_id, args.api_base)
    else:
        trades, run_data = await load_trades_from_dynamo(args.run_id)

    print(f"Loaded {len(trades)} trades")
    if not trades:
        print("ERROR: No trades found. Cannot run analysis.")
        return

    config = run_data.get("config", {})
    slippage_model = config.get("slippage_model", "ask_plus_pct")
    slippage_pct = config.get("slippage_pct", 0.05)

    # Determine which modules to run
    if args.modules == "all":
        modules_to_run = set(range(1, 11))
    else:
        modules_to_run = {int(m.strip()) for m in args.modules.split(",")}

    s3_modules = {2, 3, 4, 5, 6}
    if args.skip_s3:
        modules_to_run -= s3_modules

    # Create data provider for S3-backed modules
    dp: Optional[HistoricalDataProvider] = None
    if modules_to_run & s3_modules:
        dp = HistoricalDataProvider(s3_bucket=args.s3_bucket)

    # Run modules
    if 1 in modules_to_run:
        module_1_trade_census(trades)

    if 2 in modules_to_run and dp:
        await module_2_entry_pricing(trades, dp, slippage_model, slippage_pct)

    if 3 in modules_to_run and dp:
        await module_3_exit_pricing(trades, dp, slippage_pct)

    if 4 in modules_to_run and dp:
        await module_4_contract_continuity(trades, dp)

    if 5 in modules_to_run and dp:
        await module_5_mfe_mae_verification(trades, dp)

    if 6 in modules_to_run and dp:
        await module_6_slippage_impact(trades, dp)

    if 7 in modules_to_run:
        module_7_pipeline_funnel(trades)

    if 8 in modules_to_run:
        module_8_scanner_coverage(trades)

    if 9 in modules_to_run:
        module_9_win_rate_by_dimension(trades)

    if 10 in modules_to_run:
        module_10_red_flags(trades)

    # Summary metrics
    print(f"\n{SEPARATOR}")
    print("SUMMARY METRICS")
    print(SEPARATOR)
    from app.backtest.metrics import calculate_metrics
    metrics = calculate_metrics(trades, starting_capital=config.get("starting_capital", 10_000))
    for k, v in metrics.items():
        if k == "exit_reasons":
            print(f"  {k}:")
            for reason, count in v.items():
                print(f"    {reason}: {count}")
        else:
            print(f"  {k}: {v}")

    print(f"\n{'#' * 80}")
    print("# ANALYSIS COMPLETE")
    print(f"{'#' * 80}\n")


if __name__ == "__main__":
    asyncio.run(main())
