"""Tests for Polygon.io response parsing logic.

Tests the pure data transformation patterns used to parse Polygon API responses
into application domain objects. These tests validate parsing correctness
without making HTTP calls.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.polygon import AggregatedOptionsVolume, DailyBar


# ============================================================================
# DailyBar Dataclass Tests
# ============================================================================


class TestDailyBar:
    """Test DailyBar construction and field access."""

    def test_basic_construction(self):
        bar = DailyBar(
            ticker="AAPL",
            date="2026-01-17",
            open=180.0,
            high=185.5,
            low=178.2,
            close=183.0,
            volume=50_000_000,
        )
        assert bar.ticker == "AAPL"
        assert bar.date == "2026-01-17"
        assert bar.open == 180.0
        assert bar.high == 185.5
        assert bar.low == 178.2
        assert bar.close == 183.0
        assert bar.volume == 50_000_000
        assert bar.vwap is None

    def test_with_vwap(self):
        bar = DailyBar(
            ticker="MSFT",
            date="2026-01-17",
            open=400.0,
            high=410.0,
            low=398.0,
            close=405.0,
            volume=30_000_000,
            vwap=403.5,
        )
        assert bar.vwap == 403.5


# ============================================================================
# AggregatedOptionsVolume Dataclass Tests
# ============================================================================


class TestAggregatedOptionsVolume:

    def test_basic_construction(self):
        vol = AggregatedOptionsVolume(
            ticker="AAPL",
            total_call_volume=100_000,
            total_put_volume=50_000,
            total_call_oi=500_000,
            total_put_oi=300_000,
            call_put_volume_ratio=2.0,
            timestamp="2026-01-17T16:00:00",
        )
        assert vol.ticker == "AAPL"
        assert vol.call_put_volume_ratio == 2.0

    def test_zero_put_volume_ratio(self):
        """Ratio should be inf when put volume is 0 and call volume > 0."""
        vol = AggregatedOptionsVolume(
            ticker="AAPL",
            total_call_volume=100,
            total_put_volume=0,
            total_call_oi=500,
            total_put_oi=0,
            call_put_volume_ratio=float("inf"),
            timestamp="2026-01-17T16:00:00",
        )
        assert vol.call_put_volume_ratio == float("inf")


# ============================================================================
# Polygon Response Parsing Patterns
# ============================================================================


class TestBarResponseParsing:
    """Test the raw bar → DailyBar parsing pattern used in get_daily_bars_parsed."""

    def _parse_raw_bar(self, ticker: str, raw: dict) -> DailyBar:
        """Replicates the parsing logic from PolygonClient.get_daily_bars_parsed."""
        ts = raw.get("t", 0)
        date_str = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        return DailyBar(
            ticker=ticker,
            date=date_str,
            open=raw.get("o", 0.0),
            high=raw.get("h", 0.0),
            low=raw.get("l", 0.0),
            close=raw.get("c", 0.0),
            volume=raw.get("v", 0),
            vwap=raw.get("vw"),
        )

    def test_parse_standard_bar(self):
        raw = {
            "t": 1705536000000,  # 2024-01-18 in ms
            "o": 180.5,
            "h": 185.0,
            "l": 179.0,
            "c": 183.5,
            "v": 45000000,
            "vw": 182.3,
        }
        bar = self._parse_raw_bar("AAPL", raw)
        assert bar.ticker == "AAPL"
        assert bar.date == "2024-01-18"
        assert bar.open == 180.5
        assert bar.high == 185.0
        assert bar.low == 179.0
        assert bar.close == 183.5
        assert bar.volume == 45000000
        assert bar.vwap == 182.3

    def test_parse_bar_missing_fields(self):
        """Missing fields default to 0.0/0."""
        raw = {"t": 1705536000000}
        bar = self._parse_raw_bar("AAPL", raw)
        assert bar.open == 0.0
        assert bar.high == 0.0
        assert bar.low == 0.0
        assert bar.close == 0.0
        assert bar.volume == 0
        assert bar.vwap is None

    def test_parse_bar_zero_timestamp(self):
        """Zero timestamp → epoch start."""
        raw = {"t": 0, "c": 100.0}
        bar = self._parse_raw_bar("AAPL", raw)
        assert bar.date == "1970-01-01"

    def test_parse_bar_no_vwap(self):
        """Bar without vw field → vwap is None."""
        raw = {"t": 1705536000000, "c": 100.0}
        bar = self._parse_raw_bar("AAPL", raw)
        assert bar.vwap is None


class TestGroupedBarParsing:
    """Test the grouped bars → dict[ticker, DailyBar] parsing pattern."""

    def _parse_grouped_results(self, date: str, results: list[dict]) -> dict[str, DailyBar]:
        """Replicates the parsing logic from PolygonClient.get_grouped_daily_bars."""
        output: dict[str, DailyBar] = {}
        for bar in results:
            ticker = bar.get("T", "")
            if ticker:
                output[ticker] = DailyBar(
                    ticker=ticker,
                    date=date,
                    open=bar.get("o", 0.0),
                    high=bar.get("h", 0.0),
                    low=bar.get("l", 0.0),
                    close=bar.get("c", 0.0),
                    volume=bar.get("v", 0),
                    vwap=bar.get("vw"),
                )
        return output

    def test_parse_multiple_tickers(self):
        results = [
            {"T": "AAPL", "o": 180, "h": 185, "l": 178, "c": 183, "v": 50000000},
            {"T": "MSFT", "o": 400, "h": 410, "l": 398, "c": 405, "v": 30000000},
        ]
        parsed = self._parse_grouped_results("2026-01-17", results)
        assert len(parsed) == 2
        assert parsed["AAPL"].close == 183
        assert parsed["MSFT"].close == 405

    def test_skip_empty_ticker(self):
        results = [
            {"T": "", "o": 100, "c": 100},  # Empty ticker
            {"T": "AAPL", "o": 180, "c": 183},
        ]
        parsed = self._parse_grouped_results("2026-01-17", results)
        assert len(parsed) == 1
        assert "AAPL" in parsed

    def test_empty_results(self):
        parsed = self._parse_grouped_results("2026-01-17", [])
        assert len(parsed) == 0


class TestOptionsChainParsing:
    """Test options chain response aggregation pattern."""

    def _aggregate_options_volume(
        self, chain: list[dict]
    ) -> dict:
        """Replicates the aggregation in get_aggregated_options_volume."""
        total_call_volume = 0
        total_put_volume = 0
        total_call_oi = 0
        total_put_oi = 0

        for contract in chain:
            details = contract.get("details", {})
            day = contract.get("day", {})
            contract_type = details.get("contract_type", "").upper()
            volume = day.get("volume", 0) or 0
            oi = contract.get("open_interest", 0) or 0

            if contract_type == "CALL":
                total_call_volume += volume
                total_call_oi += oi
            elif contract_type == "PUT":
                total_put_volume += volume
                total_put_oi += oi

        if total_put_volume > 0:
            ratio = total_call_volume / total_put_volume
        else:
            ratio = float("inf") if total_call_volume > 0 else 0.0

        return {
            "call_volume": total_call_volume,
            "put_volume": total_put_volume,
            "call_oi": total_call_oi,
            "put_oi": total_put_oi,
            "ratio": ratio,
        }

    def test_balanced_volume(self):
        chain = [
            {"details": {"contract_type": "CALL"}, "day": {"volume": 500}, "open_interest": 5000},
            {"details": {"contract_type": "PUT"}, "day": {"volume": 500}, "open_interest": 3000},
        ]
        result = self._aggregate_options_volume(chain)
        assert result["call_volume"] == 500
        assert result["put_volume"] == 500
        assert result["ratio"] == 1.0

    def test_no_puts(self):
        chain = [
            {"details": {"contract_type": "CALL"}, "day": {"volume": 500}, "open_interest": 5000},
        ]
        result = self._aggregate_options_volume(chain)
        assert result["ratio"] == float("inf")

    def test_no_volume_at_all(self):
        chain = [
            {"details": {"contract_type": "CALL"}, "day": {"volume": 0}, "open_interest": 5000},
            {"details": {"contract_type": "PUT"}, "day": {"volume": 0}, "open_interest": 3000},
        ]
        result = self._aggregate_options_volume(chain)
        assert result["ratio"] == 0.0

    def test_null_volume_defaults_to_zero(self):
        chain = [
            {"details": {"contract_type": "CALL"}, "day": {"volume": None}, "open_interest": None},
        ]
        result = self._aggregate_options_volume(chain)
        assert result["call_volume"] == 0
        assert result["call_oi"] == 0

    def test_empty_chain(self):
        result = self._aggregate_options_volume([])
        assert result["ratio"] == 0.0
        assert result["call_volume"] == 0

    def test_mixed_call_put_accumulation(self):
        chain = [
            {"details": {"contract_type": "CALL"}, "day": {"volume": 100}, "open_interest": 1000},
            {"details": {"contract_type": "CALL"}, "day": {"volume": 200}, "open_interest": 2000},
            {"details": {"contract_type": "PUT"}, "day": {"volume": 150}, "open_interest": 1500},
        ]
        result = self._aggregate_options_volume(chain)
        assert result["call_volume"] == 300
        assert result["put_volume"] == 150
        assert result["call_oi"] == 3000
        assert result["put_oi"] == 1500
        assert result["ratio"] == 2.0

    def test_unknown_contract_type_ignored(self):
        chain = [
            {"details": {"contract_type": "CALL"}, "day": {"volume": 100}, "open_interest": 1000},
            {"details": {"contract_type": "SPREAD"}, "day": {"volume": 50}, "open_interest": 500},
        ]
        result = self._aggregate_options_volume(chain)
        assert result["call_volume"] == 100
        assert result["put_volume"] == 0
