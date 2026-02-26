"""Unit tests for Hard Gates (Stage 6).

Tests cover:
1. Individual gate pass/fail scenarios
2. Conditional gate (breakout volume) skip logic
3. Greeks coherence edge cases
4. Full calculator integration tests
5. Batch processing tests
6. GateContext construction
"""

import pytest
from datetime import datetime, timezone

from app.core.schemas import (
    Evaluation,
    Opportunity,
    ScannerTrigger,
    ScannerType,
    DirectionHint,
    OptionType,
    DTEBucket,
    GateConfig,
    GateOperator,
)
from app.features.models import FeatureSet
from app.gates.models import GateContext, GateEvaluation
from app.gates.gates import (
    check_min_open_interest,
    check_min_volume,
    check_max_spread_pct,
    check_dte_range,
    check_move_sufficiency,
    check_iv_percentile_max,
    check_breakout_volume,
    check_greeks_coherence,
    check_theta_burden_max,
    ALL_GATES,
)
from app.gates.calculator import (
    GateCalculator,
    evaluate_all_gates,
    check_all_gates_passed,
    get_failed_gates,
)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def default_config():
    """Default gate configuration."""
    return GateConfig()


@pytest.fixture
def passing_call_context():
    """A call context that should pass all gates."""
    return GateContext(
        evaluation_id="test-eval-pass-call",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL240315C00185000",
        option_type="CALL",
        dte=30,
        strike=185.0,
        underlying_price=180.0,
        bid=4.50,
        ask=4.60,
        mid=4.55,
        spread_pct=2.2,  # Pass (< 8%)
        delta=0.55,  # Valid CALL delta
        gamma=0.02,  # Positive
        theta=-0.08,  # Negative
        vega=0.25,  # Positive
        iv=0.32,
        open_interest=500,  # Pass (>= 300)
        volume=100,  # Pass (>= 75)
        time_adjusted_feasibility=0.85,  # Pass (<= 1.25)
        iv_percentile=45.0,  # Pass (<= 85)
        theta_pct=1.76,  # Pass (<= 4%)
        scanner_triggers=[],
        volume_ratio=None,
    )


@pytest.fixture
def passing_put_context():
    """A put context that should pass all gates."""
    return GateContext(
        evaluation_id="test-eval-pass-put",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL240315P00175000",
        option_type="PUT",
        dte=30,
        strike=175.0,
        underlying_price=180.0,
        bid=3.20,
        ask=3.30,
        mid=3.25,
        spread_pct=3.1,
        delta=-0.40,  # Valid PUT delta
        gamma=0.02,
        theta=-0.06,
        vega=0.22,
        iv=0.30,
        open_interest=400,
        volume=80,
        time_adjusted_feasibility=0.75,
        iv_percentile=35.0,
        theta_pct=1.85,
        scanner_triggers=[],
        volume_ratio=None,
    )


@pytest.fixture
def breakout_context():
    """Context with breakout scanner trigger."""
    return GateContext(
        evaluation_id="test-eval-breakout",
        underlying_ticker="TSLA",
        option_ticker="O:TSLA240315C00250000",
        option_type="CALL",
        dte=21,
        strike=250.0,
        underlying_price=245.0,
        bid=8.00,
        ask=8.20,
        mid=8.10,
        spread_pct=2.5,
        delta=0.52,
        gamma=0.015,
        theta=-0.12,
        vega=0.35,
        iv=0.45,
        open_interest=1500,
        volume=350,
        time_adjusted_feasibility=0.65,
        iv_percentile=55.0,
        theta_pct=1.48,
        scanner_triggers=["BREAKOUT"],
        volume_ratio=2.5,  # >= 1.5x threshold
    )


