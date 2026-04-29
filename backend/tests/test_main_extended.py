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

    def test_convex_daily_run(self):
        event = {"source": "oss.scheduler", "action": "convex_daily_run"}
        with patch("app.main._run_convex_daily_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"status": "ok"}
            with patch("app.main.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = {"status": "ok"}
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


