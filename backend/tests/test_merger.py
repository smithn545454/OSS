"""Tests for opportunity merger logic."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.schemas import (
    DirectionHint,
    ScannerTrigger,
    ScannerType,
)
from app.scanners.base import ScanResult
from app.scanners.merger import (
    calculate_priority_score,
    get_direction_from_trigger,
    get_scanner_base_priority,
    merge_scan_results,
    merge_triggers_to_opportunity,
    resolve_direction_hints,
)


def _create_trigger(
    scanner_type: ScannerType,
    metrics: dict = None,
) -> ScannerTrigger:
    """Helper to create a ScannerTrigger."""
    return ScannerTrigger(
        scanner_type=scanner_type,
        reason_codes=["TEST_REASON"],
        metrics=metrics or {},
        triggered_at=datetime.now(timezone.utc).isoformat(),
    )


class TestGetScannerBasePriority:
    """Tests for scanner base priority calculation."""

    def test_breakout_with_volume_confirmation(self):
        """Test breakout with volume confirmation gets 75."""
        trigger = _create_trigger(
            ScannerType.BREAKOUT,
            metrics={"volume_ratio": 2.0},
        )
        assert get_scanner_base_priority(trigger) == 75

    def test_breakout_without_volume_confirmation(self):
        """Test breakout without volume confirmation gets 65."""
        trigger = _create_trigger(
            ScannerType.BREAKOUT,
            metrics={"volume_ratio": 1.2},
        )
        assert get_scanner_base_priority(trigger) == 65

    def test_breakdown_with_volume_confirmation(self):
        """Test breakdown with volume confirmation gets 75."""
        trigger = _create_trigger(
            ScannerType.BREAKDOWN,
            metrics={"volume_ratio": 1.8},
        )
        assert get_scanner_base_priority(trigger) == 75

    def test_compression_expansion(self):
        """Test compression expansion gets 70."""
        trigger = _create_trigger(ScannerType.COMPRESSION_EXPANSION)
        assert get_scanner_base_priority(trigger) == 70

    def test_unusual_volume_high(self):
        """Test unusual volume > 3.0 gets 65."""
        trigger = _create_trigger(
            ScannerType.UNUSUAL_VOLUME,
            metrics={"volume_ratio": 3.5},
        )
        assert get_scanner_base_priority(trigger) == 65

    def test_unusual_volume_normal(self):
        """Test unusual volume 2.0-3.0 gets 55."""
        trigger = _create_trigger(
            ScannerType.UNUSUAL_VOLUME,
            metrics={"volume_ratio": 2.5},
        )
        assert get_scanner_base_priority(trigger) == 55

    def test_cheap_options(self):
        """Test cheap options gets 50."""
        trigger = _create_trigger(ScannerType.CHEAP_OPTIONS)
        assert get_scanner_base_priority(trigger) == 50


class TestGetDirectionFromTrigger:
    """Tests for direction extraction from triggers."""

    def test_breakout_gives_call(self):
        """Test breakout trigger gives CALL direction."""
        trigger = _create_trigger(ScannerType.BREAKOUT)
        assert get_direction_from_trigger(trigger) == DirectionHint.CALL

    def test_breakdown_gives_put(self):
        """Test breakdown trigger gives PUT direction."""
        trigger = _create_trigger(ScannerType.BREAKDOWN)
        assert get_direction_from_trigger(trigger) == DirectionHint.PUT

    def test_compression_up_gives_call(self):
        """Test compression up gives CALL."""
        trigger = _create_trigger(
            ScannerType.COMPRESSION_EXPANSION,
            metrics={"triggered_direction": "UP"},
        )
        assert get_direction_from_trigger(trigger) == DirectionHint.CALL

    def test_compression_down_gives_put(self):
        """Test compression down gives PUT."""
        trigger = _create_trigger(
            ScannerType.COMPRESSION_EXPANSION,
            metrics={"triggered_direction": "DOWN"},
        )
        assert get_direction_from_trigger(trigger) == DirectionHint.PUT

    def test_unusual_volume_bullish(self):
        """Test unusual volume with high call/put ratio gives CALL."""
        trigger = _create_trigger(
            ScannerType.UNUSUAL_VOLUME,
            metrics={"call_put_volume_ratio": 1.5},
        )
        assert get_direction_from_trigger(trigger) == DirectionHint.CALL

    def test_unusual_volume_bearish(self):
        """Test unusual volume with low call/put ratio gives PUT."""
        trigger = _create_trigger(
            ScannerType.UNUSUAL_VOLUME,
            metrics={"call_put_volume_ratio": 0.5},
        )
        assert get_direction_from_trigger(trigger) == DirectionHint.PUT

    def test_unusual_volume_neutral(self):
        """Test unusual volume with neutral ratio gives NONE."""
        trigger = _create_trigger(
            ScannerType.UNUSUAL_VOLUME,
            metrics={"call_put_volume_ratio": 1.0},
        )
        assert get_direction_from_trigger(trigger) == DirectionHint.NONE

    def test_cheap_options_always_none(self):
        """Test cheap options always gives NONE."""
        trigger = _create_trigger(ScannerType.CHEAP_OPTIONS)
        assert get_direction_from_trigger(trigger) == DirectionHint.NONE


class TestResolveDirectionHints:
    """Tests for direction hint resolution."""

    def test_all_agree_call(self):
        """Test all CALL hints resolve to CALL."""
        hints = [DirectionHint.CALL, DirectionHint.CALL, DirectionHint.CALL]
        assert resolve_direction_hints(hints) == DirectionHint.CALL

    def test_all_agree_put(self):
        """Test all PUT hints resolve to PUT."""
        hints = [DirectionHint.PUT, DirectionHint.PUT]
        assert resolve_direction_hints(hints) == DirectionHint.PUT

    def test_conflicting_hints_resolve_to_none(self):
        """Test conflicting hints resolve to NONE."""
        hints = [DirectionHint.CALL, DirectionHint.PUT]
        assert resolve_direction_hints(hints) == DirectionHint.NONE

    def test_none_plus_directional_uses_directional(self):
        """Test NONE + directional uses the directional."""
        hints = [DirectionHint.NONE, DirectionHint.CALL, DirectionHint.NONE]
        assert resolve_direction_hints(hints) == DirectionHint.CALL

    def test_all_none(self):
        """Test all NONE hints resolve to NONE."""
        hints = [DirectionHint.NONE, DirectionHint.NONE]
        assert resolve_direction_hints(hints) == DirectionHint.NONE

    def test_empty_list(self):
        """Test empty list resolves to NONE."""
        assert resolve_direction_hints([]) == DirectionHint.NONE


class TestCalculatePriorityScore:
    """Tests for priority score calculation."""

    def test_single_trigger(self):
        """Test single trigger gets base priority."""
        triggers = [
            _create_trigger(ScannerType.COMPRESSION_EXPANSION),
        ]
        # Compression is base 70
        assert calculate_priority_score(triggers) == 70

    def test_two_triggers(self):
        """Test two triggers get base + 15 bonus."""
        triggers = [
            _create_trigger(ScannerType.COMPRESSION_EXPANSION),  # 70
            _create_trigger(ScannerType.CHEAP_OPTIONS),  # 50
        ]
        # Max base (70) + bonus (15 * 1) = 85
        assert calculate_priority_score(triggers) == 85

    def test_three_triggers(self):
        """Test three triggers get base + 30 bonus."""
        triggers = [
            _create_trigger(ScannerType.CHEAP_OPTIONS),  # 50
            _create_trigger(ScannerType.CHEAP_OPTIONS),  # 50
            _create_trigger(ScannerType.CHEAP_OPTIONS),  # 50
        ]
        # Max base (50) + bonus (15 * 2) = 80
        assert calculate_priority_score(triggers) == 80

    def test_priority_capped_at_100(self):
        """Test priority score is capped at 100."""
        triggers = [
            _create_trigger(
                ScannerType.BREAKOUT,
                metrics={"volume_ratio": 2.0},
            ),  # 75
        ] * 4  # 4 triggers = 75 + (15 * 3) = 120 -> capped at 100

        assert calculate_priority_score(triggers) == 100

    def test_empty_triggers(self):
        """Test empty triggers returns 0."""
        assert calculate_priority_score([]) == 0


class TestMergeTriggersToOpportunity:
    """Tests for merging triggers into an opportunity."""

    def test_single_trigger_opportunity(self):
        """Test opportunity from single trigger."""
        triggers = [
            _create_trigger(ScannerType.BREAKOUT, metrics={"volume_ratio": 1.8}),
        ]

        opp = merge_triggers_to_opportunity("AAPL", triggers)

        assert opp.underlying_ticker == "AAPL"
        assert len(opp.scanner_triggers) == 1
        assert opp.direction_hint == DirectionHint.CALL
        assert opp.priority_score == 75

    def test_multi_trigger_opportunity(self):
        """Test opportunity from multiple triggers."""
        triggers = [
            _create_trigger(ScannerType.BREAKOUT, metrics={"volume_ratio": 1.8}),
            _create_trigger(
                ScannerType.COMPRESSION_EXPANSION,
                metrics={"triggered_direction": "UP"},
            ),
        ]

        opp = merge_triggers_to_opportunity("NVDA", triggers)

        assert opp.underlying_ticker == "NVDA"
        assert len(opp.scanner_triggers) == 2
        assert opp.direction_hint == DirectionHint.CALL  # Both agree
        assert opp.priority_score == 90  # 75 + 15


class TestMergeScanResults:
    """Tests for merging scan results into opportunities."""

    def test_merge_groups_by_ticker(self):
        """Test merge groups results by ticker."""
        results = [
            ScanResult(
                ticker="AAPL",
                triggered=True,
                trigger=_create_trigger(ScannerType.BREAKOUT),
            ),
            ScanResult(
                ticker="AAPL",
                triggered=True,
                trigger=_create_trigger(ScannerType.UNUSUAL_VOLUME),
            ),
            ScanResult(
                ticker="MSFT",
                triggered=True,
                trigger=_create_trigger(ScannerType.BREAKOUT),
            ),
        ]

        opportunities = merge_scan_results(results)

        assert len(opportunities) == 2  # AAPL and MSFT

        # Find AAPL opportunity
        aapl_opp = next(o for o in opportunities if o.underlying_ticker == "AAPL")
        assert len(aapl_opp.scanner_triggers) == 2

    def test_merge_excludes_non_triggered(self):
        """Test merge excludes non-triggered results."""
        results = [
            ScanResult(ticker="AAPL", triggered=False),
            ScanResult(
                ticker="MSFT",
                triggered=True,
                trigger=_create_trigger(ScannerType.BREAKOUT),
            ),
        ]

        opportunities = merge_scan_results(results)

        assert len(opportunities) == 1
        assert opportunities[0].underlying_ticker == "MSFT"

    def test_merge_sorts_by_priority(self):
        """Test merge sorts opportunities by priority (highest first)."""
        results = [
            ScanResult(
                ticker="LOW",
                triggered=True,
                trigger=_create_trigger(ScannerType.CHEAP_OPTIONS),  # 50
            ),
            ScanResult(
                ticker="HIGH",
                triggered=True,
                trigger=_create_trigger(
                    ScannerType.BREAKOUT,
                    metrics={"volume_ratio": 2.0},
                ),  # 75
            ),
        ]

        opportunities = merge_scan_results(results)

        assert opportunities[0].underlying_ticker == "HIGH"
        assert opportunities[1].underlying_ticker == "LOW"
