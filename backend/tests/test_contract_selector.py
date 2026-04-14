"""Tests for Stage 3: Contract Selection."""

import pytest
from datetime import datetime, timezone
from typing import Optional

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


def _make_candidate(
    delta: float = 0.45,
    spread_pct: float = 5.0,
    open_interest: int = 1000,
    volume: int = 100,
    moneyness_pct: float = 0.0,
    strike: float = 100.0,
    underlying_price: float = 100.0,
    option_type: OptionType = OptionType.CALL,
    dte_bucket: DTEBucket = DTEBucket.B,
    option_ticker: Optional[str] = None,
    mid: float = 1.05,
) -> ContractCandidate:
    """Helper to build a ContractCandidate with sensible defaults."""
    ticker = option_ticker or f"TEST_d{delta}_s{spread_pct}"
    return ContractCandidate(
        option_ticker=ticker,
        underlying_ticker="TEST",
        option_type=option_type,
        expiration_date="2026-03-15",
        dte=45,
        strike=strike,
        underlying_price=underlying_price,
        bid=mid - 0.05,
        ask=mid + 0.05,
        mid=mid,
        spread_abs=0.1,
        spread_pct=spread_pct,
        iv=0.25,
        delta=delta,
        gamma=0.02,
        theta=-0.05,
        vega=0.15,
        open_interest=open_interest,
        volume=volume,
        moneyness_pct=moneyness_pct,
        dte_bucket=dte_bucket,
    )


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
        """Test that rank score uses correct default weights (0.60/0.00/0.40)."""
        calc = RankingCalculator(
            weight_liquidity=0.60,
            weight_delta=0.00,
            weight_spread=0.40,
        )

        scores = calc.calculate_rank_score(
            open_interest=1000,
            volume=100,
            delta=0.45,
            spread_pct=5.0,
            is_call=True,
        )

        # Delta closeness contributes 0 with weight=0
        expected = (
            0.60 * scores.liquidity_score
            + 0.00 * scores.delta_closeness_score
            + 0.40 * scores.spread_tightness_score
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
            0.50 * scores.liquidity_score
            + 0.30 * scores.delta_closeness_score
            + 0.20 * scores.spread_tightness_score
        )

        assert abs(scores.rank_score - expected) < 0.01

    def test_zero_delta_weight_makes_delta_irrelevant(self):
        """With weight_delta=0, two contracts with different deltas get same rank."""
        calc = RankingCalculator(
            weight_liquidity=0.60,
            weight_delta=0.00,
            weight_spread=0.40,
        )

        scores_atm = calc.calculate_rank_score(
            open_interest=1000, volume=100, delta=0.45,
            spread_pct=5.0, is_call=True,
        )
        scores_otm = calc.calculate_rank_score(
            open_interest=1000, volume=100, delta=0.10,
            spread_pct=5.0, is_call=True,
        )

        # Same liquidity and spread → same rank_score
        assert abs(scores_atm.rank_score - scores_otm.rank_score) < 0.01


class TestContractCandidate:
    """Tests for ContractCandidate dataclass."""

    def test_rank_score_with_ranking_scores(self):
        """Test rank_score property with ranking scores."""
        candidate = _make_candidate(delta=0.45)
        candidate.ranking_scores = RankingScores(
            liquidity_score=80.0,
            delta_closeness_score=100.0,
            spread_tightness_score=60.0,
            rank_score=82.0,
        )

        assert candidate.rank_score == 82.0

    def test_rank_score_without_ranking_scores(self):
        """Test rank_score property without ranking scores."""
        candidate = _make_candidate(delta=0.45)
        assert candidate.rank_score == 0.0


