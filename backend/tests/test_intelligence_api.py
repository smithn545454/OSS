"""Tests for the intelligence API router (Phase 1 — Nightly Scribe)."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.schemas import DiaryEntry
from app.db.tables import DiaryTable
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _entry(d: str, summary: str = "A day") -> DiaryEntry:
    return DiaryEntry(
        date=d,
        entry_type="nightly",
        summary=summary,
        s3_key=f"diary/{d}/nightly.md",
        metrics={"approves": 3, "watches": 10, "rejects": 200},
        policy_version="v3.1.1",
    )


class TestListDiaryEntries:
    def test_default_lists_recent(self, client, fresh_dynamodb_client):
        import asyncio

        async def _seed():
            for d in ["2026-04-10", "2026-04-12", "2026-04-14"]:
                await DiaryTable.put(_entry(d))

        asyncio.run(_seed())

        response = client.get("/api/intelligence/diary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 3
        dates = [e["date"] for e in payload["entries"]]
        assert dates == ["2026-04-14", "2026-04-12", "2026-04-10"]

    def test_respects_date_range(self, client, fresh_dynamodb_client):
        import asyncio

        async def _seed():
            for d in ["2026-04-09", "2026-04-10", "2026-04-11", "2026-04-14"]:
                await DiaryTable.put(_entry(d))

        asyncio.run(_seed())

        response = client.get(
            "/api/intelligence/diary?from=2026-04-10&to=2026-04-11"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        dates = sorted(e["date"] for e in payload["entries"])
        assert dates == ["2026-04-10", "2026-04-11"]


class TestGetDiaryEntry:
    def test_returns_entry_and_markdown(self, client, fresh_dynamodb_client):
        import asyncio

        async def _seed():
            await DiaryTable.put(_entry("2026-04-14"))

        asyncio.run(_seed())

        with patch(
            "app.api.routes.intelligence.get_markdown",
            return_value="# Headline\n\nA full day of scanning.",
        ) as mock_get:
            response = client.get("/api/intelligence/diary/2026-04-14")

        assert response.status_code == 200
        mock_get.assert_called_once_with("diary/2026-04-14/nightly.md")
        payload = response.json()
        assert payload["entry"]["date"] == "2026-04-14"
        assert "A full day of scanning" in payload["markdown"]

    def test_returns_404_when_missing(self, client, fresh_dynamodb_client):
        response = client.get("/api/intelligence/diary/1999-01-01")
        assert response.status_code == 404


class TestGenerateDiaryEntry:
    def test_runs_scribe_and_persists(self, client, fresh_dynamodb_client):
        fake_entry = _entry("2026-04-14", summary="Generated day")
        fake_run = AsyncMock(return_value=fake_entry)
        with patch("app.api.routes.intelligence.run_nightly_scribe", fake_run):
            response = client.post(
                "/api/intelligence/diary/generate?date=2026-04-14"
            )

        assert response.status_code == 200
        fake_run.assert_awaited_once()
        kwargs = fake_run.await_args.kwargs
        assert kwargs["target_date"] == date(2026, 4, 14)
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["entry"]["summary"] == "Generated day"

    def test_rejects_invalid_date(self, client, fresh_dynamodb_client):
        response = client.post("/api/intelligence/diary/generate?date=not-a-date")
        assert response.status_code == 400

    def test_reports_scribe_failure_as_500(self, client, fresh_dynamodb_client):
        fake_run = AsyncMock(side_effect=RuntimeError("LLM down"))
        with patch("app.api.routes.intelligence.run_nightly_scribe", fake_run):
            response = client.post(
                "/api/intelligence/diary/generate?date=2026-04-14"
            )
        assert response.status_code == 500
        assert "LLM down" in response.json().get("detail", "")
