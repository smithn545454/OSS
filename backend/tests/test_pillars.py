"""Unit tests for Pillar Scoring (Stage 5).

Tests cover:
1. Linear interpolation and mapping utilities
2. Directional pillar subscores and final score
3. Volatility pillar subscores and final score
4. Structure pillar subscores and final score
5. Full pillar calculator integration
6. Edge cases and boundary conditions
"""

import pytest
from unittest.mock import MagicMock

from app.core.schemas import (
    PillarId,
    DirectionalPillarConfig,
    VolatilityPillarConfig,
    StructurePillarConfig,
    PillarConfig,
    Evaluation,
    Opportunity,
    ScannerTrigger,
    ScannerType,
    DirectionHint,
    OptionType,
    DTEBucket,
)
from app.pillars.utils import (
    linear_interpolate,
    linear_map_range,
    clamp_score,
    distance_from_neutral,
    blend_values,
    map_momentum_score,
    map_rs_score,
    map_iv_rv_ratio_score,
    map_iv_percentile_score,
    map_theta_adjusted_edge_score,
    map_spread_score,
    map_open_interest_score,
    map_volume_score,
    map_theta_burden_score,
    map_liquidity_trend_score,
)
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.directional import (
    compute_directional_pillar,
    compute_trend_alignment_subscore,
    compute_momentum_subscore,
    compute_signal_confirmation_subscore,
    compute_relative_strength_subscore,
    compute_catalyst_subscore,
)
from app.pillars.volatility import (
    compute_volatility_pillar,
    compute_iv_vs_rv_subscore,
    compute_iv_percentile_subscore,
    compute_iv_regime_subscore,
    compute_theta_adjusted_edge_subscore,
)
from app.pillars.structure import (
    compute_structure_pillar,
    compute_spread_subscore,
    compute_open_interest_subscore,
    compute_volume_subscore,
    compute_theta_burden_subscore,
    compute_liquidity_trend_subscore,
)
from app.pillars.calculator import (
    PillarCalculator,
    compute_all_pillars,
    compute_final_score,
)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def bullish_call_context():
    """Create a bullish context for CALL contracts."""
    return ScoringContext(
        evaluation_id="test-eval-1",
        underlying_ticker="AAPL",
        option_type="CALL",
        dte_bucket="B",
        scanner_triggers=["BREAKOUT"],
        direction_hint="CALL",
        close=185.0,
        sma20=180.0,
        sma50=175.0,
        return_5d=3.5,
        return_20d=8.0,
        trend_aligned_bullish=True,
        trend_aligned_bearish=False,
        rs_5d=2.0,
        rs_20d=5.0,
        rv20=0.25,
        iv=0.30,
        iv_rv_ratio=1.20,
        iv_percentile=45.0,
        iv_regime="IV_NEUTRAL_REGIME",
        mid=3.50,
        spread_pct=3.0,
        theta_pct=0.8,
        theta_adjusted_edge=1.8,
        open_interest=1500,
        volume=350,
        oi_5d_change_pct=5.0,
        days_to_earnings=25,
        recent_sec_filing=False,
    )


@pytest.fixture
def bearish_put_context():
    """Create a bearish context for PUT contracts."""
    return ScoringContext(
        evaluation_id="test-eval-2",
        underlying_ticker="META",
        option_type="PUT",
        dte_bucket="A",
        scanner_triggers=["BREAKDOWN"],
        direction_hint="PUT",
        close=300.0,
        sma20=310.0,
        sma50=320.0,
        return_5d=-5.0,
        return_20d=-12.0,
        trend_aligned_bullish=False,
        trend_aligned_bearish=True,
        rs_5d=-3.0,
        rs_20d=-6.0,
        rv20=0.35,
        iv=0.28,
        iv_rv_ratio=0.80,
        iv_percentile=20.0,
        iv_regime="IV_LOW_REGIME",
        mid=5.00,
        spread_pct=2.5,
        theta_pct=1.2,
        theta_adjusted_edge=2.2,
        open_interest=2500,
        volume=600,
        oi_5d_change_pct=15.0,
        days_to_earnings=5,
        recent_sec_filing=True,
    )


