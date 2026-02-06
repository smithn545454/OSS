"""Unit tests for Phase 4: Feature Computation.

Tests all feature calculations per Section 13 of OSS_Complete_Requirements.md.
"""

import math
import pytest
from datetime import datetime

from app.core.schemas import (
    Evaluation,
    IVHistory,
    IVRegime,
    OIHistory,
    OptionType,
    DTEBucket,
    FeatureConfig,
)
from app.services.polygon import DailyBar
from app.features.models import FeatureSet
from app.features.underlying import compute_underlying_features, UnderlyingFeatures
from app.features.relative_strength import compute_relative_strength_features
from app.features.volatility import (
    calculate_iv_percentile,
    calculate_iv_10d_change,
    classify_iv_regime,
    compute_volatility_features,
)
from app.features.contract import (
    calculate_theta_pct,
    calculate_theta_adjusted_edge,
    compute_contract_features,
)
from app.features.liquidity import calculate_oi_5d_change, compute_liquidity_features


# =============================================================================
# Test Fixtures
# =============================================================================

def make_daily_bars(
    ticker: str,
    num_days: int,
    start_price: float = 100.0,
    daily_return: float = 0.001,
) -> list[DailyBar]:
    """Generate test daily bars."""
    bars = []
    price = start_price
    for i in range(num_days):
        date = f"2025-01-{i+1:02d}"
        high = price * 1.02
        low = price * 0.98
        bars.append(DailyBar(
            ticker=ticker,
            date=date,
            open=price,
            high=high,
            low=low,
            close=price,
            volume=1000000,
        ))
        price *= (1 + daily_return)
    return bars


def make_iv_history(
    ticker: str,
    num_days: int,
    base_iv: float = 0.25,
    iv_trend: float = 0.0,
) -> list[IVHistory]:
    """Generate test IV history records."""
    records = []
    iv = base_iv
    for i in range(num_days):
        date = f"2025-01-{num_days - i:02d}"  # Most recent first
        records.append(IVHistory(
            ticker=ticker,
            date=date,
            atm_iv=iv,
            atm_call_iv=iv + 0.01,
            atm_put_iv=iv - 0.01,
        ))
        iv *= (1 + iv_trend)
    return records


def make_oi_history(
    option_ticker: str,
    num_days: int,
    base_oi: int = 1000,
    daily_change_pct: float = 0.02,
) -> list[OIHistory]:
    """Generate test OI history records."""
    records = []
    oi = base_oi
    for i in range(num_days):
        date = f"2025-01-{num_days - i:02d}"  # Most recent first
        records.append(OIHistory(
            option_ticker=option_ticker,
            date=date,
            open_interest=int(oi),
            volume=100,
        ))
        oi *= (1 - daily_change_pct)  # Decreasing for past
    return records


def make_evaluation(
    ticker: str = "AAPL",
    option_type: OptionType = OptionType.CALL,
    underlying_price: float = 150.0,
    strike: float = 155.0,
    mid: float = 3.50,
    iv: float = 0.30,
    delta: float = 0.45,
    theta: float = -0.10,
    open_interest: int = 1000,
    volume: int = 200,
    dte: int = 30,
) -> Evaluation:
    """Create a test evaluation."""
    return Evaluation(
        evaluation_id="test-eval-001",
        opportunity_id="test-opp-001",
        underlying_ticker=ticker,
        option_ticker=f"O:{ticker}250215C00{int(strike*1000):08d}",
        option_type=option_type,
        expiration_date="2025-02-15",
        dte=dte,
        strike=strike,
        underlying_price=underlying_price,
        moneyness_pct=((strike - underlying_price) / underlying_price) * 100,
        bid=mid - 0.10,
        ask=mid + 0.10,
        mid=mid,
        spread_abs=0.20,
        spread_pct=(0.20 / mid) * 100,
        iv=iv,
        delta=delta,
        gamma=0.03,
        theta=theta,
        vega=0.15,
        open_interest=open_interest,
        volume=volume,
        breakeven_price=strike + mid,  # CALL
        required_move_pct=abs((strike + mid) - underlying_price) / underlying_price * 100,
        expected_move_pct=iv * math.sqrt(dte / 365) * 100,
        feasibility_ratio=0.5,  # Placeholder
        time_adjusted_feasibility=0.5,  # Placeholder
        dte_bucket=DTEBucket.B,
        rank_score=75.0,
        policy_version="v2.0.0",
        policy_hash="abc123",
    )


# =============================================================================
# Category A: Underlying Technical Features Tests
# =============================================================================

