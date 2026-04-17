"""Integration tests for v4 regime dispatch through PillarCalculator and
DecisionCalculator. Exercises the v3→v4 regime switch at the orchestrator
and decision layers.
"""

from __future__ import annotations

from typing import Optional

import pytest

from app.core.schemas import (
    Decision,
    NumericSubscoreConfig,
    PillarConfig,
    PillarConfigV2,
    PillarId,
    PillarWeights,
    QualityTier,
    SubscoreBreakpoint,
    Verdict,
)
from app.decision.calculator import DecisionCalculator, DecisionContext
from app.pillars.calculator import PillarCalculator
from app.pillars.models import PillarResult, ScoringContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _v4_pillar_subconfig(
    pillar_id: PillarId, feature_field: str = "close"
) -> PillarConfigV2:
    """Trivial PillarConfigV2 — one subscore with a neutral breakpoint."""
    return PillarConfigV2(
        pillar_id=pillar_id,
        display_name=pillar_id.value,
        description="test",
        numeric_subscores=[
            NumericSubscoreConfig(
                subscore_id="test_sub",
                display_name="Test",
                feature_field=feature_field,
                weight=1.0,
                source_tier="tier2",
                breakpoints=[
                    SubscoreBreakpoint(value=0.0, score=0),
                    SubscoreBreakpoint(value=100.0, score=100),
                ],
            )
        ],
    )


def _v4_full_config() -> PillarConfig:
    return PillarConfig(
        weights=PillarWeights.v4_default(),
        composite_formula="weighted_geometric_mean",
        directional_conviction=_v4_pillar_subconfig(PillarId.DIRECTIONAL_CONVICTION),
        move_potential=_v4_pillar_subconfig(PillarId.MOVE_POTENTIAL),
        trade_structure=_v4_pillar_subconfig(PillarId.TRADE_STRUCTURE),
    )


def _decision_ctx(
    *,
    p_pl: Optional[float] = None,
    p_ub: Optional[float] = None,
    p_sq: Optional[float] = None,
    p_dc: Optional[float] = None,
    p_mp: Optional[float] = None,
    p_ts: Optional[float] = None,
    spread: float = 3.0,
    all_passed: bool = True,
) -> DecisionContext:
    return DecisionContext(
        evaluation_id="eval-1",
        underlying_ticker="TEST",
        option_type="CALL",
        spread_pct=spread,
        policy_version="v4.0.0" if p_dc is not None else "v3.1.3",
        premium_leverage_score=p_pl,
        underlying_behavior_score=p_ub,
        setup_quality_score=p_sq,
        directional_conviction_score=p_dc,
        move_potential_score=p_mp,
        trade_structure_score=p_ts,
        all_gates_passed=all_passed,
    )


# ---------------------------------------------------------------------------
# PillarCalculator regime dispatch
# ---------------------------------------------------------------------------


class TestPillarCalculatorRegimeDispatch:
    def test_v4_config_produces_v4_pillars(self) -> None:
        config = _v4_full_config()
        calc = PillarCalculator(config)

        ctx = ScoringContext(
            evaluation_id="eval-1",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            close=75.0,  # feature_field="close" → maps to score 75 via breakpoints.
        )

        results = calc.compute_pillars(evaluation=None, feature_set=None, opportunity=None, context=ctx)
        assert len(results) == 3
        pillar_ids = [r.pillar_id for r in results]
        assert PillarId.DIRECTIONAL_CONVICTION in pillar_ids
        assert PillarId.MOVE_POTENTIAL in pillar_ids
        assert PillarId.TRADE_STRUCTURE in pillar_ids
        # One subscore, score=75 on the breakpoint → v4 floor applies but
        # min-subscore rule zeros us (only 1 available < 3 min).
        for r in results:
            assert r.score == 0.0
            assert "INSUFFICIENT_DATA" in r.tags

    def test_v3_config_produces_v3_pillars(self) -> None:
        config = PillarConfig.v3_default()
        calc = PillarCalculator(config)
        ctx = ScoringContext(
            evaluation_id="eval-1",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            close=180.0,
            iv=0.30,
            iv_percentile=20.0,
            iv_rv_ratio=0.9,
            rv20=0.30,
            adx_14=20.0,
            feasibility_ratio=0.3,
            time_adjusted_feasibility=0.2,
            atr14_pct=3.0,
            open_interest=500,
            volume=200,
            convergence_count=2,
            delta=0.35,
            dte=30,
        )
        results = calc.compute_pillars(evaluation=None, feature_set=None, opportunity=None, context=ctx)
        pillar_ids = [r.pillar_id for r in results]
        assert PillarId.PREMIUM_LEVERAGE in pillar_ids
        assert PillarId.UNDERLYING_BEHAVIOR in pillar_ids
        assert PillarId.SETUP_QUALITY in pillar_ids


