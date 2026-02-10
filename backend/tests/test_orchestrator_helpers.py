"""Tests for ScannerOrchestrator pure-logic helpers.

Covers _build_scanner_triggers_map, _build_features_map,
_compute_volume_from_chain, and _chunk_list from main.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.main import _chunk_list


# ---------------------------------------------------------------------------
# Tests: _chunk_list (from main.py)
# ---------------------------------------------------------------------------


class TestChunkList:

    def test_exact_chunks(self):
        result = _chunk_list([1, 2, 3, 4, 5, 6], 3)
        assert result == [[1, 2, 3], [4, 5, 6]]

    def test_uneven_chunks(self):
        result = _chunk_list([1, 2, 3, 4, 5], 3)
        assert result == [[1, 2, 3], [4, 5]]

    def test_empty_list(self):
        result = _chunk_list([], 5)
        assert result == []

    def test_single_item_chunks(self):
        result = _chunk_list([1, 2, 3], 1)
        assert result == [[1], [2], [3]]

    def test_chunk_larger_than_list(self):
        result = _chunk_list([1, 2], 10)
        assert result == [[1, 2]]


# ---------------------------------------------------------------------------
# Tests: _compute_volume_from_chain (pure logic)
# ---------------------------------------------------------------------------


class TestComputeVolumeFromChain:

    @pytest.fixture
    def orch(self):
        from app.scanners.orchestrator import ScannerOrchestrator
        # Minimal init — we only need the method
        o = object.__new__(ScannerOrchestrator)
        return o

    def test_basic_volume(self, orch):
        chain = [
            {"day": {"volume": 100, "open_interest": 500}, "details": {"contract_type": "CALL"}},
            {"day": {"volume": 50, "open_interest": 300}, "details": {"contract_type": "PUT"}},
        ]
        result = orch._compute_volume_from_chain(chain, "AAPL")
        assert result.total_call_volume == 100
        assert result.total_put_volume == 50
        assert result.call_put_volume_ratio == 2.0

    def test_zero_put_volume(self, orch):
        chain = [
            {"day": {"volume": 200, "open_interest": 500}, "details": {"contract_type": "CALL"}},
        ]
        result = orch._compute_volume_from_chain(chain, "AAPL")
        assert result.call_put_volume_ratio == 999.0

    def test_zero_call_volume(self, orch):
        chain = [
            {"day": {"volume": 200, "open_interest": 500}, "details": {"contract_type": "PUT"}},
        ]
        result = orch._compute_volume_from_chain(chain, "AAPL")
        assert result.call_put_volume_ratio == 0.0

    def test_empty_chain(self, orch):
        result = orch._compute_volume_from_chain([], "AAPL")
        assert result.total_call_volume == 0
        assert result.total_put_volume == 0
        assert result.call_put_volume_ratio == 0.0

    def test_missing_fields_default_to_zero(self, orch):
        chain = [
            {"day": {}, "details": {"contract_type": "CALL"}},
            {"day": {"volume": None}, "details": {"contract_type": "PUT"}},
        ]
        result = orch._compute_volume_from_chain(chain, "AAPL")
        assert result.total_call_volume == 0
        assert result.total_put_volume == 0

    def test_ratio_capped_at_999(self, orch):
        chain = [
            {"day": {"volume": 10000, "open_interest": 500}, "details": {"contract_type": "CALL"}},
            {"day": {"volume": 1, "open_interest": 10}, "details": {"contract_type": "PUT"}},
        ]
        result = orch._compute_volume_from_chain(chain, "AAPL")
        assert result.call_put_volume_ratio == 999.0


# ---------------------------------------------------------------------------
# Tests: _build_scanner_triggers_map
# ---------------------------------------------------------------------------


class TestBuildScannerTriggersMap:

    @pytest.fixture
    def orch(self):
        from app.scanners.orchestrator import ScannerOrchestrator
        o = object.__new__(ScannerOrchestrator)
        return o

    def test_maps_triggers_by_ticker(self, orch):
        eval1 = MagicMock(evaluation_id="eval-1", underlying_ticker="AAPL")
        trigger = MagicMock()
        opp = MagicMock(underlying_ticker="AAPL", scanner_triggers=[trigger])

        result = orch._build_scanner_triggers_map([eval1], [opp])
        assert "eval-1" in result
        assert result["eval-1"] == [trigger]

    def test_no_matching_opp(self, orch):
        eval1 = MagicMock(evaluation_id="eval-1", underlying_ticker="MSFT")
        opp = MagicMock(underlying_ticker="AAPL", scanner_triggers=[])

        result = orch._build_scanner_triggers_map([eval1], [opp])
        assert result["eval-1"] == []

    def test_empty_evaluations(self, orch):
        result = orch._build_scanner_triggers_map([], [])
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: _build_features_map
# ---------------------------------------------------------------------------


class TestBuildFeaturesMap:

    @pytest.fixture
    def orch(self):
        from app.scanners.orchestrator import ScannerOrchestrator
        o = object.__new__(ScannerOrchestrator)
        return o

    def test_extracts_features(self, orch):
        fs = MagicMock(
            evaluation_id="eval-1",
            close=180.0, sma20=178.0, sma50=175.0,
            return_5d=2.5, return_20d=5.0,
            trend_aligned_bullish=True, trend_aligned_bearish=False,
            atr14=3.5, atr14_pct=1.9,
            spy_return_5d=1.0, spy_return_20d=3.0,
            rs_5d=1.5, rs_20d=2.0,
            rv20=0.25, iv=0.30, iv_rv_ratio=1.2,
            iv_percentile=60, iv_10d_change=-0.02,
            iv_regime="NORMAL",
            theta_pct=-0.5, theta_adjusted_edge=0.1,
            oi_5d_change_pct=15.0,
            days_to_earnings=30, recent_sec_filing=False,
        )

        result = orch._build_features_map([fs])
        assert "eval-1" in result
        assert result["eval-1"]["close"] == 180.0
        assert result["eval-1"]["iv_regime"] == "NORMAL"

    def test_empty_feature_sets(self, orch):
        result = orch._build_features_map([])
        assert result == {}
