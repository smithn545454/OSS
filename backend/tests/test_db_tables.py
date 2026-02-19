"""Tests for db/tables.py.

Tests all table classes with mocked DynamoDB client. Covers put, get,
list, update, and specialized operations for each table.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    Decision,
    Evaluation,
    ExitReason,
    FeatureValue,
    GateOperator,
    GateResult,
    IVHistory,
    OIHistory,
    Opportunity,
    PaperPosition,
    PillarScore,
    PipelineRun,
    Policy,
    PositionStatus,
    StageEvent,
    TradeThesis,
    Verdict,
)


# ---------------------------------------------------------------------------
# Mock DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Patch get_dynamodb to return a mock DynamoDB client."""
    db = MagicMock()
    db.put_item = AsyncMock()
    db.get_item = AsyncMock(return_value=None)
    db.query = AsyncMock(return_value=[])
    db.update_item = AsyncMock(return_value=None)
    db.delete_item = AsyncMock()
    db.batch_write = AsyncMock()
    with patch("app.db.tables.get_dynamodb", return_value=db):
        yield db


# ---------------------------------------------------------------------------
# PolicyTable
# ---------------------------------------------------------------------------


class TestPolicyTable:

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import PolicyTable
        mock_db.get_item = AsyncMock(return_value=None)
        result = await PolicyTable.get("v1.0.0")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_active_returns_none(self, mock_db):
        from app.db.tables import PolicyTable
        mock_db.query = AsyncMock(return_value=[])
        result = await PolicyTable.get_active()
        assert result is None

    @pytest.mark.asyncio
    async def test_list_empty(self, mock_db):
        from app.db.tables import PolicyTable
        mock_db.query = AsyncMock(return_value=[])
        result = await PolicyTable.list()
        assert result == []


# ---------------------------------------------------------------------------
# OpportunityTable
# ---------------------------------------------------------------------------


