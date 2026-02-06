"""Tests for EvaluationBuilder."""

import math
import pytest
from datetime import datetime, timezone

from app.core.schemas import (
    DirectionHint,
    DTEBucket,
    Opportunity,
    OptionType,
    ScannerTrigger,
    ScannerType,
)
from app.selection.contract_selector import ContractCandidate
from app.selection.evaluation_builder import EvaluationBuilder
from app.selection.ranking import RankingScores


@pytest.fixture
def sample_call_candidate() -> ContractCandidate:
    """Sample CALL contract candidate."""
    return ContractCandidate(
        option_ticker="AAPL260320C00185000",
        underlying_ticker="AAPL",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=50,
        strike=185.0,
        underlying_price=180.0,
        bid=4.80,
        ask=5.00,
        mid=4.90,
        spread_abs=0.20,
        spread_pct=4.08,
        iv=0.28,  # 28% IV
        delta=0.52,
        gamma=0.025,
        theta=-0.08,
        vega=0.22,
        open_interest=3500,
        volume=450,
        moneyness_pct=2.78,  # (185-180)/180 * 100
        dte_bucket=DTEBucket.B,
        ranking_scores=RankingScores(
            liquidity_score=82.0,
            delta_closeness_score=77.0,
            spread_tightness_score=59.0,
            rank_score=74.3,
        ),
    )


@pytest.fixture
def sample_put_candidate() -> ContractCandidate:
    """Sample PUT contract candidate."""
    return ContractCandidate(
        option_ticker="AAPL260320P00175000",
        underlying_ticker="AAPL",
        option_type=OptionType.PUT,
        expiration_date="2026-03-20",
        dte=50,
        strike=175.0,
        underlying_price=180.0,
        bid=3.20,
        ask=3.40,
        mid=3.30,
        spread_abs=0.20,
        spread_pct=6.06,
        iv=0.30,  # 30% IV
        delta=-0.38,
        gamma=0.022,
        theta=-0.06,
        vega=0.20,
        open_interest=2800,
        volume=320,
        moneyness_pct=2.78,  # (180-175)/180 * 100 - OTM for put
        dte_bucket=DTEBucket.B,
        ranking_scores=RankingScores(
            liquidity_score=78.0,
            delta_closeness_score=77.0,
            spread_tightness_score=39.0,
            rank_score=68.5,
        ),
    )