# ---------------------------------------------------------------------------
# DecisionCalculator regime-aware composite
# ---------------------------------------------------------------------------


class TestDecisionCalculatorV4Composite:
    def test_v4_uses_geometric_mean(self) -> None:
        config = _v4_full_config()
        calc = DecisionCalculator(pillar_config=config)
        ctx = _decision_ctx(p_dc=100.0, p_mp=100.0, p_ts=100.0)
        decision = calc.compute_decision(ctx)
        assert decision.verdict == Verdict.APPROVE
        assert decision.final_score == pytest.approx(100.0, rel=1e-2)

    def test_v4_zero_pillar_collapses_composite(self) -> None:
        """A zero pillar score (insufficient-data) → composite 0 → REJECT."""
        config = _v4_full_config()
        calc = DecisionCalculator(pillar_config=config)
        ctx = _decision_ctx(p_dc=100.0, p_mp=100.0, p_ts=0.0)
        decision = calc.compute_decision(ctx)
        assert decision.final_score == 0.0
        assert decision.verdict == Verdict.REJECT
        # SHARPSHOOTER_SETUP should not be emitted on REJECT.
        assert "SHARPSHOOTER_SETUP" not in decision.supporting_reason_codes

    def test_v4_sharpshooter_setup_reason_on_tier_1(self) -> None:
        config = _v4_full_config()
        calc = DecisionCalculator(pillar_config=config)
        # Extremely strong v4 setup → TIER_1.
        ctx = _decision_ctx(p_dc=95.0, p_mp=92.0, p_ts=90.0, spread=2.0)
        decision = calc.compute_decision(ctx)
        assert decision.verdict == Verdict.APPROVE
        assert decision.quality_tier == QualityTier.TIER_1
        assert "SHARPSHOOTER_SETUP" in decision.supporting_reason_codes
        # v3 reason codes must NOT appear.
        assert "EXCEPTIONAL_SETUP" not in decision.supporting_reason_codes
        assert "STRONG_PREMIUM_LEVERAGE" not in decision.supporting_reason_codes
        assert "STRONG_DIRECTIONAL_CONVICTION" in decision.supporting_reason_codes

    def test_v3_regime_still_uses_exceptional_setup_reason(self) -> None:
        calc = DecisionCalculator(pillar_config=PillarConfig.v3_default())
        ctx = _decision_ctx(p_pl=95.0, p_ub=92.0, p_sq=90.0, spread=2.0)
        decision = calc.compute_decision(ctx)
        assert decision.quality_tier == QualityTier.TIER_1
        assert "EXCEPTIONAL_SETUP" in decision.supporting_reason_codes
        assert "SHARPSHOOTER_SETUP" not in decision.supporting_reason_codes

    def test_insufficient_data_reason_on_v4_zero(self) -> None:
        config = _v4_full_config()
        calc = DecisionCalculator(pillar_config=config)
        # Gates passing; a pillar at 0 → REJECT by score (composite=0).
        ctx = _decision_ctx(p_dc=80.0, p_mp=80.0, p_ts=0.0)
        decision = calc.compute_decision(ctx)
        assert decision.verdict == Verdict.REJECT
        assert "INSUFFICIENT_DATA_TRADE_STRUCTURE" in decision.supporting_reason_codes

    def test_v4_decision_populates_both_regime_fields(self) -> None:
        """Decision schema keeps v3 non-Optional; v4 decisions must still validate."""
        config = _v4_full_config()
        calc = DecisionCalculator(pillar_config=config)
        ctx = _decision_ctx(p_dc=80.0, p_mp=75.0, p_ts=85.0)
        decision = calc.compute_decision(ctx)
        assert isinstance(decision, Decision)
        # v4 fields populated
        assert decision.directional_conviction_score == 80.0
        assert decision.move_potential_score == 75.0
        assert decision.trade_structure_score == 85.0
        # v3 fields sentinelled to 0.0 (non-Optional through Phase 5)
        assert decision.premium_leverage_score == 0.0
        assert decision.underlying_behavior_score == 0.0
        assert decision.setup_quality_score == 0.0

    def test_v3_decision_leaves_v4_fields_none(self) -> None:
        calc = DecisionCalculator()  # defaults to v3 weights, no pillar_config
        ctx = _decision_ctx(p_pl=80.0, p_ub=75.0, p_sq=85.0)
        decision = calc.compute_decision(ctx)
        assert decision.directional_conviction_score is None
        assert decision.move_potential_score is None
        assert decision.trade_structure_score is None
        assert decision.premium_leverage_score == 80.0


