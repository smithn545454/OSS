"""Unit tests for paper trading module.

Tests for:
- Exit condition checking
- MFE/MAE calculations
- Shadow selection
- Performance metrics
- Position lifecycle
"""

import pytest
from datetime import datetime, timedelta, timezone

from app.core.schemas import (
    Decision,
    Evaluation,
    ExitReason,
    OptionType,
    PaperPosition,
    PositionStatus,
    QualityTier,
    TrackingConfig,
    Verdict,
)
from app.gates.models import GateEvaluation, GateResult
from app.paper_trading.exit_checker import (
    calculate_dte_from_expiration,
    check_exit_conditions,
    should_exit,
)
from app.paper_trading.metrics import (
    compute_metrics_from_positions,
    analyze_exit_effectiveness,
    compare_tiers,
)
from app.paper_trading.models import (
    PerformanceMetrics,
    ShadowPosition,
    TierPerformance,
    UpdateResult,
)
from app.paper_trading.shadow_tracker import (
    is_near_miss,
    is_single_gate_failure,
    select_shadow_candidates,
    determine_sample_type,
)
from app.paper_trading.position_manager import (
    extract_underlying_from_option_ticker,
    extract_expiration_from_option_ticker,
)


# ============================================================================
# Test Fixtures
# ============================================================================


def create_test_position(
    entry_price: float = 2.50,
    current_price: float = 2.50,
    current_pnl_pct: float = 0.0,
    mfe: float = 0.0,
    mae: float = 0.0,
    days_held: int = 5,
    status: PositionStatus = PositionStatus.OPEN,
    verdict: Verdict = Verdict.APPROVE,
    tier: QualityTier = QualityTier.TIER_2,
    exit_reason: ExitReason = None,
) -> PaperPosition:
    """Create a test paper position."""
    return PaperPosition(
        position_id="test-pos-001",
        evaluation_id="test-eval-001",
        option_ticker="O:AAPL240315C00170000",
        entry_price=entry_price,
        entry_date="2024-01-15",
        quantity=1,
        verdict_at_entry=verdict,
        quality_tier_at_entry=tier,
        exit_price=current_price if status == PositionStatus.CLOSED else None,
        exit_date="2024-01-20" if status == PositionStatus.CLOSED else None,
        exit_reason=exit_reason,
        current_price=current_price,
        current_pnl_pct=current_pnl_pct,
        max_favorable_excursion=mfe,
        max_adverse_excursion=mae,
        days_held=days_held,
        status=status,
    )


def create_test_decision(
    verdict: Verdict = Verdict.REJECT,
    score: float = 60.0,
    reason_code: str = "REJECTED_BY_SCORE",
    tier: QualityTier = None,
) -> Decision:
    """Create a test decision."""
    return Decision(
        evaluation_id="test-eval-001",
        verdict=verdict,
        quality_tier=tier,
        final_score=score,
        premium_leverage_score=60.0,
        underlying_behavior_score=60.0,
        setup_quality_score=60.0,
        primary_reason_code=reason_code,
        supporting_reason_codes=[],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v2.0.0",
    )


def create_test_evaluation() -> Evaluation:
    """Create a test evaluation."""
    return Evaluation(
        evaluation_id="test-eval-001",
        opportunity_id="test-opp-001",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL240315C00170000",
        option_type=OptionType.CALL,
        expiration_date="2024-03-15",
        dte=45,
        strike=170.0,
        underlying_price=175.0,
        moneyness_pct=-2.86,
        bid=2.40,
        ask=2.60,
        mid=2.50,
        spread_abs=0.20,
        spread_pct=8.0,
        iv=0.30,
        delta=0.45,
        gamma=0.05,
        theta=-0.03,
        vega=0.15,
        open_interest=500,
        volume=100,
        breakeven_price=172.50,
        required_move_pct=1.43,
        expected_move_pct=5.0,
        feasibility_ratio=0.29,
        time_adjusted_feasibility=0.35,
        policy_version="v2.0.0",
    )


# ============================================================================
# Exit Checker Tests
# ============================================================================


