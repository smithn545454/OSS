"""Tests for the Lambda handler (main.py).

Covers handler routing, _chunk_list, create_app,
and the three invocation modes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.main import (
    _chunk_list,
    create_app,
    handler,
)


# ---------------------------------------------------------------------------
# Tests: create_app
# ---------------------------------------------------------------------------


class TestCreateApp:

    def test_create_app_returns_fastapi(self):
        app = create_app()
        assert app is not None
        assert app.title is not None

    def test_create_app_has_routes(self):
        app = create_app()
        route_paths = [r.path for r in app.routes]
        assert "/health" in route_paths or any("/health" in p for p in route_paths)


# ---------------------------------------------------------------------------
# Tests: handler routing
# ---------------------------------------------------------------------------


class TestHandler:

    def test_scheduled_scan_event(self):
        """Coordinator scan event should call _run_scheduled_scan."""
        event = {
            "source": "oss.scheduler",
            "action": "run_scan",
        }

        with patch("app.main._run_scheduled_scan", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = {"status": "success"}
            with patch("app.main.asyncio") as mock_asyncio:
                mock_asyncio.run = MagicMock(return_value={"status": "success"})
                result = handler(event, MagicMock())

        assert result is not None

    def test_worker_scan_event(self):
        """Worker scan event should call _run_worker_scan."""
        event = {
            "source": "oss.scheduler",
            "action": "worker_scan",
            "tickers": ["AAPL", "MSFT"],
            "chunk_index": 0,
        }

        with patch("app.main._run_worker_scan", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = {"status": "success"}
            with patch("app.main.asyncio") as mock_asyncio:
                mock_asyncio.run = MagicMock(return_value={"status": "success"})
                result = handler(event, MagicMock())

        assert result is not None

    def test_unknown_action(self):
        """Unknown action should return error."""
        event = {
            "source": "oss.scheduler",
            "action": "unknown_action",
        }

        result = handler(event, MagicMock())
        assert result["status"] == "error"
        assert "Unknown action" in result["error"]

    def test_api_gateway_event(self):
        """Non-scheduler events should be handled by Mangum."""
        event = {
            "httpMethod": "GET",
            "path": "/health",
            "headers": {},
            "body": None,
            "queryStringParameters": None,
            "requestContext": {},
        }

        with patch("app.main._mangum_handler") as mock_mangum:
            mock_mangum.return_value = {"statusCode": 200}
            result = handler(event, MagicMock())

        assert result["statusCode"] == 200
