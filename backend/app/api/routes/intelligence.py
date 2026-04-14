"""Intelligence API endpoints (Phase 1 — Nightly Scribe).

Provides:
- GET  /diary                      List diary entries (index rows only).
- GET  /diary/{date}               Fetch a single entry with its full markdown body.
- POST /diary/generate?date=...    Manually (re)run the scribe for a given date.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.db.tables import DiaryTable
from app.intelligence.scribe import run_nightly_scribe
from app.intelligence.storage import get_markdown

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/diary")
async def list_diary_entries(
    from_: Optional[str] = Query(None, alias="from", description="Start YYYY-MM-DD"),
    to: Optional[str] = Query(None, description="End YYYY-MM-DD (inclusive)"),
    entry_type: str = Query("nightly", description="Entry type"),
    limit: int = Query(30, ge=1, le=200),
) -> dict[str, Any]:
    """List diary index rows in reverse-chronological order.

    If ``from``/``to`` are provided, results are filtered to that range.
    Otherwise the most recent ``limit`` entries of ``entry_type`` are returned.
    """
    if from_ and to:
        entries = await DiaryTable.list_by_date_range(
            from_, to, entry_type=entry_type, limit=limit
        )
    else:
        entries = await DiaryTable.list_by_type(entry_type, limit=limit)
    return {
        "entries": [e.model_dump(mode="json") for e in entries],
        "count": len(entries),
    }


@router.get("/diary/{date}")
async def get_diary_entry(date: str, entry_type: str = "nightly") -> dict[str, Any]:
    """Return a single diary entry, including the markdown body from S3."""
    entry = await DiaryTable.get(date, entry_type=entry_type)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No {entry_type} entry for {date}")
    body = get_markdown(entry.s3_key)
    return {
        "entry": entry.model_dump(mode="json"),
        "markdown": body,
    }


@router.post("/diary/generate")
async def generate_diary_entry(
    date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD; omit to generate for today (UTC)",
    ),
) -> dict[str, Any]:
    """Manually (re)run the Nightly Scribe for a given date.

    Runs synchronously; intended for backfill, debugging, and the EventBridge
    manual-trigger flow. The scheduled invocation uses the Lambda event-source
    handler, not this endpoint.
    """
    target_date: Optional[date_cls] = None
    if date:
        try:
            target_date = date_cls.fromisoformat(date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid date: {exc}") from exc

    try:
        entry = await run_nightly_scribe(target_date=target_date)
    except Exception as exc:
        logger.exception("Nightly scribe manual generate failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "status": "ok",
        "entry": entry.model_dump(mode="json"),
    }
