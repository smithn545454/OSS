"""Unit tests for Stage 7: Decision Logic.

Tests verdict determination, quality tier assignment, concentration warnings,
and reason code generation.
"""

import pytest
from datetime import datetime, timezone

from app.core.schemas import (
    Decision,
    DecisionConfig,
    Evaluation,
    GateResult,
    Opportunity,
    PillarWeights,
    QualityTier,
    ScannerTrigger,
    Verdict,
)
from app.decision.calculator import (
    DecisionCalculator,
    DecisionContext,
    assign_quality_tier,
    compute_decision,
    determine_verdict,
)
from app.decision.concentration import (
    analyze_concentration,
    check_concentration_warnings,
    check_directional_concentration,
    check_ticker_concentration,
    update_decisions_with_warnings,
)
from app.gates.models import GateEvaluation
from app.pillars.models import PillarResult, Subscore


# =============================================================================
# Test Fixtures
# =============================================================================


def make_evaluation(
    evaluation_id: str = "eval-001",
    ticker: str = "AAPL",
    option_type: str = "CALL",
    spread_pct: float = 3.0,
    policy_version: str = "v2.0.0",
    **kwargs,
) -> Evaluation:
    """Create a test Evaluation."""
    defaults = {
        "opportunity_id": "opp-001",
        "underlying_ticker": ticker,
        "option_ticker": f"O:{ticker}240119C00190000",
        "option_type": option_type,
        "expiration_date": "2024-01-19",
        "dte": 30,
        "strike": 190.0,
        "underlying_price": 185.0,
        "moneyness_pct": 2.7,
        "bid": 4.20,
        "ask": 4.40,
        "mid": 4.30,
        "spread_abs": 0.20,
        "spread_pct": spread_pct,
        "iv": 0.32,
        "delta": 0.45,
        "gamma": 0.03,
        "theta": -0.08,
        "vega": 0.25,
        "open_interest": 1500,
        "volume": 250,
        "breakeven_price": 194.30,
        "required_move_pct": 5.03,
        "expected_move_pct": 8.16,
        "feasibility_ratio": 0.62,
        "time_adjusted_feasibility": 0.56,
        "dte_bucket": "B",
        "rank_score": 85.0,
        "policy_version": policy_version,
        "policy_hash": "abc123",
    }
    defaults.update(kwargs)
    defaults["evaluation_id"] = evaluation_id
    return Evaluation(**defaults)


def make_pillar_results(
    evaluation_id: str,
    premium_leverage: float = 75.0,
    underlying_behavior: float = 80.0,
    setup_quality: float = 70.0,
) -> list[PillarResult]:
    """Create test PillarResult list (Policy v3.0.0)."""
    return [
        PillarResult(
            pillar_id="PREMIUM_LEVERAGE",
            evaluation_id=evaluation_id,
            score=premium_leverage,
            subscores=[
                Subscore(name="abs_delta", raw_value=0.12, score=90.0, weight=0.28),
                Subscore(name="iv", raw_value=0.30, score=85.0, weight=0.52),
            ],
            tags=["DELTA_FAR_OTM"] if premium_leverage >= 70 else [],
        ),
        PillarResult(
            pillar_id="UNDERLYING_BEHAVIOR",
            evaluation_id=evaluation_id,
            score=underlying_behavior,
            subscores=[
                Subscore(name="adx_14", raw_value=16.0, score=85.0, weight=0.46),
                Subscore(name="rv20", raw_value=0.40, score=80.0, weight=0.21),
            ],
            tags=["ADX_MODERATE_TREND"] if underlying_behavior >= 70 else [],
        ),
        PillarResult(
            pillar_id="SETUP_QUALITY",
            evaluation_id=evaluation_id,
            score=setup_quality,
            subscores=[
                Subscore(name="dte_bucket", raw_value="A", score=90.0, weight=0.25),
                Subscore(name="convergence_count", raw_value=2, score=90.0, weight=0.15),
            ],
            tags=["SCANNER_CONVERGENCE"] if setup_quality >= 70 else [],
        ),
    ]


