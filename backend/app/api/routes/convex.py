"""Convex Mode API routes.

Exposes:
    - GET /api/convex/evaluations            list recent CONVEX_APPROVE candidates
    - GET /api/convex/evaluations/{ticker}/{evaluation_id}   single record
    - GET /api/convex/runs                   recent pipeline runs (Pipeline Monitor sidebar)
    - GET /api/convex/runs/{run_id}/stage-events   per-ticker journey for a run
    - GET /api/convex/runs/{run_id}/failed-candidates   debug "why didn't X make it?"
    - GET /api/convex/universe                 latest kinetic-universe snapshot

All routes are read-only and tier-aware. Tier filtering uses the GSI1
partition (``TIER#A`` / ``TIER#B`` / ``TIER#C``) so Tier A queries don't
scan the whole table.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from app.db.tables import (
    ConvexEvaluationTable,
    ConvexStageEventTable,
    ConvexUniverseSnapshotTable,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/evaluations")
async def list_evaluations(
    tier: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List recent Convex Mode CONVEX_APPROVE evaluations.

    Args:
        tier: Optional ``A`` / ``B`` / ``C`` filter; case-insensitive.
            When omitted returns evaluations across all tiers, sorted by
            ``generated_at`` descending.
        limit: Max records.
    """
    if tier is not None:
        tier_upper = tier.upper()
        if tier_upper not in ("A", "B", "C"):
            raise HTTPException(
                status_code=400,
                detail="tier must be one of A, B, C",
            )
        evaluations = await ConvexEvaluationTable.list_by_tier(tier_upper, limit=limit)
    else:
        evaluations = await ConvexEvaluationTable.list_recent(limit=limit)

    return {
        "tier": tier.upper() if tier else None,
        "evaluations": [e.model_dump() for e in evaluations],
        "count": len(evaluations),
    }


@router.get("/evaluations/{ticker}/{evaluation_id}")
async def get_evaluation(ticker: str, evaluation_id: str) -> dict[str, Any]:
    """Look up a single Convex evaluation for the Evaluation Detail page.

    Includes the stage payloads embedded on ``decision.convex_stages`` so
    the UI can render the four-stage walkthrough without a second call.
    """
    evaluation = await ConvexEvaluationTable.get_by_id(ticker.upper(), evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Convex evaluation not found")
    return {"evaluation": evaluation.model_dump()}


@router.get("/runs")
async def list_recent_runs(limit: int = 20) -> dict[str, Any]:
    """Return recent Convex pipeline runs derived from finalised evaluations.

    Pipeline Monitor sidebar uses this. Runs that produced zero finalised
    candidates (Stage 2/3/4 rejected everything) are not surfaced — users
    can navigate to a specific run by run_id from CloudWatch if needed.

    Each run reports its run_id, the most recent evaluation timestamp,
    and the per-tier finalised counts. Sorted descending by timestamp.
    """
    # Sample more evaluations than we expect to need — most days produce
    # 0–10 finalised, so limit*30 is a generous upper bound for grouping.
    evaluations = await ConvexEvaluationTable.list_recent(limit=max(limit * 30, 200))

    runs: dict[str, dict[str, Any]] = {}
    for ev in evaluations:
        run_id = ev.run_id
        if run_id not in runs:
            runs[run_id] = {
                "run_id": run_id,
                "generated_at": ev.generated_at,
                "tier_a": 0,
                "tier_b": 0,
                "tier_c": 0,
                "finalised_count": 0,
            }
        # Most recent eval's timestamp wins (list_recent is desc-sorted)
        if ev.generated_at > runs[run_id]["generated_at"]:
            runs[run_id]["generated_at"] = ev.generated_at
        runs[run_id][f"tier_{ev.convex_tier.lower()}"] += 1
        runs[run_id]["finalised_count"] += 1

    sorted_runs = sorted(
        runs.values(), key=lambda r: r["generated_at"], reverse=True
    )[:limit]
    return {"runs": sorted_runs, "count": len(sorted_runs)}


@router.get("/runs/{run_id}/stage-events")
async def list_stage_events(
    run_id: str,
    ticker: Optional[str] = None,
    limit: int = 5000,
) -> dict[str, Any]:
    """Return per-ticker, per-stage events for a Convex pipeline run.

    Drives the Evaluation Detail walkthrough's "show all stage payloads"
    fallback, plus the debug page that lists every candidate's journey.
    """
    if ticker:
        events = await ConvexStageEventTable.list_for_ticker(run_id, ticker.upper())
    else:
        events = await ConvexStageEventTable.list_by_run(run_id, limit=limit)
    return {
        "run_id": run_id,
        "ticker": ticker.upper() if ticker else None,
        "events": [e.model_dump() for e in events],
        "count": len(events),
    }


@router.get("/runs/{run_id}/failed-candidates")
async def list_failed_candidates(run_id: str) -> dict[str, Any]:
    """Per-ticker stage-failure summary for the failed-candidates debug page.

    Groups stage events by ticker and reports the highest stage they
    advanced to before failing, plus the failing stage's summary.
    Tickers that PASSed all four stages are excluded (they appear in
    ``/evaluations`` instead).
    """
    events = await ConvexStageEventTable.list_by_run(run_id)
    by_ticker: dict[str, list] = {}
    for ev in events:
        by_ticker.setdefault(ev.ticker, []).append(ev)

    failures: list[dict[str, Any]] = []
    for ticker, ticker_events in by_ticker.items():
        ticker_events.sort(key=lambda e: e.stage)
        # Find highest PASS, plus the FAIL right after it (if any).
        highest_pass = 0
        fail_event = None
        for ev in ticker_events:
            if ev.payload.result == "PASS":
                highest_pass = ev.stage
            else:
                fail_event = ev
                break
        if fail_event is None:
            # No failure: candidate advanced all the way through.
            continue
        failures.append({
            "ticker": ticker,
            "highest_stage_passed": highest_pass,
            "failed_at_stage": fail_event.stage,
            "failed_stage_name": fail_event.payload.stage_name,
            "summary": fail_event.payload.summary,
        })

    failures.sort(key=lambda f: (-f["highest_stage_passed"], f["ticker"]))
    return {
        "run_id": run_id,
        "failures": failures,
        "count": len(failures),
    }


@router.get("/universe")
async def get_universe() -> dict[str, Any]:
    """Return the most recent kinetic-universe snapshot.

    Useful for surfacing universe membership status on the UI; the
    Evaluation Detail page can show "this ticker is/is not currently in
    the kinetic universe" without consulting Stage 1 directly.
    """
    snapshot = await ConvexUniverseSnapshotTable.get_latest()
    if snapshot is None:
        return {"snapshot": None}
    return {"snapshot": snapshot.model_dump()}