@pytest.fixture
def sample_opportunity() -> Opportunity:
    """Sample opportunity."""
    return Opportunity(
        underlying_ticker="AAPL",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        scanner_triggers=[
            ScannerTrigger(
                scanner_type=ScannerType.BREAKOUT,
                reason_codes=["BREAKOUT_CLOSE_ABOVE_RANGE"],
                metrics={"prior_N_day_high": 178.0, "today_close": 180.0},
                triggered_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
        direction_hint=DirectionHint.CALL,
        priority_score=75,
    )


class TestEvaluationBuilderStaticMethods:
    """Tests for EvaluationBuilder static methods."""

    def test_compute_breakeven_call(self):
        """Test breakeven calculation for calls."""
        # CALL: breakeven = strike + premium
        breakeven = EvaluationBuilder.compute_breakeven(
            strike=185.0,
            mid=4.90,
            option_type=OptionType.CALL,
        )
        
        assert breakeven == 189.90

    def test_compute_breakeven_put(self):
        """Test breakeven calculation for puts."""
        # PUT: breakeven = strike - premium
        breakeven = EvaluationBuilder.compute_breakeven(
            strike=175.0,
            mid=3.30,
            option_type=OptionType.PUT,
        )
        
        assert breakeven == 171.70

    def test_compute_required_move_pct(self):
        """Test required move percentage calculation."""
        # |breakeven - underlying| / underlying * 100
        required_move = EvaluationBuilder.compute_required_move_pct(
            breakeven_price=189.90,
            underlying_price=180.0,
        )
        
        expected = abs(189.90 - 180.0) / 180.0 * 100  # 5.5%
        assert abs(required_move - expected) < 0.01

    def test_compute_expected_move_pct(self):
        """Test expected move percentage calculation."""
        # iv * sqrt(DTE/365) * 100
        expected_move = EvaluationBuilder.compute_expected_move_pct(
            iv=0.28,
            dte=50,
        )
        
        expected = 0.28 * math.sqrt(50 / 365) * 100
        assert abs(expected_move - expected) < 0.01

    def test_compute_expected_move_pct_zero_dte(self):
        """Test expected move with zero DTE returns small value."""
        expected_move = EvaluationBuilder.compute_expected_move_pct(
            iv=0.28,
            dte=0,
        )
        
        assert expected_move == 0.01

    def test_compute_expected_move_pct_zero_iv(self):
        """Test expected move with zero IV returns small value."""
        expected_move = EvaluationBuilder.compute_expected_move_pct(
            iv=0.0,
            dte=50,
        )
        
        assert expected_move == 0.01

    def test_compute_feasibility_ratio(self):
        """Test feasibility ratio calculation."""
        ratio = EvaluationBuilder.compute_feasibility_ratio(
            required_move_pct=5.5,
            expected_move_pct=10.0,
        )
        
        assert ratio == 0.55

    def test_compute_feasibility_ratio_zero_expected(self):
        """Test feasibility ratio with zero expected move."""
        ratio = EvaluationBuilder.compute_feasibility_ratio(
            required_move_pct=5.5,
            expected_move_pct=0.0,
        )
        
        assert ratio == 999.0

    def test_compute_time_adjusted_feasibility(self):
        """Test time-adjusted feasibility calculation."""
        # required_move_pct / (expected_move_pct * sqrt(DTE/30))
        taf = EvaluationBuilder.compute_time_adjusted_feasibility(
            required_move_pct=5.5,
            expected_move_pct=10.0,
            dte=50,
        )
        
        expected = 5.5 / (10.0 * math.sqrt(50 / 30))
        assert abs(taf - expected) < 0.01

    def test_compute_time_adjusted_feasibility_zero_dte(self):
        """Test time-adjusted feasibility with zero DTE."""
        taf = EvaluationBuilder.compute_time_adjusted_feasibility(
            required_move_pct=5.5,
            expected_move_pct=10.0,
            dte=0,
        )
        
        assert taf == 999.0


class TestEvaluationBuilder:
    """Tests for EvaluationBuilder class."""

    def test_build_evaluation_call(self, sample_call_candidate, sample_opportunity):
        """Test building evaluation for a call option."""
        builder = EvaluationBuilder(policy_version="v2.0.0", policy_hash="abc123")
        
        evaluation = builder.build_evaluation(
            sample_call_candidate,
            sample_opportunity,
        )
        
        # Check basic fields
        assert evaluation.underlying_ticker == "AAPL"
        assert evaluation.option_ticker == "AAPL260320C00185000"
        assert evaluation.option_type == OptionType.CALL
        assert evaluation.strike == 185.0
        assert evaluation.dte == 50
        assert evaluation.policy_version == "v2.0.0"
        assert evaluation.policy_hash == "abc123"
        
        # Check computed fields
        # Breakeven: 185 + 4.90 = 189.90
        assert evaluation.breakeven_price == 189.90
        
        # Required move: |189.90 - 180| / 180 * 100 = 5.5%
        assert abs(evaluation.required_move_pct - 5.5) < 0.1
        
        # Expected move: 0.28 * sqrt(50/365) * 100 ≈ 10.36%
        expected_move = 0.28 * math.sqrt(50 / 365) * 100
        assert abs(evaluation.expected_move_pct - expected_move) < 0.1
        
        # Feasibility ratio
        assert evaluation.feasibility_ratio > 0
        
        # Time-adjusted feasibility
        assert evaluation.time_adjusted_feasibility > 0
        
        # Rank score
        assert evaluation.rank_score == 74.3

    def test_build_evaluation_put(self, sample_put_candidate, sample_opportunity):
        """Test building evaluation for a put option."""
        builder = EvaluationBuilder(policy_version="v2.0.0", policy_hash="abc123")
        
        evaluation = builder.build_evaluation(
            sample_put_candidate,
            sample_opportunity,
        )
        
        # Check basic fields
        assert evaluation.option_type == OptionType.PUT
        assert evaluation.strike == 175.0
        
        # Breakeven: 175 - 3.30 = 171.70
        assert evaluation.breakeven_price == 171.70
        
        # Required move: |171.70 - 180| / 180 * 100 ≈ 4.61%
        expected_required = abs(171.70 - 180.0) / 180.0 * 100
        assert abs(evaluation.required_move_pct - expected_required) < 0.1

    def test_build_evaluations_multiple(self, sample_call_candidate, sample_put_candidate, sample_opportunity):
        """Test building multiple evaluations."""
        builder = EvaluationBuilder(policy_version="v2.0.0", policy_hash="abc123")
        
        candidates = [sample_call_candidate, sample_put_candidate]
        opportunities = [sample_opportunity]
        
        evaluations = builder.build_evaluations(candidates, opportunities)
        
        assert len(evaluations) == 2
        assert evaluations[0].option_type == OptionType.CALL
        assert evaluations[1].option_type == OptionType.PUT

    def test_build_evaluations_missing_opportunity(self, sample_call_candidate):
        """Test building evaluation when opportunity is missing."""
        builder = EvaluationBuilder(policy_version="v2.0.0", policy_hash="abc123")
        
        # Different ticker opportunity
        different_opp = Opportunity(
            underlying_ticker="MSFT",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            scanner_triggers=[],
            direction_hint=DirectionHint.NONE,
            priority_score=50,
        )
        
        candidates = [sample_call_candidate]  # AAPL candidate
        opportunities = [different_opp]  # MSFT opportunity
        
        evaluations = builder.build_evaluations(candidates, opportunities)
        
        # Should skip candidates with no matching opportunity
        assert len(evaluations) == 0


class TestMoveCalculations:
    """Tests for move calculations per Appendix C example."""

    def test_appendix_c_example(self):
        """Test the example calculation from Appendix C.
        
        Inputs:
        - AAPL at $185.00
        - $190 Call, 23 DTE, mid $2.45
        - IV: 32.5%
        
        Expected:
        - breakeven = 190 + 2.45 = $192.45
        - required_move = |192.45 - 185| / 185 = 4.03%
        - expected_move = 0.325 × sqrt(23/365) = 8.16%
        - time_adjusted = 4.03 / (8.16 × sqrt(23/30)) = 0.56
        """
        candidate = ContractCandidate(
            option_ticker="AAPL260215C00190000",
            underlying_ticker="AAPL",
            option_type=OptionType.CALL,
            expiration_date="2026-02-15",
            dte=23,
            strike=190.0,
            underlying_price=185.0,
            bid=2.40,
            ask=2.50,
            mid=2.45,
            spread_abs=0.10,
            spread_pct=4.08,
            iv=0.325,  # 32.5%
            delta=0.40,
            gamma=0.03,
            theta=-0.10,
            vega=0.18,
            open_interest=5000,
            volume=500,
            moneyness_pct=2.70,
            dte_bucket=DTEBucket.B,
        )
        
        opportunity = Opportunity(
            underlying_ticker="AAPL",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            scanner_triggers=[],
            direction_hint=DirectionHint.CALL,
            priority_score=70,
        )
        
        builder = EvaluationBuilder(policy_version="v2.0.0", policy_hash="abc123")
        evaluation = builder.build_evaluation(candidate, opportunity)
        
        # Check calculations match Appendix C
        assert abs(evaluation.breakeven_price - 192.45) < 0.01
        assert abs(evaluation.required_move_pct - 4.03) < 0.1
        assert abs(evaluation.expected_move_pct - 8.16) < 0.1
        assert abs(evaluation.time_adjusted_feasibility - 0.56) < 0.05