def make_gate_evaluation(
    evaluation_id: str,
    all_passed: bool = True,
    failed_gates: list[str] = None,
) -> GateEvaluation:
    """Create a test GateEvaluation."""
    gate_results = []
    
    if all_passed:
        # All gates passed
        gate_results = [
            GateResult(
                evaluation_id=evaluation_id,
                gate_id="GATE_MIN_OPEN_INTEREST",
                enabled=True,
                passed=True,
                measured_value=1500,
                threshold_value=300,
                operator="gte",
                units="contracts",
                reason_code="GATE_PASS_MIN_OI",
            ),
            GateResult(
                evaluation_id=evaluation_id,
                gate_id="GATE_MAX_SPREAD_PCT",
                enabled=True,
                passed=True,
                measured_value=3.0,
                threshold_value=8.0,
                operator="lte",
                units="percent",
                reason_code="GATE_PASS_SPREAD",
            ),
        ]
    else:
        # Some gates failed
        failed_list = failed_gates or ["GATE_MIN_OPEN_INTEREST"]
        gate_results = [
            GateResult(
                evaluation_id=evaluation_id,
                gate_id="GATE_MIN_OPEN_INTEREST",
                enabled=True,
                passed="GATE_MIN_OPEN_INTEREST" not in failed_list,
                measured_value=150 if "GATE_MIN_OPEN_INTEREST" in failed_list else 1500,
                threshold_value=300,
                operator="gte",
                units="contracts",
                reason_code="GATE_FAIL_MIN_OI" if "GATE_MIN_OPEN_INTEREST" in failed_list else "GATE_PASS_MIN_OI",
            ),
            GateResult(
                evaluation_id=evaluation_id,
                gate_id="GATE_MAX_SPREAD_PCT",
                enabled=True,
                passed="GATE_MAX_SPREAD_PCT" not in failed_list,
                measured_value=12.0 if "GATE_MAX_SPREAD_PCT" in failed_list else 3.0,
                threshold_value=8.0,
                operator="lte",
                units="percent",
                reason_code="GATE_FAIL_SPREAD" if "GATE_MAX_SPREAD_PCT" in failed_list else "GATE_PASS_SPREAD",
            ),
        ]
    
    return GateEvaluation(
        evaluation_id=evaluation_id,
        gate_results=gate_results,
    )


# =============================================================================
# Test DecisionCalculator - Final Score Calculation
# =============================================================================


class TestFinalScoreCalculation:
    """Test final score calculation from pillar scores."""
    
    def test_default_weights(self):
        """Test final score with default weights (Policy v3.1.0: 0.25, 0.35, 0.40)."""
        calculator = DecisionCalculator()

        # 0.25 * 80 + 0.35 * 70 + 0.40 * 90 = 20 + 24.5 + 36 = 80.5
        final = calculator.compute_final_score(80.0, 70.0, 90.0)

        assert final == pytest.approx(80.5, rel=0.01)
    
    def test_custom_weights(self):
        """Test final score with custom weights."""
        weights = PillarWeights(premium_leverage=0.40, underlying_behavior=0.40, setup_quality=0.20)
        calculator = DecisionCalculator(pillar_weights=weights)
        
        # 0.40 * 80 + 0.40 * 70 + 0.20 * 90 = 32 + 28 + 18 = 78
        final = calculator.compute_final_score(80.0, 70.0, 90.0)
        
        assert final == pytest.approx(78.0, rel=0.01)
    
    def test_score_clamping(self):
        """Test that final score is clamped to 0-100."""
        calculator = DecisionCalculator()
        
        # Even with impossible inputs, should clamp
        final = calculator.compute_final_score(150.0, 150.0, 150.0)
        assert final == 100.0
        
        final = calculator.compute_final_score(-50.0, -50.0, -50.0)
        assert final == 0.0


