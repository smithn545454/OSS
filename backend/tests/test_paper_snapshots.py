"""Tests for PaperSnapshotTable and snapshot integration.

Covers:
- put_snapshot and list_by_position
- list_by_position_range date filtering
- snapshot written during position update from chain
- Lambda handler routing for paper_update action
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import PaperPosition, PositionStatus, Verdict
from app.db.dynamodb import DynamoDBClient
from app.db.tables import PaperSnapshotTable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_open_position(
    pos_id: str = "pos-snap-001",
    option_ticker: str = "O:AAPL260320C00185000",
    entry_price: float = 5.0,
) -> PaperPosition:
    return PaperPosition(
        position_id=pos_id,
        evaluation_id=f"eval-{pos_id}",
        option_ticker=option_ticker,
        entry_price=entry_price,
        entry_date="2026-01-15",
        verdict_at_entry=Verdict.APPROVE,
        current_price=entry_price,
        current_pnl_pct=0.0,
        days_held=3,
        status=PositionStatus.OPEN,
    )


# ---------------------------------------------------------------------------
# 1. Put and list snapshots
# ---------------------------------------------------------------------------


class TestPutAndListSnapshots:

    @pytest.mark.asyncio
    async def test_put_and_list_snapshots(self, moto_dynamodb):
        """Write multiple snapshots for a position, verify sorted return."""
        DynamoDBClient._instance = None
        with patch("app.config.get_settings") as mock_settings:
            s = mock_settings.return_value
            s.dynamodb_endpoint = None
            s.aws_region = "us-east-1"
            s.dynamodb_table_prefix = "oss-test"
            DynamoDBClient._instance = DynamoDBClient()

            pos_id = "pos-list-001"

            # Write snapshots in non-chronological order
            await PaperSnapshotTable.put_snapshot(
                pos_id, "2026-01-17", {"price": 5.2, "delta": 0.55}
            )
            await PaperSnapshotTable.put_snapshot(
                pos_id, "2026-01-15", {"price": 5.0, "delta": 0.54}
            )
            await PaperSnapshotTable.put_snapshot(
                pos_id, "2026-01-16", {"price": 5.1, "delta": 0.545}
            )

            # list_by_position returns most recent first (scan_forward=False)
            snapshots = await PaperSnapshotTable.list_by_position(pos_id)

            assert len(snapshots) == 3
            # Most recent date first
            assert snapshots[0]["snapshot_date"] == "2026-01-17"
            assert snapshots[1]["snapshot_date"] == "2026-01-16"
            assert snapshots[2]["snapshot_date"] == "2026-01-15"

            # Verify data fields persisted
            assert snapshots[0]["price"] == 5.2
            assert snapshots[2]["delta"] == 0.54

        DynamoDBClient._instance = None


# ---------------------------------------------------------------------------
# 2. list_by_position_range
# ---------------------------------------------------------------------------


class TestListByPositionRange:

    @pytest.mark.asyncio
    async def test_list_by_position_range(self, moto_dynamodb):
        """Write snapshots across dates, verify range filtering."""
        DynamoDBClient._instance = None
        with patch("app.config.get_settings") as mock_settings:
            s = mock_settings.return_value
            s.dynamodb_endpoint = None
            s.aws_region = "us-east-1"
            s.dynamodb_table_prefix = "oss-test"
            DynamoDBClient._instance = DynamoDBClient()

            pos_id = "pos-range-001"

            dates = [
                "2026-01-13",
                "2026-01-14",
                "2026-01-15",
                "2026-01-16",
                "2026-01-17",
            ]
            for i, d in enumerate(dates):
                await PaperSnapshotTable.put_snapshot(
                    pos_id, d, {"price": 5.0 + i * 0.1}
                )

            # Query range 2026-01-14 to 2026-01-16 (inclusive)
            result = await PaperSnapshotTable.list_by_position_range(
                pos_id, "2026-01-14", "2026-01-16"
            )

            assert len(result) == 3
            snapshot_dates = [s["snapshot_date"] for s in result]
            # scan_forward=True in the implementation, so ascending
            assert snapshot_dates == ["2026-01-14", "2026-01-15", "2026-01-16"]

        DynamoDBClient._instance = None


# ---------------------------------------------------------------------------
# 3. Snapshot written on _update_position_from_chain
# ---------------------------------------------------------------------------


class TestSnapshotWrittenOnUpdate:

    @pytest.mark.asyncio
    async def test_snapshot_written_on_update(self, moto_dynamodb):
        """Mock Polygon chain data, run _update_position_from_chain,
        verify a snapshot was written to the table."""
        DynamoDBClient._instance = None
        with patch("app.config.get_settings") as mock_settings:
            s = mock_settings.return_value
            s.dynamodb_endpoint = None
            s.aws_region = "us-east-1"
            s.dynamodb_table_prefix = "oss-test"
            DynamoDBClient._instance = DynamoDBClient()

            from app.db.tables import PaperPositionTable
            from app.paper_trading.position_manager import _update_position_from_chain

            # Create and persist an open position
            pos = _make_open_position()
            await PaperPositionTable.put(pos)

            # Build contract_data dict simulating a Polygon chain response
            contract_data = {
                "O:AAPL260320C00185000": {
                    "details": {
                        "ticker": "O:AAPL260320C00185000",
                        "strike_price": 185.0,
                        "expiration_date": "2026-03-20",
                        "contract_type": "CALL",
                    },
                    "day": {
                        "close": 5.5,
                        "volume": 1200,
                        "open": 5.0,
                        "high": 5.6,
                        "low": 4.9,
                    },
                    "underlying_asset": {"ticker": "AAPL", "price": 190.0},
                    "greeks": {
                        "delta": 0.56,
                        "gamma": 0.03,
                        "theta": -0.07,
                        "vega": 0.24,
                    },
                    "implied_volatility": 0.31,
                    "last_quote": {"bid": 5.4, "ask": 5.6, "midpoint": 5.5},
                }
            }

            result = await _update_position_from_chain(pos, contract_data)

            # Verify the update succeeded (not an error)
            assert result.error is None

            # Now check that a snapshot was written for today
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            snapshots = await PaperSnapshotTable.list_by_position(pos.position_id)

            assert len(snapshots) >= 1
            snap = snapshots[0]
            assert snap["snapshot_date"] == today
            assert snap["delta"] == 0.56
            assert snap["volume"] == 1200
            assert snap["underlying_price"] == 190.0

        DynamoDBClient._instance = None


# ---------------------------------------------------------------------------
# 4. Lambda handler routes paper_update action
# ---------------------------------------------------------------------------


class TestPaperUpdateHandlerEvent:

    def test_paper_update_handler_event(self):
        """Verify the Lambda handler routes paper_update action correctly."""
        from app.main import handler

        event = {
            "source": "oss.scheduler",
            "action": "paper_update",
        }

        with patch(
            "app.paper_trading.position_manager.update_open_positions",
            new_callable=AsyncMock,
        ) as mock_update, patch(
            "app.services.polygon.PolygonClient"
        ) as MockPolygonClass:
            # Set up the async context manager mock
            mock_polygon_instance = AsyncMock()
            MockPolygonClass.return_value.__aenter__ = AsyncMock(
                return_value=mock_polygon_instance
            )
            MockPolygonClass.return_value.__aexit__ = AsyncMock(return_value=False)

            # Return empty results (no positions)
            mock_update.return_value = []

            result = handler(event, None)

            assert result["status"] == "success"
            assert result["positions_updated"] == 0
            assert result["exits_triggered"] == 0
            mock_update.assert_called_once()