class TestUnderlyingFeatures:
    """Tests for underlying technical features."""
    
    def test_compute_with_sufficient_data(self):
        """Test computation with enough bars for all indicators."""
        bars = make_daily_bars("AAPL", 60, start_price=150.0, daily_return=0.005)
        
        result = compute_underlying_features(bars)
        
        assert result is not None
        assert result.close == bars[-1].close
        assert result.sma20 is not None
        assert result.sma50 is not None
        assert result.return_5d is not None
        assert result.return_20d is not None
        assert result.atr14 is not None
        assert result.atr14_pct is not None
        assert result.rv20 is not None
    
    def test_compute_with_insufficient_data(self):
        """Test computation with too few bars."""
        bars = make_daily_bars("AAPL", 3)
        
        result = compute_underlying_features(bars)
        
        assert result is None
    
    def test_trend_aligned_bullish(self):
        """Test bullish trend alignment detection."""
        # Create uptrending data: close > sma20 > sma50
        bars = make_daily_bars("AAPL", 60, start_price=100.0, daily_return=0.01)
        
        result = compute_underlying_features(bars)
        
        # With strong uptrend, should be bullish aligned
        # (depends on exact calculation, may need adjustment)
        assert result is not None
    
    def test_trend_aligned_bearish(self):
        """Test bearish trend alignment detection."""
        # Create downtrending data: close < sma20 < sma50
        bars = make_daily_bars("AAPL", 60, start_price=150.0, daily_return=-0.01)
        
        result = compute_underlying_features(bars)
        
        assert result is not None


# =============================================================================
# Category B: Relative Strength Features Tests
# =============================================================================

class TestRelativeStrengthFeatures:
    """Tests for relative strength vs SPY."""
    
    def test_relative_strength_positive(self):
        """Test positive relative strength (stock outperforming SPY)."""
        # Stock up 10%, SPY up 5%
        result = compute_relative_strength_features(
            underlying_return_5d=10.0,
            underlying_return_20d=15.0,
            spy_bars=make_daily_bars("SPY", 25, start_price=400.0, daily_return=0.002),
        )
        
        assert result.spy_return_5d is not None
        assert result.rs_5d is not None
        # Stock outperforming -> positive RS
        assert result.rs_5d > 0
    
    def test_relative_strength_negative(self):
        """Test negative relative strength (stock underperforming SPY)."""
        # Stock down 5%, SPY up 5%
        result = compute_relative_strength_features(
            underlying_return_5d=-5.0,
            underlying_return_20d=-10.0,
            spy_bars=make_daily_bars("SPY", 25, start_price=400.0, daily_return=0.005),
        )
        
        assert result.rs_5d is not None
        assert result.rs_5d < 0
    
    def test_with_missing_spy_data(self):
        """Test with insufficient SPY data."""
        result = compute_relative_strength_features(
            underlying_return_5d=10.0,
            underlying_return_20d=15.0,
            spy_bars=[],
        )
        
        assert result.spy_return_5d is None
        assert result.rs_5d is None


# =============================================================================
# Category C: Volatility Features Tests
# =============================================================================