@pytest.fixture
def neutral_context():
    """Create a neutral context with no strong signals."""
    return ScoringContext(
        evaluation_id="test-eval-3",
        underlying_ticker="MSFT",
        option_type="CALL",
        dte_bucket="C",
        scanner_triggers=["CHEAP_OPTIONS"],
        direction_hint="NONE",
        close=400.0,
        sma20=398.0,
        sma50=402.0,
        return_5d=0.5,
        return_20d=1.0,
        trend_aligned_bullish=False,
        trend_aligned_bearish=False,
        rs_5d=0.2,
        rs_20d=0.5,
        rv20=0.20,
        iv=0.22,
        iv_rv_ratio=1.10,
        iv_percentile=50.0,
        iv_regime="IV_NEUTRAL_REGIME",
        mid=6.00,
        spread_pct=5.0,
        theta_pct=1.5,
        theta_adjusted_edge=1.0,
        open_interest=500,
        volume=100,
        oi_5d_change_pct=0.0,
        days_to_earnings=None,
        recent_sec_filing=False,
    )


# ============================================================================
# Test Linear Interpolation Utilities
# ============================================================================


class TestLinearInterpolate:
    """Tests for linear_interpolate function."""
    
    def test_exact_breakpoint(self):
        """Test value exactly at breakpoint."""
        breakpoints = [(0, 50), (10, 100)]
        assert linear_interpolate(0, breakpoints) == 50
        assert linear_interpolate(10, breakpoints) == 100
    
    def test_midpoint_interpolation(self):
        """Test interpolation at midpoint."""
        breakpoints = [(0, 0), (10, 100)]
        assert linear_interpolate(5, breakpoints) == 50
    
    def test_below_minimum(self):
        """Test value below minimum breakpoint."""
        breakpoints = [(0, 50), (10, 100)]
        assert linear_interpolate(-5, breakpoints) == 50
    
    def test_above_maximum(self):
        """Test value above maximum breakpoint."""
        breakpoints = [(0, 50), (10, 100)]
        assert linear_interpolate(15, breakpoints) == 100
    
    def test_multiple_breakpoints(self):
        """Test with multiple breakpoints."""
        breakpoints = [(-10, 5), (0, 50), (10, 95)]
        assert linear_interpolate(-10, breakpoints) == 5
        assert linear_interpolate(-5, breakpoints) == 27.5
        assert linear_interpolate(0, breakpoints) == 50
        assert linear_interpolate(5, breakpoints) == 72.5
        assert linear_interpolate(10, breakpoints) == 95
    
    def test_empty_breakpoints(self):
        """Test with empty breakpoints returns neutral."""
        assert linear_interpolate(5, []) == 50


class TestLinearMapRange:
    """Tests for linear_map_range function."""
    
    def test_identity_mapping(self):
        """Test mapping to same range."""
        assert linear_map_range(50, 0, 100, 0, 100) == 50
    
    def test_scale_up(self):
        """Test scaling up."""
        assert linear_map_range(0.5, 0, 1, 0, 100) == 50
    
    def test_scale_down(self):
        """Test scaling down."""
        assert linear_map_range(50, 0, 100, 0, 1) == 0.5
    
    def test_invert(self):
        """Test inverting range."""
        # Note: linear_map_range clamps by default with min/max of outputs
        # So inverting 0,100 -> 100,0 will clamp to [0, 100]
        assert linear_map_range(0, 0, 100, 100, 0, clamp=False) == 100
        assert linear_map_range(100, 0, 100, 100, 0, clamp=False) == 0