@pytest.fixture
def sample_evaluation():
    """Sample evaluation for testing."""
    return Evaluation(
        evaluation_id="test-eval-1",
        opportunity_id="opp-1",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL240315C00185000",
        option_type=OptionType.CALL,
        expiration_date="2024-03-15",
        dte=30,
        strike=185.0,
        underlying_price=180.0,
        moneyness_pct=2.78,
        bid=4.50,
        ask=4.60,
        mid=4.55,
        spread_abs=0.10,
        spread_pct=2.2,
        iv=0.32,
        delta=0.55,
        gamma=0.02,
        theta=-0.08,
        vega=0.25,
        open_interest=500,
        volume=100,
        breakeven_price=189.55,
        required_move_pct=5.3,
        expected_move_pct=8.5,
        feasibility_ratio=0.62,
        time_adjusted_feasibility=0.85,
        dte_bucket=DTEBucket.B,
        rank_score=75.5,
        policy_version="v2.0.0",
        policy_hash="abc123",
    )


@pytest.fixture
def sample_opportunity():
    """Sample opportunity with breakout trigger."""
    return Opportunity(
        opportunity_id="opp-1",
        underlying_ticker="AAPL",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        scanner_triggers=[
            ScannerTrigger(
                scanner_type=ScannerType.BREAKOUT,
                reason_codes=["BREAKOUT_CLOSE_ABOVE_RANGE"],
                metrics={"volume_ratio": 1.8, "prior_N_day_high": 178.50},
                triggered_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
        direction_hint=DirectionHint.CALL,
        priority_score=75,
    )


@pytest.fixture
def sample_feature_set():
    """Sample feature set."""
    return FeatureSet(
        evaluation_id="test-eval-1",
        close=180.0,
        iv_percentile=45.0,
    )


# ============================================================================
# GATE_MIN_OPEN_INTEREST Tests
# ============================================================================


class TestGateMinOpenInterest:
    """Tests for GATE_MIN_OPEN_INTEREST."""
    
    def test_pass_above_threshold(self, passing_call_context, default_config):
        """Should pass when OI >= 300."""
        result = check_min_open_interest(passing_call_context, default_config)
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_MIN_OI"
        assert result.measured_value == 500.0
        assert result.threshold_value == 300.0
    
    def test_fail_below_threshold(self, passing_call_context, default_config):
        """Should fail when OI < 300."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "open_interest": 150}
        )
        result = check_min_open_interest(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_MIN_OI"
    
    def test_pass_at_threshold(self, passing_call_context, default_config):
        """Should pass when OI == 300 (boundary)."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "open_interest": 300}
        )
        result = check_min_open_interest(ctx, default_config)
        assert result.passed is True


# ============================================================================
# GATE_MIN_VOLUME Tests
# ============================================================================


class TestGateMinVolume:
    """Tests for GATE_MIN_VOLUME."""
    
    def test_pass_above_threshold(self, passing_call_context, default_config):
        """Should pass when volume >= 75."""
        result = check_min_volume(passing_call_context, default_config)
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_MIN_VOLUME"
    
    def test_fail_below_threshold(self, passing_call_context, default_config):
        """Should fail when volume < 75."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "volume": 50}
        )
        result = check_min_volume(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_MIN_VOLUME"


# ============================================================================
# GATE_MAX_SPREAD_PCT Tests
# ============================================================================


class TestGateMaxSpreadPct:
    """Tests for GATE_MAX_SPREAD_PCT."""
    
    def test_pass_below_threshold(self, passing_call_context, default_config):
        """Should pass when spread <= 8%."""
        result = check_max_spread_pct(passing_call_context, default_config)
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_SPREAD"
    
    def test_fail_above_threshold(self, passing_call_context, default_config):
        """Should fail when spread > 8%."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "spread_pct": 10.5}
        )
        result = check_max_spread_pct(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_SPREAD"
    
    def test_pass_at_threshold(self, passing_call_context, default_config):
        """Should pass when spread == 8% (boundary)."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "spread_pct": 8.0}
        )
        result = check_max_spread_pct(ctx, default_config)
        assert result.passed is True


# ============================================================================
# GATE_DTE_RANGE Tests
# ============================================================================


class TestGateDTERange:
    """Tests for GATE_DTE_RANGE."""
    
    def test_pass_in_range(self, passing_call_context, default_config):
        """Should pass when 7 <= DTE <= 120."""
        result = check_dte_range(passing_call_context, default_config)
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_DTE"
    
    def test_fail_too_short(self, passing_call_context, default_config):
        """Should fail when DTE < 7."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "dte": 5}
        )
        result = check_dte_range(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_DTE_TOO_SHORT"
        assert "gamma risk" in result.notes
    
    def test_fail_too_long(self, passing_call_context, default_config):
        """Should fail when DTE > 120."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "dte": 150}
        )
        result = check_dte_range(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_DTE_TOO_LONG"
        assert "capital efficiency" in result.notes
    
    def test_pass_at_min_boundary(self, passing_call_context, default_config):
        """Should pass when DTE == 7."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "dte": 7}
        )
        result = check_dte_range(ctx, default_config)
        assert result.passed is True
    
    def test_pass_at_max_boundary(self, passing_call_context, default_config):
        """Should pass when DTE == 120."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "dte": 120}
        )
        result = check_dte_range(ctx, default_config)
        assert result.passed is True


# ============================================================================
# GATE_MOVE_SUFFICIENCY Tests
# ============================================================================


class TestGateMoveSufficiency:
    """Tests for GATE_MOVE_SUFFICIENCY."""
    
    def test_pass_below_threshold(self, passing_call_context, default_config):
        """Should pass when time_adjusted_feasibility <= 1.25."""
        result = check_move_sufficiency(passing_call_context, default_config)
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_MOVE_SUFFICIENCY"
    
    def test_fail_above_threshold(self, passing_call_context, default_config):
        """Should fail when time_adjusted_feasibility > 1.25."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "time_adjusted_feasibility": 1.5}
        )
        result = check_move_sufficiency(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_MOVE_SUFFICIENCY"


# ============================================================================
# GATE_IV_PERCENTILE_MAX Tests
# ============================================================================


class TestGateIVPercentileMax:
    """Tests for GATE_IV_PERCENTILE_MAX."""
    
    def test_pass_below_threshold(self, passing_call_context, default_config):
        """Should pass when IV percentile <= 85."""
        result = check_iv_percentile_max(passing_call_context, default_config)
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_IV_PERCENTILE"
    
    def test_fail_above_threshold(self, passing_call_context, default_config):
        """Should fail when IV percentile > 85."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "iv_percentile": 90.0}
        )
        result = check_iv_percentile_max(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_IV_PERCENTILE"
    
    def test_pass_when_none(self, passing_call_context, default_config):
        """Should pass when IV percentile is None (data unavailable)."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "iv_percentile": None}
        )
        result = check_iv_percentile_max(ctx, default_config)
        assert result.passed is True
        assert "not available" in result.notes


