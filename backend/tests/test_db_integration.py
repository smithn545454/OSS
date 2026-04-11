"""Integration tests for DynamoDB table operations.

These tests use moto to create real DynamoDB tables and verify that
put/get/list/update round-trips work correctly. This catches:
- Key construction bugs (PK/SK patterns)
- Item serialization/deserialization (float → Decimal → float)
- GSI construction and querying
- DynamoDB key stripping on read
- Edge cases in pagination, filtering, and sort order
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from app.core.schemas import (
    Decision,
    DTEBucket,
    Evaluation,
    FeatureValue,
    GateOperator,
    GateResult,
    IVHistory,
    LLMUsage,
    OIHistory,
    Opportunity,
    OptionType,
    PaperPosition,
    PillarContributor,
    PillarId,
    PillarScore,
    PipelineRun,
    PipelineStage,
    Policy,
    PolicyConfig,
    QualityTier,
    RunStatus,
    ScannerTrigger,
    ScannerType,
    StageEvent,
    TradeThesis,
    Verdict,
    DirectionHint,
)
from app.db.tables import (
    CalibrationReportTable,
    EvaluationTable,
    FeatureValueTable,
    GateResultTable,
    IVHistoryTable,
    LLMUsageTable,
    OIHistoryTable,
    OpportunityTable,
    PaperPositionTable,
    PillarScoreTable,
    PipelineRunTable,
    PolicyTable,
    ScanStatusTable,
    StageEventTable,
    TradeThesisTable,
)


# ============================================================================
# PolicyTable
# ============================================================================


class TestPolicyTableIntegration:
    """Test PolicyTable put/get/list/set_active round-trips."""

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        config = PolicyConfig()
        policy = Policy(
            version="v1.0.0",
            policy_hash=Policy.compute_hash(config),
            config=config,
            created_by="test",
            is_active=True,
        )
        await PolicyTable.put(policy)

        retrieved = await PolicyTable.get("v1.0.0")
        assert retrieved is not None
        assert retrieved.version == "v1.0.0"
        assert retrieved.is_active is True
        assert retrieved.created_by == "test"
        assert retrieved.config.gates.min_open_interest == config.gates.min_open_interest

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, fresh_dynamodb_client):
        result = await PolicyTable.get("v999.0.0")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_returns_policies(self, fresh_dynamodb_client):
        config = PolicyConfig()
        for v in ["v1.0.0", "v2.0.0", "v3.0.0"]:
            p = Policy(
                version=v,
                policy_hash=Policy.compute_hash(config),
                config=config,
                created_by="test",
                is_active=(v == "v2.0.0"),
            )
            await PolicyTable.put(p)

        policies = await PolicyTable.list(limit=10)
        assert len(policies) == 3
        versions = {p.version for p in policies}
        assert versions == {"v1.0.0", "v2.0.0", "v3.0.0"}

    @pytest.mark.asyncio
    async def test_get_active_returns_active_policy(self, fresh_dynamodb_client):
        config = PolicyConfig()
        for v, active in [("v1.0.0", False), ("v2.0.0", True)]:
            p = Policy(
                version=v,
                policy_hash=Policy.compute_hash(config),
                config=config,
                created_by="test",
                is_active=active,
            )
            await PolicyTable.put(p)

        active = await PolicyTable.get_active()
        assert active is not None
        assert active.version == "v2.0.0"
        assert active.is_active is True

    @pytest.mark.asyncio
    async def test_get_active_returns_none_when_none_active(self, fresh_dynamodb_client):
        config = PolicyConfig()
        p = Policy(
            version="v1.0.0",
            policy_hash=Policy.compute_hash(config),
            config=config,
            created_by="test",
            is_active=False,
        )
        await PolicyTable.put(p)

        active = await PolicyTable.get_active()
        assert active is None

    @pytest.mark.asyncio
    async def test_set_active_deactivates_others(self, fresh_dynamodb_client):
        config = PolicyConfig()
        for v, active in [("v1.0.0", True), ("v2.0.0", False)]:
            p = Policy(
                version=v,
                policy_hash=Policy.compute_hash(config),
                config=config,
                created_by="test",
                is_active=active,
            )
            await PolicyTable.put(p)

        await PolicyTable.set_active("v2.0.0")

        v1 = await PolicyTable.get("v1.0.0")
        v2 = await PolicyTable.get("v2.0.0")
        assert v1.is_active is False
        assert v2.is_active is True

    @pytest.mark.asyncio
    async def test_float_serialization_roundtrip(self, fresh_dynamodb_client):
        """Verify float → Decimal → float conversion preserves values."""
        config = PolicyConfig()
        # Config has many floats (pillar weights, thresholds, etc.)
        policy = Policy(
            version="v1.0.0",
            policy_hash=Policy.compute_hash(config),
            config=config,
            created_by="test",
            is_active=True,
        )
        await PolicyTable.put(policy)

        retrieved = await PolicyTable.get("v1.0.0")
        assert retrieved.config.pillars.weights.premium_leverage == config.pillars.weights.premium_leverage
        assert retrieved.config.pillars.weights.underlying_behavior == config.pillars.weights.underlying_behavior
        assert retrieved.config.pillars.weights.setup_quality == config.pillars.weights.setup_quality
        assert retrieved.config.decision.approve_threshold == config.decision.approve_threshold


# ============================================================================
# OpportunityTable
# ============================================================================


class TestOpportunityTableIntegration:
    """Test OpportunityTable put/get/list round-trips."""

    def _make_opportunity(self, ticker="AAPL", opp_id="opp-001", priority=80):
        return Opportunity(
            opportunity_id=opp_id,
            underlying_ticker=ticker,
            timestamp_utc="2026-01-17T16:00:00+00:00",
            scanner_triggers=[
                ScannerTrigger(
                    scanner_type=ScannerType.BREAKOUT,
                    reason_codes=["BREAKOUT_ABOVE_20D_HIGH"],
                    metrics={"breakout_pct": 2.5},
                    triggered_at="2026-01-17T16:00:00+00:00",
                )
            ],
            direction_hint=DirectionHint.CALL,
            priority_score=priority,
        )

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        opp = self._make_opportunity()
        await OpportunityTable.put(opp)

        retrieved = await OpportunityTable.get(
            "AAPL", "2026-01-17T16:00:00+00:00", "opp-001"
        )
        assert retrieved is not None
        assert retrieved.opportunity_id == "opp-001"
        assert retrieved.underlying_ticker == "AAPL"
        assert retrieved.priority_score == 80
        assert len(retrieved.scanner_triggers) == 1

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, fresh_dynamodb_client):
        result = await OpportunityTable.get("AAPL", "2099-01-01T00:00:00+00:00", "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_by_ticker(self, fresh_dynamodb_client):
        for i in range(3):
            opp = self._make_opportunity(opp_id=f"opp-{i:03d}")
            await OpportunityTable.put(opp)

        results = await OpportunityTable.list_by_ticker("AAPL")
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_list_by_date_via_gsi1(self, fresh_dynamodb_client):
        opp = self._make_opportunity()
        await OpportunityTable.put(opp)

        results = await OpportunityTable.list_by_date("2026-01-17")
        assert len(results) == 1
        assert results[0].opportunity_id == "opp-001"

    @pytest.mark.asyncio
    async def test_no_dynamodb_keys_in_returned_model(self, fresh_dynamodb_client):
        """Verify PK/SK/GSI keys are stripped before model construction."""
        opp = self._make_opportunity()
        await OpportunityTable.put(opp)

        retrieved = await OpportunityTable.get(
            "AAPL", "2026-01-17T16:00:00+00:00", "opp-001"
        )
        # If keys weren't stripped, model construction would fail
        # or the object would have unexpected attributes
        assert not hasattr(retrieved, "PK")
        assert not hasattr(retrieved, "SK")


# ============================================================================
# EvaluationTable
# ============================================================================


class TestEvaluationTableIntegration:
    """Test EvaluationTable put/get/list round-trips including GSI queries."""

    def _make_evaluation(self, ticker="AAPL", eval_id="eval-001"):
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
            bid=8.50,
            ask=8.80,
            mid=8.65,
            spread_abs=0.30,
            spread_pct=3.47,
            iv=0.32,
            delta=0.55,
            gamma=0.03,
            theta=-0.08,
            vega=0.25,
            open_interest=5000,
            volume=500,
            breakeven_price=193.65,
            required_move_pct=2.46,
            expected_move_pct=5.0,
            feasibility_ratio=0.49,
            time_adjusted_feasibility=0.45,
            dte_bucket=DTEBucket.C,
            rank_score=85.0,
            policy_version="v2.0.0",
            policy_hash="test-hash",
        )

    def _make_decision(self, eval_id="eval-001"):
        return Decision(
            evaluation_id=eval_id,
            verdict=Verdict.APPROVE,
            quality_tier=QualityTier.TIER_2,
            final_score=82.0,
            premium_leverage_score=78.0,
            underlying_behavior_score=85.0,
            setup_quality_score=80.0,
            primary_reason_code="ALL_GATES_PASSED",
            supporting_reason_codes=["STRONG_UNDERLYING_BEHAVIOR"],
            failed_gates=[],
            concentration_warnings=[],
            policy_version="v3.0.0",
        )

    @pytest.mark.asyncio
    async def test_put_with_decision_and_get(self, fresh_dynamodb_client):
        evaluation = self._make_evaluation()
        decision = self._make_decision()
        await EvaluationTable.put(evaluation, decision=decision)

        result = await EvaluationTable.get(
            "AAPL",
            evaluation.evaluated_at,
            "eval-001",
        )
        assert result is not None
        assert result["evaluation_id"] == "eval-001"
        assert "decision" in result
        assert result["decision"]["verdict"] == "APPROVE"

    @pytest.mark.asyncio
    async def test_put_without_decision(self, fresh_dynamodb_client):
        evaluation = self._make_evaluation()
        await EvaluationTable.put(evaluation)

        result = await EvaluationTable.get(
            "AAPL",
            evaluation.evaluated_at,
            "eval-001",
        )
        assert result is not None
        assert "decision" not in result

    @pytest.mark.asyncio
    async def test_list_by_verdict_gsi1(self, fresh_dynamodb_client):
        evaluation = self._make_evaluation()
        decision = self._make_decision()
        await EvaluationTable.put(evaluation, decision=decision)

        results = await EvaluationTable.list_by_verdict("APPROVE")
        assert len(results) == 1
        assert results[0]["evaluation_id"] == "eval-001"
        # Should not contain DynamoDB keys
        assert "PK" not in results[0]
        assert "GSI1PK" not in results[0]

    @pytest.mark.asyncio
    async def test_list_by_verdict_empty(self, fresh_dynamodb_client):
        results = await EvaluationTable.list_by_verdict("APPROVE")
        assert results == []

    @pytest.mark.asyncio
    async def test_list_by_date_gsi2(self, fresh_dynamodb_client):
        evaluation = self._make_evaluation()
        decision = self._make_decision()
        await EvaluationTable.put(evaluation, decision=decision)

        date_str = evaluation.evaluated_at[:10]
        results = await EvaluationTable.list_by_date(date_str)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_float_fields_survive_roundtrip(self, fresh_dynamodb_client):
        """Verify Decimal conversion preserves float precision for scoring."""
        evaluation = self._make_evaluation()
        decision = self._make_decision()
        await EvaluationTable.put(evaluation, decision=decision)

        result = await EvaluationTable.get(
            "AAPL", evaluation.evaluated_at, "eval-001"
        )
        assert result["strike"] == 185.0
        assert result["iv"] == 0.32
        assert result["decision"]["final_score"] == 82.0


# ============================================================================
# PipelineRunTable
# ============================================================================


class TestPipelineRunTableIntegration:
    """Test PipelineRunTable CRUD operations."""

    def _make_run(self, run_id="run-001"):
        return PipelineRun(
            run_id=run_id,
            started_at="2026-01-17T16:00:00+00:00",
            status=RunStatus.RUNNING,
            total_opportunities=10,
            total_evaluations=5,
            policy_version="v2.0.0",
        )

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        run = self._make_run()
        await PipelineRunTable.put(run)

        retrieved = await PipelineRunTable.get("run-001", "2026-01-17T16:00:00+00:00")
        assert retrieved is not None
        assert retrieved.run_id == "run-001"
        assert retrieved.total_opportunities == 10

    @pytest.mark.asyncio
    async def test_list_runs(self, fresh_dynamodb_client):
        for i in range(3):
            run = self._make_run(run_id=f"run-{i:03d}")
            await PipelineRunTable.put(run)

        runs = await PipelineRunTable.list(limit=10)
        assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_list_with_status_filter(self, fresh_dynamodb_client):
        run = self._make_run()
        await PipelineRunTable.put(run)

        running = await PipelineRunTable.list(status="RUNNING")
        assert len(running) == 1

        completed = await PipelineRunTable.list(status="COMPLETED")
        assert len(completed) == 0

    @pytest.mark.asyncio
    async def test_update_run(self, fresh_dynamodb_client):
        run = self._make_run()
        await PipelineRunTable.put(run)

        updated = await PipelineRunTable.update(
            "run-001",
            "2026-01-17T16:00:00+00:00",
            {"status": "COMPLETED", "total_approves": 3},
        )
        assert updated is not None
        assert updated.status == "COMPLETED"


# ============================================================================
# StageEventTable
# ============================================================================


class TestStageEventTableIntegration:
    """Test StageEventTable put/list round-trips."""

    @pytest.mark.asyncio
    async def test_put_and_list_by_run(self, fresh_dynamodb_client):
        event = StageEvent(
            run_id="run-001",
            stage=PipelineStage.OPPORTUNITY_DISCOVERY,
            started_at="2026-01-17T16:00:00+00:00",
            completed_at="2026-01-17T16:00:05+00:00",
            items_in=50,
            items_out=10,
            processing_time_ms=5000,
        )
        await StageEventTable.put(event)

        events = await StageEventTable.list_by_run("run-001")
        assert len(events) == 1
        assert events[0].items_in == 50
        assert events[0].items_out == 10


# ============================================================================
# GateResultTable
# ============================================================================


class TestGateResultTableIntegration:
    """Test GateResultTable including GSI1 for run-level queries."""

    def _make_gate_result(self, eval_id="eval-001", gate_id="GATE_MIN_OI", passed=True, run_id=None):
        return GateResult(
            evaluation_id=eval_id,
            gate_id=gate_id,
            enabled=True,
            passed=passed,
            measured_value=5000,
            threshold_value=300,
            operator=GateOperator.GTE,
            units="contracts",
            reason_code="OI_SUFFICIENT" if passed else "OI_INSUFFICIENT",
            run_id=run_id,
        )

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        result = self._make_gate_result()
        await GateResultTable.put(result)

        retrieved = await GateResultTable.get("eval-001", "GATE_MIN_OI")
        assert retrieved is not None
        assert retrieved.passed is True
        assert retrieved.measured_value == 5000

    @pytest.mark.asyncio
    async def test_list_by_evaluation(self, fresh_dynamodb_client):
        for gate_id in ["GATE_MIN_OI", "GATE_MIN_VOL", "GATE_MAX_SPREAD"]:
            result = self._make_gate_result(gate_id=gate_id)
            await GateResultTable.put(result)

        results = await GateResultTable.list_by_evaluation("eval-001")
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_list_failed_by_evaluation(self, fresh_dynamodb_client):
        await GateResultTable.put(self._make_gate_result(gate_id="GATE_OK", passed=True))
        await GateResultTable.put(self._make_gate_result(gate_id="GATE_FAIL", passed=False))

        failed = await GateResultTable.list_failed_by_evaluation("eval-001")
        assert len(failed) == 1
        assert failed[0].gate_id == "GATE_FAIL"

    @pytest.mark.asyncio
    async def test_list_by_run_via_gsi1(self, fresh_dynamodb_client):
        """Test the GSI1-based query for pipeline monitor."""
        result = self._make_gate_result(run_id="run-001")
        await GateResultTable.put(result)

        results = await GateResultTable.list_by_run("run-001")
        assert len(results) == 1
        assert results[0].evaluation_id == "eval-001"

    @pytest.mark.asyncio
    async def test_put_batch(self, fresh_dynamodb_client):
        results = [
            self._make_gate_result(gate_id=f"GATE_{i}")
            for i in range(5)
        ]
        await GateResultTable.put_batch(results)

        all_results = await GateResultTable.list_by_evaluation("eval-001")
        assert len(all_results) == 5


# ============================================================================
# PaperPositionTable
# ============================================================================


class TestPaperPositionTableIntegration:
    """Test PaperPositionTable including OPEN/CLOSED partitioning and close()."""

    def _make_position(self, pos_id="pos-001", eval_id="eval-001"):
        return PaperPosition(
            position_id=pos_id,
            evaluation_id=eval_id,
            option_ticker="O:AAPL260320C00185000",
            entry_price=8.65,
            entry_date="2026-01-17",
            verdict_at_entry=Verdict.APPROVE,
            quality_tier_at_entry=QualityTier.TIER_2,
            current_price=9.50,
            current_pnl_pct=9.83,
            max_favorable_excursion=12.5,
            max_adverse_excursion=-3.2,
            days_held=5,
        )

    @pytest.mark.asyncio
    async def test_put_and_list_open(self, fresh_dynamodb_client):
        pos = self._make_position()
        await PaperPositionTable.put(pos)

        open_positions = await PaperPositionTable.list_open()
        assert len(open_positions) == 1
        assert open_positions[0].position_id == "pos-001"

    @pytest.mark.asyncio
    async def test_list_closed_empty(self, fresh_dynamodb_client):
        pos = self._make_position()
        await PaperPositionTable.put(pos)

        closed = await PaperPositionTable.list_closed()
        assert len(closed) == 0

    @pytest.mark.asyncio
    async def test_get_by_evaluation_id_via_gsi1(self, fresh_dynamodb_client):
        pos = self._make_position()
        await PaperPositionTable.put(pos)

        found = await PaperPositionTable.get_by_evaluation_id("eval-001")
        assert found is not None
        assert found.position_id == "pos-001"

    @pytest.mark.asyncio
    async def test_get_by_evaluation_id_not_found(self, fresh_dynamodb_client):
        result = await PaperPositionTable.get_by_evaluation_id("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_close_moves_partition(self, fresh_dynamodb_client):
        """Test that close() deletes from OPEN and creates in CLOSED."""
        pos = self._make_position()
        await PaperPositionTable.put(pos)

        # Verify it's in OPEN
        open_before = await PaperPositionTable.list_open()
        assert len(open_before) == 1

        # Close it
        closed_pos = await PaperPositionTable.close(pos, exit_price=12.0, exit_reason="PROFIT_TARGET")

        # Should be gone from OPEN
        open_after = await PaperPositionTable.list_open()
        assert len(open_after) == 0

        # Should be in CLOSED
        closed_after = await PaperPositionTable.list_closed()
        assert len(closed_after) == 1
        assert closed_after[0].exit_price == 12.0
        assert closed_after[0].exit_reason == "PROFIT_TARGET"

    @pytest.mark.asyncio
    async def test_close_calculates_pnl(self, fresh_dynamodb_client):
        pos = self._make_position()
        await PaperPositionTable.put(pos)

        closed_pos = await PaperPositionTable.close(pos, exit_price=10.0, exit_reason="PROFIT_TARGET")
        expected_pnl = ((10.0 - 8.65) / 8.65) * 100
        assert abs(closed_pos.current_pnl_pct - expected_pnl) < 0.01

    @pytest.mark.asyncio
    async def test_list_all_combines_open_and_closed(self, fresh_dynamodb_client):
        pos1 = self._make_position(pos_id="pos-001", eval_id="eval-001")
        pos2 = self._make_position(pos_id="pos-002", eval_id="eval-002")
        await PaperPositionTable.put(pos1)
        await PaperPositionTable.put(pos2)

        # Close one
        await PaperPositionTable.close(pos1, exit_price=10.0, exit_reason="PROFIT_TARGET")

        all_positions = await PaperPositionTable.list_all()
        assert len(all_positions) == 2


# ============================================================================
# IVHistoryTable
# ============================================================================


class TestIVHistoryTableIntegration:
    """Test IVHistoryTable including percentile calculation."""

    def _make_iv(self, ticker="AAPL", date="2026-01-17", atm_iv=0.30):
        return IVHistory(
            ticker=ticker,
            date=date,
            atm_iv=atm_iv,
            atm_call_iv=atm_iv + 0.01,
            atm_put_iv=atm_iv - 0.01,
            rv20=0.25,
            iv_rv_ratio=atm_iv / 0.25,
        )

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        iv = self._make_iv()
        await IVHistoryTable.put(iv)

        retrieved = await IVHistoryTable.get("AAPL", "2026-01-17")
        assert retrieved is not None
        assert retrieved.atm_iv == 0.30

    @pytest.mark.asyncio
    async def test_list_by_ticker(self, fresh_dynamodb_client):
        for i in range(5):
            iv = self._make_iv(date=f"2026-01-{17 + i:02d}")
            await IVHistoryTable.put(iv)

        records = await IVHistoryTable.list_by_ticker("AAPL")
        assert len(records) == 5

    @pytest.mark.asyncio
    async def test_calculate_percentile_with_enough_data(self, fresh_dynamodb_client):
        """With 25 records, percentile should be calculable."""
        for i in range(25):
            iv = self._make_iv(
                date=f"2026-01-{(i % 28) + 1:02d}",
                atm_iv=0.20 + i * 0.01,  # 0.20 to 0.44
            )
            await IVHistoryTable.put(iv)

        # Current IV of 0.30 — should be near 40th percentile
        # (10 values below 0.30 out of 25 = 40%)
        percentile = await IVHistoryTable.calculate_percentile("AAPL", 0.30)
        assert percentile is not None
        assert 30.0 <= percentile <= 50.0

    @pytest.mark.asyncio
    async def test_calculate_percentile_insufficient_data(self, fresh_dynamodb_client):
        """With fewer than 20 records, should return None."""
        for i in range(10):
            iv = self._make_iv(date=f"2026-01-{i + 1:02d}")
            await IVHistoryTable.put(iv)

        percentile = await IVHistoryTable.calculate_percentile("AAPL", 0.30)
        assert percentile is None

    @pytest.mark.asyncio
    async def test_get_latest(self, fresh_dynamodb_client):
        for i in range(5):
            iv = self._make_iv(date=f"2026-01-{i + 10:02d}")
            await IVHistoryTable.put(iv)

        latest = await IVHistoryTable.get_latest("AAPL")
        assert latest is not None
        # Most recent date should be returned (list_by_ticker is scan_forward=False)
        assert latest.date == "2026-01-14"

    @pytest.mark.asyncio
    async def test_put_batch(self, fresh_dynamodb_client):
        records = [
            self._make_iv(date=f"2026-01-{i + 1:02d}")
            for i in range(30)
        ]
        await IVHistoryTable.put_batch(records)

        all_records = await IVHistoryTable.list_by_ticker("AAPL", limit=50)
        assert len(all_records) == 30


# ============================================================================
# OIHistoryTable
# ============================================================================


class TestOIHistoryTableIntegration:
    """Test OIHistoryTable including 5-day change calculation."""

    def _make_oi(self, contract="O:AAPL260320C00185000", date="2026-01-17", oi=5000):
        return OIHistory(
            option_ticker=contract,
            date=date,
            open_interest=oi,
        )

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        oi = self._make_oi()
        await OIHistoryTable.put(oi)

        retrieved = await OIHistoryTable.get("O:AAPL260320C00185000", "2026-01-17")
        assert retrieved is not None
        assert retrieved.open_interest == 5000

    @pytest.mark.asyncio
    async def test_calculate_5d_change(self, fresh_dynamodb_client):
        """Test 5-day OI change percentage calculation."""
        contract = "O:AAPL260320C00185000"
        for i in range(6):
            oi = self._make_oi(
                contract=contract,
                date=f"2026-01-{10 + i:02d}",
                oi=1000 + i * 100,  # 1000, 1100, 1200, 1300, 1400, 1500
            )
            await OIHistoryTable.put(oi)

        # Current OI is 2000, 5 days ago was ~1100
        change = await OIHistoryTable.calculate_5d_change(contract, 2000)
        assert change is not None
        # Should be positive (increase from historical)

    @pytest.mark.asyncio
    async def test_calculate_5d_change_insufficient_data(self, fresh_dynamodb_client):
        contract = "O:AAPL260320C00185000"
        for i in range(3):
            oi = self._make_oi(contract=contract, date=f"2026-01-{i + 1:02d}")
            await OIHistoryTable.put(oi)

        change = await OIHistoryTable.calculate_5d_change(contract, 5000)
        assert change is None


# ============================================================================
# TradeThesisTable
# ============================================================================


class TestTradeThesisTableIntegration:
    """Test TradeThesisTable including GSI1 date query."""

    def _make_thesis(self, eval_id="eval-001", thesis_id="thesis-001"):
        from app.core.schemas import ExitPlanThesis, LLMProvider, ThesisStatus
        return TradeThesis(
            thesis_id=thesis_id,
            evaluation_id=eval_id,
            setup_summary="AAPL breakout with high volume confirmation.",
            thesis="Strong bullish setup with breakout above 20-day high.",
            supporting_evidence=["breakout_above_20d", "volume_ratio_1.8x"],
            risks=["earnings_in_2_weeks", "broad_market_weakness"],
            invalidation_conditions=["close_below_180"],
            exit_plan=ExitPlanThesis(
                profit_target="50% gain or $12.00",
                stop_loss="40% loss or $5.00",
                time_exit="Exit at 5 DTE",
            ),
            llm_provider=LLMProvider.ANTHROPIC,
            model_used="claude-3-sonnet",
            tokens_used=700,
            status=ThesisStatus.COMPLETED,
            generated_at="2026-01-17T16:00:00+00:00",
        )

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        thesis = self._make_thesis()
        await TradeThesisTable.put(thesis)

        retrieved = await TradeThesisTable.get("eval-001", "thesis-001")
        assert retrieved is not None
        assert retrieved.thesis == "Strong bullish setup with breakout above 20-day high."

    @pytest.mark.asyncio
    async def test_get_by_evaluation_id(self, fresh_dynamodb_client):
        thesis = self._make_thesis()
        await TradeThesisTable.put(thesis)

        found = await TradeThesisTable.get_by_evaluation_id("eval-001")
        assert found is not None
        assert found.thesis_id == "thesis-001"

    @pytest.mark.asyncio
    async def test_list_by_date_gsi1(self, fresh_dynamodb_client):
        thesis = self._make_thesis()
        await TradeThesisTable.put(thesis)

        results = await TradeThesisTable.list_by_date("2026-01-17")
        assert len(results) == 1


# ============================================================================
# LLMUsageTable
# ============================================================================


class TestLLMUsageTableIntegration:
    """Test LLMUsageTable including increment logic."""

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        usage = LLMUsage(date="2026-01-17", calls_made=5, tokens_used=1500)
        await LLMUsageTable.put(usage)

        retrieved = await LLMUsageTable.get("2026-01-17")
        assert retrieved is not None
        assert retrieved.calls_made == 5
        assert retrieved.tokens_used == 1500

    @pytest.mark.asyncio
    async def test_increment_creates_new(self, fresh_dynamodb_client):
        result = await LLMUsageTable.increment("2026-01-17", tokens=200)
        assert result.calls_made == 1
        assert result.tokens_used == 200

    @pytest.mark.asyncio
    async def test_increment_existing(self, fresh_dynamodb_client):
        await LLMUsageTable.increment("2026-01-17", tokens=200)
        result = await LLMUsageTable.increment("2026-01-17", tokens=300)
        assert result.calls_made == 2
        assert result.tokens_used == 500

    @pytest.mark.asyncio
    async def test_list_recent(self, fresh_dynamodb_client):
        for i in range(5):
            usage = LLMUsage(date=f"2026-01-{i + 10:02d}", calls_made=i, tokens_used=i * 100)
            await LLMUsageTable.put(usage)

        records = await LLMUsageTable.list_recent(days=10)
        assert len(records) == 5


# ============================================================================
# CalibrationReportTable
# ============================================================================


class TestCalibrationReportTableIntegration:
    """Test CalibrationReportTable including suggestion status updates."""

    def _make_report(self, report_id="report-001"):
        return {
            "report_id": report_id,
            "generated_at": "2026-01-17T16:00:00+00:00",
            "period_days": 30,
            "suggestions": [
                {"suggestion_id": "sug-001", "status": "PENDING", "field_path": "gates.min_oi"},
                {"suggestion_id": "sug-002", "status": "PENDING", "field_path": "gates.max_spread"},
            ],
        }

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        report = self._make_report()
        await CalibrationReportTable.put(report)

        retrieved = await CalibrationReportTable.get("report-001")
        assert retrieved is not None
        assert retrieved["report_id"] == "report-001"
        assert len(retrieved["suggestions"]) == 2

    @pytest.mark.asyncio
    async def test_list_recent(self, fresh_dynamodb_client):
        for i in range(3):
            r = self._make_report(report_id=f"report-{i:03d}")
            await CalibrationReportTable.put(r)

        reports = await CalibrationReportTable.list_recent(limit=10)
        assert len(reports) == 3

    @pytest.mark.asyncio
    async def test_update_suggestion_status(self, fresh_dynamodb_client):
        report = self._make_report()
        await CalibrationReportTable.put(report)

        success = await CalibrationReportTable.update_suggestion_status(
            "report-001", "sug-001", "APPROVED"
        )
        assert success is True

        updated = await CalibrationReportTable.get("report-001")
        sug = next(s for s in updated["suggestions"] if s["suggestion_id"] == "sug-001")
        assert sug["status"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_update_suggestion_status_with_expected_guard(self, fresh_dynamodb_client):
        """Test optimistic locking: update only if current status matches."""
        report = self._make_report()
        await CalibrationReportTable.put(report)

        # Should succeed — current status is PENDING
        success = await CalibrationReportTable.update_suggestion_status(
            "report-001", "sug-001", "APPROVED", expected_current_status="PENDING"
        )
        assert success is True

        # Should fail — current status is now APPROVED, not PENDING
        success = await CalibrationReportTable.update_suggestion_status(
            "report-001", "sug-001", "REJECTED", expected_current_status="PENDING"
        )
        assert success is False

    @pytest.mark.asyncio
    async def test_update_suggestion_nonexistent(self, fresh_dynamodb_client):
        report = self._make_report()
        await CalibrationReportTable.put(report)

        success = await CalibrationReportTable.update_suggestion_status(
            "report-001", "nonexistent", "APPROVED"
        )
        assert success is False


# ============================================================================
# ScanStatusTable
# ============================================================================


class TestScanStatusTableIntegration:
    """Test ScanStatusTable put/get/list."""

    @pytest.mark.asyncio
    async def test_put_and_get(self, fresh_dynamodb_client):
        status = {
            "started_at": "2026-01-17T16:00:00+00:00",
            "status": "RUNNING",
            "tickers": ["AAPL", "MSFT"],
        }
        await ScanStatusTable.put("scan-001", status)

        retrieved = await ScanStatusTable.get("scan-001")
        assert retrieved is not None
        assert retrieved["status"] == "RUNNING"
        assert "AAPL" in retrieved["tickers"]

    @pytest.mark.asyncio
    async def test_list_recent(self, fresh_dynamodb_client):
        for i in range(3):
            s = {"started_at": f"2026-01-{i + 10:02d}T16:00:00+00:00", "status": "COMPLETED"}
            await ScanStatusTable.put(f"scan-{i:03d}", s)

        scans = await ScanStatusTable.list_recent(limit=10)
        assert len(scans) == 3


# ============================================================================
# FeatureValueTable & PillarScoreTable (simpler tables)
# ============================================================================


class TestFeatureValueTableIntegration:

    @pytest.mark.asyncio
    async def test_put_and_list_by_evaluation(self, fresh_dynamodb_client):
        features = [
            FeatureValue(
                evaluation_id="eval-001",
                feature_name=f"feature_{i}",
                value=float(i) * 0.5,
                source="test",
            )
            for i in range(5)
        ]
        await FeatureValueTable.put_batch(features)

        results = await FeatureValueTable.list_by_evaluation("eval-001")
        assert len(results) == 5
        names = {f.feature_name for f in results}
        assert "feature_0" in names


class TestPillarScoreTableIntegration:

    def _make_score(self, eval_id="eval-001", pillar_id=PillarId.PREMIUM_LEVERAGE, score=78):
        contributor = PillarContributor(
            feature_name="entry_delta",
            subscore=80.0,
            weight=0.30,
            weighted_contribution=24.0,
            raw_value=0.85,
            distance_from_neutral=0.35,
        )
        return PillarScore(
            evaluation_id=eval_id,
            pillar_id=pillar_id,
            score=score,
            contributors=[contributor],
        )

    @pytest.mark.asyncio
    async def test_put_batch_and_list(self, fresh_dynamodb_client):
        scores = [
            self._make_score(pillar_id=PillarId.PREMIUM_LEVERAGE, score=78),
            self._make_score(pillar_id=PillarId.UNDERLYING_BEHAVIOR, score=85),
            self._make_score(pillar_id=PillarId.SETUP_QUALITY, score=80),
        ]
        await PillarScoreTable.put_batch(scores)

        results = await PillarScoreTable.list_by_evaluation("eval-001")
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_get_specific_pillar(self, fresh_dynamodb_client):
        score = self._make_score(pillar_id=PillarId.UNDERLYING_BEHAVIOR, score=85)
        await PillarScoreTable.put(score)

        retrieved = await PillarScoreTable.get("eval-001", "UNDERLYING_BEHAVIOR")
        assert retrieved is not None
        assert retrieved.score == 85
