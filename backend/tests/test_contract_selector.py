"""Tests for Stage 3: Contract Selection."""

import pytest
from datetime import datetime, timezone

from app.core.schemas import (
    ContractSelectionConfig,
    DeltaBand,
    DirectionHint,
    DTEBucket,
    DTEBucketRange,
    Opportunity,
    OptionType,
    ScannerTrigger,
    ScannerType,
)
from app.selection.contract_selector import ContractCandidate, ContractSelector
from app.selection.ranking import RankingCalculator, RankingScores


class TestRankingCalculator:
    """Tests for RankingCalculator."""

    def test_liquidity_score_high_values(self):
        """Test liquidity score with high OI and volume."""
        calc = RankingCalculator()
        
        scores = calc.calculate_rank_score(
            open_interest=10000,
            volume=1000,
            delta=0.45,
            spread_pct=2.0,
            is_call=True,
        )
        
        # High OI (10000) -> log10(10000)/log10(10000) * 50 = 50
        # High volume (1000) -> log10(1001)/log10(1000) * 50 = ~50
        assert scores.liquidity_score >= 95  # Should be near max
        
    def test_liquidity_score_low_values(self):
        """Test liquidity score with low OI and volume."""
        calc = RankingCalculator()
        
        scores = calc.calculate_rank_score(
            open_interest=100,
            volume=10,
            delta=0.45,
            spread_pct=2.0,
            is_call=True,
        )
        
        # Low values -> lower scores
        assert scores.liquidity_score < 50

    def test_delta_closeness_at_target(self):
        """Test delta closeness score at target delta."""
        calc = RankingCalculator(target_delta_call=0.45, target_delta_put=-0.45)
        
        # Exactly at target
        scores = calc.calculate_rank_score(
            open_interest=1000,
            volume=100,
            delta=0.45,
            spread_pct=5.0,
            is_call=True,
        )
        
        assert scores.delta_closeness_score == 100.0

    def test_delta_closeness_far_from_target(self):
        """Test delta closeness score far from target."""
        calc = RankingCalculator(target_delta_call=0.45, target_delta_put=-0.45)
        
        # Far from target (0.75 vs 0.45 = 0.30 distance = max distance)
        scores = calc.calculate_rank_score(
            open_interest=1000,
            volume=100,
            delta=0.75,
            spread_pct=5.0,
            is_call=True,
        )
        
        assert scores.delta_closeness_score == 0.0

    def test_delta_closeness_put(self):
        """Test delta closeness score for puts."""
        calc = RankingCalculator(target_delta_call=0.45, target_delta_put=-0.45)
        
        # PUT at target
        scores = calc.calculate_rank_score(
            open_interest=1000,
            volume=100,
            delta=-0.45,
            spread_pct=5.0,
            is_call=False,
        )
        
        assert scores.delta_closeness_score == 100.0

    def test_spread_tightness_tight(self):
        """Test spread tightness score with tight spread."""
        calc = RankingCalculator()
        
        scores = calc.calculate_rank_score(
            open_interest=1000,
            volume=100,
            delta=0.45,
            spread_pct=0.0,  # No spread
            is_call=True,
        )
        
        assert scores.spread_tightness_score == 100.0

    def test_spread_tightness_wide(self):
        """Test spread tightness score with wide spread."""
        calc = RankingCalculator()
        
        scores = calc.calculate_rank_score(
            open_interest=1000,
            volume=100,
            delta=0.45,
            spread_pct=10.0,  # Max spread
            is_call=True,
        )
        
        assert scores.spread_tightness_score == 0.0

    def test_rank_score_weights(self):
        """Test that rank score uses correct weights."""
        calc = RankingCalculator()
        
        scores = calc.calculate_rank_score(
            open_interest=1000,
            volume=100,
            delta=0.45,
            spread_pct=5.0,
            is_call=True,
        )
        
        # Verify weighted calculation
        expected = (
            0.40 * scores.liquidity_score +
            0.35 * scores.delta_closeness_score +
            0.25 * scores.spread_tightness_score
        )
        
        assert abs(scores.rank_score - expected) < 0.01

    def test_rank_score_custom_weights(self):
        """Test that configurable weights are used correctly."""
        # Custom weights: liquidity=0.50, delta=0.30, spread=0.20
        calc = RankingCalculator(
            weight_liquidity=0.50,
            weight_delta=0.30,
            weight_spread=0.20,
        )
        
        scores = calc.calculate_rank_score(
            open_interest=1000,
            volume=100,
            delta=0.45,
            spread_pct=5.0,
            is_call=True,
        )
        
        # Verify weighted calculation with custom weights
        expected = (
            0.50 * scores.liquidity_score +
            0.30 * scores.delta_closeness_score +
            0.20 * scores.spread_tightness_score
        )
        
        assert abs(scores.rank_score - expected) < 0.01


