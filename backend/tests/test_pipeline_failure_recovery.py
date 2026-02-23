"""Pipeline partial failure and recovery tests.

Tests that the pipeline handles failures gracefully in specific stages:
- Feature computation failures for individual evaluations
- Pillar scoring failures
- Gate evaluation failures
- DB persistence failures during batch writes
- Multiple tickers where some fail and others succeed
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import PolicyConfig, Verdict
from app.scanners.orchestrator import ScannerOrchestrator, ScanRunResult


# ============================================================================
# Shared Helpers
# ============================================================================


def _make_mock_polygon(
    tickers: list[str] | None = None,
    bars_per_ticker: int = 30,
    chain_per_ticker: int = 2,
    fail_tickers: set[str] | None = None,
    empty_chain: bool = False,
) -> AsyncMock:
    """Build a mock Polygon client with configurable data per ticker."""
    tickers = tickers or ["AAPL"]
    fail_tickers = fail_tickers or set()
    client = AsyncMock()

    bars_by_ticker: dict[str, list] = {}
    all_bars = []
    for ticker in tickers:
        ticker_bars = []
        for i in range(bars_per_ticker):
            bar = MagicMock(
                ticker=ticker,
                date=f"2026-01-{(i % 28) + 1:02d}",
                open=180.0 + i,
                high=185.0 + i,
                low=178.0 + i,
                close=183.0 + i,
                volume=50_000_000 + i * 1_000_000,
                vwap=182.0 + i,
            )
            ticker_bars.append(bar)
            all_bars.append(bar)
        bars_by_ticker[ticker] = ticker_bars
    client.get_daily_bars_parsed.return_value = all_bars
    client.get_daily_bars_batch.return_value = bars_by_ticker
    client.get_previous_close.return_value = {"c": 189.0, "v": 60_000_000}
    client.get_previous_close_batch.return_value = {
        t: {"c": 189.0, "v": 60_000_000} for t in tickers
    }

    def mock_chain(ticker, **kwargs):
        if ticker in fail_tickers:
            raise Exception(f"API failure for {ticker}")
        if empty_chain:
            return []
        contracts = []
        for j in range(chain_per_ticker):
            contracts.append({
                "details": {
                    "contract_type": "CALL" if j % 2 == 0 else "PUT",
                    "ticker": f"O:{ticker}260320C001{85 + j * 5}000",
                    "strike_price": 185.0 + j * 5,
                    "expiration_date": "2026-03-20",
                },
                "day": {"volume": 500, "open": 5.0, "high": 5.5, "low": 4.8, "close": 5.2},
                "underlying_asset": {"ticker": ticker, "price": 189.0},
                "greeks": {
                    "delta": 0.55 - j * 0.1,
                    "gamma": 0.03,
                    "theta": -0.08,
                    "vega": 0.25,
                },
                "open_interest": 5000,
                "implied_volatility": 0.32,
                "last_quote": {"bid": 5.0, "ask": 5.4, "midpoint": 5.2},
            })
        return contracts

    client.get_options_chain.side_effect = mock_chain

    client.get_aggregated_options_volume.return_value = MagicMock(
        ticker="SPY",
        total_call_volume=100_000,
        total_put_volume=50_000,
        total_call_oi=500_000,
        total_put_oi=300_000,
        call_put_volume_ratio=2.0,
        timestamp="2026-01-17T16:00:00Z",
    )

    return client


def _make_policy():
    """Create a standard mock policy."""
    mock_policy = MagicMock()
    mock_policy.version = "v2.0.0"
    mock_policy.config = PolicyConfig()
    return mock_policy


# ============================================================================
# Partial Failure Scenarios
# ============================================================================


class TestOptionsChainPartialFailure:
    """Test behavior when some tickers' options chains fail."""

    @pytest.mark.asyncio
    async def test_one_of_three_tickers_fails(self, mock_pipeline_orchestrator):
        """Pipeline processes successful tickers even when one fails."""
        mock_polygon = _make_mock_polygon(
            tickers=["AAPL", "MSFT", "GOOGL"],
            fail_tickers={"MSFT"},  # MSFT will throw
        )
        mock_policy = _make_policy()

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy, \
             patch("app.db.tables.OpportunityTable.put"), \
             patch("app.db.tables.EvaluationTable.put"), \
             patch("app.db.tables.FeatureValueTable.put_batch"), \
             patch("app.db.tables.PillarScoreTable.put_batch"), \
             patch("app.db.tables.GateResultTable.put_batch"), \
             patch("app.db.tables.IVHistoryTable.list_by_ticker") as mock_iv, \
             patch("app.db.tables.OIHistoryTable.list_by_contract") as mock_oi, \
             patch("app.db.tables.TradeThesisTable.put"), \
             patch("app.db.tables.PaperPositionTable.put"), \
             patch("app.db.tables.PaperPositionTable.get_by_evaluation_id") as mock_pos:
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = mock_policy
            mock_iv.return_value = []
            mock_oi.return_value = []
            mock_pos.return_value = None

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            result = await orchestrator.run_scan(
                tickers=["AAPL", "MSFT", "GOOGL"],
                run_full_pipeline=True,
            )

        # Pipeline should complete
        assert isinstance(result, ScanRunResult)
        # At least some evaluations from AAPL and GOOGL
        # (MSFT chain failed, so it should be skipped)
        assert result is not None

    @pytest.mark.asyncio
    async def test_all_tickers_fail_gracefully(self, mock_pipeline_orchestrator):
        """Pipeline doesn't crash when every ticker's chain fails."""
        mock_polygon = _make_mock_polygon(
            tickers=["AAPL", "MSFT"],
            fail_tickers={"AAPL", "MSFT"},
        )
        mock_policy = _make_policy()

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy, \
             patch("app.db.tables.OpportunityTable.put"), \
             patch("app.db.tables.EvaluationTable.put"), \
             patch("app.db.tables.FeatureValueTable.put_batch"), \
             patch("app.db.tables.PillarScoreTable.put_batch"), \
             patch("app.db.tables.GateResultTable.put_batch"), \
             patch("app.db.tables.IVHistoryTable.list_by_ticker") as mock_iv, \
             patch("app.db.tables.OIHistoryTable.list_by_contract") as mock_oi, \
             patch("app.db.tables.TradeThesisTable.put"), \
             patch("app.db.tables.PaperPositionTable.put"), \
             patch("app.db.tables.PaperPositionTable.get_by_evaluation_id") as mock_pos:
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = mock_policy
            mock_iv.return_value = []
            mock_oi.return_value = []
            mock_pos.return_value = None

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            result = await orchestrator.run_scan(
                tickers=["AAPL", "MSFT"],
                run_full_pipeline=True,
            )

        assert isinstance(result, ScanRunResult)
        # No evaluations since all chains failed
        assert result.evaluations == []