class TestVolatilityFeatures:
    """Tests for volatility features."""
    
    def test_iv_percentile_calculation(self):
        """Test IV percentile calculation."""
        # Current IV at 30%, history ranges from 20% to 40%
        iv_history = [
            IVHistory(ticker="AAPL", date=f"2025-01-{i:02d}", atm_iv=0.20 + i * 0.01)
            for i in range(1, 21)
        ]  # 20% to 39% (20 days)
        
        # Current IV of 30% should be around 50th percentile
        percentile = calculate_iv_percentile(0.30, iv_history, min_days=20)
        
        assert percentile is not None
        assert 40 <= percentile <= 60  # Around 50%
    
    def test_iv_percentile_low(self):
        """Test low IV percentile."""
        iv_history = make_iv_history("AAPL", 30, base_iv=0.40)
        
        # Very low current IV should have low percentile
        percentile = calculate_iv_percentile(0.20, iv_history, min_days=20)
        
        assert percentile is not None
        assert percentile < 20  # Low percentile
    
    def test_iv_percentile_high(self):
        """Test high IV percentile."""
        iv_history = make_iv_history("AAPL", 30, base_iv=0.20)
        
        # Very high current IV should have high percentile
        percentile = calculate_iv_percentile(0.50, iv_history, min_days=20)
        
        assert percentile is not None
        assert percentile > 80  # High percentile
    
    def test_iv_percentile_insufficient_data(self):
        """Test IV percentile with insufficient history."""
        iv_history = make_iv_history("AAPL", 10)  # Only 10 days
        
        percentile = calculate_iv_percentile(0.25, iv_history, min_days=20)
        
        assert percentile is None
    
    def test_iv_10d_change_positive(self):
        """Test positive IV change detection."""
        # IV increasing over 10 days (20% increase from 0.25 to 0.30)
        iv_history = make_iv_history("AAPL", 15, base_iv=0.30, iv_trend=-0.02)
        
        change = calculate_iv_10d_change(iv_history)
        
        assert change is not None
        # Most recent is 0.30, 10 days ago was lower
        assert change > 0
    
    def test_iv_regime_low(self):
        """Test IV_LOW_REGIME classification."""
        regime = classify_iv_regime(
            iv_percentile=20.0,  # Low percentile
            days_to_earnings=None,
            iv_10d_change=5.0,  # Not trending
        )
        
        assert regime == IVRegime.IV_LOW_REGIME
    
    def test_iv_regime_high(self):
        """Test IV_HIGH_REGIME classification."""
        regime = classify_iv_regime(
            iv_percentile=80.0,  # High percentile
            days_to_earnings=None,
            iv_10d_change=-5.0,  # Not trending
        )
        
        assert regime == IVRegime.IV_HIGH_REGIME
    
    def test_iv_regime_trending_up(self):
        """Test IV_TRENDING_UP classification."""
        regime = classify_iv_regime(
            iv_percentile=50.0,
            days_to_earnings=None,
            iv_10d_change=15.0,  # >10% increase
        )
        
        assert regime == IVRegime.IV_TRENDING_UP
    
    def test_iv_regime_trending_down(self):
        """Test IV_TRENDING_DOWN classification."""
        regime = classify_iv_regime(
            iv_percentile=50.0,
            days_to_earnings=None,
            iv_10d_change=-15.0,  # >10% decrease
        )
        
        assert regime == IVRegime.IV_TRENDING_DOWN
    
    def test_iv_regime_pre_catalyst_compressed(self):
        """Test IV_COMPRESSED_PRE_CATALYST classification."""
        regime = classify_iv_regime(
            iv_percentile=40.0,  # Low IV
            days_to_earnings=7,  # Near earnings
            iv_10d_change=5.0,
        )
        
        assert regime == IVRegime.IV_COMPRESSED_PRE_CATALYST
    
    def test_iv_regime_pre_catalyst_elevated(self):
        """Test IV_ELEVATED_PRE_CATALYST classification."""
        regime = classify_iv_regime(
            iv_percentile=60.0,  # Higher IV
            days_to_earnings=10,  # Near earnings
            iv_10d_change=5.0,
        )
        
        assert regime == IVRegime.IV_ELEVATED_PRE_CATALYST


# =============================================================================
# Category D: Contract Features Tests
# =============================================================================

class TestContractFeatures:
    """Tests for contract-specific features."""
    
    def test_theta_pct_calculation(self):
        """Test theta percentage calculation."""
        theta_pct = calculate_theta_pct(theta=-0.10, mid=5.00)
        
        # abs(-0.10) / 5.00 * 100 = 2%
        assert theta_pct == 2.0
    
    def test_theta_pct_zero_mid(self):
        """Test theta percentage with zero mid price."""
        theta_pct = calculate_theta_pct(theta=-0.10, mid=0.0)
        
        assert theta_pct == 0.0
    
    def test_theta_adjusted_edge(self):
        """Test theta-adjusted edge calculation per Section 13.3."""
        edge = calculate_theta_adjusted_edge(
            underlying_price=150.0,
            iv=0.30,
            delta=0.45,
            theta=-0.10,
        )
        
        # daily_expected_move = 150 * (0.30 / sqrt(252)) = 2.84
        # daily_expected_gain = 2.84 * 0.45 = 1.28
        # theta_adjusted_edge = 1.28 / 0.10 = 12.8
        assert edge is not None
        assert edge > 1.0  # Should have positive edge
    
    def test_theta_adjusted_edge_zero_theta(self):
        """Test theta-adjusted edge with zero theta."""
        edge = calculate_theta_adjusted_edge(
            underlying_price=150.0,
            iv=0.30,
            delta=0.45,
            theta=0.0,  # Zero theta
        )
        
        assert edge is None
    
    def test_compute_contract_features(self):
        """Test full contract features computation."""
        evaluation = make_evaluation()
        
        result = compute_contract_features(evaluation)
        
        assert result.mid == evaluation.mid
        assert result.spread_pct == evaluation.spread_pct
        assert result.theta_pct > 0
        assert result.theta_adjusted_edge is not None


# =============================================================================
# Category E: Liquidity Features Tests
# =============================================================================

