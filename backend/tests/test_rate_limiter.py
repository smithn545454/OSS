"""Tests for LLM rate limiter."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.schemas import LLMUsage


class TestRateLimiterRecordCall:

    @pytest.mark.asyncio
    async def test_record_call_uses_conditional_increment(self):
        from app.llm.rate_limiter import RateLimiter

        mock_table = AsyncMock()
        mock_table.increment = AsyncMock(
            return_value=LLMUsage(date="2026-01-17", calls_made=6, tokens_used=600)
        )

        limiter = RateLimiter(max_daily_calls=50, usage_table=mock_table)
        with patch.object(limiter, "_get_today", return_value="2026-01-17"):
            result = await limiter.record_call(tokens_used=100)

        assert result is True
        mock_table.increment.assert_called_once_with(
            "2026-01-17", tokens=100, max_calls=50,
        )

    @pytest.mark.asyncio
    async def test_record_call_limit_exceeded_via_condition(self):
        from app.llm.rate_limiter import RateLimiter

        mock_table = AsyncMock()
        # increment returns None when ConditionalCheckFailedException fires
        mock_table.increment = AsyncMock(return_value=None)

        limiter = RateLimiter(max_daily_calls=50, usage_table=mock_table)
        with patch.object(limiter, "_get_today", return_value="2026-01-17"):
            result = await limiter.record_call(tokens_used=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_record_call_fallback_to_in_memory(self):
        from app.llm.rate_limiter import RateLimiter

        # No usage_table — falls back to in-memory
        limiter = RateLimiter(max_daily_calls=50, usage_table=None)
        with patch.object(limiter, "_get_today", return_value="2026-01-17"):
            result = await limiter.record_call(tokens_used=100)
        assert result is True

    @pytest.mark.asyncio
    async def test_record_call_in_memory_respects_limit(self):
        from app.llm.rate_limiter import RateLimiter

        limiter = RateLimiter(max_daily_calls=2, usage_table=None)
        with patch.object(limiter, "_get_today", return_value="2026-01-17"):
            assert await limiter.record_call(tokens_used=50) is True
            assert await limiter.record_call(tokens_used=50) is True
            assert await limiter.record_call(tokens_used=50) is False  # Limit hit