class TestPerScannerWeightsDecision:
    """Test per-scanner weight selection in DecisionCalculator."""

    def test_per_scanner_weights_via_pillar_config(self):
        """DecisionCalculator with pillar_config uses scanner-specific weights."""
        from app.core.schemas import PillarConfig

        config = PillarConfig.v3_default().model_copy(update={
            "scanner_weights": {
                "BREAKOUT": PillarWeights(
                    premium_leverage=0.15,
                    underlying_behavior=0.80,
                    setup_quality=0.05,
                ),
            }
        })
        calculator = DecisionCalculator(pillar_config=config)

        # Global weights: 0.25*80+0.35*70+0.40*90=80.5
        global_score = calculator.compute_final_score(80.0, 70.0, 90.0)
        assert global_score == pytest.approx(80.5, abs=0.01)

        # BREAKOUT weights: 0.15*80+0.80*70+0.05*90=72.5
        breakout_score = calculator.compute_final_score(
            80.0, 70.0, 90.0, scanner_source="BREAKOUT"
        )
        assert breakout_score == pytest.approx(72.5, abs=0.01)

    def test_decision_context_populates_scanner_source(self):
        """from_evaluation_and_results sets scanner_source from evaluation."""
        eval_obj = make_evaluation(scanner_source="BREAKOUT")
        ctx = DecisionContext.from_evaluation_and_results(eval_obj, [])
        assert ctx.scanner_source == "BREAKOUT"

    def test_compute_decision_uses_scanner_source(self):
        """compute_decision uses ctx.scanner_source for weight selection."""
        from app.core.schemas import PillarConfig
        from app.decision.calculator import DecisionContext

        config = PillarConfig.v3_default().model_copy(update={
            "scanner_weights": {
                "UNUSUAL_VOLUME": PillarWeights(
                    premium_leverage=0.45,
                    underlying_behavior=0.15,
                    setup_quality=0.40,
                ),
            }
        })
        calculator = DecisionCalculator(pillar_config=config)

        ctx = DecisionContext(
            evaluation_id="test-001",
            underlying_ticker="AAPL",
            option_type="CALL",
            spread_pct=3.0,
            policy_version="3.1.0",
            premium_leverage_score=80.0,
            underlying_behavior_score=70.0,
            setup_quality_score=90.0,
            scanner_source="UNUSUAL_VOLUME",
        )
        decision = calculator.compute_decision(ctx)
        # 0.45*80+0.15*70+0.40*90=82.5
        assert decision.final_score == pytest.approx(82.5, abs=0.01)


# =============================================================================
# Test DecisionCalculator - Verdict Determination
# =============================================================================


class TestVerdictDetermination:
    """Test verdict determination from score and gate results."""
    
    def test_gate_failure_always_rejects(self):
        """Test that any gate failure results in REJECT."""
        calculator = DecisionCalculator()
        
        # High score but gates failed
        verdict, reason = calculator.determine_verdict(95.0, all_gates_passed=False)
        
        assert verdict == Verdict.REJECT
        assert reason == "REJECTED_BY_GATES"
    
    def test_approve_threshold_default(self):
        """Test APPROVE with default threshold (75)."""
        calculator = DecisionCalculator()
        
        # Exactly at threshold
        verdict, reason = calculator.determine_verdict(75.0, all_gates_passed=True)
        assert verdict == Verdict.APPROVE
        assert reason == "APPROVED_BY_SCORE"
        
        # Above threshold
        verdict, reason = calculator.determine_verdict(85.0, all_gates_passed=True)
        assert verdict == Verdict.APPROVE
    
    def test_watch_threshold_default(self):
        """Test WATCH with default thresholds (65-75)."""
        calculator = DecisionCalculator()
        
        # At watch threshold
        verdict, reason = calculator.determine_verdict(65.0, all_gates_passed=True)
        assert verdict == Verdict.WATCH
        assert reason == "WATCH_BY_SCORE"
        
        # Between thresholds
        verdict, reason = calculator.determine_verdict(70.0, all_gates_passed=True)
        assert verdict == Verdict.WATCH
        
        # Just below approve
        verdict, reason = calculator.determine_verdict(74.99, all_gates_passed=True)
        assert verdict == Verdict.WATCH
    
    def test_reject_by_score(self):
        """Test REJECT when score below watch threshold."""
        calculator = DecisionCalculator()
        
        # Below watch threshold
        verdict, reason = calculator.determine_verdict(64.99, all_gates_passed=True)
        assert verdict == Verdict.REJECT
        assert reason == "REJECTED_BY_SCORE"
        
        # Much lower
        verdict, reason = calculator.determine_verdict(40.0, all_gates_passed=True)
        assert verdict == Verdict.REJECT
    
    def test_custom_thresholds(self):
        """Test with custom approve/watch thresholds."""
        config = DecisionConfig(approve_threshold=80, watch_threshold=60)
        calculator = DecisionCalculator(decision_config=config)
        
        # Between new thresholds
        verdict, reason = calculator.determine_verdict(75.0, all_gates_passed=True)
        assert verdict == Verdict.WATCH  # Would be APPROVE with defaults


# =============================================================================
# Test DecisionCalculator - Quality Tier Assignment
# =============================================================================