class TestExitConditions:
    """Test exit condition checking."""
    
    def test_profit_target_triggered(self):
        """Test profit target exit (Priority 1)."""
        position = create_test_position(entry_price=2.00)
        config = TrackingConfig(profit_target_pct=50.0)
        
        # 60% gain should trigger profit target
        current_price = 3.20
        result = check_exit_conditions(position, current_price, 30, config)
        
        assert result == ExitReason.PROFIT_TARGET
    
    def test_stop_loss_triggered(self):
        """Test stop loss exit (Priority 2)."""
        position = create_test_position(entry_price=2.00)
        config = TrackingConfig(stop_loss_pct=50.0)
        
        # 55% loss should trigger stop loss
        current_price = 0.90
        result = check_exit_conditions(position, current_price, 30, config)
        
        assert result == ExitReason.STOP_LOSS
    
    def test_time_exit_triggered(self):
        """Test time exit (Priority 3)."""
        position = create_test_position(entry_price=2.00)
        config = TrackingConfig(time_exit_dte=5)
        
        # 4 DTE should trigger time exit (no P&L exit)
        current_price = 2.20  # 10% gain, not enough for profit target
        result = check_exit_conditions(position, current_price, 4, config)
        
        assert result == ExitReason.TIME_EXIT
    
    def test_expiration_triggered(self):
        """Test expiration exit (Priority 4)."""
        position = create_test_position(entry_price=2.00)
        config = TrackingConfig(time_exit_dte=5)
        
        # 0 DTE should trigger expiration
        current_price = 2.20
        result = check_exit_conditions(position, current_price, 0, config)
        
        assert result == ExitReason.EXPIRATION
    
    def test_no_exit_triggered(self):
        """Test when no exit condition is met."""
        position = create_test_position(entry_price=2.00)
        config = TrackingConfig()
        
        # Normal price, plenty of DTE
        current_price = 2.20  # 10% gain
        result = check_exit_conditions(position, current_price, 30, config)
        
        assert result is None
    
    def test_profit_target_priority_over_time_exit(self):
        """Test that profit target takes priority over time exit."""
        position = create_test_position(entry_price=2.00)
        config = TrackingConfig(profit_target_pct=50.0, time_exit_dte=5)
        
        # Both profit target and time exit would trigger
        current_price = 3.50  # 75% gain
        result = check_exit_conditions(position, current_price, 3, config)
        
        # Profit target has priority
        assert result == ExitReason.PROFIT_TARGET
    
    def test_stop_loss_priority_over_time_exit(self):
        """Test that stop loss takes priority over time exit."""
        position = create_test_position(entry_price=2.00)
        config = TrackingConfig(stop_loss_pct=50.0, time_exit_dte=5)
        
        # Both stop loss and time exit would trigger
        current_price = 0.80  # 60% loss
        result = check_exit_conditions(position, current_price, 3, config)
        
        # Stop loss has priority
        assert result == ExitReason.STOP_LOSS


class TestDTECalculation:
    """Test DTE calculation from expiration dates."""
    
    def test_calculate_dte_future_date(self):
        """Test DTE for a future expiration date."""
        # Use a date far in the future
        future_date = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        dte = calculate_dte_from_expiration(future_date)
        
        assert 29 <= dte <= 31
    
    def test_calculate_dte_past_date(self):
        """Test DTE for an expired contract (negative DTE)."""
        past_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        dte = calculate_dte_from_expiration(past_date)
        
        assert dte < 0
    
    def test_calculate_dte_invalid_date(self):
        """Test DTE with invalid date format returns large number."""
        dte = calculate_dte_from_expiration("invalid-date")
        
        assert dte == 999


# ============================================================================
# Metrics Tests
# ============================================================================