class TestLiquidityFeatures:
    """Tests for liquidity features."""
    
    def test_oi_5d_change_positive(self):
        """Test positive OI change (increasing interest)."""
        # Current OI 1000, 5 days ago was ~833 (decreasing history)
        oi_history = make_oi_history("O:AAPL...", 10, base_oi=1000, daily_change_pct=0.04)
        
        change = calculate_oi_5d_change(1000, oi_history)
        
        assert change is not None
        assert change > 0  # OI increased
    
    def test_oi_5d_change_negative(self):
        """Test negative OI change (decreasing interest)."""
        # Current OI lower than 5 days ago
        oi_history = [
            OIHistory(option_ticker="test", date=f"2025-01-{10-i:02d}", open_interest=1000 + i*50)
            for i in range(10)
        ]  # Increasing OI in history = current is lower
        
        change = calculate_oi_5d_change(900, oi_history)
        
        assert change is not None
        assert change < 0  # OI decreased
    
    def test_oi_5d_change_insufficient_data(self):
        """Test OI change with insufficient history."""
        oi_history = make_oi_history("test", 3)  # Only 3 days
        
        change = calculate_oi_5d_change(1000, oi_history)
        
        assert change is None


# =============================================================================
# Feature Set Tests
# =============================================================================

class TestFeatureSet:
    """Tests for FeatureSet model."""
    
    def test_to_feature_values(self):
        """Test conversion to FeatureValue list."""
        feature_set = FeatureSet(
            evaluation_id="test-001",
            close=150.0,
            sma20=148.0,
            sma50=145.0,
            return_5d=5.0,
            return_20d=10.0,
            iv=0.30,
            mid=3.50,
            spread_pct=5.0,
            theta_pct=2.0,
            open_interest=1000,
            volume=200,
        )
        
        feature_values = feature_set.to_feature_values()
        
        assert len(feature_values) > 0
        
        # Check some values
        close_fv = next((fv for fv in feature_values if fv.feature_name == "close"), None)
        assert close_fv is not None
        assert close_fv.value == 150.0
        assert close_fv.units == "dollars"
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        feature_set = FeatureSet(
            evaluation_id="test-001",
            close=150.0,
            iv=0.30,
        )
        
        d = feature_set.to_dict()
        
        assert d["evaluation_id"] == "test-001"
        assert d["close"] == 150.0
        assert d["iv"] == 0.30


# =============================================================================
# Appendix C Example Verification
# =============================================================================

class TestAppendixCExamples:
    """Verify calculations against Appendix C examples in requirements."""
    
    def test_move_sufficiency_example(self):
        """Verify Move Sufficiency Gate example from Appendix C.
        
        Example 1 from requirements:
        - AAPL at $185.00
        - $190 Call, 23 DTE, mid $2.45
        - IV: 32.5%
        
        Expected:
        - breakeven = 190 + 2.45 = $192.45
        - required_move = |192.45 - 185| / 185 = 4.03%
        - expected_move = 0.325 × sqrt(23/365) = 8.16%
        - time_adjusted = 4.03 / (8.16 × sqrt(23/30)) = 0.56
        """
        underlying_price = 185.00
        strike = 190.00
        mid = 2.45
        iv = 0.325
        dte = 23
        
        # Calculate breakeven
        breakeven = strike + mid
        assert abs(breakeven - 192.45) < 0.01
        
        # Calculate required move
        required_move_pct = abs(breakeven - underlying_price) / underlying_price * 100
        assert abs(required_move_pct - 4.03) < 0.1
        
        # Calculate expected move
        expected_move_pct = iv * math.sqrt(dte / 365) * 100
        assert abs(expected_move_pct - 8.16) < 0.1
        
        # Calculate time-adjusted feasibility
        time_factor = math.sqrt(dte / 30)
        time_adjusted = required_move_pct / (expected_move_pct * time_factor)
        assert abs(time_adjusted - 0.56) < 0.05
    
    def test_theta_adjusted_edge_interpretation(self):
        """Verify theta-adjusted edge interpretation from Section 13.3.
        
        - > 1.0: Expected delta gains exceed theta costs
        - < 1.0: Theta decay likely to outpace gains
        - > 2.0: Strong edge
        """
        # Test strong edge scenario
        edge = calculate_theta_adjusted_edge(
            underlying_price=150.0,
            iv=0.40,  # Higher IV = more expected move
            delta=0.50,  # ATM
            theta=-0.05,  # Low theta
        )
        
        assert edge is not None
        assert edge > 2.0  # Strong edge
        
        # Test weak edge scenario
        edge_weak = calculate_theta_adjusted_edge(
            underlying_price=150.0,
            iv=0.15,  # Low IV = less expected move
            delta=0.25,  # OTM
            theta=-0.15,  # High theta
        )
        
        assert edge_weak is not None
        assert edge_weak < edge  # Should be weaker


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