class TestContractCandidate:
    """Tests for ContractCandidate dataclass."""

    def test_rank_score_with_ranking_scores(self):
        """Test rank_score property with ranking scores."""
        candidate = ContractCandidate(
            option_ticker="AAPL240315C00180000",
            underlying_ticker="AAPL",
            option_type=OptionType.CALL,
            expiration_date="2024-03-15",
            dte=45,
            strike=180.0,
            underlying_price=175.0,
            bid=5.00,
            ask=5.20,
            mid=5.10,
            spread_abs=0.20,
            spread_pct=3.92,
            iv=0.25,
            delta=0.45,
            gamma=0.02,
            theta=-0.05,
            vega=0.15,
            open_interest=5000,
            volume=500,
            moneyness_pct=2.86,
            dte_bucket=DTEBucket.B,
            ranking_scores=RankingScores(
                liquidity_score=80.0,
                delta_closeness_score=100.0,
                spread_tightness_score=60.0,
                rank_score=82.0,
            ),
        )
        
        assert candidate.rank_score == 82.0

    def test_rank_score_without_ranking_scores(self):
        """Test rank_score property without ranking scores."""
        candidate = ContractCandidate(
            option_ticker="AAPL240315C00180000",
            underlying_ticker="AAPL",
            option_type=OptionType.CALL,
            expiration_date="2024-03-15",
            dte=45,
            strike=180.0,
            underlying_price=175.0,
            bid=5.00,
            ask=5.20,
            mid=5.10,
            spread_abs=0.20,
            spread_pct=3.92,
            iv=0.25,
            delta=0.45,
            gamma=0.02,
            theta=-0.05,
            vega=0.15,
            open_interest=5000,
            volume=500,
            moneyness_pct=2.86,
            dte_bucket=DTEBucket.B,
        )
        
        assert candidate.rank_score == 0.0


