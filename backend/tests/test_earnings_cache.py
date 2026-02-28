"""Tests for EarningsCacheService and FinnhubClient earnings methods."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.services.earnings_cache import CacheResult, EarningsCacheService


@pytest.fixture
def earnings_table(moto_dynamodb):
    """Return the mocked earnings-cache DynamoDB table."""
    import boto3

    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table("oss-test-earnings-cache")


@pytest.fixture
def mock_finnhub():
    """Create a mock FinnhubClient."""
    client = AsyncMock()
    client.get_all_upcoming_earnings = AsyncMock(return_value=[])
    client.get_next_earnings_date = AsyncMock(return_value=None)
    return client


class TestCacheResult:
    """Tests for the CacheResult sentinel type."""

    def test_cache_miss(self):
        result = CacheResult(hit=False)
        assert result.hit is False
        assert result.days_to_earnings is None

    def test_cache_hit_with_earnings(self):
        result = CacheResult(hit=True, days_to_earnings=5)
        assert result.hit is True
        assert result.days_to_earnings == 5

    def test_cache_hit_no_earnings(self):
        result = CacheResult(hit=True, days_to_earnings=None)
        assert result.hit is True
        assert result.days_to_earnings is None


class TestGetCached:
    """Tests for _get_cached sentinel behavior."""

    def test_miss_when_absent(self, earnings_table):
        """No item in DynamoDB should return cache miss."""
        service = EarningsCacheService(table_name="oss-test-earnings-cache")
        result = service._get_cached("AAPL")
        assert result.hit is False

    def test_miss_when_expired(self, earnings_table):
        """Expired TTL should return cache miss."""
        # Insert an item with expired TTL
        earnings_table.put_item(Item={
            "ticker": "AAPL",
            "earnings_date": "2026-03-15",
            "earnings_time": "amc",
            "fetched_at": datetime.utcnow().isoformat(),
            "ttl": int((datetime.utcnow() - timedelta(hours=1)).timestamp()),
        })
        service = EarningsCacheService(table_name="oss-test-earnings-cache")
        result = service._get_cached("AAPL")
        assert result.hit is False

    def test_hit_not_found(self, earnings_table):
        """Cached 'not found' entry should return hit with None days."""
        earnings_table.put_item(Item={
            "ticker": "AAPL",
            "earnings_date": None,
            "earnings_time": None,
            "fetched_at": datetime.utcnow().isoformat(),
            "not_found": True,
            "ttl": int((datetime.utcnow() + timedelta(hours=24)).timestamp()),
        })
        service = EarningsCacheService(table_name="oss-test-earnings-cache")
        result = service._get_cached("AAPL")
        assert result.hit is True
        assert result.days_to_earnings is None

    def test_hit_with_future_date(self, earnings_table):
        """Cached future earnings date should return hit with positive days."""
        future_date = date.today() + timedelta(days=10)
        earnings_table.put_item(Item={
            "ticker": "AAPL",
            "earnings_date": future_date.isoformat(),
            "earnings_time": "bmo",
            "fetched_at": datetime.utcnow().isoformat(),
            "ttl": int((datetime.utcnow() + timedelta(days=11)).timestamp()),
        })
        service = EarningsCacheService(table_name="oss-test-earnings-cache")
        result = service._get_cached("AAPL")
        assert result.hit is True
        assert result.days_to_earnings == 10


class TestGetDaysToEarnings:
    """Tests for get_days_to_earnings with sentinel fix."""

    @pytest.mark.asyncio
    async def test_does_not_refetch_on_not_found(self, earnings_table, mock_finnhub):
        """Cached 'not found' should NOT trigger a Finnhub API call."""
        earnings_table.put_item(Item={
            "ticker": "AAPL",
            "earnings_date": None,
            "earnings_time": None,
            "fetched_at": datetime.utcnow().isoformat(),
            "not_found": True,
            "ttl": int((datetime.utcnow() + timedelta(hours=24)).timestamp()),
        })
        service = EarningsCacheService(
            finnhub_client=mock_finnhub, table_name="oss-test-earnings-cache",
        )
        result = await service.get_days_to_earnings("AAPL")

        assert result is None
        mock_finnhub.get_next_earnings_date.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_cached_days(self, earnings_table):
        """Cached earnings date should return days without API call."""
        future_date = date.today() + timedelta(days=7)
        earnings_table.put_item(Item={
            "ticker": "MSFT",
            "earnings_date": future_date.isoformat(),
            "earnings_time": "amc",
            "fetched_at": datetime.utcnow().isoformat(),
            "ttl": int((datetime.utcnow() + timedelta(days=8)).timestamp()),
        })
        service = EarningsCacheService(table_name="oss-test-earnings-cache")
        result = await service.get_days_to_earnings("MSFT")
        assert result == 7

    @pytest.mark.asyncio
    async def test_fetches_on_cache_miss(self, earnings_table, mock_finnhub):
        """Cache miss with Finnhub client should trigger a fetch."""
        future_date = date.today() + timedelta(days=3)
        mock_finnhub.get_next_earnings_date.return_value = (future_date, "bmo")

        service = EarningsCacheService(
            finnhub_client=mock_finnhub, table_name="oss-test-earnings-cache",
        )
        result = await service.get_days_to_earnings("TSLA")

        assert result == 3
        mock_finnhub.get_next_earnings_date.assert_called_once_with("TSLA")

    @pytest.mark.asyncio
    async def test_returns_none_without_client(self, earnings_table):
        """Cache miss without Finnhub client should return None."""
        service = EarningsCacheService(table_name="oss-test-earnings-cache")
        result = await service.get_days_to_earnings("UNKNOWN")
        assert result is None


class TestRefreshAll:
    """Tests for the bulk refresh_all method."""

    @pytest.mark.asyncio
    async def test_raises_without_client(self, earnings_table):
        """refresh_all should raise if no FinnhubClient configured."""
        service = EarningsCacheService(table_name="oss-test-earnings-cache")
        with pytest.raises(RuntimeError, match="requires a FinnhubClient"):
            await service.refresh_all()

    @pytest.mark.asyncio
    async def test_bulk_fetches_and_caches(self, earnings_table, mock_finnhub):
        """refresh_all should fetch all earnings and write to DynamoDB."""
        future_date = date.today() + timedelta(days=5)
        mock_finnhub.get_all_upcoming_earnings.return_value = [
            {"symbol": "AAPL", "date": future_date.isoformat(), "hour": "amc"},
            {"symbol": "MSFT", "date": future_date.isoformat(), "hour": "bmo"},
            {"symbol": "GOOG", "date": future_date.isoformat(), "hour": ""},
        ]

        service = EarningsCacheService(
            finnhub_client=mock_finnhub, table_name="oss-test-earnings-cache",
        )
        result = await service.refresh_all(lookforward_days=10)

        assert result["events_received"] == 3
        assert result["cached"] == 3
        assert result["errors"] == 0

        # Verify items were written to DynamoDB
        for ticker in ["AAPL", "MSFT", "GOOG"]:
            item = earnings_table.get_item(Key={"ticker": ticker}).get("Item")
            assert item is not None
            assert item["earnings_date"] == future_date.isoformat()

        # Verify AAPL has correct time
        aapl = earnings_table.get_item(Key={"ticker": "AAPL"}).get("Item")
        assert aapl["earnings_time"] == "amc"

        # Verify GOOG has "unknown" for empty hour
        goog = earnings_table.get_item(Key={"ticker": "GOOG"}).get("Item")
        assert goog["earnings_time"] == "unknown"

    @pytest.mark.asyncio
    async def test_skips_malformed_events(self, earnings_table, mock_finnhub):
        """refresh_all should skip events missing symbol or date."""
        mock_finnhub.get_all_upcoming_earnings.return_value = [
            {"symbol": "AAPL", "date": "2026-03-15", "hour": "amc"},
            {"symbol": None, "date": "2026-03-15"},  # missing symbol
            {"symbol": "MSFT"},  # missing date
            {"date": "2026-03-15"},  # missing symbol key
        ]

        service = EarningsCacheService(
            finnhub_client=mock_finnhub, table_name="oss-test-earnings-cache",
        )
        result = await service.refresh_all()

        assert result["cached"] == 1  # Only AAPL
        assert result["events_received"] == 4

    @pytest.mark.asyncio
    async def test_returns_summary(self, earnings_table, mock_finnhub):
        """refresh_all should return a summary dict."""
        mock_finnhub.get_all_upcoming_earnings.return_value = []

        service = EarningsCacheService(
            finnhub_client=mock_finnhub, table_name="oss-test-earnings-cache",
        )
        result = await service.refresh_all()

        assert "events_received" in result
        assert "cached" in result
        assert "errors" in result
        assert "date_range" in result


class TestPrefetchBatch:
    """Tests for prefetch_batch with sentinel fix."""

    @pytest.mark.asyncio
    async def test_cached_not_found_not_refetched(self, earnings_table, mock_finnhub):
        """Tickers cached as 'not found' should not be re-fetched in batch."""
        earnings_table.put_item(Item={
            "ticker": "AAPL",
            "earnings_date": None,
            "not_found": True,
            "fetched_at": datetime.utcnow().isoformat(),
            "ttl": int((datetime.utcnow() + timedelta(hours=24)).timestamp()),
        })

        service = EarningsCacheService(
            finnhub_client=mock_finnhub, table_name="oss-test-earnings-cache",
        )
        results = await service.prefetch_batch(["AAPL"])

        assert "AAPL" in results
        assert results["AAPL"] is None
        mock_finnhub.get_next_earnings_date.assert_not_called()
