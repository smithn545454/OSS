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
             patch("lambdas.unusual_volume.worker._get_previous_close", return_value=189.0), \
             patch("lambdas.unusual_volume.worker._record_oi_snapshots", return_value=1), \
             patch("lambdas.unusual_volume.worker._get_expected_volume", return_value=100.0), \
             patch("lambdas.unusual_volume.worker._get_prior_oi", return_value=4500), \
             patch("lambdas.unusual_volume.worker.candidates_table", mock_candidates):
            result = _process_ticker("scan-001", "AAPL")

        assert result["status"] == "OK"
        assert result["contracts_scanned"] == 1
        assert result["candidates_found"] == 1
        mock_candidates.put_item.assert_called_once()

    def test_process_ticker_no_baseline_no_oi_trigger(self):
        """Contract with no baseline and no OI trigger produces no candidates."""
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
             patch("lambdas.unusual_volume.worker._get_previous_close", return_value=189.0), \
             patch("lambdas.unusual_volume.worker._record_oi_snapshots", return_value=1), \
             patch("lambdas.unusual_volume.worker._get_expected_volume", return_value=None), \
             patch("lambdas.unusual_volume.worker._get_prior_oi", return_value=5000):
            result = _process_ticker("scan-001", "AAPL")

        # No volume baseline (volume_ratio=0) and OI unchanged → no triggers
        assert result["candidates_found"] == 0

    def test_process_ticker_no_baseline_oi_trigger_fires(self):
        """OI trigger fires even when no volume baseline is available."""
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
                "open_interest": 6000,  # +20% from prior_oi of 5000
                "underlying_asset": {"ticker": "AAPL", "price": 189.0},
                "greeks": {},
                "last_quote": {"bid": 5.0, "ask": 5.4},
            }
        ]

        mock_candidates = MagicMock()

        with patch("lambdas.unusual_volume.worker._fetch_options_chain", return_value=mock_chain), \
             patch("lambdas.unusual_volume.worker._get_previous_close", return_value=189.0), \
             patch("lambdas.unusual_volume.worker._record_oi_snapshots", return_value=1), \
             patch("lambdas.unusual_volume.worker._get_expected_volume", return_value=None), \
             patch("lambdas.unusual_volume.worker._get_prior_oi", return_value=5000), \
             patch("lambdas.unusual_volume.worker.candidates_table", mock_candidates):
            result = _process_ticker("scan-001", "AAPL")

        # OI increased 20% which exceeds 15% threshold → OI_INCREASE trigger fires
        assert result["candidates_found"] == 1

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
             patch("lambdas.unusual_volume.worker._get_previous_close", return_value=189.0), \
             patch("lambdas.unusual_volume.worker._record_oi_snapshots", return_value=1), \
             patch("lambdas.unusual_volume.worker._get_expected_volume", return_value=200.0), \
             patch("lambdas.unusual_volume.worker._get_prior_oi", return_value=5000):
            result = _process_ticker("scan-001", "AAPL")

        assert result["candidates_found"] == 0

    def test_process_ticker_day_close_fallback(self):
        """Bid/ask fallback uses day.close with 5% spread when last_quote missing."""
        from lambdas.unusual_volume.worker import _process_ticker

        exp_date = (date.today() + timedelta(days=30)).isoformat()
        mock_chain = [
            {
                "ticker": "O:AAPL260320C00185000",
                "details": {
                    "contract_type": "call",
                    "ticker": "O:AAPL260320C00185000",
                    "strike_price": 185,
                    "expiration_date": exp_date,
                },
                "day": {"volume": 500, "close": 6.0},
                "open_interest": 5000,
                "underlying_asset": {"ticker": "AAPL"},
                "implied_volatility": 0.35,
                "greeks": {"delta": 0.55, "gamma": 0.03, "theta": -0.08, "vega": 0.25},
                "last_quote": {},  # No bid/ask data
            }
        ]

        mock_candidates = MagicMock()

        with patch("lambdas.unusual_volume.worker._fetch_options_chain", return_value=mock_chain), \
             patch("lambdas.unusual_volume.worker._get_previous_close", return_value=189.0), \
             patch("lambdas.unusual_volume.worker._record_oi_snapshots", return_value=1), \
             patch("lambdas.unusual_volume.worker._get_expected_volume", return_value=100.0), \
             patch("lambdas.unusual_volume.worker._get_prior_oi", return_value=4500), \
             patch("lambdas.unusual_volume.worker.candidates_table", mock_candidates):
            result = _process_ticker("scan-001", "AAPL")

        assert result["candidates_found"] == 1
        mock_candidates.put_item.assert_called_once()

        # Verify the persisted bid/ask use the day.close fallback
        item = mock_candidates.put_item.call_args[1]["Item"]
        expected_bid = Decimal(str(6.0 - 6.0 * 0.025))  # 5.85
        expected_ask = Decimal(str(6.0 + 6.0 * 0.025))  # 6.15
        assert item["bid"] == expected_bid
        assert item["ask"] == expected_ask
        assert item["underlying_price"] == Decimal("189.0")

    def test_process_ticker_uses_previous_close_for_price(self):
        """Underlying price comes from previous close API, not underlying_asset."""
        from lambdas.unusual_volume.worker import _process_ticker

        exp_date = (date.today() + timedelta(days=30)).isoformat()
        mock_chain = [
            {
                "ticker": "O:AAPL260320C00185000",
                "details": {
                    "contract_type": "call",
                    "ticker": "O:AAPL260320C00185000",
                    "strike_price": 185,
                    "expiration_date": exp_date,
                },
                "day": {"volume": 500, "close": 6.0},
                "open_interest": 5000,
                "underlying_asset": {"ticker": "AAPL", "price": 0},  # Zero — Basic tier
                "greeks": {"delta": 0.55, "implied_volatility": 0.32},
                "last_quote": {"bid": 5.0, "ask": 5.4},
            }
        ]

        mock_candidates = MagicMock()

        with patch("lambdas.unusual_volume.worker._fetch_options_chain", return_value=mock_chain), \
             patch("lambdas.unusual_volume.worker._get_previous_close", return_value=255.0), \
             patch("lambdas.unusual_volume.worker._record_oi_snapshots", return_value=1), \
             patch("lambdas.unusual_volume.worker._get_expected_volume", return_value=100.0), \
             patch("lambdas.unusual_volume.worker._get_prior_oi", return_value=4500), \
             patch("lambdas.unusual_volume.worker.candidates_table", mock_candidates):
            result = _process_ticker("scan-001", "AAPL")

        assert result["candidates_found"] == 1
        item = mock_candidates.put_item.call_args[1]["Item"]
        # underlying_price should be 255.0 from previous close, not 0 from underlying_asset
        assert item["underlying_price"] == Decimal("255.0")


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
        mock_proc.assert_called_once_with("s1", "AAPL", False)

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
# Worker Lambda: _get_previous_close
# ============================================================================


