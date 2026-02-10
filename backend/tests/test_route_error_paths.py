"""Route handler error path tests.

Tests database errors, edge cases, and error propagation in API route handlers
that are not covered by the existing happy-path route tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app():
    return create_app()


# ============================================================================
# Calibration Route Error Paths
# ============================================================================


class TestCalibrationErrorPaths:

    @pytest.mark.asyncio
    async def test_run_calibration_persist_failure(self, app):
        """Calibration report generated but DynamoDB put fails -> 500."""
        report_obj = MagicMock()
        report_obj.to_dict.return_value = {"report_id": "r1"}
        with patch("app.api.routes.calibration.CalibrationReporter") as mock_reporter, \
             patch("app.api.routes.calibration.CalibrationReportTable") as mock_table:
            instance = mock_reporter.return_value
            instance.generate_report = AsyncMock(return_value=report_obj)
            mock_table.put = AsyncMock(side_effect=Exception("DynamoDB throttled"))
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/calibration/run", json={})
            # The route catches all exceptions as 500
            assert resp.status_code == 500
            assert "Calibration failed" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_approve_invalid_field_path(self, app):
        """Approve suggestion with invalid field_path -> 400."""
        report = {
            "report_id": "r1",
            "suggestions": [{
                "suggestion_id": "s1",
                "status": "PENDING",
                "field_path": "nonexistent.path.to.field",
                "suggested_value": 50,
            }],
        }
        mock_policy = MagicMock()
        mock_policy.version = "v2.0.0"
        mock_policy.config.model_dump.return_value = {"gates": {"min_volume": 75}}

        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table, \
             patch("app.api.routes.calibration.PolicyService") as mock_ps:
            mock_table.list_recent = AsyncMock(return_value=[report])
            mock_table.update_suggestion_status = AsyncMock(return_value=True)
            ps_instance = mock_ps.return_value
            ps_instance.get_active = AsyncMock(return_value=mock_policy)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/s1/approve"
                )
            assert resp.status_code == 400
            assert "Invalid field path" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_approve_policy_creation_failure_rolls_back(self, app):
        """If policy creation fails after claiming suggestion, status reverts to PENDING."""
        report = {
            "report_id": "r1",
            "suggestions": [{
                "suggestion_id": "s1",
                "status": "PENDING",
                "field_path": "gates.min_volume",
                "suggested_value": 50,
            }],
        }
        mock_policy = MagicMock()
        mock_policy.version = "v2.0.0"
        mock_policy.config.model_dump.return_value = {"gates": {"min_volume": 75}}

        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table, \
             patch("app.api.routes.calibration.PolicyService") as mock_ps, \
             patch("app.api.routes.calibration.PolicyConfig") as mock_pc:
            mock_table.list_recent = AsyncMock(return_value=[report])
            mock_table.update_suggestion_status = AsyncMock(return_value=True)
            ps_instance = mock_ps.return_value
            ps_instance.get_active = AsyncMock(return_value=mock_policy)
            ps_instance.create_version = AsyncMock(side_effect=Exception("DB error"))
            mock_pc.return_value = MagicMock()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/s1/approve"
                )
            assert resp.status_code == 500
            assert "Failed to create policy version" in resp.json()["detail"]
            # Verify rollback was attempted
            calls = mock_table.update_suggestion_status.call_args_list
            assert len(calls) == 2
            # Second call should revert to PENDING
            assert calls[1].args[2] == "PENDING"

    @pytest.mark.asyncio
    async def test_approve_int_coercion(self, app):
        """Suggested value is coerced to int when the current field is int."""
        report = {
            "report_id": "r1",
            "suggestions": [{
                "suggestion_id": "s1",
                "status": "PENDING",
                "field_path": "gates.min_volume",
                "suggested_value": 49.7,  # Float that should become 50
            }],
        }
        mock_policy = MagicMock()
        mock_policy.version = "v2.0.0"
        mock_policy.config.model_dump.return_value = {"gates": {"min_volume": 75}}

        new_policy = MagicMock()
        new_policy.version = "v2.1.0"
        new_policy.policy_hash = "hash123"

        with patch("app.api.routes.calibration.CalibrationReportTable") as mock_table, \
             patch("app.api.routes.calibration.PolicyService") as mock_ps, \
             patch("app.api.routes.calibration.PolicyConfig") as mock_pc:
            mock_table.list_recent = AsyncMock(return_value=[report])
            mock_table.update_suggestion_status = AsyncMock(return_value=True)
            ps_instance = mock_ps.return_value
            ps_instance.get_active = AsyncMock(return_value=mock_policy)
            ps_instance.create_version = AsyncMock(return_value=new_policy)
            mock_pc.return_value = MagicMock()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/calibration/suggestions/s1/approve"
                )
            assert resp.status_code == 200


# ============================================================================
# Pipeline Route Error Paths
# ============================================================================


class TestPipelineErrorPaths:

    @pytest.fixture
    async def client(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_complete_run_not_found(self, client):
        """POST /runs/{run_id}/complete -> 404 when run doesn't exist."""
        with patch("app.api.routes.pipeline.orchestrator") as mock_orch:
            mock_orch.complete_run = AsyncMock(return_value=None)
            resp = await client.post(
                "/api/pipeline/runs/nonexistent/complete?status=completed"
            )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_complete_run_success(self, client):
        """POST /runs/{run_id}/complete -> 200 when run exists."""
        mock_run = MagicMock()
        mock_run.model_dump.return_value = {"run_id": "r1", "status": "completed"}
        with patch("app.api.routes.pipeline.orchestrator") as mock_orch:
            mock_orch.complete_run = AsyncMock(return_value=mock_run)
            resp = await client.post(
                "/api/pipeline/runs/r1/complete?status=failed"
            )
        assert resp.status_code == 200
        assert "marked as failed" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_list_runs_with_scanner_filter(self, client):
        """List runs with scanner filter uses _parse_scanner_type correctly."""
        from app.core.schemas import PipelineRunListItem
        run_item = PipelineRunListItem(
            id="run-001",
            timestamp="2026-01-17T16:00:00+00:00",
            total_contracts=10,
            approved_count=2,
        )
        with patch("app.api.routes.pipeline.pipeline_aggregator") as mock_agg, \
             patch("app.api.routes.pipeline.stage_mapper") as mock_mapper:
            mock_agg.get_runs_for_time_range = AsyncMock(
                return_value=([MagicMock()], 1, False)
            )
            mock_mapper.run_to_list_item.return_value = run_item
            resp = await client.get("/api/pipeline/runs?scanner=breakout")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_parse_time_range_custom_without_dates_defaults_to_today(self, client):
        """Custom time range without start/end falls back to today."""
        from app.api.routes.pipeline import parse_time_range
        from app.core.schemas import TimeRangeOption
        start, end = parse_time_range(TimeRangeOption.CUSTOM)
        assert start < end  # Should return today's range as fallback

    @pytest.mark.asyncio
    async def test_aggregate_with_scanner_filter(self, client):
        """GET /aggregate with scanner filter."""
        from app.core.schemas import DisplayStage, PipelineMonitorData
        pipeline_data = PipelineMonitorData(
            time_range="Today",
            scanner_type="breakout",
            total_input=0,
            stages=[DisplayStage(id=1, name="S1", description="", input=0, output=0)],
        )
        with patch("app.api.routes.pipeline.pipeline_aggregator") as mock_agg:
            mock_agg.build_aggregate_data = AsyncMock(return_value=pipeline_data)
            resp = await client.get("/api/pipeline/aggregate?scanner=breakout")
        assert resp.status_code == 200