class TestPerformanceMetrics:
    """Test performance metrics calculations."""
    
    def test_win_rate_calculation(self):
        """Test win rate calculation."""
        positions = [
            create_test_position(current_pnl_pct=25.0, status=PositionStatus.CLOSED, exit_reason=ExitReason.PROFIT_TARGET),
            create_test_position(current_pnl_pct=30.0, status=PositionStatus.CLOSED, exit_reason=ExitReason.PROFIT_TARGET),
            create_test_position(current_pnl_pct=-20.0, status=PositionStatus.CLOSED, exit_reason=ExitReason.STOP_LOSS),
        ]
        
        # Modify position IDs to be unique
        for i, pos in enumerate(positions):
            pos.position_id = f"pos-{i}"
        
        metrics = compute_metrics_from_positions(positions)
        
        # 2 wins out of 3 = 66.67% win rate
        assert metrics.win_count == 2
        assert metrics.loss_count == 1
        assert 66 < metrics.win_rate < 67
    
    def test_expectancy_calculation(self):
        """Test expectancy calculation."""
        metrics = PerformanceMetrics(
            closed_positions=10,
            win_count=6,
            loss_count=4,
            avg_win_pct=30.0,
            avg_loss_pct=-20.0,
        )
        
        # Expectancy = (0.6 * 30) - (0.4 * 20) = 18 - 8 = 10
        assert metrics.expectancy == 10.0
    
    def test_tier_performance(self):
        """Test performance breakdown by tier."""
        positions = [
            create_test_position(current_pnl_pct=50.0, status=PositionStatus.CLOSED, tier=QualityTier.TIER_1, exit_reason=ExitReason.PROFIT_TARGET),
            create_test_position(current_pnl_pct=30.0, status=PositionStatus.CLOSED, tier=QualityTier.TIER_2, exit_reason=ExitReason.PROFIT_TARGET),
            create_test_position(current_pnl_pct=-30.0, status=PositionStatus.CLOSED, tier=QualityTier.TIER_3, exit_reason=ExitReason.STOP_LOSS),
        ]
        
        for i, pos in enumerate(positions):
            pos.position_id = f"pos-{i}"
        
        metrics = compute_metrics_from_positions(positions)
        
        assert "TIER_1" in metrics.tier_performance
        assert metrics.tier_performance["TIER_1"].win_rate == 100.0
    
    def test_empty_positions(self):
        """Test metrics with no positions."""
        metrics = compute_metrics_from_positions([])
        
        assert metrics.total_positions == 0
        assert metrics.win_rate == 0.0
        assert metrics.expectancy == 0.0


class TestMFEMAE:
    """Test MFE/MAE tracking."""
    
    def test_mfe_calculation(self):
        """Test MFE (max favorable excursion) tracking."""
        positions = [
            create_test_position(mfe=30.0, current_pnl_pct=20.0, status=PositionStatus.CLOSED, exit_reason=ExitReason.PROFIT_TARGET),
            create_test_position(mfe=25.0, current_pnl_pct=15.0, status=PositionStatus.CLOSED, exit_reason=ExitReason.TIME_EXIT),
        ]
        
        for i, pos in enumerate(positions):
            pos.position_id = f"pos-{i}"
        
        metrics = compute_metrics_from_positions(positions)
        
        # Average MFE should be (30 + 25) / 2 = 27.5
        assert metrics.avg_mfe == 27.5
        assert metrics.max_mfe == 30.0
    
    def test_mae_calculation(self):
        """Test MAE (max adverse excursion) tracking."""
        positions = [
            create_test_position(mae=-15.0, current_pnl_pct=10.0, status=PositionStatus.CLOSED, exit_reason=ExitReason.PROFIT_TARGET),
            create_test_position(mae=-25.0, current_pnl_pct=-20.0, status=PositionStatus.CLOSED, exit_reason=ExitReason.STOP_LOSS),
        ]
        
        for i, pos in enumerate(positions):
            pos.position_id = f"pos-{i}"
        
        metrics = compute_metrics_from_positions(positions)
        
        # Average MAE should be (-15 + -25) / 2 = -20
        assert metrics.avg_mae == -20.0
        # Max MAE is most negative
        assert metrics.max_mae == -25.0


# ============================================================================
# Shadow Tracker Tests
# ============================================================================


