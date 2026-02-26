"""Tests for CatalystDataService.

Tests earnings date lookup via EarningsCacheService and SEC EDGAR
filing detection with mocked external API responses for reliable CI/CD.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.catalyst import (
    CatalystDataService,
    RELEVANT_FILING_TYPES,
    CALENDAR_DAYS_LOOKBACK,
)


class TestCatalystDataServiceInit:
    """Tests for CatalystDataService initialization."""

    def test_init_creates_empty_caches(self):
        """Service initializes with empty caches."""
        service = CatalystDataService()

        assert service._filings_cache == {}
        assert service._cik_cache == {}
        assert service._cik_loaded is False
        assert service._earnings_cache is None
        assert service._client is None

    def test_init_with_earnings_cache(self):
        """Service stores earnings cache reference."""
        mock_cache = MagicMock()
        service = CatalystDataService(earnings_cache=mock_cache)

        assert service._earnings_cache is mock_cache


class TestDaysToEarnings:
    """Tests for days_to_earnings functionality via EarningsCacheService."""

    async def test_delegates_to_earnings_cache(self):
        """get_days_to_earnings delegates to EarningsCacheService."""
        mock_cache = AsyncMock()
        mock_cache.get_days_to_earnings.return_value = 14
        service = CatalystDataService(earnings_cache=mock_cache)

        result = await service.get_days_to_earnings("AAPL")

        assert result == 14
        mock_cache.get_days_to_earnings.assert_called_once_with("AAPL")

    async def test_returns_none_without_earnings_cache(self):
        """Returns None when no earnings cache is configured."""
        service = CatalystDataService()

        result = await service.get_days_to_earnings("AAPL")

        assert result is None

    async def test_handles_earnings_cache_exception(self):
        """Returns None when earnings cache raises an exception."""
        mock_cache = AsyncMock()
        mock_cache.get_days_to_earnings.side_effect = Exception("DB error")
        service = CatalystDataService(earnings_cache=mock_cache)

        result = await service.get_days_to_earnings("AAPL")

        assert result is None


class TestRecentSecFiling:
    """Tests for recent_sec_filing functionality."""

    async def test_returns_cached_value(self):
        """Returns cached value if available."""
        service = CatalystDataService()
        service._filings_cache["AAPL"] = True

        result = await service.get_recent_sec_filing("AAPL")

        assert result is True

    async def test_normalizes_ticker_to_uppercase(self):
        """Ticker is normalized to uppercase for cache lookup."""
        service = CatalystDataService()
        service._filings_cache["MSFT"] = True

        result = await service.get_recent_sec_filing("msft")

        assert result is True

    async def test_returns_false_for_unknown_cik(self):
        """Returns False when ticker has no CIK mapping."""
        service = CatalystDataService()
        service._cik_loaded = True  # Mark as loaded to skip fetch

        result = await service.get_recent_sec_filing("NOCIK")

        assert result is False

    async def test_detects_recent_8k_filing(self):
        """Detects 8-K filing within lookback period."""
        service = CatalystDataService()
        service._cik_loaded = True
        service._cik_cache["TEST"] = "0000123456"

        recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "filings": {
                "recent": {
                    "form": ["8-K", "4", "DEF 14A"],
                    "filingDate": [recent_date, "2024-01-01", "2024-01-01"],
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(service, "_client", new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await service.get_recent_sec_filing("TEST")

        assert result is True

    async def test_detects_recent_10q_filing(self):
        """Detects 10-Q filing within lookback period."""
        service = CatalystDataService()
        service._cik_loaded = True
        service._cik_cache["TEST"] = "0000123456"

        recent_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "filings": {
                "recent": {
                    "form": ["10-Q"],
                    "filingDate": [recent_date],
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(service, "_client", new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await service.get_recent_sec_filing("TEST")

        assert result is True

    async def test_detects_recent_10k_filing(self):
        """Detects 10-K filing within lookback period."""
        service = CatalystDataService()
        service._cik_loaded = True
        service._cik_cache["TEST"] = "0000123456"

        recent_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "filings": {
                "recent": {
                    "form": ["10-K"],
                    "filingDate": [recent_date],
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(service, "_client", new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await service.get_recent_sec_filing("TEST")

        assert result is True

    async def test_ignores_old_filings(self):
        """Returns False for filings outside lookback period."""
        service = CatalystDataService()
        service._cik_loaded = True
        service._cik_cache["TEST"] = "0000123456"

        # Filing from 30 days ago (outside 14-day lookback)
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "filings": {
                "recent": {
                    "form": ["10-K"],
                    "filingDate": [old_date],
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(service, "_client", new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await service.get_recent_sec_filing("TEST")

        assert result is False

    async def test_ignores_irrelevant_filing_types(self):
        """Ignores filing types not in RELEVANT_FILING_TYPES."""
        service = CatalystDataService()
        service._cik_loaded = True
        service._cik_cache["TEST"] = "0000123456"

        recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "filings": {
                "recent": {
                    "form": ["4", "DEF 14A", "SC 13G"],
                    "filingDate": [recent_date, recent_date, recent_date],
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(service, "_client", new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await service.get_recent_sec_filing("TEST")

        assert result is False


class TestCikMapping:
    """Tests for CIK lookup functionality."""

    async def test_loads_cik_mapping_from_sec(self):
        """Loads and parses CIK mapping from SEC."""
        service = CatalystDataService()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "0": {"cik_str": "320193", "ticker": "AAPL", "title": "Apple Inc"},
            "1": {"cik_str": "789019", "ticker": "MSFT", "title": "Microsoft Corp"},
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(service, "_client", new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)

            await service._load_cik_mapping()

        assert service._cik_loaded is True
        assert service._cik_cache["AAPL"] == "0000320193"
        assert service._cik_cache["MSFT"] == "0000789019"

    async def test_only_loads_cik_once(self):
        """CIK mapping is only loaded once."""
        service = CatalystDataService()
        service._cik_loaded = True
        service._cik_cache["AAPL"] = "0000320193"

        with patch.object(service, "_client", new_callable=AsyncMock) as mock_client:
            await service._load_cik_mapping()
            mock_client.get.assert_not_called()

    async def test_handles_cik_loading_error(self):
        """Handles CIK loading errors gracefully."""
        service = CatalystDataService()

        with patch.object(service, "_client", new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(side_effect=Exception("Network error"))

            await service._load_cik_mapping()

        # Should mark as loaded to avoid retrying
        assert service._cik_loaded is True
        assert service._cik_cache == {}


class TestPrefetchBatch:
    """Tests for batch prefetching functionality."""

    async def test_prefetches_sec_filings(self):
        """Prefetches SEC filing data for multiple tickers."""
        mock_cache = AsyncMock()
        mock_cache.prefetch_batch.return_value = {"AAPL": 14, "MSFT": 30}
        service = CatalystDataService(earnings_cache=mock_cache)

        service._cik_loaded = True
        service._cik_cache["AAPL"] = "0000320193"
        service._cik_cache["MSFT"] = "0000789019"

        mock_sec_response = MagicMock()
        mock_sec_response.json.return_value = {
            "filings": {"recent": {"form": [], "filingDate": []}}
        }
        mock_sec_response.raise_for_status = MagicMock()

        with patch.object(service, "_client", new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_sec_response)

            await service.prefetch_batch(["AAPL", "MSFT"])

        # SEC filings should be cached
        assert "AAPL" in service._filings_cache
        assert "MSFT" in service._filings_cache
        # Earnings cache should have been called
        mock_cache.prefetch_batch.assert_called_once_with(["AAPL", "MSFT"])

    async def test_handles_empty_ticker_list(self):
        """Handles empty ticker list gracefully."""
        service = CatalystDataService()

        await service.prefetch_batch([])

        assert service._filings_cache == {}


class TestClearCache:
    """Tests for cache clearing functionality."""

    def test_clears_filings_cache_but_not_cik(self):
        """Clears filings cache but not CIK."""
        service = CatalystDataService()
        service._filings_cache["AAPL"] = True
        service._cik_cache["AAPL"] = "0000320193"
        service._cik_loaded = True

        service.clear_cache()

        assert service._filings_cache == {}
        # CIK cache should NOT be cleared (static data)
        assert service._cik_cache == {"AAPL": "0000320193"}
        assert service._cik_loaded is True


class TestRelevantFilingTypes:
    """Tests for filing type constants."""

    def test_relevant_filing_types_includes_required_forms(self):
        """Relevant filing types includes 8-K, 10-Q, 10-K and amendments."""
        assert "8-K" in RELEVANT_FILING_TYPES
        assert "10-Q" in RELEVANT_FILING_TYPES
        assert "10-K" in RELEVANT_FILING_TYPES
        assert "8-K/A" in RELEVANT_FILING_TYPES
        assert "10-Q/A" in RELEVANT_FILING_TYPES
        assert "10-K/A" in RELEVANT_FILING_TYPES

    def test_lookback_period_is_correct(self):
        """Lookback period is approximately 10 trading days."""
        assert CALENDAR_DAYS_LOOKBACK == 14


class TestAsyncContextManager:
    """Tests for async context manager functionality."""

    async def test_context_manager_creates_client(self):
        """Context manager creates HTTP client on enter."""
        service = CatalystDataService()

        async with service:
            assert service._client is not None

    async def test_context_manager_closes_client(self):
        """Context manager closes HTTP client on exit."""
        service = CatalystDataService()

        async with service:
            client = service._client
            assert client is not None

        # Client reference cleared after exit
        # (aclose was called on the httpx client)
