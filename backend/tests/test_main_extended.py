"""Extended tests for main.py.

Covers handler routing (scheduled scan, worker scan, unknown action, API Gateway),
_chunk_list, _run_scheduled_scan, _run_worker_scan, and create_app.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.main import (
    _chunk_list,
    create_app,
    handler,
)


class TestChunkList:

    def test_exact_chunks(self):
        result = _chunk_list([1, 2, 3, 4], 2)
        assert result == [[1, 2], [3, 4]]

    def test_partial_chunk(self):
        result = _chunk_list([1, 2, 3], 2)
        assert result == [[1, 2], [3]]

    def test_single_chunk(self):
        result = _chunk_list([1, 2], 5)
        assert result == [[1, 2]]

    def test_empty_list(self):
        result = _chunk_list([], 3)
        assert result == []

    def test_chunk_size_one(self):
        result = _chunk_list([1, 2, 3], 1)
        assert result == [[1], [2], [3]]


class TestCreateApp:

    def test_app_created(self):
        app = create_app()
        assert app.title is not None


class TestHandler:

    def test_scheduled_scan(self):
        event = {"source": "oss.scheduler", "action": "run_scan"}
        with patch("app.main._run_scheduled_scan", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = {"status": "success"}
            with patch("app.main.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = {"status": "success"}
                result = handler(event, None)
        assert result is not None

    def test_worker_scan(self):
        event = {
            "source": "oss.scheduler",
            "action": "worker_scan",
            "tickers": ["AAPL", "MSFT"],
            "chunk_index": 0,
        }
        with patch("app.main.asyncio") as mock_asyncio:
            mock_asyncio.run.return_value = {"status": "success", "mode": "worker"}
            result = handler(event, None)
        assert result is not None

    def test_unknown_action(self):
        event = {"source": "oss.scheduler", "action": "unknown_thing"}
        result = handler(event, None)
        assert result["status"] == "error"
        assert "Unknown action" in result["error"]

    def test_api_gateway_event(self):
        event = {
            "httpMethod": "GET",
            "path": "/health",
            "headers": {},
            "queryStringParameters": None,
            "body": None,
            "requestContext": {
                "httpMethod": "GET",
                "path": "/health",
                "resourcePath": "/health",
            },
        }
        with patch("app.main._mangum_handler") as mock_mangum:
            mock_mangum.return_value = {"statusCode": 200}
            result = handler(event, None)
        assert result["statusCode"] == 200


class TestRunWorkerScan:

    @pytest.mark.asyncio
    async def test_worker_scan_success(self):
        from app.main import _run_worker_scan
        with patch("app.scanners.orchestrator.ScannerOrchestrator") as mock_orch:
            instance = mock_orch.return_value
            mock_result = MagicMock()
            mock_result.tickers_scanned = 5
            mock_result.opportunities_created = 2
            mock_result.evaluations_created = 1
            mock_result.duration_ms = 1000
            instance.run_scan = AsyncMock(return_value=mock_result)

            result = await _run_worker_scan(["AAPL", "MSFT"])

        assert result["status"] == "success"
        assert result["tickers_scanned"] == 5

    @pytest.mark.asyncio
    async def test_worker_scan_error(self):
        from app.main import _run_worker_scan
        with patch("app.scanners.orchestrator.ScannerOrchestrator") as mock_orch:
            instance = mock_orch.return_value
            instance.run_scan = AsyncMock(side_effect=Exception("scan failed"))

            result = await _run_worker_scan(["AAPL"])

        assert result["status"] == "error"
        assert "scan failed" in result["error"]


class TestRunScheduledScan:

    @pytest.mark.asyncio
    async def test_routes_to_coordinator(self):
        from app.main import _run_scheduled_scan
        with patch("app.main.USE_CHUNKED_PROCESSING", True), \
             patch("app.main._run_coordinator_scan", new_callable=AsyncMock) as mock_coord:
            mock_coord.return_value = {"status": "success", "mode": "coordinator"}
            result = await _run_scheduled_scan()
        assert result["mode"] == "coordinator"

    @pytest.mark.asyncio
    async def test_routes_to_worker(self):
        from app.main import _run_scheduled_scan
        with patch("app.main.USE_CHUNKED_PROCESSING", False), \
             patch("app.main._run_worker_scan", new_callable=AsyncMock) as mock_worker:
            mock_worker.return_value = {"status": "success", "mode": "worker"}
            result = await _run_scheduled_scan()
        assert result["mode"] == "worker"


class TestHandlerUVScanTrigger:

    def test_api_gateway_event_after_asyncio_run(self):
        """Test that handler restores event loop if RuntimeError occurs."""
        event = {
            "httpMethod": "GET",
            "path": "/health",
            "headers": {},
            "queryStringParameters": None,
            "body": None,
            "requestContext": {
                "httpMethod": "GET",
                "path": "/health",
                "resourcePath": "/health",
            },
        }
        with patch("app.main._mangum_handler") as mock_mangum, \
             patch("app.main.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.side_effect = RuntimeError("No event loop")
            mock_mangum.return_value = {"statusCode": 200}
            result = handler(event, None)
        assert result["statusCode"] == 200
        mock_asyncio.set_event_loop.assert_called_once()


class TestRunCoordinatorScan:

    @pytest.mark.asyncio
    async def test_coordinator_small_ticker_list(self):
        """When tickers <= CHUNK_SIZE, process directly."""
        from app.main import _run_coordinator_scan
        with patch("app.main.CHUNK_SIZE", 500), \
             patch("app.db.tables.PolicyTable.get_active", new_callable=AsyncMock) as mock_policy, \
             patch("app.core.watchlist.WatchlistManager.from_policy_async", new_callable=AsyncMock) as mock_wl, \
             patch("app.main._run_worker_scan", new_callable=AsyncMock) as mock_worker:
            mock_p = MagicMock()
            mock_p.config = MagicMock()
            mock_policy.return_value = mock_p
            mock_wl_instance = MagicMock()
            mock_wl_instance.tickers = ["AAPL", "MSFT"]
            mock_wl.return_value = mock_wl_instance
            mock_worker.return_value = {"status": "success", "mode": "worker"}

            result = await _run_coordinator_scan()

        assert result["status"] == "success"
        mock_worker.assert_called_once_with(["AAPL", "MSFT"])

    @pytest.mark.asyncio
    async def test_coordinator_no_active_policy(self):
        """When no active policy, return error."""
        from app.main import _run_coordinator_scan
        with patch("app.db.tables.PolicyTable.get_active", new_callable=AsyncMock) as mock_policy:
            mock_policy.return_value = None
            result = await _run_coordinator_scan()

        assert result["status"] == "error"
        assert "No active policy" in result["error"]

    @pytest.mark.asyncio
    async def test_coordinator_large_ticker_list(self):
        """When tickers > CHUNK_SIZE, dispatch workers asynchronously via Event."""
        from app.main import _run_coordinator_scan
        tickers = [f"TICK{i}" for i in range(150)]

        with patch("app.main.CHUNK_SIZE", 50), \
             patch("app.db.tables.PolicyTable.get_active", new_callable=AsyncMock) as mock_policy, \
             patch("app.core.watchlist.WatchlistManager.from_policy_async", new_callable=AsyncMock) as mock_wl, \
             patch("app.core.pipeline.PipelineOrchestrator") as mock_pipeline_cls, \
             patch("app.db.tables.PipelineRunTable.update", new_callable=AsyncMock) as mock_update, \
             patch("boto3.client") as mock_boto, \
             patch("app.main.time") as mock_time:
            mock_p = MagicMock()
            mock_p.config = MagicMock()
            mock_p.config_version = "v2.0.0"
            mock_policy.return_value = mock_p
            mock_wl_instance = MagicMock()
            mock_wl_instance.tickers = tickers
            mock_wl.return_value = mock_wl_instance

            mock_pipeline = MagicMock()
            mock_pipeline_run = MagicMock()
            mock_pipeline_run.run_id = "run-coord-123"
            mock_pipeline_run.started_at = "2026-02-18T00:00:00+00:00"
            mock_pipeline.start_run = AsyncMock(return_value=mock_pipeline_run)
            mock_pipeline_cls.return_value = mock_pipeline

            mock_lambda = MagicMock()
            mock_boto.return_value = mock_lambda

            result = await _run_coordinator_scan()

        assert result["mode"] == "coordinator"
        assert result["status"] == "success"
        assert result["chunks_dispatched"] == 3
        assert result["total_tickers"] == 150
        # Verify Event invocation type (fire-and-forget)
        assert mock_lambda.invoke.call_count == 3
        for call in mock_lambda.invoke.call_args_list:
            assert call.kwargs.get("InvocationType") == "Event"

    @pytest.mark.asyncio
    async def test_coordinator_worker_dispatch_failure(self):
        """When worker dispatch fails, result should be partial_success."""
        from app.main import _run_coordinator_scan
        tickers = [f"TICK{i}" for i in range(100)]

        with patch("app.main.CHUNK_SIZE", 50), \
             patch("app.db.tables.PolicyTable.get_active", new_callable=AsyncMock) as mock_policy, \
             patch("app.core.watchlist.WatchlistManager.from_policy_async", new_callable=AsyncMock) as mock_wl, \
             patch("app.core.pipeline.PipelineOrchestrator") as mock_pipeline_cls, \
             patch("app.db.tables.PipelineRunTable.update", new_callable=AsyncMock) as mock_update, \
             patch("boto3.client") as mock_boto, \
             patch("app.main.time") as mock_time:
            mock_p = MagicMock()
            mock_p.config = MagicMock()
            mock_p.config_version = "v2.0.0"
            mock_policy.return_value = mock_p
            mock_wl_instance = MagicMock()
            mock_wl_instance.tickers = tickers
            mock_wl.return_value = mock_wl_instance

            mock_pipeline = MagicMock()
            mock_pipeline_run = MagicMock()
            mock_pipeline_run.run_id = "run-coord-456"
            mock_pipeline_run.started_at = "2026-02-18T00:00:00+00:00"
            mock_pipeline.start_run = AsyncMock(return_value=mock_pipeline_run)
            mock_pipeline_cls.return_value = mock_pipeline

            mock_lambda = MagicMock()
            mock_lambda.invoke.side_effect = Exception("Lambda dispatch error")
            mock_boto.return_value = mock_lambda

            result = await _run_coordinator_scan()

        assert result["mode"] == "coordinator"
        assert result["status"] == "partial_success"
        assert result["chunks_failed_to_dispatch"] == 2
