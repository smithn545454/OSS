"""Feature models and data structures.

Contains the FeatureSet dataclass that holds all computed features
for a single evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.schemas import IVRegime, FeatureValue


@dataclass
class FeatureSet:
    """Complete set of computed features for an evaluation.
    
    Per Section 13 of OSS_Complete_Requirements.md, this contains all features
    needed for pillar scoring in Stage 5.
    """
    
    evaluation_id: str
    
    # =========================================================================
    # Category A: Underlying Technical Features (Section 13.2)
    # =========================================================================
    close: float
    sma20: Optional[float] = None
    sma50: Optional[float] = None
    return_5d: Optional[float] = None  # percent
    return_20d: Optional[float] = None  # percent
    trend_aligned_bullish: bool = False  # 1 if close > sma20 > sma50
    trend_aligned_bearish: bool = False  # 1 if close < sma20 < sma50
    atr14: Optional[float] = None  # dollars
    atr14_pct: Optional[float] = None  # percent
    
    # =========================================================================
    # Category B: Relative Strength Features (Section 13.2)
    # =========================================================================
    spy_return_5d: Optional[float] = None  # percent
    spy_return_20d: Optional[float] = None  # percent
    rs_5d: Optional[float] = None  # return_5d - spy_return_5d
    rs_20d: Optional[float] = None  # return_20d - spy_return_20d
    
    # =========================================================================
    # Category C: Volatility Features (Section 13.2)
    # =========================================================================
    rv20: Optional[float] = None  # decimal (e.g., 0.25 for 25%)
    iv: float = 0.0  # decimal (from contract)
    iv_rv_ratio: Optional[float] = None  # iv / rv20
    iv_percentile: Optional[float] = None  # 0-100 (252-day rank)
    iv_10d_change: Optional[float] = None  # percent change in IV over 10 days
    iv_regime: IVRegime = IVRegime.IV_NEUTRAL_REGIME
    
    # =========================================================================
    # Category D: Contract-Specific Features (Section 13.2)
    # =========================================================================
    mid: float = 0.0  # dollars
    spread_pct: float = 0.0  # percent
    theta_pct: float = 0.0  # abs(theta) / mid * 100
    breakeven_price: float = 0.0  # dollars
    required_move_pct: float = 0.0  # percent
    expected_move_pct: float = 0.0  # percent
    feasibility_ratio: float = 0.0  # required_move_pct / expected_move_pct
    time_adjusted_feasibility: float = 0.0  # see Section 13.2
    theta_adjusted_edge: Optional[float] = None  # see Section 13.3
    
    # =========================================================================
    # Category E: Liquidity Features (Section 13.2)
    # =========================================================================
    open_interest: int = 0
    volume: int = 0
    oi_5d_change_pct: Optional[float] = None  # percent
    
    # =========================================================================
    # Category F: Catalyst Features (Section 13.2)
    # =========================================================================
    days_to_earnings: Optional[int] = None  # days (null if unknown)
    recent_sec_filing: bool = False  # 1 if 8-K/10-Q/10-K in last 10 trading days
    
    # =========================================================================
    # Metadata
    # =========================================================================
    computed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    
    def to_feature_values(self) -> list[FeatureValue]:
        """Convert FeatureSet to list of FeatureValue records for storage.
        
        Returns:
            List of FeatureValue records, one per feature.
        """
        features = []
        
        # Category A
        features.extend([
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="close", value=self.close, units="dollars"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="sma20", value=self.sma20, units="dollars"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="sma50", value=self.sma50, units="dollars"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="return_5d", value=self.return_5d, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="return_20d", value=self.return_20d, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="trend_aligned_bullish", value=self.trend_aligned_bullish, units="boolean"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="trend_aligned_bearish", value=self.trend_aligned_bearish, units="boolean"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="atr14", value=self.atr14, units="dollars"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="atr14_pct", value=self.atr14_pct, units="percent"),
        ])
        
        # Category B
        features.extend([
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="spy_return_5d", value=self.spy_return_5d, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="spy_return_20d", value=self.spy_return_20d, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="rs_5d", value=self.rs_5d, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="rs_20d", value=self.rs_20d, units="percent"),
        ])
        
        # Category C
        features.extend([
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="rv20", value=self.rv20, units="decimal"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="iv", value=self.iv, units="decimal"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="iv_rv_ratio", value=self.iv_rv_ratio, units="ratio"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="iv_percentile", value=self.iv_percentile, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="iv_10d_change", value=self.iv_10d_change, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="iv_regime", value=self.iv_regime.value if isinstance(self.iv_regime, IVRegime) else self.iv_regime, units="enum"),
        ])
        
        # Category D
        features.extend([
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="mid", value=self.mid, units="dollars"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="spread_pct", value=self.spread_pct, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="theta_pct", value=self.theta_pct, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="breakeven_price", value=self.breakeven_price, units="dollars"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="required_move_pct", value=self.required_move_pct, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="expected_move_pct", value=self.expected_move_pct, units="percent"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="feasibility_ratio", value=self.feasibility_ratio, units="ratio"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="time_adjusted_feasibility", value=self.time_adjusted_feasibility, units="ratio"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="theta_adjusted_edge", value=self.theta_adjusted_edge, units="ratio"),
        ])
        
        # Category E
        features.extend([
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="open_interest", value=self.open_interest, units="contracts"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="volume", value=self.volume, units="contracts"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="oi_5d_change_pct", value=self.oi_5d_change_pct, units="percent"),
        ])
        
        # Category F
        features.extend([
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="days_to_earnings", value=self.days_to_earnings, units="days"),
            FeatureValue(evaluation_id=self.evaluation_id, feature_name="recent_sec_filing", value=self.recent_sec_filing, units="boolean"),
        ])
        
        return features
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for easy access."""
        return {
            "evaluation_id": self.evaluation_id,
            # Category A
            "close": self.close,
            "sma20": self.sma20,
            "sma50": self.sma50,
            "return_5d": self.return_5d,
            "return_20d": self.return_20d,
            "trend_aligned_bullish": self.trend_aligned_bullish,
            "trend_aligned_bearish": self.trend_aligned_bearish,
            "atr14": self.atr14,
            "atr14_pct": self.atr14_pct,
            # Category B
            "spy_return_5d": self.spy_return_5d,
            "spy_return_20d": self.spy_return_20d,
            "rs_5d": self.rs_5d,
            "rs_20d": self.rs_20d,
            # Category C
            "rv20": self.rv20,
            "iv": self.iv,
            "iv_rv_ratio": self.iv_rv_ratio,
            "iv_percentile": self.iv_percentile,
            "iv_10d_change": self.iv_10d_change,
            "iv_regime": self.iv_regime.value if isinstance(self.iv_regime, IVRegime) else self.iv_regime,
            # Category D
            "mid": self.mid,
            "spread_pct": self.spread_pct,
            "theta_pct": self.theta_pct,
            "breakeven_price": self.breakeven_price,
            "required_move_pct": self.required_move_pct,
            "expected_move_pct": self.expected_move_pct,
            "feasibility_ratio": self.feasibility_ratio,
            "time_adjusted_feasibility": self.time_adjusted_feasibility,
            "theta_adjusted_edge": self.theta_adjusted_edge,
            # Category E
            "open_interest": self.open_interest,
            "volume": self.volume,
            "oi_5d_change_pct": self.oi_5d_change_pct,
            # Category F
            "days_to_earnings": self.days_to_earnings,
            "recent_sec_filing": self.recent_sec_filing,
            # Metadata
            "computed_at": self.computed_at,
        }
