"""Tests for backtest API routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_s3():
    """Mock S3 client for route tests."""
    with (
        patch("app.api.routes.backtest._get_s3_client") as mock_get,
        patch("app.api.routes.backtest._get_bucket_name", return_value="test-bucket"),
    ):
        s3 = MagicMock()
        mock_get.return_value = s3
        yield s3


class TestDataStoreStatus:
    def test_status_empty_bucket(self, client, mock_s3):
        # S3 returns no objects for any prefix
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": []}]
        mock_s3.get_paginator.return_value = paginator
        error_resp = {"Error": {"Code": "404", "Message": "Not Found"}}
        mock_s3.head_object.side_effect = ClientError(error_resp, "HeadObject")

        response = client.get("/api/backtest/data-store/status")
        assert response.status_code == 200
        data = response.json()
        assert data["bucket"] == "test-bucket"
        assert data["overall_status"] in ("incomplete", "empty")
        assert len(data["datasets"]) == 4

    def test_status_with_data(self, client, mock_s3):
        # Mock paginator to return some objects
        paginator = MagicMock()

        def paginate_side_effect(**kwargs):
            if "options-chains" in kwargs.get("Prefix", ""):
                # Return objects with date partitions
                objects = [
                    {
                        "Key": f"options-chains/date=2024-01-{i:02d}/data.parquet",
                        "Size": 1024 * 1024,
                    }
                    for i in range(2, 22)
                ]
                return [{"Contents": objects}]
            return [{"Contents": []}]

        paginator.paginate = MagicMock(side_effect=paginate_side_effect)
        mock_s3.get_paginator.return_value = paginator
        error_resp = {"Error": {"Code": "404", "Message": "Not Found"}}
        mock_s3.head_object.side_effect = ClientError(error_resp, "HeadObject")

        response = client.get("/api/backtest/data-store/status")
        assert response.status_code == 200
        data = response.json()
        # Options chains should have "partial" status (20 dates < 100)
        options = next(d for d in data["datasets"] if d["name"] == "Options Chains")
        assert options["status"] == "partial"
        assert options["date_count"] == 20


class TestDataStoreValidate:
    def test_validate_bucket_not_found(self, client, mock_s3):
        from botocore.exceptions import ClientError

        error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
        mock_s3.head_bucket.side_effect = ClientError(error_response, "HeadBucket")

        response = client.post("/api/backtest/data-store/validate")
        assert response.status_code == 200
        data = response.json()
        assert data["passed"] is False
        assert data["checks"][0]["check_name"] == "bucket_exists"
        assert data["checks"][0]["passed"] is False

    def test_validate_bucket_exists_empty(self, client, mock_s3):
        mock_s3.head_bucket.return_value = {}
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": []}]
        mock_s3.get_paginator.return_value = paginator
        error_resp = {"Error": {"Code": "404", "Message": "Not Found"}}
        mock_s3.head_object.side_effect = ClientError(error_resp, "HeadObject")

        response = client.post("/api/backtest/data-store/validate")
        assert response.status_code == 200
        data = response.json()
        assert data["passed"] is False
        # Bucket exists check should pass
        bucket_check = next(c for c in data["checks"] if c["check_name"] == "bucket_exists")
        assert bucket_check["passed"] is True


class TestBacktestRuns:
    def test_list_runs_empty(self, client, moto_dynamodb):
        response = client.get("/api/backtest/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["runs"] == []
        assert data["count"] == 0

    def test_get_run_not_found(self, client, moto_dynamodb):
        response = client.get("/api/backtest/runs/nonexistent")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_runs_with_data(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable

        await BacktestRunTable.put(
            {
                "run_id": "test-run-1",
                "name": "Test Run",
                "status": "COMPLETED",
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )

        response = client.get("/api/backtest/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1


class TestCreateRun:
    def test_create_run_invalid_dates(self, client, moto_dynamodb):
        response = client.post(
            "/api/backtest/runs",
            json={
                "name": "Test",
                "start_date": "not-a-date",
                "end_date": "2026-01-10",
            },
        )
        assert response.status_code == 400

    def test_create_run_start_after_end(self, client, moto_dynamodb):
        response = client.post(
            "/api/backtest/runs",
            json={
                "name": "Test",
                "start_date": "2026-01-20",
                "end_date": "2026-01-10",
            },
        )
        assert response.status_code == 400

    def test_create_run_weekend_only(self, client, moto_dynamodb):
        # Saturday to Sunday — no trading days
        response = client.post(
            "/api/backtest/runs",
            json={
                "name": "Test",
                "start_date": "2026-01-24",  # Saturday
                "end_date": "2026-01-25",  # Sunday
            },
        )
        assert response.status_code == 400

    def test_create_run_success(self, client, moto_dynamodb):
        """Test successful run creation with inline mode."""
        # Mock the background task to prevent import of pyarrow
        with patch(
            "app.api.routes.backtest._run_backtest_inline",
            new_callable=AsyncMock,
        ):
            response = client.post(
                "/api/backtest/runs",
                json={
                    "name": "Test Run",
                    "start_date": "2026-01-05",
                    "end_date": "2026-01-09",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["run_id"]
        assert data["trading_days"] == 5
        assert data["mode"] == "inline"

    def test_create_run_custom_params(self, client, moto_dynamodb):
        with patch(
            "app.api.routes.backtest._run_backtest_inline",
            new_callable=AsyncMock,
        ):
            response = client.post(
                "/api/backtest/runs",
                json={
                    "name": "Custom Run",
                    "start_date": "2026-01-05",
                    "end_date": "2026-01-09",
                    "slippage_model": "mid",
                    "slippage_pct": 0.0,
                    "scanners_enabled": ["breakout", "compression"],
                    "starting_capital": 50_000.0,
                    "exit_rules": {
                        "stop_loss_pct": 30.0,
                        "profit_target_pct": 80.0,
                        "max_holding_days": 14,
                    },
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"


class TestDeleteRun:
    @pytest.mark.asyncio
    async def test_delete_run_not_found(self, client, moto_dynamodb):
        response = client.delete("/api/backtest/runs/nonexistent")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_run_success(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable

        await BacktestRunTable.put(
            {
                "run_id": "del-run-1",
                "name": "Delete Me",
                "status": "COMPLETED",
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )

        response = client.delete("/api/backtest/runs/del-run-1")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["run_id"] == "del-run-1"

        # Verify it's gone
        response = client.get("/api/backtest/runs/del-run-1")
        assert response.status_code == 404


class TestListTrades:
    @pytest.mark.asyncio
    async def test_list_trades_run_not_found(self, client, moto_dynamodb):
        response = client.get("/api/backtest/runs/nonexistent/trades")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_trades_empty(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable

        await BacktestRunTable.put(
            {
                "run_id": "trades-run-1",
                "name": "Trades Run",
                "status": "COMPLETED",
                "progress": {"days_completed": 5, "days_total": 5, "trades_found": 0},
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )

        response = client.get("/api/backtest/runs/trades-run-1/trades")
        assert response.status_code == 200
        data = response.json()
        assert data["trades"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_list_trades_with_data(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

        await BacktestRunTable.put(
            {
                "run_id": "trades-run-2",
                "name": "Trades Run",
                "status": "COMPLETED",
                "progress": {"days_completed": 5, "days_total": 5, "trades_found": 2},
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )

        await BacktestTradeTable.put(
            {
                "trade_id": "t1",
                "run_id": "trades-run-2",
                "ticker": "AAPL",
                "option_ticker": "O:AAPL260220C00200000",
                "option_type": "CALL",
                "strike": 200.0,
                "entry_date": "2026-01-05",
                "exit_date": "2026-01-08",
                "expiration_date": "2026-02-20",
                "scanner_type": "breakout",
                "verdict": "APPROVE",
                "combined_score": 82.0,
                "entry_price": 5.25,
                "exit_price": 7.88,
                "exit_reason": "PROFIT_TARGET",
                "pnl_pct": 50.0,
            }
        )

        await BacktestTradeTable.put(
            {
                "trade_id": "t2",
                "run_id": "trades-run-2",
                "ticker": "MSFT",
                "option_ticker": "O:MSFT260220P00350000",
                "option_type": "PUT",
                "strike": 350.0,
                "entry_date": "2026-01-05",
                "expiration_date": "2026-02-20",
                "scanner_type": "compression",
                "verdict": "WATCH",
                "combined_score": 68.0,
                "entry_price": 3.50,
            }
        )

        response = client.get("/api/backtest/runs/trades-run-2/trades")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2

    @pytest.mark.asyncio
    async def test_list_trades_with_scanner_filter(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

        await BacktestRunTable.put(
            {
                "run_id": "trades-run-3",
                "name": "Filtered Run",
                "status": "COMPLETED",
                "progress": {"trades_found": 2},
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )

        for i, scanner in enumerate(["breakout", "compression"]):
            await BacktestTradeTable.put(
                {
                    "trade_id": f"tf{i}",
                    "run_id": "trades-run-3",
                    "ticker": "AAPL",
                    "option_ticker": f"O:AAPL{i}",
                    "option_type": "CALL",
                    "strike": 200.0,
                    "entry_date": "2026-01-05",
                    "expiration_date": "2026-02-20",
                    "scanner_type": scanner,
                    "verdict": "APPROVE",
                    "combined_score": 80.0,
                    "entry_price": 5.0,
                }
            )

        # Filter by scanner
        response = client.get("/api/backtest/runs/trades-run-3/trades?scanner=breakout")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["trades"][0]["scanner_type"] == "breakout"


# ============================================================================
# Results Endpoints (Phase 3)
# ============================================================================


def _seed_completed_run(run_id: str = "results-run-1"):
    """Helper to seed a completed run with trades for results tests."""
    import asyncio

    from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

    trades_data = [
        {
            "trade_id": f"rt{i}",
            "run_id": run_id,
            "ticker": ticker,
            "option_ticker": f"O:{ticker}260220C00200000",
            "option_type": "CALL",
            "strike": 200.0,
            "entry_date": entry,
            "exit_date": exit_d,
            "expiration_date": "2026-03-20",
            "scanner_type": scanner,
            "verdict": "APPROVE",
            "combined_score": score,
            "entry_price": 5.00,
            "exit_price": exit_p,
            "exit_reason": reason,
            "pnl_dollars": pnl,
            "pnl_pct": pnl_pct,
            "days_held": days,
            "mfe_pct": 30.0,
            "mae_pct": -10.0,
            "peak_price": 7.0,
            "market_regime": regime,
        }
        for i, (
            ticker,
            scanner,
            entry,
            exit_d,
            exit_p,
            pnl,
            pnl_pct,
            days,
            score,
            reason,
            regime,
        ) in enumerate(
            [
                (
                    "AAPL",
                    "breakout",
                    "2026-01-05",
                    "2026-01-08",
                    7.50,
                    250,
                    50,
                    3,
                    82,
                    "PROFIT_TARGET",
                    "bull_trend",
                ),
                (
                    "MSFT",
                    "compression",
                    "2026-01-06",
                    "2026-01-10",
                    3.80,
                    -120,
                    -24,
                    4,
                    74,
                    "STOP_LOSS",
                    "low_vol",
                ),
                (
                    "GOOG",
                    "breakout",
                    "2026-01-07",
                    "2026-01-12",
                    8.20,
                    320,
                    64,
                    5,
                    88,
                    "PROFIT_TARGET",
                    "bull_trend",
                ),
                (
                    "TSLA",
                    "cheap_options",
                    "2026-01-08",
                    "2026-01-15",
                    2.10,
                    -290,
                    -58,
                    7,
                    65,
                    "STOP_LOSS",
                    "high_vol",
                ),
                (
                    "NVDA",
                    "breakout",
                    "2026-02-03",
                    "2026-02-07",
                    9.00,
                    400,
                    80,
                    4,
                    91,
                    "PROFIT_TARGET",
                    "bull_trend",
                ),
                (
                    "META",
                    "compression",
                    "2026-02-04",
                    "2026-02-10",
                    6.50,
                    150,
                    30,
                    6,
                    78,
                    "TRAILING_STOP",
                    "low_vol",
                ),
            ]
        )
    ]

    async def _seed():
        await BacktestRunTable.put(
            {
                "run_id": run_id,
                "name": "Results Test Run",
                "status": "COMPLETED",
                "config": {
                    "starting_capital": 10000.0,
                    "start_date": "2026-01-05",
                    "end_date": "2026-02-10",
                },
                "summary": {
                    "total_trades": 6,
                    "sharpe_ratio": 1.8,
                    "profit_factor": 2.1,
                    "max_drawdown_pct": 8.5,
                    "net_pnl": 710.0,
                    "win_rate": 66.7,
                },
                "progress": {"days_completed": 25, "days_total": 25, "trades_found": 6},
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )
        for t in trades_data:
            await BacktestTradeTable.put(t)

    asyncio.get_event_loop().run_until_complete(_seed())


class TestSummaryEndpoint:
    @pytest.mark.asyncio
    async def test_summary_not_found(self, client, moto_dynamodb):
        response = client.get("/api/backtest/runs/nonexistent/summary")
        assert response.status_code == 404

    def test_summary_completed_with_stored_summary(self, client, moto_dynamodb):
        _seed_completed_run()
        response = client.get("/api/backtest/runs/results-run-1/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "COMPLETED"
        assert data["summary"]["total_trades"] == 6
        assert data["summary"]["sharpe_ratio"] == 1.8

    @pytest.mark.asyncio
    async def test_summary_pending_run(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable

        await BacktestRunTable.put(
            {
                "run_id": "pending-run",
                "name": "Pending",
                "status": "RUNNING",
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )
        response = client.get("/api/backtest/runs/pending-run/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["summary"] is None
        assert "RUNNING" in data.get("message", "")


class TestEquityCurveEndpoint:
    def test_equity_curve_completed(self, client, moto_dynamodb):
        _seed_completed_run("ec-run-1")
        response = client.get("/api/backtest/runs/ec-run-1/equity-curve")
        assert response.status_code == 200
        data = response.json()
        assert data["starting_capital"] == 10000.0
        assert len(data["curve"]) > 0
        # First point should be near starting capital
        assert data["curve"][0]["equity"] >= 9000

    @pytest.mark.asyncio
    async def test_equity_curve_running(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable

        await BacktestRunTable.put(
            {
                "run_id": "ec-running",
                "name": "Running",
                "status": "RUNNING",
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )
        response = client.get("/api/backtest/runs/ec-running/equity-curve")
        assert response.status_code == 200
        data = response.json()
        assert data["curve"] == []


class TestMonthlyPnlEndpoint:
    def test_monthly_pnl_completed(self, client, moto_dynamodb):
        _seed_completed_run("mp-run-1")
        response = client.get("/api/backtest/runs/mp-run-1/monthly-pnl")
        assert response.status_code == 200
        data = response.json()
        assert data["total_months"] >= 1
        # Should have Jan and Feb data
        months = [m["month"] for m in data["months"]]
        assert "2026-01" in months


class TestSegmentsEndpoint:
    def test_segments_by_scanner(self, client, moto_dynamodb):
        _seed_completed_run("seg-run-1")
        response = client.get("/api/backtest/runs/seg-run-1/segments/scanner")
        assert response.status_code == 200
        data = response.json()
        assert data["segment_type"] == "scanner"
        assert data["total_segments"] >= 2  # breakout, compression, cheap_options

    def test_segments_invalid_type(self, client, moto_dynamodb):
        _seed_completed_run("seg-run-2")
        response = client.get("/api/backtest/runs/seg-run-2/segments/invalid_type")
        assert response.status_code == 400

    def test_segments_by_regime(self, client, moto_dynamodb):
        _seed_completed_run("seg-run-3")
        response = client.get("/api/backtest/runs/seg-run-3/segments/regime")
        assert response.status_code == 200
        data = response.json()
        assert data["total_segments"] >= 2  # bull_trend, low_vol, high_vol


# ============================================================================
# AI Advisor Endpoints (Phase 4)
# ============================================================================


class TestReadinessEndpoint:
    def test_readiness_completed(self, client, moto_dynamodb):
        _seed_completed_run("ready-run-1")
        response = client.get("/api/backtest/runs/ready-run-1/readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "COMPLETED"
        readiness = data["readiness"]
        assert readiness["verdict"] in ("READY", "CONDITIONAL", "NOT_READY")
        assert readiness["total_checks"] == 7
        assert "checks" in readiness
        # Each check should have required fields
        for check in readiness["checks"]:
            assert "criterion" in check
            assert "passed" in check
            assert "severity" in check

    @pytest.mark.asyncio
    async def test_readiness_not_found(self, client, moto_dynamodb):
        response = client.get("/api/backtest/runs/nonexistent/readiness")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_readiness_running(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable

        await BacktestRunTable.put(
            {
                "run_id": "ready-running",
                "name": "Running",
                "status": "RUNNING",
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )
        response = client.get("/api/backtest/runs/ready-running/readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["readiness"] is None


class TestInsightsEndpoint:
    @pytest.mark.asyncio
    async def test_insights_empty(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable

        await BacktestRunTable.put(
            {
                "run_id": "insights-run-1",
                "name": "Insights Run",
                "status": "COMPLETED",
                "created_at": "2026-01-15T12:00:00+00:00",
            }
        )
        response = client.get("/api/backtest/runs/insights-run-1/insights")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["insights"] == []

    @pytest.mark.asyncio
    async def test_insights_not_found(self, client, moto_dynamodb):
        response = client.get("/api/backtest/runs/nonexistent/insights")
        assert response.status_code == 404


# ============================================================================
# End-to-End Integration Test (Phase 5)
# ============================================================================


class TestEndToEndFlow:
    """Test the full backtest flow: create run → trades → summary → readiness."""

    @pytest.mark.asyncio
    async def test_full_flow(self, client, moto_dynamodb):
        """Test that a completed run with trades flows through all endpoints."""
        from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

        run_id = "e2e-test-run"

        # Step 1: Seed run record (as if coordinator created it)
        await BacktestRunTable.put(
            {
                "run_id": run_id,
                "name": "E2E Test Run",
                "status": "COMPLETED",
                "config": {
                    "starting_capital": 10000.0,
                    "start_date": "2024-06-03",
                    "end_date": "2024-09-30",
                },
                "summary": {
                    "total_trades": 8,
                    "net_pnl": 1250.0,
                    "return_pct": 12.5,
                    "sharpe_ratio": 1.9,
                    "profit_factor": 2.3,
                    "max_drawdown_pct": 7.2,
                    "win_rate": 62.5,
                },
                "progress": {"days_completed": 85, "days_total": 85, "trades_found": 8},
                "created_at": "2026-02-23T12:00:00+00:00",
            }
        )

        # Step 2: Seed trades across multiple months, scanners, and regimes
        trades_seed = [
            (
                "AAPL",
                "breakout",
                "2024-06-05",
                "2024-06-10",
                200,
                40,
                "PROFIT_TARGET",
                "bull_trend",
                85,
            ),
            (
                "GOOG",
                "compression",
                "2024-06-15",
                "2024-06-20",
                150,
                30,
                "TRAILING_STOP",
                "low_vol",
                78,
            ),
            (
                "MSFT",
                "breakout",
                "2024-07-02",
                "2024-07-08",
                -100,
                -20,
                "STOP_LOSS",
                "bull_trend",
                72,
            ),
            (
                "NVDA",
                "cheap_options",
                "2024-07-15",
                "2024-07-22",
                300,
                60,
                "PROFIT_TARGET",
                "high_vol",
                90,
            ),
            ("TSLA", "breakout", "2024-08-01", "2024-08-06", -150, -30, "STOP_LOSS", "choppy", 68),
            (
                "META",
                "compression",
                "2024-08-12",
                "2024-08-18",
                250,
                50,
                "PROFIT_TARGET",
                "bull_trend",
                82,
            ),
            (
                "AMZN",
                "breakout",
                "2024-09-03",
                "2024-09-10",
                400,
                80,
                "PROFIT_TARGET",
                "bull_trend",
                92,
            ),
            (
                "AMD",
                "cheap_options",
                "2024-09-15",
                "2024-09-22",
                100,
                20,
                "MAX_HOLDING",
                "low_vol",
                75,
            ),
        ]

        for i, (tkr, sc, entry, exit_d, pnl, pnl_pct, reason, regime, score) in enumerate(
            trades_seed
        ):
            await BacktestTradeTable.put(
                {
                    "trade_id": f"e2e-t{i}",
                    "run_id": run_id,
                    "ticker": tkr,
                    "option_ticker": f"O:{tkr}240920C00200000",
                    "option_type": "CALL",
                    "strike": 200.0,
                    "entry_date": entry,
                    "exit_date": exit_d,
                    "expiration_date": "2024-10-18",
                    "scanner_type": sc,
                    "verdict": "APPROVE",
                    "combined_score": float(score),
                    "entry_price": 5.00,
                    "exit_price": 5.00 + pnl / 100.0,
                    "exit_reason": reason,
                    "pnl_dollars": float(pnl),
                    "pnl_pct": float(pnl_pct),
                    "days_held": 5,
                    "mfe_pct": 30.0,
                    "mae_pct": -10.0,
                    "peak_price": 7.0,
                    "market_regime": regime,
                }
            )

        # Step 3: Verify run shows up in list
        response = client.get("/api/backtest/runs")
        assert response.status_code == 200
        runs = response.json()["runs"]
        assert any(r.get("run_id") == run_id for r in runs)

        # Step 4: Verify run details
        response = client.get(f"/api/backtest/runs/{run_id}")
        assert response.status_code == 200
        run_data = response.json()
        assert run_data["status"] == "COMPLETED"

        # Step 5: Verify summary
        response = client.get(f"/api/backtest/runs/{run_id}/summary")
        assert response.status_code == 200
        summary = response.json()["summary"]
        assert summary["total_trades"] == 8
        assert summary["sharpe_ratio"] == 1.9

        # Step 6: Verify trades
        response = client.get(f"/api/backtest/runs/{run_id}/trades")
        assert response.status_code == 200
        trades_data = response.json()
        assert trades_data["count"] == 8

        # Step 7: Verify equity curve
        response = client.get(f"/api/backtest/runs/{run_id}/equity-curve")
        assert response.status_code == 200
        ec_data = response.json()
        assert ec_data["starting_capital"] == 10000.0
        assert len(ec_data["curve"]) > 0

        # Step 8: Verify monthly P&L
        response = client.get(f"/api/backtest/runs/{run_id}/monthly-pnl")
        assert response.status_code == 200
        mp_data = response.json()
        assert mp_data["total_months"] >= 3  # Jun, Jul, Aug, Sep

        # Step 9: Verify segmented analysis for each type
        for seg_type in ["scanner", "regime", "month", "exit_reason"]:
            response = client.get(f"/api/backtest/runs/{run_id}/segments/{seg_type}")
            assert response.status_code == 200
            seg_data = response.json()
            assert seg_data["total_segments"] >= 2, f"{seg_type} should have >= 2 segments"

        # Step 10: Verify readiness assessment
        response = client.get(f"/api/backtest/runs/{run_id}/readiness")
        assert response.status_code == 200
        readiness = response.json()["readiness"]
        assert readiness["verdict"] in ("READY", "CONDITIONAL", "NOT_READY")
        assert readiness["total_checks"] == 7
        # With only 8 trades, min_trades (300) should fail
        min_trades_check = next(
            c for c in readiness["checks"] if c["criterion"] == "Minimum Trades"
        )
        assert not min_trades_check["passed"]

        # Step 11: Verify insights (empty for now — no AI analysis triggered)
        response = client.get(f"/api/backtest/runs/{run_id}/insights")
        assert response.status_code == 200
        assert response.json()["count"] == 0
