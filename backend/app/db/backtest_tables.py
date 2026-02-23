"""DynamoDB table operations for backtesting.

Tables:
- BacktestRuns:    PK=RUN#{run_id}  SK=META        GSI1: STATUS#{status} → created_at
- BacktestTrades:  PK=RUN#{run_id}  SK=TRADE#{id}  GSI1: SCANNER#{scanner} → date
                                                    GSI2: REGIME#{regime} → date
- BacktestInsights: PK=RUN#{run_id} SK=INSIGHT#{id}
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.dynamodb import get_dynamodb

logger = logging.getLogger(__name__)


def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    """Remove DynamoDB key attributes from a response item."""
    skip = {"PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"}
    return {k: v for k, v in item.items() if k not in skip}


class BacktestRunTable:
    """Operations on the backtest-runs DynamoDB table."""

    TABLE_SUFFIX = "backtest-runs"

    @staticmethod
    async def put(run: dict[str, Any]) -> None:
        """Create or overwrite a backtest run."""
        db = get_dynamodb()
        run_id = run["run_id"]
        status = run.get("status", "PENDING")

        item = {
            "PK": f"RUN#{run_id}",
            "SK": "META",
            "GSI1PK": f"STATUS#{status}",
            "GSI1SK": run.get("created_at", datetime.now(timezone.utc).isoformat()),
            **run,
        }
        await db.put_item(BacktestRunTable.TABLE_SUFFIX, item)

    @staticmethod
    async def get(run_id: str) -> Optional[dict[str, Any]]:
        """Get a backtest run by ID."""
        db = get_dynamodb()
        item = await db.get_item(BacktestRunTable.TABLE_SUFFIX, f"RUN#{run_id}", "META")
        if item:
            return _strip_keys(item)
        return None

    @staticmethod
    async def update_status(run_id: str, status: str, **extra: Any) -> None:
        """Update run status and optional extra fields."""
        db = get_dynamodb()
        updates = {"status": status, "GSI1PK": f"STATUS#{status}", **extra}
        if status in ("COMPLETED", "FAILED"):
            updates["completed_at"] = datetime.now(timezone.utc).isoformat()
        await db.update_item(BacktestRunTable.TABLE_SUFFIX, f"RUN#{run_id}", "META", updates)

    @staticmethod
    async def update_progress(
        run_id: str,
        progress: Optional[dict[str, Any]] = None,
        days_increment: int = 0,
        trades_increment: int = 0,
        summary: Optional[dict[str, Any]] = None,
    ) -> None:
        """Update run progress counters.

        If progress dict is provided, overwrites the entire progress field.
        If increments are provided, fetches current state and increments.
        """
        db = get_dynamodb()
        updates: dict[str, Any] = {}

        if progress is not None:
            updates["progress"] = progress
        elif days_increment or trades_increment:
            # Fetch current progress and increment
            current = await BacktestRunTable.get(run_id)
            if current:
                current_progress = current.get("progress", {})
                current_progress["days_completed"] = (
                    int(current_progress.get("days_completed", 0)) + days_increment
                )
                current_progress["trades_found"] = (
                    int(current_progress.get("trades_found", 0)) + trades_increment
                )
                updates["progress"] = current_progress

        if summary is not None:
            updates["summary"] = summary

        if updates:
            await db.update_item(
                BacktestRunTable.TABLE_SUFFIX, f"RUN#{run_id}", "META", updates
            )

    @staticmethod
    async def list_runs(
        status: Optional[str] = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List runs, optionally filtered by status."""
        db = get_dynamodb()
        if status:
            items = await db.query(
                BacktestRunTable.TABLE_SUFFIX,
                pk=f"STATUS#{status}",
                limit=limit,
                index_name="GSI1",
                scan_forward=False,
            )
        else:
            # Scan for all runs — acceptable for small table
            table = db.get_table(BacktestRunTable.TABLE_SUFFIX)
            response = table.scan(Limit=limit)
            raw = response.get("Items", [])
            items = [
                db.convert_from_dynamodb(i)
                if hasattr(db, "convert_from_dynamodb")
                else i
                for i in raw
            ]
        return [_strip_keys(i) for i in items]

    @staticmethod
    async def delete(run_id: str) -> None:
        """Delete a backtest run."""
        db = get_dynamodb()
        await db.delete_item(BacktestRunTable.TABLE_SUFFIX, f"RUN#{run_id}", "META")


