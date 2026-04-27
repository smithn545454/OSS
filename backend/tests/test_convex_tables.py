"""Integration tests for the Convex Mode DynamoDB tables.

Covers ConvexUniverseSnapshotTable, ConvexStageEventTable, CatalystCalendarTable.
Uses moto to verify PK/SK round-trips and query patterns end-to-end.
"""

from __future__ import annotations

import pytest

from app.core.schemas import (
    CatalystCalendarEntry,
    CatalystEventType,
    ConvexStageEventRecord,
    ConvexStagePayload,
    ConvexUniverseEntry,
    ConvexUniverseSnapshot,
)
from app.db.tables import (
    CatalystCalendarTable,
    ConvexStageEventTable,
    ConvexUniverseSnapshotTable,
)


class TestConvexUniverseSnapshotTable:

    def _make_snapshot(self, snapshot_date: str = "2026-04-01") -> ConvexUniverseSnapshot:
        return ConvexUniverseSnapshot(
            snapshot_date=snapshot_date,
            policy_version="v4.1.1",
            tickers=[
                ConvexUniverseEntry(
                    ticker="NVDA",
                    sector="Technology",
                    market_cap=3.2e12,
                    avg_options_volume_30d=412000.0,
                    avg_atm_spread_pct=1.2,
                    tail_event_count_252d=23,
                    hv_regime_ratio=1.08,
                    historical_max_30d_move_pct=18.4,
                ),
                ConvexUniverseEntry(
                    ticker="TSLA",
                    sector="Consumer Discretionary",
                    tail_event_count_252d=18,
                ),
            ],
            total_count=2,
            sector_distribution={"Technology": 1, "Consumer Discretionary": 1},
        )

    @pytest.mark.asyncio
    async def test_put_and_get_latest(self, fresh_dynamodb_client):
        snap = self._make_snapshot()
        await ConvexUniverseSnapshotTable.put(snap)

        latest = await ConvexUniverseSnapshotTable.get_latest()
        assert latest is not None
        assert latest.snapshot_date == "2026-04-01"
        assert latest.total_count == 2
        assert latest.tickers[0].ticker == "NVDA"
        assert latest.tickers[0].tail_event_count_252d == 23

    @pytest.mark.asyncio
    async def test_get_latest_returns_most_recent(self, fresh_dynamodb_client):
        await ConvexUniverseSnapshotTable.put(self._make_snapshot("2026-02-01"))
        await ConvexUniverseSnapshotTable.put(self._make_snapshot("2026-03-01"))
        await ConvexUniverseSnapshotTable.put(self._make_snapshot("2026-04-01"))

        latest = await ConvexUniverseSnapshotTable.get_latest()
        assert latest is not None
        assert latest.snapshot_date == "2026-04-01"

    @pytest.mark.asyncio
    async def test_get_by_date(self, fresh_dynamodb_client):
        await ConvexUniverseSnapshotTable.put(self._make_snapshot("2026-03-01"))
        await ConvexUniverseSnapshotTable.put(self._make_snapshot("2026-04-01"))

        snap = await ConvexUniverseSnapshotTable.get_by_date("2026-03-01")
        assert snap is not None
        assert snap.snapshot_date == "2026-03-01"

        missing = await ConvexUniverseSnapshotTable.get_by_date("2026-01-01")
        assert missing is None


