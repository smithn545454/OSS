"""Tests for the Convex Mode API routes (/api/convex/...).

Uses httpx + ASGITransport against the FastAPI app, with the moto-backed
DynamoDB fixture so route handlers exercise real CRUD paths.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.schemas import (
    ConvexEvaluation,
    ConvexSelectedContract,
    ConvexStageEventRecord,
    ConvexStagePayload,
    ConvexStagesPayload,
    ConvexUniverseEntry,
    ConvexUniverseSnapshot,
    Decision,
    Verdict,
)
from app.db.tables import (
    ConvexEvaluationTable,
    ConvexStageEventTable,
    ConvexUniverseSnapshotTable,
)
from app.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_evaluation(
    ticker: str = "NVDA",
    eval_id: str = "convex-abc-NVDA",
    tier: str = "A",
    generated_at: str = "2026-04-26T22:30:00+00:00",
) -> ConvexEvaluation:
    decision = Decision(
        evaluation_id=eval_id,
        verdict=Verdict.CONVEX_APPROVE,
        final_score=0.0,
        primary_reason_code="CONVEX_APPROVED_BY_TIER",
        supporting_reason_codes=[f"convex_tier_{tier.lower()}", "direction_bullish"],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v4.1.1",
        convex_tier=tier,
        smart_money_confirmation=False,
        position_sizing_recommendation=f"Tier {tier} \u2192 50% of standard sizing",
    )
    return ConvexEvaluation(
        evaluation_id=eval_id,
        run_id="run-abc",
        ticker=ticker,
        direction="bullish",
        convex_tier=tier,
        composite_strength=0.78,
        smart_money_confirmation=False,
        selected_call=ConvexSelectedContract(
            option_ticker=f"O:{ticker}260620C00145000",
            option_type="CALL",
            strike=145,
            expiry="2026-06-20",
            dte=42,
            delta=0.32,
            bid=4.75,
            ask=4.95,
            open_interest=8240,
            volume=1850,
        ),
        decision=decision,
        generated_at=generated_at,
    )


# ---------------------------------------------------------------------------
# /evaluations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_evaluations_empty(fresh_dynamodb_client):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/evaluations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["evaluations"] == []


@pytest.mark.asyncio
async def test_list_evaluations_returns_persisted(fresh_dynamodb_client):
    await ConvexEvaluationTable.put(_make_evaluation(ticker="NVDA", tier="A"))
    await ConvexEvaluationTable.put(
        _make_evaluation(ticker="TSLA", tier="B", eval_id="convex-abc-TSLA")
    )

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/evaluations")
    data = resp.json()
    assert data["count"] == 2
    tickers = {e["ticker"] for e in data["evaluations"]}
    assert tickers == {"NVDA", "TSLA"}


@pytest.mark.asyncio
async def test_list_evaluations_tier_filter(fresh_dynamodb_client):
    await ConvexEvaluationTable.put(_make_evaluation(ticker="NVDA", tier="A"))
    await ConvexEvaluationTable.put(
        _make_evaluation(ticker="TSLA", tier="B", eval_id="convex-abc-TSLA")
    )

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/evaluations?tier=A")
    data = resp.json()
    assert data["tier"] == "A"
    assert data["count"] == 1
    assert data["evaluations"][0]["ticker"] == "NVDA"


@pytest.mark.asyncio
async def test_list_evaluations_invalid_tier(fresh_dynamodb_client):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/evaluations?tier=Z")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /evaluations/{ticker}/{evaluation_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_evaluation_404_when_missing(fresh_dynamodb_client):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/evaluations/NVDA/missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_evaluation_returns_record(fresh_dynamodb_client):
    await ConvexEvaluationTable.put(_make_evaluation(ticker="NVDA", eval_id="ev-1"))

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/evaluations/NVDA/ev-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["evaluation"]["ticker"] == "NVDA"
    assert data["evaluation"]["evaluation_id"] == "ev-1"
    assert data["evaluation"]["convex_tier"] == "A"
    assert data["evaluation"]["selected_call"]["strike"] == 145


# ---------------------------------------------------------------------------
# /runs/{run_id}/stage-events
# ---------------------------------------------------------------------------


def _stage_event(
    run_id: str, ticker: str, stage: int, result: str, summary: str = "x"
) -> ConvexStageEventRecord:
    return ConvexStageEventRecord(
        run_id=run_id,
        ticker=ticker,
        stage=stage,
        payload=ConvexStagePayload(
            stage=stage,
            stage_name=f"Stage {stage}",
            result=result,
            summary=summary,
        ),
    )


@pytest.mark.asyncio
async def test_list_stage_events_for_run(fresh_dynamodb_client):
    await ConvexStageEventTable.put_batch([
        _stage_event("run-1", "NVDA", 1, "PASS"),
        _stage_event("run-1", "NVDA", 2, "PASS"),
        _stage_event("run-1", "TSLA", 1, "PASS"),
        _stage_event("run-1", "TSLA", 2, "FAIL"),
    ])

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/runs/run-1/stage-events")
    data = resp.json()
    assert data["count"] == 4
    tickers = {e["ticker"] for e in data["events"]}
    assert tickers == {"NVDA", "TSLA"}


@pytest.mark.asyncio
async def test_list_stage_events_filter_by_ticker(fresh_dynamodb_client):
    await ConvexStageEventTable.put_batch([
        _stage_event("run-1", "NVDA", 1, "PASS"),
        _stage_event("run-1", "TSLA", 1, "PASS"),
    ])

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/runs/run-1/stage-events?ticker=NVDA")
    data = resp.json()
    assert data["count"] == 1
    assert data["events"][0]["ticker"] == "NVDA"


# ---------------------------------------------------------------------------
# /runs/{run_id}/failed-candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_candidates_summarises_failures(fresh_dynamodb_client):
    # NVDA: passed Stage 1, failed Stage 2 → appears
    # TSLA: passed Stages 1+2, failed Stage 3 → appears (higher rank)
    # AAPL: passed all four → excluded
    await ConvexStageEventTable.put_batch([
        _stage_event("run-1", "NVDA", 1, "PASS"),
        _stage_event("run-1", "NVDA", 2, "FAIL", summary="no catalyst"),
        _stage_event("run-1", "TSLA", 1, "PASS"),
        _stage_event("run-1", "TSLA", 2, "PASS"),
        _stage_event("run-1", "TSLA", 3, "FAIL", summary="vol too rich"),
        _stage_event("run-1", "AAPL", 1, "PASS"),
        _stage_event("run-1", "AAPL", 2, "PASS"),
        _stage_event("run-1", "AAPL", 3, "PASS"),
        _stage_event("run-1", "AAPL", 4, "PASS"),
    ])

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/runs/run-1/failed-candidates")
    data = resp.json()
    failures = data["failures"]
    tickers = {f["ticker"] for f in failures}
    assert tickers == {"NVDA", "TSLA"}
    # TSLA passed more stages, should rank first
    assert failures[0]["ticker"] == "TSLA"
    assert failures[0]["highest_stage_passed"] == 2
    assert failures[0]["failed_at_stage"] == 3
    nvda = next(f for f in failures if f["ticker"] == "NVDA")
    assert nvda["highest_stage_passed"] == 1


# ---------------------------------------------------------------------------
# /runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_empty_when_no_data(fresh_dynamodb_client):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/runs")
    data = resp.json()
    assert data["count"] == 0
    assert data["runs"] == []


@pytest.mark.asyncio
async def test_runs_surface_zero_finalised_runs(fresh_dynamodb_client):
    """A run with stage events but zero finalised candidates must appear.

    Pre-fix the handler derived runs from ConvexEvaluationTable, hiding any
    run where Stage 3/4 rejected everything. This regresses if that ever
    comes back.
    """
    await ConvexStageEventTable.put_batch([
        _stage_event("run-dry", "NVDA", 1, "PASS"),
        _stage_event("run-dry", "NVDA", 2, "PASS"),
        _stage_event("run-dry", "NVDA", 3, "FAIL"),
    ])

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/runs")
    data = resp.json()
    assert data["count"] == 1
    run = data["runs"][0]
    assert run["run_id"] == "run-dry"
    assert run["finalised_count"] == 0
    assert run["tier_a"] == 0
    assert run["universe_size"] == 1
    assert run["stage2_advancers"] == 1
    assert run["stage3_advancers"] == 0


@pytest.mark.asyncio
async def test_runs_join_finalised_tier_counts(fresh_dynamodb_client):
    """When a run has both stage events and finalised evaluations, the
    response merges per-stage advancer counts with per-tier finalised
    counts."""
    await ConvexStageEventTable.put_batch([
        _stage_event("run-good", "NVDA", 1, "PASS"),
        _stage_event("run-good", "NVDA", 2, "PASS"),
        _stage_event("run-good", "NVDA", 3, "PASS"),
        _stage_event("run-good", "NVDA", 4, "PASS"),
    ])
    ev = _make_evaluation(ticker="NVDA", tier="A", eval_id="convex-good-NVDA")
    ev = ev.model_copy(update={"run_id": "run-good"})
    await ConvexEvaluationTable.put(ev)

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/runs")
    data = resp.json()
    assert data["count"] == 1
    run = data["runs"][0]
    assert run["run_id"] == "run-good"
    assert run["tier_a"] == 1
    assert run["finalised_count"] == 1
    assert run["stage4_advancers"] == 1


@pytest.mark.asyncio
async def test_runs_sorted_by_started_at_desc(fresh_dynamodb_client):
    older = ConvexStageEventRecord(
        run_id="run-old",
        ticker="NVDA",
        stage=1,
        payload=ConvexStagePayload(stage=1, stage_name="x", result="PASS", summary="x"),
        recorded_at="2026-04-25T10:00:00+00:00",
    )
    newer = ConvexStageEventRecord(
        run_id="run-new",
        ticker="NVDA",
        stage=1,
        payload=ConvexStagePayload(stage=1, stage_name="x", result="PASS", summary="x"),
        recorded_at="2026-04-30T10:00:00+00:00",
    )
    await ConvexStageEventTable.put_batch([older, newer])

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/runs")
    data = resp.json()
    assert [r["run_id"] for r in data["runs"]] == ["run-new", "run-old"]


# ---------------------------------------------------------------------------
# /universe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_universe_empty(fresh_dynamodb_client):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/universe")
    data = resp.json()
    assert data["snapshot"] is None


@pytest.mark.asyncio
async def test_universe_returns_latest(fresh_dynamodb_client):
    snapshot = ConvexUniverseSnapshot(
        snapshot_date="2026-04-01",
        policy_version="v4.1.1",
        tickers=[ConvexUniverseEntry(ticker="NVDA", sector="Technology")],
        total_count=1,
        sector_distribution={"Technology": 1},
    )
    await ConvexUniverseSnapshotTable.put(snapshot)

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/convex/universe")
    data = resp.json()
    assert data["snapshot"] is not None
    assert data["snapshot"]["snapshot_date"] == "2026-04-01"
    assert data["snapshot"]["total_count"] == 1


# ---------------------------------------------------------------------------
# Daily-runner persistence end-to-end (verifies Phase 7.1 wiring)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_runner_persists_finalised_evaluations(fresh_dynamodb_client):
    """Sanity that finalised candidates land in ConvexEvaluationTable.

    Mirrors the test_convex_daily_runner test that exercises stage-event
    persistence, but exercising the new ConvexEvaluationTable write path.
    """
    from app.convex import ConvexCandidate
    from app.convex.daily_runner import _finalise_and_persist
    from app.convex.pipeline import ConvexPipelineResult, Tier
    from app.core.schemas import ConvexConfig

    # Construct a fully-advanced candidate manually.
    stages = ConvexStagesPayload()
    for n in range(1, 5):
        stages = stages.model_copy(update={
            f"stage_{n}": ConvexStagePayload(
                stage=n,
                stage_name=f"Stage {n}",
                result="PASS",
                summary="x",
                strength=0.85,
            )
        })
    candidate = ConvexCandidate(ticker="NVDA", stages=stages, direction="bullish")

    pipeline_result = ConvexPipelineResult(
        run_id="run-test",
        started_at="2026-04-26T22:30:00+00:00",
        candidates=[candidate],
        universe_size=1,
        stage4_advancers=1,
        tier_a_count=1,
    )
    finalised = await _finalise_and_persist(
        pipeline_result, ConvexConfig(), "v4.1.1", "run-test"
    )
    assert len(finalised) == 1
    assert finalised[0].tier == Tier.A

    # Persisted record should be queryable via the table API.
    persisted = await ConvexEvaluationTable.list_by_tier("A")
    assert len(persisted) == 1
    assert persisted[0].ticker == "NVDA"
    assert persisted[0].convex_tier == "A"
    assert persisted[0].decision.verdict == Verdict.CONVEX_APPROVE