# ============================================================================
# GATE_BREAKOUT_VOLUME Tests (Conditional)
# ============================================================================


class TestGateBreakoutVolume:
    """Tests for GATE_BREAKOUT_VOLUME (conditional gate)."""
    
    def test_skip_when_not_breakout(self, passing_call_context, default_config):
        """Should skip (enabled=False) when no breakout trigger."""
        result = check_breakout_volume(passing_call_context, default_config)
        assert result.enabled is False
        assert result.passed is True
        assert result.reason_code == "GATE_SKIP_NOT_BREAKOUT"
    
    def test_pass_with_sufficient_volume(self, breakout_context, default_config):
        """Should pass when volume_ratio >= 1.5."""
        result = check_breakout_volume(breakout_context, default_config)
        assert result.enabled is True
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_BREAKOUT_VOLUME"
    
    def test_fail_with_insufficient_volume(self, breakout_context, default_config):
        """Should fail when volume_ratio < 1.0."""
        ctx = GateContext(
            **{**breakout_context.__dict__, "volume_ratio": 0.8}
        )
        result = check_breakout_volume(ctx, default_config)
        assert result.enabled is True
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_BREAKOUT_VOLUME"
    
    def test_pass_when_volume_ratio_none(self, breakout_context, default_config):
        """Should pass when volume_ratio is None (data unavailable)."""
        ctx = GateContext(
            **{**breakout_context.__dict__, "volume_ratio": None}
        )
        result = check_breakout_volume(ctx, default_config)
        assert result.passed is True
        assert "not available" in result.notes


