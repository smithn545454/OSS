"""Tests for the Convex daily pipeline runner + production providers.

Covers the orchestration glue: policy gating, snapshot lookup, peer-cache
construction, provider chain → finalisation → persistence. Pure-function
stage logic and pipeline wiring are exercised in their own test modules.
"""

from __future__ import annotations

import math
import random
from typing import Optional

import pytest

from app.convex.daily_runner import run_daily_convex_pipeline
from app.convex.providers import (
    PeerReactionsCache,
    ProductionStage2InputsProvider,
    ProductionStage3InputsProvider,
    ProductionStage4InputsProvider,
    _chain_to_contract_candidates,
    _chain_to_iv_metrics,
    _split_chain_volume,
    _underlying_price_from_chain,
    build_peer_reactions_cache,
)
from app.core.schemas import (
    ConvexConfig,
    ConvexUniverseEntry,
    ConvexUniverseSnapshot,
    EarningsEvent,
    PriceHistory,
)
from app.db.tables import (
    ConvexStageEventTable,
    ConvexUniverseSnapshotTable,
    EarningsHistoryTable,
    PriceHistoryTable,
)

# ---------------------------------------------------------------------------
# Stub Polygon client (no network)
# ---------------------------------------------------------------------------


class _FakePolygon:
    """Minimal Polygon stub used by the production providers under test."""

    def __init__(self, chain_by_ticker: Optional[dict[str, list[dict]]] = None) -> None:
        self._chains = chain_by_ticker or {}

    async def get_options_chain_minimal(self, ticker: str) -> list[dict]:
        return self._chains.get(ticker, [])


def _polygon_chain_row(
    ticker: str,
    expiry: str,
    contract_type: str,
    strike: float,
    delta: float,
    bid: float = 4.50,
    ask: float = 4.70,
    iv: float = 0.30,
    underlying_price: float = 140.0,
    open_interest: int = 5000,
    volume: int = 1000,
) -> dict:
    exp_part = expiry.replace("-", "")[2:]
    cp = contract_type[0].upper()
    strike_part = f"{int(strike * 1000):08d}"
    return {
        "ticker": f"O:{ticker}{exp_part}{cp}{strike_part}",
        "underlying_asset": {"price": underlying_price, "ticker": ticker},
        "details": {
            "ticker": ticker,
            "contract_type": contract_type,
            "strike_price": strike,
            "expiration_date": expiry,
        },
        "greeks": {"delta": delta},
        "implied_volatility": iv,
        "last_quote": {"bid": bid, "ask": ask},
        "open_interest": open_interest,
        "day": {"volume": volume},
    }


# ---------------------------------------------------------------------------
# Chain helpers
# ---------------------------------------------------------------------------


class TestChainHelpers:

    def test_split_chain_volume_separates_calls_and_puts(self):
        chain = [
            _polygon_chain_row("NVDA", "2026-06-26", "call", 145, 0.30, volume=500),
            _polygon_chain_row("NVDA", "2026-06-26", "call", 150, 0.20, volume=200),
            _polygon_chain_row("NVDA", "2026-06-26", "put", 135, -0.30, volume=300),
        ]
        total, call, put = _split_chain_volume(chain)
        assert total == 1000.0
        assert call == 700.0
        assert put == 300.0

    def test_underlying_price_from_chain_uses_first_match(self):
        chain = [
            _polygon_chain_row("NVDA", "2026-06-26", "call", 145, 0.30, underlying_price=140.5),
        ]
        assert _underlying_price_from_chain(chain) == 140.5

    def test_underlying_price_returns_none_when_missing(self):
        assert _underlying_price_from_chain([]) is None

    def test_chain_to_contract_candidates_filters_invalid_rows(self):
        chain = [
            _polygon_chain_row("NVDA", "2026-06-26", "call", 145, 0.30),
            # Invalid: missing strike
            {
                "ticker": "O:bad",
                "details": {"contract_type": "call", "expiration_date": "2026-06-26"},
                "greeks": {"delta": 0.30},
                "last_quote": {"bid": 1, "ask": 2},
                "open_interest": 100,
                "day": {"volume": 10},
            },
        ]
        candidates = _chain_to_contract_candidates(chain, "2026-04-26")
        assert len(candidates) == 1
        assert candidates[0].strike == 145

    def test_chain_to_iv_metrics_extracts_targets(self):
        chain = [
            _polygon_chain_row("NVDA", "2026-05-26", "call", 140, 0.50, iv=0.30),
            _polygon_chain_row("NVDA", "2026-06-25", "call", 140, 0.50, iv=0.25),
            _polygon_chain_row("NVDA", "2026-05-26", "put", 130, -0.25, iv=0.36),
            _polygon_chain_row("NVDA", "2026-05-26", "call", 145, 0.25, iv=0.32),
        ]
        result = _chain_to_iv_metrics(chain, "2026-04-26")
        assert result is not None
        assert result["iv_30d"] is not None
        assert result["iv_60d"] is not None
        assert result["iv_25d_call"] is not None
        assert result["iv_25d_put"] is not None


