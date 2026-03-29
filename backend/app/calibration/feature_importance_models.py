"""Feature importance analysis result models.

Pure data containers for statistical analysis results.
No database imports, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class QuintileStats:
    """Performance stats for a single feature quintile bucket."""

    quintile: int  # 1 (lowest) to 5 (highest)
    range_low: float
    range_high: float
    n: int
    win_rate: float  # 0-100
    avg_return: float  # mean P&L %
    median_return: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "quintile": self.quintile,
            "range_low": round(self.range_low, 4),
            "range_high": round(self.range_high, 4),
            "n": self.n,
            "win_rate": round(self.win_rate, 1),
            "avg_return": round(self.avg_return, 2),
            "median_return": round(self.median_return, 2),
        }


@dataclass
class FeatureStats:
    """Statistical importance results for a single feature."""

    feature_name: str
    display_name: str
    pillar: Optional[str]  # "directional", "volatility", "structure", or None
    n_valid: int  # positions with non-null values
    pearson_r: float  # correlation with outcome
    p_value: float  # permutation-based significance
    is_significant: bool  # p < 0.05
    win_mean: Optional[float]  # mean value for winning trades
    loss_mean: Optional[float]  # mean value for losing trades
    difference: Optional[float]  # win_mean - loss_mean
    effect_size: Optional[float]  # Cohen's d
    direction: str  # "higher_is_better" or "lower_is_better" or "unclear"
    quintiles: list[QuintileStats] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "display_name": self.display_name,
            "pillar": self.pillar,
            "n_valid": self.n_valid,
            "pearson_r": round(self.pearson_r, 4),
            "p_value": round(self.p_value, 4),
            "is_significant": self.is_significant,
            "win_mean": round(self.win_mean, 4) if self.win_mean is not None else None,
            "loss_mean": round(self.loss_mean, 4) if self.loss_mean is not None else None,
            "difference": round(self.difference, 4) if self.difference is not None else None,
            "effect_size": round(self.effect_size, 4) if self.effect_size is not None else None,
            "direction": self.direction,
            "quintiles": [q.to_dict() for q in self.quintiles],
        }


@dataclass
class FeaturePairStats:
    """Interaction analysis for a pair of features."""

    feature_a: str
    feature_a_display: str
    feature_b: str
    feature_b_display: str
    both_high_wr: float
    both_high_avg_return: float
    both_high_n: int
    a_only_high_wr: float
    a_only_high_n: int
    b_only_high_wr: float
    b_only_high_n: int
    both_low_wr: float
    both_low_n: int
    interaction_lift: float  # both_favorable_wr - max(a_only, b_only)
    a_direction: str = "high"  # "high" or "low" — what's favorable for feature A
    b_direction: str = "high"  # "high" or "low" — what's favorable for feature B

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_a": self.feature_a,
            "feature_a_display": self.feature_a_display,
            "feature_b": self.feature_b,
            "feature_b_display": self.feature_b_display,
            "both_high_wr": round(self.both_high_wr, 1),
            "both_high_avg_return": round(self.both_high_avg_return, 2),
            "both_high_n": self.both_high_n,
            "a_only_high_wr": round(self.a_only_high_wr, 1),
            "a_only_high_n": self.a_only_high_n,
            "b_only_high_wr": round(self.b_only_high_wr, 1),
            "b_only_high_n": self.b_only_high_n,
            "both_low_wr": round(self.both_low_wr, 1),
            "both_low_n": self.both_low_n,
            "a_direction": self.a_direction,
            "b_direction": self.b_direction,
            "interaction_lift": round(self.interaction_lift, 1),
        }


@dataclass
class WeightComparison:
    """Comparison of current vs empirical weight for a pillar subscore."""

    pillar: str
    subscore: str
    display_name: str
    current_weight: float  # from policy (e.g., 0.30)
    empirical_importance: float  # normalized |pearson_r| within pillar
    delta: float  # empirical - current
    recommendation: str  # "increase", "decrease", "aligned"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pillar": self.pillar,
            "subscore": self.subscore,
            "display_name": self.display_name,
            "current_weight": round(self.current_weight, 3),
            "empirical_importance": round(self.empirical_importance, 3),
            "delta": round(self.delta, 3),
            "recommendation": self.recommendation,
        }


@dataclass
class TemporalWindow:
    """Feature correlations for a rolling time window."""

    window_start: str
    window_end: str
    n_positions: int
    feature_correlations: dict[str, Optional[float]]  # feature_name → pearson_r

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "n_positions": self.n_positions,
            "feature_correlations": {
                k: round(v, 4) if v is not None else None
                for k, v in self.feature_correlations.items()
            },
        }


@dataclass
class FeatureImportanceResult:
    """Complete feature importance analysis result."""

    analysis_id: str
    created_at: str
    period: str  # "30d", "90d", "all"
    outcome: str  # "pnl" or "mfe"
    n_positions: int
    overall_win_rate: float
    overall_avg_return: float
    features: list[FeatureStats] = field(default_factory=list)
    interactions: list[FeaturePairStats] = field(default_factory=list)
    weight_comparisons: list[WeightComparison] = field(default_factory=list)
    temporal_windows: list[TemporalWindow] = field(default_factory=list)
    segments: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    narrative: Optional[str] = None
    status: str = "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "created_at": self.created_at,
            "period": self.period,
            "outcome": self.outcome,
            "n_positions": self.n_positions,
            "overall_win_rate": round(self.overall_win_rate, 1),
            "overall_avg_return": round(self.overall_avg_return, 2),
            "features": [f.to_dict() for f in self.features],
            "interactions": [p.to_dict() for p in self.interactions],
            "weight_comparisons": [w.to_dict() for w in self.weight_comparisons],
            "temporal_windows": [t.to_dict() for t in self.temporal_windows],
            "segments": self.segments,
            "narrative": self.narrative,
            "status": self.status,
        }
