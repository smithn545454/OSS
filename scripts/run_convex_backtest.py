#!/usr/bin/env python3
"""Phase 8 Convex Mode backtest driver.

Runs the four-stage Convex pipeline against historical data over a
trading-day window (e.g. Apr 2025 → Apr 2026), resolves each
CONVEX_APPROVE candidate's eventual outcome by walking forward through
PriceHistory + S3 chain parquet, and emits §11 acceptance metrics to a
JSON file.

Survivorship-bias caveat: this driver uses the *current* universe
snapshot for every backtest day. We deliberately did not rebuild a
kinetic Apr-2025 universe — accept that delisted/acquired names from the
backtest period are absent. This biases hit rate slightly upward (we
trade only survivors) but the test is still useful for relative tier
comparison and gross expectancy. See docs/convex-mode-impact-report.md
§14 for the trade-off discussion.

Usage:
    AWS_REGION=us-west-1 PYTHONPATH=backend \\
        python scripts/run_convex_backtest.py \\
            --start 2025-04-28 --end 2026-04-25 \\
            --output baselines/2026-04-27-convex-phase8-backtest.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.convex import (  # noqa: E402
    ConvexBacktestConfig,
    HistoricalProviderBundle,
    HistoricalProviders,
    report_to_dict,
    run_convex_backtest,
)
from app.core.schemas import (  # noqa: E402
    ConvexConfig,
    ConvexUniverseEntry,
    ConvexUniverseSnapshot,
)
from app.core.watchlist import DEFAULT_WATCHLIST  # noqa: E402

logger = logging.getLogger("convex-backtest")


async def _load_universe(args: argparse.Namespace) -> ConvexUniverseSnapshot:
    """Build universe from CLI: 'default' (DEFAULT_WATCHLIST) or 'sp500' (DDB scan)."""
    source = args.universe
    if source == "default":
        entries = [ConvexUniverseEntry(ticker=t) for t in DEFAULT_WATCHLIST]
        label = "DEFAULT_WATCHLIST"
    elif source == "sp500":
        import boto3

        region = os.environ.get("AWS_REGION", "us-west-1")
        table = boto3.resource("dynamodb", region_name=region).Table(
            "oss-dev-sp500-tickers"
        )
        items: list[dict] = []
        last_key = None
        while True:
            kwargs = {
                "FilterExpression": "has_options = :t AND is_active = :t",
                "ExpressionAttributeValues": {":t": True},
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            resp = table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
        items.sort(key=lambda i: i["ticker"])
        if args.universe_limit:
            # Deterministic stride sampling for diversity across alphabet.
            stride = max(1, len(items) // args.universe_limit)
            items = items[::stride][: args.universe_limit]
        entries = [
            ConvexUniverseEntry(ticker=i["ticker"], sector=i.get("sector"))
            for i in items
        ]
        label = f"oss-dev-sp500-tickers (filtered, limit={args.universe_limit or 'none'})"
    else:
        raise ValueError(f"Unknown --universe: {source}")

    snapshot = ConvexUniverseSnapshot(
        snapshot_date=args.start,
        policy_version="phase8-backtest",
        tickers=entries,
        total_count=len(entries),
    )
    logger.info("Built universe from %s: %d tickers", label, len(entries))
    return snapshot


async def _run(args: argparse.Namespace) -> int:
    universe = await _load_universe(args)

    bundle = HistoricalProviderBundle.build(s3_bucket=args.s3_bucket)
    providers = HistoricalProviders(
        stage2=bundle.stage2,
        stage3=bundle.stage3,
        stage4=bundle.stage4,
        future_prices=bundle.future_prices,
        option_prices=bundle.option_prices,
    )

    # Backtest must run regardless of the production kill switch. Apply
    # any tuning knobs from CLI flags.
    convex_config = ConvexConfig(
        enabled=True,
        vol_iv_rank_max=args.vol_iv_rank_max,
        tier_b_stage2_strength_min=args.tier_b_stage2_min,
        tier_b_stage3_composite_min=args.tier_b_stage3_min,
    )
    config = ConvexBacktestConfig(
        start_date=args.start,
        end_date=args.end,
        convex_config=convex_config,
        universe_snapshot=universe,
        profit_target_pct=args.profit_target_pct,
        stop_loss_pct=args.stop_loss_pct,
        max_holding_days=args.max_holding_days,
    )

    logger.info(
        "Starting backtest: %s → %s (%d universe tickers, S3 bucket=%s)",
        config.start_date,
        config.end_date,
        config.universe_snapshot.total_count,
        args.s3_bucket,
    )
    started = datetime.now(timezone.utc)

    trades, report = await run_convex_backtest(config, providers)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    logger.info(
        "Backtest finished in %.1fs: %d trades, hit_rate=%.1f%%, expectancy=%.2f%%, passes=%s",
        elapsed,
        report.total_trades,
        report.hit_rate_pct,
        report.expectancy_pct,
        report.passes_acceptance(),
    )

    output = {
        "metadata": {
            "start_date": config.start_date,
            "end_date": config.end_date,
            "universe_size": config.universe_snapshot.total_count,
            "universe_source": args.universe,
            "tuning": {
                "profit_target_pct": args.profit_target_pct,
                "stop_loss_pct": args.stop_loss_pct,
                "max_holding_days": args.max_holding_days,
                "vol_iv_rank_max": args.vol_iv_rank_max,
                "tier_b_stage2_strength_min": args.tier_b_stage2_min,
                "tier_b_stage3_composite_min": args.tier_b_stage3_min,
            },
            "started_at": started.isoformat(),
            "elapsed_seconds": elapsed,
            "survivorship_caveat": (
                "Snapshot is current-date — delisted/acquired tickers from "
                "the backtest period are absent. Hit rate may be biased up."
            ),
        },
        "validation_report": report_to_dict(report),
        "trades": [asdict(t) for t in trades],
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, default=str))
    logger.info("Wrote %d trades + report to %s", len(trades), out_path)

    return 0 if report.passes_acceptance() else 1


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument(
        "--output",
        required=True,
        help="Output JSON path (e.g. baselines/2026-04-27-convex-phase8-backtest.json)",
    )
    p.add_argument(
        "--universe",
        choices=("default", "sp500"),
        default="default",
        help="Ticker universe: default (DEFAULT_WATCHLIST) or sp500 (DDB scan)",
    )
    p.add_argument(
        "--universe-limit",
        type=int,
        default=None,
        help="If --universe sp500, deterministic stride-sampling cap on universe size",
    )
    p.add_argument(
        "--s3-bucket",
        default=os.environ.get(
            "CONVEX_BACKTEST_BUCKET", "oss-dev-backtest-982534389101"
        ),
        help="S3 bucket holding options-chains/ and stock-ohlcv/ parquets",
    )
    # Backtest exit-rule tuning
    p.add_argument("--profit-target-pct", type=float, default=50.0)
    p.add_argument("--stop-loss-pct", type=float, default=50.0)
    p.add_argument("--max-holding-days", type=int, default=30)
    # ConvexConfig tuning
    p.add_argument("--vol-iv-rank-max", type=int, default=40)
    p.add_argument("--tier-b-stage2-min", type=float, default=0.50)
    p.add_argument("--tier-b-stage3-min", type=float, default=0.40)
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
