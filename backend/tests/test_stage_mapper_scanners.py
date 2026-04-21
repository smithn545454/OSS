"""Tests for Stage 1 scanner labeling in StageMapper (Phase 3 / audit C2).

Ensures BREAKDOWN and REVALIDATION scanner trigger rows surface with
friendly labels in Pipeline Monitor instead of falling back to the raw
enum value.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.schemas import PipelineStage, StageEvent
from app.observability.stage_mapper import StageMapper


def _make_stage1_event(scanner_stats: dict, trigger_counts: dict | None = None) -> StageEvent:
    now = datetime.now(timezone.utc).isoformat()
    md: dict = {"scanner_stats": scanner_stats}
    if trigger_counts is not None:
        md["trigger_counts"] = trigger_counts
    return StageEvent(
        run_id="run-1",
        stage=PipelineStage.OPPORTUNITY_DISCOVERY,
        started_at=now,
        completed_at=now,
        items_in=1000,
        items_out=300,
        items_dropped=700,
        metadata=md,
    )


def test_stage1_labels_breakdown_and_revalidation() -> None:
    mapper = StageMapper()
    event = _make_stage1_event({
        "BREAKOUT": {"total_scanned": 1000, "triggered": 90},
        "BREAKDOWN": {"total_scanned": 1000, "triggered": 14},
        "CHEAP_OPTIONS": {"total_scanned": 1000, "triggered": 280},
        "COMPRESSION_EXPANSION": {"total_scanned": 1000, "triggered": 8},
        "UNUSUAL_VOLUME": {"total_scanned": 1000, "triggered": 60},
        "REVALIDATION": {"total_scanned": 60, "triggered": 40},
    })
    gates = mapper._build_stage1_gates([event])
    assert len(gates) == 1
    rule_names = {rule.name for rule in gates[0].rules}

    assert "Breakdown Scanner" in rule_names
    assert "Breakout Scanner" in rule_names
    assert "Cheap Options Scanner" in rule_names
    assert "Compression Scanner" in rule_names
    assert "Unusual Volume Scanner" in rule_names
    assert "Re-evaluation" in rule_names
    # Raw scanner enum values must not leak through when we have a label.
    assert "BREAKDOWN" not in rule_names
    assert "REVALIDATION" not in rule_names


def test_stage1_unknown_scanner_falls_back_to_raw_name() -> None:
    mapper = StageMapper()
    event = _make_stage1_event({
        "BREAKOUT": {"total_scanned": 500, "triggered": 20},
        "NEW_EXPERIMENTAL_SCANNER": {"total_scanned": 500, "triggered": 3},
    })
    gates = mapper._build_stage1_gates([event])
    rule_names = {rule.name for rule in gates[0].rules}
    # Unknown scanner names pass through unchanged rather than disappearing.
    assert "NEW_EXPERIMENTAL_SCANNER" in rule_names


def test_stage1_breakdown_counts_preserve_triggered_and_failed() -> None:
    mapper = StageMapper()
    event = _make_stage1_event({
        "BREAKDOWN": {"total_scanned": 1000, "triggered": 14},
    })
    gates = mapper._build_stage1_gates([event])
    breakdown_rule = next(
        r for r in gates[0].rules if r.name == "Breakdown Scanner"
    )
    assert breakdown_rule.passed == 14
    assert breakdown_rule.failed == 986


def test_stage1_trigger_counts_surfaces_breakdown_when_absent_from_scanner_stats() -> None:
    """BREAKDOWN ships as a *trigger variant* inside the Breakout scanner,
    so it never appears in scanner_stats. The new trigger_counts metadata
    field surfaces it anyway using the largest scanned universe as a
    proportional baseline for the 'failed' display.
    """
    mapper = StageMapper()
    event = _make_stage1_event(
        scanner_stats={
            "BREAKOUT": {"total_scanned": 1000, "triggered": 90},
            "CHEAP_OPTIONS": {"total_scanned": 1000, "triggered": 280},
        },
        trigger_counts={
            "BREAKOUT": 80,      # Already in scanner_stats — should not duplicate.
            "BREAKDOWN": 14,     # Not in scanner_stats — should surface.
            "REVALIDATION": 22,  # Not in scanner_stats — should surface.
        },
    )
    gates = mapper._build_stage1_gates([event])
    rules_by_name = {r.name: r for r in gates[0].rules}

    assert "Breakdown Scanner" in rules_by_name
    breakdown = rules_by_name["Breakdown Scanner"]
    assert breakdown.passed == 14
    assert breakdown.failed == 986  # total_scanned - triggered

    assert "Re-evaluation" in rules_by_name
    reval = rules_by_name["Re-evaluation"]
    assert reval.passed == 22

    # Breakout row must not double-count — scanner_stats wins.
    assert rules_by_name["Breakout Scanner"].passed == 90
