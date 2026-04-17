"""Tests for scanner performance analysis module."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import pytest

from app.paper_trading.scanner_analysis import (
    KNOWN_SCANNERS,
    build_scanner_analysis_input,
    build_scanner_analysis_prompt,
    parse_scanner_analysis_response,
)


# ---------------------------------------------------------------------------
# Mock PaperPosition
# ---------------------------------------------------------------------------

@dataclass
class MockPosition:
    """Minimal PaperPosition-like object for testing."""

    status: str = "CLOSED"
    option_type: Optional[str] = "PUT"
    scanner_source: Optional[str] = "UNUSUAL_VOLUME"
    conviction_score: Optional[float] = 72.0
    convergence_count: Optional[int] = 1
    quality_tier_at_entry: Optional[str] = "NONE"
    dte_bucket: Optional[str] = "B"
    current_pnl_pct: float = -5.0
    dollar_pnl: Optional[float] = -250.0
    days_held: int = 4
    entry_date: str = "2026-03-10"
    max_favorable_excursion: float = 8.0
    max_adverse_excursion: float = -12.0
    gate_margin: Optional[float] = 0.15
    theta_adj_ev: Optional[float] = 3.5
    entry_iv: Optional[float] = 0.65
    entry_delta: Optional[float] = 0.35
    entry_theta: Optional[float] = -0.08
    dte_at_entry: Optional[int] = 21
    pillar_premium_leverage: Optional[float] = 62.0
    pillar_underlying_behavior: Optional[float] = 55.0
    pillar_setup_quality: Optional[float] = 70.0
    pillar_directional_conviction: Optional[float] = None
    pillar_move_potential: Optional[float] = None
    pillar_trade_structure: Optional[float] = None
    strike: Optional[float] = 150.0
    expiration_date: Optional[str] = "2026-04-01"
    matched_rule_ids: Optional[list] = None
    exit_reason: Optional[str] = "STOP_LOSS"


def _make_positions(
    n: int,
    scanner: str = "UNUSUAL_VOLUME",
    win_pct: float = 0.4,
    **overrides,
) -> list[MockPosition]:
    """Create n mock positions with the given characteristics."""
    positions = []
    for i in range(n):
        is_win = i < int(n * win_pct)
        pnl = 20.0 if is_win else -8.0
        kwargs = {
            "scanner_source": scanner,
            "current_pnl_pct": pnl,
            "dollar_pnl": pnl * 100,
            "exit_reason": "PROFIT_TARGET" if is_win else "STOP_LOSS",
            "max_favorable_excursion": 30.0 if is_win else 5.0,
            "max_adverse_excursion": -5.0 if is_win else -15.0,
            "gate_margin": 0.25 if is_win else 0.08,
            "entry_iv": 0.45 if is_win else 0.82,
            **overrides,
        }
        positions.append(MockPosition(**kwargs))
    return positions


MOCK_GATE_CONFIG = {
    "min_open_interest": 300,
    "min_volume": 75,
    "max_spread_pct": 8.0,
    "dte_min": 7,
    "dte_max": 120,
    "move_sufficiency_max": 1.25,
    "iv_percentile_max": 85,
    "breakout_volume_min": 1.5,
    "theta_burden_max": 4.0,
    "trend_alignment_min": 0.6,
    "iv_rv_ratio_max": 1.5,
    "feasibility_ratio_max": 1.5,
    "delta_min": 0.20,
    "delta_max": 0.70,
    "max_premium": 20.0,
}


# ---------------------------------------------------------------------------
# Tests: build_scanner_analysis_input
# ---------------------------------------------------------------------------

class TestBuildScannerAnalysisInput:
    def test_basic_assembly(self):
        positions = _make_positions(20, scanner="UNUSUAL_VOLUME", win_pct=0.4)
        # Add some from another scanner for comparison
        positions += _make_positions(10, scanner="BREAKOUT", win_pct=0.7)

        result = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )

        assert result["scanner_name"] == "UNUSUAL_VOLUME"
        assert result["total_closed"] == 20
        assert result["win_rate"] is not None
        assert result["win_rate"] == pytest.approx(40.0)
        assert result["winners"]["count"] == 8
        assert result["losers"]["count"] == 12

    def test_winner_loser_profiles(self):
        positions = _make_positions(10, win_pct=0.5)
        result = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )

        winners = result["winners"]
        losers = result["losers"]

        assert winners["count"] == 5
        assert losers["count"] == 5
        assert winners["avg_return"] > 0
        assert losers["avg_return"] < 0
        # Winners should have higher gate margin (from mock data)
        assert winners["avg_gate_margin"] > losers["avg_gate_margin"]

    def test_feature_divergences(self):
        positions = _make_positions(20, win_pct=0.5)
        result = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )

        divergences = result["feature_divergences"]
        assert len(divergences) > 0
        # Should be sorted by divergence descending
        for i in range(len(divergences) - 1):
            assert divergences[i]["divergence_pct"] >= divergences[i + 1]["divergence_pct"]

        # Entry IV should show divergence (winners=0.45, losers=0.82)
        iv_div = next((d for d in divergences if d["feature"] == "Entry IV"), None)
        assert iv_div is not None
        assert iv_div["divergence_pct"] > 0

    def test_scanner_comparison(self):
        positions = _make_positions(10, scanner="UNUSUAL_VOLUME", win_pct=0.4)
        positions += _make_positions(10, scanner="BREAKOUT", win_pct=0.8)
        positions += _make_positions(10, scanner="COMPRESSION_EXPANSION", win_pct=0.3)

        result = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )

        comparison = result["scanner_comparison"]
        scanner_names = [s["scanner"] for s in comparison]
        assert "UNUSUAL_VOLUME" in scanner_names
        assert "BREAKOUT" in scanner_names
        assert "COMPRESSION_EXPANSION" in scanner_names

    def test_no_data_for_scanner(self):
        positions = _make_positions(10, scanner="BREAKOUT", win_pct=0.7)
        result = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )
        assert result["error"] == "no_data"

    def test_gate_thresholds_included(self):
        positions = _make_positions(10)
        result = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )
        assert result["gate_thresholds"] == MOCK_GATE_CONFIG

    def test_exit_reasons(self):
        positions = _make_positions(20, win_pct=0.4)
        result = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )

        exit_reasons = result["exit_reasons"]
        assert "STOP_LOSS" in exit_reasons
        assert exit_reasons["STOP_LOSS"] == 12  # 60% are losers with STOP_LOSS

    def test_all_winners(self):
        positions = _make_positions(10, win_pct=1.0)
        result = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )
        assert result["winners"]["count"] == 10
        assert result["losers"]["count"] == 0
        assert result["win_rate"] == pytest.approx(100.0)

    def test_all_losers(self):
        positions = _make_positions(10, win_pct=0.0)
        result = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )
        assert result["winners"]["count"] == 0
        assert result["losers"]["count"] == 10
        assert result["win_rate"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: build_scanner_analysis_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_prompt_contains_sections(self):
        positions = _make_positions(20, win_pct=0.4)
        positions += _make_positions(10, scanner="BREAKOUT", win_pct=0.7)
        analysis_input = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )

        system_prompt, user_prompt = build_scanner_analysis_prompt(analysis_input)

        # System prompt should contain key instructions
        assert "expert" in system_prompt.lower()
        assert "JSON" in system_prompt

        # User prompt should contain all sections
        assert "UNUSUAL VOLUME" in user_prompt
        assert "Winner vs Loser" in user_prompt
        assert "Feature Divergences" in user_prompt
        assert "Gate Thresholds" in user_prompt
        assert "Scanner Comparison" in user_prompt
        assert "Required Output Format" in user_prompt

    def test_prompt_includes_gate_config(self):
        positions = _make_positions(10)
        analysis_input = build_scanner_analysis_input(
            "UNUSUAL_VOLUME", positions, MOCK_GATE_CONFIG
        )

        _, user_prompt = build_scanner_analysis_prompt(analysis_input)

        assert "GATE_MIN_OPEN_INTEREST" in user_prompt
        assert "GATE_MAX_SPREAD_PCT" in user_prompt
        assert "300" in user_prompt  # min_open_interest value


# ---------------------------------------------------------------------------
# Tests: parse_scanner_analysis_response
# ---------------------------------------------------------------------------

class TestParseResponse:
    def _valid_response(self) -> dict:
        return {
            "summary": "The scanner underperforms due to high IV entries.",
            "root_causes": [
                {
                    "cause": "High IV at entry",
                    "evidence": "Losers avg IV 0.82 vs winners 0.45",
                    "severity": "high",
                }
            ],
            "gate_recommendations": [
                {
                    "gate_id": "GATE_MAX_SPREAD_PCT",
                    "gate_name": "Max Spread %",
                    "current_value": 8.0,
                    "suggested_value": 5.0,
                    "direction": "tighten",
                    "rationale": "65% of losers had wide spreads.",
                    "expected_impact": "Would filter ~40% of losers.",
                }
            ],
            "additional_filters": [
                {
                    "description": "Skip UV signals with convergence=1",
                    "rationale": "Single-scanner UV has 30% WR.",
                }
            ],
            "confidence": "medium",
            "confidence_rationale": "20 trades is a small sample.",
        }

    def test_valid_json(self):
        response = json.dumps(self._valid_response())
        result = parse_scanner_analysis_response(response)
        assert result["summary"] == "The scanner underperforms due to high IV entries."
        assert len(result["root_causes"]) == 1
        assert len(result["gate_recommendations"]) == 1
        assert result["confidence"] == "medium"

    def test_markdown_wrapped_json(self):
        response = "```json\n" + json.dumps(self._valid_response()) + "\n```"
        result = parse_scanner_analysis_response(response)
        assert result["summary"] is not None

    def test_missing_required_fields(self):
        response = json.dumps({"summary": "test"})
        with pytest.raises(ValueError, match="Missing required fields"):
            parse_scanner_analysis_response(response)

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_scanner_analysis_response("not json at all")

    def test_root_causes_must_be_list(self):
        data = self._valid_response()
        data["root_causes"] = "not a list"
        with pytest.raises(ValueError, match="root_causes must be a list"):
            parse_scanner_analysis_response(json.dumps(data))

    def test_defaults_optional_fields(self):
        data = self._valid_response()
        del data["additional_filters"]
        del data["confidence_rationale"]
        result = parse_scanner_analysis_response(json.dumps(data))
        assert result["additional_filters"] == []
        assert result["confidence_rationale"] == ""


# ---------------------------------------------------------------------------
# Tests: KNOWN_SCANNERS
# ---------------------------------------------------------------------------

class TestKnownScanners:
    def test_known_scanners_set(self):
        assert "BREAKOUT" in KNOWN_SCANNERS
        assert "UNUSUAL_VOLUME" in KNOWN_SCANNERS
        assert "COMPRESSION_EXPANSION" in KNOWN_SCANNERS
        assert "CHEAP_OPTIONS" in KNOWN_SCANNERS
        assert "BREAKDOWN" in KNOWN_SCANNERS
