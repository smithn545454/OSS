"""Integration test for UniverseConstructor.

Mocks the metadata fetcher (no Polygon) and uses moto-backed PriceHistoryTable
to verify the full fetch → gate → snapshot persistence flow.
"""

from __future__ import annotations

import math
import random

import pytest

from app.convex import (
    TickerMetadata,
    TickerMetadataFetcher,
    UniverseConstructor,
)
from app.core.schemas import ConvexConfig, PriceHistory
from app.db.tables import (
    ConvexUniverseSnapshotTable,
    PriceHistoryTable,
)


def _random_walk(n: int = 252, sigma: float = 0.025, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n):
        closes.append(max(0.01, closes[-1] * math.exp(rng.gauss(0, sigma))))
    return closes


async def _seed_price_history(ticker: str, n_days: int = 252, seed: int = 0) -> None:
    """Seed PriceHistoryTable with synthetic OHLCV bars on contiguous dates.

    Dates are derived as ``2024-01-01 + i days`` so each bar has a unique
    SK and the moto-backed query returns them in chronological order.
    """
    from datetime import date, timedelta

    closes = _random_walk(n=n_days, sigma=0.025, seed=seed)
    bars = []
    for i, c in enumerate(closes):
        d = (date(2024, 1, 1) + timedelta(days=i)).isoformat()
        bars.append(
            PriceHistory(
                ticker=ticker,
                date=d,
                open=c,
                high=c * 1.005,
                low=c * 0.995,
                close=c,
                volume=1_000_000,
            )
        )
    await PriceHistoryTable.put_batch(bars)


class StubFetcher:
    """In-memory metadata fetcher injected in tests."""

    def __init__(self, data: dict[str, TickerMetadata]) -> None:
        self._data = data

    async def fetch(self, ticker: str):
        return self._data.get(ticker)


class TestUniverseConstructor:

    @pytest.mark.asyncio
    async def test_full_build_persists_snapshot(self, fresh_dynamodb_client):
        await _seed_price_history("NVDA", seed=1)
        await _seed_price_history("TSLA", seed=2)

        fetcher: TickerMetadataFetcher = StubFetcher(
            {
                "NVDA": TickerMetadata(
                    market_cap=3.2e12,
                    avg_options_volume_30d=412_000,
                    avg_atm_spread_pct=1.2,
                ),
                "TSLA": TickerMetadata(
                    market_cap=8.0e11,
                    avg_options_volume_30d=300_000,
                    avg_atm_spread_pct=1.5,
                ),
            }
        )
        constructor = UniverseConstructor(ConvexConfig(), fetcher)

        snapshot = await constructor.build_snapshot(
            tickers=["NVDA", "TSLA"], policy_version="v4.1.1"
        )

        assert snapshot.policy_version == "v4.1.1"
        # At least one ticker should pass with these inputs.
        assert snapshot.total_count >= 1
        # Persisted to DynamoDB; latest fetch returns the same snapshot.
        latest = await ConvexUniverseSnapshotTable.get_latest()
        assert latest is not None
        assert latest.snapshot_date == snapshot.snapshot_date
        assert latest.total_count == snapshot.total_count

    @pytest.mark.asyncio
    async def test_skips_tickers_missing_price_history(self, fresh_dynamodb_client):
        # Only seed price history for NVDA; TSLA has nothing in the table.
        await _seed_price_history("NVDA", seed=11)

        fetcher = StubFetcher(
            {
                "NVDA": TickerMetadata(
                    market_cap=3.2e12,
                    avg_options_volume_30d=412_000,
                    avg_atm_spread_pct=1.2,
                ),
                "TSLA": TickerMetadata(
                    market_cap=8.0e11,
                    avg_options_volume_30d=300_000,
                    avg_atm_spread_pct=1.5,
                ),
            }
        )
        constructor = UniverseConstructor(ConvexConfig(), fetcher)

        snapshot = await constructor.build_snapshot(
            tickers=["NVDA", "TSLA"], policy_version="v4.1.1"
        )
        # TSLA is silently skipped (no price history), so it cannot land in
        # the snapshot regardless of metadata. NVDA is the only candidate.
        assert all(e.ticker != "TSLA" for e in snapshot.tickers)

    @pytest.mark.asyncio
    async def test_skips_tickers_with_failing_metadata(self, fresh_dynamodb_client):
        await _seed_price_history("NVDA", seed=21)
        await _seed_price_history("TINY", seed=22)

        fetcher = StubFetcher(
            {
                "NVDA": TickerMetadata(
                    market_cap=3.2e12,
                    avg_options_volume_30d=412_000,
                    avg_atm_spread_pct=1.2,
                ),
                "TINY": TickerMetadata(
                    market_cap=200_000_000,  # Below $1B floor
                    avg_options_volume_30d=412_000,
                    avg_atm_spread_pct=1.2,
                ),
            }
        )
        constructor = UniverseConstructor(ConvexConfig(), fetcher)
        snapshot = await constructor.build_snapshot(
            tickers=["NVDA", "TINY"], policy_version="v4.1.1"
        )
        # TINY should fail the market_cap gate.
        assert all(e.ticker != "TINY" for e in snapshot.tickers)
