"""Rate limiter for LLM API calls.

Per Section 21.4: Max 50 LLM calls per day.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.schemas import LLMUsage

logger = logging.getLogger(__name__)


class RateLimiter:
    """Daily rate limiter for LLM API calls.

    Tracks daily usage and enforces the max 50 calls/day limit.
    Usage is stored in DynamoDB for persistence across Lambda invocations.
    """

    def __init__(
        self,
        max_daily_calls: int = 50,
        usage_table: Optional["LLMUsageTable"] = None,
    ) -> None:
        """Initialize rate limiter.

        Args:
            max_daily_calls: Maximum LLM calls per day (default: 50)
            usage_table: DynamoDB table for persistence (optional)
        """
        self._max_daily_calls = max_daily_calls
        self._usage_table = usage_table
        self._in_memory_usage: dict[str, LLMUsage] = {}

    def _get_today(self) -> str:
        """Get today's date string in YYYY-MM-DD format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def _get_usage(self, date: str) -> LLMUsage:
        """Get usage for a specific date.

        Args:
            date: Date string in YYYY-MM-DD format

        Returns:
            LLMUsage record for the date
        """
        # Try DynamoDB first if available
        if self._usage_table:
            try:
                usage = await self._usage_table.get(date)
                if usage:
                    return usage
            except Exception as e:
                logger.warning(f"Failed to get usage from DynamoDB: {e}")

        # Fall back to in-memory
        if date in self._in_memory_usage:
            return self._in_memory_usage[date]

        # Create new usage record
        return LLMUsage(date=date, calls_made=0, tokens_used=0)

    async def _save_usage(self, usage: LLMUsage) -> None:
        """Save usage record.

        Args:
            usage: LLMUsage record to save
        """
        # Update in-memory
        self._in_memory_usage[usage.date] = usage

        # Try DynamoDB if available
        if self._usage_table:
            try:
                await self._usage_table.put(usage)
            except Exception as e:
                logger.warning(f"Failed to save usage to DynamoDB: {e}")

    async def can_make_call(self) -> bool:
        """Check if we can make another LLM call today.

        Returns:
            True if under the daily limit
        """
        today = self._get_today()
        usage = await self._get_usage(today)
        return usage.calls_made < self._max_daily_calls

    async def get_remaining_calls(self) -> int:
        """Get remaining calls for today.

        Returns:
            Number of remaining calls
        """
        today = self._get_today()
        usage = await self._get_usage(today)
        return max(0, self._max_daily_calls - usage.calls_made)

    async def record_call(self, tokens_used: int = 0) -> bool:
        """Record an LLM call.

        Args:
            tokens_used: Number of tokens used in the call

        Returns:
            True if call was recorded, False if limit exceeded
        """
        today = self._get_today()
        usage = await self._get_usage(today)

        if usage.calls_made >= self._max_daily_calls:
            logger.warning(f"Daily LLM limit reached: {usage.calls_made}/{self._max_daily_calls}")
            return False

        # Create updated usage (LLMUsage is frozen)
        updated = LLMUsage(
            date=today,
            calls_made=usage.calls_made + 1,
            tokens_used=usage.tokens_used + tokens_used,
        )

        await self._save_usage(updated)
        logger.info(
            f"LLM call recorded: {updated.calls_made}/{self._max_daily_calls} "
            f"(+{tokens_used} tokens)"
        )
        return True

    async def get_usage_stats(self) -> dict:
        """Get current usage statistics.

        Returns:
            Dict with calls_made, tokens_used, remaining, limit
        """
        today = self._get_today()
        usage = await self._get_usage(today)
        return {
            "date": today,
            "calls_made": usage.calls_made,
            "tokens_used": usage.tokens_used,
            "remaining": max(0, self._max_daily_calls - usage.calls_made),
            "limit": self._max_daily_calls,
        }
