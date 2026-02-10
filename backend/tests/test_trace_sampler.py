"""Tests for TraceSampler (observability/trace_sampler.py).

Covers get_common_gate_failures, get_highest_reject_scores,
get_lowest_approve_scores, get_tier_1_approvals, get_all_traces,
and helper functions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.observability.trace_sampler import (
    GateFailureSample,
    RepresentativeTraces,
    TraceSample,
    TraceSampler,
    _evaluation_to_sample,
    _sample_to_dict,
    get_representative_traces,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_eval_dict(eval_id, verdict="REJECT", score=50.0, quality_tier=None,
                     failed_gates=None, primary_reason="REJECTED_BY_SCORE"):
    return {
        "evaluation_id": eval_id,
        "underlying_ticker": "AAPL",
        "option_ticker": f"O:AAPL{eval_id}",
        "option_type": "CALL",
        "strike": 185.0,
        "dte": 30,
        "dte_bucket": "B",
        "evaluated_at": "2026-01-17T10:00:00Z",
        "decision": {
            "final_score": score,
            "verdict": verdict,
            "quality_tier": quality_tier,
            "failed_gates": failed_gates or [],
            "primary_reason_code": primary_reason,
        },
    }


# ---------------------------------------------------------------------------
# Tests: _evaluation_to_sample / _sample_to_dict
# ---------------------------------------------------------------------------


class TestHelpers:

    def test_evaluation_to_sample(self):
        ed = _make_eval_dict("e1", score=72.0, verdict="APPROVE")
        sample = _evaluation_to_sample(ed)
        assert isinstance(sample, TraceSample)
        assert sample.evaluation_id == "e1"
        assert sample.final_score == 72.0
        assert sample.verdict == "APPROVE"

    def test_sample_to_dict(self):
        sample = TraceSample(
            evaluation_id="e1", ticker="AAPL", option_ticker="O:AAPL",
            option_type="CALL", strike=185.0, dte=30, dte_bucket="B",
            final_score=72.0, verdict="APPROVE", quality_tier="TIER_1",
            evaluated_at="2026-01-17T10:00:00Z", timestamp="2026-01-17T10:00:00Z",
        )
        d = _sample_to_dict(sample)
        assert d["evaluation_id"] == "e1"
        assert d["final_score"] == 72.0

    def test_representative_traces_to_dict(self):
        rt = RepresentativeTraces()
        d = rt.to_dict()
        assert "common_gate_failures" in d
        assert "summary" in d
        assert d["summary"]["total_rejects_sampled"] == 0


# ---------------------------------------------------------------------------
# Tests: TraceSampler methods
# ---------------------------------------------------------------------------


class TestTraceSamplerGetCommonGateFailures:

    @pytest.mark.asyncio
    async def test_common_gate_failures(self):
        rejects = [
            _make_eval_dict("e1", failed_gates=["GATE_MIN_OI", "GATE_SPREAD"], primary_reason="REJECTED_BY_GATES"),
            _make_eval_dict("e2", failed_gates=["GATE_MIN_OI"], primary_reason="REJECTED_BY_GATES"),
            _make_eval_dict("e3", failed_gates=["GATE_SPREAD"], primary_reason="REJECTED_BY_GATES"),
        ]

        with patch("app.observability.trace_sampler.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=rejects)
            sampler = TraceSampler()
            result = await sampler.get_common_gate_failures()

        assert len(result) >= 1
        # GATE_MIN_OI should be the most common
        assert result[0].gate_id == "GATE_MIN_OI"
        assert result[0].failure_count == 2

    @pytest.mark.asyncio
    async def test_empty_rejects(self):
        with patch("app.observability.trace_sampler.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=[])
            sampler = TraceSampler()
            result = await sampler.get_common_gate_failures()
        assert len(result) == 0


class TestTraceSamplerScores:

    @pytest.mark.asyncio
    async def test_highest_reject_scores(self):
        rejects = [
            _make_eval_dict("e1", score=62.0),
            _make_eval_dict("e2", score=58.0),
            _make_eval_dict("e3", score=64.0),
        ]

        with patch("app.observability.trace_sampler.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=rejects)
            sampler = TraceSampler()
            result = await sampler.get_highest_reject_scores()

        assert len(result) == 3
        assert result[0].final_score == 64.0  # Highest first

    @pytest.mark.asyncio
    async def test_lowest_approve_scores(self):
        approves = [
            _make_eval_dict("e1", verdict="APPROVE", score=76.0),
            _make_eval_dict("e2", verdict="APPROVE", score=85.0),
            _make_eval_dict("e3", verdict="APPROVE", score=72.0),
        ]

        with patch("app.observability.trace_sampler.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=approves)
            sampler = TraceSampler()
            result = await sampler.get_lowest_approve_scores()

        assert len(result) == 3
        assert result[0].final_score == 72.0  # Lowest first

    @pytest.mark.asyncio
    async def test_tier_1_approvals(self):
        approves = [
            _make_eval_dict("e1", verdict="APPROVE", score=92.0, quality_tier="TIER_1"),
            _make_eval_dict("e2", verdict="APPROVE", score=78.0, quality_tier="TIER_2"),
            _make_eval_dict("e3", verdict="APPROVE", score=95.0, quality_tier="TIER_1"),
        ]

        with patch("app.observability.trace_sampler.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=approves)
            sampler = TraceSampler()
            result = await sampler.get_tier_1_approvals()

        assert len(result) == 2  # Only TIER_1


class TestTraceSamplerGetAllTraces:

    @pytest.mark.asyncio
    async def test_get_all_traces(self):
        rejects = [_make_eval_dict("e1", score=62.0, failed_gates=["GATE_OI"], primary_reason="REJECTED_BY_GATES")]
        approves = [_make_eval_dict("e2", verdict="APPROVE", score=78.0, quality_tier="TIER_1")]

        with patch("app.observability.trace_sampler.EvaluationTable") as mock_table:
            # list_by_verdict is called 4 times (REJECT, REJECT, APPROVE, APPROVE)
            mock_table.list_by_verdict = AsyncMock(side_effect=[
                rejects,  # gate failures
                rejects,  # highest rejects
                approves,  # lowest approves
                approves,  # tier 1
            ])

            sampler = TraceSampler()
            result = await sampler.get_all_traces()

        assert isinstance(result, RepresentativeTraces)
        assert result.total_rejects_sampled >= 0


class TestConvenienceFunction:

    @pytest.mark.asyncio
    async def test_get_representative_traces(self):
        with patch("app.observability.trace_sampler.EvaluationTable") as mock_table:
            mock_table.list_by_verdict = AsyncMock(return_value=[])

            result = await get_representative_traces()

        assert isinstance(result, RepresentativeTraces)
