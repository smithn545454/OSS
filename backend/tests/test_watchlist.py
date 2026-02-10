"""Tests for the watchlist module.

Covers WatchlistManager operations, validation, batching,
and the merge utility.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.schemas import PolicyConfig, WatchlistConfig
from app.core.watchlist import (
    DEFAULT_WATCHLIST,
    WatchlistManager,
    get_default_watchlist,
    merge_watchlists,
)


class TestWatchlistManager:
    """Test WatchlistManager configuration and operations."""

    def test_default_config_uses_default_watchlist(self):
        """When no tickers configured, falls back to default or S&P 500."""
        config = WatchlistConfig(tickers=[])
        manager = WatchlistManager(config=config)
        # With no DynamoDB, it falls back to DEFAULT_WATCHLIST
        tickers = manager.tickers
        assert len(tickers) > 0

    def test_explicit_tickers_override_defaults(self):
        config = WatchlistConfig(tickers=["AAPL", "MSFT", "GOOGL"])
        manager = WatchlistManager(config=config)
        assert manager.tickers == ["AAPL", "MSFT", "GOOGL"]

    def test_batch_size_property(self):
        config = WatchlistConfig(batch_size=50)
        manager = WatchlistManager(config=config)
        assert manager.batch_size == 50

    def test_max_concurrent_property(self):
        config = WatchlistConfig(max_concurrent_requests=25)
        manager = WatchlistManager(config=config)
        assert manager.max_concurrent == 25

    def test_get_batches_splits_correctly(self):
        config = WatchlistConfig(tickers=["A", "B", "C", "D", "E"], batch_size=2)
        manager = WatchlistManager(config=config)
        batches = manager.get_batches()
        assert len(batches) == 3
        assert batches[0] == ["A", "B"]
        assert batches[1] == ["C", "D"]
        assert batches[2] == ["E"]

    def test_get_batches_empty_tickers(self):
        config = WatchlistConfig(tickers=["AAPL"], batch_size=100)
        manager = WatchlistManager(config=config)
        batches = manager.get_batches()
        assert len(batches) == 1

    def test_validate_tickers_valid(self):
        config = WatchlistConfig(tickers=["AAPL", "MSFT", "SPY"])
        manager = WatchlistManager(config=config)
        valid, invalid = manager.validate_tickers()
        assert valid == ["AAPL", "MSFT", "SPY"]
        assert invalid == []

    def test_validate_tickers_invalid(self):
        config = WatchlistConfig(tickers=["AAPL", "123", "too_long_ticker", "msft"])
        manager = WatchlistManager(config=config)
        valid, invalid = manager.validate_tickers()
        assert "AAPL" in valid
        assert "123" in invalid  # Digits
        assert "msft" in invalid  # Lowercase

    def test_from_policy(self):
        policy_config = PolicyConfig(
            watchlist=WatchlistConfig(tickers=["NVDA", "AMD"])
        )
        manager = WatchlistManager.from_policy(policy_config)
        assert manager.tickers == ["NVDA", "AMD"]


class TestDefaultWatchlist:
    """Test the default watchlist utilities."""

    def test_default_watchlist_has_entries(self):
        assert len(DEFAULT_WATCHLIST) > 50

    def test_default_watchlist_includes_key_tickers(self):
        assert "AAPL" in DEFAULT_WATCHLIST
        assert "MSFT" in DEFAULT_WATCHLIST
        assert "SPY" in DEFAULT_WATCHLIST
        assert "QQQ" in DEFAULT_WATCHLIST
        assert "NVDA" in DEFAULT_WATCHLIST

    def test_get_default_watchlist_returns_copy(self):
        wl1 = get_default_watchlist()
        wl2 = get_default_watchlist()
        assert wl1 == wl2
        # Modifying one should not affect the other
        wl1.append("TEST")
        assert "TEST" not in wl2


class TestMergeWatchlists:
    """Test the merge_watchlists utility."""

    def test_merge_removes_duplicates(self):
        result = merge_watchlists(
            ["AAPL", "MSFT"],
            ["MSFT", "GOOGL"],
            ["AAPL", "NVDA"],
        )
        assert result == ["AAPL", "MSFT", "GOOGL", "NVDA"]

    def test_merge_uppercases_tickers(self):
        result = merge_watchlists(["aapl"], ["Msft"])
        assert result == ["AAPL", "MSFT"]

    def test_merge_empty_lists(self):
        result = merge_watchlists([], [], [])
        assert result == []

    def test_merge_preserves_order(self):
        result = merge_watchlists(["C", "B", "A"])
        assert result == ["C", "B", "A"]
