"""Tests for api/routes/evaluations.py.

Covers pure functions (calculate_theta_adjusted_ev, calculate_gate_margin,
determine_urgency) and route endpoints with mocked DB tables.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.evaluations import (
    calculate_gate_margin,
    calculate_theta_adjusted_ev,
    determine_urgency,
)
from app.main import create_app


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


class TestCalculateThetaAdjustedEV:

    def test_basic_calculation(self):
        ev = calculate_theta_adjusted_ev(
            delta=0.50, theta=-0.08, mid=4.25, iv=0.30,
            underlying_price=180.0, dte=30,
        )
        assert isinstance(ev, float)

    def test_zero_mid(self):
        ev = calculate_theta_adjusted_ev(
            delta=0.50, theta=-0.08, mid=0.0, iv=0.30,
            underlying_price=180.0, dte=30,
        )
        assert ev == 0.0

    def test_zero_dte(self):
        ev = calculate_theta_adjusted_ev(
            delta=0.50, theta=-0.08, mid=4.25, iv=0.30,
            underlying_price=180.0, dte=0,
        )
        assert ev == 0.0

    def test_custom_hold_days(self):
        ev = calculate_theta_adjusted_ev(
            delta=0.50, theta=-0.08, mid=4.25, iv=0.30,
            underlying_price=180.0, dte=30, expected_hold_days=10,
        )
        assert isinstance(ev, float)


class TestCalculateGateMargin:

    def test_empty_gates(self):
        assert calculate_gate_margin([]) == 50.0

    def test_gte_gate(self):
        gates = [{
            "enabled": True, "passed": True,
            "measured_value": 600, "threshold_value": 300,
            "operator": "gte",
        }]
        margin = calculate_gate_margin(gates)
        assert margin > 50.0

    def test_lte_gate(self):
        gates = [{
            "enabled": True, "passed": True,
            "measured_value": 3.0, "threshold_value": 8.0,
            "operator": "lte",
        }]
        margin = calculate_gate_margin(gates)
        assert margin > 50.0

    def test_disabled_gate_ignored(self):
        gates = [{
            "enabled": False, "passed": True,
            "measured_value": 600, "threshold_value": 300,
            "operator": "gte",
        }]
        margin = calculate_gate_margin(gates)
        assert margin == 50.0

    def test_failed_gate_ignored(self):
        gates = [{
            "enabled": True, "passed": False,
            "measured_value": 100, "threshold_value": 300,
            "operator": "gte",
        }]
        margin = calculate_gate_margin(gates)
        assert margin == 50.0

    def test_zero_threshold(self):
        gates = [{
            "enabled": True, "passed": True,
            "measured_value": 100, "threshold_value": 0,
            "operator": "gte",
        }]
        margin = calculate_gate_margin(gates)
        # Zero threshold is skipped
        assert margin == 50.0

    def test_between_operator(self):
        gates = [{
            "enabled": True, "passed": True,
            "measured_value": 30, "threshold_value": 7,
            "operator": "between",
        }]
        margin = calculate_gate_margin(gates)
        assert margin == 50.0  # Default for between

    def test_multiple_gates(self):
        gates = [
            {"enabled": True, "passed": True, "measured_value": 600,
             "threshold_value": 300, "operator": "gte"},
            {"enabled": True, "passed": True, "measured_value": 3.0,
             "threshold_value": 8.0, "operator": "lte"},
        ]
        margin = calculate_gate_margin(gates)
        assert isinstance(margin, float)


class TestDetermineUrgency:

    def test_breakout_act_now(self):
        assert determine_urgency(["BREAKOUT"]) == "act_now"

    def test_breakdown_act_now(self):
        assert determine_urgency(["BREAKDOWN"]) == "act_now"

    def test_unusual_volume_hours(self):
        assert determine_urgency(["UNUSUAL_VOLUME"]) == "hours"

    def test_compression_patient(self):
        assert determine_urgency(["COMPRESSION_EXPANSION"]) == "patient"

    def test_cheap_options_patient(self):
        assert determine_urgency(["CHEAP_OPTIONS"]) == "patient"

    def test_mixed_highest_wins(self):
        # BREAKOUT + UNUSUAL_VOLUME => act_now wins
        assert determine_urgency(["UNUSUAL_VOLUME", "BREAKOUT"]) == "act_now"

    def test_empty_default(self):
        assert determine_urgency([]) == "patient"

    def test_unknown_scanner(self):
        assert determine_urgency(["UNKNOWN"]) == "patient"


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    return create_app()


class TestEvaluationRoutes:

    @pytest.mark.asyncio
    async def test_list_evaluations_no_verdict(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/evaluations")
        assert resp.status_code == 200
        data = resp.json()
        assert "evaluations" in data
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_list_evaluations_with_verdict(self, app):
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/evaluations?verdict=APPROVE")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_evaluation_not_found(self, app):
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/evaluations/AAPL/2026-01-17/eval-1")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_evaluation_found(self, app):
        mock_eval = {"evaluation_id": "eval-1", "underlying_ticker": "AAPL"}
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.get = AsyncMock(return_value=mock_eval)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/evaluations/AAPL/2026-01-17/eval-1")
            assert resp.status_code == 200
            assert resp.json()["evaluation_id"] == "eval-1"

    @pytest.mark.asyncio
    async def test_list_by_verdict_invalid(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/evaluations/by-verdict/INVALID")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_by_verdict_valid(self, app):
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/evaluations/by-verdict/APPROVE")
            assert resp.status_code == 200
            data = resp.json()
            assert data["verdict"] == "APPROVE"

    @pytest.mark.asyncio
    async def test_list_evaluations_filtered(self, app):
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/evaluations/filtered?verdict=APPROVE&dte_bucket=B&option_type=CALL"
                )
            assert resp.status_code == 200
            data = resp.json()
            assert "statistics" in data
            assert data["filters"]["verdict"] == "APPROVE"

    @pytest.mark.asyncio
    async def test_list_evaluations_filtered_no_filters(self, app):
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/evaluations/filtered")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_approve_evaluations_empty(self, app):
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.list_by_verdict_since = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/evaluations/approve")
            assert resp.status_code == 200
            data = resp.json()
            assert data["evaluations"] == []

    @pytest.mark.asyncio
    async def test_get_evaluation_detail_not_found(self, app):
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/evaluations/AAPL/2026-01-17/eval-1/detail"
                )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_evaluation_detail_found(self, app):
        mock_eval = {
            "evaluation_id": "eval-1",
            "underlying_ticker": "AAPL",
            "opportunity_id": None,
            "evaluated_at": "2026-01-17T10:00:00Z",
        }
        with (
            patch("app.api.routes.evaluations.EvaluationTable") as mock_et,
            patch("app.api.routes.evaluations.PillarScoreTable") as mock_ps,
            patch("app.api.routes.evaluations.GateResultTable") as mock_gr,
            patch("app.api.routes.evaluations.PaperPositionTable") as mock_pp,
            patch("app.api.routes.evaluations.FeatureValueTable") as mock_fv,
            patch("app.api.routes.evaluations.TradeThesisTable") as mock_tt,
        ):
            mock_et.get = AsyncMock(return_value=mock_eval)
            mock_ps.list_by_evaluation = AsyncMock(return_value=[])
            mock_gr.list_by_evaluation = AsyncMock(return_value=[])
            mock_pp.get_by_evaluation_id = AsyncMock(return_value=None)
            mock_fv.list_by_evaluation = AsyncMock(return_value=[])
            mock_tt.get_by_evaluation_id = AsyncMock(return_value=None)

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/evaluations/AAPL/2026-01-17/eval-1/detail"
                )
            assert resp.status_code == 200
            data = resp.json()
            assert data["summary"]["pillar_count"] == 0
            assert data["summary"]["has_position"] is False
            assert data["summary"]["has_thesis"] is False

    @pytest.mark.asyncio
    async def test_watch_insights_empty(self, app):
        with (
            patch("app.api.routes.evaluations.EvaluationTable") as mock_et,
        ):
            mock_et.list_by_verdict = AsyncMock(return_value=[])
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/evaluations/watch/insights")
            assert resp.status_code == 200
            data = resp.json()
            assert data["watchCount"] == 0
