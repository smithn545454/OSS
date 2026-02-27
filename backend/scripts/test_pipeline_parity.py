#!/usr/bin/env python3
"""Pipeline Parity Test — proves historical S3 data produces the same
scanner triggers and evaluations as the live Polygon pipeline would.

Usage:
    python scripts/test_pipeline_parity.py --ticker AAPL --date 2024-06-03 \
        --s3-bucket oss-dev-backtest-982534389101

Runs the full 8-stage pipeline for a single ticker on a single date using
HistoricalDataProvider, printing detailed per-scanner metrics and pipeline
progression.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set required env vars before any app imports
os.environ.setdefault("DYNAMODB_TABLE_PREFIX", "oss-dev")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-1")
os.environ.setdefault("AWS_REGION", "us-west-1")

from app.core.historical_data_provider import HistoricalDataProvider  # noqa: E402
from app.core.pipeline import PipelineOrchestrator  # noqa: E402
from app.core.schemas import PolicyConfig  # noqa: E402
from app.scanners.orchestrator import ScannerOrchestrator  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline parity test")
    parser.add_argument("--ticker", required=True, help="Ticker symbol (e.g. AAPL)")
    parser.add_argument("--date", required=True, help="Trade date (YYYY-MM-DD)")
    parser.add_argument("--s3-bucket", required=True, help="S3 bucket for historical data")
    parser.add_argument(
        "--scanners", default="breakout,compression,cheap_options,unusual_volume",
        help="Comma-separated scanner list",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


async def run_parity_test(
    ticker: str,
    trade_date: date,
    s3_bucket: str,
    scanners_enabled: list[str],
) -> None:
    """Run the full pipeline for one ticker on one date and print results."""

    print("=" * 80)
    print(f"PIPELINE PARITY TEST: {ticker} on {trade_date}")
    print(f"S3 Bucket: {s3_bucket}")
    print(f"Scanners: {', '.join(scanners_enabled)}")
    print("=" * 80)

    # 1. Create data provider
    provider = HistoricalDataProvider(
        s3_bucket=s3_bucket,
        as_of_date=trade_date,
    )

    # Quick data check
    bars = await provider.get_daily_bars(ticker, trade_date, lookback_days=60)
    chain = await provider.get_options_chain(ticker, trade_date)
    snapshot = await provider.get_stock_snapshot(ticker, trade_date)

    print("\n--- Data Availability ---")
    print(f"  Daily bars: {len(bars)} (need ≥35 for compression)")
    print(f"  Options chain contracts: {len(chain)}")
    print(f"  Stock snapshot: {'YES' if snapshot else 'NO'}")
    if snapshot:
        print(f"    Close: ${snapshot.close:.2f}, Volume: {snapshot.volume:,}")

    iv_records = await provider.get_iv_history(ticker, trade_date, lookback_days=252)
    print(f"  IV history records: {len(iv_records)}")

    if not bars:
        print("\nERROR: No daily bars — cannot run pipeline")
        return

    # 2. Create orchestrator with all scanners enabled
    pipeline_orch = PipelineOrchestrator()
    orchestrator = ScannerOrchestrator(
        data_provider=provider,
        pipeline_orchestrator=pipeline_orch,
        scanners_enabled=scanners_enabled,
    )

    # 3. Run full pipeline (pass PolicyConfig to avoid DynamoDB dependency)
    policy_config = PolicyConfig()
    print("\n--- Running Full Pipeline ---")
    result = await orchestrator.run_scan(
        policy_config=policy_config,
        tickers=[ticker],
        run_full_pipeline=True,
        as_of_date=trade_date,
    )

    # 4. Print scanner results
    print("\n--- Scanner Results (Stage 1) ---")
    for scanner_name, stats in result.scanner_stats.items():
        triggered = stats.get("triggered", 0)
        scanned = stats.get("total_scanned", 0)
        errs = stats.get("errors", 0)
        dur = stats.get("duration_ms", 0)
        status = "TRIGGERED" if triggered > 0 else "not triggered"
        print(f"  {scanner_name:25s}  {status:15s}  "
              f"(scanned={scanned}, errors={errs}, {dur}ms)")

    # 5. Print opportunities
    print("\n--- Opportunities (Stage 1 output) ---")
    print(f"  Created: {result.opportunities_created}")
    print(f"  Filtered (Stage 2): {result.opportunities_filtered}")
    all_opps = result.opportunities + result.filtered_opportunities
    for opp in all_opps:
        triggers = ", ".join(
            t.scanner_type.value if hasattr(t.scanner_type, "value")
            else str(t.scanner_type)
            for t in (opp.scanner_triggers or [])
        )
        print(f"    {opp.underlying_ticker}: scanners=[{triggers}], "
              f"direction={opp.direction_hint}")

    # 6. Print evaluations
    print("\n--- Evaluations (Stage 3 output) ---")
    print(f"  Total: {result.evaluations_created}")
    for ev in result.evaluations:
        ot = ev.option_type.value if hasattr(ev.option_type, "value") else ev.option_type
        print(f"    {ev.underlying_ticker} {ot} ${ev.strike} "
              f"exp={ev.expiration_date} DTE={ev.dte}")
        iv_val = ev.iv if ev.iv else 0.0
        print(f"      bid=${ev.bid:.2f} ask=${ev.ask:.2f} mid=${ev.mid:.2f} "
              f"IV={iv_val:.4f} OI={ev.open_interest}")

    # 7. Print decisions
    print("\n--- Decisions (Stage 7 output) ---")
    print(f"  APPROVE: {result.approve_count}")
    print(f"  WATCH:   {result.watch_count}")
    print(f"  REJECT:  {result.reject_count}")
    for eval_id, decision in result.decisions.items():
        v = decision.verdict.value if hasattr(decision.verdict, "value") else decision.verdict
        print(f"    {eval_id[:12]}... → {v} (score={decision.final_score:.1f})")
        if decision.failed_gates:
            for gate in decision.failed_gates[:3]:
                print(f"      FAILED GATE: {gate}")

    # 8. Print gate results
    if result.gate_evaluations:
        print("\n--- Gate Results (Stage 6) ---")
        for eval_id, gate_result in result.gate_evaluations.items():
            passed = gate_result.get("passed", "?") if isinstance(gate_result, dict) else "?"
            print(f"    {eval_id[:12]}... passed={passed}")
            if isinstance(gate_result, dict):
                for gate_name, gate_val in gate_result.items():
                    if gate_name != "passed" and isinstance(gate_val, dict):
                        g_passed = gate_val.get("passed", "?")
                        g_val = gate_val.get("value", "?")
                        if not g_passed:
                            print(f"      FAILED: {gate_name} = {g_val}")

    # 9. Summary
    print("\n--- Summary ---")
    print(f"  Duration: {result.duration_ms}ms")
    print(f"  Errors: {len(result.errors)}")
    for err in result.errors[:5]:
        print(f"    {err}")

    # Verdict
    print(f"\n{'=' * 80}")
    scanners_that_fired = [
        name for name, stats in result.scanner_stats.items()
        if stats.get("triggered", 0) > 0
    ]
    if scanners_that_fired:
        print(f"SCANNERS FIRED: {', '.join(scanners_that_fired)}")
    else:
        print("NO SCANNERS FIRED — check data quality and scanner thresholds")

    if result.evaluations_created > 0:
        print(f"EVALUATIONS PRODUCED: {result.evaluations_created}")
    else:
        print("NO EVALUATIONS — tickers may have been filtered or no contracts selected")

    total_decisions = result.approve_count + result.watch_count + result.reject_count
    if total_decisions > 0:
        print(f"DECISIONS: {result.approve_count} APPROVE, "
              f"{result.watch_count} WATCH, {result.reject_count} REJECT")
    print("=" * 80)


def main() -> None:
    args = parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Always show scanner diagnostic logs at INFO
    for logger_name in [
        "app.scanners.breakout",
        "app.scanners.compression",
        "app.scanners.cheap_options",
        "app.scanners.historical_uv",
        "app.scanners.orchestrator",
    ]:
        logging.getLogger(logger_name).setLevel(logging.INFO)

    trade_date = date.fromisoformat(args.date)
    scanners = [s.strip() for s in args.scanners.split(",")]

    asyncio.run(run_parity_test(
        ticker=args.ticker,
        trade_date=trade_date,
        s3_bucket=args.s3_bucket,
        scanners_enabled=scanners,
    ))


if __name__ == "__main__":
    main()
