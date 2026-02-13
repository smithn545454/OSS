"""Calibration data models.

Per Section 20 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class SuggestionStatus(str, Enum):
    """Status of a threshold suggestion."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RecommendationType(str, Enum):
    """Type of recommendation for a gate."""
    TIGHTEN = "TIGHTEN"
    LOOSEN = "LOOSEN"
    NO_CHANGE = "NO_CHANGE"


@dataclass
class GateAnalysis:
    """Analysis results for a single gate.
    
    Per Section 20.2 - Gate Effectiveness Analysis.
    """
    gate_id: str
    rejection_count: int
    rejection_rate: float  # 0-100
    false_negative_count: int  # REJECTs that would have been winners
    false_negative_rate: float  # 0-100
    effectiveness_score: float  # rejection_rate × (100 - false_negative_rate)
    recommendation: RecommendationType
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "gate_id": self.gate_id,
            "rejection_count": self.rejection_count,
            "rejection_rate": round(self.rejection_rate, 2),
            "false_negative_count": self.false_negative_count,
            "false_negative_rate": round(self.false_negative_rate, 2),
            "effectiveness_score": round(self.effectiveness_score, 2),
            "recommendation": self.recommendation.value,
        }


@dataclass
class EstimatedImpact:
    """Estimated impact of applying a threshold suggestion."""
    additional_approvals: int
    estimated_win_rate_change: float  # percentage points
    
    def to_dict(self) -> dict:
        return {
            "additional_approvals": self.additional_approvals,
            "estimated_win_rate_change": round(self.estimated_win_rate_change, 2),
        }


@dataclass
class ThresholdSuggestion:
    """A suggested threshold adjustment.
    
    Per Section 20.3 - Calibration Report Format.
    """
    suggestion_id: str
    gate_id: str
    field_path: str  # e.g., "gates.min_open_interest"
    current_value: float
    suggested_value: float
    estimated_impact: EstimatedImpact
    status: SuggestionStatus = SuggestionStatus.PENDING
    reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    @staticmethod
    def create(
        gate_id: str,
        field_path: str,
        current_value: float,
        suggested_value: float,
        estimated_impact: EstimatedImpact,
        reason: str = "",
    ) -> ThresholdSuggestion:
        """Create a new threshold suggestion with generated ID."""
        return ThresholdSuggestion(
            suggestion_id=str(uuid.uuid4()),
            gate_id=gate_id,
            field_path=field_path,
            current_value=current_value,
            suggested_value=suggested_value,
            estimated_impact=estimated_impact,
            reason=reason,
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "suggestion_id": self.suggestion_id,
            "gate_id": self.gate_id,
            "field_path": self.field_path,
            "current_value": self.current_value,
            "suggested_value": self.suggested_value,
            "estimated_impact": self.estimated_impact.to_dict(),
            "status": self.status.value,
            "reason": self.reason,
            "created_at": self.created_at,
        }


@dataclass
class CounterfactualResult:
    """Result of a gate counterfactual simulation (verdict-level transitions)."""
    gate_id: str
    original_value: float
    new_value: float
    scenario_label: str
    verdict_changes: dict = field(default_factory=dict)


@dataclass
class ScoreThresholdResult:
    """Result of simulating a score threshold change."""
    approve_threshold: float
    watch_threshold: float
    verdict_changes: dict = field(default_factory=dict)
    counterfactual_counts: dict = field(default_factory=dict)


@dataclass
class CounterfactualSummary:
    """Summary of counterfactual simulations."""
    gate_scenarios: list = field(default_factory=list)
    score_scenarios: list = field(default_factory=list)
    watch_to_approve: Optional[WatchToApproveAnalysis] = None

    def to_dict(self) -> dict:
        return {
            "gate_scenarios": self.gate_scenarios,
            "score_scenarios": self.score_scenarios,
            "watch_to_approve": self.watch_to_approve.to_dict() if self.watch_to_approve else None,
        }


@dataclass
class WatchToApproveAnalysis:
    """Result of analyzing WATCH evaluations near the APPROVE boundary."""
    total_watch: int = 0
    rate: float = 0.0
    would_flip_count: int = 0
    threshold_tested: float = 0.0
    near_boundary_count: int = 0

    def to_dict(self) -> dict:
        return {
            "total_watch": self.total_watch,
            "rate": round(self.rate, 2),
            "would_flip_count": self.would_flip_count,
            "threshold_tested": self.threshold_tested,
            "near_boundary_count": self.near_boundary_count,
        }


@dataclass
class ScoreBandAnalysis:
    """Performance analysis for a score band.
    
    Per Section 20.2 - Score Band Calibration.
    """
    band: str  # e.g., "60-65", "65-75", "75-85", "85+"
    min_score: float
    max_score: float
    count: int
    win_rate: float  # 0-100
    avg_return: float  # percentage
    
    def to_dict(self) -> dict:
        return {
            "band": self.band,
            "min_score": self.min_score,
            "max_score": self.max_score,
            "count": self.count,
            "win_rate": round(self.win_rate, 2),
            "avg_return": round(self.avg_return, 2),
        }


@dataclass
class CalibrationReport:
    """Weekly calibration report.
    
    Per Section 20.3 - Report Format.
    """
    report_id: str
    week_start: str  # ISO date
    week_end: str  # ISO date
    generated_at: str
    positions_closed: int
    win_rate: float  # 0-100
    avg_return: float  # percentage
    gate_analyses: list[GateAnalysis] = field(default_factory=list)
    suggestions: list[ThresholdSuggestion] = field(default_factory=list)
    score_band_analysis: list[ScoreBandAnalysis] = field(default_factory=list)
    
    @staticmethod
    def create(
        week_start: str,
        week_end: str,
        positions_closed: int,
        win_rate: float,
        avg_return: float,
    ) -> CalibrationReport:
        """Create a new calibration report with generated ID."""
        return CalibrationReport(
            report_id=str(uuid.uuid4()),
            week_start=week_start,
            week_end=week_end,
            generated_at=datetime.utcnow().isoformat(),
            positions_closed=positions_closed,
            win_rate=win_rate,
            avg_return=avg_return,
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "report_id": self.report_id,
            "week_start": self.week_start,
            "week_end": self.week_end,
            "generated_at": self.generated_at,
            "positions_closed": self.positions_closed,
            "win_rate": round(self.win_rate, 2),
            "avg_return": round(self.avg_return, 2),
            "gate_analyses": [ga.to_dict() for ga in self.gate_analyses],
            "suggestions": [s.to_dict() for s in self.suggestions],
            "score_band_analysis": [sb.to_dict() for sb in self.score_band_analysis],
        }
    
    def to_dynamodb_item(self) -> dict:
        """Convert to DynamoDB item format."""
        return self.to_dict()
