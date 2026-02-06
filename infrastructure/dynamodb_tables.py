#!/usr/bin/env python3
"""Create DynamoDB tables for OSS.

Usage:
    python dynamodb_tables.py [--endpoint URL] [--prefix PREFIX]

Example (local development):
    python dynamodb_tables.py --endpoint http://localhost:8000

Example (AWS):
    python dynamodb_tables.py --prefix oss-prod
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import boto3
from botocore.exceptions import ClientError


def create_table(dynamodb: Any, table_name: str, key_schema: list, attribute_definitions: list,
                 gsi_definitions: list | None = None) -> None:
    """Create a DynamoDB table."""
    try:
        create_params: dict[str, Any] = {
            "TableName": table_name,
            "KeySchema": key_schema,
            "AttributeDefinitions": attribute_definitions,
            "BillingMode": "PAY_PER_REQUEST",
        }

        if gsi_definitions:
            create_params["GlobalSecondaryIndexes"] = gsi_definitions

        dynamodb.create_table(**create_params)
        print(f"✓ Created table: {table_name}")

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"  Table already exists: {table_name}")
        else:
            print(f"✗ Error creating {table_name}: {e}")
            raise


def create_policies_table(dynamodb: Any, prefix: str) -> None:
    """Create the policies table."""
    create_table(
        dynamodb,
        f"{prefix}-policies",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def create_opportunities_table(dynamodb: Any, prefix: str) -> None:
    """Create the opportunities table."""
    create_table(
        dynamodb,
        f"{prefix}-opportunities",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        gsi_definitions=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def create_evaluations_table(dynamodb: Any, prefix: str) -> None:
    """Create the evaluations table."""
    create_table(
        dynamodb,
        f"{prefix}-evaluations",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
        ],
        gsi_definitions=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI2",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )


def create_pipeline_runs_table(dynamodb: Any, prefix: str) -> None:
    """Create the pipeline-runs table."""
    create_table(
        dynamodb,
        f"{prefix}-pipeline-runs",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def create_stage_events_table(dynamodb: Any, prefix: str) -> None:
    """Create the stage-events table."""
    create_table(
        dynamodb,
        f"{prefix}-stage-events",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def create_paper_positions_table(dynamodb: Any, prefix: str) -> None:
    """Create the paper-positions table."""
    create_table(
        dynamodb,
        f"{prefix}-paper-positions",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def create_feature_values_table(dynamodb: Any, prefix: str) -> None:
    """Create the feature-values table."""
    create_table(
        dynamodb,
        f"{prefix}-feature-values",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def create_pillar_scores_table(dynamodb: Any, prefix: str) -> None:
    """Create the pillar-scores table."""
    create_table(
        dynamodb,
        f"{prefix}-pillar-scores",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def create_gate_results_table(dynamodb: Any, prefix: str) -> None:
    """Create the gate-results table."""
    create_table(
        dynamodb,
        f"{prefix}-gate-results",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def create_iv_history_table(dynamodb: Any, prefix: str) -> None:
    """Create the iv-history table for IV percentile calculation.

    Uses TTL for automatic cleanup after 365 days.
    """
    create_table(
        dynamodb,
        f"{prefix}-iv-history",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


# ============================================================================
# Unusual Volume Scanner Tables
# ============================================================================


def create_sp500_tickers_table(dynamodb: Any, prefix: str) -> None:
    """Create the sp500-tickers table for unusual volume scanner.

    PK: "TICKER_LIST"
    SK: "TICKER#{ticker}"
    """
    create_table(
        dynamodb,
        f"{prefix}-sp500-tickers",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def create_volume_stats_table(dynamodb: Any, prefix: str) -> None:
    """Create the volume-stats table for pre-computed volume statistics.

    PK: "CONTRACT#{option_ticker}" or "BUCKET#{underlying}#{option_type}#{moneyness}#{dte_bucket}"
    SK: "STATS#{as_of_date}"

    GSI: underlying-date-index for querying all stats by underlying and date.
    TTL: 7 days for automatic cleanup.
    """
    create_table(
        dynamodb,
        f"{prefix}-volume-stats",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "underlying_ticker", "AttributeType": "S"},
            {"AttributeName": "as_of_date", "AttributeType": "S"},
        ],
        gsi_definitions=[
            {
                "IndexName": "underlying-date-index",
                "KeySchema": [
                    {"AttributeName": "underlying_ticker", "KeyType": "HASH"},
                    {"AttributeName": "as_of_date", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def create_unusual_volume_candidates_table(dynamodb: Any, prefix: str) -> None:
    """Create the unusual-volume-candidates table for scan results.

    PK: "SCAN#{scan_id}"
    SK: "CONTRACT#{option_ticker}"

    GSI: handoff-status-index for querying pending candidates.
    TTL: 24 hours for automatic cleanup.
    """
    create_table(
        dynamodb,
        f"{prefix}-unusual-volume-candidates",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "handoff_status", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        gsi_definitions=[
            {
                "IndexName": "handoff-status-index",
                "KeySchema": [
                    {"AttributeName": "handoff_status", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def create_scan_runs_table(dynamodb: Any, prefix: str) -> None:
    """Create the scan-runs table for tracking unusual volume scan metadata.

    PK: "SCAN#{scan_id}"
    SK: "METADATA"
    TTL: 7 days for automatic cleanup.
    """
    create_table(
        dynamodb,
        f"{prefix}-scan-runs",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def create_underlying_stats_table(dynamodb: Any, prefix: str) -> None:
    """Create the underlying-stats table for Stage 2 filter data.

    PK: "TICKER#{ticker}"
    SK: "STATS#LATEST" or "STATS#{date}"
    """
    create_table(
        dynamodb,
        f"{prefix}-underlying-stats",
        key_schema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        attribute_definitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    )


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Create DynamoDB tables for OSS")
    parser.add_argument(
        "--endpoint",
        help="DynamoDB endpoint URL (for local development)",
        default=None,
    )
    parser.add_argument(
        "--prefix",
        help="Table name prefix",
        default="oss",
    )
    parser.add_argument(
        "--region",
        help="AWS region",
        default="us-east-1",
    )
    args = parser.parse_args()

    print(f"Creating OSS DynamoDB tables with prefix: {args.prefix}")
    if args.endpoint:
        print(f"Using endpoint: {args.endpoint}")

    # Create DynamoDB client
    client_kwargs = {"region_name": args.region}
    if args.endpoint:
        client_kwargs["endpoint_url"] = args.endpoint

    dynamodb = boto3.client("dynamodb", **client_kwargs)

    # Create all tables
    print("\nCreating tables...")
    create_policies_table(dynamodb, args.prefix)
    create_opportunities_table(dynamodb, args.prefix)
    create_evaluations_table(dynamodb, args.prefix)
    create_pipeline_runs_table(dynamodb, args.prefix)
    create_stage_events_table(dynamodb, args.prefix)
    create_paper_positions_table(dynamodb, args.prefix)
    create_feature_values_table(dynamodb, args.prefix)
    create_pillar_scores_table(dynamodb, args.prefix)
    create_gate_results_table(dynamodb, args.prefix)
    create_iv_history_table(dynamodb, args.prefix)

    # Unusual Volume Scanner tables
    print("\nCreating Unusual Volume Scanner tables...")
    create_sp500_tickers_table(dynamodb, args.prefix)
    create_volume_stats_table(dynamodb, args.prefix)
    create_unusual_volume_candidates_table(dynamodb, args.prefix)
    create_scan_runs_table(dynamodb, args.prefix)
    create_underlying_stats_table(dynamodb, args.prefix)

    print("\n✓ All tables created successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
