#!/usr/bin/env python3
"""Seed the default policy from OSS Requirements Appendix B.

Usage:
    python seed_default_policy.py [--endpoint URL] [--activate]

Example (local development):
    python seed_default_policy.py --endpoint http://localhost:8000 --activate

Example (AWS):
    python seed_default_policy.py --activate
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.schemas import (
    BreakoutConfig,
    CheapOptionsConfig,
    CompressionConfig,
    ContractSelectionConfig,
    DecisionConfig,
    DeltaBand,
    DTEBucketRange,
    GateConfig,
    PillarWeights,
    PolicyConfig,
    ScannerConfig,
    TrackingConfig,
    UnderlyingFilterConfig,
    UnusualVolumeConfig,
    WatchlistConfig,
)
from app.core.policy import PolicyService
from app.db.tables import PolicyTable


def create_default_policy_config() -> PolicyConfig:
    """Create the default policy configuration from Appendix B."""
    return PolicyConfig(
        scanner=ScannerConfig(
            unusual_volume=UnusualVolumeConfig(
                volume_ratio_threshold=2.0,
                oi_change_threshold_pct=15.0,
            ),
            breakout=BreakoutConfig(
                lookback_days=20,
            ),
            compression=CompressionConfig(
                atr_period=14,
                compression_multiplier=1.10,
                break_pct=2.0,
            ),
            cheap_options=CheapOptionsConfig(
                iv_rv_ratio_max=1.10,
                iv_percentile_max=40,
            ),
        ),
        underlying_filter=UnderlyingFilterConfig(
            min_price=5.00,
            min_avg_dollar_volume=20_000_000,
            max_missing_bars=2,
            exclude_earnings_within_days=0,  # OFF by default
        ),
        contract_selection=ContractSelectionConfig(
            dte_buckets={
                "A": DTEBucketRange(min_dte=7, max_dte=21),
                "B": DTEBucketRange(min_dte=22, max_dte=45),
                "C": DTEBucketRange(min_dte=46, max_dte=75),
                "D": DTEBucketRange(min_dte=76, max_dte=120),
            },
            delta_bands={
                "CALL": DeltaBand(min_delta=0.20, max_delta=0.75),
                "PUT": DeltaBand(min_delta=-0.75, max_delta=-0.20),
            },
            top_k=3,
            target_delta_call=0.45,
            target_delta_put=-0.45,
            min_open_interest=200,
            min_volume=50,
            max_spread_pct=10.0,
            min_mid_price=0.20,
        ),
        gates=GateConfig(
            min_open_interest=300,
            min_volume=75,
            max_spread_pct=8.0,
            dte_min=7,
            dte_max=120,
            move_sufficiency_max=1.25,
            iv_percentile_max=85,
            breakout_volume_min=1.5,
            theta_burden_max=4.0,
        ),
        pillar_weights=PillarWeights(
            directional=0.35,
            volatility=0.35,
            structure=0.30,
        ),
        decision=DecisionConfig(
            approve_threshold=75,
            watch_threshold=65,
            tier_1_min_score=85,
            tier_1_min_pillar=70,
            tier_1_max_spread=5.0,
            tier_2_min_pillar=55,
        ),
        tracking=TrackingConfig(
            profit_target_pct=50.0,
            stop_loss_pct=50.0,
            time_exit_dte=5,
            shadow_sample_rate=0.05,  # 5% of rejects
            shadow_near_miss_threshold=60,
        ),
        watchlist=WatchlistConfig(
            tickers=[],  # Empty = use default watchlist
            max_concurrent_requests=50,  # Increased for better throughput
            batch_size=100,  # Increased for larger ticker lists
        ),
    )


async def seed_default_policy(activate: bool = False) -> None:
    """Seed the default policy to DynamoDB."""
    print("Creating default policy configuration from Appendix B...")

    config = create_default_policy_config()
    policy_service = PolicyService()

    # Check if v2.0.0 already exists
    existing = await policy_service.get_version("v2.0.0")
    if existing:
        print("Policy v2.0.0 already exists. Skipping creation.")
        if activate and not existing.is_active:
            print("Activating policy v2.0.0...")
            await policy_service.activate_version("v2.0.0")
            print("✓ Policy v2.0.0 activated")
        return

    # Create the policy
    policy = await policy_service.create_version(
        config=config,
        user="system",
    )

    print(f"✓ Created policy version: {policy.version}")
    print(f"  Hash: {policy.policy_hash[:16]}...")

    if activate:
        print("Activating policy...")
        await policy_service.activate_version(policy.version)
        print(f"✓ Policy {policy.version} activated")

    print("\nDefault policy configuration:")
    print(f"  Scanner - Unusual Volume Ratio: {config.scanner.unusual_volume.volume_ratio_threshold}x")
    print(f"  Scanner - Breakout Lookback: {config.scanner.breakout.lookback_days} days")
    print(f"  Gates - Min OI: {config.gates.min_open_interest} contracts")
    print(f"  Gates - Max Spread: {config.gates.max_spread_pct}%")
    print(f"  Decision - Approve >= {config.decision.approve_threshold}")
    print(f"  Decision - Watch >= {config.decision.watch_threshold}")
    print(f"  Pillar Weights: D={config.pillar_weights.directional:.0%} V={config.pillar_weights.volatility:.0%} S={config.pillar_weights.structure:.0%}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Seed default OSS policy")
    parser.add_argument(
        "--endpoint",
        help="DynamoDB endpoint URL (for local development)",
        default=None,
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Activate the policy after creation",
    )
    args = parser.parse_args()

    # Set environment variable for DynamoDB endpoint if provided
    if args.endpoint:
        os.environ["DYNAMODB_ENDPOINT"] = args.endpoint
        print(f"Using DynamoDB endpoint: {args.endpoint}")

    print("Seeding default OSS policy...")
    print("=" * 50)

    asyncio.run(seed_default_policy(activate=args.activate))

    print("=" * 50)
    print("✓ Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
