"""Extended tests for Unusual Volume Scanner Worker and Nightly Stats Lambdas.

Covers:
- Worker full scan mode (_process_ticker pipeline)
- Worker EOD recording mode
- Worker pre-filter function directly
- Worker OI change calculation
- Nightly stats aggregation logic
- Nightly stats bucket-level computation
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Add lambda utils to path so imports like `from utils.buckets import ...` work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "unusual_volume"))


# ============================================================================
# Worker Lambda: _passes_prefilter
# ============================================================================


class TestWorkerPreFilter:
    """Test the _passes_prefilter function directly."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:test"
        yield

    def _make_contract(
        self,
        volume=500,
        oi=1000,
        bid=5.0,
        ask=5.5,
        expiration_date=None,
    ) -> dict:
        if expiration_date is None:
            expiration_date = (date.today() + timedelta(days=30)).isoformat()
        return {
            "day": {"volume": volume},
            "open_interest": oi,
            "last_quote": {"bid": bid, "ask": ask},
            "details": {
                "expiration_date": expiration_date,
                "contract_type": "call",
                "strike_price": 185,
            },
        }

    def test_passes_all_filters(self):
        """Contract meeting all criteria passes."""
        from lambdas.unusual_volume.worker import _passes_prefilter
        contract = self._make_contract()
        assert _passes_prefilter(contract) is True

    def test_fails_low_volume(self):
        """Contract with volume < 100 is filtered."""
        from lambdas.unusual_volume.worker import _passes_prefilter
        contract = self._make_contract(volume=50)
        assert _passes_prefilter(contract) is False

    def test_fails_zero_oi(self):
        """Contract with zero open interest is filtered."""
        from lambdas.unusual_volume.worker import _passes_prefilter
        contract = self._make_contract(oi=0)
        assert _passes_prefilter(contract) is False

    def test_fails_wide_spread(self):
        """Contract with spread > 50% is filtered."""
        from lambdas.unusual_volume.worker import _passes_prefilter
        contract = self._make_contract(bid=0.10, ask=0.50)
        assert _passes_prefilter(contract) is False

    def test_fails_expired_contract(self):
        """Contract with DTE < 1 (expired) is filtered."""
        from lambdas.unusual_volume.worker import _passes_prefilter
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        contract = self._make_contract(expiration_date=yesterday)
        assert _passes_prefilter(contract) is False

    def test_fails_dte_too_high(self):
        """Contract with DTE > 90 is filtered."""
        from lambdas.unusual_volume.worker import _passes_prefilter
        far_future = (date.today() + timedelta(days=180)).isoformat()
        contract = self._make_contract(expiration_date=far_future)
        assert _passes_prefilter(contract) is False

    def test_zero_mid_passes_spread_check(self):
        """Contract with bid=0, ask=0 passes spread check (mid=0 skip)."""
        from lambdas.unusual_volume.worker import _passes_prefilter
        contract = self._make_contract(bid=0, ask=0)
        # mid=0 so spread check is skipped, but volume/oi/dte must pass
        assert _passes_prefilter(contract) is True

    def test_invalid_expiration_date_fails(self):
        """Contract with unparseable expiration date fails."""
        from lambdas.unusual_volume.worker import _passes_prefilter
        contract = self._make_contract(expiration_date="not-a-date")
        assert _passes_prefilter(contract) is False


# ============================================================================
# Worker Lambda: _calculate_oi_change_pct
# ============================================================================


