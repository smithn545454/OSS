"""Tests for selection/telemetry.py.

Covers SelectedContract, BucketStats, TickerSelectionStats, and SelectionTelemetry.
"""

from __future__ import annotations

import pytest

from app.selection.telemetry import (
    BucketStats,
    SelectedContract,
    SelectionTelemetry,
    TickerSelectionStats,
)


class TestSelectedContract:
    def test_to_dict(self):
        sc = SelectedContract(
            option_ticker="O:AAPL",
            strike=150.0,
            dte=30,
            delta=0.45,
            rank_score=75.5,
            open_interest=1000,
            volume=200,
            spread_pct=3.5,
            mid=5.25,
        )
        d = sc.to_dict()
        assert d["option_ticker"] == "O:AAPL"
        assert d["delta"] == 0.45
        assert d["rank_score"] == 75.5


class TestBucketStats:
    def test_to_dict(self):
        bs = BucketStats(
            bucket="B",
            side="CALL",
            contracts_in_dte_range=50,
            survived_delta_filter=30,
            survived_liquidity_filter=20,
            survived_moneyness_filter=15,
            selected_count=3,
        )
        d = bs.to_dict()
        assert d["bucket"] == "B"
        assert d["selected_count"] == 3


class TestTickerSelectionStats:
    def test_to_dict(self):
        ts = TickerSelectionStats(
            underlying_ticker="AAPL",
            underlying_price=150.5,
            contracts_in_chain=200,
            total_selected=5,
        )
        d = ts.to_dict()
        assert d["underlying_ticker"] == "AAPL"
        assert d["underlying_price"] == 150.5


class TestSelectionTelemetry:
    def test_start_ticker(self):
        tel = SelectionTelemetry()
        tel.start_ticker("AAPL", 150.0, 200)
        stats = tel.get_ticker_stats("AAPL")
        assert stats is not None
        assert stats.contracts_in_chain == 200

    def test_record_bucket_stats(self):
        tel = SelectionTelemetry()
        tel.start_ticker("AAPL", 150.0, 200)
        bs = BucketStats(bucket="B", side="CALL", contracts_in_dte_range=50, selected_count=3)
        tel.record_bucket_stats("AAPL", bs)
        stats = tel.get_ticker_stats("AAPL")
        assert stats.total_selected == 3

    def test_record_unknown_ticker(self):
        tel = SelectionTelemetry()
        bs = BucketStats(bucket="B", side="CALL", selected_count=1)
        tel.record_bucket_stats("UNKNOWN", bs)
        assert tel.get_ticker_stats("UNKNOWN") is None

    def test_get_all_stats(self):
        tel = SelectionTelemetry()
        tel.start_ticker("AAPL", 150.0, 200)
        tel.start_ticker("TSLA", 250.0, 300)
        all_stats = tel.get_all_stats()
        assert len(all_stats) == 2

    def test_get_summary(self):
        tel = SelectionTelemetry()
        tel.start_ticker("AAPL", 150.0, 200)
        bs = BucketStats(bucket="B", side="CALL", contracts_in_dte_range=50, selected_count=3)
        tel.record_bucket_stats("AAPL", bs)
        summary = tel.get_summary()
        assert summary["tickers_processed"] == 1
        assert summary["total_contracts_selected"] == 3
        assert summary["selection_rate_pct"] > 0

    def test_to_stage_metadata(self):
        tel = SelectionTelemetry()
        tel.start_ticker("AAPL", 150.0, 200)
        meta = tel.to_stage_metadata()
        assert "summary" in meta
        assert "ticker_stats" not in meta
