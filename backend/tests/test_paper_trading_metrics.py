"""Tests for paper_trading/metrics.py.

Covers compute_metrics_from_positions, compare_tiers,
and analyze_exit_effectiveness.
"""

from __future__ import annotations

import pytest

from app.core.schemas import (
    ExitReason,
    PaperPosition,
    PositionStatus,
    Verdict,
)
from app.paper_trading.metrics import (
    compute_metrics_from_positions,
    compare_tiers,
    analyze_exit_effectiveness,
)
from app.paper_trading.models import PerformanceMetrics


def _pos(pos_id, pnl, status=PositionStatus.CLOSED, exit_reason=None, tier=None, days=5):
    return PaperPosition(
        position_id=pos_id,
        evaluation_id=f"eval-{pos_id}",
        option_ticker="O:TEST",
        entry_price=5.0,
        entry_date="2026-01-15",
        verdict_at_entry=Verdict.APPROVE,
        quality_tier_at_entry=tier,
        status=status,
        exit_date="2026-01-20" if status == PositionStatus.CLOSED else None,
        exit_reason=exit_reason,
        current_price=5.0 * (1 + pnl / 100),
        current_pnl_pct=pnl,
        max_favorable_excursion=max(pnl, 0),
        max_adverse_excursion=min(pnl, 0),
        days_held=days,
    )


class TestComputeMetricsFromPositions:

    def test_empty_positions(self):
        result = compute_metrics_from_positions([])
        assert isinstance(result, PerformanceMetrics)
        assert result.total_positions == 0

    def test_with_closed_positions(self):
        positions = [
            _pos("p1", 25.0),
            _pos("p2", -15.0),
            _pos("p3", 10.0),
        ]
        result = compute_metrics_from_positions(positions)
        assert result.total_positions == 3
        assert result.closed_positions == 3
        assert result.win_count == 2
        assert result.loss_count == 1
        assert result.win_rate == pytest.approx(66.67, abs=0.1)

    def test_all_open(self):
        positions = [_pos("p1", 5.0, status=PositionStatus.OPEN)]
        result = compute_metrics_from_positions(positions)
        assert result.total_positions == 1
        assert result.open_positions == 1
        assert result.closed_positions == 0

    def test_avg_win_and_loss(self):
        positions = [
            _pos("p1", 30.0),
            _pos("p2", 20.0),
            _pos("p3", -10.0),
        ]
        result = compute_metrics_from_positions(positions)
        assert result.avg_win_pct == pytest.approx(25.0, abs=0.1)
        assert result.avg_loss_pct == pytest.approx(-10.0, abs=0.1)

    def test_best_worst_trade(self):
        positions = [
            _pos("p1", 50.0),
            _pos("p2", -30.0),
            _pos("p3", 10.0),
        ]
        result = compute_metrics_from_positions(positions)
        assert result.best_trade_pct == pytest.approx(50.0)
        assert result.worst_trade_pct == pytest.approx(-30.0)


class TestCompareTiers:

    def test_compare_tiers(self):
        positions = [
            _pos("p1", 30.0, tier="TIER_1"),
            _pos("p2", -10.0, tier="TIER_1"),
            _pos("p3", 15.0, tier="TIER_2"),
            _pos("p4", -5.0, tier="TIER_3"),
        ]
        result = compare_tiers(positions)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_empty_positions(self):
        result = compare_tiers([])
        assert isinstance(result, dict)


class TestAnalyzeExitEffectiveness:

    def test_analyze_exits(self):
        positions = [
            _pos("p1", 50.0, exit_reason=ExitReason.PROFIT_TARGET),
            _pos("p2", -40.0, exit_reason=ExitReason.STOP_LOSS),
            _pos("p3", 5.0, exit_reason=ExitReason.TIME_EXIT),
        ]
        result = analyze_exit_effectiveness(positions)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_empty_positions(self):
        result = analyze_exit_effectiveness([])
        assert isinstance(result, dict)
        assert len(result) == 0

    def test_single_exit_type(self):
        positions = [
            _pos("p1", 30.0, exit_reason=ExitReason.PROFIT_TARGET),
            _pos("p2", 20.0, exit_reason=ExitReason.PROFIT_TARGET),
        ]
        result = analyze_exit_effectiveness(positions)
        assert len(result) == 1

    def test_no_exit_reason_excluded(self):
        positions = [
            _pos("p1", 30.0, exit_reason=None),
        ]
        result = analyze_exit_effectiveness(positions)
        assert len(result) == 0