class TestShadowSelection:
    """Test shadow candidate selection."""
    
    def test_near_miss_detection(self):
        """Test near-miss REJECT detection (score 60-65)."""
        decision = create_test_decision(
            verdict=Verdict.REJECT,
            score=62.0,
            reason_code="REJECTED_BY_SCORE",
        )
        config = TrackingConfig(shadow_near_miss_threshold=60)
        
        assert is_near_miss(decision, config) is True
    
    def test_near_miss_rejected_by_gates(self):
        """Test that gate rejections are not near-miss."""
        decision = create_test_decision(
            verdict=Verdict.REJECT,
            score=62.0,
            reason_code="REJECTED_BY_GATES",
        )
        
        assert is_near_miss(decision) is False
    
    def test_near_miss_score_too_low(self):
        """Test that low scores are not near-miss."""
        decision = create_test_decision(
            verdict=Verdict.REJECT,
            score=45.0,
            reason_code="REJECTED_BY_SCORE",
        )
        
        assert is_near_miss(decision) is False
    
    def test_single_gate_failure_detection(self):
        """Test single gate failure detection."""
        gate_eval = GateEvaluation(
            evaluation_id="test-001",
            gate_results=[
                GateResult(
                    evaluation_id="test-001",
                    gate_id="GATE_MIN_OI",
                    enabled=True,
                    passed=False,
                    measured_value=200,
                    threshold_value=300,
                    operator="gte",
                    units="contracts",
                    reason_code="GATE_FAIL_MIN_OI",
                ),
            ],
        )
        
        assert is_single_gate_failure(gate_eval) is True
    
    def test_multiple_gate_failures(self):
        """Test that multiple failures are not single-gate."""
        gate_eval = GateEvaluation(
            evaluation_id="test-001",
            gate_results=[
                GateResult(
                    evaluation_id="test-001",
                    gate_id="GATE_MIN_OI",
                    enabled=True,
                    passed=False,
                    measured_value=200,
                    threshold_value=300,
                    operator="gte",
                    units="contracts",
                    reason_code="GATE_FAIL_MIN_OI",
                ),
                GateResult(
                    evaluation_id="test-001",
                    gate_id="GATE_MIN_VOLUME",
                    enabled=True,
                    passed=False,
                    measured_value=50,
                    threshold_value=75,
                    operator="gte",
                    units="contracts",
                    reason_code="GATE_FAIL_MIN_VOLUME",
                ),
            ],
        )
        
        assert is_single_gate_failure(gate_eval) is False
    
    def test_sample_type_determination(self):
        """Test sample type determination."""
        # Near miss
        decision = create_test_decision(score=62.0, reason_code="REJECTED_BY_SCORE")
        assert determine_sample_type(decision, None) == "NEAR_MISS"
        
        # Single gate
        decision2 = create_test_decision(score=55.0, reason_code="REJECTED_BY_GATES")
        gate_eval = GateEvaluation(
            evaluation_id="test-001",
            gate_results=[
                GateResult(
                    evaluation_id="test-001",
                    gate_id="GATE_MIN_OI",
                    enabled=True,
                    passed=False,
                    measured_value=200,
                    threshold_value=300,
                    operator="gte",
                    units="contracts",
                    reason_code="GATE_FAIL_MIN_OI",
                ),
            ],
        )
        assert determine_sample_type(decision2, gate_eval) == "SINGLE_GATE"


# ============================================================================
# Shadow Position Tests
# ============================================================================


class TestShadowPosition:
    """Test shadow position functionality."""
    
    def test_shadow_position_update(self):
        """Test shadow position price update."""
        shadow = ShadowPosition(
            evaluation_id="test-001",
            option_ticker="O:AAPL240315C00170000",
            entry_price=2.50,
            entry_date="2024-01-15",
            rejection_reason="REJECTED_BY_SCORE",
            final_score=62.0,
        )
        
        # Update with positive price
        shadow.update(3.00)
        
        assert shadow.current_price == 3.00
        assert shadow.current_pnl_pct == 20.0
        assert shadow.max_favorable_excursion == 20.0
        assert shadow.peak_pnl_pct == 20.0
        assert shadow.days_tracked == 1
    
    def test_shadow_false_negative_detection(self):
        """Test false negative detection."""
        shadow = ShadowPosition(
            evaluation_id="test-001",
            option_ticker="O:AAPL240315C00170000",
            entry_price=2.00,
            entry_date="2024-01-15",
            rejection_reason="REJECTED_BY_SCORE",
            final_score=62.0,
            would_have_hit_target=True,
        )
        
        assert shadow.is_false_negative is True
    
    def test_shadow_not_false_negative(self):
        """Test non-false negative."""
        shadow = ShadowPosition(
            evaluation_id="test-001",
            option_ticker="O:AAPL240315C00170000",
            entry_price=2.00,
            entry_date="2024-01-15",
            rejection_reason="REJECTED_BY_SCORE",
            final_score=62.0,
            peak_pnl_pct=15.0,  # Not high enough
        )
        
        assert shadow.is_false_negative is False