class BacktestTradeTable:
    """Operations on the backtest-trades DynamoDB table."""

    TABLE_SUFFIX = "backtest-trades"

    @staticmethod
    async def put(trade: dict[str, Any]) -> None:
        """Create a backtest trade record."""
        db = get_dynamodb()
        run_id = trade["run_id"]
        trade_id = trade.get("trade_id", str(uuid.uuid4()))

        item = {
            "PK": f"RUN#{run_id}",
            "SK": f"TRADE#{trade_id}",
            **trade,
        }

        # Populate GSI keys if present
        scanner = trade.get("scanner_type")
        if scanner:
            item["GSI1PK"] = f"SCANNER#{scanner}"
            item["GSI1SK"] = trade.get("entry_date", "")

        regime = trade.get("market_regime")
        if regime:
            item["GSI2PK"] = f"REGIME#{regime}"
            item["GSI2SK"] = trade.get("entry_date", "")

        await db.put_item(BacktestTradeTable.TABLE_SUFFIX, item)

    @staticmethod
    async def put_batch(trades: list[dict[str, Any]]) -> None:
        """Batch write multiple trades."""
        db = get_dynamodb()
        items = []
        for trade in trades:
            run_id = trade["run_id"]
            trade_id = trade.get("trade_id", str(uuid.uuid4()))
            item = {
                "PK": f"RUN#{run_id}",
                "SK": f"TRADE#{trade_id}",
                **trade,
            }
            scanner = trade.get("scanner_type")
            if scanner:
                item["GSI1PK"] = f"SCANNER#{scanner}"
                item["GSI1SK"] = trade.get("entry_date", "")
            regime = trade.get("market_regime")
            if regime:
                item["GSI2PK"] = f"REGIME#{regime}"
                item["GSI2SK"] = trade.get("entry_date", "")
            items.append(item)

        await db.batch_write(BacktestTradeTable.TABLE_SUFFIX, items)

    @staticmethod
    async def list_by_run(run_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        """List all trades for a run."""
        db = get_dynamodb()
        items = await db.query(
            BacktestTradeTable.TABLE_SUFFIX,
            pk=f"RUN#{run_id}",
            sk_prefix="TRADE#",
            limit=limit,
        )
        return [_strip_keys(i) for i in items]

    @staticmethod
    async def list_by_scanner(
        scanner_type: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List trades by scanner type via GSI1."""
        db = get_dynamodb()
        items = await db.query(
            BacktestTradeTable.TABLE_SUFFIX,
            pk=f"SCANNER#{scanner_type}",
            limit=limit,
            index_name="GSI1",
            scan_forward=False,
        )
        return [_strip_keys(i) for i in items]

    @staticmethod
    async def list_by_regime(
        regime: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List trades by market regime via GSI2."""
        db = get_dynamodb()
        items = await db.query(
            BacktestTradeTable.TABLE_SUFFIX,
            pk=f"REGIME#{regime}",
            limit=limit,
            index_name="GSI2",
            scan_forward=False,
        )
        return [_strip_keys(i) for i in items]

    @staticmethod
    async def count_by_run(run_id: str) -> int:
        """Count trades for a run."""
        db = get_dynamodb()
        table = db.get_table(BacktestTradeTable.TABLE_SUFFIX)
        from boto3.dynamodb.conditions import Key

        response = table.query(
            KeyConditionExpression=Key("PK").eq(f"RUN#{run_id}") & Key("SK").begins_with("TRADE#"),
            Select="COUNT",
        )
        return response.get("Count", 0)

    @staticmethod
    async def delete_by_run(run_id: str) -> int:
        """Delete all trades for a run. Returns count of deleted items."""
        db = get_dynamodb()
        trades = await BacktestTradeTable.list_by_run(run_id, limit=5000)
        for trade in trades:
            trade_id = trade.get("trade_id", "")
            await db.delete_item(
                BacktestTradeTable.TABLE_SUFFIX,
                f"RUN#{run_id}",
                f"TRADE#{trade_id}",
            )
        return len(trades)


class BacktestInsightTable:
    """Operations on the backtest-insights DynamoDB table."""

    TABLE_SUFFIX = "backtest-insights"

    @staticmethod
    async def put(insight: dict[str, Any]) -> None:
        """Create a backtest insight."""
        db = get_dynamodb()
        run_id = insight["run_id"]
        insight_id = insight.get("insight_id", str(uuid.uuid4()))

        item = {
            "PK": f"RUN#{run_id}",
            "SK": f"INSIGHT#{insight_id}",
            **insight,
        }
        await db.put_item(BacktestInsightTable.TABLE_SUFFIX, item)

    @staticmethod
    async def list_by_run(run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """List all insights for a run."""
        db = get_dynamodb()
        items = await db.query(
            BacktestInsightTable.TABLE_SUFFIX,
            pk=f"RUN#{run_id}",
            sk_prefix="INSIGHT#",
            limit=limit,
        )
        return [_strip_keys(i) for i in items]

    @staticmethod
    async def delete_by_run(run_id: str) -> int:
        """Delete all insights for a run."""
        db = get_dynamodb()
        insights = await BacktestInsightTable.list_by_run(run_id, limit=1000)
        for insight in insights:
            insight_id = insight.get("insight_id", "")
            await db.delete_item(
                BacktestInsightTable.TABLE_SUFFIX, f"RUN#{run_id}", f"INSIGHT#{insight_id}"
            )
        return len(insights)