class TestQualityTierAssignment:
    """Test quality tier assignment for APPROVE verdicts."""
    
    def test_tier_1_requirements(self):
        """Test TIER_1: score >= 85, all pillars >= 70, spread <= 5%."""
        calculator = DecisionCalculator()
        
        tier = calculator.assign_quality_tier(
            final_score=87.0,
            premium_leverage=75.0,
            underlying_behavior=80.0,
            setup_quality=72.0,
            spread_pct=4.0,
        )
        
        assert tier == QualityTier.TIER_1
    
    def test_tier_1_fails_on_low_score(self):
        """Test TIER_1 fails if score < 85."""
        calculator = DecisionCalculator()
        
        tier = calculator.assign_quality_tier(
            final_score=82.0,  # Below 85
            premium_leverage=75.0,
            underlying_behavior=80.0,
            setup_quality=72.0,
            spread_pct=4.0,
        )
        
        assert tier == QualityTier.TIER_2  # Falls to TIER_2
    
    def test_tier_1_fails_on_low_pillar(self):
        """Test TIER_1 fails if any pillar < 70."""
        calculator = DecisionCalculator()
        
        tier = calculator.assign_quality_tier(
            final_score=87.0,
            premium_leverage=75.0,
            underlying_behavior=80.0,
            setup_quality=65.0,  # Below 70
            spread_pct=4.0,
        )
        
        assert tier == QualityTier.TIER_2
    
    def test_tier_1_fails_on_wide_spread(self):
        """Test TIER_1 fails if spread > 5%."""
        calculator = DecisionCalculator()
        
        tier = calculator.assign_quality_tier(
            final_score=87.0,
            premium_leverage=75.0,
            underlying_behavior=80.0,
            setup_quality=72.0,
            spread_pct=6.0,  # Above 5%
        )
        
        assert tier == QualityTier.TIER_2
    
    def test_tier_2_requirements(self):
        """Test TIER_2: score >= 75, all pillars >= 55."""
        calculator = DecisionCalculator()
        
        tier = calculator.assign_quality_tier(
            final_score=78.0,
            premium_leverage=60.0,
            underlying_behavior=65.0,
            setup_quality=58.0,
            spread_pct=7.0,
        )
        
        assert tier == QualityTier.TIER_2
    
    def test_tier_3_weak_pillar(self):
        """Test TIER_3: APPROVE but one pillar < 55."""
        calculator = DecisionCalculator()
        
        tier = calculator.assign_quality_tier(
            final_score=76.0,
            premium_leverage=50.0,  # Below 55
            underlying_behavior=85.0,
            setup_quality=80.0,
            spread_pct=3.0,
        )
        
        assert tier == QualityTier.TIER_3
    
    def test_no_tier_below_approve(self):
        """Test no tier assigned if below approve threshold."""
        calculator = DecisionCalculator()
        
        tier = calculator.assign_quality_tier(
            final_score=70.0,  # Below 75
            premium_leverage=80.0,
            underlying_behavior=80.0,
            setup_quality=80.0,
            spread_pct=3.0,
        )
        
        assert tier is None


# =============================================================================
# Test DecisionCalculator - Full Decision Computation
# =============================================================================


