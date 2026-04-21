"""Regression test for audit C1: concentration warning reconstruction
must preserve v5 and archetype fields on Decision.

Before Phase 5b, ``update_decisions_with_warnings`` hand-rolled a new
Decision with only 14 fields, silently dropping every v5 dual-conviction
field and every v4.1.0 archetype field. That's why CHEAP_OPTIONS
APPROVEs persisted with ``v5_scoring_version=NULL`` even though the
decision calculator had correctly populated the field.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.schemas import Decision, QualityTier, Verdict
from app.decision.concentration import update_decisions_with_warnings


def _make_v5_decision(eval_id: str, *, with_warnings: list[str] | None = None) -> Decision:
    return Decision(
        evaluation_id=eval_id,
        verdict=Verdict.APPROVE,
        quality_tier=QualityTier.TIER_2,
        final_score=72.5,
        primary_reason_code="V5_QUALITY",
        supporting_reason_codes=["ARCHETYPE_CHEAP_CONTRARIAN"],
        failed_gates=[],
        concentration_warnings=list(with_warnings or []),
        policy_version="v4.1.1",
        decided_at=datetime.now(timezone.utc).isoformat(),
        # v4.1.0 archetype fields
        archetype_matched="CHEAP_CONTRARIAN_CHEAP_VOL",
        archetype_match_score=88.0,
        archetype_all_fits={"CHEAP_CONTRARIAN_CHEAP_VOL": 88.0},
        anti_archetype_triggered=None,
        # v5 dual-conviction fields
        hr_conviction=3.4,
        hr_archetype_matched="CHEAP_LOTTERY_CALL",
        hr_archetype_fit=65.0,
        hr_p_point=0.19,
        hr_p_lower=0.12,
        hr_p_upper=0.27,
        hr_n_trades=42,
        p_conviction=58.2,
        p_archetype_matched="CHEAP_CONTRARIAN_CHEAP_VOL",
        p_archetype_fit=88.0,
        p_win_point=0.72,
        p_win_lower=0.61,
        p_mean_pnl_estimate=50.2,
        regime_alignment=1.0,
        gbm_hr_score=2.1,
        gbm_p_score=0.0,
        v5_scoring_version="v5.0.0",
    )


def test_concentration_warning_preserves_v5_and_archetype_fields() -> None:
    """The canonical audit C1 regression test — every v5/archetype field
    must survive a concentration-warning update."""
    original = _make_v5_decision("eval-1")
    warnings = {"eval-1": ["CONC_DIRECTIONAL_CALL_HEAVY"]}

    updated = update_decisions_with_warnings({"eval-1": original}, warnings)
    result = updated["eval-1"]

    # The warning was appended
    assert result.concentration_warnings == ["CONC_DIRECTIONAL_CALL_HEAVY"]

    # Every v5 field survived the copy
    assert result.v5_scoring_version == "v5.0.0"
    assert result.hr_conviction == 3.4
    assert result.hr_archetype_matched == "CHEAP_LOTTERY_CALL"
    assert result.hr_archetype_fit == 65.0
    assert result.hr_p_point == 0.19
    assert result.hr_p_lower == 0.12
    assert result.hr_p_upper == 0.27
    assert result.hr_n_trades == 42
    assert result.p_conviction == 58.2
    assert result.p_archetype_matched == "CHEAP_CONTRARIAN_CHEAP_VOL"
    assert result.p_archetype_fit == 88.0
    assert result.p_win_point == 0.72
    assert result.p_win_lower == 0.61
    assert result.p_mean_pnl_estimate == 50.2
    assert result.regime_alignment == 1.0
    assert result.gbm_hr_score == 2.1
    assert result.gbm_p_score == 0.0

    # v4.1.0 archetype fields also preserved
    assert result.archetype_matched == "CHEAP_CONTRARIAN_CHEAP_VOL"
    assert result.archetype_match_score == 88.0
    assert result.archetype_all_fits == {"CHEAP_CONTRARIAN_CHEAP_VOL": 88.0}


def test_concentration_warning_appends_without_clobbering_existing() -> None:
    original = _make_v5_decision(
        "eval-2", with_warnings=["CONC_TICKER_REPEAT_NVDA"],
    )
    warnings = {"eval-2": ["CONC_DIRECTIONAL_CALL_HEAVY"]}

    updated = update_decisions_with_warnings({"eval-2": original}, warnings)

    assert updated["eval-2"].concentration_warnings == [
        "CONC_TICKER_REPEAT_NVDA",
        "CONC_DIRECTIONAL_CALL_HEAVY",
    ]
    assert updated["eval-2"].v5_scoring_version == "v5.0.0"


def test_no_warnings_returns_decision_unchanged() -> None:
    original = _make_v5_decision("eval-3")
    updated = update_decisions_with_warnings({"eval-3": original}, warnings={})
    # Same object reference when no warnings to apply
    assert updated["eval-3"] is original


def test_decisions_without_warnings_entry_pass_through() -> None:
    original = _make_v5_decision("eval-4")
    updated = update_decisions_with_warnings(
        {"eval-4": original},
        warnings={"other-eval": ["IGNORED"]},
    )
    assert updated["eval-4"] is original
    assert updated["eval-4"].v5_scoring_version == "v5.0.0"