class TestClampScore:
    """Tests for clamp_score function."""
    
    def test_in_range(self):
        """Test value in range unchanged."""
        assert clamp_score(50) == 50
        assert clamp_score(0) == 0
        assert clamp_score(100) == 100
    
    def test_below_minimum(self):
        """Test clamping below minimum."""
        assert clamp_score(-10) == 0
    
    def test_above_maximum(self):
        """Test clamping above maximum."""
        assert clamp_score(110) == 100


class TestDistanceFromNeutral:
    """Tests for distance_from_neutral function."""
    
    def test_at_neutral(self):
        """Test distance at neutral is zero."""
        assert distance_from_neutral(50) == 0
    
    def test_above_neutral(self):
        """Test distance above neutral."""
        assert distance_from_neutral(80) == 30
    
    def test_below_neutral(self):
        """Test distance below neutral."""
        assert distance_from_neutral(20) == 30


class TestBlendValues:
    """Tests for blend_values function."""
    
    def test_equal_blend(self):
        """Test 50/50 blend."""
        assert blend_values(0, 100, 0.5) == 50
    
    def test_full_weight_first(self):
        """Test full weight on first value."""
        assert blend_values(100, 0, 1.0) == 100
    
    def test_full_weight_second(self):
        """Test full weight on second value."""
        assert blend_values(100, 0, 0.0) == 0
    
    def test_first_none(self):
        """Test with first value None."""
        assert blend_values(None, 100, 0.5) == 100
    
    def test_second_none(self):
        """Test with second value None."""
        assert blend_values(100, None, 0.5) == 100
    
    def test_both_none(self):
        """Test with both values None."""
        assert blend_values(None, None, 0.5) is None


# ============================================================================
# Test Score Mapping Functions
# ============================================================================


class TestMomentumScoreMapping:
    """Tests for map_momentum_score function."""
    
    def test_strong_positive_call(self):
        """Test strong positive return for CALL."""
        score = map_momentum_score(10.0, is_call=True)
        assert score >= 90
    
    def test_strong_negative_call(self):
        """Test strong negative return for CALL."""
        score = map_momentum_score(-10.0, is_call=True)
        assert score <= 10
    
    def test_neutral_return(self):
        """Test neutral return."""
        score = map_momentum_score(0.0, is_call=True)
        assert 40 <= score <= 60
    
    def test_put_inverts(self):
        """Test PUT inverts the return."""
        call_score = map_momentum_score(5.0, is_call=True)
        put_score = map_momentum_score(5.0, is_call=False)
        # For PUT, positive return should score lower
        assert put_score < call_score


class TestIVRVRatioScoreMapping:
    """Tests for map_iv_rv_ratio_score function."""
    
    def test_cheap_options(self):
        """Test IV/RV < 1 (cheap options)."""
        assert map_iv_rv_ratio_score(0.8) >= 90
    
    def test_fair_options(self):
        """Test IV/RV ~1 (fair value)."""
        score = map_iv_rv_ratio_score(1.0)
        assert 70 <= score <= 90
    
    def test_expensive_options(self):
        """Test IV/RV > 1.5 (expensive)."""
        assert map_iv_rv_ratio_score(1.8) <= 30


class TestSpreadScoreMapping:
    """Tests for map_spread_score function."""
    
    def test_tight_spread(self):
        """Test tight spread scores high."""
        assert map_spread_score(1.0) >= 90
    
    def test_moderate_spread(self):
        """Test moderate spread."""
        score = map_spread_score(5.0)
        assert 60 <= score <= 80
    
    def test_wide_spread(self):
        """Test wide spread scores low."""
        assert map_spread_score(12.0) <= 30


class TestOpenInterestScoreMapping:
    """Tests for map_open_interest_score function."""
    
    def test_high_oi(self):
        """Test high OI scores high."""
        assert map_open_interest_score(3000) >= 90
    
    def test_moderate_oi(self):
        """Test moderate OI."""
        score = map_open_interest_score(500)
        assert 60 <= score <= 70
    
    def test_low_oi(self):
        """Test low OI scores low."""
        assert map_open_interest_score(100) <= 30