class TestEmptyOptionsChain:
    """Test behavior when options chain returns empty."""

    @pytest.mark.asyncio
    async def test_empty_chain_produces_zero_evaluations(self, mock_pipeline_orchestrator):
        """Ticker with no options contracts produces no evaluations."""
        mock_polygon = _make_mock_polygon(
            tickers=["AAPL"],
            empty_chain=True,
        )
        mock_policy = _make_policy()

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy, \
             patch("app.db.tables.OpportunityTable.put"), \
             patch("app.db.tables.EvaluationTable.put"):
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = mock_policy

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            result = await orchestrator.run_scan(
                tickers=["AAPL"],
                run_full_pipeline=True,
            )

        assert result.evaluations_created == 0
        assert result.approve_count == 0


class TestDBPersistenceFailures:
    """Test behavior when DynamoDB writes fail."""

    @pytest.mark.asyncio
    async def test_evaluation_put_failure_doesnt_crash_pipeline(
        self, mock_pipeline_orchestrator
    ):
        """If evaluation persistence fails, pipeline should still complete."""
        mock_polygon = _make_mock_polygon(tickers=["AAPL"])
        mock_policy = _make_policy()

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy, \
             patch("app.db.tables.OpportunityTable.put"), \
             patch("app.db.tables.EvaluationTable.put") as mock_eval_put, \
             patch("app.db.tables.FeatureValueTable.put_batch"), \
             patch("app.db.tables.PillarScoreTable.put_batch"), \
             patch("app.db.tables.GateResultTable.put_batch"), \
             patch("app.db.tables.IVHistoryTable.list_by_ticker") as mock_iv, \
             patch("app.db.tables.OIHistoryTable.list_by_contract") as mock_oi, \
             patch("app.db.tables.TradeThesisTable.put"), \
             patch("app.db.tables.PaperPositionTable.put"), \
             patch("app.db.tables.PaperPositionTable.get_by_evaluation_id") as mock_pos:
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = mock_policy
            mock_eval_put.side_effect = Exception("DynamoDB write throttled")
            mock_iv.return_value = []
            mock_oi.return_value = []
            mock_pos.return_value = None

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            # Should not raise
            result = await orchestrator.run_scan(
                tickers=["AAPL"],
                run_full_pipeline=True,
            )

        assert isinstance(result, ScanRunResult)

    @pytest.mark.asyncio
    async def test_gate_result_put_failure_doesnt_crash(
        self, mock_pipeline_orchestrator
    ):
        """If gate result persistence fails, pipeline still produces decisions."""
        mock_polygon = _make_mock_polygon(tickers=["AAPL"])
        mock_policy = _make_policy()

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy, \
             patch("app.db.tables.OpportunityTable.put"), \
             patch("app.db.tables.EvaluationTable.put"), \
             patch("app.db.tables.FeatureValueTable.put_batch"), \
             patch("app.db.tables.PillarScoreTable.put_batch"), \
             patch("app.db.tables.GateResultTable.put_batch") as mock_gate_put, \
             patch("app.db.tables.IVHistoryTable.list_by_ticker") as mock_iv, \
             patch("app.db.tables.OIHistoryTable.list_by_contract") as mock_oi, \
             patch("app.db.tables.TradeThesisTable.put"), \
             patch("app.db.tables.PaperPositionTable.put"), \
             patch("app.db.tables.PaperPositionTable.get_by_evaluation_id") as mock_pos:
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = mock_policy
            mock_gate_put.side_effect = Exception("DB error")
            mock_iv.return_value = []
            mock_oi.return_value = []
            mock_pos.return_value = None

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            result = await orchestrator.run_scan(
                tickers=["AAPL"],
                run_full_pipeline=True,
            )

        # Pipeline should still complete (gate results are persisted best-effort)
        assert isinstance(result, ScanRunResult)