class TestFullDecisionComputation:
    """Test complete decision computation flow."""
    
    def test_approve_with_tier_1(self):
        """Test approved decision with TIER_1 quality."""
        evaluation = make_evaluation()
        pillar_results = make_pillar_results(
            evaluation.evaluation_id,
            premium_leverage=86.0,
            underlying_behavior=88.0,
            setup_quality=85.0,
        )
        gate_eval = make_gate_evaluation(evaluation.evaluation_id, all_passed=True)

        decision = compute_decision(
            evaluation=evaluation,
            pillar_results=pillar_results,
            gate_evaluation=gate_eval,
        )

        assert decision.verdict == Verdict.APPROVE
        assert decision.quality_tier == QualityTier.TIER_1
        assert decision.primary_reason_code == "APPROVED_BY_SCORE"
        assert decision.failed_gates == []
        # v3.1.0 final score: 0.25*86 + 0.35*88 + 0.40*85 = 21.5 + 30.8 + 34 = 86.3
        assert decision.final_score >= 85.0
    
    def test_approve_with_tier_3(self):
        """Test approved decision with TIER_3 (weak pillar)."""
        evaluation = make_evaluation()
        # Final score: 0.35*52 + 0.35*95 + 0.30*90 = 18.2 + 33.25 + 27 = 78.45 (APPROVE)
        # But directional < 55 means TIER_3
        pillar_results = make_pillar_results(
            evaluation.evaluation_id,
            premium_leverage=52.0,  # Weak (below 55)
            underlying_behavior=95.0,
            setup_quality=90.0,
        )
        gate_eval = make_gate_evaluation(evaluation.evaluation_id, all_passed=True)
        
        decision = compute_decision(
            evaluation=evaluation,
            pillar_results=pillar_results,
            gate_evaluation=gate_eval,
        )
        
        assert decision.verdict == Verdict.APPROVE
        assert decision.quality_tier == QualityTier.TIER_3
        assert "WEAK_PREMIUM_LEVERAGE" in decision.supporting_reason_codes
    
    def test_watch_verdict(self):
        """Test WATCH verdict."""
        evaluation = make_evaluation()
        pillar_results = make_pillar_results(
            evaluation.evaluation_id,
            premium_leverage=70.0,
            underlying_behavior=68.0,
            setup_quality=65.0,
        )
        gate_eval = make_gate_evaluation(evaluation.evaluation_id, all_passed=True)
        
        decision = compute_decision(
            evaluation=evaluation,
            pillar_results=pillar_results,
            gate_evaluation=gate_eval,
        )
        
        # Final: 0.35*70 + 0.35*68 + 0.30*65 = 24.5 + 23.8 + 19.5 = 67.8
        assert decision.verdict == Verdict.WATCH
        assert decision.quality_tier is None
        assert decision.primary_reason_code == "WATCH_BY_SCORE"
    
    def test_reject_by_gates(self):
        """Test REJECT due to gate failure."""
        evaluation = make_evaluation()
        pillar_results = make_pillar_results(
            evaluation.evaluation_id,
            premium_leverage=90.0,
            underlying_behavior=90.0,
            setup_quality=90.0,
        )
        gate_eval = make_gate_evaluation(
            evaluation.evaluation_id,
            all_passed=False,
            failed_gates=["GATE_MIN_OPEN_INTEREST"],
        )
        
        decision = compute_decision(
            evaluation=evaluation,
            pillar_results=pillar_results,
            gate_evaluation=gate_eval,
        )
        
        assert decision.verdict == Verdict.REJECT
        assert decision.quality_tier is None
        assert decision.primary_reason_code == "REJECTED_BY_GATES"
        assert "GATE_MIN_OPEN_INTEREST" in decision.failed_gates
    
    def test_reject_by_score(self):
        """Test REJECT due to low score."""
        evaluation = make_evaluation()
        pillar_results = make_pillar_results(
            evaluation.evaluation_id,
            premium_leverage=50.0,
            underlying_behavior=55.0,
            setup_quality=45.0,
        )
        gate_eval = make_gate_evaluation(evaluation.evaluation_id, all_passed=True)
        
        decision = compute_decision(
            evaluation=evaluation,
            pillar_results=pillar_results,
            gate_evaluation=gate_eval,
        )
        
        # Final: 0.35*50 + 0.35*55 + 0.30*45 = 17.5 + 19.25 + 13.5 = 50.25
        assert decision.verdict == Verdict.REJECT
        assert decision.primary_reason_code == "REJECTED_BY_SCORE"


# =============================================================================
# Test Concentration Warnings
# =============================================================================


