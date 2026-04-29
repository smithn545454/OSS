"""Shared test fixtures for the OSS test suite.

Provides common fixtures used across multiple test modules to reduce
duplication and ensure consistent test data.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time

# Ensure test environment variables are set before any app imports
os.environ.setdefault("DYNAMODB_TABLE_PREFIX", "oss-test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("POLYGON_API_KEY", "fake-test-key")

from app.core.schemas import (
    Decision,
    DirectionHint,
    DTEBucket,
    Evaluation,
    GateConfig,
    GateOperator,
    GateResult,
    IVRegime,
    Opportunity,
    OptionType,
    PaperPosition,
    PillarContributor,
    PillarId,
    PillarScore,
    PipelineRun,
    PipelineStage,
    Policy,
    PolicyConfig,
    QualityTier,
    RunStatus,
    ScannerTrigger,
    ScannerType,
    StageEvent,
    Verdict,
)


# ============================================================================
# Policy Fixtures
# ============================================================================


@pytest.fixture
def default_policy_config() -> PolicyConfig:
    """Default PolicyConfig for tests (Policy v3.0.0).

    PolicyConfig() loads pillar defaults from the seed JSON automatically
    via PillarConfig's default_factory. See app/core/schemas.py.
    """
    return PolicyConfig()


@pytest.fixture
def default_policy(default_policy_config: PolicyConfig) -> Policy:
    """A complete Policy object with default config."""
    return Policy(
        version="v3.0.0",
        policy_hash=Policy.compute_hash(default_policy_config),
        config=default_policy_config,
        created_by="test",
        is_active=True,
    )


@pytest.fixture
def mock_policy(default_policy_config: PolicyConfig) -> MagicMock:
    """A MagicMock policy (used where full Policy construction isn't needed)."""
    policy = MagicMock()
    policy.version = "v3.0.0"
    policy.config = default_policy_config
    policy.is_active = True
    policy.policy_hash = "test-hash"
    return policy


# ============================================================================
# Sample Data Fixtures
# ============================================================================


@pytest.fixture
def sample_opportunity() -> Opportunity:
    """A canonical test opportunity for AAPL."""
    return Opportunity(
        opportunity_id="opp-test-001",
        underlying_ticker="AAPL",
        timestamp_utc="2026-01-17T16:00:00+00:00",
        scanner_triggers=[
            ScannerTrigger(
                scanner_type=ScannerType.BREAKOUT,
                reason_codes=["BREAKOUT_ABOVE_20D_HIGH"],
                metrics={"breakout_pct": 2.5, "volume_ratio": 1.8},
                triggered_at="2026-01-17T16:00:00+00:00",
            )
        ],
        direction_hint=DirectionHint.CALL,
        priority_score=80,
    )


@pytest.fixture
def sample_evaluation() -> Evaluation:
    """A canonical test evaluation for an AAPL call."""
    return Evaluation(
        evaluation_id="eval-test-001",
        opportunity_id="opp-test-001",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL260320C00185000",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=62,
        strike=185.0,
        underlying_price=189.0,
        moneyness_pct=-2.12,
        bid=8.50,
        ask=8.80,
        mid=8.65,
        spread_abs=0.30,
        spread_pct=3.47,
        iv=0.32,
        delta=0.55,
        gamma=0.03,
        theta=-0.08,
        vega=0.25,
        open_interest=5000,
        volume=500,
        breakeven_price=193.65,
        required_move_pct=2.46,
        expected_move_pct=5.0,
        feasibility_ratio=0.49,
        time_adjusted_feasibility=0.45,
        dte_bucket=DTEBucket.C,
        rank_score=85.0,
        policy_version="v2.0.0",
        policy_hash="test-hash",
    )


@pytest.fixture
def sample_decision() -> Decision:
    """A canonical APPROVE decision."""
    return Decision(
        evaluation_id="eval-test-001",
        verdict=Verdict.APPROVE,
        quality_tier=QualityTier.TIER_2,
        final_score=82.0,
        premium_leverage_score=78.0,
        underlying_behavior_score=85.0,
        setup_quality_score=80.0,
        primary_reason_code="ALL_GATES_PASSED",
        supporting_reason_codes=["STRONG_UNDERLYING_BEHAVIOR", "GOOD_SETUP_QUALITY"],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v3.0.0",
    )


@pytest.fixture
def sample_paper_position() -> PaperPosition:
    """A canonical open paper position."""
    return PaperPosition(
        position_id="pos-test-001",
        evaluation_id="eval-test-001",
        option_ticker="O:AAPL260320C00185000",
        entry_price=8.65,
        entry_date="2026-01-17",
        verdict_at_entry=Verdict.APPROVE,
        quality_tier_at_entry=QualityTier.TIER_2,
        current_price=9.50,
        current_pnl_pct=9.83,
        max_favorable_excursion=12.5,
        max_adverse_excursion=-3.2,
        days_held=5,
    )


@pytest.fixture
def sample_gate_results() -> list[GateResult]:
    """A set of passing gate results."""
    gates = [
        ("GATE_MIN_OPEN_INTEREST", True, 5000, 300, GateOperator.GTE, "contracts", "OI_SUFFICIENT"),
        ("GATE_MIN_VOLUME", True, 500, 75, GateOperator.GTE, "contracts", "VOLUME_SUFFICIENT"),
        ("GATE_MAX_SPREAD_PCT", True, 3.47, 8.0, GateOperator.LTE, "percent", "SPREAD_OK"),
        ("GATE_DTE_RANGE", True, 62, 7, GateOperator.GTE, "days", "DTE_IN_RANGE"),
        ("GATE_MOVE_SUFFICIENCY", True, 0.49, 1.25, GateOperator.LTE, "ratio", "MOVE_FEASIBLE"),
    ]
    return [
        GateResult(
            evaluation_id="eval-test-001",
            gate_id=gid,
            enabled=True,
            passed=passed,
            measured_value=measured,
            threshold_value=threshold,
            operator=op,
            units=units,
            reason_code=reason,
        )
        for gid, passed, measured, threshold, op, units, reason in gates
    ]


@pytest.fixture
def sample_pillar_scores() -> list[PillarScore]:
    """A set of pillar scores for a passing evaluation."""
    contributor = PillarContributor(
        feature_name="trend_alignment",
        subscore=80.0,
        weight=0.30,
        weighted_contribution=24.0,
        raw_value=0.85,
        distance_from_neutral=0.35,
    )
    return [
        PillarScore(
            evaluation_id="eval-test-001",
            pillar_id=PillarId.DIRECTIONAL,
            score=78,
            contributors=[contributor],
        ),
        PillarScore(
            evaluation_id="eval-test-001",
            pillar_id=PillarId.VOLATILITY,
            score=85,
            contributors=[contributor],
        ),
        PillarScore(
            evaluation_id="eval-test-001",
            pillar_id=PillarId.STRUCTURE,
            score=80,
            contributors=[contributor],
        ),
    ]


# ============================================================================
# Mock Client Fixtures
# ============================================================================


@pytest.fixture
def mock_polygon_client() -> AsyncMock:
    """A mock Polygon client with realistic return data."""
    client = AsyncMock()

    # Mock daily bars
    client.get_daily_bars_parsed.return_value = [
        MagicMock(
            ticker="AAPL",
            date=f"2026-01-{15 + i:02d}",
            open=180.0 + i,
            high=185.0 + i,
            low=178.0 + i,
            close=183.0 + i,
            volume=50_000_000 + i * 5_000_000,
        )
        for i in range(30)
    ]

    # Mock previous close
    client.get_previous_close.return_value = {"c": 189.0, "v": 60_000_000}

    # Batch methods used by LiveDataProvider
    client.get_daily_bars_batch.return_value = {
        "AAPL": client.get_daily_bars_parsed.return_value,
    }
    client.get_previous_close_batch.return_value = {
        "AAPL": {"c": 189.0, "v": 60_000_000},
    }

    # Mock options chain
    client.get_options_chain.return_value = [
        {
            "details": {
                "contract_type": "CALL",
                "ticker": "O:AAPL260320C00185000",
                "strike_price": 185.0,
                "expiration_date": "2026-03-20",
            },
            "day": {"volume": 500, "open": 5.0, "high": 5.5, "low": 4.8, "close": 5.2},
            "underlying_asset": {"ticker": "AAPL", "price": 189.0},
            "greeks": {"delta": 0.55, "gamma": 0.03, "theta": -0.08, "vega": 0.25},
            "open_interest": 5000,
            "implied_volatility": 0.32,
            "last_quote": {"bid": 5.0, "ask": 5.4, "midpoint": 5.2},
        },
    ]

    # Mock aggregated options volume
    client.get_aggregated_options_volume.return_value = MagicMock(
        ticker="AAPL",
        total_call_volume=100_000,
        total_put_volume=50_000,
        total_call_oi=500_000,
        total_put_oi=300_000,
        call_put_volume_ratio=2.0,
        timestamp="2026-01-17T16:00:00Z",
    )

    return client


@pytest.fixture
def mock_pipeline_orchestrator() -> AsyncMock:
    """A mock PipelineOrchestrator for telemetry tracking."""
    orchestrator = AsyncMock()
    orchestrator.start_run.return_value = MagicMock(run_id="test-run-001")
    orchestrator.update_current_stage.return_value = None
    orchestrator.record_stage_event.return_value = MagicMock()
    orchestrator.complete_run.return_value = MagicMock()
    return orchestrator


# ============================================================================
# DynamoDB / moto Fixtures
# ============================================================================


@pytest.fixture
def moto_dynamodb():
    """Create moto-managed DynamoDB tables matching the CDK stack.

    Yields the boto3 DynamoDB resource. All tables are created fresh
    for each test and destroyed afterward.
    """
    from moto import mock_aws

    with mock_aws():
        import boto3

        db = boto3.resource("dynamodb", region_name="us-east-1")

        # Common key schema used by most tables
        pk_sk_schema = {
            "KeySchema": [
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        }

        # Tables that need GSI1
        gsi1_attrs = [
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ]
        gsi1 = [
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ]

        # Tables that need GSI2
        gsi2_attrs = [
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
        ]
        gsi2 = [
            {
                "IndexName": "GSI2",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ]

        table_prefix = "oss-test"

        # Simple PK/SK tables
        simple_tables = [
            "policies",
            "pipeline-runs",
            "stage-events",
            "feature-values",
            "pillar-scores",
            "iv-history",
            "oi-history",
            "price-history",
            "llm-usage",
            "calibration-reports",
            "scan-status",
            "paper-snapshots",
            "convex-universe-snapshots",
            "convex-stage-events",
            "catalyst-calendar",
        ]
        for name in simple_tables:
            db.create_table(TableName=f"{table_prefix}-{name}", **pk_sk_schema)

        # Opportunities: GSI1
        db.create_table(
            TableName=f"{table_prefix}-opportunities",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs,
            GlobalSecondaryIndexes=gsi1,
            BillingMode="PAY_PER_REQUEST",
        )

        # Evaluations: GSI1 + GSI2
        db.create_table(
            TableName=f"{table_prefix}-evaluations",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs + gsi2_attrs,
            GlobalSecondaryIndexes=gsi1 + gsi2,
            BillingMode="PAY_PER_REQUEST",
        )

        # Paper positions: GSI1 + GSI2
        db.create_table(
            TableName=f"{table_prefix}-paper-positions",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs + gsi2_attrs,
            GlobalSecondaryIndexes=gsi1 + gsi2,
            BillingMode="PAY_PER_REQUEST",
        )

        # Gate results: GSI1
        db.create_table(
            TableName=f"{table_prefix}-gate-results",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs,
            GlobalSecondaryIndexes=gsi1,
            BillingMode="PAY_PER_REQUEST",
        )

        # Convex evaluations: GSI1 (tier-filtered queries)
        db.create_table(
            TableName=f"{table_prefix}-convex-evaluations",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs,
            GlobalSecondaryIndexes=gsi1,
            BillingMode="PAY_PER_REQUEST",
        )

        # Trade thesis: GSI1
        db.create_table(
            TableName=f"{table_prefix}-trade-thesis",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs,
            GlobalSecondaryIndexes=gsi1,
            BillingMode="PAY_PER_REQUEST",
        )

        # UV candidates: GSI1
        db.create_table(
            TableName=f"{table_prefix}-unusual-volume-candidates",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs,
            GlobalSecondaryIndexes=gsi1,
            BillingMode="PAY_PER_REQUEST",
        )

        # S&P 500 tickers: simple PK/SK
        db.create_table(TableName=f"{table_prefix}-sp500-tickers", **pk_sk_schema)

        # Backtest runs: GSI1 (status)
        db.create_table(
            TableName=f"{table_prefix}-backtest-runs",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs,
            GlobalSecondaryIndexes=gsi1,
            BillingMode="PAY_PER_REQUEST",
        )

        # Backtest trades: GSI1 (scanner) + GSI2 (regime)
        db.create_table(
            TableName=f"{table_prefix}-backtest-trades",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs + gsi2_attrs,
            GlobalSecondaryIndexes=gsi1 + gsi2,
            BillingMode="PAY_PER_REQUEST",
        )

        # Backtest insights: simple PK/SK
        db.create_table(TableName=f"{table_prefix}-backtest-insights", **pk_sk_schema)

        # Real trades: GSI1 (ticker) + GSI2 (evaluation_id dedup)
        db.create_table(
            TableName=f"{table_prefix}-real-trades",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs + gsi2_attrs,
            GlobalSecondaryIndexes=gsi1 + gsi2,
            BillingMode="PAY_PER_REQUEST",
        )

        # Stock summaries: PK/SK only (per-ticker per-day cache)
        db.create_table(
            TableName=f"{table_prefix}-stock-summaries", **pk_sk_schema
        )

        # Earnings cache: ticker-keyed (no PK/SK pattern)
        db.create_table(
            TableName=f"{table_prefix}-earnings-cache",
            KeySchema=[{"AttributeName": "ticker", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "ticker", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Earnings history: PK/SK + GSI1 (for date-range cross-ticker queries)
        db.create_table(
            TableName=f"{table_prefix}-earnings-history",
            KeySchema=pk_sk_schema["KeySchema"],
            AttributeDefinitions=pk_sk_schema["AttributeDefinitions"] + gsi1_attrs,
            GlobalSecondaryIndexes=gsi1,
            BillingMode="PAY_PER_REQUEST",
        )

        # Reset DynamoDBClient singleton so it creates fresh boto3 clients
        # within this mock_aws context. Without this, tests that go through
        # get_dynamodb() (e.g., backtest tests) get stale clients from a
        # previous mock context, causing "invalid security token" errors.
        from app.db.dynamodb import DynamoDBClient

        DynamoDBClient._instance = None

        yield db

        DynamoDBClient._instance = None


# ============================================================================
# Bad-Data Fixtures (for fault injection / edge case testing)
# ============================================================================


@pytest.fixture
def bad_data_nan_greeks_evaluation() -> Evaluation:
    """Evaluation with NaN-like extreme values in greeks."""
    return Evaluation(
        evaluation_id="eval-bad-nan",
        opportunity_id="opp-bad",
        underlying_ticker="BAD",
        option_ticker="O:BAD260320C00100000",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=62,
        strike=100.0,
        underlying_price=100.0,
        moneyness_pct=0.0,
        bid=0.0,
        ask=0.0,
        mid=0.0,
        spread_abs=0.0,
        spread_pct=0.0,
        iv=0.0,
        delta=0.0,
        gamma=0.0,
        theta=0.0,
        vega=0.0,
        open_interest=0,
        volume=0,
        breakeven_price=100.0,
        required_move_pct=0.0,
        expected_move_pct=0.0,
        feasibility_ratio=999.0,
        time_adjusted_feasibility=999.0,
        dte_bucket=DTEBucket.C,
        rank_score=0.0,
        policy_version="v2.0.0",
        policy_hash="test-hash",
    )


@pytest.fixture
def bad_data_zero_bid_ask_evaluation() -> Evaluation:
    """Evaluation with zero bid/ask (stale or halted)."""
    return Evaluation(
        evaluation_id="eval-bad-zero-ba",
        opportunity_id="opp-bad",
        underlying_ticker="HALT",
        option_ticker="O:HALT260320C00050000",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=62,
        strike=50.0,
        underlying_price=50.0,
        moneyness_pct=0.0,
        bid=0.0,
        ask=0.0,
        mid=0.0,
        spread_abs=0.0,
        spread_pct=999.0,
        iv=0.30,
        delta=0.50,
        gamma=0.03,
        theta=-0.05,
        vega=0.20,
        open_interest=100,
        volume=10,
        breakeven_price=50.0,
        required_move_pct=0.0,
        expected_move_pct=5.0,
        feasibility_ratio=0.0,
        time_adjusted_feasibility=0.0,
        dte_bucket=DTEBucket.C,
        rank_score=30.0,
        policy_version="v2.0.0",
        policy_hash="test-hash",
    )


@pytest.fixture
def bad_data_expired_evaluation() -> Evaluation:
    """Evaluation with DTE=0 (expired contract)."""
    return Evaluation(
        evaluation_id="eval-bad-expired",
        opportunity_id="opp-bad",
        underlying_ticker="EXP",
        option_ticker="O:EXP260117C00100000",
        option_type=OptionType.CALL,
        expiration_date="2026-01-17",
        dte=0,
        strike=100.0,
        underlying_price=105.0,
        moneyness_pct=-5.0,
        bid=5.00,
        ask=5.20,
        mid=5.10,
        spread_abs=0.20,
        spread_pct=3.92,
        iv=0.50,
        delta=0.90,
        gamma=0.01,
        theta=-0.50,
        vega=0.05,
        open_interest=1000,
        volume=200,
        breakeven_price=105.10,
        required_move_pct=0.10,
        expected_move_pct=0.01,
        feasibility_ratio=10.0,
        time_adjusted_feasibility=999.0,
        dte_bucket=DTEBucket.A,
        rank_score=20.0,
        policy_version="v2.0.0",
        policy_hash="test-hash",
    )


# ============================================================================
# Time-Frozen Fixture
# ============================================================================


@pytest.fixture
def frozen_time():
    """Freeze time to a deterministic market-hours timestamp.

    Usage: def test_something(frozen_time): ...
    Time will be 2026-01-17 16:00:00 UTC (market close).
    """
    with freeze_time("2026-01-17T16:00:00+00:00"):
        yield


# ============================================================================
# Configurable Mock Polygon (promoted from test_pipeline_scenarios.py)
# ============================================================================


def make_mock_polygon(
    tickers: list[str] | None = None,
    bars_per_ticker: int = 30,
    chain_per_ticker: int = 2,
    fail_tickers: set[str] | None = None,
    empty_chain: bool = False,
) -> AsyncMock:
    """Build a mock Polygon client with configurable data per ticker.

    Args:
        tickers: Tickers to generate data for (default: ["AAPL"])
        bars_per_ticker: Number of daily bars to return
        chain_per_ticker: Number of options contracts per ticker
        fail_tickers: Tickers that raise on chain fetch
        empty_chain: If True, return empty options chain for all tickers
    """
    tickers = tickers or ["AAPL"]
    fail_tickers = fail_tickers or set()
    client = AsyncMock()

    # Mock daily bars — both individual and batch methods
    bars_by_ticker: dict[str, list] = {}
    all_bars = []
    for ticker in tickers:
        ticker_bars = []
        for i in range(bars_per_ticker):
            bar = MagicMock(
                ticker=ticker,
                date=f"2026-01-{(i % 28) + 1:02d}",
                open=180.0 + i,
                high=185.0 + i,
                low=178.0 + i,
                close=183.0 + i,
                volume=50_000_000 + i * 1_000_000,
                vwap=182.0 + i,
            )
            ticker_bars.append(bar)
            all_bars.append(bar)
        bars_by_ticker[ticker] = ticker_bars
    client.get_daily_bars_parsed.return_value = all_bars
    client.get_daily_bars_batch.return_value = bars_by_ticker
    client.get_previous_close.return_value = {"c": 189.0, "v": 60_000_000}
    client.get_previous_close_batch.return_value = {
        t: {"c": 189.0, "v": 60_000_000} for t in tickers
    }

    def mock_chain(ticker, **kwargs):
        if ticker in fail_tickers:
            raise Exception(f"API failure for {ticker}")
        if empty_chain:
            return []
        contracts = []
        for j in range(chain_per_ticker):
            contracts.append({
                "details": {
                    "contract_type": "CALL" if j % 2 == 0 else "PUT",
                    "ticker": f"O:{ticker}260320C001{85 + j * 5}000",
                    "strike_price": 185.0 + j * 5,
                    "expiration_date": "2026-03-20",
                },
                "day": {"volume": 500, "open": 5.0, "high": 5.5, "low": 4.8, "close": 5.2},
                "underlying_asset": {"ticker": ticker, "price": 189.0},
                "greeks": {"delta": 0.55 - j * 0.1, "gamma": 0.03, "theta": -0.08, "vega": 0.25},
                "open_interest": 5000,
                "implied_volatility": 0.32,
                "last_quote": {"bid": 5.0, "ask": 5.4, "midpoint": 5.2},
            })
        return contracts

    client.get_options_chain.side_effect = mock_chain

    client.get_aggregated_options_volume.return_value = MagicMock(
        ticker="SPY",
        total_call_volume=100_000,
        total_put_volume=50_000,
        total_call_oi=500_000,
        total_put_oi=300_000,
        call_put_volume_ratio=2.0,
        timestamp="2026-01-17T16:00:00Z",
    )

    return client


# ============================================================================
# DynamoDB Client Auto-Reset Fixture
# ============================================================================


@pytest.fixture
def fresh_dynamodb_client(moto_dynamodb):
    """Reset the DynamoDB singleton so it picks up moto tables.

    Use this instead of manually calling DynamoDBClient._instance = None
    in every test class.
    """
    from app.db.dynamodb import DynamoDBClient

    DynamoDBClient._instance = None
    with patch("app.config.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.dynamodb_endpoint = None
        settings.aws_region = "us-east-1"
        settings.dynamodb_table_prefix = "oss-test"
        client = DynamoDBClient()
        DynamoDBClient._instance = client
        yield client
    DynamoDBClient._instance = None
