"""Tests for REVALIDATION provenance fields (Phase 4 / audit C5).

Verifies that a REVALIDATION ScannerTrigger carries both:
- ``original_scanners`` (list) — legacy field already in use
- ``originating_scanner`` (str) — canonical singular provenance pointer
  added in Phase 4 so downstream consumers don't have to guess the
  upstream source
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.schemas import (
    DirectionHint,
    Opportunity,
    ScannerTrigger,
    ScannerType,
)


def test_revalidation_trigger_carries_originating_scanner() -> None:
    now = datetime.now(timezone.utc).isoformat()
    trig = ScannerTrigger(
        scanner_type=ScannerType.REVALIDATION,
        reason_codes=["APPROVE_REVALIDATION"],
        metrics={
            "lookback_hours": 8,
            "original_scanners": ["UNUSUAL_VOLUME", "CHEAP_OPTIONS"],
            "originating_scanner": "UNUSUAL_VOLUME",
        },
        triggered_at=now,
    )

    assert trig.metrics["originating_scanner"] == "UNUSUAL_VOLUME"
    assert trig.metrics["original_scanners"] == ["UNUSUAL_VOLUME", "CHEAP_OPTIONS"]
    assert trig.metrics["lookback_hours"] == 8


def test_revalidation_trigger_without_originating_scanner_still_valid() -> None:
    """The field is optional — first-ever revalidation for a ticker that
    can't look up its original scanner should still produce a valid trigger.
    """
    now = datetime.now(timezone.utc).isoformat()
    trig = ScannerTrigger(
        scanner_type=ScannerType.REVALIDATION,
        reason_codes=["APPROVE_REVALIDATION"],
        metrics={"lookback_hours": 8},
        triggered_at=now,
    )

    assert "originating_scanner" not in trig.metrics
    assert trig.metrics["lookback_hours"] == 8


def test_revalidation_opportunity_preserves_provenance_on_model_roundtrip() -> None:
    now = datetime.now(timezone.utc).isoformat()
    opp = Opportunity(
        underlying_ticker="NVDA",
        timestamp_utc=now,
        scanner_triggers=[
            ScannerTrigger(
                scanner_type=ScannerType.REVALIDATION,
                reason_codes=["APPROVE_REVALIDATION"],
                metrics={
                    "lookback_hours": 8,
                    "original_scanners": ["CHEAP_OPTIONS"],
                    "originating_scanner": "CHEAP_OPTIONS",
                },
                triggered_at=now,
            ),
        ],
        direction_hint=DirectionHint.CALL,
        priority_score=50,
    )

    # Pydantic v2 roundtrip through dict — the shape DynamoDB persists.
    dumped = opp.model_dump()
    trig = dumped["scanner_triggers"][0]
    assert trig["metrics"]["originating_scanner"] == "CHEAP_OPTIONS"

    restored = Opportunity(**dumped)
    assert (
        restored.scanner_triggers[0].metrics["originating_scanner"]
        == "CHEAP_OPTIONS"
    )
