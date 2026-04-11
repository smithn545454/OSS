"""Tests for paper_trading/stage.py orchestration.

Covers:
- PaperTradingStage.execute with position creation and shadow tracking
- Empty evaluations (early return)
- Verdict counting (APPROVE vs WATCH positions)
- Shadow candidate type counting (NEAR_MISS, SINGLE_GATE, random)
- run_paper_trading convenience function
- get_actionable_decisions filtering
- summarize_pipeline_results output structure and counts
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.schemas import (
    Decision,
    Evaluation,
    DTEBucket,
    OptionType,
    PipelineStage,
    QualityTier,
    Verdict,
)
from app.paper_trading.stage import (
    PaperTradingStage,
    get_actionable_decisions,
    run_paper_trading,
    summarize_pipeline_results,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_evaluation(eval_id="eval-001", ticker="AAPL"):
    return Evaluation(
        evaluation_id=eval_id,
        opportunity_id="opp-001",
        underlying_ticker=ticker,
        option_ticker=f"O:{ticker}260320C00185000",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=62,
        strike=185.0,
        underlying_price=189.0,
        moneyness_pct=-2.12,
        bid=8.50, ask=8.80, mid=8.65,
        spread_abs=0.30, spread_pct=3.47,
        iv=0.32, delta=0.55, gamma=0.03, theta=-0.08, vega=0.25,
        open_interest=5000, volume=500,
        breakeven_price=193.65,
        required_move_pct=2.46, expected_move_pct=5.0,
        feasibility_ratio=0.49, time_adjusted_feasibility=0.45,
        dte_bucket=DTEBucket.C,
        rank_score=85.0,
        policy_version="v2.0.0",
        policy_hash="test-hash",
    )


def _make_decision(eval_id="eval-001", verdict=Verdict.APPROVE, tier=QualityTier.TIER_2, score=82.0):
    return Decision(
        evaluation_id=eval_id,
        verdict=verdict,
        quality_tier=tier if verdict == Verdict.APPROVE else None,
        final_score=score,
        premium_leverage_score=78.0,
        underlying_behavior_score=85.0,
        setup_quality_score=80.0,
        primary_reason_code="ALL_GATES_PASSED",
        supporting_reason_codes=["STRONG_VOLATILITY"],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v2.0.0",
    )


def _make_orchestrator():
    orch = AsyncMock()
    orch.update_current_stage.return_value = None
    orch.record_stage_event.return_value = MagicMock()
    return orch


# ============================================================================
# PaperTradingStage.execute
# ============================================================================


class TestPaperTradingStageExecute:

    @pytest.mark.asyncio
    async def test_empty_evaluations_returns_zeros(self):
        orch = _make_orchestrator()
        stage = PaperTradingStage(orchestrator=orch)

        result = await stage.execute(
            run_id="run-001",
            evaluations=[],
            decisions={},
            gate_evaluations={},
        )

        assert result["positions_created"] == 0
        assert result["shadow_candidates"] == 0
        orch.record_stage_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_positions_and_counts_verdicts(self):
        orch = _make_orchestrator()
        stage = PaperTradingStage(orchestrator=orch)

        evals = [_make_evaluation("e1"), _make_evaluation("e2")]
        decisions = {
            "e1": _make_decision("e1", Verdict.APPROVE),
            "e2": _make_decision("e2", Verdict.WATCH),
        }

        # Mock position creation to return positions with verdict_at_entry
        mock_pos_approve = MagicMock()
        mock_pos_approve.verdict_at_entry = Verdict.APPROVE
        mock_pos_watch = MagicMock()
        mock_pos_watch.verdict_at_entry = Verdict.WATCH

        with patch(
            "app.paper_trading.stage.create_positions_from_decisions",
            new_callable=AsyncMock,
            return_value=[mock_pos_approve, mock_pos_watch],
        ), patch(
            "app.paper_trading.stage.select_shadow_candidates",
            return_value=[],
        ), patch(
            "app.paper_trading.stage.create_shadow_positions",
            return_value=[],
        ):
            result = await stage.execute(
                run_id="run-001",
                evaluations=evals,
                decisions=decisions,
                gate_evaluations={},
            )

        assert result["positions_created"] == 2
        assert result["approve_positions"] == 1
        assert result["watch_positions"] == 1

    @pytest.mark.asyncio
    async def test_counts_shadow_types(self):
        orch = _make_orchestrator()
        stage = PaperTradingStage(orchestrator=orch)

        evals = [_make_evaluation("e1")]
        decisions = {"e1": _make_decision("e1", Verdict.REJECT)}

        shadow1 = MagicMock(sample_type="NEAR_MISS")
        shadow2 = MagicMock(sample_type="SINGLE_GATE")
        shadow3 = MagicMock(sample_type="RANDOM")

        with patch(
            "app.paper_trading.stage.create_positions_from_decisions",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.paper_trading.stage.select_shadow_candidates",
            return_value=["e1"],
        ), patch(
            "app.paper_trading.stage.create_shadow_positions",
            return_value=[shadow1, shadow2, shadow3],
        ):
            result = await stage.execute(
                run_id="run-001",
                evaluations=evals,
                decisions=decisions,
                gate_evaluations={},
            )

        assert result["shadow_candidates"] == 1
        assert result["near_miss_shadows"] == 1
        assert result["single_gate_shadows"] == 1
        assert result["random_shadows"] == 1

    @pytest.mark.asyncio
    async def test_skip_positions_when_create_positions_false(self):
        orch = _make_orchestrator()
        stage = PaperTradingStage(orchestrator=orch)

        evals = [_make_evaluation("e1")]
        decisions = {"e1": _make_decision("e1", Verdict.APPROVE)}

        with patch(
            "app.paper_trading.stage.select_shadow_candidates",
            return_value=[],
        ), patch(
            "app.paper_trading.stage.create_shadow_positions",
            return_value=[],
        ):
            result = await stage.execute(
                run_id="run-001",
                evaluations=evals,
                decisions=decisions,
                gate_evaluations={},
                create_positions=False,
            )

        assert result["positions_created"] == 0

    @pytest.mark.asyncio
    async def test_skip_shadows_when_track_shadows_false(self):
        orch = _make_orchestrator()
        stage = PaperTradingStage(orchestrator=orch)

        evals = [_make_evaluation("e1")]
        decisions = {"e1": _make_decision("e1", Verdict.REJECT)}

        with patch(
            "app.paper_trading.stage.create_positions_from_decisions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await stage.execute(
                run_id="run-001",
                evaluations=evals,
                decisions=decisions,
                gate_evaluations={},
                track_shadows=False,
            )

        assert result["shadow_candidates"] == 0

    @pytest.mark.asyncio
    async def test_records_stage_event_with_correct_items(self):
        orch = _make_orchestrator()
        stage = PaperTradingStage(orchestrator=orch)

        evals = [_make_evaluation("e1")]
        decisions = {"e1": _make_decision("e1", Verdict.APPROVE)}

        mock_pos = MagicMock()
        mock_pos.verdict_at_entry = Verdict.APPROVE

        with patch(
            "app.paper_trading.stage.create_positions_from_decisions",
            new_callable=AsyncMock,
            return_value=[mock_pos],
        ), patch(
            "app.paper_trading.stage.select_shadow_candidates",
            return_value=[],
        ), patch(
            "app.paper_trading.stage.create_shadow_positions",
            return_value=[],
        ):
            await stage.execute(
                run_id="run-001",
                evaluations=evals,
                decisions=decisions,
                gate_evaluations={},
            )

        call_kwargs = orch.record_stage_event.call_args
        assert call_kwargs.kwargs["items_in"] == 1
        assert call_kwargs.kwargs["items_out"] == 1  # 1 position, 0 shadows


# ============================================================================
# run_paper_trading convenience function
# ============================================================================


class TestRunPaperTrading:

    @pytest.mark.asyncio
    async def test_delegates_to_stage(self):
        orch = _make_orchestrator()

        with patch(
            "app.paper_trading.stage.create_positions_from_decisions",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.paper_trading.stage.select_shadow_candidates",
            return_value=[],
        ), patch(
            "app.paper_trading.stage.create_shadow_positions",
            return_value=[],
        ):
            result = await run_paper_trading(
                run_id="run-001",
                evaluations=[],
                decisions={},
                gate_evaluations={},
                orchestrator=orch,
            )

        assert result["positions_created"] == 0


# ============================================================================
# get_actionable_decisions
# ============================================================================


class TestGetActionableDecisions:

    def test_filters_approve_and_watch(self):
        decisions = {
            "e1": _make_decision("e1", Verdict.APPROVE),
            "e2": _make_decision("e2", Verdict.WATCH),
            "e3": _make_decision("e3", Verdict.REJECT),
        }

        actionable = get_actionable_decisions(decisions)
        assert len(actionable) == 2
        ids = {a[0] for a in actionable}
        assert ids == {"e1", "e2"}

    def test_empty_decisions(self):
        assert get_actionable_decisions({}) == []

    def test_all_rejects(self):
        decisions = {
            "e1": _make_decision("e1", Verdict.REJECT),
            "e2": _make_decision("e2", Verdict.REJECT),
        }
        assert get_actionable_decisions(decisions) == []


# ============================================================================
# summarize_pipeline_results
# ============================================================================


class TestSummarizePipelineResults:

    def test_counts_verdicts_correctly(self):
        evals = [_make_evaluation(f"e{i}") for i in range(5)]
        decisions = {
            "e0": _make_decision("e0", Verdict.APPROVE, tier=QualityTier.TIER_1, score=90),
            "e1": _make_decision("e1", Verdict.APPROVE, tier=QualityTier.TIER_2, score=82),
            "e2": _make_decision("e2", Verdict.WATCH),
            "e3": _make_decision("e3", Verdict.REJECT),
            "e4": _make_decision("e4", Verdict.REJECT),
        }
        pt_results = {
            "positions_created": 3,
            "approve_positions": 2,
            "watch_positions": 1,
            "shadow_candidates": 2,
            "near_miss_shadows": 1,
            "single_gate_shadows": 0,
            "random_shadows": 1,
        }

        summary = summarize_pipeline_results(evals, decisions, pt_results)

        assert summary["total_evaluations"] == 5
        assert summary["verdicts"]["approve"] == 2
        assert summary["verdicts"]["watch"] == 1
        assert summary["verdicts"]["reject"] == 2
        assert summary["quality_tiers"]["TIER_1"] == 1
        assert summary["quality_tiers"]["TIER_2"] == 1
        assert summary["paper_trading"]["positions_created"] == 3
        assert summary["shadow_tracking"]["total_candidates"] == 2

    def test_empty_inputs(self):
        summary = summarize_pipeline_results([], {}, {})
        assert summary["total_evaluations"] == 0
        assert summary["verdicts"]["approve"] == 0
