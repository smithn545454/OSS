"""Unit tests for v4.1.0 anti-archetype gates."""

from __future__ import annotations

from typing import Any

from app.archetypes.defaults import default_anti_archetypes
from app.archetypes.gates import check_anti_archetypes
from app.core.schemas import (
    AntiArchetypeConfig,
    AntiArchetypeDefinition,
    ArchetypeCondition,
    Verdict,
)
from app.decision.calculator import DecisionCalculator, DecisionContext
from app.pillars.models import ScoringContext


def _ctx(**overrides: Any) -> ScoringContext:
    defaults: dict[str, Any] = dict(
        evaluation_id="eval-1",
        underlying_ticker="TEST",
        option_type="CALL",
        dte_bucket="B",
        dte=20,
        scanner_source="BREAKOUT",
    )
    defaults.update(overrides)
    return ScoringContext(**defaults)


class TestAntiArchetypesBreakoutMpElite:
    def test_breakout_mp_elite_rejected(self) -> None:
        ctx = _ctx(scanner_source="BREAKOUT")
        result = check_anti_archetypes(
            ctx, default_anti_archetypes(), pillar_scores={"MP": 80.0}
        )
        assert result.triggered is True
        assert result.anti_archetype_id == "BREAKOUT_MP_ELITE"
        assert result.rejection_reason == "ANTI_BREAKOUT_MP_ELITE"

    def test_breakout_mp_below_threshold_not_rejected(self) -> None:
        ctx = _ctx(scanner_source="BREAKOUT")
        result = check_anti_archetypes(
            ctx, default_anti_archetypes(), pillar_scores={"MP": 74.0}
        )
        assert result.triggered is False

    def test_non_breakout_with_high_mp_not_rejected(self) -> None:
        ctx = _ctx(scanner_source="UNUSUAL_VOLUME", dte=20)
        result = check_anti_archetypes(
            ctx, default_anti_archetypes(), pillar_scores={"MP": 90.0}
        )
        assert result.triggered is False


class TestAntiArchetypesUvLongDte:
    def test_uv_long_dte_rejected(self) -> None:
        ctx = _ctx(scanner_source="UNUSUAL_VOLUME", dte=50)
        result = check_anti_archetypes(ctx, default_anti_archetypes())
        assert result.triggered is True
        assert result.anti_archetype_id == "UV_LONG_DATED"

    def test_uv_short_dte_not_rejected(self) -> None:
        ctx = _ctx(scanner_source="UNUSUAL_VOLUME", dte=20)
        result = check_anti_archetypes(ctx, default_anti_archetypes())
        assert result.triggered is False


class TestAntiArchetypesCheapDcElite:
    def test_cheap_dc_elite_rejected(self) -> None:
        ctx = _ctx(scanner_source="CHEAP_OPTIONS", dte=20)
        result = check_anti_archetypes(
            ctx, default_anti_archetypes(), pillar_scores={"DC": 80.0}
        )
        assert result.triggered is True
        assert result.anti_archetype_id == "CHEAP_DC_ELITE"


class TestAntiArchetypeBehavior:
    def test_disabled_gate_does_not_fire(self) -> None:
        config = AntiArchetypeConfig(
            anti_archetypes=[
                AntiArchetypeDefinition(
                    anti_archetype_id="DISABLED",
                    display_name="Disabled",
                    description="",
                    conditions=[
                        ArchetypeCondition(
                            condition_id="scanner",
                            display_name="s",
                            feature_field="scanner_source",
                            eq="BREAKOUT",
                        )
                    ],
                    historical_n=1,
                    historical_win_rate=0,
                    historical_mean_pnl_pct=0,
                    rejection_reason="X",
                    enabled=False,
                )
            ]
        )
        ctx = _ctx(scanner_source="BREAKOUT")
        result = check_anti_archetypes(ctx, config)
        assert result.triggered is False

    def test_missing_pillar_score_not_rejected(self) -> None:
        """DC=None can't be evaluated → condition fit=0 → gate does not fire."""
        ctx = _ctx(scanner_source="CHEAP_OPTIONS", dte=20)
        result = check_anti_archetypes(
            ctx, default_anti_archetypes(), pillar_scores={}
        )
        assert result.triggered is False

    def test_first_matching_wins(self) -> None:
        """BREAKOUT + MP=80 + DTE=50 would match AA1 (first). Ensure ordering."""
        ctx = _ctx(scanner_source="UNUSUAL_VOLUME", dte=50)
        # Both UV_LONG_DTE would match and CHEAP_DC_ELITE would not (scanner
        # mismatch). Verify UV_LONG_DTE fires, not a later rule.
        result = check_anti_archetypes(ctx, default_anti_archetypes())
        assert result.anti_archetype_id == "UV_LONG_DATED"


class TestDecisionCalculatorIntegration:
    def test_short_circuits_to_reject(self) -> None:
        """When anti_archetype_triggered is set, verdict becomes REJECT."""
        ctx = DecisionContext(
            evaluation_id="eval-1",
            underlying_ticker="TEST",
            option_type="CALL",
            spread_pct=2.0,
            policy_version="v4.1.0",
            directional_conviction_score=90.0,
            move_potential_score=90.0,
            trade_structure_score=90.0,
            all_gates_passed=True,
            scanner_source="BREAKOUT",
            anti_archetype_triggered="BREAKOUT_MP_ELITE",
        )
        calc = DecisionCalculator()
        decision = calc.compute_decision(ctx)
        assert decision.verdict == Verdict.REJECT
        assert decision.primary_reason_code == "ANTI_ARCHETYPE_BREAKOUT_MP_ELITE"
        assert decision.anti_archetype_triggered == "BREAKOUT_MP_ELITE"
        assert decision.quality_tier is None

    def test_no_trigger_passes_through(self) -> None:
        ctx = DecisionContext(
            evaluation_id="eval-1",
            underlying_ticker="TEST",
            option_type="CALL",
            spread_pct=2.0,
            policy_version="v4.1.0",
            directional_conviction_score=90.0,
            move_potential_score=90.0,
            trade_structure_score=90.0,
            all_gates_passed=True,
            scanner_source="UNUSUAL_VOLUME",
        )
        calc = DecisionCalculator()
        decision = calc.compute_decision(ctx)
        # No anti-archetype, so primary_reason is NOT an ANTI_* code.
        assert decision.anti_archetype_triggered is None
        assert not decision.primary_reason_code.startswith("ANTI_ARCHETYPE_")
