"""Tests for the CalibrationReporter (calibration/reporter.py).

Covers summary stats, score bands, build_eval_decisions,
analyze_watch_to_approve, and generate_report (mocked _load_data).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.calibration.models import (
    CalibrationReport,
    CounterfactualResult,
    CounterfactualSummary,
    GateAnalysis,
    RecommendationType,
    ScoreBandAnalysis,
    WatchToApproveAnalysis,
)
from app.calibration.reporter import CalibrationReporter, SCORE_BANDS
from app.core.schemas import (
    PaperPosition,
    Policy,
    PolicyConfig,
    PositionStatus,
    Verdict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_position(pos_id, pnl_pct, status=PositionStatus.CLOSED, exit_date="2026-02-01", eval_id=None):
    return PaperPosition(
        position_id=pos_id,
        evaluation_id=eval_id or f"eval-{pos_id}",
        option_ticker="O:TEST",
        entry_price=5.0,
        entry_date="2026-01-15",
        verdict_at_entry=Verdict.APPROVE,
        status=status,
        exit_date=exit_date,
        current_price=5.0 * (1 + pnl_pct / 100),
        current_pnl_pct=pnl_pct,
    )


def _make_policy():
    config = PolicyConfig()
    return Policy(
        version="v2.0.0",
        policy_hash="hash",
        config=config,
        created_by="test",
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Tests: _calculate_summary_stats
# ---------------------------------------------------------------------------


class TestCalculateSummaryStats:

    def test_empty_positions(self):
        reporter = CalibrationReporter()
        win_rate, avg_return = reporter._calculate_summary_stats([])
        assert win_rate == 0.0
        assert avg_return == 0.0

    def test_all_winners(self):
        reporter = CalibrationReporter()
        positions = [_make_position("p1", 25.0), _make_position("p2", 10.0)]
        win_rate, avg_return = reporter._calculate_summary_stats(positions)
        assert win_rate == 100.0
        assert avg_return == 17.5

    def test_mixed_pnl(self):
        reporter = CalibrationReporter()
        positions = [
            _make_position("p1", 30.0),
            _make_position("p2", -20.0),
            _make_position("p3", 10.0),
            _make_position("p4", -5.0),
        ]
        win_rate, avg_return = reporter._calculate_summary_stats(positions)
        assert win_rate == 50.0
        assert avg_return == pytest.approx(3.75)

    def test_all_losers(self):
        reporter = CalibrationReporter()
        positions = [_make_position("p1", -10.0), _make_position("p2", -20.0)]
        win_rate, avg_return = reporter._calculate_summary_stats(positions)
        assert win_rate == 0.0
        assert avg_return == -15.0


# ---------------------------------------------------------------------------
# Tests: _build_eval_decisions
# ---------------------------------------------------------------------------


class TestBuildEvalDecisions:

    def test_builds_from_evaluations(self):
        reporter = CalibrationReporter()
        reporter._evaluations = [
            {
                "evaluation_id": "e1",
                "decision": {
                    "final_score": 82.0,
                    "verdict": "APPROVE",
                    "failed_gates": [],
                },
            },
            {
                "evaluation_id": "e2",
                "decision": {
                    "final_score": 45.0,
                    "verdict": "REJECT",
                    "failed_gates": ["GATE_MIN_OI"],
                },
            },
        ]

        result = reporter._build_eval_decisions()
        assert "e1" in result
        assert result["e1"]["final_score"] == 82.0
        assert result["e2"]["verdict"] == "REJECT"

    def test_skips_missing_decision(self):
        reporter = CalibrationReporter()
        reporter._evaluations = [
            {"evaluation_id": "e1"},  # No decision
        ]

        result = reporter._build_eval_decisions()
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Tests: _analyze_watch_to_approve
# ---------------------------------------------------------------------------


class TestAnalyzeWatchToApprove:

    def test_no_watches(self):
        reporter = CalibrationReporter()
        reporter._policy = _make_policy()
        reporter._evaluations = []

        result = reporter._analyze_watch_to_approve()
        assert isinstance(result, WatchToApproveAnalysis)
        assert result.total_watch == 0
        assert result.rate == 0.0

    def test_some_would_flip(self):
        reporter = CalibrationReporter()
        reporter._policy = _make_policy()
        reporter._evaluations = [
            {"evaluation_id": "e1", "decision": {"verdict": "WATCH", "final_score": 72.0, "failed_gates": []}},
            {"evaluation_id": "e2", "decision": {"verdict": "WATCH", "final_score": 68.0, "failed_gates": []}},
            {"evaluation_id": "e3", "decision": {"verdict": "WATCH", "final_score": 60.0, "failed_gates": []}},
        ]

        result = reporter._analyze_watch_to_approve()
        assert result.total_watch == 3
        assert result.would_flip_count >= 1  # e1 (72) and e2 (68) may flip


# ---------------------------------------------------------------------------
# Tests: _analyze_score_bands
# ---------------------------------------------------------------------------


class TestAnalyzeScoreBands:

    def test_empty_positions(self):
        reporter = CalibrationReporter()
        reporter._evaluations = []
        result = reporter._analyze_score_bands([])
        assert len(result) == len(SCORE_BANDS)
        assert all(band.count == 0 for band in result)

    def test_positions_in_band(self):
        reporter = CalibrationReporter()
        reporter._evaluations = [
            {"evaluation_id": "eval-p1", "decision": {"final_score": 80.0}},
            {"evaluation_id": "eval-p2", "decision": {"final_score": 62.0}},
        ]
        positions = [
            _make_position("p1", 25.0, eval_id="eval-p1"),
            _make_position("p2", -10.0, eval_id="eval-p2"),
        ]

        result = reporter._analyze_score_bands(positions)
        # 80 is in the 75-85 band
        band_75 = [b for b in result if b.band == "75-85"][0]
        assert band_75.count == 1
        assert band_75.win_rate == 100.0

        # 62 is in the 60-65 band
        band_60 = [b for b in result if b.band == "60-65"][0]
        assert band_60.count == 1
        assert band_60.win_rate == 0.0


# ---------------------------------------------------------------------------
# Tests: generate_report (mocked data loading)
# ---------------------------------------------------------------------------


class TestGenerateReport:

    @pytest.mark.asyncio
    async def test_generate_report_basic(self):
        reporter = CalibrationReporter()

        # Mock _load_data to inject test data
        async def mock_load_data():
            reporter._policy = _make_policy()
            reporter._positions = [
                _make_position("p1", 25.0, exit_date="2026-02-01"),
                _make_position("p2", -10.0, exit_date="2026-02-02"),
            ]
            reporter._evaluations = [
                {"evaluation_id": "eval-p1", "decision": {"final_score": 80.0, "verdict": "APPROVE", "failed_gates": []}},
                {"evaluation_id": "eval-p2", "decision": {"final_score": 62.0, "verdict": "REJECT", "failed_gates": []}},
            ]
            reporter._gate_results = {}

        reporter._load_data = mock_load_data

        report = await reporter.generate_report(
            week_start="2026-01-28",
            week_end="2026-02-07",
        )

        assert isinstance(report, CalibrationReport)
        assert report.positions_closed == 2
        assert report.win_rate == 50.0

    @pytest.mark.asyncio
    async def test_generate_report_empty(self):
        reporter = CalibrationReporter()

        async def mock_load_data():
            reporter._policy = _make_policy()
            reporter._positions = []
            reporter._evaluations = []
            reporter._gate_results = {}

        reporter._load_data = mock_load_data

        report = await reporter.generate_report(
            week_start="2026-01-28",
            week_end="2026-02-07",
        )

        assert report.positions_closed == 0
        assert report.win_rate == 0.0