class TestWorkerOIChange:
    """Test OI change percentage calculation."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:test"
        yield

    def test_positive_oi_change(self):
        from lambdas.unusual_volume.worker import _calculate_oi_change_pct
        # 1200 today, 1000 prior → +20%
        assert _calculate_oi_change_pct(1200, 1000) == pytest.approx(20.0)

    def test_negative_oi_change(self):
        from lambdas.unusual_volume.worker import _calculate_oi_change_pct
        # 800 today, 1000 prior → -20%
        assert _calculate_oi_change_pct(800, 1000) == pytest.approx(-20.0)

    def test_zero_prior_returns_zero(self):
        from lambdas.unusual_volume.worker import _calculate_oi_change_pct
        assert _calculate_oi_change_pct(500, 0) == 0.0

    def test_negative_prior_returns_zero(self):
        from lambdas.unusual_volume.worker import _calculate_oi_change_pct
        assert _calculate_oi_change_pct(500, -10) == 0.0

    def test_no_change(self):
        from lambdas.unusual_volume.worker import _calculate_oi_change_pct
        assert _calculate_oi_change_pct(1000, 1000) == pytest.approx(0.0)


# ============================================================================
# Worker Lambda: Full scan mode (_process_ticker)
# ============================================================================


class TestWorkerScanMode:
    """Test the full _process_ticker scan pipeline."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:test"
        yield

    def test_process_ticker_no_options(self):
        """Returns NO_OPTIONS when options chain is empty."""
        from lambdas.unusual_volume.worker import _process_ticker

        with patch("lambdas.unusual_volume.worker._fetch_options_chain", return_value=[]):
            result = _process_ticker("scan-001", "AAPL")

        assert result["status"] == "NO_OPTIONS"
        assert result["contracts_scanned"] == 0

    def test_process_ticker_with_candidates(self):
        """Full scan finds candidates when thresholds are exceeded."""
        from lambdas.unusual_volume.worker import _process_ticker

        exp_date = (date.today() + timedelta(days=30)).isoformat()
        mock_chain = [
            {
                "details": {
                    "contract_type": "call",
                    "ticker": "O:AAPL260320C00185000",
                    "strike_price": 185,
                    "expiration_date": exp_date,
                },
                "day": {"volume": 500},
                "open_interest": 5000,
                "underlying_asset": {"ticker": "AAPL", "price": 189.0},
                "greeks": {"delta": 0.55, "gamma": 0.03, "theta": -0.08, "vega": 0.25,
                           "implied_volatility": 0.32},
                "last_quote": {"bid": 5.0, "ask": 5.4},
            }
        ]

        mock_candidates = MagicMock()

        with patch("lambdas.unusual_volume.worker._fetch_options_chain", return_value=mock_chain), \
             patch("lambdas.unusual_volume.worker._get_expected_volume", return_value=100.0), \
             patch("lambdas.unusual_volume.worker._get_prior_oi", return_value=4500), \
             patch("lambdas.unusual_volume.worker.candidates_table", mock_candidates):
            result = _process_ticker("scan-001", "AAPL")

        assert result["status"] == "OK"
        assert result["contracts_scanned"] == 1
        assert result["candidates_found"] == 1
        mock_candidates.put_item.assert_called_once()

    def test_process_ticker_no_baseline(self):
        """Contract with no baseline volume stats is skipped."""
        from lambdas.unusual_volume.worker import _process_ticker

        exp_date = (date.today() + timedelta(days=30)).isoformat()
        mock_chain = [
            {
                "details": {
                    "contract_type": "call",
                    "ticker": "O:AAPL260320C00185000",
                    "strike_price": 185,
                    "expiration_date": exp_date,
                },
                "day": {"volume": 500},
                "open_interest": 5000,
                "underlying_asset": {"ticker": "AAPL", "price": 189.0},
                "greeks": {},
                "last_quote": {"bid": 5.0, "ask": 5.4},
            }
        ]

        with patch("lambdas.unusual_volume.worker._fetch_options_chain", return_value=mock_chain), \
             patch("lambdas.unusual_volume.worker._get_expected_volume", return_value=None):
            result = _process_ticker("scan-001", "AAPL")

        assert result["candidates_found"] == 0

    def test_process_ticker_below_threshold(self):
        """Contract with volume ratio below threshold is not a candidate."""
        from lambdas.unusual_volume.worker import _process_ticker

        exp_date = (date.today() + timedelta(days=30)).isoformat()
        mock_chain = [
            {
                "details": {
                    "contract_type": "call",
                    "ticker": "O:AAPL260320C00185000",
                    "strike_price": 185,
                    "expiration_date": exp_date,
                },
                "day": {"volume": 150},  # Volume ratio will be 150/200 = 0.75
                "open_interest": 5000,
                "underlying_asset": {"ticker": "AAPL", "price": 189.0},
                "greeks": {},
                "last_quote": {"bid": 5.0, "ask": 5.4},
            }
        ]

        with patch("lambdas.unusual_volume.worker._fetch_options_chain", return_value=mock_chain), \
             patch("lambdas.unusual_volume.worker._get_expected_volume", return_value=200.0), \
             patch("lambdas.unusual_volume.worker._get_prior_oi", return_value=5000):
            result = _process_ticker("scan-001", "AAPL")

        assert result["candidates_found"] == 0


# ============================================================================
# Worker Lambda: lambda_handler
# ============================================================================