class TestContractSelector:
    """Tests for ContractSelector class."""

    @pytest.fixture
    def default_config(self) -> ContractSelectionConfig:
        """Default selection configuration."""
        return ContractSelectionConfig(
            dte_buckets={
                "A": DTEBucketRange(min_dte=7, max_dte=21),
                "B": DTEBucketRange(min_dte=22, max_dte=45),
                "C": DTEBucketRange(min_dte=46, max_dte=75),
                "D": DTEBucketRange(min_dte=76, max_dte=120),
            },
            delta_bands={
                "CALL": DeltaBand(min_delta=0.20, max_delta=0.75),
                "PUT": DeltaBand(min_delta=-0.75, max_delta=-0.20),
            },
            top_k=3,
            target_delta_call=0.45,
            target_delta_put=-0.45,
            min_open_interest=200,
            min_volume=50,
            max_spread_pct=10.0,
            min_mid_price=0.20,
            rank_weight_liquidity=0.40,
            rank_weight_delta=0.35,
            rank_weight_spread=0.25,
        )

    def test_init(self, default_config):
        """Test initialization."""
        selector = ContractSelector(default_config)
        
        assert selector._config == default_config

    def test_get_bucket_range(self, default_config):
        """Test getting bucket range from config."""
        selector = ContractSelector(default_config)
        
        assert selector._get_bucket_range(DTEBucket.A) == (7, 21)
        assert selector._get_bucket_range(DTEBucket.B) == (22, 45)
        assert selector._get_bucket_range(DTEBucket.C) == (46, 75)
        assert selector._get_bucket_range(DTEBucket.D) == (76, 120)

    def test_filter_delta_band_calls(self, default_config):
        """Test delta band filter for calls."""
        selector = ContractSelector(default_config)
        
        candidates = [
            ContractCandidate(
                option_ticker=f"TEST{i}",
                underlying_ticker="TEST",
                option_type=OptionType.CALL,
                expiration_date="2026-03-15",
                dte=45,
                strike=100.0,
                underlying_price=100.0,
                bid=1.0,
                ask=1.1,
                mid=1.05,
                spread_abs=0.1,
                spread_pct=9.5,
                iv=0.25,
                delta=delta,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=1000,
                volume=100,
                moneyness_pct=0.0,
                dte_bucket=DTEBucket.B,
            )
            for i, delta in enumerate([0.15, 0.20, 0.45, 0.75, 0.80])
        ]
        
        filtered = selector._filter_delta_band(candidates, OptionType.CALL)
        
        # Should keep 0.20, 0.45, 0.75 (within 0.20-0.75)
        assert len(filtered) == 3
        deltas = [c.delta for c in filtered]
        assert 0.15 not in deltas
        assert 0.80 not in deltas

    def test_filter_delta_band_puts(self, default_config):
        """Test delta band filter for puts."""
        selector = ContractSelector(default_config)
        
        candidates = [
            ContractCandidate(
                option_ticker=f"TEST{i}",
                underlying_ticker="TEST",
                option_type=OptionType.PUT,
                expiration_date="2026-03-15",
                dte=45,
                strike=100.0,
                underlying_price=100.0,
                bid=1.0,
                ask=1.1,
                mid=1.05,
                spread_abs=0.1,
                spread_pct=9.5,
                iv=0.25,
                delta=delta,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=1000,
                volume=100,
                moneyness_pct=0.0,
                dte_bucket=DTEBucket.B,
            )
            for i, delta in enumerate([-0.15, -0.20, -0.45, -0.75, -0.80])
        ]
        
        filtered = selector._filter_delta_band(candidates, OptionType.PUT)
        
        # Should keep -0.20, -0.45, -0.75 (within -0.75 to -0.20)
        assert len(filtered) == 3
        deltas = [c.delta for c in filtered]
        assert -0.15 not in deltas
        assert -0.80 not in deltas

    def test_filter_liquidity(self, default_config):
        """Test liquidity baseline filters."""
        selector = ContractSelector(default_config)
        
        candidates = [
            # Pass all filters
            ContractCandidate(
                option_ticker="PASS",
                underlying_ticker="TEST",
                option_type=OptionType.CALL,
                expiration_date="2026-03-15",
                dte=45,
                strike=100.0,
                underlying_price=100.0,
                bid=1.0,
                ask=1.1,
                mid=1.05,
                spread_abs=0.1,
                spread_pct=9.5,  # < 10%
                iv=0.25,
                delta=0.45,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=500,  # > 200
                volume=100,  # > 50
                moneyness_pct=0.0,
                dte_bucket=DTEBucket.B,
            ),
            # Fail OI filter
            ContractCandidate(
                option_ticker="FAIL_OI",
                underlying_ticker="TEST",
                option_type=OptionType.CALL,
                expiration_date="2026-03-15",
                dte=45,
                strike=100.0,
                underlying_price=100.0,
                bid=1.0,
                ask=1.1,
                mid=1.05,
                spread_abs=0.1,
                spread_pct=9.5,
                iv=0.25,
                delta=0.45,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=100,  # < 200
                volume=100,
                moneyness_pct=0.0,
                dte_bucket=DTEBucket.B,
            ),
            # Fail volume filter
            ContractCandidate(
                option_ticker="FAIL_VOL",
                underlying_ticker="TEST",
                option_type=OptionType.CALL,
                expiration_date="2026-03-15",
                dte=45,
                strike=100.0,
                underlying_price=100.0,
                bid=1.0,
                ask=1.1,
                mid=1.05,
                spread_abs=0.1,
                spread_pct=9.5,
                iv=0.25,
                delta=0.45,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=500,
                volume=20,  # < 50
                moneyness_pct=0.0,
                dte_bucket=DTEBucket.B,
            ),
            # Fail spread filter
            ContractCandidate(
                option_ticker="FAIL_SPREAD",
                underlying_ticker="TEST",
                option_type=OptionType.CALL,
                expiration_date="2026-03-15",
                dte=45,
                strike=100.0,
                underlying_price=100.0,
                bid=1.0,
                ask=1.2,
                mid=1.10,
                spread_abs=0.2,
                spread_pct=18.0,  # > 10%
                iv=0.25,
                delta=0.45,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=500,
                volume=100,
                moneyness_pct=0.0,
                dte_bucket=DTEBucket.B,
            ),
        ]
        
        filtered = selector._filter_liquidity(candidates)
        
        assert len(filtered) == 1
        assert filtered[0].option_ticker == "PASS"

    def test_filter_moneyness_call(self, default_config):
        """Test moneyness filter for calls."""
        selector = ContractSelector(default_config)
        
        # CALL moneyness: (strike - underlying) / underlying * 100
        # Range: -5% (ITM) to +15% (OTM)
        candidates = [
            ContractCandidate(
                option_ticker=f"TEST{i}",
                underlying_ticker="TEST",
                option_type=OptionType.CALL,
                expiration_date="2026-03-15",
                dte=45,
                strike=strike,
                underlying_price=100.0,
                bid=1.0,
                ask=1.1,
                mid=1.05,
                spread_abs=0.1,
                spread_pct=9.5,
                iv=0.25,
                delta=0.45,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=500,
                volume=100,
                moneyness_pct=(strike - 100.0) / 100.0 * 100,  # Calculate moneyness
                dte_bucket=DTEBucket.B,
            )
            for i, strike in enumerate([90, 95, 100, 105, 115, 120])
            # Moneyness: -10%, -5%, 0%, +5%, +15%, +20%
        ]
        
        filtered = selector._filter_moneyness(candidates, OptionType.CALL)
        
        # Should keep -5%, 0%, +5%, +15% (within -5% to +15%)
        assert len(filtered) == 4
        moneyness_values = [c.moneyness_pct for c in filtered]
        assert all(-5.0 <= m <= 15.0 for m in moneyness_values)

    def test_filter_moneyness_put(self, default_config):
        """Test moneyness filter for puts."""
        selector = ContractSelector(default_config)
        
        # PUT moneyness: (underlying - strike) / underlying * 100
        # Range: -15% (ITM) to +5% (OTM)
        candidates = [
            ContractCandidate(
                option_ticker=f"TEST{i}",
                underlying_ticker="TEST",
                option_type=OptionType.PUT,
                expiration_date="2026-03-15",
                dte=45,
                strike=strike,
                underlying_price=100.0,
                bid=1.0,
                ask=1.1,
                mid=1.05,
                spread_abs=0.1,
                spread_pct=9.5,
                iv=0.25,
                delta=-0.45,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=500,
                volume=100,
                moneyness_pct=(100.0 - strike) / 100.0 * 100,  # Calculate moneyness for PUT
                dte_bucket=DTEBucket.B,
            )
            for i, strike in enumerate([80, 85, 95, 100, 105, 120])
            # Moneyness: +20%, +15%, +5%, 0%, -5%, -20%
        ]
        
        filtered = selector._filter_moneyness(candidates, OptionType.PUT)
        
        # Should keep +5%, 0%, -5% (within -15% to +5%)
        # But we also get +15% and -20% filtered
        moneyness_values = [c.moneyness_pct for c in filtered]
        assert all(-15.0 <= m <= 5.0 for m in moneyness_values)

    def test_rank_and_select_top_k(self, default_config):
        """Test ranking and top-K selection."""
        selector = ContractSelector(default_config)
        
        candidates = [
            ContractCandidate(
                option_ticker=f"TEST{i}",
                underlying_ticker="TEST",
                option_type=OptionType.CALL,
                expiration_date="2026-03-15",
                dte=45,
                strike=100.0,
                underlying_price=100.0,
                bid=1.0,
                ask=1.1,
                mid=1.05,
                spread_abs=0.1,
                spread_pct=spread,
                iv=0.25,
                delta=delta,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=oi,
                volume=100,
                moneyness_pct=0.0,
                dte_bucket=DTEBucket.B,
            )
            for i, (oi, delta, spread) in enumerate([
                (5000, 0.45, 2.0),  # Best: high OI, target delta, tight spread
                (3000, 0.40, 3.0),  # Good
                (1000, 0.35, 5.0),  # Okay
                (500, 0.30, 7.0),   # Worse
                (200, 0.25, 9.0),   # Worst
            ])
        ]
        
        selected = selector._rank_and_select(candidates, OptionType.CALL)
        
        # Should select top 3 (default K)
        assert len(selected) == 3
        
        # Should be sorted by rank score descending
        assert selected[0].option_ticker == "TEST0"  # Highest score
        assert all(c.ranking_scores is not None for c in selected)

    def test_rank_and_select_fewer_than_k(self, default_config):
        """Test selection when fewer candidates than K."""
        selector = ContractSelector(default_config)
        
        candidates = [
            ContractCandidate(
                option_ticker="TEST0",
                underlying_ticker="TEST",
                option_type=OptionType.CALL,
                expiration_date="2026-03-15",
                dte=45,
                strike=100.0,
                underlying_price=100.0,
                bid=1.0,
                ask=1.1,
                mid=1.05,
                spread_abs=0.1,
                spread_pct=5.0,
                iv=0.25,
                delta=0.45,
                gamma=0.02,
                theta=-0.05,
                vega=0.15,
                open_interest=1000,
                volume=100,
                moneyness_pct=0.0,
                dte_bucket=DTEBucket.B,
            ),
        ]
        
        selected = selector._rank_and_select(candidates, OptionType.CALL)
        
        # Should return all 1 candidate
        assert len(selected) == 1

    def test_rank_and_select_empty(self, default_config):
        """Test selection with empty candidates."""
        selector = ContractSelector(default_config)
        
        selected = selector._rank_and_select([], OptionType.CALL)
        
        assert len(selected) == 0
