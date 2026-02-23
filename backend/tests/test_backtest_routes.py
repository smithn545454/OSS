"""Tests for backtest API routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
