"""Pre-aggregated metrics for paper trading using DynamoDB atomic counters.

Stores summary metrics in the paper-positions table using special PK patterns:
  PK=METRICS#GLOBAL   SK=SUMMARY         — total counts, P&L, win rate
  PK=METRICS#SCANNER  SK={scanner_type}   — per-scanner breakdown
  PK=METRICS#VERDICT  SK={verdict}        — per-verdict breakdown
  PK=METRICS#TIER     SK={tier}           — per-tier breakdown
  PK=METRICS#DAILY    SK={YYYY-MM-DD}     — daily equity point
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.schemas import PaperPosition
from app.db.dynamodb import get_dynamodb
from app.db.tables import PAPER_POSITIONS_TABLE

logger = logging.getLogger(__name__)


class MetricsAggregator:
    """Hooks into position lifecycle to maintain pre-aggregated counters."""

    TABLE = PAPER_POSITIONS_TABLE

    @staticmethod
    async def on_position_opened(position: PaperPosition) -> None:
        """Increment counters when a position is opened."""
        db = get_dynamodb()
        table = db.get_table(MetricsAggregator.TABLE)

        # Global summary — counts + optional score tracking
        update_expr = "ADD open_count :inc, total_count :inc SET last_updated = :now"
        expr_values: dict[str, Any] = {
            ":inc": 1,
            ":now": datetime.now(timezone.utc).isoformat(),
        }
        if position.conviction_score is not None:
            update_expr = (
                "ADD open_count :inc, total_count :inc, "
                "score_sum :score, score_count :sinc "
                "SET last_updated = :now"
            )
            expr_values[":score"] = _decimal_safe(position.conviction_score)
            expr_values[":sinc"] = 1
        table.update_item(
            Key={"PK": "METRICS#GLOBAL", "SK": "SUMMARY"},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )

        # Per-scanner
        scanner = position.scanner_source or "UNKNOWN"
        table.update_item(
            Key={"PK": "METRICS#SCANNER", "SK": scanner},
            UpdateExpression="ADD #cnt :inc",
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={":inc": 1},
        )

        # Per-verdict
        verdict = _enum_val(position.verdict_at_entry)
        table.update_item(
            Key={"PK": "METRICS#VERDICT", "SK": verdict},
            UpdateExpression="ADD #cnt :inc",
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={":inc": 1},
        )

        # Per-tier
        tier = _enum_val(position.quality_tier_at_entry) or "NONE"
        table.update_item(
            Key={"PK": "METRICS#TIER", "SK": tier},
            UpdateExpression="ADD #cnt :inc",
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={":inc": 1},
        )

        # Per-score-band
        if position.conviction_score is not None:
            band = _score_band_label(position.conviction_score)
            table.update_item(
                Key={"PK": "METRICS#SCOREBAND", "SK": band},
                UpdateExpression="ADD #cnt :inc",
                ExpressionAttributeNames={"#cnt": "count"},
                ExpressionAttributeValues={":inc": 1},
            )

    @staticmethod
    async def on_position_closed(position: PaperPosition) -> None:
        """Update counters when a position is closed."""
        db = get_dynamodb()
        table = db.get_table(MetricsAggregator.TABLE)

        pnl = position.current_pnl_pct
        is_win = 1 if pnl > 0 else 0
        is_loss = 1 if pnl < 0 else 0

        # Dollar P&L: (exit_or_current - entry) * quantity * 100 (option multiplier)
        exit_price = (
            position.exit_price if position.exit_price is not None else position.current_price
        )
        dollar_pnl = (exit_price - position.entry_price) * position.quantity * 100

        # Global summary
        table.update_item(
            Key={"PK": "METRICS#GLOBAL", "SK": "SUMMARY"},
            UpdateExpression=(
                "ADD open_count :dec, closed_count :inc, "
                "win_count :win, loss_count :loss, "
                "total_pnl :pnl, sum_returns :pnl, "
                "total_pnl_dollars :dollar_pnl "
                "SET last_updated = :now"
            ),
            ExpressionAttributeValues={
                ":dec": -1,
                ":inc": 1,
                ":win": is_win,
                ":loss": is_loss,
                ":pnl": _decimal_safe(pnl),
                ":dollar_pnl": _decimal_safe(dollar_pnl),
                ":now": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Track best trade (conditional update — only if this PnL exceeds current best)
        try:
            table.update_item(
                Key={"PK": "METRICS#GLOBAL", "SK": "SUMMARY"},
                UpdateExpression="SET best_trade_pnl = :pnl",
                ConditionExpression=(
                    "attribute_not_exists(best_trade_pnl) OR best_trade_pnl < :pnl"
                ),
                ExpressionAttributeValues={":pnl": _decimal_safe(pnl)},
            )
        except Exception:
            pass  # ConditionalCheckFailed — current best is higher

        # Per-scanner
        scanner = position.scanner_source or "UNKNOWN"
        table.update_item(
            Key={"PK": "METRICS#SCANNER", "SK": scanner},
            UpdateExpression=(
                "ADD closed_count :inc, win_count :win, "
                "loss_count :loss, total_pnl :pnl, "
                "total_pnl_dollars :dollar_pnl"
            ),
            ExpressionAttributeValues={
                ":inc": 1,
                ":win": is_win,
                ":loss": is_loss,
                ":pnl": _decimal_safe(pnl),
                ":dollar_pnl": _decimal_safe(dollar_pnl),
            },
        )

        # Per-verdict
        verdict = _enum_val(position.verdict_at_entry)
        table.update_item(
            Key={"PK": "METRICS#VERDICT", "SK": verdict},
            UpdateExpression=(
                "ADD closed_count :inc, win_count :win, "
                "loss_count :loss, total_pnl :pnl, "
                "total_pnl_dollars :dollar_pnl"
            ),
            ExpressionAttributeValues={
                ":inc": 1,
                ":win": is_win,
                ":loss": is_loss,
                ":pnl": _decimal_safe(pnl),
                ":dollar_pnl": _decimal_safe(dollar_pnl),
            },
        )

        # Per-tier
        tier = _enum_val(position.quality_tier_at_entry) or "NONE"
        table.update_item(
            Key={"PK": "METRICS#TIER", "SK": tier},
            UpdateExpression=(
                "ADD closed_count :inc, win_count :win, "
                "loss_count :loss, total_pnl :pnl, "
                "total_pnl_dollars :dollar_pnl"
            ),
            ExpressionAttributeValues={
                ":inc": 1,
                ":win": is_win,
                ":loss": is_loss,
                ":pnl": _decimal_safe(pnl),
                ":dollar_pnl": _decimal_safe(dollar_pnl),
            },
        )

        # Per-score-band
        if position.conviction_score is not None:
            band = _score_band_label(position.conviction_score)
            table.update_item(
                Key={"PK": "METRICS#SCOREBAND", "SK": band},
                UpdateExpression=(
                    "ADD closed_count :inc, win_count :win, "
                    "loss_count :loss, total_pnl_dollars :dollar_pnl"
                ),
                ExpressionAttributeValues={
                    ":inc": 1,
                    ":win": is_win,
                    ":loss": is_loss,
                    ":dollar_pnl": _decimal_safe(dollar_pnl),
                },
            )

    @staticmethod
    async def write_daily_equity(date: str, daily_pnl: float) -> None:
        """Write or update a daily equity point."""
        db = get_dynamodb()
        table = db.get_table(MetricsAggregator.TABLE)
        table.update_item(
            Key={"PK": "METRICS#DAILY", "SK": date},
            UpdateExpression="ADD daily_pnl :pnl SET updated_at = :now",
            ExpressionAttributeValues={
                ":pnl": _decimal_safe(daily_pnl),
                ":now": datetime.now(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    async def get_summary() -> dict[str, Any]:
        """Fetch global summary metrics. Single GetItem — <50ms."""
        db = get_dynamodb()
        item = await db.get_item(MetricsAggregator.TABLE, "METRICS#GLOBAL", "SUMMARY")
        if not item:
            return _empty_summary()
        item.pop("PK", None)
        item.pop("SK", None)
        return item

    @staticmethod
    async def get_scanner_metrics() -> list[dict[str, Any]]:
        """Fetch per-scanner breakdown."""
        db = get_dynamodb()
        items = await db.query(
            MetricsAggregator.TABLE, "METRICS#SCANNER",
            limit=20, scan_forward=True,
        )
        for item in items:
            item["scanner_type"] = item.get("SK", "UNKNOWN")
            item.pop("PK", None)
            item.pop("SK", None)
        return items

    @staticmethod
    async def get_verdict_metrics() -> list[dict[str, Any]]:
        """Fetch per-verdict breakdown."""
        db = get_dynamodb()
        items = await db.query(
            MetricsAggregator.TABLE, "METRICS#VERDICT",
            limit=10, scan_forward=True,
        )
        for item in items:
            item["verdict"] = item.get("SK", "UNKNOWN")
            item.pop("PK", None)
            item.pop("SK", None)
        return items

    @staticmethod
    async def get_tier_metrics() -> list[dict[str, Any]]:
        """Fetch per-tier breakdown."""
        db = get_dynamodb()
        items = await db.query(
            MetricsAggregator.TABLE, "METRICS#TIER",
            limit=10, scan_forward=True,
        )
        for item in items:
            item["tier"] = item.get("SK", "UNKNOWN")
            item.pop("PK", None)
            item.pop("SK", None)
        return items

    @staticmethod
    async def get_scoreband_metrics() -> list[dict[str, Any]]:
        """Fetch per-score-band breakdown."""
        db = get_dynamodb()
        items = await db.query(
            MetricsAggregator.TABLE, "METRICS#SCOREBAND",
            limit=20, scan_forward=True,
        )
        for item in items:
            item["band"] = item.get("SK", "UNKNOWN")
            item.pop("PK", None)
            item.pop("SK", None)
        return items

    @staticmethod
    async def get_daily_equity(days: int = 90) -> list[dict[str, Any]]:
        """Fetch daily equity points."""
        db = get_dynamodb()
        items = await db.query(
            MetricsAggregator.TABLE, "METRICS#DAILY",
            limit=days, scan_forward=False,
        )
        for item in items:
            item["date"] = item.get("SK", "")
            item.pop("PK", None)
            item.pop("SK", None)
        # Reverse to chronological order
        items.reverse()
        return items


def _score_band_label(score: float) -> str:
    """Map a conviction score to a display band label."""
    if score < 65:
        return "60-64"
    elif score < 70:
        return "65-69"
    elif score < 75:
        return "70-74"
    elif score < 80:
        return "75-79"
    elif score < 85:
        return "80-84"
    elif score < 90:
        return "85-89"
    else:
        return "90+"


def _enum_val(v: Any) -> str:
    """Extract .value from an enum, or return string as-is."""
    return str(v.value if hasattr(v, "value") else v)


def _decimal_safe(val: float) -> Any:
    """Convert float to Decimal for DynamoDB ADD expressions."""
    from decimal import Decimal
    return Decimal(str(val))


def _empty_summary() -> dict[str, Any]:
    """Return empty summary for when no metrics exist yet."""
    return {
        "open_count": 0,
        "closed_count": 0,
        "total_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "total_pnl": 0,
        "total_pnl_dollars": 0,
        "sum_returns": 0,
        "last_updated": None,
    }
