"""Tests for scanners/merger.py.

Covers all merger functions: priority, direction, merge logic.
"""

from __future__ import annotations

import pytest

from app.core.schemas import (
    DirectionHint,
    Opportunity,
    ScannerTrigger,
    ScannerType,
)
from app.scanners.base import ScanResult
from app.scanners.merger import (
    OpportunityMerger,
    calculate_priority_score,
    get_direction_from_trigger,
    get_scanner_base_priority,
    merge_scan_results,
    merge_triggers_to_opportunity,
    resolve_direction_hints,
)


def _trigger(scanner_type: ScannerType, **metrics) -> ScannerTrigger:
    return ScannerTrigger(
        scanner_type=scanner_type,
        reason_codes=["TEST_TRIGGER"],
        triggered_at="2026-01-17T10:00:00Z",
        metrics=metrics,
    )


def _scan_result(ticker: str, triggered: bool, trigger: ScannerTrigger = None) -> ScanResult:
    return ScanResult(
        ticker=ticker,
        triggered=triggered,
        trigger=trigger,
    )


# ---------------------------------------------------------------------------
# get_scanner_base_priority
# ---------------------------------------------------------------------------


class TestGetScannerBasePriority:
    def test_breakout_with_volume(self):
        t = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        assert get_scanner_base_priority(t) == 75

    def test_breakout_without_volume(self):
        t = _trigger(ScannerType.BREAKOUT, volume_ratio=0.5)
        assert get_scanner_base_priority(t) == 65

    def test_breakdown(self):
        t = _trigger(ScannerType.BREAKDOWN, volume_ratio=1.5)
        assert get_scanner_base_priority(t) == 75

    def test_compression(self):
        t = _trigger(ScannerType.COMPRESSION_EXPANSION)
        assert get_scanner_base_priority(t) == 70

    def test_unusual_volume_high(self):
        t = _trigger(ScannerType.UNUSUAL_VOLUME, volume_ratio=4.0)
        assert get_scanner_base_priority(t) == 65

    def test_unusual_volume_normal(self):
        t = _trigger(ScannerType.UNUSUAL_VOLUME, volume_ratio=2.5)
        assert get_scanner_base_priority(t) == 55

    def test_cheap_options(self):
        t = _trigger(ScannerType.CHEAP_OPTIONS)
        assert get_scanner_base_priority(t) == 50


# ---------------------------------------------------------------------------
# get_direction_from_trigger
# ---------------------------------------------------------------------------


class TestGetDirectionFromTrigger:
    def test_breakout(self):
        t = _trigger(ScannerType.BREAKOUT)
        assert get_direction_from_trigger(t) == DirectionHint.CALL

    def test_breakdown(self):
        t = _trigger(ScannerType.BREAKDOWN)
        assert get_direction_from_trigger(t) == DirectionHint.PUT

    def test_compression_up(self):
        t = _trigger(ScannerType.COMPRESSION_EXPANSION, triggered_direction="UP")
        assert get_direction_from_trigger(t) == DirectionHint.CALL

    def test_compression_down(self):
        t = _trigger(ScannerType.COMPRESSION_EXPANSION, triggered_direction="DOWN")
        assert get_direction_from_trigger(t) == DirectionHint.PUT

    def test_compression_none(self):
        t = _trigger(ScannerType.COMPRESSION_EXPANSION)
        assert get_direction_from_trigger(t) == DirectionHint.NONE

    def test_uv_call(self):
        t = _trigger(ScannerType.UNUSUAL_VOLUME, call_put_volume_ratio=2.0)
        assert get_direction_from_trigger(t) == DirectionHint.CALL

    def test_uv_put(self):
        t = _trigger(ScannerType.UNUSUAL_VOLUME, call_put_volume_ratio=0.5)
        assert get_direction_from_trigger(t) == DirectionHint.PUT

    def test_uv_none(self):
        t = _trigger(ScannerType.UNUSUAL_VOLUME, call_put_volume_ratio=1.0)
        assert get_direction_from_trigger(t) == DirectionHint.NONE

    def test_cheap_options(self):
        t = _trigger(ScannerType.CHEAP_OPTIONS)
        assert get_direction_from_trigger(t) == DirectionHint.NONE


# ---------------------------------------------------------------------------
# resolve_direction_hints
# ---------------------------------------------------------------------------


class TestResolveDirectionHints:
    def test_empty(self):
        assert resolve_direction_hints([]) == DirectionHint.NONE

    def test_all_none(self):
        assert resolve_direction_hints([DirectionHint.NONE, DirectionHint.NONE]) == DirectionHint.NONE

    def test_agree_call(self):
        assert resolve_direction_hints([DirectionHint.CALL, DirectionHint.CALL]) == DirectionHint.CALL

    def test_agree_put(self):
        assert resolve_direction_hints([DirectionHint.PUT, DirectionHint.PUT]) == DirectionHint.PUT

    def test_conflict(self):
        assert resolve_direction_hints([DirectionHint.CALL, DirectionHint.PUT]) == DirectionHint.NONE

    def test_directional_with_none(self):
        assert resolve_direction_hints([DirectionHint.CALL, DirectionHint.NONE]) == DirectionHint.CALL