# ============================================================================
# Test Directional Pillar Subscores
# ============================================================================


class TestTrendAlignmentSubscore:
    """Tests for trend alignment subscore."""
    
    def test_strong_bullish_call(self, bullish_call_context):
        """Test strong bullish trend with CALL."""
        subscore = compute_trend_alignment_subscore(bullish_call_context)
        assert subscore.score >= 85
        assert subscore.name == "trend_alignment"
    
    def test_strong_bearish_put(self, bearish_put_context):
        """Test strong bearish trend with PUT."""
        subscore = compute_trend_alignment_subscore(bearish_put_context)
        assert subscore.score >= 85
    
    def test_neutral_trend(self, neutral_context):
        """Test neutral trend."""
        # Neutral context has close=400, sma20=398, sma50=402
        # close > sma20 but sma20 < sma50 = partial bullish = 65 for CALL
        subscore = compute_trend_alignment_subscore(neutral_context)
        assert 60 <= subscore.score <= 70
    
    def test_missing_data(self):
        """Test with missing SMA data."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            close=100.0,
            sma20=None,
            sma50=None,
        )
        subscore = compute_trend_alignment_subscore(ctx)
        assert subscore.score == 50  # Neutral default


class TestMomentumSubscore:
    """Tests for momentum subscore."""
    
    def test_positive_momentum_call(self, bullish_call_context):
        """Test positive momentum for CALL."""
        config = DirectionalPillarConfig()
        subscore = compute_momentum_subscore(bullish_call_context, config)
        assert subscore.score >= 60
        assert subscore.name == "momentum"
    
    def test_negative_momentum_put(self, bearish_put_context):
        """Test negative momentum for PUT (should score high)."""
        config = DirectionalPillarConfig()
        subscore = compute_momentum_subscore(bearish_put_context, config)
        assert subscore.score >= 60  # PUT benefits from negative momentum
    
    def test_dte_bucket_weighting(self):
        """Test DTE bucket affects momentum blend."""
        config = DirectionalPillarConfig()
        
        ctx_a = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="A",
            return_5d=10.0,
            return_20d=0.0,
        )
        ctx_d = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="D",
            return_5d=10.0,
            return_20d=0.0,
        )
        
        score_a = compute_momentum_subscore(ctx_a, config)
        score_d = compute_momentum_subscore(ctx_d, config)
        
        # Bucket A weights 5d more, so should score higher with positive 5d
        assert score_a.score > score_d.score


class TestSignalConfirmationSubscore:
    """Tests for signal confirmation subscore."""
    
    def test_breakout_call_match(self, bullish_call_context):
        """Test breakout matching CALL."""
        subscore = compute_signal_confirmation_subscore(bullish_call_context)
        assert subscore.score >= 80
    
    def test_breakdown_put_match(self, bearish_put_context):
        """Test breakdown matching PUT."""
        subscore = compute_signal_confirmation_subscore(bearish_put_context)
        assert subscore.score >= 80
    
    def test_conflicting_signal(self):
        """Test conflicting signal (breakout + PUT)."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="PUT",
            dte_bucket="B",
            scanner_triggers=["BREAKOUT"],
            direction_hint="CALL",
        )
        subscore = compute_signal_confirmation_subscore(ctx)
        assert subscore.score <= 30
    
    def test_no_signals(self):
        """Test no scanner signals."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            scanner_triggers=[],
            direction_hint="NONE",
        )
        subscore = compute_signal_confirmation_subscore(ctx)
        assert 40 <= subscore.score <= 50


class TestRelativeStrengthSubscore:
    """Tests for relative strength subscore."""
    
    def test_outperforming_call(self, bullish_call_context):
        """Test outperforming stock with CALL."""
        subscore = compute_relative_strength_subscore(bullish_call_context)
        assert subscore.score >= 65
    
    def test_underperforming_put(self, bearish_put_context):
        """Test underperforming stock with PUT (should score high)."""
        subscore = compute_relative_strength_subscore(bearish_put_context)
        assert subscore.score >= 65
    
    def test_missing_data(self):
        """Test with missing RS data."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            rs_20d=None,
        )
        subscore = compute_relative_strength_subscore(ctx)
        assert subscore.score == 50