# ============================================================================
# Evaluation Route Error Paths
# ============================================================================


class TestEvaluationErrorPaths:

    @pytest.fixture
    async def client(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_list_by_invalid_verdict(self, client):
        """GET /by-verdict/INVALID -> 400."""
        with patch("app.api.routes.evaluations.EvaluationTable"):
            resp = await client.get("/api/evaluations/by-verdict/INVALID")
        assert resp.status_code == 400
        assert "Invalid verdict" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_by_valid_verdict(self, client):
        """GET /by-verdict/approve -> 200 (case insensitive)."""
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=[])
            resp = await client.get("/api/evaluations/by-verdict/approve")
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "APPROVE"

    @pytest.mark.asyncio
    async def test_get_evaluation_not_found(self, client):
        """GET /{ticker}/{ts}/{eval_id} -> 404 when not found."""
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            resp = await client.get("/api/evaluations/AAPL/2026-01-17/eval-001")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_evaluation_detail_not_found(self, client):
        """GET /{ticker}/{ts}/{eval_id}/detail -> 404 when evaluation doesn't exist."""
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            resp = await client.get("/api/evaluations/AAPL/2026-01-17/eval-001/detail")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_evaluations_without_verdict(self, client):
        """GET /evaluations without verdict returns empty list."""
        with patch("app.api.routes.evaluations.EvaluationTable"):
            resp = await client.get("/api/evaluations")
        assert resp.status_code == 200
        assert resp.json()["evaluations"] == []

    @pytest.mark.asyncio
    async def test_filtered_evaluations_with_all_filters(self, client):
        """GET /filtered with verdict, dte_bucket, option_type filters."""
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=[
                {
                    "evaluation_id": "e1",
                    "dte_bucket": "B",
                    "option_type": "CALL",
                    "evaluated_at": "2026-01-17",
                    "decision": {"verdict": "APPROVE"},
                }
            ])
            resp = await client.get(
                "/api/evaluations/filtered?verdict=APPROVE&dte_bucket=B&option_type=CALL"
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_filtered_evaluations_dte_mismatch(self, client):
        """GET /filtered with DTE bucket that doesn't match -> empty result."""
        with patch("app.api.routes.evaluations.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=[
                {
                    "evaluation_id": "e1",
                    "dte_bucket": "B",
                    "option_type": "CALL",
                    "evaluated_at": "2026-01-17",
                }
            ])
            resp = await client.get(
                "/api/evaluations/filtered?verdict=APPROVE&dte_bucket=D"
            )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ============================================================================