# ---------------------------------------------------------------------------
# Peer reactions cache
# ---------------------------------------------------------------------------


def _seed_universe(snapshot_date: str = "2026-04-01") -> ConvexUniverseSnapshot:
    return ConvexUniverseSnapshot(
        snapshot_date=snapshot_date,
        policy_version="v4.1.1",
        tickers=[
            ConvexUniverseEntry(ticker="NVDA", sector="Technology", tail_event_count_252d=20),
            ConvexUniverseEntry(ticker="AMD", sector="Technology", tail_event_count_252d=18),
            ConvexUniverseEntry(ticker="JPM", sector="Financials", tail_event_count_252d=10),
        ],
        total_count=3,
        sector_distribution={"Technology": 2, "Financials": 1},
    )


class TestPeerReactionsCache:

    @pytest.mark.asyncio
    async def test_cache_groups_by_sector_and_filters_by_window(
        self, fresh_dynamodb_client
    ):
        # AMD reported 4 days ago with a +8% move; NVDA didn't report.
        await EarningsHistoryTable.put(EarningsEvent(
            ticker="AMD",
            earnings_date="2026-04-22",
            one_day_move_pct=8.0,
        ))
        # JPM reported 4 days ago in a different sector.
        await EarningsHistoryTable.put(EarningsEvent(
            ticker="JPM",
            earnings_date="2026-04-22",
            one_day_move_pct=2.0,
        ))

        cache = await build_peer_reactions_cache(_seed_universe(), today_iso="2026-04-26")

        tech_reactions = cache.reactions_for_sector("Technology")
        amd_tickers = {r.ticker for r in tech_reactions}
        assert "AMD" in amd_tickers
        # NVDA had no event so should not appear
        assert "NVDA" not in amd_tickers

        fin_reactions = cache.reactions_for_sector("Financials")
        assert {r.ticker for r in fin_reactions} == {"JPM"}

    @pytest.mark.asyncio
    async def test_event_outside_window_excluded(self, fresh_dynamodb_client):
        await EarningsHistoryTable.put(EarningsEvent(
            ticker="AMD",
            earnings_date="2026-04-15",  # 11 days ago, beyond 5-day window
            one_day_move_pct=8.0,
        ))
        cache = await build_peer_reactions_cache(_seed_universe(), today_iso="2026-04-26")
        tech_reactions = cache.reactions_for_sector("Technology")
        assert all(r.ticker != "AMD" for r in tech_reactions)


# ---------------------------------------------------------------------------
# Production Stage 2/3/4 providers (data wiring)
# ---------------------------------------------------------------------------


def _random_walk(n: int = 252, seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n):
        closes.append(max(0.01, closes[-1] * math.exp(rng.gauss(0, 0.025))))
    return closes