class TestGetPreviousClose:
    """Test the _get_previous_close function."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:test"
        yield

    def test_returns_close_price(self):
        """Returns close price from Polygon previous close endpoint."""
        from lambdas.unusual_volume.worker import _get_previous_close

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps({
            "results": [{"c": 189.50, "h": 191.0, "l": 188.0, "o": 190.0, "v": 50000000}]
        }).encode()

        with patch("lambdas.unusual_volume.worker._get_polygon_api_key", return_value="test-key"), \
             patch("lambdas.unusual_volume.worker.http") as mock_http:
            mock_http.request.return_value = mock_response
            price = _get_previous_close("AAPL")

        assert price == 189.50

    def test_returns_zero_on_api_error(self):
        """Returns 0 when API returns non-200."""
        from lambdas.unusual_volume.worker import _get_previous_close

        mock_response = MagicMock()
        mock_response.status = 403

        with patch("lambdas.unusual_volume.worker._get_polygon_api_key", return_value="test-key"), \
             patch("lambdas.unusual_volume.worker.http") as mock_http:
            mock_http.request.return_value = mock_response
            price = _get_previous_close("AAPL")

        assert price == 0

    def test_returns_zero_on_empty_results(self):
        """Returns 0 when API returns empty results."""
        from lambdas.unusual_volume.worker import _get_previous_close

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps({"results": []}).encode()

        with patch("lambdas.unusual_volume.worker._get_polygon_api_key", return_value="test-key"), \
             patch("lambdas.unusual_volume.worker.http") as mock_http:
            mock_http.request.return_value = mock_response
            price = _get_previous_close("AAPL")

        assert price == 0

    def test_returns_zero_on_exception(self):
        """Returns 0 when an exception occurs."""
        from lambdas.unusual_volume.worker import _get_previous_close

        with patch("lambdas.unusual_volume.worker._get_polygon_api_key", return_value="test-key"), \
             patch("lambdas.unusual_volume.worker.http") as mock_http:
            mock_http.request.side_effect = Exception("connection timeout")
            price = _get_previous_close("AAPL")

        assert price == 0


# ============================================================================
# Worker Lambda: _write_candidate bid/ask fallback and IV fix
# ============================================================================


class TestWriteCandidatePricingFixes:
    """Test bid/ask fallback and IV field location in _write_candidate."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        os.environ["POLYGON_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:test"
        yield

    def _make_contract(self, bid=0, ask=0, day_close=0, iv_top_level=None, iv_greeks=None):
        exp_date = (date.today() + timedelta(days=30)).isoformat()
        greeks = {"delta": 0.5, "gamma": 0.03, "theta": -0.08, "vega": 0.25}
        if iv_greeks is not None:
            greeks["implied_volatility"] = iv_greeks
        contract = {
            "details": {
                "ticker": "O:AAPL260320C00185000",
                "contract_type": "call",
                "strike_price": 185,
                "expiration_date": exp_date,
            },
            "day": {"volume": 500, "close": day_close},
            "open_interest": 5000,
            "greeks": greeks,
            "last_quote": {"bid": bid, "ask": ask},
        }
        if iv_top_level is not None:
            contract["implied_volatility"] = iv_top_level
        return contract

    def test_day_close_fallback_when_no_bid_ask(self):
        """Falls back to day.close with 5% spread when bid/ask are zero."""
        from lambdas.unusual_volume.worker import _write_candidate

        contract = self._make_contract(bid=0, ask=0, day_close=6.0)
        mock_candidates = MagicMock()

        with patch("lambdas.unusual_volume.worker.candidates_table", mock_candidates):
            _write_candidate(
                scan_id="scan-001",
                contract=contract,
                ticker="AAPL",
                underlying_price=189.0,
                volume_ratio=5.0,
                expected_volume=100.0,
                today_oi=5000,
                prior_oi=4500,
                oi_change_pct=11.11,
                trigger_reasons=["VOL_SPIKE"],
                priority_score=85.0,
                volume_source="CONTRACT_SPECIFIC",
                ttl=9999999,
            )

        item = mock_candidates.put_item.call_args[1]["Item"]
        assert item["bid"] == Decimal(str(6.0 - 6.0 * 0.025))
        assert item["ask"] == Decimal(str(6.0 + 6.0 * 0.025))
        assert item["underlying_price"] == Decimal("189.0")

    def test_iv_read_from_contract_top_level(self):
        """IV is read from contract top level, not just greeks."""
        from lambdas.unusual_volume.worker import _write_candidate

        # IV at top level, not in greeks
        contract = self._make_contract(bid=5.0, ask=5.4, iv_top_level=0.42, iv_greeks=None)
        mock_candidates = MagicMock()

        with patch("lambdas.unusual_volume.worker.candidates_table", mock_candidates):
            _write_candidate(
                scan_id="scan-001",
                contract=contract,
                ticker="AAPL",
                underlying_price=189.0,
                volume_ratio=5.0,
                expected_volume=100.0,
                today_oi=5000,
                prior_oi=4500,
                oi_change_pct=11.11,
                trigger_reasons=["VOL_SPIKE"],
                priority_score=85.0,
                volume_source="CONTRACT_SPECIFIC",
                ttl=9999999,
            )

        item = mock_candidates.put_item.call_args[1]["Item"]
        assert item["iv"] == Decimal("0.42")

    def test_iv_falls_back_to_greeks(self):
        """IV falls back to greeks when not at contract top level."""
        from lambdas.unusual_volume.worker import _write_candidate

        contract = self._make_contract(bid=5.0, ask=5.4, iv_top_level=None, iv_greeks=0.35)
        mock_candidates = MagicMock()

        with patch("lambdas.unusual_volume.worker.candidates_table", mock_candidates):
            _write_candidate(
                scan_id="scan-001",
                contract=contract,
                ticker="AAPL",
                underlying_price=189.0,
                volume_ratio=5.0,
                expected_volume=100.0,
                today_oi=5000,
                prior_oi=4500,
                oi_change_pct=11.11,
                trigger_reasons=["VOL_SPIKE"],
                priority_score=85.0,
                volume_source="CONTRACT_SPECIFIC",
                ttl=9999999,
            )

        item = mock_candidates.put_item.call_args[1]["Item"]
        assert item["iv"] == Decimal("0.35")

    def test_no_fallback_when_bid_ask_present(self):
        """Does NOT apply fallback when bid/ask are already present."""
        from lambdas.unusual_volume.worker import _write_candidate

        contract = self._make_contract(bid=5.0, ask=5.4, day_close=6.0)
        mock_candidates = MagicMock()

        with patch("lambdas.unusual_volume.worker.candidates_table", mock_candidates):
            _write_candidate(
                scan_id="scan-001",
                contract=contract,
                ticker="AAPL",
                underlying_price=189.0,
                volume_ratio=5.0,
                expected_volume=100.0,
                today_oi=5000,
                prior_oi=4500,
                oi_change_pct=11.11,
                trigger_reasons=["VOL_SPIKE"],
                priority_score=85.0,
                volume_source="CONTRACT_SPECIFIC",
                ttl=9999999,
            )

        item = mock_candidates.put_item.call_args[1]["Item"]
        assert item["bid"] == Decimal("5.0")
        assert item["ask"] == Decimal("5.4")


