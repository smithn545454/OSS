"""Tests for paper trading AI insights engine.

Covers:
- insights_prompt: build_insights_prompt, parse_insights_response
- insights: generate_ai_insights
- Route: GET /api/paper-trading/ai-insights
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.paper_trading.insights_prompt import build_insights_prompt, parse_insights_response
from app.paper_trading.models import PerformanceMetrics


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def sample_metrics_dict() -> dict:
    """Metrics dict matching PerformanceMetrics.to_dict() output."""
    return {
        "total_positions": 20,
        "open_positions": 5,
        "closed_positions": 15,
        "win_count": 9,
        "loss_count": 6,
        "win_rate": 60.0,
        "loss_rate": 40.0,
        "avg_win_pct": 35.0,
        "avg_loss_pct": -20.0,
        "best_trade_pct": 85.0,
        "worst_trade_pct": -45.0,
        "expectancy": 13.0,
        "profit_factor": 2.63,
        "avg_mfe": 40.0,
        "avg_mae": 15.0,
        "max_mfe": 90.0,
        "max_mae": 50.0,
        "mfe_capture_ratio": 87.5,
        "avg_days_held": 8.5,
        "max_days_held": 21,
        "exit_distribution": {"PROFIT_TARGET": 7, "STOP_LOSS": 5, "TIME_EXIT": 3},
        "tier_performance": {
            "TIER_1": {"win_count": 5, "loss_count": 1, "win_rate": 83.3, "avg_return_pct": 30.0},
            "TIER_2": {"win_count": 3, "loss_count": 3, "win_rate": 50.0, "avg_return_pct": 5.0},
        },
        "approve_performance": {"win_rate": 65.0, "avg_return": 18.0, "count": 12},
        "watch_performance": {"win_rate": 50.0, "avg_return": 5.0, "count": 3},
    }


@pytest.fixture
def sample_tier_data() -> dict:
    return {
        "TIER_1": {"total": 8, "closed": 6, "wins": 5, "losses": 1, "win_rate": 83.3, "avg_return": 30.0},
        "TIER_2": {"total": 7, "closed": 6, "wins": 3, "losses": 3, "win_rate": 50.0, "avg_return": 5.0},
        "TIER_3": {"total": 5, "closed": 3, "wins": 1, "losses": 2, "win_rate": 33.3, "avg_return": -10.0},
    }


@pytest.fixture
def sample_exit_data() -> dict:
    return {
        "PROFIT_TARGET": {"count": 7, "avg_return": 40.0, "avg_mfe": 45.0, "avg_mae": 8.0, "avg_days_held": 6.0, "mfe_left_on_table": 5.0},
        "STOP_LOSS": {"count": 5, "avg_return": -25.0, "avg_mfe": 10.0, "avg_mae": 30.0, "avg_days_held": 4.0},
        "TIME_EXIT": {"count": 3, "avg_return": -5.0, "avg_mfe": 15.0, "avg_mae": 12.0, "avg_days_held": 14.0},
    }


@pytest.fixture
def sample_calibration_report() -> dict:
    return {
        "report_id": "report-001",
        "gate_analyses": [
            {
                "gate_id": "GATE_MIN_OPEN_INTEREST",
                "rejection_rate": 12.0,
                "false_negative_rate": 2.0,
                "effectiveness_score": 85.0,
                "recommendation": "NO_CHANGE",
            },
        ],
        "suggestions": [
            {
                "gate_id": "GATE_MAX_SPREAD_PCT",
                "current_value": 8.0,
                "suggested_value": 10.0,
                "status": "PENDING",
            },
        ],
        "score_band_analysis": [
            {"band": "80-90", "count": 5, "win_rate": 70.0, "avg_return": 20.0},
            {"band": "70-80", "count": 8, "win_rate": 50.0, "avg_return": 5.0},
        ],
    }


# ============================================================================
# build_insights_prompt tests
# ============================================================================


class TestBuildInsightsPrompt:

    def test_includes_all_sections(self, sample_metrics_dict, sample_tier_data, sample_exit_data):
        prompt = build_insights_prompt(sample_metrics_dict, sample_tier_data, sample_exit_data)
        assert "Overall Performance" in prompt
        assert "Verdict Performance" in prompt
        assert "Tier Performance" in prompt
        assert "Exit Strategy Analysis" in prompt
        assert "60.0%" in prompt  # win rate
        assert "TIER_1" in prompt

    def test_includes_calibration_when_provided(
        self, sample_metrics_dict, sample_tier_data, sample_exit_data, sample_calibration_report
    ):
        prompt = build_insights_prompt(
            sample_metrics_dict, sample_tier_data, sample_exit_data, sample_calibration_report
        )
        assert "Latest Calibration Report" in prompt
        assert "GATE_MIN_OPEN_INTEREST" in prompt
        assert "Score Band Analysis" in prompt

    def test_no_calibration_section_when_none(
        self, sample_metrics_dict, sample_tier_data, sample_exit_data
    ):
        prompt = build_insights_prompt(sample_metrics_dict, sample_tier_data, sample_exit_data, None)
        assert "Latest Calibration Report" not in prompt

    def test_includes_output_schema(self, sample_metrics_dict, sample_tier_data, sample_exit_data):
        prompt = build_insights_prompt(sample_metrics_dict, sample_tier_data, sample_exit_data)
        assert "GATE_TUNING" in prompt
        assert "estimated_impact" in prompt


# ============================================================================
# parse_insights_response tests
# ============================================================================


class TestParseInsightsResponse:

    def test_valid_json_array(self):
        response = json.dumps([
            {
                "category": "GATE_TUNING",
                "severity": "HIGH",
                "title": "Tighten spread gate",
                "description": "Spread gate allows too many losers",
                "data_points": {"spread_pct": 5.0},
                "suggested_action": "Lower max spread to 6%",
                "estimated_impact": "Improve win rate by 3%",
            }
        ])
        result = parse_insights_response(response)
        assert len(result) == 1
        assert result[0]["category"] == "GATE_TUNING"
        assert result[0]["severity"] == "HIGH"

    def test_handles_markdown_code_blocks(self):
        response = "```json\n" + json.dumps([
            {
                "category": "EXIT_STRATEGY",
                "severity": "MEDIUM",
                "title": "Widen profit target",
                "description": "Leaving MFE on the table",
                "data_points": {},
                "suggested_action": "Raise target to 60%",
                "estimated_impact": "+5% avg return",
            }
        ]) + "\n```"
        result = parse_insights_response(response)
        assert len(result) == 1
        assert result[0]["category"] == "EXIT_STRATEGY"

    def test_malformed_json_returns_empty(self):
        result = parse_insights_response("not json at all")
        assert result == []

    def test_non_array_returns_empty(self):
        result = parse_insights_response('{"not": "an array"}')
        assert result == []

    def test_missing_required_fields_skipped(self):
        response = json.dumps([
            {"category": "GATE_TUNING", "severity": "HIGH"},  # missing fields
            {
                "category": "GATE_TUNING",
                "severity": "LOW",
                "title": "Valid insight",
                "description": "Has all fields",
                "data_points": {},
                "suggested_action": "Do this",
                "estimated_impact": "Some improvement",
            },
        ])
        result = parse_insights_response(response)
        assert len(result) == 1
        assert result[0]["title"] == "Valid insight"

    def test_invalid_category_normalized(self):
        response = json.dumps([
            {
                "category": "INVALID_CATEGORY",
                "severity": "HIGH",
                "title": "Test",
                "description": "Test desc",
                "data_points": {},
                "suggested_action": "Action",
                "estimated_impact": "Impact",
            }
        ])
        result = parse_insights_response(response)
        assert len(result) == 1
        assert result[0]["category"] == "SCORE_OPTIMIZATION"  # default fallback

    def test_empty_response(self):
        result = parse_insights_response("")
        assert result == []

    def test_data_points_non_dict_normalized(self):
        response = json.dumps([
            {
                "category": "TIER_QUALITY",
                "severity": "LOW",
                "title": "Test",
                "description": "Test",
                "data_points": "not a dict",
                "suggested_action": "Action",
                "estimated_impact": "Impact",
            }
        ])
        result = parse_insights_response(response)
        assert len(result) == 1
        assert result[0]["data_points"] == {}


# ============================================================================
# generate_ai_insights tests
# ============================================================================


class TestGenerateAIInsights:

    @pytest.mark.asyncio
    async def test_returns_message_when_too_few_positions(self):
        metrics = PerformanceMetrics(closed_positions=2, total_positions=3)
        with (
            patch(
                "app.paper_trading.insights.calculate_performance_metrics",
                new_callable=AsyncMock,
                return_value=metrics,
            ),
            patch(
                "app.paper_trading.insights.PaperPositionTable"
            ) as mock_table,
            patch(
                "app.paper_trading.insights.CalibrationReportTable"
            ) as mock_cal,
        ):
            mock_table.list_all = AsyncMock(return_value=[])
            mock_cal.list_recent = AsyncMock(return_value=[])

            from app.paper_trading.insights import generate_ai_insights

            result = await generate_ai_insights()

        assert result["insights"] == []
        assert "at least 5" in result["message"]

    @pytest.mark.asyncio
    async def test_returns_insights_from_llm(self):
        metrics = PerformanceMetrics(
            closed_positions=10,
            total_positions=15,
            open_positions=5,
            win_count=6,
            loss_count=4,
        )
        fake_llm_response = MagicMock(
            success=True,
            content=json.dumps([
                {
                    "category": "GATE_TUNING",
                    "severity": "HIGH",
                    "title": "Tighten spread gate",
                    "description": "Data shows spread above 5% correlates with losses",
                    "data_points": {"avg_spread_losers": "7.2%"},
                    "suggested_action": "Lower max spread to 5%",
                    "estimated_impact": "Improve win rate by 3%",
                }
            ]),
            tokens_used=350,
        )
        mock_provider = MagicMock()
        mock_provider.name = "anthropic"
        mock_provider.is_available.return_value = True
        mock_provider.generate = AsyncMock(return_value=fake_llm_response)

        with (
            patch(
                "app.paper_trading.insights.calculate_performance_metrics",
                new_callable=AsyncMock,
                return_value=metrics,
            ),
            patch(
                "app.paper_trading.insights.PaperPositionTable"
            ) as mock_table,
            patch(
                "app.paper_trading.insights.CalibrationReportTable"
            ) as mock_cal,
            patch(
                "app.paper_trading.insights.get_provider",
                return_value=mock_provider,
            ),
            patch(
                "app.paper_trading.insights.compare_tiers",
                return_value={},
            ),
            patch(
                "app.paper_trading.insights.analyze_exit_effectiveness",
                return_value={},
            ),
        ):
            mock_table.list_all = AsyncMock(return_value=[])
            mock_cal.list_recent = AsyncMock(return_value=[])

            from app.paper_trading.insights import generate_ai_insights

            result = await generate_ai_insights()

        assert len(result["insights"]) == 1
        assert result["insights"][0]["title"] == "Tighten spread gate"
        assert result["llm_provider"] == "anthropic"
        assert result["tokens_used"] == 350

    @pytest.mark.asyncio
    async def test_handles_llm_failure_gracefully(self):
        metrics = PerformanceMetrics(
            closed_positions=10,
            total_positions=15,
            open_positions=5,
        )
        fake_llm_response = MagicMock(
            success=False,
            content="",
            tokens_used=0,
        )
        mock_provider = MagicMock()
        mock_provider.name = "anthropic"
        mock_provider.is_available.return_value = True
        mock_provider.generate = AsyncMock(return_value=fake_llm_response)

        with (
            patch(
                "app.paper_trading.insights.calculate_performance_metrics",
                new_callable=AsyncMock,
                return_value=metrics,
            ),
            patch("app.paper_trading.insights.PaperPositionTable") as mock_table,
            patch("app.paper_trading.insights.CalibrationReportTable") as mock_cal,
            patch("app.paper_trading.insights.get_provider", return_value=mock_provider),
            patch("app.paper_trading.insights.compare_tiers", return_value={}),
            patch("app.paper_trading.insights.analyze_exit_effectiveness", return_value={}),
        ):
            mock_table.list_all = AsyncMock(return_value=[])
            mock_cal.list_recent = AsyncMock(return_value=[])

            from app.paper_trading.insights import generate_ai_insights

            result = await generate_ai_insights()

        assert result["insights"] == []


# ============================================================================
# Route tests
# ============================================================================


class TestAIInsightsRoute:

    @pytest.mark.asyncio
    async def test_endpoint_returns_200(self, app):
        mock_result = {
            "insights": [],
            "generated_at": "2026-01-20T12:00:00",
            "data_summary": {
                "positions_analyzed": 0,
                "win_rate": 0,
                "closed_positions": 0,
                "calibration_report_used": None,
            },
            "message": "Need at least 5 closed positions for meaningful insights",
            "llm_provider": None,
            "tokens_used": 0,
        }
        with patch(
            "app.paper_trading.insights.generate_ai_insights",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/ai-insights")
            assert resp.status_code == 200
            data = resp.json()
            assert "insights" in data
            assert "data_summary" in data

    @pytest.mark.asyncio
    async def test_endpoint_handles_error(self, app):
        with patch(
            "app.paper_trading.insights.generate_ai_insights",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM error"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/paper-trading/ai-insights")
            assert resp.status_code == 500
            assert "Insights generation failed" in resp.json()["detail"]
