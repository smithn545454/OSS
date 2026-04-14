"""Tests for DiaryTable (Phase 1 — Nightly Scribe).

Uses moto-backed DynamoDB via the fresh_dynamodb_client fixture so we exercise
the real PK/SK and GSI1 query paths end-to-end.
"""

from __future__ import annotations

import pytest

from app.core.schemas import DiaryEntry
from app.db.tables import DiaryTable


def _make_entry(date: str, entry_type: str = "nightly", summary: str = "A day") -> DiaryEntry:
    return DiaryEntry(
        date=date,
        entry_type=entry_type,
        summary=summary,
        s3_key=f"diary/{date}/{entry_type}.md",
        metrics={"approves": 3, "watches": 10, "rejects": 200},
        policy_version="v3.1.1",
    )


class TestDiaryTable:

    @pytest.mark.asyncio
    async def test_put_and_get_roundtrip(self, fresh_dynamodb_client):
        entry = _make_entry("2026-04-14")
        await DiaryTable.put(entry)

        fetched = await DiaryTable.get("2026-04-14")
        assert fetched is not None
        assert fetched.date == "2026-04-14"
        assert fetched.entry_type == "nightly"
        assert fetched.summary == "A day"
        assert fetched.s3_key == "diary/2026-04-14/nightly.md"
        assert fetched.metrics == {"approves": 3, "watches": 10, "rejects": 200}
        assert fetched.policy_version == "v3.1.1"
        assert fetched.regime_tag is None

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, fresh_dynamodb_client):
        result = await DiaryTable.get("1999-01-01")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_by_type_reverse_chronological(self, fresh_dynamodb_client):
        for d in ["2026-04-10", "2026-04-12", "2026-04-11", "2026-04-14"]:
            await DiaryTable.put(_make_entry(d))

        entries = await DiaryTable.list_by_type("nightly")
        dates = [e.date for e in entries]
        assert dates == ["2026-04-14", "2026-04-12", "2026-04-11", "2026-04-10"]

    @pytest.mark.asyncio
    async def test_list_by_type_respects_limit(self, fresh_dynamodb_client):
        for d in ["2026-04-10", "2026-04-11", "2026-04-12"]:
            await DiaryTable.put(_make_entry(d))

        entries = await DiaryTable.list_by_type("nightly", limit=2)
        assert len(entries) == 2
        assert entries[0].date == "2026-04-12"
        assert entries[1].date == "2026-04-11"

    @pytest.mark.asyncio
    async def test_list_by_type_filters_by_entry_type(self, fresh_dynamodb_client):
        # Two entries on the same date but different types — must coexist.
        await DiaryTable.put(_make_entry("2026-04-14", entry_type="nightly"))
        await DiaryTable.put(_make_entry("2026-04-14", entry_type="weekly_summary"))

        nightly = await DiaryTable.list_by_type("nightly")
        weekly = await DiaryTable.list_by_type("weekly_summary")

        assert len(nightly) == 1 and nightly[0].entry_type == "nightly"
        assert len(weekly) == 1 and weekly[0].entry_type == "weekly_summary"

    @pytest.mark.asyncio
    async def test_list_by_date_range(self, fresh_dynamodb_client):
        for d in ["2026-04-09", "2026-04-10", "2026-04-11", "2026-04-12", "2026-04-14"]:
            await DiaryTable.put(_make_entry(d))

        entries = await DiaryTable.list_by_date_range("2026-04-10", "2026-04-12")
        dates = [e.date for e in entries]
        assert dates == ["2026-04-12", "2026-04-11", "2026-04-10"]

    @pytest.mark.asyncio
    async def test_put_overwrites_same_date_and_type(self, fresh_dynamodb_client):
        await DiaryTable.put(_make_entry("2026-04-14", summary="first"))
        await DiaryTable.put(_make_entry("2026-04-14", summary="second"))

        fetched = await DiaryTable.get("2026-04-14")
        assert fetched is not None
        assert fetched.summary == "second"

        # Only one row visible in the GSI listing.
        entries = await DiaryTable.list_by_type("nightly")
        assert len(entries) == 1
