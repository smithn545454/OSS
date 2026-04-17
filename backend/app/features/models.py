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
    # Category G: Technical Indicators (for Directional pillar)
    # =========================================================================
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    ema_alignment: Optional[str] = None  # BULLISH_STACK, BEARISH_STACK, etc.
    rsi_14: Optional[float] = None
    macd_histogram: Optional[float] = None
    adx_14: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None
    obv_trend: Optional[str] = None  # RISING, FALLING, FLAT

    # =========================================================================
    # Category H: Pillar v4 Long-Range Technicals & Sector Context
    # =========================================================================
    ma_150: Optional[float] = None  # 150-day SMA (Stage 2 template)
    ma_200: Optional[float] = None  # 200-day SMA (Stage 2 template)
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    dist_to_52w_high_pct: Optional[float] = None
    dist_to_52w_low_pct: Optional[float] = None
    bb_width: Optional[float] = None  # current (upper-lower)/middle
    bb_width_percentile: Optional[float] = None  # 0-100 (252-day rank)

    sector: Optional[str] = None  # GICS sector, used to look up ETF proxy
    sector_rs_20d: Optional[float] = None  # sector ETF 20d return minus SPY

    # =========================================================================
    # Category I: Pillar v4 Catalyst Extensions (Move Potential)
    # =========================================================================
    historical_move_magnitude: Optional[float] = None  # mean |1-day post-earnings return| (%)
    historical_move_confidence: Optional[int] = None  # count of events used (0-4)

    # =========================================================================
    # Metadata
    # =========================================================================
    computed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def from_feature_values(
        cls, evaluation_id: str, feature_values: list[FeatureValue]
    ) -> "FeatureSet":
        """Reconstruct a FeatureSet from stored FeatureValue records.

        Args:
            evaluation_id: The evaluation this feature set belongs to.
            feature_values: List of FeatureValue records from FeatureValueTable.

        Returns:
            FeatureSet with all available fields populated.
        """
        fv_map: dict[str, Any] = {fv.feature_name: fv.value for fv in feature_values}

        def _float(key: str, default: Any = None) -> Optional[float]:
            v = fv_map.get(key)
            if v is None:
                return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        def _int(key: str, default: Any = None) -> Optional[int]:
            v = fv_map.get(key)
            if v is None:
                return default
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        def _bool(key: str, default: bool = False) -> bool:
            v = fv_map.get(key)
            if v is None:
                return default
            if isinstance(v, bool):
                return v
            return str(v).lower() in ("true", "1", "yes")

        def _str(key: str, default: Optional[str] = None) -> Optional[str]:
            v = fv_map.get(key)
            return str(v) if v is not None else default

        iv_regime_str = _str("iv_regime")
        try:
            iv_regime = IVRegime(iv_regime_str) if iv_regime_str else IVRegime.IV_NEUTRAL_REGIME
        except ValueError:
            iv_regime = IVRegime.IV_NEUTRAL_REGIME

        return cls(
            evaluation_id=evaluation_id,
            # Category A
            close=_float("close", 0.0),
            sma20=_float("sma20"),
            sma50=_float("sma50"),
            return_5d=_float("return_5d"),
            return_20d=_float("return_20d"),
            trend_aligned_bullish=_bool("trend_aligned_bullish"),
            trend_aligned_bearish=_bool("trend_aligned_bearish"),
            atr14=_float("atr14"),
            atr14_pct=_float("atr14_pct"),
            # Category B
            spy_return_5d=_float("spy_return_5d"),
            spy_return_20d=_float("spy_return_20d"),
            rs_5d=_float("rs_5d"),
            rs_20d=_float("rs_20d"),
            # Category C
            rv20=_float("rv20"),
            iv=_float("iv", 0.0),
            iv_rv_ratio=_float("iv_rv_ratio"),
            iv_percentile=_float("iv_percentile"),
            iv_10d_change=_float("iv_10d_change"),
            iv_regime=iv_regime,
            # Category D
            mid=_float("mid", 0.0),
            spread_pct=_float("spread_pct", 0.0),
            theta_pct=_float("theta_pct", 0.0),
            breakeven_price=_float("breakeven_price", 0.0),
            required_move_pct=_float("required_move_pct", 0.0),
            expected_move_pct=_float("expected_move_pct", 0.0),
            feasibility_ratio=_float("feasibility_ratio", 0.0),
            time_adjusted_feasibility=_float("time_adjusted_feasibility", 0.0),
            theta_adjusted_edge=_float("theta_adjusted_edge"),
            # Category E
            open_interest=_int("open_interest", 0),
            volume=_int("volume", 0),
            oi_5d_change_pct=_float("oi_5d_change_pct"),
            # Category F
            days_to_earnings=_int("days_to_earnings"),
            recent_sec_filing=_bool("recent_sec_filing"),
            # Category G
            ema_9=_float("ema_9"),
            ema_21=_float("ema_21"),
            ema_50=_float("ema_50"),
            ema_200=_float("ema_200"),
            ema_alignment=_str("ema_alignment"),
            rsi_14=_float("rsi_14"),
            macd_histogram=_float("macd_histogram"),
            adx_14=_float("adx_14"),
            plus_di=_float("plus_di"),
            minus_di=_float("minus_di"),
            obv_trend=_str("obv_trend"),
            # Category H — Pillar v4 long-range + sector
            ma_150=_float("ma_150"),
            ma_200=_float("ma_200"),
            high_52w=_float("high_52w"),
            low_52w=_float("low_52w"),
            dist_to_52w_high_pct=_float("dist_to_52w_high_pct"),
            dist_to_52w_low_pct=_float("dist_to_52w_low_pct"),
            bb_width=_float("bb_width"),
            bb_width_percentile=_float("bb_width_percentile"),
            sector=_str("sector"),
            sector_rs_20d=_float("sector_rs_20d"),
            # Category I — Pillar v4 catalyst extensions
            historical_move_magnitude=_float("historical_move_magnitude"),
            historical_move_confidence=_int("historical_move_confidence"),
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

        # Category G
        eid = self.evaluation_id
        features.extend([
            FeatureValue(evaluation_id=eid, feature_name="ema_9", value=self.ema_9, units="dollars"),
            FeatureValue(evaluation_id=eid, feature_name="ema_21", value=self.ema_21, units="dollars"),
            FeatureValue(evaluation_id=eid, feature_name="ema_50", value=self.ema_50, units="dollars"),
            FeatureValue(evaluation_id=eid, feature_name="ema_200", value=self.ema_200, units="dollars"),
            FeatureValue(evaluation_id=eid, feature_name="ema_alignment", value=self.ema_alignment, units="enum"),
            FeatureValue(evaluation_id=eid, feature_name="rsi_14", value=self.rsi_14, units="index"),
            FeatureValue(evaluation_id=eid, feature_name="macd_histogram", value=self.macd_histogram, units="dollars"),
            FeatureValue(evaluation_id=eid, feature_name="adx_14", value=self.adx_14, units="index"),
            FeatureValue(evaluation_id=eid, feature_name="plus_di", value=self.plus_di, units="index"),
            FeatureValue(evaluation_id=eid, feature_name="minus_di", value=self.minus_di, units="index"),
            FeatureValue(evaluation_id=eid, feature_name="obv_trend", value=self.obv_trend, units="enum"),
        ])

        # Category H — Pillar v4 long-range + sector
        features.extend([
            FeatureValue(evaluation_id=eid, feature_name="ma_150", value=self.ma_150, units="dollars"),
            FeatureValue(evaluation_id=eid, feature_name="ma_200", value=self.ma_200, units="dollars"),
            FeatureValue(evaluation_id=eid, feature_name="high_52w", value=self.high_52w, units="dollars"),
            FeatureValue(evaluation_id=eid, feature_name="low_52w", value=self.low_52w, units="dollars"),
            FeatureValue(evaluation_id=eid, feature_name="dist_to_52w_high_pct", value=self.dist_to_52w_high_pct, units="percent"),
            FeatureValue(evaluation_id=eid, feature_name="dist_to_52w_low_pct", value=self.dist_to_52w_low_pct, units="percent"),
            FeatureValue(evaluation_id=eid, feature_name="bb_width", value=self.bb_width, units="ratio"),
            FeatureValue(evaluation_id=eid, feature_name="bb_width_percentile", value=self.bb_width_percentile, units="percent"),
            FeatureValue(evaluation_id=eid, feature_name="sector", value=self.sector, units="string"),
            FeatureValue(evaluation_id=eid, feature_name="sector_rs_20d", value=self.sector_rs_20d, units="percent"),
        ])

        # Category I — Pillar v4 catalyst extensions
        features.extend([
            FeatureValue(evaluation_id=eid, feature_name="historical_move_magnitude", value=self.historical_move_magnitude, units="percent"),
            FeatureValue(evaluation_id=eid, feature_name="historical_move_confidence", value=self.historical_move_confidence, units="count"),
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
            # Category G
            "ema_9": self.ema_9,
            "ema_21": self.ema_21,
            "ema_50": self.ema_50,
            "ema_200": self.ema_200,
            "ema_alignment": self.ema_alignment,
            "rsi_14": self.rsi_14,
            "macd_histogram": self.macd_histogram,
            "adx_14": self.adx_14,
            "plus_di": self.plus_di,
            "minus_di": self.minus_di,
            "obv_trend": self.obv_trend,
            # Category H — Pillar v4 long-range + sector
            "ma_150": self.ma_150,
            "ma_200": self.ma_200,
            "high_52w": self.high_52w,
            "low_52w": self.low_52w,
            "dist_to_52w_high_pct": self.dist_to_52w_high_pct,
            "dist_to_52w_low_pct": self.dist_to_52w_low_pct,
            "bb_width": self.bb_width,
            "bb_width_percentile": self.bb_width_percentile,
            "sector": self.sector,
            "sector_rs_20d": self.sector_rs_20d,
            # Category I — Pillar v4 catalyst extensions
            "historical_move_magnitude": self.historical_move_magnitude,
            "historical_move_confidence": self.historical_move_confidence,
            # Metadata
            "computed_at": self.computed_at,
        }