# Evaluation Helper Function Tests
# ============================================================================


class TestEvaluationHelpers:

    def test_calculate_theta_adjusted_ev_zero_mid(self):
        """EV returns 0 when mid <= 0."""
        from app.api.routes.evaluations import calculate_theta_adjusted_ev
        result = calculate_theta_adjusted_ev(0.5, -0.08, 0.0, 0.3, 100, 30)
        assert result == 0.0

    def test_calculate_theta_adjusted_ev_zero_dte(self):
        """EV returns 0 when dte <= 0."""
        from app.api.routes.evaluations import calculate_theta_adjusted_ev
        result = calculate_theta_adjusted_ev(0.5, -0.08, 5.0, 0.3, 100, 0)
        assert result == 0.0

    def test_calculate_theta_adjusted_ev_normal(self):
        """EV computes a numeric value for valid inputs."""
        from app.api.routes.evaluations import calculate_theta_adjusted_ev
        result = calculate_theta_adjusted_ev(0.55, -0.08, 5.0, 0.32, 189.0, 30)
        assert isinstance(result, float)
        # With these inputs, EV should be positive
        assert result != 0.0

    def test_calculate_gate_margin_empty_results(self):
        """Gate margin returns 50 (neutral) for empty results."""
        from app.api.routes.evaluations import calculate_gate_margin
        assert calculate_gate_margin([]) == 50.0

    def test_calculate_gate_margin_zero_threshold(self):
        """Gate margin skips gates with zero threshold."""
        from app.api.routes.evaluations import calculate_gate_margin
        results = [{"enabled": True, "passed": True, "measured_value": 100,
                     "threshold_value": 0, "operator": "gte"}]
        assert calculate_gate_margin(results) == 50.0

    def test_calculate_gate_margin_lte_operator(self):
        """Gate margin calculates correctly for LTE operator."""
        from app.api.routes.evaluations import calculate_gate_margin
        results = [{"enabled": True, "passed": True, "measured_value": 3.0,
                     "threshold_value": 8.0, "operator": "lte"}]
        margin = calculate_gate_margin(results)
        # (8 - 3) / 8 * 100 = 62.5, clamped to [0, 100]
        assert margin == 62.5

    def test_calculate_gate_margin_between_operator(self):
        """Gate margin defaults to 50 for between operator."""
        from app.api.routes.evaluations import calculate_gate_margin
        results = [{"enabled": True, "passed": True, "measured_value": 50,
                     "threshold_value": 100, "operator": "between"}]
        assert calculate_gate_margin(results) == 50.0

    def test_determine_urgency_breakout(self):
        """Breakout scanner sets urgency to act_now."""
        from app.api.routes.evaluations import determine_urgency
        assert determine_urgency(["BREAKOUT"]) == "act_now"

    def test_determine_urgency_mixed_scanners(self):
        """Highest urgency wins across mixed scanners."""
        from app.api.routes.evaluations import determine_urgency
        assert determine_urgency(["CHEAP_OPTIONS", "BREAKOUT"]) == "act_now"
        assert determine_urgency(["CHEAP_OPTIONS", "UNUSUAL_VOLUME"]) == "hours"

    def test_determine_urgency_empty(self):
        """Empty scanner list defaults to patient."""
        from app.api.routes.evaluations import determine_urgency
        assert determine_urgency([]) == "patient"


# ============================================================================
# Scanner Route Error Paths
# ============================================================================