class TestTickerConcentration:
    """Test ticker concentration warning detection."""
    
    def test_no_warning_under_threshold(self):
        """Test no warning when contracts per ticker <= 3."""
        evaluations = [
            make_evaluation("eval-1", ticker="AAPL"),
            make_evaluation("eval-2", ticker="AAPL"),
            make_evaluation("eval-3", ticker="AAPL"),  # 3 is OK
            make_evaluation("eval-4", ticker="MSFT"),
        ]
        decisions = {
            "eval-1": Decision(
                evaluation_id="eval-1", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-2": Decision(
                evaluation_id="eval-2", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-3": Decision(
                evaluation_id="eval-3", verdict=Verdict.WATCH, quality_tier=None,
                final_score=70.0, premium_leverage_score=65.0, underlying_behavior_score=70.0, setup_quality_score=75.0,
                primary_reason_code="WATCH_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-4": Decision(
                evaluation_id="eval-4", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
        }
        
        warnings = check_ticker_concentration(evaluations, decisions)
        
        assert len(warnings) == 0
    
    def test_warning_over_threshold(self):
        """Test warning when contracts per ticker > 3."""
        evaluations = [
            make_evaluation("eval-1", ticker="AAPL"),
            make_evaluation("eval-2", ticker="AAPL"),
            make_evaluation("eval-3", ticker="AAPL"),
            make_evaluation("eval-4", ticker="AAPL"),  # 4th = warning
        ]
        decisions = {
            f"eval-{i}": Decision(
                evaluation_id=f"eval-{i}", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            )
            for i in range(1, 5)
        }
        
        warnings = check_ticker_concentration(evaluations, decisions)
        
        assert len(warnings) == 4  # All 4 AAPL contracts get warning
        for eval_id in warnings:
            assert any("SAME_TICKER" in w for w in warnings[eval_id])
    
    def test_rejects_not_counted(self):
        """Test that REJECT verdicts don't count toward ticker concentration."""
        evaluations = [
            make_evaluation("eval-1", ticker="AAPL"),
            make_evaluation("eval-2", ticker="AAPL"),
            make_evaluation("eval-3", ticker="AAPL"),
            make_evaluation("eval-4", ticker="AAPL"),  # Rejected
        ]
        decisions = {
            "eval-1": Decision(
                evaluation_id="eval-1", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-2": Decision(
                evaluation_id="eval-2", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-3": Decision(
                evaluation_id="eval-3", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-4": Decision(
                evaluation_id="eval-4", verdict=Verdict.REJECT, quality_tier=None,
                final_score=50.0, premium_leverage_score=45.0, underlying_behavior_score=50.0, setup_quality_score=55.0,
                primary_reason_code="REJECTED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
        }
        
        warnings = check_ticker_concentration(evaluations, decisions)
        
        assert len(warnings) == 0  # Only 3 non-rejected AAPL = OK


class TestDirectionalConcentration:
    """Test directional concentration warning detection."""
    
    def test_no_warning_balanced(self):
        """Test no warning when direction is balanced."""
        evaluations = [
            make_evaluation("eval-1", option_type="CALL"),
            make_evaluation("eval-2", option_type="CALL"),
            make_evaluation("eval-3", option_type="PUT"),
            make_evaluation("eval-4", option_type="PUT"),
        ]
        decisions = {
            f"eval-{i}": Decision(
                evaluation_id=f"eval-{i}", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            )
            for i in range(1, 5)
        }
        
        warnings = check_directional_concentration(evaluations, decisions)
        
        assert len(warnings) == 0  # 50% each = OK
    
    def test_warning_call_heavy(self):
        """Test warning when >70% CALLs."""
        evaluations = [
            make_evaluation("eval-1", option_type="CALL"),
            make_evaluation("eval-2", option_type="CALL"),
            make_evaluation("eval-3", option_type="CALL"),
            make_evaluation("eval-4", option_type="CALL"),  # 4 CALLs
            make_evaluation("eval-5", option_type="PUT"),   # 1 PUT = 80% CALLs
        ]
        decisions = {
            f"eval-{i}": Decision(
                evaluation_id=f"eval-{i}", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            )
            for i in range(1, 6)
        }
        
        warnings = check_directional_concentration(evaluations, decisions)
        
        assert len(warnings) == 5  # All approves get warning
        for eval_id in warnings:
            assert any("DIRECTIONAL:CALL" in w for w in warnings[eval_id])
    
    def test_warning_put_heavy(self):
        """Test warning when >70% PUTs."""
        evaluations = [
            make_evaluation("eval-1", option_type="PUT"),
            make_evaluation("eval-2", option_type="PUT"),
            make_evaluation("eval-3", option_type="PUT"),
            make_evaluation("eval-4", option_type="PUT"),  # 4 PUTs
            make_evaluation("eval-5", option_type="CALL"),  # 1 CALL = 80% PUTs
        ]
        decisions = {
            f"eval-{i}": Decision(
                evaluation_id=f"eval-{i}", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            )
            for i in range(1, 6)
        }
        
        warnings = check_directional_concentration(evaluations, decisions)
        
        assert len(warnings) == 5
        for eval_id in warnings:
            assert any("DIRECTIONAL:PUT" in w for w in warnings[eval_id])
    
    def test_watch_not_counted(self):
        """Test that WATCH verdicts don't count toward directional concentration."""
        evaluations = [
            make_evaluation("eval-1", option_type="CALL"),
            make_evaluation("eval-2", option_type="CALL"),
            make_evaluation("eval-3", option_type="CALL"),  # All 3 approves are CALL
            make_evaluation("eval-4", option_type="PUT"),   # WATCH - not counted
        ]
        decisions = {
            "eval-1": Decision(
                evaluation_id="eval-1", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-2": Decision(
                evaluation_id="eval-2", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-3": Decision(
                evaluation_id="eval-3", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-4": Decision(
                evaluation_id="eval-4", verdict=Verdict.WATCH, quality_tier=None,
                final_score=70.0, premium_leverage_score=65.0, underlying_behavior_score=70.0, setup_quality_score=75.0,
                primary_reason_code="WATCH_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
        }
        
        warnings = check_directional_concentration(evaluations, decisions)
        
        # 100% CALL among approves = warning
        assert len(warnings) == 3  # Only the 3 approves


class TestCombinedConcentration:
    """Test combined concentration warning checks."""
    
    def test_combined_warnings(self):
        """Test both ticker and directional warnings together."""
        evaluations = [
            make_evaluation("eval-1", ticker="AAPL", option_type="CALL"),
            make_evaluation("eval-2", ticker="AAPL", option_type="CALL"),
            make_evaluation("eval-3", ticker="AAPL", option_type="CALL"),
            make_evaluation("eval-4", ticker="AAPL", option_type="CALL"),  # 4 AAPL + all CALL
        ]
        decisions = {
            f"eval-{i}": Decision(
                evaluation_id=f"eval-{i}", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            )
            for i in range(1, 5)
        }
        
        warnings = check_concentration_warnings(evaluations, decisions)
        
        # All 4 should have both warnings
        assert len(warnings) == 4
        for eval_id in warnings:
            assert any("SAME_TICKER" in w for w in warnings[eval_id])
            assert any("DIRECTIONAL:CALL" in w for w in warnings[eval_id])


class TestUpdateDecisionsWithWarnings:
    """Test updating decisions with concentration warnings."""
    
    def test_updates_preserve_other_fields(self):
        """Test that update preserves all original decision fields."""
        original = Decision(
            evaluation_id="eval-1",
            verdict=Verdict.APPROVE,
            quality_tier=QualityTier.TIER_1,
            final_score=87.5,
            premium_leverage_score=85.0,
            underlying_behavior_score=88.0,
            setup_quality_score=90.0,
            primary_reason_code="APPROVED_BY_SCORE",
            supporting_reason_codes=["STRONG_PREMIUM_LEVERAGE", "STRONG_UNDERLYING_BEHAVIOR"],
            failed_gates=[],
            concentration_warnings=[],
            policy_version="v2.0.0",
        )
        
        warnings = {"eval-1": ["WARN_CONCENTRATION_SAME_TICKER:AAPL:4"]}
        
        updated = update_decisions_with_warnings({"eval-1": original}, warnings)
        
        new_decision = updated["eval-1"]
        assert new_decision.verdict == original.verdict
        assert new_decision.quality_tier == original.quality_tier
        assert new_decision.final_score == original.final_score
        assert new_decision.premium_leverage_score == original.premium_leverage_score
        assert new_decision.primary_reason_code == original.primary_reason_code
        assert "WARN_CONCENTRATION_SAME_TICKER:AAPL:4" in new_decision.concentration_warnings


# =============================================================================
# Test DecisionContext
# =============================================================================


class TestDecisionContext:
    """Test DecisionContext construction."""
    
    def test_from_evaluation_and_results(self):
        """Test building context from evaluation and results."""
        evaluation = make_evaluation()
        pillar_results = make_pillar_results(
            evaluation.evaluation_id,
            premium_leverage=78.0,
            underlying_behavior=82.0,
            setup_quality=75.0,
        )
        gate_eval = make_gate_evaluation(evaluation.evaluation_id, all_passed=True)
        
        ctx = DecisionContext.from_evaluation_and_results(
            evaluation=evaluation,
            pillar_results=pillar_results,
            gate_evaluation=gate_eval,
        )
        
        assert ctx.evaluation_id == evaluation.evaluation_id
        assert ctx.underlying_ticker == "AAPL"
        assert ctx.option_type == "CALL"
        assert ctx.spread_pct == 3.0
        assert ctx.premium_leverage_score == 78.0
        assert ctx.underlying_behavior_score == 82.0
        assert ctx.setup_quality_score == 75.0
        assert ctx.all_gates_passed is True
        assert ctx.failed_gates == []
    
    def test_context_with_failed_gates(self):
        """Test context captures failed gates correctly."""
        evaluation = make_evaluation()
        pillar_results = make_pillar_results(evaluation.evaluation_id)
        gate_eval = make_gate_evaluation(
            evaluation.evaluation_id,
            all_passed=False,
            failed_gates=["GATE_MIN_OPEN_INTEREST", "GATE_MAX_SPREAD_PCT"],
        )
        
        ctx = DecisionContext.from_evaluation_and_results(
            evaluation=evaluation,
            pillar_results=pillar_results,
            gate_evaluation=gate_eval,
        )
        
        assert ctx.all_gates_passed is False
        assert "GATE_MIN_OPEN_INTEREST" in ctx.failed_gates
        assert "GATE_MAX_SPREAD_PCT" in ctx.failed_gates


# =============================================================================
# Test Batch Processing
# =============================================================================


class TestBatchProcessing:
    """Test batch decision processing."""
    
    def test_batch_processing(self):
        """Test processing multiple evaluations in batch."""
        evaluations = [
            make_evaluation("eval-1", ticker="AAPL"),
            make_evaluation("eval-2", ticker="MSFT"),
            make_evaluation("eval-3", ticker="GOOGL"),
        ]
        
        pillar_results = {
            "eval-1": make_pillar_results("eval-1", 85.0, 88.0, 82.0),  # APPROVE
            "eval-2": make_pillar_results("eval-2", 70.0, 68.0, 65.0),  # WATCH
            "eval-3": make_pillar_results("eval-3", 45.0, 50.0, 48.0),  # REJECT
        }
        
        gate_evaluations = {
            "eval-1": make_gate_evaluation("eval-1", all_passed=True),
            "eval-2": make_gate_evaluation("eval-2", all_passed=True),
            "eval-3": make_gate_evaluation("eval-3", all_passed=True),
        }
        
        calculator = DecisionCalculator()
        decisions = calculator.compute_decisions_batch(
            evaluations=evaluations,
            pillar_results=pillar_results,
            gate_evaluations=gate_evaluations,
        )
        
        assert len(decisions) == 3
        assert decisions["eval-1"].verdict == Verdict.APPROVE
        assert decisions["eval-2"].verdict == Verdict.WATCH
        assert decisions["eval-3"].verdict == Verdict.REJECT


# =============================================================================
# Test Convenience Functions
# =============================================================================


class TestConvenienceFunctions:
    """Test convenience functions."""
    
    def test_determine_verdict_function(self):
        """Test standalone determine_verdict function."""
        verdict, reason = determine_verdict(80.0, all_gates_passed=True)
        assert verdict == Verdict.APPROVE
        
        verdict, reason = determine_verdict(80.0, all_gates_passed=False)
        assert verdict == Verdict.REJECT
        assert reason == "REJECTED_BY_GATES"
    
    def test_assign_quality_tier_function(self):
        """Test standalone assign_quality_tier function."""
        tier = assign_quality_tier(
            final_score=87.0,
            premium_leverage=75.0,
            underlying_behavior=80.0,
            setup_quality=72.0,
            spread_pct=4.0,
        )
        assert tier == QualityTier.TIER_1


# =============================================================================
# Test Concentration Analysis
# =============================================================================


class TestConcentrationAnalysis:
    """Test concentration analysis."""
    
    def test_analyze_concentration(self):
        """Test comprehensive concentration analysis."""
        evaluations = [
            make_evaluation("eval-1", ticker="AAPL", option_type="CALL"),
            make_evaluation("eval-2", ticker="AAPL", option_type="CALL"),
            make_evaluation("eval-3", ticker="MSFT", option_type="CALL"),
            make_evaluation("eval-4", ticker="GOOGL", option_type="PUT"),
        ]
        decisions = {
            "eval-1": Decision(
                evaluation_id="eval-1", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-2": Decision(
                evaluation_id="eval-2", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=80.0, premium_leverage_score=75.0, underlying_behavior_score=80.0, setup_quality_score=85.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-3": Decision(
                evaluation_id="eval-3", verdict=Verdict.WATCH, quality_tier=None,
                final_score=70.0, premium_leverage_score=65.0, underlying_behavior_score=70.0, setup_quality_score=75.0,
                primary_reason_code="WATCH_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
            "eval-4": Decision(
                evaluation_id="eval-4", verdict=Verdict.APPROVE, quality_tier=QualityTier.TIER_2,
                final_score=78.0, premium_leverage_score=72.0, underlying_behavior_score=78.0, setup_quality_score=82.0,
                primary_reason_code="APPROVED_BY_SCORE", supporting_reason_codes=[],
                failed_gates=[], concentration_warnings=[], policy_version="v2.0.0",
            ),
        }
        
        analysis = analyze_concentration(evaluations, decisions)
        
        assert analysis.total_approves == 3
        assert analysis.total_watches == 1
        assert analysis.ticker_counts["AAPL"] == 2  # APPROVE + APPROVE
        assert analysis.ticker_counts["MSFT"] == 1  # WATCH
        assert analysis.ticker_counts["GOOGL"] == 1  # APPROVE
        assert analysis.call_count == 2  # 2 CALL approves
        assert analysis.put_count == 1  # 1 PUT approve
        assert analysis.call_pct == pytest.approx(2/3, rel=0.01)