class TestCatalystSubscore:
    """Tests for catalyst subscore."""
    
    def test_imminent_earnings(self, bearish_put_context):
        """Test earnings within 7 days."""
        subscore = compute_catalyst_subscore(bearish_put_context)
        assert subscore.score == 70
    
    def test_distant_earnings(self):
        """Test earnings far away."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            days_to_earnings=60,
            recent_sec_filing=False,
        )
        subscore = compute_catalyst_subscore(ctx)
        assert subscore.score == 50
    
    def test_sec_filing(self):
        """Test recent SEC filing."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            days_to_earnings=None,
            recent_sec_filing=True,
        )
        subscore = compute_catalyst_subscore(ctx)
        assert subscore.score == 60


# ============================================================================
# Test Volatility Pillar Subscores
# ============================================================================


class TestIVvsRVSubscore:
    """Tests for IV vs RV subscore."""
    
    def test_cheap_iv(self, bearish_put_context):
        """Test IV/RV < 1 (cheap options)."""
        subscore = compute_iv_vs_rv_subscore(bearish_put_context)
        assert subscore.score >= 85
    
    def test_expensive_iv(self):
        """Test IV/RV > 1.25 (expensive)."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            iv=0.50,
            rv20=0.25,
            iv_rv_ratio=2.0,
        )
        subscore = compute_iv_vs_rv_subscore(ctx)
        assert subscore.score <= 30


class TestIVPercentileSubscore:
    """Tests for IV percentile subscore."""
    
    def test_low_percentile(self, bearish_put_context):
        """Test low IV percentile (favorable)."""
        subscore = compute_iv_percentile_subscore(bearish_put_context)
        assert subscore.score >= 85
    
    def test_high_percentile(self):
        """Test high IV percentile (unfavorable)."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            iv_percentile=90.0,
        )
        subscore = compute_iv_percentile_subscore(ctx)
        # 90% percentile should score low (< 30)
        assert subscore.score <= 30


class TestIVRegimeSubscore:
    """Tests for IV regime subscore."""
    
    def test_low_regime(self, bearish_put_context):
        """Test IV_LOW_REGIME scores high."""
        subscore = compute_iv_regime_subscore(bearish_put_context)
        assert subscore.score == 80
    
    def test_high_regime(self):
        """Test IV_HIGH_REGIME scores low."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            iv_regime="IV_HIGH_REGIME",
        )
        subscore = compute_iv_regime_subscore(ctx)
        assert subscore.score == 30
    
    def test_neutral_regime(self, neutral_context):
        """Test IV_NEUTRAL_REGIME scores neutral."""
        subscore = compute_iv_regime_subscore(neutral_context)
        assert subscore.score == 60


class TestThetaAdjustedEdgeSubscore:
    """Tests for theta-adjusted edge subscore."""
    
    def test_strong_edge(self, bearish_put_context):
        """Test strong theta-adjusted edge."""
        subscore = compute_theta_adjusted_edge_subscore(bearish_put_context)
        assert subscore.score >= 75
    
    def test_weak_edge(self):
        """Test weak theta-adjusted edge."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            theta_adjusted_edge=0.5,
        )
        subscore = compute_theta_adjusted_edge_subscore(ctx)
        assert subscore.score <= 35


# ============================================================================
# Test Structure Pillar Subscores
# ============================================================================