# ============================================================================
# GATE_GREEKS_COHERENCE Tests
# ============================================================================


class TestGateGreeksCoherence:
    """Tests for GATE_GREEKS_COHERENCE."""
    
    def test_pass_valid_call_greeks(self, passing_call_context, default_config):
        """Should pass with valid CALL Greeks."""
        result = check_greeks_coherence(passing_call_context, default_config)
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_GREEKS_COHERENCE"
    
    def test_pass_valid_put_greeks(self, passing_put_context, default_config):
        """Should pass with valid PUT Greeks."""
        result = check_greeks_coherence(passing_put_context, default_config)
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_GREEKS_COHERENCE"
    
    def test_fail_call_negative_delta(self, passing_call_context, default_config):
        """Should fail CALL with negative delta."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "delta": -0.30}
        )
        result = check_greeks_coherence(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_GREEKS_DELTA"
    
    def test_fail_put_positive_delta(self, passing_put_context, default_config):
        """Should fail PUT with positive delta."""
        ctx = GateContext(
            **{**passing_put_context.__dict__, "delta": 0.30}
        )
        result = check_greeks_coherence(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_GREEKS_DELTA"
    
    def test_fail_positive_theta(self, passing_call_context, default_config):
        """Should fail with positive theta."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "theta": 0.05}
        )
        result = check_greeks_coherence(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_GREEKS_THETA"
    
    def test_fail_negative_vega(self, passing_call_context, default_config):
        """Should fail with negative vega."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "vega": -0.10}
        )
        result = check_greeks_coherence(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_GREEKS_VEGA"
    
    def test_fail_negative_gamma(self, passing_call_context, default_config):
        """Should fail with negative gamma."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "gamma": -0.01}
        )
        result = check_greeks_coherence(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_GREEKS_GAMMA"


# ============================================================================
# GATE_THETA_BURDEN_MAX Tests
# ============================================================================


class TestGateThetaBurdenMax:
    """Tests for GATE_THETA_BURDEN_MAX."""
    
    def test_pass_below_threshold(self, passing_call_context, default_config):
        """Should pass when theta_pct <= 4%."""
        result = check_theta_burden_max(passing_call_context, default_config)
        assert result.passed is True
        assert result.reason_code == "GATE_PASS_THETA_BURDEN"
    
    def test_fail_above_threshold(self, passing_call_context, default_config):
        """Should fail when theta_pct > 4%."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "theta_pct": 5.5}
        )
        result = check_theta_burden_max(ctx, default_config)
        assert result.passed is False
        assert result.reason_code == "GATE_FAIL_THETA_BURDEN"
    
    def test_calculate_from_theta_and_mid(self, passing_call_context, default_config):
        """Should calculate theta_pct from theta and mid if not provided."""
        ctx = GateContext(
            **{
                **passing_call_context.__dict__,
                "theta_pct": None,
                "theta": -0.10,
                "mid": 5.0,
            }
        )
        result = check_theta_burden_max(ctx, default_config)
        # abs(-0.10) / 5.0 * 100 = 2.0%
        assert result.passed is True
        assert result.measured_value == pytest.approx(2.0, rel=0.01)


# ============================================================================
# GateContext Construction Tests
# ============================================================================


class TestGateContext:
    """Tests for GateContext construction."""
    
    def test_from_evaluation_and_features(
        self, sample_evaluation, sample_feature_set, sample_opportunity
    ):
        """Should build context from evaluation, features, and opportunity."""
        ctx = GateContext.from_evaluation_and_features(
            evaluation=sample_evaluation,
            feature_set=sample_feature_set,
            opportunity=sample_opportunity,
        )
        
        assert ctx.evaluation_id == sample_evaluation.evaluation_id
        assert ctx.underlying_ticker == "AAPL"
        assert ctx.option_type == "CALL"
        assert ctx.open_interest == 500
        assert ctx.iv_percentile == 45.0
        assert "BREAKOUT" in ctx.scanner_triggers
        assert ctx.volume_ratio == 1.8
    
    def test_without_feature_set(self, sample_evaluation, sample_opportunity):
        """Should work without feature set."""
        ctx = GateContext.from_evaluation_and_features(
            evaluation=sample_evaluation,
            feature_set=None,
            opportunity=sample_opportunity,
        )
        
        assert ctx.iv_percentile is None
    
    def test_without_opportunity(self, sample_evaluation, sample_feature_set):
        """Should work without opportunity."""
        ctx = GateContext.from_evaluation_and_features(
            evaluation=sample_evaluation,
            feature_set=sample_feature_set,
            opportunity=None,
        )
        
        assert ctx.scanner_triggers == []
        assert ctx.volume_ratio is None


# ============================================================================
# GateEvaluation Tests
# ============================================================================


class TestGateEvaluation:
    """Tests for GateEvaluation aggregation."""
    
    def test_all_passed_true(self, passing_call_context, default_config):
        """Should report all_passed=True when all gates pass."""
        from app.gates.gates import ALL_GATES
        
        results = [
            gate_func(passing_call_context, default_config)
            for _, gate_func in ALL_GATES
        ]
        gate_eval = GateEvaluation(
            evaluation_id=passing_call_context.evaluation_id,
            gate_results=results,
        )
        
        assert gate_eval.all_passed is True
        assert gate_eval.failed_gates == []
        assert gate_eval.failed_count == 0
    
    def test_all_passed_false(self, passing_call_context, default_config):
        """Should report all_passed=False when any gate fails."""
        # Create a context that fails one gate
        ctx = GateContext(
            **{**passing_call_context.__dict__, "open_interest": 100}
        )
        
        results = [
            gate_func(ctx, default_config)
            for _, gate_func in ALL_GATES
        ]
        gate_eval = GateEvaluation(
            evaluation_id=ctx.evaluation_id,
            gate_results=results,
        )
        
        assert gate_eval.all_passed is False
        assert "GATE_MIN_OPEN_INTEREST" in gate_eval.failed_gates
        assert gate_eval.failed_count >= 1


# ============================================================================
# GateCalculator Integration Tests
# ============================================================================


class TestGateCalculator:
    """Integration tests for GateCalculator."""
    
    def test_evaluate_gates_all_pass(
        self, sample_evaluation, sample_feature_set, sample_opportunity, default_config
    ):
        """Should pass all gates for a good evaluation."""
        calculator = GateCalculator(default_config)
        
        result = calculator.evaluate_gates(
            evaluation=sample_evaluation,
            feature_set=sample_feature_set,
            opportunity=sample_opportunity,
        )
        
        assert result.all_passed is True
        assert len(result.gate_results) == len(ALL_GATES)
    
    def test_evaluate_gates_batch(
        self, sample_evaluation, sample_feature_set, sample_opportunity, default_config
    ):
        """Should handle batch evaluation."""
        calculator = GateCalculator(default_config)
        
        results = calculator.evaluate_gates_batch(
            evaluations=[sample_evaluation],
            feature_sets={sample_evaluation.evaluation_id: sample_feature_set},
            opportunities={sample_evaluation.underlying_ticker: sample_opportunity},
        )
        
        assert len(results) == 1
        assert sample_evaluation.evaluation_id in results
    
    def test_get_failure_summary(self, default_config):
        """Should aggregate failure counts correctly."""
        calculator = GateCalculator(default_config)
        
        # Create evaluations with different failures
        eval1 = Evaluation(
            evaluation_id="test-1",
            opportunity_id="opp-1",
            underlying_ticker="AAPL",
            option_ticker="O:AAPL1",
            option_type=OptionType.CALL,
            expiration_date="2024-03-15",
            dte=30,
            strike=185.0,
            underlying_price=180.0,
            moneyness_pct=2.78,
            bid=4.50,
            ask=4.60,
            mid=4.55,
            spread_abs=0.10,
            spread_pct=2.2,
            iv=0.32,
            delta=0.55,
            gamma=0.02,
            theta=-0.08,
            vega=0.25,
            open_interest=100,  # Below 300 threshold
            volume=100,
            breakeven_price=189.55,
            required_move_pct=5.3,
            expected_move_pct=8.5,
            feasibility_ratio=0.62,
            time_adjusted_feasibility=0.85,
            dte_bucket=DTEBucket.B,
            rank_score=75.5,
            policy_version="v2.0.0",
            policy_hash="abc123",
        )
        
        results = calculator.evaluate_gates_batch(
            evaluations=[eval1],
            feature_sets={},
            opportunities={},
        )
        
        summary = calculator.get_failure_summary(results)
        assert summary.get("GATE_MIN_OPEN_INTEREST", 0) >= 1


# ============================================================================
# Convenience Function Tests
# ============================================================================


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""
    
    def test_evaluate_all_gates(
        self, sample_evaluation, sample_feature_set, sample_opportunity
    ):
        """Should return GateEvaluation."""
        result = evaluate_all_gates(
            evaluation=sample_evaluation,
            feature_set=sample_feature_set,
            opportunity=sample_opportunity,
        )
        
        assert isinstance(result, GateEvaluation)
        assert len(result.gate_results) > 0
    
    def test_check_all_gates_passed(
        self, sample_evaluation, sample_feature_set, sample_opportunity
    ):
        """Should return boolean."""
        result = check_all_gates_passed(
            evaluation=sample_evaluation,
            feature_set=sample_feature_set,
            opportunity=sample_opportunity,
        )
        
        assert isinstance(result, bool)
    
    def test_get_failed_gates(
        self, sample_evaluation, sample_feature_set, sample_opportunity
    ):
        """Should return list of failed gate IDs."""
        result = get_failed_gates(
            evaluation=sample_evaluation,
            feature_set=sample_feature_set,
            opportunity=sample_opportunity,
        )
        
        assert isinstance(result, list)


# ============================================================================
# Edge Case Tests
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""
    
    def test_zero_mid_price(self, passing_call_context, default_config):
        """Should handle zero mid price gracefully."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "mid": 0.0, "theta_pct": None}
        )
        result = check_theta_burden_max(ctx, default_config)
        assert result.passed is True
        assert "Cannot calculate" in result.notes
    
    def test_delta_at_boundary_call(self, passing_call_context, default_config):
        """Should handle delta exactly at 1.0 for CALL."""
        ctx = GateContext(
            **{**passing_call_context.__dict__, "delta": 1.0}
        )
        result = check_greeks_coherence(ctx, default_config)
        assert result.passed is True
    
    def test_delta_at_boundary_put(self, passing_put_context, default_config):
        """Should handle delta exactly at -1.0 for PUT."""
        ctx = GateContext(
            **{**passing_put_context.__dict__, "delta": -1.0}
        )
        result = check_greeks_coherence(ctx, default_config)
        assert result.passed is True
    
    def test_all_gates_registered(self):
        """Should have all 9 gates registered."""
        assert len(ALL_GATES) == 9
        
        expected_gates = [
            "GATE_MIN_OPEN_INTEREST",
            "GATE_MIN_VOLUME",
            "GATE_MAX_SPREAD_PCT",
            "GATE_DTE_RANGE",
            "GATE_MOVE_SUFFICIENCY",
            "GATE_IV_PERCENTILE_MAX",
            "GATE_BREAKOUT_VOLUME",
            "GATE_GREEKS_COHERENCE",
            "GATE_THETA_BURDEN_MAX",
        ]
        
        registered_gates = [gate_id for gate_id, _ in ALL_GATES]
        assert registered_gates == expected_gates