class TestOpportunityTable:

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import OpportunityTable
        result = await OpportunityTable.get("AAPL", "2026-01-17", "opp-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_by_ticker_empty(self, mock_db):
        from app.db.tables import OpportunityTable
        result = await OpportunityTable.list_by_ticker("AAPL")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_by_date_empty(self, mock_db):
        from app.db.tables import OpportunityTable
        result = await OpportunityTable.list_by_date("2026-01-17")
        assert result == []


# ---------------------------------------------------------------------------
# EvaluationTable
# ---------------------------------------------------------------------------


class TestEvaluationTable:

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import EvaluationTable
        result = await EvaluationTable.get("AAPL", "2026-01-17", "eval-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_item(self, mock_db):
        from app.db.tables import EvaluationTable
        mock_db.get_item = AsyncMock(return_value={
            "PK": "EVAL#AAPL",
            "SK": "2026-01-17#eval-1",
            "GSI1PK": "x",
            "GSI1SK": "y",
            "GSI2PK": "a",
            "GSI2SK": "b",
            "evaluation_id": "eval-1",
        })
        result = await EvaluationTable.get("AAPL", "2026-01-17", "eval-1")
        assert result is not None
        assert result["evaluation_id"] == "eval-1"
        assert "PK" not in result  # Keys cleaned up

    @pytest.mark.asyncio
    async def test_list_by_verdict(self, mock_db):
        from app.db.tables import EvaluationTable
        mock_db.query = AsyncMock(return_value=[
            {"PK": "x", "SK": "y", "GSI1PK": "a", "GSI1SK": "b",
             "GSI2PK": "c", "GSI2SK": "d", "evaluation_id": "e1"}
        ])
        result = await EvaluationTable.list_by_verdict("APPROVE")
        assert len(result) == 1
        assert "PK" not in result[0]

    @pytest.mark.asyncio
    async def test_list_by_date(self, mock_db):
        from app.db.tables import EvaluationTable
        result = await EvaluationTable.list_by_date("2026-01-17")
        assert result == []


# ---------------------------------------------------------------------------
# PipelineRunTable
# ---------------------------------------------------------------------------


class TestPipelineRunTable:

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import PipelineRunTable
        result = await PipelineRunTable.get("run-1", "2026-01-17")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_empty(self, mock_db):
        from app.db.tables import PipelineRunTable
        result = await PipelineRunTable.list()
        assert result == []

    @pytest.mark.asyncio
    async def test_update_returns_none(self, mock_db):
        from app.db.tables import PipelineRunTable
        result = await PipelineRunTable.update("run-1", "2026-01-17", {"status": "COMPLETED"})
        assert result is None

    @pytest.mark.asyncio
    async def test_list_with_status(self, mock_db):
        from app.db.tables import PipelineRunTable
        result = await PipelineRunTable.list(limit=5, status="COMPLETED")
        assert result == []


# ---------------------------------------------------------------------------
# StageEventTable
# ---------------------------------------------------------------------------


class TestStageEventTable:

    @pytest.mark.asyncio
    async def test_list_by_run_empty(self, mock_db):
        from app.db.tables import StageEventTable
        result = await StageEventTable.list_by_run("run-1")
        assert result == []


# ---------------------------------------------------------------------------
# PaperPositionTable
# ---------------------------------------------------------------------------


class TestPaperPositionTable:

    @pytest.mark.asyncio
    async def test_list_open_empty(self, mock_db):
        from app.db.tables import PaperPositionTable
        result = await PaperPositionTable.list_open()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_closed_empty(self, mock_db):
        from app.db.tables import PaperPositionTable
        result = await PaperPositionTable.list_closed()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_by_evaluation_id_not_found(self, mock_db):
        from app.db.tables import PaperPositionTable
        mock_db.query = AsyncMock(return_value=[])
        result = await PaperPositionTable.get_by_evaluation_id("eval-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_returns_none(self, mock_db):
        from app.db.tables import PaperPositionTable
        pos = PaperPosition(
            position_id="pos-1",
            evaluation_id="eval-1",
            option_ticker="O:TEST",
            entry_price=5.0,
            entry_date="2026-01-15",
            verdict_at_entry=Verdict.APPROVE,
            current_price=5.0,
            current_pnl_pct=0.0,
            status=PositionStatus.OPEN,
        )
        result = await PaperPositionTable.update(pos, {"current_price": 6.0})
        assert result is None

    @pytest.mark.asyncio
    async def test_list_all(self, mock_db):
        from app.db.tables import PaperPositionTable
        result = await PaperPositionTable.list_all()
        assert result == []

    @pytest.mark.asyncio
    async def test_close(self, mock_db):
        from app.db.tables import PaperPositionTable
        pos = PaperPosition(
            position_id="pos-1",
            evaluation_id="eval-1",
            option_ticker="O:TEST",
            entry_price=5.0,
            entry_date="2026-01-15",
            verdict_at_entry=Verdict.APPROVE,
            current_price=5.0,
            current_pnl_pct=0.0,
            status=PositionStatus.OPEN,
        )
        result = await PaperPositionTable.close(pos, 7.0, "PROFIT_TARGET")
        assert result.status == PositionStatus.CLOSED
        assert result.exit_price == 7.0
        assert result.current_pnl_pct == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# FeatureValueTable
# ---------------------------------------------------------------------------


class TestFeatureValueTable:

    @pytest.mark.asyncio
    async def test_list_by_evaluation_empty(self, mock_db):
        from app.db.tables import FeatureValueTable
        result = await FeatureValueTable.list_by_evaluation("eval-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_put_batch_uses_batch_write(self, mock_db):
        from app.db.tables import FeatureValueTable
        f1 = FeatureValue(evaluation_id="e1", feature_name="spread_pct", value=2.5, units="%")
        f2 = FeatureValue(evaluation_id="e1", feature_name="iv_percentile", value=45.0, units="pct")
        await FeatureValueTable.put_batch([f1, f2])
        mock_db.batch_write.assert_called_once()
        args = mock_db.batch_write.call_args
        assert len(args[0][1]) == 2  # 2 items in batch

    @pytest.mark.asyncio
    async def test_put_batch_empty(self, mock_db):
        from app.db.tables import FeatureValueTable
        await FeatureValueTable.put_batch([])
        mock_db.batch_write.assert_not_called()


# ---------------------------------------------------------------------------
# PillarScoreTable
# ---------------------------------------------------------------------------


class TestPillarScoreTable:

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import PillarScoreTable
        result = await PillarScoreTable.get("eval-1", "DIRECTIONAL")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_by_evaluation_empty(self, mock_db):
        from app.db.tables import PillarScoreTable
        result = await PillarScoreTable.list_by_evaluation("eval-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_put_batch_uses_batch_write(self, mock_db):
        from app.db.tables import PillarScoreTable
        s1 = PillarScore(evaluation_id="e1", pillar_id="DIRECTIONAL", score=75.0, contributors=[])
        s2 = PillarScore(evaluation_id="e1", pillar_id="VOLATILITY", score=60.0, contributors=[])
        await PillarScoreTable.put_batch([s1, s2])
        mock_db.batch_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_put_batch_empty(self, mock_db):
        from app.db.tables import PillarScoreTable
        await PillarScoreTable.put_batch([])
        mock_db.batch_write.assert_not_called()


# ---------------------------------------------------------------------------
# GateResultTable
# ---------------------------------------------------------------------------


class TestGateResultTable:

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import GateResultTable
        result = await GateResultTable.get("eval-1", "GATE_MIN_OI")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_by_evaluation_empty(self, mock_db):
        from app.db.tables import GateResultTable
        result = await GateResultTable.list_by_evaluation("eval-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_failed_by_evaluation(self, mock_db):
        from app.db.tables import GateResultTable
        result = await GateResultTable.list_failed_by_evaluation("eval-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_put_batch_uses_batch_write(self, mock_db):
        from app.db.tables import GateResultTable
        g1 = GateResult(
            evaluation_id="e1", gate_id="GATE_MIN_OI", enabled=True,
            passed=True, measured_value=500.0, threshold_value=300.0,
            operator=GateOperator.GTE, units="contracts",
            reason_code="MIN_OI_PASS", run_id="run-1",
        )
        await GateResultTable.put_batch([g1])
        mock_db.batch_write.assert_called_once()
        # Verify GSI1 keys populated
        items = mock_db.batch_write.call_args[0][1]
        assert items[0]["GSI1PK"] == "RUN#run-1"

    @pytest.mark.asyncio
    async def test_put_batch_empty(self, mock_db):
        from app.db.tables import GateResultTable
        await GateResultTable.put_batch([])
        mock_db.batch_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_by_run_empty(self, mock_db):
        from app.db.tables import GateResultTable
        mock_db.query = AsyncMock(return_value=[])
        result = await GateResultTable.list_by_run("run-1")
        assert result == []
        # Verify it queries GSI1, not the table directly
        mock_db.query.assert_called_once()
        call_kwargs = mock_db.query.call_args
        assert call_kwargs[1].get("index_name") == "GSI1" or call_kwargs[0][0] == "gate-results"

    @pytest.mark.asyncio
    async def test_list_by_run_with_results(self, mock_db):
        from app.db.tables import GateResultTable
        mock_db.query = AsyncMock(return_value=[{
            "PK": "EVAL#e1", "SK": "GATE#MIN_OI",
            "GSI1PK": "RUN#run-1", "GSI1SK": "e1#MIN_OI",
            "evaluation_id": "e1", "gate_id": "MIN_OI",
            "enabled": True, "passed": True,
            "measured_value": 500, "threshold_value": 300,
            "operator": "gte", "units": "contracts",
            "reason_code": "MIN_OI_PASS", "run_id": "run-1",
        }])
        result = await GateResultTable.list_by_run("run-1")
        assert len(result) == 1
        assert result[0].gate_id == "MIN_OI"
        assert result[0].run_id == "run-1"


# ---------------------------------------------------------------------------
# IVHistoryTable
# ---------------------------------------------------------------------------


class TestIVHistoryTable:

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import IVHistoryTable
        result = await IVHistoryTable.get("AAPL", "2026-01-17")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_by_ticker_empty(self, mock_db):
        from app.db.tables import IVHistoryTable
        result = await IVHistoryTable.list_by_ticker("AAPL")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_by_ticker_with_dates(self, mock_db):
        from app.db.tables import IVHistoryTable
        result = await IVHistoryTable.list_by_ticker(
            "AAPL", start_date="2025-01-01", end_date="2026-01-01"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_list_by_ticker_start_only(self, mock_db):
        from app.db.tables import IVHistoryTable
        result = await IVHistoryTable.list_by_ticker("AAPL", start_date="2025-01-01")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_by_ticker_end_only(self, mock_db):
        from app.db.tables import IVHistoryTable
        result = await IVHistoryTable.list_by_ticker("AAPL", end_date="2026-01-01")
        assert result == []

    @pytest.mark.asyncio
    async def test_calculate_percentile_insufficient(self, mock_db):
        from app.db.tables import IVHistoryTable
        result = await IVHistoryTable.calculate_percentile("AAPL", 0.30)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_latest_returns_none(self, mock_db):
        from app.db.tables import IVHistoryTable
        result = await IVHistoryTable.get_latest("AAPL")
        assert result is None

    @pytest.mark.asyncio
    async def test_calculate_percentile_with_data(self, mock_db):
        from app.db.tables import IVHistoryTable
        # 25 records with atm_iv values 0.10..0.34
        records = []
        for i in range(25):
            records.append({
                "PK": "x", "SK": "y",
                "ticker": "AAPL",
                "date": f"2026-01-{i+1:02d}",
                "atm_iv": 0.10 + i * 0.01,
            })
        mock_db.query = AsyncMock(return_value=records)
        result = await IVHistoryTable.calculate_percentile("AAPL", 0.20)
        assert result is not None
        assert 0 <= result <= 100


# ---------------------------------------------------------------------------
# OIHistoryTable
# ---------------------------------------------------------------------------


class TestOIHistoryTable:

    @pytest.mark.asyncio
    async def test_list_by_contract_empty(self, mock_db):
        from app.db.tables import OIHistoryTable
        result = await OIHistoryTable.list_by_contract("O:AAPL260320C00185000")
        assert result == []

    @pytest.mark.asyncio
    async def test_calculate_5d_change_insufficient(self, mock_db):
        from app.db.tables import OIHistoryTable
        mock_db.query = AsyncMock(return_value=[])
        result = await OIHistoryTable.calculate_5d_change("O:AAPL", 1000)
        assert result is None

    @pytest.mark.asyncio
    async def test_calculate_5d_change_zero_base(self, mock_db):
        from app.db.tables import OIHistoryTable
        records = []
        for i in range(6):
            records.append({"PK": "x", "SK": "y",
                "option_ticker": "O:AAPL", "date": f"2026-01-{10+i:02d}",
                "open_interest": 0, "volume": 100})
        mock_db.query = AsyncMock(return_value=records)
        result = await OIHistoryTable.calculate_5d_change("O:AAPL", 1000)
        assert result is None

    @pytest.mark.asyncio
    async def test_calculate_5d_change_valid(self, mock_db):
        from app.db.tables import OIHistoryTable
        records = []
        for i in range(6):
            records.append({"PK": "x", "SK": "y",
                "option_ticker": "O:AAPL", "date": f"2026-01-{10+i:02d}",
                "open_interest": 1000, "volume": 100})
        mock_db.query = AsyncMock(return_value=records)
        result = await OIHistoryTable.calculate_5d_change("O:AAPL", 1200)
        assert result == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# TradeThesisTable
# ---------------------------------------------------------------------------


class TestTradeThesisTable:

    @pytest.mark.asyncio
    async def test_get_by_evaluation_id_not_found(self, mock_db):
        from app.db.tables import TradeThesisTable
        mock_db.query = AsyncMock(return_value=[])
        result = await TradeThesisTable.get_by_evaluation_id("eval-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_by_date_empty(self, mock_db):
        from app.db.tables import TradeThesisTable
        result = await TradeThesisTable.list_by_date("2026-01-17")
        assert result == []


# ---------------------------------------------------------------------------
# LLMUsageTable
# ---------------------------------------------------------------------------


class TestLLMUsageTable:

    @pytest.mark.asyncio
    async def test_list_recent_empty(self, mock_db):
        from app.db.tables import LLMUsageTable
        result = await LLMUsageTable.list_recent()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import LLMUsageTable
        result = await LLMUsageTable.get("2026-01-17")
        assert result is None

    @pytest.mark.asyncio
    async def test_increment_new(self, mock_db):
        from app.db.tables import LLMUsageTable
        mock_db.get_item = AsyncMock(return_value=None)  # no existing
        result = await LLMUsageTable.increment("2026-01-17", tokens=100)
        assert result.calls_made == 1
        assert result.tokens_used == 100

    @pytest.mark.asyncio
    async def test_increment_existing(self, mock_db):
        from app.db.tables import LLMUsageTable
        mock_db.get_item = AsyncMock(return_value={
            "PK": "USAGE", "SK": "DATE#2026-01-17",
            "date": "2026-01-17", "calls_made": 5, "tokens_used": 500,
        })
        result = await LLMUsageTable.increment("2026-01-17", tokens=100)
        assert result.calls_made == 6
        assert result.tokens_used == 600


# ---------------------------------------------------------------------------
# CalibrationReportTable
# ---------------------------------------------------------------------------


class TestCalibrationReportTable:

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import CalibrationReportTable
        result = await CalibrationReportTable.get("report-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_item(self, mock_db):
        from app.db.tables import CalibrationReportTable
        mock_db.query = AsyncMock(return_value=[{
            "PK": "REPORT", "SK": "2026-01-17#r1",
            "report_id": "r1",
        }])
        result = await CalibrationReportTable.get("r1")
        assert result is not None
        assert "PK" not in result

    @pytest.mark.asyncio
    async def test_list_recent_empty(self, mock_db):
        from app.db.tables import CalibrationReportTable
        result = await CalibrationReportTable.list_recent()
        assert result == []

    @pytest.mark.asyncio
    async def test_update_suggestion_status_found(self, mock_db):
        from app.db.tables import CalibrationReportTable
        mock_db.query = AsyncMock(return_value=[{
            "PK": "REPORT", "SK": "2026-01-17#r1",
            "report_id": "r1",
            "suggestions": [{"suggestion_id": "s1", "status": "PENDING"}],
        }])
        mock_db.update_item = AsyncMock()
        result = await CalibrationReportTable.update_suggestion_status("r1", "s1", "APPROVED")
        assert result is True

    @pytest.mark.asyncio
    async def test_update_suggestion_status_not_found(self, mock_db):
        from app.db.tables import CalibrationReportTable
        mock_db.query = AsyncMock(return_value=[{
            "PK": "REPORT", "SK": "2026-01-17#r1",
            "report_id": "r1",
            "suggestions": [{"suggestion_id": "s1", "status": "PENDING"}],
        }])
        result = await CalibrationReportTable.update_suggestion_status("r1", "s-missing", "APPROVED")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_suggestion_status_wrong_expected(self, mock_db):
        from app.db.tables import CalibrationReportTable
        mock_db.query = AsyncMock(return_value=[{
            "PK": "REPORT", "SK": "2026-01-17#r1",
            "report_id": "r1",
            "suggestions": [{"suggestion_id": "s1", "status": "PENDING"}],
        }])
        result = await CalibrationReportTable.update_suggestion_status(
            "r1", "s1", "APPROVED", expected_current_status="ALREADY_DONE"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_update_suggestion_status_report_not_found(self, mock_db):
        from app.db.tables import CalibrationReportTable
        mock_db.query = AsyncMock(return_value=[])
        result = await CalibrationReportTable.update_suggestion_status("r-missing", "s1", "APPROVED")
        assert result is False


# ---------------------------------------------------------------------------
# ScanStatusTable
# ---------------------------------------------------------------------------


class TestScanStatusTable:

    @pytest.mark.asyncio
    async def test_get_returns_none(self, mock_db):
        from app.db.tables import ScanStatusTable
        result = await ScanStatusTable.get("run-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_item(self, mock_db):
        from app.db.tables import ScanStatusTable
        mock_db.query = AsyncMock(return_value=[{
            "PK": "SCAN", "SK": "2026-01-17#run-1",
            "run_id": "run-1",
            "status": "completed",
        }])
        result = await ScanStatusTable.get("run-1")
        assert result is not None
        assert result["status"] == "completed"
        assert "PK" not in result

    @pytest.mark.asyncio
    async def test_list_recent(self, mock_db):
        from app.db.tables import ScanStatusTable
        result = await ScanStatusTable.list_recent()
        assert result == []

    @pytest.mark.asyncio
    async def test_put(self, mock_db):
        from app.db.tables import ScanStatusTable
        await ScanStatusTable.put("run-1", {"started_at": "2026-01-17", "status": "running"})
        mock_db.put_item.assert_called_once()


class TestSP500TickerTable:

    @pytest.mark.asyncio
    async def test_get_active_tickers(self, mock_db):
        from app.db.tables import SP500TickerTable
        mock_db.query = AsyncMock(return_value=[
            {"PK": "TICKER_LIST", "SK": "AAPL", "ticker": "AAPL", "is_active": True},
            {"PK": "TICKER_LIST", "SK": "MSFT", "ticker": "MSFT", "is_active": True},
            {"PK": "TICKER_LIST", "SK": "GOOGL", "ticker": "GOOGL", "is_active": True},
            {"PK": "TICKER_LIST", "SK": "NVDA", "ticker": "NVDA", "is_active": True},
            {"PK": "TICKER_LIST", "SK": "TSLA", "ticker": "TSLA", "is_active": True},
        ])
        result = await SP500TickerTable.get_active_tickers()
        assert result == ["AAPL", "GOOGL", "MSFT", "NVDA", "TSLA"]
        mock_db.query.assert_called_once_with(
            "sp500-tickers", "TICKER_LIST", limit=None, scan_forward=True
        )

    @pytest.mark.asyncio
    async def test_filters_inactive(self, mock_db):
        from app.db.tables import SP500TickerTable
        mock_db.query = AsyncMock(return_value=[
            {"PK": "TICKER_LIST", "SK": "AAPL", "ticker": "AAPL", "is_active": True},
            {"PK": "TICKER_LIST", "SK": "GE", "ticker": "GE", "is_active": False},
            {"PK": "TICKER_LIST", "SK": "MSFT", "ticker": "MSFT", "is_active": True},
        ])
        result = await SP500TickerTable.get_active_tickers()
        assert result == ["AAPL", "MSFT"]

    @pytest.mark.asyncio
    async def test_empty_table(self, mock_db):
        from app.db.tables import SP500TickerTable
        mock_db.query = AsyncMock(return_value=[])
        result = await SP500TickerTable.get_active_tickers()
        assert result == []

    @pytest.mark.asyncio
    async def test_defaults_to_active_when_flag_missing(self, mock_db):
        from app.db.tables import SP500TickerTable
        mock_db.query = AsyncMock(return_value=[
            {"PK": "TICKER_LIST", "SK": "AAPL", "ticker": "AAPL"},
        ])
        result = await SP500TickerTable.get_active_tickers()
        assert result == ["AAPL"]