class TestSpreadSubscore:
    """Tests for spread subscore."""
    
    def test_tight_spread(self, bearish_put_context):
        """Test tight spread scores high."""
        subscore = compute_spread_subscore(bearish_put_context)
        assert subscore.score >= 85
    
    def test_wide_spread(self):
        """Test wide spread scores low."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            spread_pct=12.0,
        )
        subscore = compute_spread_subscore(ctx)
        assert subscore.score <= 30


class TestOpenInterestSubscore:
    """Tests for open interest subscore."""
    
    def test_high_oi(self, bearish_put_context):
        """Test high OI scores high."""
        subscore = compute_open_interest_subscore(bearish_put_context)
        assert subscore.score >= 85
    
    def test_low_oi(self):
        """Test low OI scores low."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            open_interest=100,
        )
        subscore = compute_open_interest_subscore(ctx)
        assert subscore.score <= 30


class TestVolumeSubscore:
    """Tests for volume subscore."""
    
    def test_high_volume(self, bearish_put_context):
        """Test high volume scores high."""
        subscore = compute_volume_subscore(bearish_put_context)
        assert subscore.score >= 85
    
    def test_low_volume(self):
        """Test low volume scores low."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            volume=20,  # Very low volume
        )
        subscore = compute_volume_subscore(ctx)
        assert subscore.score <= 30


class TestThetaBurdenSubscore:
    """Tests for theta burden subscore."""
    
    def test_low_theta_burden(self):
        """Test low theta burden scores high."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            theta_pct=0.3,
        )
        subscore = compute_theta_burden_subscore(ctx)
        assert subscore.score >= 85
    
    def test_high_theta_burden(self):
        """Test high theta burden scores low."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            theta_pct=4.0,
        )
        subscore = compute_theta_burden_subscore(ctx)
        assert subscore.score <= 30


class TestLiquidityTrendSubscore:
    """Tests for liquidity trend subscore."""
    
    def test_growing_oi(self, bearish_put_context):
        """Test growing OI scores high."""
        subscore = compute_liquidity_trend_subscore(bearish_put_context)
        assert subscore.score >= 80
    
    def test_declining_oi(self):
        """Test declining OI scores low."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            oi_5d_change_pct=-25.0,
        )
        subscore = compute_liquidity_trend_subscore(ctx)
        assert subscore.score <= 35
    
    def test_missing_data(self):
        """Test missing OI change data."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            oi_5d_change_pct=None,
        )
        subscore = compute_liquidity_trend_subscore(ctx)
        assert subscore.score == 50


# ============================================================================
# Test Full Pillar Computations
# ============================================================================


class TestDirectionalPillar:
    """Tests for complete directional pillar."""
    
    def test_bullish_call_high_score(self, bullish_call_context):
        """Test bullish setup with CALL scores high."""
        result = compute_directional_pillar(bullish_call_context)
        assert result.pillar_id == PillarId.DIRECTIONAL
        assert result.score >= 65
        assert len(result.subscores) == 5
        assert len(result.top_contributors) <= 3
    
    def test_bearish_put_high_score(self, bearish_put_context):
        """Test bearish setup with PUT scores high."""
        result = compute_directional_pillar(bearish_put_context)
        assert result.score >= 70
    
    def test_generates_tags(self, bullish_call_context):
        """Test tag generation."""
        result = compute_directional_pillar(bullish_call_context)
        assert isinstance(result.tags, list)
        assert "STRONG_TREND" in result.tags or "HIGH_MOMENTUM" in result.tags or "BREAKOUT_CONFIRMED" in result.tags


class TestVolatilityPillar:
    """Tests for complete volatility pillar."""
    
    def test_cheap_options_high_score(self, bearish_put_context):
        """Test cheap options score high."""
        result = compute_volatility_pillar(bearish_put_context)
        assert result.pillar_id == PillarId.VOLATILITY
        assert result.score >= 70
        assert len(result.subscores) == 4
    
    def test_expensive_options_low_score(self):
        """Test expensive options score low."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            iv_rv_ratio=2.0,
            iv_percentile=90.0,
            iv_regime="IV_HIGH_REGIME",
            theta_adjusted_edge=0.5,
        )
        result = compute_volatility_pillar(ctx)
        assert result.score <= 35