# ============================================================================
# Nightly Stats: Simplified aggregate reporting
# ============================================================================


class TestNightlyStatsHandler:
    """Test the simplified nightly stats Lambda handler."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        yield

    def test_handler_returns_200(self):
        """Handler returns 200 with metrics."""
        from lambdas.unusual_volume.nightly_stats import lambda_handler

        with patch("lambdas.unusual_volume.nightly_stats._compute_scan_metrics",
                   return_value={"total_scans": 5, "completed": 5}), \
             patch("lambdas.unusual_volume.nightly_stats._compute_candidate_metrics",
                   return_value={"pending": 10, "processed": 20, "filtered": 5}), \
             patch("lambdas.unusual_volume.nightly_stats._write_daily_summary"):
            result = lambda_handler({}, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "scan_metrics" in body
        assert "candidate_metrics" in body

    def test_handler_handles_errors(self):
        """Handler catches exceptions and returns 500."""
        from lambdas.unusual_volume.nightly_stats import lambda_handler

        with patch("lambdas.unusual_volume.nightly_stats._compute_scan_metrics",
                   side_effect=Exception("boom")):
            result = lambda_handler({}, None)

        assert result["statusCode"] == 500


class TestNightlyStatsScanMetrics:
    """Test scan metrics computation."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        yield

    def test_scan_metrics_computes_counts(self):
        from lambdas.unusual_volume.nightly_stats import _compute_scan_metrics

        mock_items = [
            {"status": "COMPLETED", "tickers_processed": 100, "candidates_found": 15},
            {"status": "COMPLETED", "tickers_processed": 100, "candidates_found": 12},
            {"status": "FAILED", "tickers_processed": 0, "candidates_found": 0},
        ]

        with patch("lambdas.unusual_volume.nightly_stats.scan_runs_table") as mock_table:
            mock_table.query.return_value = {"Items": mock_items}
            result = _compute_scan_metrics("2026-02-26")

        assert result["total_scans"] == 3
        assert result["completed"] == 2
        assert result["failed"] == 1
        assert result["total_tickers_processed"] == 200
        assert result["total_candidates_found"] == 27

    def test_scan_metrics_empty(self):
        from lambdas.unusual_volume.nightly_stats import _compute_scan_metrics

        with patch("lambdas.unusual_volume.nightly_stats.scan_runs_table") as mock_table:
            mock_table.query.return_value = {"Items": []}
            result = _compute_scan_metrics("2026-02-26")

        assert result["total_scans"] == 0


class TestNightlyStatsCandidateMetrics:
    """Test candidate metrics computation."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["DYNAMODB_TABLE_PREFIX"] = "oss-test"
        yield

    def test_candidate_metrics_queries_all_statuses(self):
        from lambdas.unusual_volume.nightly_stats import _compute_candidate_metrics

        with patch("lambdas.unusual_volume.nightly_stats.candidates_table") as mock_table:
            mock_table.query.return_value = {"Count": 42}
            result = _compute_candidate_metrics("2026-02-26")

        assert result["pending"] == 42
        assert result["processed"] == 42
        assert result["filtered"] == 42