class TestWorkerHandler:
    """Test the worker Lambda handler routing."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:test"
        yield

    def test_handler_routes_to_scan(self):
        """Default mode calls _process_ticker."""
        from lambdas.unusual_volume.worker import lambda_handler

        with patch("lambdas.unusual_volume.worker._process_ticker") as mock_proc:
            mock_proc.return_value = {"ticker": "AAPL", "status": "OK"}
            event = {
                "Records": [{
                    "body": json.dumps({"scan_id": "s1", "ticker": "AAPL"})
                }]
            }
            result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["records_processed"] == 1
        mock_proc.assert_called_once_with("s1", "AAPL")

    def test_handler_skips_missing_ticker(self):
        """Records without ticker are skipped gracefully."""
        from lambdas.unusual_volume.worker import lambda_handler

        event = {
            "Records": [{
                "body": json.dumps({"scan_id": "s1"})
            }]
        }
        result = lambda_handler(event, None)
        body = json.loads(result["body"])
        assert body["records_processed"] == 0

    def test_handler_catches_exceptions(self):
        """Exceptions in processing are caught and reported."""
        from lambdas.unusual_volume.worker import lambda_handler

        with patch("lambdas.unusual_volume.worker._process_ticker",
                   side_effect=Exception("boom")):
            event = {
                "Records": [{
                    "body": json.dumps({"scan_id": "s1", "ticker": "AAPL"})
                }]
            }
            result = lambda_handler(event, None)

        body = json.loads(result["body"])
        assert body["records_processed"] == 1


# ============================================================================
# Nightly Stats: _process_ticker_from_histories
# ============================================================================


class TestNightlyStatsProcessing:
    """Test nightly stats computation via _process_ticker."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:test"
        yield

    def _make_history(self, days=20, volume=100, oi=500):
        """Build a list of history dicts."""
        today = date.today()
        return [
            {
                "date": (today - timedelta(days=days - i)).isoformat(),
                "volume": volume + i,
                "open_interest": oi + i * 10,
            }
            for i in range(days)
        ]

    def test_empty_histories_returns_zero(self):
        from lambdas.unusual_volume.nightly_stats import _process_ticker

        with patch("lambdas.unusual_volume.nightly_stats._fetch_contract_histories",
                   return_value={}):
            contract_count, bucket_count = _process_ticker("AAPL", "2026-01-17", 99999999)
        assert contract_count == 0
        assert bucket_count == 0

    def test_contract_with_sufficient_history(self):
        """Contract with >= 5 days produces contract-level stats."""
        from lambdas.unusual_volume.nightly_stats import _process_ticker

        history = self._make_history(days=20)
        contract_histories = {"AAPL250320C00185000": history}

        with patch("lambdas.unusual_volume.nightly_stats._fetch_contract_histories",
                   return_value=contract_histories), \
             patch("lambdas.unusual_volume.nightly_stats.volume_stats_table") as mock_table:
            contract_count, bucket_count = _process_ticker("AAPL", "2026-01-17", 99999999)

        assert contract_count == 1
        mock_table.put_item.assert_called()
        item = mock_table.put_item.call_args[1]["Item"]
        assert "avg_volume_20d" in item
        assert item["underlying_ticker"] == "AAPL"

    def test_contract_with_insufficient_history_goes_to_bucket(self):
        """Contract with < 5 days goes into bucket aggregation."""
        from lambdas.unusual_volume.nightly_stats import _process_ticker

        history = self._make_history(days=3)
        contract_histories = {"AAPL250320C00185000": history}

        with patch("lambdas.unusual_volume.nightly_stats._fetch_contract_histories",
                   return_value=contract_histories), \
             patch("lambdas.unusual_volume.nightly_stats.volume_stats_table"):
            contract_count, bucket_count = _process_ticker("AAPL", "2026-01-17", 99999999)

        assert contract_count == 0
        assert bucket_count >= 1

    def test_mixed_history_depths(self):
        """Mix of sufficient and insufficient history contracts."""
        from lambdas.unusual_volume.nightly_stats import _process_ticker

        contract_histories = {
            "AAPL250320C00185000": self._make_history(days=20),
            "AAPL250320C00190000": self._make_history(days=3),
            "AAPL250320P00180000": self._make_history(days=10),
        }

        with patch("lambdas.unusual_volume.nightly_stats._fetch_contract_histories",
                   return_value=contract_histories), \
             patch("lambdas.unusual_volume.nightly_stats.volume_stats_table"):
            contract_count, bucket_count = _process_ticker("AAPL", "2026-01-17", 99999999)

        assert contract_count == 2


# ============================================================================
# Nightly Stats: _write_contract_stats
# ============================================================================