class TestConvexStageEventTable:

    def _make_record(
        self,
        run_id: str = "run-abc",
        ticker: str = "NVDA",
        stage: int = 1,
        result: str = "PASS",
    ) -> ConvexStageEventRecord:
        return ConvexStageEventRecord(
            run_id=run_id,
            ticker=ticker,
            stage=stage,
            payload=ConvexStagePayload(
                stage=stage,
                stage_name=f"Stage {stage}",
                result=result,
                summary="test summary",
                criteria={"liquidity": {"pass": True, "value": "OK"}},
                strength=0.8,
            ),
        )

    @pytest.mark.asyncio
    async def test_put_and_list_by_run(self, fresh_dynamodb_client):
        records = [
            self._make_record(stage=1),
            self._make_record(stage=2),
            self._make_record(stage=3, result="FAIL"),
            self._make_record(ticker="TSLA", stage=1),
        ]
        for r in records:
            await ConvexStageEventTable.put(r)

        all_events = await ConvexStageEventTable.list_by_run("run-abc")
        assert len(all_events) == 4

    @pytest.mark.asyncio
    async def test_list_for_ticker_returns_ordered_stages(self, fresh_dynamodb_client):
        await ConvexStageEventTable.put(self._make_record(stage=3))
        await ConvexStageEventTable.put(self._make_record(stage=1))
        await ConvexStageEventTable.put(self._make_record(stage=2))
        await ConvexStageEventTable.put(self._make_record(ticker="AAPL", stage=1))

        nvda_stages = await ConvexStageEventTable.list_for_ticker("run-abc", "NVDA")
        assert [r.stage for r in nvda_stages] == [1, 2, 3]
        # Confirm only NVDA records returned
        assert all(r.ticker == "NVDA" for r in nvda_stages)

    @pytest.mark.asyncio
    async def test_put_batch(self, fresh_dynamodb_client):
        records = [self._make_record(stage=s) for s in (1, 2, 3, 4)]
        await ConvexStageEventTable.put_batch(records)

        events = await ConvexStageEventTable.list_for_ticker("run-abc", "NVDA")
        assert len(events) == 4


class TestCatalystCalendarTable:

    def _make_entry(
        self,
        ticker: str = "NVDA",
        event_date: str = "2026-05-14",
        event_type: CatalystEventType = CatalystEventType.EARNINGS,
        confirmed: bool = True,
    ) -> CatalystCalendarEntry:
        return CatalystCalendarEntry(
            ticker=ticker,
            event_date=event_date,
            event_type=event_type,
            confirmed=confirmed,
            source="finnhub",
            metadata={"fiscal_period": "Q1 2026"},
        )

    @pytest.mark.asyncio
    async def test_put_and_list(self, fresh_dynamodb_client):
        await CatalystCalendarTable.put(self._make_entry())
        await CatalystCalendarTable.put(
            self._make_entry(event_date="2026-08-14")
        )

        events = await CatalystCalendarTable.list_for_ticker("NVDA")
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_list_with_date_range(self, fresh_dynamodb_client):
        await CatalystCalendarTable.put(self._make_entry(event_date="2026-04-01"))
        await CatalystCalendarTable.put(self._make_entry(event_date="2026-05-14"))
        await CatalystCalendarTable.put(self._make_entry(event_date="2026-08-14"))

        # Within window
        within = await CatalystCalendarTable.list_for_ticker(
            "NVDA", start_date="2026-05-01", end_date="2026-06-30"
        )
        assert len(within) == 1
        assert within[0].event_date == "2026-05-14"

    @pytest.mark.asyncio
    async def test_delete(self, fresh_dynamodb_client):
        entry = self._make_entry()
        await CatalystCalendarTable.put(entry)
        events = await CatalystCalendarTable.list_for_ticker("NVDA")
        assert len(events) == 1

        await CatalystCalendarTable.delete(
            "NVDA", "2026-05-14", CatalystEventType.EARNINGS
        )
        events = await CatalystCalendarTable.list_for_ticker("NVDA")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_macro_events(self, fresh_dynamodb_client):
        await CatalystCalendarTable.put(
            CatalystCalendarEntry(
                ticker="MACRO",
                event_date="2026-05-07",
                event_type=CatalystEventType.FOMC,
                confirmed=True,
                source="manual",
            )
        )

        events = await CatalystCalendarTable.list_for_ticker("MACRO")
        assert len(events) == 1
        assert events[0].event_type == CatalystEventType.FOMC
