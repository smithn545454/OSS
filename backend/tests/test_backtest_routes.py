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
    with patch("app.api.routes.backtest._get_s3_client") as mock_get, \
         patch("app.api.routes.backtest._get_bucket_name", return_value="test-bucket"):
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

        def paginate_side_effect(Bucket, Prefix):
            if "options-chains" in Prefix:
                # Return objects with date partitions
                objects = [
                    {"Key": f"options-chains/date=2024-01-{i:02d}/data.parquet", "Size": 1024 * 1024}
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

        await BacktestRunTable.put({
            "run_id": "test-run-1",
            "name": "Test Run",
            "status": "COMPLETED",
            "created_at": "2026-01-15T12:00:00+00:00",
        })

        response = client.get("/api/backtest/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1


class TestCreateRun:
    def test_create_run_invalid_dates(self, client, moto_dynamodb):
        response = client.post("/api/backtest/runs", json={
            "name": "Test",
            "start_date": "not-a-date",
            "end_date": "2026-01-10",
        })
        assert response.status_code == 400

    def test_create_run_start_after_end(self, client, moto_dynamodb):
        response = client.post("/api/backtest/runs", json={
            "name": "Test",
            "start_date": "2026-01-20",
            "end_date": "2026-01-10",
        })
        assert response.status_code == 400

    def test_create_run_weekend_only(self, client, moto_dynamodb):
        # Saturday to Sunday — no trading days
        response = client.post("/api/backtest/runs", json={
            "name": "Test",
            "start_date": "2026-01-24",  # Saturday
            "end_date": "2026-01-25",  # Sunday
        })
        assert response.status_code == 400

    def test_create_run_success(self, client, moto_dynamodb):
        """Test successful run creation with inline mode."""
        # Mock the background task to prevent import of pyarrow
        with patch(
            "app.api.routes.backtest._run_backtest_inline",
            new_callable=AsyncMock,
        ):
            response = client.post("/api/backtest/runs", json={
                "name": "Test Run",
                "start_date": "2026-01-05",
                "end_date": "2026-01-09",
            })
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
            response = client.post("/api/backtest/runs", json={
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
            })
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

        await BacktestRunTable.put({
            "run_id": "del-run-1",
            "name": "Delete Me",
            "status": "COMPLETED",
            "created_at": "2026-01-15T12:00:00+00:00",
        })

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

        await BacktestRunTable.put({
            "run_id": "trades-run-1",
            "name": "Trades Run",
            "status": "COMPLETED",
            "progress": {"days_completed": 5, "days_total": 5, "trades_found": 0},
            "created_at": "2026-01-15T12:00:00+00:00",
        })

        response = client.get("/api/backtest/runs/trades-run-1/trades")
        assert response.status_code == 200
        data = response.json()
        assert data["trades"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_list_trades_with_data(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

        await BacktestRunTable.put({
            "run_id": "trades-run-2",
            "name": "Trades Run",
            "status": "COMPLETED",
            "progress": {"days_completed": 5, "days_total": 5, "trades_found": 2},
            "created_at": "2026-01-15T12:00:00+00:00",
        })

        await BacktestTradeTable.put({
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
        })

        await BacktestTradeTable.put({
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
        })

        response = client.get("/api/backtest/runs/trades-run-2/trades")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2

    @pytest.mark.asyncio
    async def test_list_trades_with_scanner_filter(self, client, moto_dynamodb):
        from app.db.backtest_tables import BacktestRunTable, BacktestTradeTable

        await BacktestRunTable.put({
            "run_id": "trades-run-3",
            "name": "Filtered Run",
            "status": "COMPLETED",
            "progress": {"trades_found": 2},
            "created_at": "2026-01-15T12:00:00+00:00",
        })

        for i, scanner in enumerate(["breakout", "compression"]):
            await BacktestTradeTable.put({
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
            })

        # Filter by scanner
        response = client.get("/api/backtest/runs/trades-run-3/trades?scanner=breakout")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["trades"][0]["scanner_type"] == "breakout"