class TestNightlyStatsContractWrite:
    """Test contract stats writing."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:test"
        yield

    def test_write_contract_stats_calculates_averages(self):
        """Verifies avg_volume_20d and avg_volume_10d computation."""
        from lambdas.unusual_volume.nightly_stats import _write_contract_stats

        today = date.today()
        history = [
            {
                "date": (today - timedelta(days=20 - i)).isoformat(),
                "volume": 100 + i * 10,
                "open_interest": 5000,
            }
            for i in range(20)
        ]

        with patch("lambdas.unusual_volume.nightly_stats.volume_stats_table") as mock_table:
            _write_contract_stats(
                "AAPL250320C00185000", "AAPL", history, "2026-01-17", 99999999
            )

        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        assert float(item["avg_volume_20d"]) > 0
        assert float(item["avg_volume_10d"]) > 0
        assert item["volume_history_days"] == 20

    def test_write_contract_stats_insufficient_data(self):
        """Does not write when not enough volume data after filtering."""
        from lambdas.unusual_volume.nightly_stats import _write_contract_stats

        history = [
            {"date": "2026-01-15", "volume": None, "open_interest": 5000},
            {"date": "2026-01-16", "volume": None, "open_interest": 5100},
            {"date": "2026-01-17", "volume": 100, "open_interest": 5200},
        ]

        with patch("lambdas.unusual_volume.nightly_stats.volume_stats_table") as mock_table:
            _write_contract_stats(
                "AAPL250320C00185000", "AAPL", history, "2026-01-17", 99999999
            )

        # Only 1 non-None volume < MIN_HISTORY_DAYS (5) → should not write
        mock_table.put_item.assert_not_called()

    def test_write_contract_stats_invalid_occ_symbol(self):
        """Does not write for unparseable OCC symbol."""
        from lambdas.unusual_volume.nightly_stats import _write_contract_stats

        today = date.today()
        history = [
            {
                "date": (today - timedelta(days=10 - i)).isoformat(),
                "volume": 100,
                "open_interest": 5000,
            }
            for i in range(10)
        ]

        with patch("lambdas.unusual_volume.nightly_stats.volume_stats_table") as mock_table:
            _write_contract_stats(
                "INVALID_TICKER", "AAPL", history, "2026-01-17", 99999999
            )
        mock_table.put_item.assert_not_called()


# ============================================================================
# Nightly Stats: _compute_bucket_stats
# ============================================================================


class TestNightlyStatsBucketComputation:
    """Test bucket-level stats aggregation."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:test"
        yield

    def test_bucket_stats_groups_by_type_and_dte(self):
        """Contracts grouped by option_type and DTE bucket."""
        from lambdas.unusual_volume.nightly_stats import _compute_bucket_stats

        today = date.today()
        history_3d = [
            {
                "date": (today - timedelta(days=2 - i)).isoformat(),
                "volume": 100 + i * 10,
                "open_interest": 5000,
            }
            for i in range(3)
        ]

        insufficient_history = {
            "AAPL250320C00185000": history_3d,
            "AAPL250320C00190000": history_3d,
        }

        with patch("lambdas.unusual_volume.nightly_stats.volume_stats_table"):
            count = _compute_bucket_stats(
                "AAPL", insufficient_history, "2026-01-17", 99999999
            )

        # Both contracts should contribute to the same bucket
        assert count >= 1

    def test_bucket_stats_empty_input(self):
        """Empty insufficient_history produces no bucket stats."""
        from lambdas.unusual_volume.nightly_stats import _compute_bucket_stats

        with patch("lambdas.unusual_volume.nightly_stats.volume_stats_table"):
            count = _compute_bucket_stats(
                "AAPL", {}, "2026-01-17", 99999999
            )
        assert count == 0

    def test_bucket_stats_skips_leaps(self):
        """Contracts with DTE bucket X (LEAPS) are skipped."""
        from lambdas.unusual_volume.nightly_stats import _compute_bucket_stats

        today = date.today()
        history = [
            {
                "date": (today - timedelta(days=2 - i)).isoformat(),
                "volume": 100,
                "open_interest": 5000,
            }
            for i in range(3)
        ]
        # Ticker with far-out expiration to get DTE bucket X
        insufficient_history = {
            "AAPL270320C00185000": history,  # Mar 2027 → DTE > 365 → bucket X
        }

        with patch("lambdas.unusual_volume.nightly_stats.volume_stats_table"):
            count = _compute_bucket_stats(
                "AAPL", insufficient_history, "2026-01-17", 99999999
            )
        # LEAPS should be skipped, so nothing written
        assert count == 0
