"""Tests for edge intelligence computation module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from app.paper_trading.edge_intelligence import (
    DimensionStats,
    EdgeBriefing,
    TradeContext,
    compute_edge_briefing,
    compute_trade_context,
    _compute_stats,
    _score_bucket,
    _convergence_label,
    _generate_insights,
)


# ---------------------------------------------------------------------------
# Mock PaperPosition — lightweight stand-in for real PaperPosition schema
# ---------------------------------------------------------------------------

@dataclass
class MockPosition:
    """Minimal PaperPosition-like object for testing."""

    status: str = "CLOSED"
    option_type: Optional[str] = "PUT"
    scanner_source: Optional[str] = "BREAKOUT"
    conviction_score: Optional[float] = 78.0
    convergence_count: Optional[int] = 1
    quality_tier_at_entry: Optional[str] = "TIER_1"
    dte_bucket: Optional[str] = "B"
    current_pnl_pct: float = 10.0
    dollar_pnl: Optional[float] = 50.0
    days_held: int = 3
    entry_date: str = "2026-03-10"


def _make_positions(
    n: int,
    option_type: str = "PUT",
    scanner: str = "BREAKOUT",
    score: float = 78.0,
    win_pct: float = 0.7,
    status: str = "CLOSED",
    tier: str = "TIER_1",
    convergence: int = 1,
    dte_bucket: str = "B",
) -> list[MockPosition]:
    """Create n mock positions with the given characteristics."""
    positions = []
    for i in range(n):
        is_win = i < int(n * win_pct)
        pnl = 25.0 if is_win else -30.0
        positions.append(MockPosition(
            status=status,
            option_type=option_type,
            scanner_source=scanner,
            conviction_score=score,
            convergence_count=convergence,
            quality_tier_at_entry=tier,
            dte_bucket=dte_bucket,
            current_pnl_pct=pnl,
            dollar_pnl=pnl * 100,
            days_held=3,
        ))
    return positions


# ---------------------------------------------------------------------------
# Unit tests: _compute_stats
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_empty_positions(self):
        stats = _compute_stats("Test", [])
        assert stats.total == 0
        assert stats.closed == 0
        assert stats.win_rate is None
        assert stats.avg_return is None
        assert stats.expectancy is None

    def test_all_winners(self):
        positions = _make_positions(5, win_pct=1.0)
        stats = _compute_stats("Winners", positions)
        assert stats.closed == 5
        assert stats.wins == 5
        assert stats.losses == 0
        assert stats.win_rate == 100.0
        assert stats.avg_return == 25.0

    def test_all_losers(self):
        positions = _make_positions(5, win_pct=0.0)
        stats = _compute_stats("Losers", positions)
        assert stats.wins == 0
        assert stats.losses == 5
        assert stats.win_rate == 0.0

    def test_mixed(self):
        positions = _make_positions(10, win_pct=0.7)
        stats = _compute_stats("Mixed", positions)
        assert stats.wins == 7
        assert stats.losses == 3
        assert stats.win_rate == 70.0
        assert stats.expectancy is not None

    def test_open_positions_not_counted_as_closed(self):
        positions = _make_positions(5, status="OPEN")
        stats = _compute_stats("Open", positions)
        assert stats.total == 5
        assert stats.closed == 0
        assert stats.win_rate is None

    def test_dollar_pnl_summed(self):
        positions = _make_positions(4, win_pct=0.5)
        stats = _compute_stats("PnL", positions)
        # 2 wins at 25*100=2500, 2 losses at -30*100=-3000
        assert stats.total_pnl_dollars == pytest.approx(-1000.0)


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_score_bucket(self):
        assert _score_bucket(None) == "UNKNOWN"
        assert _score_bucket(60) == "<65"
        assert _score_bucket(65) == "65-69"
        assert _score_bucket(69.9) == "65-69"
        assert _score_bucket(70) == "70-74"
        assert _score_bucket(75) == "75-79"
        assert _score_bucket(80) == "80+"
        assert _score_bucket(95) == "80+"

    def test_convergence_label(self):
        assert _convergence_label(None) == "1"
        assert _convergence_label(1) == "1"
        assert _convergence_label(2) == "2"
        assert _convergence_label(3) == "3+"
        assert _convergence_label(5) == "3+"


# ---------------------------------------------------------------------------
# Unit tests: compute_edge_briefing
# ---------------------------------------------------------------------------

class TestComputeEdgeBriefing:
    def test_empty_positions(self):
        briefing = compute_edge_briefing([], "2026-03-04", "2026-03-14", 10)
        assert briefing.total_positions == 0
        assert briefing.total_closed == 0
        assert briefing.overall_win_rate is None
        assert briefing.insights == []

    def test_slices_by_option_type(self):
        positions = (
            _make_positions(10, option_type="CALL", win_pct=0.4)
            + _make_positions(10, option_type="PUT", win_pct=0.8)
        )
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)

        assert "CALL" in briefing.by_option_type
        assert "PUT" in briefing.by_option_type
        assert briefing.by_option_type["CALL"].win_rate == 40.0
        assert briefing.by_option_type["PUT"].win_rate == 80.0
        assert briefing.option_type_edge == "PUT"

    def test_slices_by_scanner(self):
        positions = (
            _make_positions(8, scanner="BREAKOUT", win_pct=0.75)
            + _make_positions(8, scanner="COMPRESSION", win_pct=0.25)
        )
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)

        assert "BREAKOUT" in briefing.by_scanner
        assert "COMPRESSION" in briefing.by_scanner
        assert briefing.by_scanner["BREAKOUT"].win_rate == 75.0

    def test_normalizes_scanner_suffix(self):
        positions = _make_positions(5, scanner="BREAKOUT_SCANNER")
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)
        assert "BREAKOUT" in briefing.by_scanner

    def test_slices_by_score_bucket(self):
        positions = (
            _make_positions(5, score=62.0, win_pct=0.2)
            + _make_positions(5, score=72.0, win_pct=0.6)
            + _make_positions(5, score=85.0, win_pct=0.8)
        )
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)

        assert "<65" in briefing.by_score_bucket
        assert "70-74" in briefing.by_score_bucket
        assert "80+" in briefing.by_score_bucket

    def test_slices_by_quality_tier(self):
        positions = (
            _make_positions(6, tier="TIER_1", win_pct=0.83)
            + _make_positions(6, tier="TIER_3", win_pct=0.33)
        )
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)

        assert "TIER_1" in briefing.by_quality_tier
        assert "TIER_3" in briefing.by_quality_tier

    def test_slices_by_convergence(self):
        positions = (
            _make_positions(6, convergence=1, win_pct=0.5)
            + _make_positions(6, convergence=3, win_pct=0.83)
        )
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)

        assert "1" in briefing.by_convergence
        assert "3+" in briefing.by_convergence

    def test_market_context_parsed(self):
        positions = _make_positions(5)
        market_ctx = {
            "spy": {"price": 510.0, "change_percent": -1.5},
            "vix": {"price": 28.5},
        }
        briefing = compute_edge_briefing(
            positions, "2026-03-04", "2026-03-14", 10, market_context=market_ctx
        )
        assert briefing.vix_level == 28.5
        assert briefing.spy_direction == "down"

    def test_to_dict_roundtrip(self):
        positions = _make_positions(10, win_pct=0.7)
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)
        d = briefing.to_dict()

        assert isinstance(d, dict)
        assert d["total_positions"] == 10
        assert d["total_closed"] == 10
        assert isinstance(d["by_option_type"], dict)
        assert isinstance(d["insights"], list)


# ---------------------------------------------------------------------------
# Unit tests: insight generation
# ---------------------------------------------------------------------------

class TestInsightGeneration:
    def test_option_type_divergence_insight(self):
        positions = (
            _make_positions(10, option_type="CALL", win_pct=0.3)
            + _make_positions(10, option_type="PUT", win_pct=0.8)
        )
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)

        type_insights = [i for i in briefing.insights if i.category == "option_type"]
        assert len(type_insights) == 1
        assert "Puts winning" in type_insights[0].headline
        assert type_insights[0].strength == "strong"  # 50pp diff

    def test_no_insight_when_diff_small(self):
        positions = (
            _make_positions(10, option_type="CALL", win_pct=0.55)
            + _make_positions(10, option_type="PUT", win_pct=0.58)
        )
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)

        type_insights = [i for i in briefing.insights if i.category == "option_type"]
        assert len(type_insights) == 0

    def test_max_three_insights(self):
        # Create conditions that would trigger many insights
        positions = (
            _make_positions(10, option_type="CALL", win_pct=0.2, scanner="BREAKOUT",
                            tier="TIER_1", convergence=3, score=85.0)
            + _make_positions(10, option_type="PUT", win_pct=0.9, scanner="COMPRESSION",
                              tier="TIER_3", convergence=1, score=62.0)
        )
        briefing = compute_edge_briefing(positions, "2026-03-04", "2026-03-14", 10)
        assert len(briefing.insights) <= 3

    def test_no_insights_on_empty(self):
        briefing = compute_edge_briefing([], "2026-03-04", "2026-03-14", 10)
        assert briefing.insights == []


# ---------------------------------------------------------------------------
# Unit tests: compute_trade_context
# ---------------------------------------------------------------------------

class TestComputeTradeContext:
    def test_insufficient_data(self):
        positions = _make_positions(2)
        ctx = compute_trade_context("PUT", "BREAKOUT", 78.0, "B", positions)
        assert ctx.confidence_flag == "insufficient"

    def test_exact_match_high_confidence(self):
        positions = _make_positions(15, option_type="PUT", scanner="BREAKOUT",
                                     score=78.0, win_pct=0.73)
        ctx = compute_trade_context("PUT", "BREAKOUT", 78.0, "B", positions)

        assert ctx.confidence_flag == "high_confidence"
        assert ctx.exact_match is not None
        assert ctx.exact_match.closed == 15
        assert "winners" in ctx.summary

    def test_broader_context_when_exact_sparse(self):
        # 2 exact matches but many broader
        positions = (
            _make_positions(2, option_type="PUT", scanner="BREAKOUT", score=78.0)
            + _make_positions(10, option_type="PUT", scanner="COMPRESSION", score=72.0)
        )
        ctx = compute_trade_context("PUT", "BREAKOUT", 78.0, "B", positions)

        # Exact match has only 2 closed, broader has more
        assert ctx.by_option_type is not None
        assert ctx.by_option_type.closed == 12  # all PUTs

    def test_description_includes_all_parts(self):
        positions = _make_positions(10)
        ctx = compute_trade_context("PUT", "BREAKOUT", 78.0, "B", positions)
        assert "PUT" in ctx.exact_match_description
        assert "Breakout" in ctx.exact_match_description
        assert "75-79" in ctx.exact_match_description

    def test_to_dict(self):
        positions = _make_positions(10)
        ctx = compute_trade_context("PUT", "BREAKOUT", 78.0, "B", positions)
        d = ctx.to_dict()
        assert isinstance(d, dict)
        assert "confidence_flag" in d
        assert "summary" in d


# ---------------------------------------------------------------------------
# Unit tests: DimensionStats.to_dict
# ---------------------------------------------------------------------------

class TestDimensionStatsDict:
    def test_rounds_floats(self):
        stats = DimensionStats(
            label="Test",
            total=10,
            closed=10,
            wins=7,
            losses=3,
            win_rate=70.12345,
            avg_return=15.6789,
            avg_days_held=3.4567,
            total_pnl_dollars=1234.5678,
            expectancy=8.9012,
        )
        d = stats.to_dict()
        assert d["win_rate"] == 70.1
        assert d["avg_return"] == 15.68
        assert d["avg_days_held"] == 3.5
        assert d["total_pnl_dollars"] == 1234.57
        assert d["expectancy"] == 8.90
