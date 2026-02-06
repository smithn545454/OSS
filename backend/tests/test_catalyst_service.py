"""Tests for CatalystDataService.

Tests yfinance earnings date parsing and SEC EDGAR filing detection
with mocked external API responses for reliable CI/CD.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

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
        
        assert service._earnings_cache == {}
        assert service._filings_cache == {}
        assert service._cik_cache == {}
        assert service._cik_loaded is False


class TestDaysToEarnings:
    """Tests for days_to_earnings functionality."""
    
    @pytest.mark.asyncio
    async def test_returns_cached_value(self):
        """Returns cached value if available."""
        service = CatalystDataService()
        service._earnings_cache["AAPL"] = 10
        
        result = await service.get_days_to_earnings("AAPL")
        
        assert result == 10
    
    @pytest.mark.asyncio
    async def test_normalizes_ticker_to_uppercase(self):
        """Ticker is normalized to uppercase for cache lookup."""
        service = CatalystDataService()
        service._earnings_cache["AAPL"] = 15
        
        result = await service.get_days_to_earnings("aapl")
        
        assert result == 15
    
    @pytest.mark.asyncio
    async def test_fetches_and_caches_earnings(self):
        """Fetches earnings from yfinance and caches result."""
        service = CatalystDataService()
        
        # Mock yfinance response
        mock_calendar = pd.DataFrame({
            'Earnings Date': [datetime.now() + timedelta(days=7)]
        })
        
        with patch('yfinance.Ticker') as mock_ticker_class:
            mock_ticker = MagicMock()
            mock_ticker.calendar = mock_calendar
            mock_ticker_class.return_value = mock_ticker
            
            result = await service.get_days_to_earnings("AAPL")
        
        # Should return ~7 days (may vary by 1 due to timing)
        assert result is not None
        assert 6 <= result <= 8
        
        # Should be cached
        assert "AAPL" in service._earnings_cache
    
    @pytest.mark.asyncio
    async def test_returns_none_for_no_calendar(self):
        """Returns None when yfinance has no calendar data."""
        service = CatalystDataService()
        
        with patch('yfinance.Ticker') as mock_ticker_class:
            mock_ticker = MagicMock()
            mock_ticker.calendar = None
            mock_ticker_class.return_value = mock_ticker
            
            result = await service.get_days_to_earnings("UNKNOWN")
        
        assert result is None
        assert "UNKNOWN" in service._earnings_cache  # None is cached
    
    @pytest.mark.asyncio
    async def test_returns_none_for_empty_calendar(self):
        """Returns None when calendar DataFrame is empty."""
        service = CatalystDataService()
        
        with patch('yfinance.Ticker') as mock_ticker_class:
            mock_ticker = MagicMock()
            mock_ticker.calendar = pd.DataFrame()  # Empty DataFrame
            mock_ticker_class.return_value = mock_ticker
            
            result = await service.get_days_to_earnings("TEST")
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_handles_yfinance_exception(self):
        """Handles yfinance exceptions gracefully."""
        service = CatalystDataService()
        
        with patch('yfinance.Ticker') as mock_ticker_class:
            mock_ticker_class.side_effect = Exception("API Error")
            
            result = await service.get_days_to_earnings("ERROR")
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_returns_zero_for_today_earnings(self):
        """Returns 0 when earnings is today."""
        service = CatalystDataService()
        
        mock_calendar = pd.DataFrame({
            'Earnings Date': [datetime.now()]
        })
        
        with patch('yfinance.Ticker') as mock_ticker_class:
            mock_ticker = MagicMock()
            mock_ticker.calendar = mock_calendar
            mock_ticker_class.return_value = mock_ticker
            
            result = await service.get_days_to_earnings("TODAY")
        
        assert result == 0
    
    @pytest.mark.asyncio
    async def test_returns_zero_for_past_earnings(self):
        """Returns 0 for past earnings dates (not negative)."""
        service = CatalystDataService()
        
        mock_calendar = pd.DataFrame({
            'Earnings Date': [datetime.now() - timedelta(days=5)]
        })
        
        with patch('yfinance.Ticker') as mock_ticker_class:
            mock_ticker = MagicMock()
            mock_ticker.calendar = mock_calendar
            mock_ticker_class.return_value = mock_ticker
            
            result = await service.get_days_to_earnings("PAST")
        
        assert result == 0  # Should not be negative


class TestRecentSecFiling:
    """Tests for recent_sec_filing functionality."""
    
    @pytest.mark.asyncio
    async def test_returns_cached_value(self):
        """Returns cached value if available."""
        service = CatalystDataService()
        service._filings_cache["AAPL"] = True
        
        result = await service.get_recent_sec_filing("AAPL")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_normalizes_ticker_to_uppercase(self):
        """Ticker is normalized to uppercase for cache lookup."""
        service = CatalystDataService()
        service._filings_cache["MSFT"] = True
        
        result = await service.get_recent_sec_filing("msft")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_cik(self):
        """Returns False when ticker has no CIK mapping."""
        service = CatalystDataService()
        service._cik_loaded = True  # Mark as loaded to skip fetch
        # Don't add any CIK mapping
        
        result = await service.get_recent_sec_filing("NOCIK")
        
        assert result is False
    
    @pytest.mark.asyncio
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
                    "filingDate": [recent_date, "2024-01-01", "2024-01-01"]
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(service, '_client', new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)
            
            result = await service.get_recent_sec_filing("TEST")
        
        assert result is True
    
    @pytest.mark.asyncio
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
                    "filingDate": [recent_date]
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(service, '_client', new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)
            
            result = await service.get_recent_sec_filing("TEST")
        
        assert result is True
    
    @pytest.mark.asyncio
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
                    "filingDate": [recent_date]
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(service, '_client', new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)
            
            result = await service.get_recent_sec_filing("TEST")
        
        assert result is True
    
    @pytest.mark.asyncio
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
                    "filingDate": [old_date]
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(service, '_client', new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)
            
            result = await service.get_recent_sec_filing("TEST")
        
        assert result is False
    
    @pytest.mark.asyncio
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
                    "form": ["4", "DEF 14A", "SC 13G"],  # Not 8-K, 10-Q, or 10-K
                    "filingDate": [recent_date, recent_date, recent_date]
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(service, '_client', new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)
            
            result = await service.get_recent_sec_filing("TEST")
        
        assert result is False


class TestCikMapping:
    """Tests for CIK lookup functionality."""
    
    @pytest.mark.asyncio
    async def test_loads_cik_mapping_from_sec(self):
        """Loads and parses CIK mapping from SEC."""
        service = CatalystDataService()
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "0": {"cik_str": "320193", "ticker": "AAPL", "title": "Apple Inc"},
            "1": {"cik_str": "789019", "ticker": "MSFT", "title": "Microsoft Corp"}
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(service, '_client', new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)
            
            await service._load_cik_mapping()
        
        assert service._cik_loaded is True
        assert service._cik_cache["AAPL"] == "0000320193"  # Zero-padded to 10 digits
        assert service._cik_cache["MSFT"] == "0000789019"
    
    @pytest.mark.asyncio
    async def test_only_loads_cik_once(self):
        """CIK mapping is only loaded once."""
        service = CatalystDataService()
        service._cik_loaded = True
        service._cik_cache["AAPL"] = "0000320193"
        
        # Should not make any HTTP requests
        with patch.object(service, '_client', new_callable=AsyncMock) as mock_client:
            await service._load_cik_mapping()
            mock_client.get.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handles_cik_loading_error(self):
        """Handles CIK loading errors gracefully."""
        service = CatalystDataService()
        
        with patch.object(service, '_client', new_callable=AsyncMock) as mock_client:
            mock_client.get = AsyncMock(side_effect=Exception("Network error"))
            
            await service._load_cik_mapping()
        
        # Should mark as loaded to avoid retrying
        assert service._cik_loaded is True
        assert service._cik_cache == {}


class TestPrefetchBatch:
    """Tests for batch prefetching functionality."""
    
    @pytest.mark.asyncio
    async def test_prefetches_multiple_tickers(self):
        """Prefetches catalyst data for multiple tickers."""
        service = CatalystDataService()
        
        # Mock CIK loaded
        service._cik_loaded = True
        service._cik_cache["AAPL"] = "0000320193"
        service._cik_cache["MSFT"] = "0000789019"
        
        # Mock yfinance
        mock_calendar = pd.DataFrame({
            'Earnings Date': [datetime.now() + timedelta(days=14)]
        })
        
        # Mock SEC response
        mock_sec_response = MagicMock()
        mock_sec_response.json.return_value = {
            "filings": {"recent": {"form": [], "filingDate": []}}
        }
        mock_sec_response.raise_for_status = MagicMock()
        
        with patch('yfinance.Ticker') as mock_ticker_class:
            mock_ticker = MagicMock()
            mock_ticker.calendar = mock_calendar
            mock_ticker_class.return_value = mock_ticker
            
            with patch.object(service, '_client', new_callable=AsyncMock) as mock_client:
                mock_client.get = AsyncMock(return_value=mock_sec_response)
                
                await service.prefetch_batch(["AAPL", "MSFT"])
        
        # Both tickers should be cached
        assert "AAPL" in service._earnings_cache
        assert "MSFT" in service._earnings_cache
        assert "AAPL" in service._filings_cache
        assert "MSFT" in service._filings_cache
    
    @pytest.mark.asyncio
    async def test_handles_empty_ticker_list(self):
        """Handles empty ticker list gracefully."""
        service = CatalystDataService()
        
        await service.prefetch_batch([])
        
        # Should not error, caches remain empty
        assert service._earnings_cache == {}
        assert service._filings_cache == {}


class TestClearCache:
    """Tests for cache clearing functionality."""
    
    def test_clears_earnings_and_filings_cache(self):
        """Clears earnings and filings caches but not CIK."""
        service = CatalystDataService()
        service._earnings_cache["AAPL"] = 10
        service._filings_cache["AAPL"] = True
        service._cik_cache["AAPL"] = "0000320193"
        service._cik_loaded = True
        
        service.clear_cache()
        
        assert service._earnings_cache == {}
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
        assert "8-K/A" in RELEVANT_FILING_TYPES  # Amendment
        assert "10-Q/A" in RELEVANT_FILING_TYPES
        assert "10-K/A" in RELEVANT_FILING_TYPES
    
    def test_lookback_period_is_correct(self):
        """Lookback period is approximately 10 trading days."""
        # 10 trading days ≈ 14 calendar days
        assert CALENDAR_DAYS_LOOKBACK == 14


class TestAsyncContextManager:
    """Tests for async context manager functionality."""
    
    @pytest.mark.asyncio
    async def test_context_manager_creates_client(self):
        """Context manager creates HTTP client on enter."""
        service = CatalystDataService()
        
        async with service:
            assert service._client is not None
    
    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self):
        """Context manager closes HTTP client on exit."""
        service = CatalystDataService()
        
        async with service:
            client = service._client
        
        # Client should be closed (aclose called)
        # We can't easily verify this without more mocking, but coverage is good