# ============================================================================
# Position Manager Tests
# ============================================================================


class TestOptionTickerParsing:
    """Test option ticker parsing utilities."""
    
    def test_extract_underlying_standard(self):
        """Test extracting underlying from standard format."""
        ticker = "O:AAPL240315C00170000"
        underlying = extract_underlying_from_option_ticker(ticker)
        
        assert underlying == "AAPL"
    
    def test_extract_underlying_long_symbol(self):
        """Test extracting underlying with longer symbol."""
        ticker = "O:GOOGL240315C01500000"
        underlying = extract_underlying_from_option_ticker(ticker)
        
        assert underlying == "GOOGL"
    
    def test_extract_underlying_short_symbol(self):
        """Test extracting underlying with short symbol."""
        ticker = "O:V240315P00250000"
        underlying = extract_underlying_from_option_ticker(ticker)
        
        assert underlying == "V"
    
    def test_extract_expiration_date(self):
        """Test extracting expiration date from option ticker."""
        ticker = "O:AAPL240315C00170000"
        expiration = extract_expiration_from_option_ticker(ticker)
        
        assert expiration == "2024-03-15"
    
    def test_extract_expiration_put(self):
        """Test extracting expiration from put option."""
        ticker = "O:TSLA241220P00200000"
        expiration = extract_expiration_from_option_ticker(ticker)
        
        assert expiration == "2024-12-20"


# ============================================================================
# Update Result Tests
# ============================================================================


class TestUpdateResult:
    """Test UpdateResult dataclass."""
    
    def test_price_change_calculation(self):
        """Test price change percentage calculation."""
        result = UpdateResult(
            position_id="test-001",
            option_ticker="O:AAPL240315C00170000",
            previous_price=2.00,
            current_price=2.40,
            previous_pnl_pct=0.0,
            current_pnl_pct=20.0,
            mfe_updated=True,
            mae_updated=False,
            exit_triggered=False,
        )
        
        # Use pytest.approx for floating point comparison
        assert result.price_change_pct == pytest.approx(20.0, rel=1e-5)
    
    def test_exit_result(self):
        """Test update result with exit triggered."""
        result = UpdateResult(
            position_id="test-001",
            option_ticker="O:AAPL240315C00170000",
            previous_price=2.00,
            current_price=3.20,
            previous_pnl_pct=40.0,
            current_pnl_pct=60.0,
            mfe_updated=True,
            mae_updated=False,
            exit_triggered=True,
            exit_reason=ExitReason.PROFIT_TARGET,
        )
        
        assert result.exit_triggered is True
        assert result.exit_reason == ExitReason.PROFIT_TARGET


# ============================================================================
# Tier Performance Tests
# ============================================================================


class TestTierPerformance:
    """Test TierPerformance dataclass."""
    
    def test_tier_win_rate(self):
        """Test tier win rate calculation."""
        tier = TierPerformance(
            tier="TIER_1",
            total_positions=10,
            closed_positions=8,
            win_count=6,
            loss_count=2,
        )
        
        # 6/8 = 75%
        assert tier.win_rate == 75.0
    
    def test_tier_avg_return(self):
        """Test tier average return calculation."""
        tier = TierPerformance(
            tier="TIER_2",
            closed_positions=5,
            total_return_pct=125.0,
        )
        
        # 125/5 = 25%
        assert tier.avg_return_pct == 25.0
    
    def test_tier_no_closed_positions(self):
        """Test tier metrics with no closed positions."""
        tier = TierPerformance(
            tier="TIER_3",
            total_positions=3,
            closed_positions=0,
        )
        
        assert tier.win_rate == 0.0
        assert tier.avg_return_pct == 0.0
