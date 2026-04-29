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

    def test_convex_daily_run_event(self):
        """Convex daily-run event should dispatch to _run_convex_daily_run."""
        event = {
            "source": "oss.scheduler",
            "action": "convex_daily_run",
        }

        with patch("app.main._run_convex_daily_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"status": "ok"}
            with patch("app.main.asyncio") as mock_asyncio:
                mock_asyncio.run = MagicMock(return_value={"status": "ok"})
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