# ---------------------------------------------------------------------------
# calculate_priority_score
# ---------------------------------------------------------------------------


class TestCalculatePriorityScore:
    def test_empty(self):
        assert calculate_priority_score([]) == 0

    def test_single(self):
        t = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        assert calculate_priority_score([t]) == 75

    def test_two_triggers(self):
        t1 = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        t2 = _trigger(ScannerType.COMPRESSION_EXPANSION)
        score = calculate_priority_score([t1, t2])
        # max(75, 70) + 15*1 = 90
        assert score == 90

    def test_three_triggers_capped(self):
        t1 = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        t2 = _trigger(ScannerType.COMPRESSION_EXPANSION)
        t3 = _trigger(ScannerType.UNUSUAL_VOLUME, volume_ratio=4.0)
        score = calculate_priority_score([t1, t2, t3])
        # max(75, 70, 65) + 15*2 = 105 -> capped at 100
        assert score == 100


# ---------------------------------------------------------------------------
# merge_triggers_to_opportunity
# ---------------------------------------------------------------------------


class TestMergeTriggersToOpportunity:
    def test_single(self):
        t = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        opp = merge_triggers_to_opportunity("AAPL", [t], "2026-01-17T10:00:00Z")
        assert opp.underlying_ticker == "AAPL"
        assert opp.priority_score == 75
        assert opp.direction_hint == DirectionHint.CALL

    def test_multi_scanner(self):
        t1 = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        t2 = _trigger(ScannerType.CHEAP_OPTIONS)
        opp = merge_triggers_to_opportunity("TSLA", [t1, t2], "2026-01-17T10:00:00Z")
        assert len(opp.scanner_triggers) == 2
        assert opp.priority_score == 90  # 75 + 15


# ---------------------------------------------------------------------------
# merge_scan_results
# ---------------------------------------------------------------------------


class TestMergeScanResults:
    def test_empty_results(self):
        result = merge_scan_results([], "2026-01-17T10:00:00Z")
        assert result == []

    def test_no_triggered(self):
        results = [_scan_result("AAPL", False)]
        opps = merge_scan_results(results, "2026-01-17T10:00:00Z")
        assert opps == []

    def test_single_ticker(self):
        t = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        results = [_scan_result("AAPL", True, t)]
        opps = merge_scan_results(results, "2026-01-17T10:00:00Z")
        assert len(opps) == 1
        assert opps[0].underlying_ticker == "AAPL"

    def test_multi_ticker(self):
        t1 = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        t2 = _trigger(ScannerType.CHEAP_OPTIONS)
        results = [
            _scan_result("AAPL", True, t1),
            _scan_result("TSLA", True, t2),
        ]
        opps = merge_scan_results(results, "2026-01-17T10:00:00Z")
        assert len(opps) == 2
        # Sorted by priority (AAPL 75 first, TSLA 50)
        assert opps[0].underlying_ticker == "AAPL"

    def test_same_ticker_merged(self):
        t1 = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        t2 = _trigger(ScannerType.COMPRESSION_EXPANSION, triggered_direction="UP")
        results = [
            _scan_result("AAPL", True, t1),
            _scan_result("AAPL", True, t2),
        ]
        opps = merge_scan_results(results, "2026-01-17T10:00:00Z")
        assert len(opps) == 1
        assert len(opps[0].scanner_triggers) == 2


# ---------------------------------------------------------------------------
# OpportunityMerger (class)
# ---------------------------------------------------------------------------


class TestOpportunityMerger:
    def test_merge_and_stats(self):
        merger = OpportunityMerger()
        merger.set_timestamp("2026-01-17T10:00:00Z")

        t1 = _trigger(ScannerType.BREAKOUT, volume_ratio=2.0)
        t2 = _trigger(ScannerType.COMPRESSION_EXPANSION, triggered_direction="UP")
        results = [
            _scan_result("AAPL", True, t1),
            _scan_result("AAPL", True, t2),
            _scan_result("TSLA", False, None),
        ]
        opps = merger.merge(results)
        assert len(opps) == 1

        stats = merger.get_merge_stats(results, opps)
        assert stats["total_scan_results"] == 3
        assert stats["total_triggers"] == 2
        assert stats["opportunities_created"] == 1
        assert stats["multi_scanner_opportunities"] == 1
        assert stats["direction_call"] == 1