class TestStructurePillar:
    """Tests for complete structure pillar."""
    
    def test_liquid_contract_high_score(self, bearish_put_context):
        """Test liquid contract scores high."""
        result = compute_structure_pillar(bearish_put_context)
        assert result.pillar_id == PillarId.STRUCTURE
        assert result.score >= 75
        assert len(result.subscores) == 5
    
    def test_illiquid_contract_low_score(self):
        """Test illiquid contract scores low."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            spread_pct=12.0,
            open_interest=100,
            volume=20,
            theta_pct=4.0,
            oi_5d_change_pct=-25.0,
        )
        result = compute_structure_pillar(ctx)
        assert result.score <= 35


# ============================================================================
# Test Pillar Calculator Integration
# ============================================================================


class TestPillarCalculator:
    """Tests for PillarCalculator class."""
    
    def test_compute_all_pillars(self, bullish_call_context):
        """Test computing all three pillars."""
        # Create mock evaluation
        eval_mock = MagicMock()
        eval_mock.evaluation_id = bullish_call_context.evaluation_id
        eval_mock.underlying_ticker = bullish_call_context.underlying_ticker
        eval_mock.option_type = bullish_call_context.option_type
        eval_mock.dte_bucket = bullish_call_context.dte_bucket
        eval_mock.underlying_price = bullish_call_context.close
        eval_mock.iv = bullish_call_context.iv
        eval_mock.mid = bullish_call_context.mid
        eval_mock.spread_pct = bullish_call_context.spread_pct
        eval_mock.open_interest = bullish_call_context.open_interest
        eval_mock.volume = bullish_call_context.volume
        
        # Create mock feature set
        fs_mock = MagicMock()
        fs_mock.evaluation_id = bullish_call_context.evaluation_id
        fs_mock.close = bullish_call_context.close
        fs_mock.sma20 = bullish_call_context.sma20
        fs_mock.sma50 = bullish_call_context.sma50
        fs_mock.return_5d = bullish_call_context.return_5d
        fs_mock.return_20d = bullish_call_context.return_20d
        fs_mock.trend_aligned_bullish = bullish_call_context.trend_aligned_bullish
        fs_mock.trend_aligned_bearish = bullish_call_context.trend_aligned_bearish
        fs_mock.rs_5d = bullish_call_context.rs_5d
        fs_mock.rs_20d = bullish_call_context.rs_20d
        fs_mock.rv20 = bullish_call_context.rv20
        fs_mock.iv = bullish_call_context.iv
        fs_mock.iv_rv_ratio = bullish_call_context.iv_rv_ratio
        fs_mock.iv_percentile = bullish_call_context.iv_percentile
        fs_mock.iv_regime = bullish_call_context.iv_regime
        fs_mock.mid = bullish_call_context.mid
        fs_mock.spread_pct = bullish_call_context.spread_pct
        fs_mock.theta_pct = bullish_call_context.theta_pct
        fs_mock.theta_adjusted_edge = bullish_call_context.theta_adjusted_edge
        fs_mock.open_interest = bullish_call_context.open_interest
        fs_mock.volume = bullish_call_context.volume
        fs_mock.oi_5d_change_pct = bullish_call_context.oi_5d_change_pct
        fs_mock.days_to_earnings = bullish_call_context.days_to_earnings
        fs_mock.recent_sec_filing = bullish_call_context.recent_sec_filing
        
        # Create mock opportunity
        opp_mock = MagicMock()
        opp_mock.scanner_triggers = [
            MagicMock(scanner_type="BREAKOUT")
        ]
        opp_mock.direction_hint = "CALL"
        
        calculator = PillarCalculator()
        results = calculator.compute_pillars(eval_mock, fs_mock, opp_mock)
        
        assert len(results) == 3
        assert results[0].pillar_id == PillarId.DIRECTIONAL
        assert results[1].pillar_id == PillarId.VOLATILITY
        assert results[2].pillar_id == PillarId.STRUCTURE


class TestComputeFinalScore:
    """Tests for compute_final_score function."""
    
    def test_default_weights(self):
        """Test with default weights (0.35, 0.35, 0.30)."""
        score = compute_final_score(80, 70, 60)
        expected = 0.35 * 80 + 0.35 * 70 + 0.30 * 60
        assert abs(score - expected) < 0.01
    
    def test_all_100(self):
        """Test with all perfect scores."""
        score = compute_final_score(100, 100, 100)
        assert score == 100
    
    def test_all_0(self):
        """Test with all zero scores."""
        score = compute_final_score(0, 0, 0)
        assert score == 0
    
    def test_custom_weights(self):
        """Test with custom weights."""
        from app.core.schemas import PillarWeights
        
        # Create new config with custom weights (schemas are frozen)
        custom_weights = PillarWeights(
            directional=0.50,
            volatility=0.30,
            structure=0.20,
        )
        config = PillarConfig(weights=custom_weights)
        
        score = compute_final_score(80, 70, 60, config)
        expected = 0.50 * 80 + 0.30 * 70 + 0.20 * 60
        assert abs(score - expected) < 0.01


# ============================================================================
# Test Edge Cases
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""
    
    def test_all_none_features(self):
        """Test with all None features."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
        )
        
        result = compute_directional_pillar(ctx)
        assert result.score >= 0 and result.score <= 100
    
    def test_extreme_values(self):
        """Test with extreme feature values."""
        ctx = ScoringContext(
            evaluation_id="test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="B",
            return_5d=100.0,
            return_20d=200.0,
            iv_rv_ratio=10.0,
            spread_pct=50.0,
            open_interest=100000,
        )
        
        d_result = compute_directional_pillar(ctx)
        v_result = compute_volatility_pillar(ctx)
        s_result = compute_structure_pillar(ctx)
        
        # Scores should still be in valid range
        assert 0 <= d_result.score <= 100
        assert 0 <= v_result.score <= 100
        assert 0 <= s_result.score <= 100
    
    def test_pillar_result_to_schema(self, bullish_call_context):
        """Test PillarResult conversion to PillarScore schema."""
        result = compute_directional_pillar(bullish_call_context)
        score = result.to_pillar_score()
        
        assert score.evaluation_id == bullish_call_context.evaluation_id
        assert score.pillar_id == PillarId.DIRECTIONAL
        assert 0 <= score.score <= 100
        assert len(score.contributors) <= 3
        assert isinstance(score.tags, list)


# ============================================================================
# Test Subscore Model
# ============================================================================


class TestSubscoreModel:
    """Tests for Subscore dataclass."""
    
    def test_weighted_contribution(self):
        """Test weighted contribution calculation."""
        subscore = Subscore(
            name="test",
            raw_value=100,
            score=80.0,
            weight=0.25,
        )
        assert subscore.weighted_contribution == 20.0
    
    def test_distance_from_neutral(self):
        """Test distance from neutral calculation."""
        subscore = Subscore(
            name="test",
            raw_value=100,
            score=80.0,
            weight=0.25,
        )
        assert subscore.distance_from_neutral == 30.0
    
    def test_to_contributor(self):
        """Test conversion to PillarContributor."""
        subscore = Subscore(
            name="test",
            raw_value=100,
            score=80.0,
            weight=0.25,
        )
        contributor = subscore.to_contributor()
        
        assert contributor.feature_name == "test"
        assert contributor.subscore == 80.0
        assert contributor.weight == 0.25
        assert contributor.weighted_contribution == 20.0
        assert contributor.raw_value == 100
        assert contributor.distance_from_neutral == 30.0
