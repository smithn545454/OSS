"""Tests for calibration/reporter.py.

Covers CalibrationReporter._load_data, _calculate_summary_stats,
_analyze_watch_to_approve, _build_eval_decisions, _analyze_score_bands,
_generate_counterfactual_summary, and run_weekly_calibration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.calibration.models import (
    GateAnalysis,
    RecommendationType,
)
from app.calibration.reporter import CalibrationReporter, run_weekly_calibration
from app.core.schemas import PositionStatus


def _make_position(
    position_id: str,
    pnl: float = 10.0,
    status: str = "CLOSED",
    exit_date: str = "2026-01-15",
    evaluation_id: str = "eval-1",
    verdict_at_entry: str = "APPROVE",
):
    p = MagicMock()
    p.position_id = position_id
    p.evaluation_id = evaluation_id
    p.status = PositionStatus.CLOSED if status == "CLOSED" else PositionStatus.OPEN
    p.exit_date = exit_date
    p.current_pnl_pct = pnl
    p.verdict_at_entry = MagicMock(value=verdict_at_entry)
    return p


class TestCalculateSummaryStats:

    def test_no_positions(self):
        reporter = CalibrationReporter()
        win_rate, avg_return = reporter._calculate_summary_stats([])
        assert win_rate == 0.0
        assert avg_return == 0.0

    def test_all_winners(self):
        reporter = CalibrationReporter()
        positions = [_make_position(f"p{i}", pnl=10.0) for i in range(5)]
        win_rate, avg_return = reporter._calculate_summary_stats(positions)
        assert win_rate == 100.0
        assert avg_return == 10.0

    def test_mixed_positions(self):
        reporter = CalibrationReporter()
        positions = [
            _make_position("p1", pnl=20.0),
            _make_position("p2", pnl=-5.0),
            _make_position("p3", pnl=15.0),
            _make_position("p4", pnl=-10.0),
        ]
        win_rate, avg_return = reporter._calculate_summary_stats(positions)
        assert win_rate == 50.0
        assert avg_return == 5.0


class TestBuildEvalDecisions:

    def test_builds_decisions(self):
        reporter = CalibrationReporter()
        reporter._evaluations = [
            {
                "evaluation_id": "e1",
                "decision": {
                    "final_score": 85.0,
                    "verdict": "APPROVE",
                    "failed_gates": [],
                },
            },
            {
                "evaluation_id": "e2",
                "decision": {
                    "final_score": 50.0,
                    "verdict": "REJECT",
                    "failed_gates": ["GATE_MIN_VOLUME"],
                },
            },
            {
                "evaluation_id": "e3",
                "decision": None,
            },
        ]
        result = reporter._build_eval_decisions()
        assert "e1" in result
        assert "e2" in result
        assert "e3" not in result
        assert result["e1"]["verdict"] == "APPROVE"

    def test_empty_evaluations(self):
        reporter = CalibrationReporter()
        reporter._evaluations = []
        result = reporter._build_eval_decisions()
        assert result == {}


class TestAnalyzeWatchToApprove:

    def test_no_watch_evals(self):
        reporter = CalibrationReporter()
        reporter._evaluations = []
        reporter._policy = None
        result = reporter._analyze_watch_to_approve()
        assert result.total_watch == 0
        assert result.rate == 0.0

    def test_watch_evals_near_boundary(self):
        reporter = CalibrationReporter()
        reporter._evaluations = [
            {
                "evaluation_id": "e1",
                "decision": {
                    "final_score": 72.0,
                    "verdict": "WATCH",
                    "failed_gates": [],
                },
            },
            {
                "evaluation_id": "e2",
                "decision": {
                    "final_score": 65.0,
                    "verdict": "WATCH",
                    "failed_gates": [],
                },
            },
            {
                "evaluation_id": "e3",
                "decision": {
                    "final_score": 73.0,
                    "verdict": "WATCH",
                    "failed_gates": ["GATE_MIN_VOL"],
                },
            },
        ]
        reporter._policy = None
        result = reporter._analyze_watch_to_approve()
        assert result.total_watch == 3
        # e1: score=72 >= test_threshold(70), no failed gates => would flip
        # e2: score=65 < 70 => no flip
        # e3: score=73, but has failed gates => no flip
        assert result.would_flip_count == 1

    def test_watch_with_custom_policy_threshold(self):
        reporter = CalibrationReporter()
        reporter._evaluations = [
            {
                "evaluation_id": "e1",
                "decision": {
                    "final_score": 62.0,
                    "verdict": "WATCH",
                    "failed_gates": [],
                },
            },
        ]
        mock_policy = MagicMock()
        mock_policy.config.decision.approve_threshold = 65.0
        reporter._policy = mock_policy
        result = reporter._analyze_watch_to_approve()
        # test_threshold = 65 - 5 = 60; score 62 >= 60 => flip
        assert result.would_flip_count == 1


class TestAnalyzeScoreBands:

    def test_empty_positions(self):
        reporter = CalibrationReporter()
        reporter._evaluations = []
        result = reporter._analyze_score_bands([])
        assert len(result) > 0
        for band in result:
            assert band.count == 0

    def test_positions_in_bands(self):
        reporter = CalibrationReporter()
        reporter._evaluations = [
            {"evaluation_id": "eval-1", "decision": {"final_score": 82.0}},
            {"evaluation_id": "eval-2", "decision": {"final_score": 92.0}},
        ]
        p1 = _make_position("p1", pnl=15.0, evaluation_id="eval-1")
        p2 = _make_position("p2", pnl=-5.0, evaluation_id="eval-2")
        result = reporter._analyze_score_bands([p1, p2])
        # At least some bands should have data
        total_count = sum(b.count for b in result)
        assert total_count == 2


class TestGenerateCounterfactualSummary:

    def test_no_simulator(self):
        reporter = CalibrationReporter()
        reporter._evaluations = []
        reporter._policy = None
        reporter._simulator = None
        result = reporter._generate_counterfactual_summary([])
        assert result is not None
        assert result.gate_scenarios == []
        assert result.score_scenarios == []

    def test_with_simulator_and_gate_analyses(self):
        reporter = CalibrationReporter()
        reporter._evaluations = []
        reporter._policy = None

        mock_sim = MagicMock()
        mock_sim.simulate_gate_counterfactual.return_value = MagicMock()
        mock_sim.simulate_score_threshold.return_value = MagicMock()
        reporter._simulator = mock_sim

        analyses = [
            GateAnalysis(
                gate_id="GATE_MIN_VOLUME",
                rejection_count=20,
                rejection_rate=40.0,
                false_negative_count=5,
                false_negative_rate=25.0,
                effectiveness_score=20.0,
                recommendation=RecommendationType.LOOSEN,
            ),
            GateAnalysis(
                gate_id="GATE_MIN_OI",
                rejection_count=5,
                rejection_rate=10.0,
                false_negative_count=0,
                false_negative_rate=0.0,
                effectiveness_score=80.0,
                recommendation=RecommendationType.TIGHTEN,
            ),
            GateAnalysis(
                gate_id="GATE_DTE_RANGE",
                rejection_count=10,
                rejection_rate=20.0,
                false_negative_count=1,
                false_negative_rate=5.0,
                effectiveness_score=50.0,
                recommendation=RecommendationType.NO_CHANGE,
            ),
        ]

        result = reporter._generate_counterfactual_summary(analyses)
        assert result is not None
        # 2 gate scenarios (LOOSEN + TIGHTEN), 0 for NO_CHANGE
        assert len(result.gate_scenarios) == 2
        # 2 score scenarios (threshold 70, 80)
        assert len(result.score_scenarios) == 2


class TestLoadData:

    @pytest.mark.asyncio
    async def test_load_data(self):
        reporter = CalibrationReporter()

        with patch("app.calibration.reporter.PolicyTable") as mock_policy_table, \
             patch("app.calibration.reporter.PaperPositionTable") as mock_pos_table, \
             patch("app.calibration.reporter.EvaluationTable") as mock_eval_table, \
             patch("app.calibration.reporter.GateResultTable") as mock_gate_table:

            mock_policy_table.get_active = AsyncMock(return_value=MagicMock())
            mock_pos_table.list_all = AsyncMock(return_value=[])
            mock_eval_table.list_by_verdict = AsyncMock(return_value=[])
            mock_gate_table.list_by_evaluation = AsyncMock(return_value=[])

            await reporter._load_data()

        assert reporter._policy is not None
        assert reporter._evaluations == []

    @pytest.mark.asyncio
    async def test_load_data_with_evaluations(self):
        reporter = CalibrationReporter()

        with patch("app.calibration.reporter.PolicyTable") as mock_policy_table, \
             patch("app.calibration.reporter.PaperPositionTable") as mock_pos_table, \
             patch("app.calibration.reporter.EvaluationTable") as mock_eval_table, \
             patch("app.calibration.reporter.GateResultTable") as mock_gate_table:

            mock_policy_table.get_active = AsyncMock(return_value=MagicMock())
            mock_pos_table.list_all = AsyncMock(return_value=[])
            mock_eval_table.list_by_verdict = AsyncMock(return_value=[
                {"evaluation_id": "e1"},
                {"evaluation_id": "e2"},
            ])
            mock_gate_table.list_by_evaluation = AsyncMock(return_value=[])

            await reporter._load_data()

        # approve + watch + reject = 3 calls, each returns 2
        assert len(reporter._evaluations) == 6


class TestRunWeeklyCalibration:

    @pytest.mark.asyncio
    async def test_convenience_function(self):
        with patch("app.calibration.reporter.CalibrationReporter") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_report = MagicMock()
            mock_instance.generate_report = AsyncMock(return_value=mock_report)
            result = await run_weekly_calibration()
        assert result is mock_report