async def _seed_price_history(ticker: str, n_days: int = 252, seed: int = 1) -> None:
    from datetime import date, timedelta

    closes = _random_walk(n=n_days, seed=seed)
    bars = [
        PriceHistory(
            ticker=ticker,
            date=(date(2024, 1, 1) + timedelta(days=i)).isoformat(),
            open=c,
            high=c * 1.005,
            low=c * 0.995,
            close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]
    await PriceHistoryTable.put_batch(bars)


class TestProductionProviders:

    @pytest.mark.asyncio
    async def test_stage2_provider_returns_none_when_price_history_thin(
        self, fresh_dynamodb_client
    ):
        # No price history seeded → provider returns None.
        cache = PeerReactionsCache(today_iso="2026-04-26")
        provider = ProductionStage2InputsProvider(_FakePolygon(), cache)
        result = await provider.fetch("NVDA", "Technology", "2026-04-26")
        assert result is None

    @pytest.mark.asyncio
    async def test_stage2_provider_assembles_full_inputs(
        self, fresh_dynamodb_client
    ):
        await _seed_price_history("NVDA")
        chain = [
            _polygon_chain_row("NVDA", "2026-05-26", "call", 145, 0.30, volume=500),
            _polygon_chain_row("NVDA", "2026-05-26", "put", 135, -0.30, volume=300),
        ]
        cache = PeerReactionsCache(today_iso="2026-04-26")
        provider = ProductionStage2InputsProvider(
            _FakePolygon({"NVDA": chain}), cache
        )
        result = await provider.fetch("NVDA", "Technology", "2026-04-26")
        assert result is not None
        assert result.ticker == "NVDA"
        assert len(result.closes) >= 60
        assert result.today_total_options_volume == 800.0
        assert result.today_call_options_volume == 500.0
        assert result.today_put_options_volume == 300.0

    @pytest.mark.asyncio
    async def test_stage3_provider_falls_back_to_chain_for_iv(
        self, fresh_dynamodb_client
    ):
        await _seed_price_history("NVDA")
        # IVHistory is empty; provider should fall back to chain extraction.
        chain = [
            _polygon_chain_row("NVDA", "2026-05-26", "call", 140, 0.50, iv=0.30),
            _polygon_chain_row("NVDA", "2026-06-25", "call", 140, 0.50, iv=0.25),
        ]
        provider = ProductionStage3InputsProvider(_FakePolygon({"NVDA": chain}))
        result = await provider.fetch("NVDA", "state_based", "2026-04-26")
        assert result is not None
        # iv_30d should be populated from the chain extractor.
        assert result.current_iv_30d is not None

    @pytest.mark.asyncio
    async def test_stage4_provider_returns_none_when_chain_empty(
        self, fresh_dynamodb_client
    ):
        provider = ProductionStage4InputsProvider(_FakePolygon())
        result = await provider.fetch(
            ticker="NVDA",
            direction="bullish",
            catalyst_type="state_based",
            catalyst_date_iso=None,
            uv_directional_skew=None,
            today_iso="2026-04-26",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_stage4_provider_assembles_inputs(
        self, fresh_dynamodb_client
    ):
        await _seed_price_history("NVDA")
        chain = [
            _polygon_chain_row(
                "NVDA", "2026-06-26", "call", 145, 0.30,
                bid=4.75, ask=4.95, open_interest=5000, volume=1500,
            ),
            _polygon_chain_row(
                "NVDA", "2026-06-26", "put", 135, -0.30,
                bid=4.40, ask=4.60, open_interest=3000, volume=800,
            ),
        ]
        provider = ProductionStage4InputsProvider(_FakePolygon({"NVDA": chain}))
        result = await provider.fetch(
            ticker="NVDA",
            direction="bullish",
            catalyst_type="state_based",
            catalyst_date_iso=None,
            uv_directional_skew=None,
            today_iso="2026-04-26",
        )
        assert result is not None
        assert result.underlying_price == 140.0
        assert len(result.available_contracts) == 2
        # measured_move_pct populated from PriceHistory's 20-day range
        assert result.measured_move_pct is not None
        # No date-known catalyst, so historical_event_move_pct is None
        assert result.historical_event_move_pct is None


# ---------------------------------------------------------------------------
# Daily runner
# ---------------------------------------------------------------------------


class TestRunDailyConvexPipeline:

    @pytest.mark.asyncio
    async def test_disabled_returns_no_op(self, fresh_dynamodb_client):
        cfg = ConvexConfig(enabled=False)
        result = await run_daily_convex_pipeline(
            config=cfg,
            polygon_client=_FakePolygon(),
            policy_version="v4.1.1",
        )
        assert result.error is None
        assert result.universe_size == 0
        assert result.finalised == []

    @pytest.mark.asyncio
    async def test_no_snapshot_returns_error_status(self, fresh_dynamodb_client):
        cfg = ConvexConfig(enabled=True)
        result = await run_daily_convex_pipeline(
            config=cfg,
            polygon_client=_FakePolygon(),
            policy_version="v4.1.1",
        )
        assert result.error == "no_universe_snapshot"

    @pytest.mark.asyncio
    async def test_full_run_persists_stage_events(self, fresh_dynamodb_client):
        # Seed universe + price history so Stage 1 + Stage 2 can advance.
        snapshot = _seed_universe()
        await ConvexUniverseSnapshotTable.put(snapshot)
        await _seed_price_history("NVDA")
        await _seed_price_history("AMD", seed=2)
        await _seed_price_history("JPM", seed=3)

        # Empty chains → Stage 2 still gets price history; UV and other
        # detectors won't fire so Stage 2 will FAIL for everyone, but
        # stage events for Stage 1 + Stage 2 should be persisted.
        cfg = ConvexConfig(enabled=True)
        result = await run_daily_convex_pipeline(
            config=cfg,
            polygon_client=_FakePolygon(),
            policy_version="v4.1.1",
            today_iso="2026-04-26",
        )
        assert result.error is None
        assert result.universe_size == 3
        # No tier promotion since Stage 2 fails for all
        assert result.tier_a_count == 0
        assert result.finalised == []

        # Stage events were persisted for all three tickers (Stage 1 PASS,
        # Stage 2 FAIL; nothing for Stage 3/4).
        events = await ConvexStageEventTable.list_by_run(result.run_id)
        # 3 tickers × at least Stage 1 + Stage 2
        assert len(events) >= 6
        stages_seen = {(e.ticker, e.stage) for e in events}
        for t in ("NVDA", "AMD", "JPM"):
            assert (t, 1) in stages_seen
            assert (t, 2) in stages_seen
