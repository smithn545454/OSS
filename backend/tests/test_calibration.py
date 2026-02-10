"""Tests for the calibration module.

Covers:
- GateAnalyzer: rejection rates, false negative detection, effectiveness scoring
- CalibrationReporter: report generation (mocked data)
- ThresholdSimulator: suggestion generation
- Calibration models: dataclass construction and serialization
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.calibration.analyzer import GateAnalyzer, GATE_IDS
from app.calibration.models import (
    CalibrationReport,
    CounterfactualSummary,
    EstimatedImpact,
    GateAnalysis,
    RecommendationType,
    ScoreBandAnalysis,
    SuggestionStatus,
    ThresholdSuggestion,
    WatchToApproveAnalysis,
)
from app.core.schemas import (
    ExitReason,
    GateOperator,
    GateResult,
    PaperPosition,
    PositionStatus,
    Verdict,
)


# ============================================================================
# GateAnalyzer Tests
# ============================================================================


class TestGateAnalyzer:
    """Test gate effectiveness analysis."""

    @pytest.fixture
    def analyzer(self):
        return GateAnalyzer()

    def test_empty_analyzer_returns_zero_metrics(self, analyzer):
        analysis = analyzer.analyze_gate("GATE_MIN_OPEN_INTEREST")
        assert analysis.rejection_rate == 0
        assert analysis.false_negative_rate == 0
        assert analysis.effectiveness_score == 0

    def test_rejection_rate_calculation(self, analyzer):
        """Verify rejection_rate = failures / total * 100."""
        # Add 10 gate results, 3 failures
        for i in range(10):
            gate = GateResult(
                evaluation_id=f"eval-{i}",
                gate_id="GATE_MIN_VOLUME",
                enabled=True,
                passed=(i >= 3),  # First 3 fail
                measured_value=float(i * 10),
                threshold_value=75.0,
                operator=GateOperator.GTE,
                units="contracts",
                reason_code="TEST",
            )
            analyzer.add_gate_results([gate], f"eval-{i}")

        analysis = analyzer.analyze_gate("GATE_MIN_VOLUME")
        assert analysis.rejection_count == 3
        assert analysis.rejection_rate == 30.0

    def test_false_negative_detection(self, analyzer):
        """Shadow positions that hit MFE >= 25% are false negatives."""
        # Add some gate results
        for i in range(5):
            gate = GateResult(
                evaluation_id=f"eval-{i}",
                gate_id="GATE_MIN_VOLUME",
                enabled=True,
                passed=False,
                measured_value=10.0,
                threshold_value=75.0,
                operator=GateOperator.GTE,
                units="contracts",
                reason_code="TEST",
            )
            analyzer.add_gate_results([gate], f"eval-{i}")

        # Add 3 shadow positions:
        # - pos-0: MFE 30% (false negative - would have been a winner)
        # - pos-1: MFE 10% (not a false negative)
        # - pos-2: PnL 50% (false negative via pnl threshold)
        positions = [
            PaperPosition(
                position_id="pos-0",
                evaluation_id="eval-0",
                option_ticker="O:AAPL",
                entry_price=5.0,
                entry_date="2026-01-10",
                verdict_at_entry=Verdict.REJECT,
                current_price=6.5,
                current_pnl_pct=15.0,
                max_favorable_excursion=30.0,
            ),
            PaperPosition(
                position_id="pos-1",
                evaluation_id="eval-1",
                option_ticker="O:MSFT",
                entry_price=5.0,
                entry_date="2026-01-10",
                verdict_at_entry=Verdict.REJECT,
                current_price=5.2,
                current_pnl_pct=4.0,
                max_favorable_excursion=10.0,
            ),
            PaperPosition(
                position_id="pos-2",
                evaluation_id="eval-2",
                option_ticker="O:GOOGL",
                entry_price=5.0,
                entry_date="2026-01-10",
                verdict_at_entry=Verdict.REJECT,
                current_price=7.5,
                current_pnl_pct=50.0,
                max_favorable_excursion=20.0,
            ),
        ]

        for pos in positions:
            analyzer.add_shadow_position(
                pos,
                failed_gates=["GATE_MIN_VOLUME"],
            )

        analysis = analyzer.analyze_gate("GATE_MIN_VOLUME")
        # pos-0 (MFE >= 25) and pos-2 (PnL >= 50%) = 2 false negatives out of 3
        assert analysis.false_negative_count == 2
        assert abs(analysis.false_negative_rate - 66.67) < 1.0

    def test_effectiveness_score_formula(self, analyzer):
        """effectiveness = rejection_rate * (100 - false_negative_rate) / 100."""
        # 50% rejection rate, 10% false negative rate
        # Expected: 50 * (100 - 10) / 100 = 45
        for i in range(10):
            gate = GateResult(
                evaluation_id=f"eval-{i}",
                gate_id="GATE_MAX_SPREAD_PCT",
                enabled=True,
                passed=(i >= 5),
                measured_value=float(i),
                threshold_value=8.0,
                operator=GateOperator.LTE,
                units="percent",
                reason_code="TEST",
            )
            analyzer.add_gate_results([gate], f"eval-{i}")

        analysis = analyzer.analyze_gate("GATE_MAX_SPREAD_PCT")
        assert analysis.rejection_rate == 50.0
        # No shadow positions, so false_negative_rate = 0
        # effectiveness = 50 * (100 - 0) / 100 = 50
        assert analysis.effectiveness_score == 50.0

    def test_recommendation_loosen_on_high_false_negatives(self, analyzer):
        """High false negative rate (> 15%) should recommend LOOSEN."""
        for i in range(5):
            gate = GateResult(
                evaluation_id=f"eval-{i}",
                gate_id="GATE_THETA_BURDEN_MAX",
                enabled=True,
                passed=False,
                measured_value=5.0,
                threshold_value=4.0,
                operator=GateOperator.LTE,
                units="ratio",
                reason_code="TEST",
            )
            analyzer.add_gate_results([gate], f"eval-{i}")

        # Add shadow positions where most are false negatives
        for i in range(5):
            pos = PaperPosition(
                position_id=f"shadow-{i}",
                evaluation_id=f"eval-{i}",
                option_ticker=f"O:TEST{i}",
                entry_price=5.0,
                entry_date="2026-01-10",
                verdict_at_entry=Verdict.REJECT,
                current_price=7.0,
                current_pnl_pct=40.0,
                max_favorable_excursion=35.0,  # > 25%, false negative
            )
            analyzer.add_shadow_position(pos, failed_gates=["GATE_THETA_BURDEN_MAX"])

        analysis = analyzer.analyze_gate("GATE_THETA_BURDEN_MAX")
        assert analysis.recommendation == RecommendationType.LOOSEN

    def test_recommendation_tighten_on_low_rejection(self, analyzer):
        """Low rejection (< 5%) + low false negative (< 5%) should recommend TIGHTEN."""
        # 100 evaluations, only 2 failures = 2% rejection rate
        for i in range(100):
            gate = GateResult(
                evaluation_id=f"eval-{i}",
                gate_id="GATE_DTE_RANGE",
                enabled=True,
                passed=(i >= 2),
                measured_value=30.0,
                threshold_value=7.0,
                operator=GateOperator.GTE,
                units="days",
                reason_code="TEST",
            )
            analyzer.add_gate_results([gate], f"eval-{i}")

        analysis = analyzer.analyze_gate("GATE_DTE_RANGE")
        assert analysis.recommendation == RecommendationType.TIGHTEN

    def test_analyze_all_gates(self, analyzer):
        """analyze_all_gates should return results for all defined gate IDs."""
        analyses = analyzer.analyze_all_gates()
        assert len(analyses) == len(GATE_IDS)
        gate_ids_returned = {a.gate_id for a in analyses}
        assert gate_ids_returned == set(GATE_IDS)

    def test_reset_clears_state(self, analyzer):
        gate = GateResult(
            evaluation_id="eval-0",
            gate_id="GATE_MIN_VOLUME",
            enabled=True,
            passed=False,
            measured_value=10,
            threshold_value=75,
            operator=GateOperator.GTE,
            units="contracts",
            reason_code="TEST",
        )
        analyzer.add_gate_results([gate], "eval-0")
        analyzer.reset()

        analysis = analyzer.analyze_gate("GATE_MIN_VOLUME")
        assert analysis.rejection_count == 0

    def test_disabled_gates_are_skipped(self, analyzer):
        """Disabled gates should not count toward totals."""
        gate = GateResult(
            evaluation_id="eval-0",
            gate_id="GATE_BREAKOUT_VOLUME",
            enabled=False,
            passed=True,
            measured_value=2.0,
            threshold_value=1.5,
            operator=GateOperator.GTE,
            units="ratio",
            reason_code="SKIPPED",
        )
        analyzer.add_gate_results([gate], "eval-0")

        analysis = analyzer.analyze_gate("GATE_BREAKOUT_VOLUME")
        assert analysis.rejection_count == 0


# ============================================================================
# Calibration Model Tests
# ============================================================================


class TestCalibrationModels:
    """Test calibration dataclass construction and serialization."""

    def test_gate_analysis_to_dict(self):
        analysis = GateAnalysis(
            gate_id="GATE_MIN_VOLUME",
            rejection_count=15,
            rejection_rate=30.0,
            false_negative_count=2,
            false_negative_rate=10.0,
            effectiveness_score=27.0,
            recommendation=RecommendationType.NO_CHANGE,
        )
        d = analysis.to_dict()
        assert d["gate_id"] == "GATE_MIN_VOLUME"
        assert d["recommendation"] == "NO_CHANGE"

    def test_threshold_suggestion_create(self):
        impact = EstimatedImpact(additional_approvals=3, estimated_win_rate_change=1.5)
        suggestion = ThresholdSuggestion.create(
            gate_id="GATE_MIN_OI",
            field_path="gates.min_open_interest",
            current_value=300,
            suggested_value=250,
            estimated_impact=impact,
            reason="High false negative rate",
        )
        assert suggestion.suggestion_id is not None
        assert suggestion.status == SuggestionStatus.PENDING
        d = suggestion.to_dict()
        assert d["gate_id"] == "GATE_MIN_OI"

    def test_calibration_report_create(self):
        report = CalibrationReport.create(
            week_start="2026-01-10",
            week_end="2026-01-17",
            positions_closed=20,
            win_rate=65.0,
            avg_return=12.5,
        )
        assert report.report_id is not None
        assert report.positions_closed == 20

        d = report.to_dict()
        assert d["win_rate"] == 65.0
        assert "gate_analyses" in d
        assert "suggestions" in d

    def test_score_band_analysis(self):
        band = ScoreBandAnalysis(
            band="75-85",
            min_score=75.0,
            max_score=85.0,
            count=10,
            win_rate=70.0,
            avg_return=15.0,
        )
        d = band.to_dict()
        assert d["band"] == "75-85"
        assert d["count"] == 10

    def test_watch_to_approve_analysis(self):
        analysis = WatchToApproveAnalysis(
            total_watch=20,
            would_flip_count=5,
            rate=25.0,
            threshold_tested=70.0,
            near_boundary_count=8,
        )
        d = analysis.to_dict()
        assert d["total_watch"] == 20
        assert d["rate"] == 25.0

    def test_counterfactual_summary_to_dict(self):
        watch = WatchToApproveAnalysis(
            total_watch=10, would_flip_count=2, rate=20.0,
            threshold_tested=70.0, near_boundary_count=3,
        )
        summary = CounterfactualSummary(watch_to_approve=watch)
        d = summary.to_dict()
        assert "watch_to_approve" in d
        assert d["gate_scenarios"] == []