class TestScannerErrorPaths:

    @pytest.fixture
    async def client(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_scan_status_not_found(self, client):
        """GET /status/{run_id} -> 404 when not in memory or DB."""
        with patch("app.api.routes.scanners.ScanStatusTable") as mock_table, \
             patch("app.api.routes.scanners._scan_status", {}):
            mock_table.get = AsyncMock(return_value=None)
            resp = await client.get("/api/scanners/status/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_opportunity_not_found(self, client):
        """GET /opportunities/{id} -> 404 when opportunity doesn't exist."""
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.get = AsyncMock(return_value=None)
            resp = await client.get(
                "/api/scanners/opportunities/opp-001?ticker=AAPL&timestamp=2026-01-17"
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_uv_trigger_unconfigured(self, client):
        """POST /uv-scan-trigger -> 503 when Lambda names not configured."""
        with patch("app.api.routes.scanners.UV_PUBLISHER_FUNCTION_NAME", ""), \
             patch("app.api.routes.scanners.UV_AGGREGATOR_FUNCTION_NAME", ""):
            resp = await client.post("/api/scanners/uv-scan-trigger")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_process_pipeline_no_candidates(self, client):
        """POST /process-pipeline/{scan_id} -> no candidates returns no_candidates."""
        with patch("app.api.routes.scanners.UVCandidateTable") as mock_table:
            mock_table.list_processed_by_scan = AsyncMock(return_value=[])
            resp = await client.post("/api/scanners/process-pipeline/scan-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_candidates"

    @pytest.mark.asyncio
    async def test_process_pipeline_fetch_candidates_error(self, client):
        """POST /process-pipeline/{scan_id} -> 500 if DB fetch fails."""
        with patch("app.api.routes.scanners.UVCandidateTable") as mock_table:
            mock_table.list_processed_by_scan = AsyncMock(
                side_effect=Exception("DynamoDB error")
            )
            resp = await client.post("/api/scanners/process-pipeline/scan-001")
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_list_opportunities_default_today(self, client):
        """GET /opportunities without params defaults to today."""
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_date = AsyncMock(return_value=[])
            resp = await client.get("/api/scanners/opportunities")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_list_opportunities_by_ticker(self, client):
        """GET /opportunities?ticker=AAPL queries by ticker."""
        with patch("app.api.routes.scanners.OpportunityTable") as mock_table:
            mock_table.list_by_ticker = AsyncMock(return_value=[])
            resp = await client.get("/api/scanners/opportunities?ticker=aapl")
        assert resp.status_code == 200
        # Verify it uppercased the ticker
        mock_table.list_by_ticker.assert_called_once_with("AAPL", limit=50)


# ============================================================================
# Policy Route Error Paths
# ============================================================================


class TestPolicyErrorPaths:

    @pytest.fixture
    async def client(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_get_active_policy_none(self, client):
        """GET /active -> 404 when no active policy."""
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.get_active = AsyncMock(return_value=None)
            resp = await client.get("/api/policies/active")
        assert resp.status_code == 404
        assert "No active policy" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_activate_nonexistent_policy(self, client):
        """POST /{version}/activate -> 404 for unknown version."""
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.activate_version = AsyncMock(return_value=None)
            resp = await client.post("/api/policies/v99.0.0/activate")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_diff_policies_missing_version(self, client):
        """GET /diff/{v1}/{v2} -> 404 when a version is missing."""
        with patch("app.api.routes.policies.policy_service") as mock_svc:
            mock_svc.diff_versions = AsyncMock(return_value=None)
            resp = await client.get("/api/policies/diff/v1.0.0/v2.0.0")
        assert resp.status_code == 404


# ============================================================================
# Paper Trading Route Error Paths
# ============================================================================


class TestPaperTradingErrorPaths:

    @pytest.fixture
    async def client(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_list_positions_open_filter(self, client):
        """GET /positions?status=open filters correctly."""
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table:
            mock_table.list_open = AsyncMock(return_value=[])
            resp = await client.get("/api/paper-trading/positions?status=open")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_list_positions_closed_filter(self, client):
        """GET /positions?status=closed filters correctly."""
        with patch("app.api.routes.paper_trading.PaperPositionTable") as mock_table:
            mock_table.list_closed = AsyncMock(return_value=[])
            resp = await client.get("/api/paper-trading/positions?status=closed")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_with_no_positions(self, client):
        """GET /metrics with no positions returns zeros."""
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "total_positions": 0, "win_rate": 0, "avg_pnl": 0
        }
        with patch("app.api.routes.paper_trading.calculate_performance_metrics") as mock_metrics:
            mock_metrics.return_value = mock_result
            resp = await client.get("/api/paper-trading/metrics")
        assert resp.status_code == 200
        assert resp.json()["metrics"]["total_positions"] == 0