# ---------------------------------------------------------------------------
# DecisionContext regime detection
# ---------------------------------------------------------------------------


class TestDecisionContextRegime:
    def test_v4_populated_context_is_v4(self) -> None:
        ctx = _decision_ctx(p_dc=80.0, p_mp=70.0, p_ts=90.0)
        assert ctx.is_v4() is True
        trip = ctx.pillar_triplet()
        assert trip == (80.0, 70.0, 90.0)

    def test_v3_populated_context_is_not_v4(self) -> None:
        ctx = _decision_ctx(p_pl=80.0, p_ub=70.0, p_sq=90.0)
        assert ctx.is_v4() is False
        trip = ctx.pillar_triplet()
        assert trip == (80.0, 70.0, 90.0)

    def test_from_evaluation_and_results_dispatches_by_pillar_id(self) -> None:
        """PillarResults with v4 ids should populate v4 fields on the context."""
        from app.core.schemas import Evaluation, OptionType, DTEBucket

        eval_ = Evaluation(
            opportunity_id="opp-1",
            underlying_ticker="TEST",
            option_ticker="TEST230101C00180000",
            option_type=OptionType.CALL,
            expiration_date="2026-05-15",
            dte=30,
            strike=180.0,
            underlying_price=180.0,
            moneyness_pct=0.0,
            bid=1.0,
            ask=1.1,
            mid=1.05,
            spread_abs=0.1,
            spread_pct=3.0,
            iv=0.30,
            delta=0.35,
            gamma=0.05,
            theta=-0.08,
            vega=0.10,
            open_interest=500,
            volume=200,
            breakeven_price=181.05,
            required_move_pct=2.0,
            expected_move_pct=4.0,
            feasibility_ratio=0.5,
            time_adjusted_feasibility=0.4,
            dte_bucket=DTEBucket.B,
            rank_score=75.0,
            policy_version="v4.0.0",
            policy_hash="abc",
        )
        results = [
            PillarResult(
                pillar_id=PillarId.DIRECTIONAL_CONVICTION, evaluation_id=eval_.evaluation_id, score=85
            ),
            PillarResult(
                pillar_id=PillarId.MOVE_POTENTIAL, evaluation_id=eval_.evaluation_id, score=80
            ),
            PillarResult(
                pillar_id=PillarId.TRADE_STRUCTURE, evaluation_id=eval_.evaluation_id, score=75
            ),
        ]
        ctx = DecisionContext.from_evaluation_and_results(eval_, results, None)
        assert ctx.is_v4() is True
        assert ctx.directional_conviction_score == 85
        assert ctx.trade_structure_score == 75
        # v3 fields untouched
        assert ctx.premium_leverage_score is None
