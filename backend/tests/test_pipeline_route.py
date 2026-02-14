"""Tests for api/routes/pipeline.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.pipeline import parse_time_range
from app.config import get_settings
from app.core.schemas import TimeRangeOption
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------



class TestParseTimeRange:
    def test_last_hour(self):
        start, end = parse_time_range(TimeRangeOption.LAST_HOUR)
        assert (end - start).total_seconds() <= 3601

    def test_today(self):
        start, end = parse_time_range(TimeRangeOption.TODAY)
        pacific = get_settings().display_tz
        start_local = start.astimezone(pacific)
        assert start_local.hour == 0

    def test_yesterday(self):
        start, end = parse_time_range(TimeRangeOption.YESTERDAY)
        assert start < end

    def test_last_7_days(self):
        start, end = parse_time_range(TimeRangeOption.LAST_7_DAYS)
        diff = (end - start).days
        assert diff >= 6


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


class TestListRuns:

    @pytest.mark.asyncio
    async def test_list_runs(self, app):
        with patch("app.api.routes.pipeline.pipeline_aggregator") as agg, \
             patch("app.api.routes.pipeline.stage_mapper") as sm:
            agg.get_runs_for_time_range = AsyncMock(return_value=([], 0, False))
            sm.run_to_list_item = MagicMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/pipeline/runs")
            assert resp.status_code == 200


class TestGetRunDetail:

    @pytest.mark.asyncio
    async def test_run_not_found(self, app):
        with patch("app.api.routes.pipeline.orchestrator") as orch:
            orch.get_run = AsyncMock(return_value=None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/pipeline/runs/unknown-id")
            assert resp.status_code == 404