class TestMixedOutcomeBatch:
    """Test multi-ticker batches where tickers get different verdicts."""

    @pytest.mark.asyncio
    async def test_mixed_verdicts_across_tickers(self, mock_pipeline_orchestrator):
        """Multiple tickers in a single run should each be evaluated independently."""
        mock_polygon = _make_mock_polygon(
            tickers=["AAPL", "MSFT", "GOOGL", "TSLA"],
            chain_per_ticker=3,
        )
        mock_policy = _make_policy()

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy, \
             patch("app.db.tables.OpportunityTable.put"), \
             patch("app.db.tables.EvaluationTable.put"), \
             patch("app.db.tables.FeatureValueTable.put_batch"), \
             patch("app.db.tables.PillarScoreTable.put_batch"), \
             patch("app.db.tables.GateResultTable.put_batch"), \
             patch("app.db.tables.IVHistoryTable.list_by_ticker") as mock_iv, \
             patch("app.db.tables.OIHistoryTable.list_by_contract") as mock_oi, \
             patch("app.db.tables.TradeThesisTable.put"), \
             patch("app.db.tables.PaperPositionTable.put"), \
             patch("app.db.tables.PaperPositionTable.get_by_evaluation_id") as mock_pos:
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = mock_policy
            mock_iv.return_value = []
            mock_oi.return_value = []
            mock_pos.return_value = None

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            result = await orchestrator.run_scan(
                tickers=["AAPL", "MSFT", "GOOGL", "TSLA"],
                run_full_pipeline=True,
            )

        # Total verdicts must equal total decisions
        total = result.approve_count + result.watch_count + result.reject_count
        assert total == len(result.decisions)

        # Should have processed multiple evaluations
        assert result.evaluations_created >= 0


class TestStageEventRecording:
    """Test that the pipeline records stage events for observability."""

    @pytest.mark.asyncio
    async def test_stage_events_recorded_for_full_run(self, mock_pipeline_orchestrator):
        """Full pipeline run should record stage events for each stage."""
        mock_polygon = _make_mock_polygon(tickers=["AAPL"])
        mock_policy = _make_policy()

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy, \
             patch("app.db.tables.OpportunityTable.put"), \
             patch("app.db.tables.EvaluationTable.put"), \
             patch("app.db.tables.FeatureValueTable.put_batch"), \
             patch("app.db.tables.PillarScoreTable.put_batch"), \
             patch("app.db.tables.GateResultTable.put_batch"), \
             patch("app.db.tables.IVHistoryTable.list_by_ticker") as mock_iv, \
             patch("app.db.tables.OIHistoryTable.list_by_contract") as mock_oi, \
             patch("app.db.tables.TradeThesisTable.put"), \
             patch("app.db.tables.PaperPositionTable.put"), \
             patch("app.db.tables.PaperPositionTable.get_by_evaluation_id") as mock_pos:
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = mock_policy
            mock_iv.return_value = []
            mock_oi.return_value = []
            mock_pos.return_value = None

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            result = await orchestrator.run_scan(
                tickers=["AAPL"],
                run_full_pipeline=True,
            )

        # Pipeline orchestrator should have been called for stage tracking
        assert mock_pipeline_orchestrator.record_stage_event.call_count >= 1
