"""DynamoDB operations for backtest pending trades.

Phase 1 workers write pending trades here after evaluation (stages 1-7).
Phase 2 workers read from here to resolve exits.

Table: oss-{env}-backtest-pending-trades
  PK: RUN#{run_id}
  SK: TRADE#{trade_id}
  GSI1PK: TICKER#{underlying_ticker}
  GSI1SK: RUN#{run_id}#{entry_date}
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from app.db.dynamodb import get_dynamodb

logger = logging.getLogger(__name__)


def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    """Remove DynamoDB key attributes from a response item."""
    skip = {"PK", "SK", "GSI1PK", "GSI1SK"}
    return {k: v for k, v in item.items() if k not in skip}


class BacktestPendingTradeTable:
    """Operations on the backtest-pending-trades DynamoDB table."""

    TABLE_SUFFIX = "backtest-pending-trades"

    @staticmethod
    async def put_batch(trades: list[dict[str, Any]]) -> None:
        """Batch write multiple pending trades."""
        db = get_dynamodb()
        items = []
        for trade in trades:
            run_id = trade["run_id"]
            trade_id = trade.get("trade_id", str(uuid.uuid4()))
            ticker = trade.get("underlying_ticker", "")
            entry_date = trade.get("entry_date", "")

            item = {
                "PK": f"RUN#{run_id}",
                "SK": f"TRADE#{trade_id}",
                "GSI1PK": f"TICKER#{ticker}",
                "GSI1SK": f"RUN#{run_id}#{entry_date}",
                **trade,
            }
            items.append(item)

        if items:
            await db.batch_write(BacktestPendingTradeTable.TABLE_SUFFIX, items)

    @staticmethod
    async def list_by_run(run_id: str, limit: int = 10000) -> list[dict[str, Any]]:
        """List all pending trades for a run."""
        db = get_dynamodb()
        items = await db.query(
            BacktestPendingTradeTable.TABLE_SUFFIX,
            pk=f"RUN#{run_id}",
            sk_prefix="TRADE#",
            limit=limit,
        )
        return [_strip_keys(i) for i in items]

    @staticmethod
    async def list_by_ticker(
        ticker: str,
        run_id: Optional[str] = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """List pending trades for a ticker, optionally scoped to a run.

        Uses GSI1 for efficient ticker-based lookups (Phase 2 pattern).
        """
        db = get_dynamodb()
        sk_prefix = f"RUN#{run_id}#" if run_id else None
        items = await db.query(
            BacktestPendingTradeTable.TABLE_SUFFIX,
            pk=f"TICKER#{ticker}",
            sk_prefix=sk_prefix,
            limit=limit,
            index_name="GSI1",
        )
        return [_strip_keys(i) for i in items]

    @staticmethod
    async def list_by_tickers(
        tickers: list[str],
        run_id: str,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """List pending trades for multiple tickers in a run.

        Queries GSI1 per ticker and merges results. Used by Phase 2 resolvers.
        """
        all_trades: list[dict[str, Any]] = []
        for ticker in tickers:
            trades = await BacktestPendingTradeTable.list_by_ticker(
                ticker, run_id=run_id, limit=limit,
            )
            all_trades.extend(trades)
        return all_trades

    @staticmethod
    async def count_by_run(run_id: str) -> int:
        """Count pending trades for a run."""
        db = get_dynamodb()
        table = db.get_table(BacktestPendingTradeTable.TABLE_SUFFIX)
        from boto3.dynamodb.conditions import Key

        response = table.query(
            KeyConditionExpression=(
                Key("PK").eq(f"RUN#{run_id}") & Key("SK").begins_with("TRADE#")
            ),
            Select="COUNT",
        )
        return response.get("Count", 0)

    @staticmethod
    async def delete_by_run(run_id: str) -> int:
        """Delete all pending trades for a run (cleanup after Phase 2)."""
        db = get_dynamodb()
        trades = await BacktestPendingTradeTable.list_by_run(run_id, limit=10000)
        for trade in trades:
            trade_id = trade.get("trade_id", "")
            await db.delete_item(
                BacktestPendingTradeTable.TABLE_SUFFIX,
                f"RUN#{run_id}",
                f"TRADE#{trade_id}",
            )
        return len(trades)
