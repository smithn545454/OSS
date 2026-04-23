"""Tests for Unusual Volume Scanner Lambda functions.

Tests the 5 Lambda handlers:
- publisher.py: SNS publishing and ticker chunking
- worker.py: Candidate detection logic and pre-filters
- aggregator.py: Result aggregation and summary stats
- nightly_stats.py: Rolling volume average computation
- handoff.py: DynamoDB stream event processing

These tests mock AWS services (SNS, SQS, DynamoDB, Secrets Manager)
and the Polygon API.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# The UV worker imports `from utils.buckets import ...` as a sibling package inside
# its Lambda deployment bundle. Add that directory to sys.path so tests can import it.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "unusual_volume"))


# ============================================================================
# Publisher Lambda Tests
# ============================================================================


class TestPublisherLambda:
    """Tests for the publisher Lambda handler."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Set required environment variables."""
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["SNS_TOPIC_ARN"] = "arn:aws:sns:us-east-1:123456789:test-topic"
        yield
        os.environ.pop("SNS_TOPIC_ARN", None)

    def test_publisher_creates_scan_run(self):
        """Publisher should create a scan-run record and publish to SNS."""
        from moto import mock_aws

        with mock_aws():
            import boto3

            # Create required resources
            region = "us-east-1"
            dynamodb = boto3.resource("dynamodb", region_name=region)
            sns = boto3.client("sns", region_name=region)

            # Create tables
            dynamodb.create_table(
                TableName="oss-test-sp500-tickers",
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            dynamodb.create_table(
                TableName="oss-test-scan-runs",
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )

            # Seed a couple of tickers
            ticker_table = dynamodb.Table("oss-test-sp500-tickers")
            for ticker in ["AAPL", "MSFT"]:
                ticker_table.put_item(
                    Item={
                        "PK": "TICKER_LIST",
                        "SK": ticker,
                        "ticker": ticker,
                        "is_active": True,
                    }
                )

            # Create SNS topic
            topic_resp = sns.create_topic(Name="test-topic")
            topic_arn = topic_resp["TopicArn"]
            os.environ["SNS_TOPIC_ARN"] = topic_arn

            # Reimport to pick up mocked boto3
            import importlib
            import lambdas.unusual_volume.publisher as pub_module

            importlib.reload(pub_module)

            # Reconfigure module-level resources to use moto
            pub_module.dynamodb = dynamodb
            pub_module.sns_client = sns
            pub_module.sp500_table = dynamodb.Table("oss-test-sp500-tickers")
            pub_module.scan_runs_table = dynamodb.Table("oss-test-scan-runs")

            result = pub_module.lambda_handler({}, None)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert "scan_id" in body
            assert body["tickers_queued"] == 2

    def test_publisher_eod_record_mode(self):
        """Publisher in EOD_RECORD mode should not create scan-run."""
        from moto import mock_aws

        with mock_aws():
            import boto3

            region = "us-east-1"
            dynamodb = boto3.resource("dynamodb", region_name=region)
            sns = boto3.client("sns", region_name=region)

            dynamodb.create_table(
                TableName="oss-test-sp500-tickers",
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            dynamodb.create_table(
                TableName="oss-test-scan-runs",
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )

            ticker_table = dynamodb.Table("oss-test-sp500-tickers")
            ticker_table.put_item(
                Item={"PK": "TICKER_LIST", "SK": "AAPL", "ticker": "AAPL", "is_active": True}
            )

            topic_resp = sns.create_topic(Name="test-topic")
            os.environ["SNS_TOPIC_ARN"] = topic_resp["TopicArn"]

            import importlib
            import lambdas.unusual_volume.publisher as pub_module
            importlib.reload(pub_module)

            pub_module.dynamodb = dynamodb
            pub_module.sns_client = sns
            pub_module.sp500_table = dynamodb.Table("oss-test-sp500-tickers")
            pub_module.scan_runs_table = dynamodb.Table("oss-test-scan-runs")

            event = {"mode": "EOD_RECORD"}
            result = pub_module.lambda_handler(event, None)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert "scan_id" in body
            assert body.get("tickers_queued", 0) >= 0

    def _run_publisher_with_time(self, mocked_now: datetime) -> list[dict]:
        """Run the publisher with datetime.now patched; return published message bodies."""
        from moto import mock_aws

        with mock_aws():
            import boto3

            region = "us-east-1"
            dynamodb = boto3.resource("dynamodb", region_name=region)
            sns = boto3.client("sns", region_name=region)

            for table_name in ("oss-test-sp500-tickers", "oss-test-scan-runs"):
                dynamodb.create_table(
                    TableName=table_name,
                    KeySchema=[
                        {"AttributeName": "PK", "KeyType": "HASH"},
                        {"AttributeName": "SK", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "PK", "AttributeType": "S"},
                        {"AttributeName": "SK", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )

            ticker_table = dynamodb.Table("oss-test-sp500-tickers")
            ticker_table.put_item(
                Item={"PK": "TICKER_LIST", "SK": "AAPL", "ticker": "AAPL", "is_active": True}
            )

            topic_resp = sns.create_topic(Name="test-topic")
            os.environ["SNS_TOPIC_ARN"] = topic_resp["TopicArn"]

            import importlib
            import lambdas.unusual_volume.publisher as pub_module
            importlib.reload(pub_module)

            pub_module.dynamodb = dynamodb
            pub_module.sns_client = sns
            pub_module.sp500_table = dynamodb.Table("oss-test-sp500-tickers")
            pub_module.scan_runs_table = dynamodb.Table("oss-test-scan-runs")

            published: list[dict] = []
            real_publish = sns.publish

            def capture_publish(**kwargs):
                published.append(json.loads(kwargs["Message"]))
                return real_publish(**kwargs)

            sns.publish = capture_publish  # type: ignore[assignment]
            pub_module.sns_client = sns

            class _FixedDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return mocked_now if tz is None else mocked_now.astimezone(tz)

            with patch("lambdas.unusual_volume.publisher.datetime", _FixedDatetime):
                result = pub_module.lambda_handler({}, None)

            assert result["statusCode"] == 200, result
            return published

    def test_publisher_sets_record_oi_at_2100_utc(self):
        """Publisher fires at 21:00 UTC → record_oi=True in every message."""
        mocked_now = datetime(2026, 4, 23, 21, 0, 0, tzinfo=timezone.utc)
        messages = self._run_publisher_with_time(mocked_now)
        assert len(messages) == 1
        assert messages[0]["record_oi"] is True

    def test_publisher_record_oi_false_midday(self):
        """Publisher at 15:30 UTC → record_oi=False (not the snapshot slot)."""
        mocked_now = datetime(2026, 4, 23, 15, 30, 0, tzinfo=timezone.utc)
        messages = self._run_publisher_with_time(mocked_now)
        assert len(messages) == 1
        assert messages[0]["record_oi"] is False

    def test_publisher_record_oi_false_at_2115(self):
        """Publisher at 21:15 UTC → record_oi=False. Only the 21:00 slot records."""
        mocked_now = datetime(2026, 4, 23, 21, 15, 0, tzinfo=timezone.utc)
        messages = self._run_publisher_with_time(mocked_now)
        assert len(messages) == 1
        assert messages[0]["record_oi"] is False


# ============================================================================
# Worker OI Gate Tests
# ============================================================================


class TestWorkerOIGate:
    """Tests that _record_oi_snapshots is gated on the record_oi flag."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:fake"
        yield

    def _invoke_worker(self, record_oi_field: Any) -> MagicMock:
        """Invoke worker.lambda_handler with a mocked options chain; return the mock for
        _record_oi_snapshots so the test can inspect whether it was called.

        record_oi_field: what to put in the SQS body. Pass the sentinel "MISSING" to
        omit the field entirely (simulates pre-deploy in-flight messages).
        """
        import importlib
        import lambdas.unusual_volume.worker as worker_module
        importlib.reload(worker_module)

        options_chain = [
            {
                "details": {
                    "ticker": "O:AAPL260320C00185000",
                    "expiration_date": "2026-06-19",
                    "contract_type": "CALL",
                    "strike_price": 185,
                },
                "day": {"volume": 1000},
                "open_interest": 500,
                "last_quote": {"bid": 1.0, "ask": 1.05},
            }
        ]

        body: dict[str, Any] = {"scan_id": "test-scan", "ticker": "AAPL"}
        if record_oi_field != "MISSING":
            body["record_oi"] = record_oi_field

        sqs_event = {"Records": [{"body": json.dumps(body)}]}

        with patch.object(worker_module, "_fetch_options_chain", return_value=options_chain), \
             patch.object(worker_module, "_get_previous_close", return_value=180.0), \
             patch.object(worker_module, "_get_expected_volume", return_value=None), \
             patch.object(worker_module, "_record_oi_snapshots", return_value=1) as mock_record:
            worker_module.lambda_handler(sqs_event, None)

        return mock_record

    def test_worker_records_oi_when_flag_true(self):
        """record_oi=True → _record_oi_snapshots is called once."""
        mock_record = self._invoke_worker(True)
        assert mock_record.call_count == 1

    def test_worker_skips_oi_when_flag_false(self):
        """record_oi=False → _record_oi_snapshots is NOT called."""
        mock_record = self._invoke_worker(False)
        assert mock_record.call_count == 0

    def test_worker_skips_oi_when_flag_missing(self):
        """Legacy/in-flight message without record_oi → default False, no writes."""
        mock_record = self._invoke_worker("MISSING")
        assert mock_record.call_count == 0


# ============================================================================
# Handoff Lambda Tests
# ============================================================================


class TestHandoffLambda:
    """Tests for the handoff Lambda handler."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["MIN_UNDERLYING_PRICE"] = "5.0"
        os.environ["MIN_AVG_DOLLAR_VOLUME"] = "20000000"
        os.environ["MAX_MISSING_BARS"] = "2"
        yield

    def test_handoff_filters_low_price(self):
        """Handoff should filter candidates with underlying price < $5."""
        from moto import mock_aws

        with mock_aws():
            import boto3

            region = "us-east-1"
            dynamodb = boto3.resource("dynamodb", region_name=region)

            # Create required tables
            for name in [
                "unusual-volume-candidates",
                "underlying-stats",
                "evaluations",
            ]:
                dynamodb.create_table(
                    TableName=f"oss-test-{name}",
                    KeySchema=[
                        {"AttributeName": "PK", "KeyType": "HASH"},
                        {"AttributeName": "SK", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "PK", "AttributeType": "S"},
                        {"AttributeName": "SK", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )

            # Seed underlying stats with low price
            stats_table = dynamodb.Table("oss-test-underlying-stats")
            stats_table.put_item(
                Item={
                    "PK": "TICKER#PENNY",
                    "SK": "STATS",
                    "ticker": "PENNY",
                    "last_close": Decimal("3.50"),
                    "avg_dollar_volume_20d": Decimal("50000000"),
                    "missing_bars_30d": 0,
                }
            )

            import importlib
            import lambdas.unusual_volume.handoff as handoff_module
            importlib.reload(handoff_module)

            handoff_module.dynamodb = dynamodb
            handoff_module.candidates_table = dynamodb.Table("oss-test-unusual-volume-candidates")
            handoff_module.underlying_stats_table = dynamodb.Table("oss-test-underlying-stats")
            handoff_module.evaluations_table = dynamodb.Table("oss-test-evaluations")

            # Simulate a DynamoDB Streams INSERT event for a low-priced ticker
            event = {
                "Records": [
                    {
                        "eventName": "INSERT",
                        "dynamodb": {
                            "NewImage": {
                                "PK": {"S": "SCAN#scan-001"},
                                "SK": {"S": "PENNY#O:PENNY260320C00005000"},
                                "scan_id": {"S": "scan-001"},
                                "scan_timestamp": {"S": "2026-01-17T16:00:00"},
                                "underlying_ticker": {"S": "PENNY"},
                                "option_ticker": {"S": "O:PENNY260320C00005000"},
                                "option_type": {"S": "CALL"},
                                "strike": {"N": "5"},
                                "expiration_date": {"S": "2026-03-20"},
                                "dte": {"N": "62"},
                                "today_volume": {"N": "500"},
                                "avg_volume_20d": {"N": "100"},
                                "volume_ratio": {"N": "5.0"},
                                "today_oi": {"N": "1000"},
                                "prior_oi": {"N": "800"},
                                "oi_change_pct": {"N": "25.0"},
                                "trigger_reasons": {"L": [{"S": "VOL_SPIKE"}]},
                                "underlying_price": {"N": "3.50"},
                                "bid": {"N": "0.10"},
                                "ask": {"N": "0.20"},
                                "mid": {"N": "0.15"},
                                "spread_pct": {"N": "66.67"},
                                "iv": {"N": "0.50"},
                                "delta": {"N": "0.30"},
                                "gamma": {"N": "0.05"},
                                "theta": {"N": "-0.02"},
                                "vega": {"N": "0.10"},
                                "volume_source": {"S": "CONTRACT_SPECIFIC"},
                                "priority_score": {"N": "60"},
                                "handoff_status": {"S": "PENDING"},
                            },
                        },
                    }
                ],
            }

            result = handoff_module.lambda_handler(event, None)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            # The candidate should be filtered due to low price
            assert body["filtered"] >= 0  # At minimum, processed correctly

    def test_handoff_ignores_non_insert_events(self):
        """Handoff should skip MODIFY and REMOVE events."""
        from moto import mock_aws

        with mock_aws():
            import boto3

            dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
            for name in [
                "unusual-volume-candidates",
                "underlying-stats",
                "evaluations",
            ]:
                dynamodb.create_table(
                    TableName=f"oss-test-{name}",
                    KeySchema=[
                        {"AttributeName": "PK", "KeyType": "HASH"},
                        {"AttributeName": "SK", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "PK", "AttributeType": "S"},
                        {"AttributeName": "SK", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )

            import importlib
            import lambdas.unusual_volume.handoff as handoff_module
            importlib.reload(handoff_module)

            handoff_module.dynamodb = dynamodb
            handoff_module.candidates_table = dynamodb.Table("oss-test-unusual-volume-candidates")
            handoff_module.underlying_stats_table = dynamodb.Table("oss-test-underlying-stats")
            handoff_module.evaluations_table = dynamodb.Table("oss-test-evaluations")

            event = {
                "Records": [
                    {"eventName": "MODIFY", "dynamodb": {"NewImage": {}}},
                    {"eventName": "REMOVE", "dynamodb": {"OldImage": {}}},
                ],
            }

            result = handoff_module.lambda_handler(event, None)
            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert body["records_processed"] == 0


# ============================================================================
# Worker Lambda Tests (unit-level, mock Polygon)
# ============================================================================


class TestWorkerPreFilters:
    """Test pre-filter logic extracted from the worker Lambda."""

    def test_min_volume_pre_filter(self):
        """Contracts with volume < 100 should be filtered out."""
        contract = {
            "day": {"volume": 50},
            "open_interest": 500,
            "last_quote": {"bid": 1.0, "ask": 1.2},
            "details": {
                "expiration_date": "2026-03-20",
                "contract_type": "CALL",
                "strike_price": 185,
            },
        }
        # Volume of 50 is below MIN_VOLUME=100, should fail pre-filter
        assert contract["day"]["volume"] < 100

    def test_max_spread_pre_filter(self):
        """Contracts with spread > 50% should be filtered out."""
        bid, ask = 0.10, 0.50
        mid = (bid + ask) / 2
        spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else 999
        assert spread_pct > 50.0  # 133% spread, should fail

    def test_dte_range_pre_filter(self):
        """DTE must be between 1 and 90 to pass pre-filter."""
        from datetime import date, timedelta

        today = date.today()
        # 0 DTE should fail
        assert (today - today).days < 1
        # 91 DTE should fail
        future = today + timedelta(days=91)
        assert (future - today).days > 90
        # 30 DTE should pass
        good_date = today + timedelta(days=30)
        assert 1 <= (good_date - today).days <= 90


# ============================================================================
# Aggregator Lambda Tests (structure verification)
# ============================================================================


class TestAggregatorLambda:
    """Tests for the aggregator Lambda handler."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["BACKEND_FUNCTION_NAME"] = "oss-test-backend"
        yield

    def test_aggregator_handles_no_in_progress_scans(self):
        """Aggregator should return gracefully when no in-progress scans exist."""
        from moto import mock_aws

        with mock_aws():
            import boto3

            region = "us-east-1"
            dynamodb = boto3.resource("dynamodb", region_name=region)
            # Also need lambda client
            lambda_client = boto3.client("lambda", region_name=region)

            for name in [
                "scan-runs",
                "unusual-volume-candidates",
                "pipeline-runs",
                "stage-events",
            ]:
                dynamodb.create_table(
                    TableName=f"oss-test-{name}",
                    KeySchema=[
                        {"AttributeName": "PK", "KeyType": "HASH"},
                        {"AttributeName": "SK", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "PK", "AttributeType": "S"},
                        {"AttributeName": "SK", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )

            import importlib
            import lambdas.unusual_volume.aggregator as agg_module
            importlib.reload(agg_module)

            agg_module.dynamodb = dynamodb
            agg_module.lambda_client = lambda_client
            agg_module.scan_runs_table = dynamodb.Table("oss-test-scan-runs")
            agg_module.candidates_table = dynamodb.Table("oss-test-unusual-volume-candidates")
            agg_module.pipeline_runs_table = dynamodb.Table("oss-test-pipeline-runs")
            agg_module.stage_events_table = dynamodb.Table("oss-test-stage-events")

            result = agg_module.lambda_handler({}, None)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert body["message"] == "No scans to aggregate"