class TestContractSelector:
    """Tests for ContractSelector class."""

    @pytest.fixture
    def default_config(self) -> ContractSelectionConfig:
        """Default selection configuration (uses new defaults)."""
        return ContractSelectionConfig()

    @pytest.fixture
    def legacy_config(self) -> ContractSelectionConfig:
        """Legacy config with old delta bands and weights for backward compat tests."""
        return ContractSelectionConfig(
            delta_bands={
                "CALL": DeltaBand(min_delta=0.20, max_delta=0.75),
                "PUT": DeltaBand(min_delta=-0.75, max_delta=-0.20),
            },
            rank_weight_liquidity=0.40,
            rank_weight_delta=0.35,
            rank_weight_spread=0.25,
            diversity_mode="none",
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

    # --- Delta Band Filter Tests ---

    def test_filter_delta_band_calls_new_defaults(self, default_config):
        """Test delta band filter for calls with new 0.05-0.75 band."""
        selector = ContractSelector(default_config)

        candidates = [
            _make_candidate(delta=d, option_ticker=f"TEST{i}")
            for i, d in enumerate([0.03, 0.05, 0.10, 0.20, 0.45, 0.75, 0.80])
        ]

        filtered = selector._filter_delta_band(candidates, OptionType.CALL)

        # Should keep 0.05, 0.10, 0.20, 0.45, 0.75 (within 0.05-0.75)
        assert len(filtered) == 5
        deltas = [c.delta for c in filtered]
        assert 0.03 not in deltas  # Too low
        assert 0.80 not in deltas  # Too high
        assert 0.05 in deltas      # New lower bound passes
        assert 0.10 in deltas      # Previously filtered, now passes

    def test_filter_delta_band_puts_new_defaults(self, default_config):
        """Test delta band filter for puts with new -0.75 to -0.05 band."""
        selector = ContractSelector(default_config)

        candidates = [
            _make_candidate(
                delta=d, option_type=OptionType.PUT, option_ticker=f"TEST{i}"
            )
            for i, d in enumerate([-0.03, -0.05, -0.10, -0.20, -0.45, -0.75, -0.80])
        ]

        filtered = selector._filter_delta_band(candidates, OptionType.PUT)

        assert len(filtered) == 5
        deltas = [c.delta for c in filtered]
        assert -0.03 not in deltas
        assert -0.80 not in deltas
        assert -0.05 in deltas
        assert -0.10 in deltas

    def test_filter_delta_band_calls_legacy(self, legacy_config):
        """Test delta band filter with legacy 0.20-0.75 config."""
        selector = ContractSelector(legacy_config)

        candidates = [
            _make_candidate(delta=d, option_ticker=f"TEST{i}")
            for i, d in enumerate([0.15, 0.20, 0.45, 0.75, 0.80])
        ]

        filtered = selector._filter_delta_band(candidates, OptionType.CALL)

        # Should keep 0.20, 0.45, 0.75
        assert len(filtered) == 3
        deltas = [c.delta for c in filtered]
        assert 0.15 not in deltas
        assert 0.80 not in deltas

    # --- Liquidity Filter Tests ---

    def test_filter_liquidity(self, default_config):
        """Test liquidity baseline filters."""
        selector = ContractSelector(default_config)

        candidates = [
            _make_candidate(
                open_interest=500, volume=100, spread_pct=9.5,
                option_ticker="PASS",
            ),
            _make_candidate(
                open_interest=100, volume=100, spread_pct=9.5,
                option_ticker="FAIL_OI",
            ),
            _make_candidate(
                open_interest=500, volume=20, spread_pct=9.5,
                option_ticker="FAIL_VOL",
            ),
            _make_candidate(
                open_interest=500, volume=100, spread_pct=18.0,
                option_ticker="FAIL_SPREAD",
            ),
        ]

        filtered = selector._filter_liquidity(candidates)

        assert len(filtered) == 1
        assert filtered[0].option_ticker == "PASS"

    # --- Moneyness Filter Tests ---

    def test_filter_moneyness_call(self, default_config):
        """Test moneyness filter for calls (-5% to +15%)."""
        selector = ContractSelector(default_config)

        candidates = [
            _make_candidate(
                strike=strike, moneyness_pct=(strike - 100.0) / 100.0 * 100,
                option_ticker=f"TEST{i}",
            )
            for i, strike in enumerate([90, 95, 100, 105, 115, 120])
            # Moneyness: -10%, -5%, 0%, +5%, +15%, +20%
        ]

        filtered = selector._filter_moneyness(candidates, OptionType.CALL)

        # Should keep -5%, 0%, +5%, +15% (within -5% to +15%)
        assert len(filtered) == 4
        moneyness_values = [c.moneyness_pct for c in filtered]
        assert all(-5.0 <= m <= 15.0 for m in moneyness_values)

    def test_filter_moneyness_put_new_defaults(self, default_config):
        """Test moneyness filter for puts with new symmetric -15% to +15% range."""
        selector = ContractSelector(default_config)

        candidates = [
            _make_candidate(
                strike=strike,
                moneyness_pct=(100.0 - strike) / 100.0 * 100,
                option_type=OptionType.PUT,
                delta=-0.45,
                option_ticker=f"TEST{i}",
            )
            for i, strike in enumerate([80, 85, 95, 100, 105, 120])
            # Moneyness: +20%, +15%, +5%, 0%, -5%, -20%
        ]

        filtered = selector._filter_moneyness(candidates, OptionType.PUT)

        moneyness_values = [c.moneyness_pct for c in filtered]
        # With new range -15% to +15%, should keep +15%, +5%, 0%, -5%
        assert all(-15.0 <= m <= 15.0 for m in moneyness_values)
        # +20% and -20% should be filtered out
        assert 20.0 not in moneyness_values
        assert -20.0 not in moneyness_values

    def test_filter_moneyness_put_legacy(self):
        """Test moneyness filter for puts with old 5% OTM cap."""
        config = ContractSelectionConfig(
            moneyness_put_min=-15.0,
            moneyness_put_max=5.0,  # Old default
        )
        selector = ContractSelector(config)

        candidates = [
            _make_candidate(
                strike=strike,
                moneyness_pct=(100.0 - strike) / 100.0 * 100,
                option_type=OptionType.PUT,
                delta=-0.45,
                option_ticker=f"TEST{i}",
            )
            for i, strike in enumerate([80, 85, 95, 100, 105])
            # Moneyness: +20%, +15%, +5%, 0%, -5%
        ]

        filtered = selector._filter_moneyness(candidates, OptionType.PUT)

        moneyness_values = [c.moneyness_pct for c in filtered]
        assert all(-15.0 <= m <= 5.0 for m in moneyness_values)

    # --- Rank and Select Tests ---

    def test_rank_and_select_top_k(self, default_config):
        """Test ranking and top-K selection."""
        selector = ContractSelector(default_config)

        candidates = [
            _make_candidate(
                open_interest=oi, delta=delta, spread_pct=spread,
                option_ticker=f"TEST{i}",
            )
            for i, (oi, delta, spread) in enumerate([
                (5000, 0.45, 2.0),  # Best: high OI, tight spread
                (3000, 0.40, 3.0),
                (1000, 0.35, 5.0),
                (500, 0.30, 7.0),
                (200, 0.25, 9.0),
            ])
        ]

        selected = selector._rank_and_select(candidates, OptionType.CALL)

        # Should select top 3 (default K)
        assert len(selected) == 3

        # Should be sorted by rank score descending
        assert all(c.ranking_scores is not None for c in selected)
        assert selected[0].rank_score >= selected[1].rank_score

    def test_rank_and_select_fewer_than_k(self, default_config):
        """Test selection when fewer candidates than K."""
        selector = ContractSelector(default_config)

        candidates = [_make_candidate(option_ticker="TEST0")]

        selected = selector._rank_and_select(candidates, OptionType.CALL)
        assert len(selected) == 1

    def test_rank_and_select_empty(self, default_config):
        """Test selection with empty candidates."""
        selector = ContractSelector(default_config)
        selected = selector._rank_and_select([], OptionType.CALL)
        assert len(selected) == 0

    # --- Diversity Tests ---

    def test_diversity_reserves_otm_slot(self):
        """With diversity_mode=delta_spread and reserved_slots=1,
        one OTM contract gets selected even when ATM contracts rank higher."""
        config = ContractSelectionConfig(
            diversity_mode="delta_spread",
            diversity_reserved_slots=1,
            diversity_delta_threshold_call=0.30,
            rank_weight_liquidity=0.60,
            rank_weight_delta=0.00,
            rank_weight_spread=0.40,
            top_k=3,
        )
        selector = ContractSelector(config)

        # ATM contracts with great liquidity and spreads
        candidates = [
            _make_candidate(
                delta=0.50, open_interest=8000, spread_pct=1.0,
                option_ticker="ATM1",
            ),
            _make_candidate(
                delta=0.45, open_interest=6000, spread_pct=1.5,
                option_ticker="ATM2",
            ),
            _make_candidate(
                delta=0.40, open_interest=5000, spread_pct=2.0,
                option_ticker="ATM3",
            ),
            # OTM contract with worse liquidity but valid
            _make_candidate(
                delta=0.15, open_interest=500, spread_pct=5.0,
                option_ticker="OTM1",
            ),
            _make_candidate(
                delta=0.10, open_interest=300, spread_pct=6.0,
                option_ticker="OTM2",
            ),
        ]

        selected = selector._rank_and_select(candidates, OptionType.CALL)

        assert len(selected) == 3
        tickers = [c.option_ticker for c in selected]

        # At least one OTM contract should be selected
        otm_selected = [c for c in selected if abs(c.delta) < 0.30]
        assert len(otm_selected) >= 1, f"No OTM contracts in selection: {tickers}"

        # The best OTM contract (OTM1) should be the diversity pick
        assert "OTM1" in tickers

    def test_diversity_graceful_fallback_no_otm(self):
        """When no OTM candidates exist, diversity falls back to ranked order."""
        config = ContractSelectionConfig(
            diversity_mode="delta_spread",
            diversity_reserved_slots=1,
            diversity_delta_threshold_call=0.30,
            rank_weight_liquidity=0.60,
            rank_weight_delta=0.00,
            rank_weight_spread=0.40,
            top_k=3,
        )
        selector = ContractSelector(config)

        # Only ATM contracts — no OTM available
        candidates = [
            _make_candidate(
                delta=0.50, open_interest=8000, spread_pct=1.0,
                option_ticker="ATM1",
            ),
            _make_candidate(
                delta=0.45, open_interest=6000, spread_pct=1.5,
                option_ticker="ATM2",
            ),
            _make_candidate(
                delta=0.40, open_interest=5000, spread_pct=2.0,
                option_ticker="ATM3",
            ),
            _make_candidate(
                delta=0.35, open_interest=4000, spread_pct=2.5,
                option_ticker="ATM4",
            ),
        ]

        selected = selector._rank_and_select(candidates, OptionType.CALL)

        assert len(selected) == 3
        # Should just pick top 3 by rank (graceful degradation)
        tickers = [c.option_ticker for c in selected]
        assert "ATM1" in tickers
        assert "ATM2" in tickers

    def test_diversity_mode_none_preserves_original(self):
        """With diversity_mode=none, original top-K behavior is preserved."""
        config = ContractSelectionConfig(
            diversity_mode="none",
            diversity_reserved_slots=1,
            rank_weight_liquidity=0.60,
            rank_weight_delta=0.00,
            rank_weight_spread=0.40,
            top_k=3,
        )
        selector = ContractSelector(config)

        # ATM contracts dominate, OTM exists but won't get diversity slot
        candidates = [
            _make_candidate(
                delta=0.50, open_interest=8000, spread_pct=1.0,
                option_ticker="ATM1",
            ),
            _make_candidate(
                delta=0.45, open_interest=6000, spread_pct=1.5,
                option_ticker="ATM2",
            ),
            _make_candidate(
                delta=0.40, open_interest=5000, spread_pct=2.0,
                option_ticker="ATM3",
            ),
            _make_candidate(
                delta=0.10, open_interest=300, spread_pct=6.0,
                option_ticker="OTM1",
            ),
        ]

        selected = selector._rank_and_select(candidates, OptionType.CALL)

        assert len(selected) == 3
        tickers = [c.option_ticker for c in selected]
        # OTM1 should NOT be selected (no diversity, lower rank)
        assert "OTM1" not in tickers

    def test_diversity_puts(self):
        """Diversity works correctly for PUT contracts."""
        config = ContractSelectionConfig(
            diversity_mode="delta_spread",
            diversity_reserved_slots=1,
            diversity_delta_threshold_put=-0.30,
            rank_weight_liquidity=0.60,
            rank_weight_delta=0.00,
            rank_weight_spread=0.40,
            top_k=3,
        )
        selector = ContractSelector(config)

        candidates = [
            _make_candidate(
                delta=-0.50, open_interest=8000, spread_pct=1.0,
                option_type=OptionType.PUT, option_ticker="ATM_PUT1",
            ),
            _make_candidate(
                delta=-0.45, open_interest=6000, spread_pct=1.5,
                option_type=OptionType.PUT, option_ticker="ATM_PUT2",
            ),
            _make_candidate(
                delta=-0.40, open_interest=5000, spread_pct=2.0,
                option_type=OptionType.PUT, option_ticker="ATM_PUT3",
            ),
            _make_candidate(
                delta=-0.15, open_interest=500, spread_pct=5.0,
                option_type=OptionType.PUT, option_ticker="OTM_PUT1",
            ),
        ]

        selected = selector._rank_and_select(candidates, OptionType.PUT)

        assert len(selected) == 3
        tickers = [c.option_ticker for c in selected]
        # OTM put should get the diversity slot
        assert "OTM_PUT1" in tickers


class TestSelectForTickerDirectionHint:
    """Tests for _select_for_ticker honoring opportunity direction_hint.

    The selector used to always produce both calls and puts for every
    ticker, ignoring the directional signal from upstream scanners. When
    a scanner has resolved a clear direction (e.g., BREAKOUT_UP → CALL,
    CHEAP_OPTIONS with momentum filter → CALL or PUT), the opposite side
    should be skipped.
    """

    @pytest.fixture
    def default_config(self) -> ContractSelectionConfig:
        return ContractSelectionConfig()

    def _chain_with_both_sides(self):
        """Minimal options chain with a viable call and put close to ATM."""
        import datetime as _dt

        exp = (_dt.date.today() + _dt.timedelta(days=30)).strftime("%Y-%m-%d")

        def _contract(side: str, strike: float, delta: float):
            return {
                "details": {
                    "contract_type": side,
                    "ticker": f"O:TEST260320{side[0]}{int(strike * 1000):08d}",
                    "strike_price": strike,
                    "expiration_date": exp,
                },
                "open_interest": 8000,
                "day": {
                    "volume": 800,
                    "last_bid": 2.0,
                    "last_ask": 2.2,
                    "close": 2.1,
                },
                "last_quote": {"bid": 2.0, "ask": 2.2, "midpoint": 2.1},
                "greeks": {
                    "delta": delta,
                    "gamma": 0.03,
                    "theta": -0.05,
                    "vega": 0.2,
                },
                "implied_volatility": 0.30,
            }

        return [
            _contract("CALL", 100.0, 0.55),
            _contract("CALL", 102.0, 0.45),
            _contract("PUT", 100.0, -0.45),
            _contract("PUT", 98.0, -0.35),
        ]

    @pytest.mark.asyncio
    async def test_direction_hint_call_skips_puts(self, default_config):
        """CALL hint → only CALL candidates in the output."""
        selector = ContractSelector(default_config)
        chain = self._chain_with_both_sides()

        candidates = await selector._select_for_ticker(
            "TEST", underlying_price=100.0, chain=chain,
            direction_hint=DirectionHint.CALL,
        )

        assert candidates, "expected at least one CALL candidate"
        assert all(c.option_type == OptionType.CALL for c in candidates)

    @pytest.mark.asyncio
    async def test_direction_hint_put_skips_calls(self, default_config):
        """PUT hint → only PUT candidates in the output."""
        selector = ContractSelector(default_config)
        chain = self._chain_with_both_sides()

        candidates = await selector._select_for_ticker(
            "TEST", underlying_price=100.0, chain=chain,
            direction_hint=DirectionHint.PUT,
        )

        assert candidates, "expected at least one PUT candidate"
        assert all(c.option_type == OptionType.PUT for c in candidates)

    @pytest.mark.asyncio
    async def test_direction_hint_none_picks_both_sides(self, default_config):
        """NONE hint → current behavior: both sides selected (regression)."""
        selector = ContractSelector(default_config)
        chain = self._chain_with_both_sides()

        candidates = await selector._select_for_ticker(
            "TEST", underlying_price=100.0, chain=chain,
            direction_hint=DirectionHint.NONE,
        )

        types = {c.option_type for c in candidates}
        assert OptionType.CALL in types
        assert OptionType.PUT in types

    @pytest.mark.asyncio
    async def test_direction_hint_default_is_none(self, default_config):
        """Omitting direction_hint should behave as NONE (backward compatible)."""
        selector = ContractSelector(default_config)
        chain = self._chain_with_both_sides()

        candidates = await selector._select_for_ticker(
            "TEST", underlying_price=100.0, chain=chain,
        )

        types = {c.option_type for c in candidates}
        assert OptionType.CALL in types
        assert OptionType.PUT in types
