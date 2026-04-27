"""Tests for Convex Mode Stage 1 (Kinetic Universe Construction).

Pure-function gate logic + sector cap + build_universe orchestrator.
Universe-builder integration (DB + metadata fetcher) is covered separately
in test_convex_universe_builder.py.
"""

from __future__ import annotations

import math
import random

import pytest

from app.convex import (
    TickerKineticInputs,
    apply_sector_cap,
    build_universe,
    calculate_realized_volatility,
    count_tail_events,
    evaluate_ticker,
    historical_max_30d_move_pct,
)
from app.convex.stage1_universe import (
    gate_hv_regime,
    gate_kinetic_capability,
    gate_liquidity,
    gate_market_cap,
)
from app.core.schemas import ConvexConfig, ConvexUniverseEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def random_walk(
    n: int = 252, start: float = 100.0, sigma: float = 0.02, seed: int = 0
) -> list[float]:
    """Generate a synthetic price series via geometric random walk."""
    rng = random.Random(seed)
    closes = [start]
    for _ in range(n):
        ret = rng.gauss(0, sigma)
        closes.append(max(0.01, closes[-1] * math.exp(ret)))
    return closes


# ---------------------------------------------------------------------------
# Volatility math
# ---------------------------------------------------------------------------


class TestRealizedVolatility:

    def test_returns_none_when_insufficient_data(self):
        assert calculate_realized_volatility([100, 101, 102], window=20) is None

    def test_returns_none_for_non_positive_prices(self):
        closes = [100.0] * 25
        closes[10] = 0.0
        assert calculate_realized_volatility(closes, window=20) is None

    def test_constant_prices_yield_zero_vol(self):
        closes = [100.0] * 30
        rv = calculate_realized_volatility(closes, window=20)
        assert rv == pytest.approx(0.0, abs=1e-9)

    def test_higher_volatility_returns_larger_value(self):
        calm = random_walk(n=252, sigma=0.005, seed=1)
        wild = random_walk(n=252, sigma=0.05, seed=1)
        assert (
            calculate_realized_volatility(wild, 20)
            > calculate_realized_volatility(calm, 20)
        )


class TestCountTailEvents:

    def test_zero_when_no_volatility(self):
        closes = [100.0] * 100
        assert count_tail_events(closes) == 0

    def test_returns_zero_when_too_few_bars(self):
        assert count_tail_events([100, 101, 102]) == 0

    def test_outliers_above_threshold_counted(self):
        # Build a series with stable returns then inject one large move.
        closes = random_walk(n=252, sigma=0.01, seed=2)
        # Inject a 10% move at the end — far above 2σ for a 1% vol series.
        closes.append(closes[-1] * 1.10)
        count = count_tail_events(closes)
        assert count >= 1


class TestHistoricalMax30dMove:

    def test_returns_none_for_short_series(self):
        assert historical_max_30d_move_pct([100, 101]) is None

    def test_picks_largest_30d_excursion(self):
        closes = [100.0] * 31
        # 30-day move of 0%; bumping the latest to 130 → a 30% move
        closes[-1] = 130.0
        assert historical_max_30d_move_pct(closes) == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------


class TestIndividualGates:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_liquidity_pass(self):
        inp = TickerKineticInputs(
            ticker="NVDA",
            closes=[100.0] * 70,
            avg_options_volume_30d=10_000,
            avg_atm_spread_pct=2.5,
        )
        result = gate_liquidity(inp, self.cfg)
        assert result.pass_ is True

    def test_liquidity_fails_when_volume_low(self):
        inp = TickerKineticInputs(
            ticker="X",
            closes=[100.0] * 70,
            avg_options_volume_30d=100,
            avg_atm_spread_pct=2.0,
        )
        assert gate_liquidity(inp, self.cfg).pass_ is False

    def test_liquidity_fails_on_wide_spread(self):
        inp = TickerKineticInputs(
            ticker="X",
            closes=[100.0] * 70,
            avg_options_volume_30d=10_000,
            avg_atm_spread_pct=12.0,  # > 5% cap
        )
        assert gate_liquidity(inp, self.cfg).pass_ is False

    def test_liquidity_fails_when_data_missing(self):
        inp = TickerKineticInputs(ticker="X", closes=[])
        assert gate_liquidity(inp, self.cfg).pass_ is False

    def test_kinetic_capability_threshold(self):
        assert gate_kinetic_capability(8, self.cfg).pass_ is True
        assert gate_kinetic_capability(7, self.cfg).pass_ is False

    def test_hv_regime_in_range(self):
        assert gate_hv_regime(0.20, 0.25, self.cfg).pass_ is True

    def test_hv_regime_below_floor(self):
        # ratio = 0.5; below 0.7 floor
        assert gate_hv_regime(0.10, 0.20, self.cfg).pass_ is False

    def test_hv_regime_above_ceiling(self):
        # ratio = 2.0; above 1.5 ceiling
        assert gate_hv_regime(0.40, 0.20, self.cfg).pass_ is False

    def test_hv_regime_missing_data(self):
        assert gate_hv_regime(None, 0.2, self.cfg).pass_ is False
        assert gate_hv_regime(0.2, None, self.cfg).pass_ is False
        assert gate_hv_regime(0.2, 0.0, self.cfg).pass_ is False

    def test_market_cap_pass(self):
        inp = TickerKineticInputs(
            ticker="X", closes=[], market_cap=2_500_000_000
        )
        assert gate_market_cap(inp, self.cfg).pass_ is True

    def test_market_cap_fail(self):
        inp = TickerKineticInputs(
            ticker="X", closes=[], market_cap=500_000_000
        )
        assert gate_market_cap(inp, self.cfg).pass_ is False


# ---------------------------------------------------------------------------
# evaluate_ticker (integrates all four gates)
# ---------------------------------------------------------------------------


class TestEvaluateTicker:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_full_pass_creates_universe_entry(self):
        # Construct a series with ample volatility so kinetic + HV gates pass.
        closes = random_walk(n=252, sigma=0.025, seed=42)
        inp = TickerKineticInputs(
            ticker="NVDA",
            closes=closes,
            sector="Technology",
            market_cap=3.2e12,
            avg_options_volume_30d=412_000,
            avg_atm_spread_pct=1.2,
        )
        ev = evaluate_ticker(inp, self.cfg)
        assert ev.passed is True
        assert ev.entry is not None
        assert ev.entry.ticker == "NVDA"
        assert ev.entry.tail_event_count_252d >= 0
        assert ev.payload.result == "PASS"
        assert ev.payload.criteria["liquidity"]["pass"] is True
        assert "qualifies as kinetically capable" in ev.payload.summary

    def test_fail_on_market_cap(self):
        closes = random_walk(n=252, sigma=0.025, seed=7)
        inp = TickerKineticInputs(
            ticker="SMALL",
            closes=closes,
            sector="Technology",
            market_cap=200_000_000,
            avg_options_volume_30d=412_000,
            avg_atm_spread_pct=1.2,
        )
        ev = evaluate_ticker(inp, self.cfg)
        assert ev.passed is False
        assert ev.entry is None
        assert ev.payload.result == "FAIL"
        assert ev.payload.criteria["market_cap"]["pass"] is False

    def test_fail_when_data_too_thin(self):
        # Only 20 closes — not enough for HV60.
        inp = TickerKineticInputs(
            ticker="X",
            closes=[100.0] * 20,
            market_cap=2e9,
            avg_options_volume_30d=10_000,
            avg_atm_spread_pct=1.0,
        )
        ev = evaluate_ticker(inp, self.cfg)
        assert ev.passed is False


# ---------------------------------------------------------------------------
# Sector cap
# ---------------------------------------------------------------------------


class TestApplySectorCap:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def _entry(self, ticker: str, sector: str, tail_events: int = 10) -> ConvexUniverseEntry:
        return ConvexUniverseEntry(
            ticker=ticker,
            sector=sector,
            tail_event_count_252d=tail_events,
        )

    def test_no_trim_when_balanced(self):
        # 25% of 16 = 4 per sector, so 4 + 4 + 4 + 4 stays intact.
        entries = []
        for sector in ("Tech", "Health", "Energy", "Finance"):
            for i in range(4):
                entries.append(self._entry(f"{sector[0]}{i}", sector))
        trimmed, dist = apply_sector_cap(entries, self.cfg)
        assert len(trimmed) == 16
        assert dist == {"Tech": 4, "Health": 4, "Energy": 4, "Finance": 4}

    def test_trims_overrepresented_sector(self):
        # 8 Tech + 2 Health = 10 entries. Cap is 25% → max 2 per sector.
        entries = [
            self._entry(f"T{i}", "Tech", tail_events=i)
            for i in range(8)
        ] + [
            self._entry(f"H{i}", "Health")
            for i in range(2)
        ]
        trimmed, dist = apply_sector_cap(entries, self.cfg)
        # 25% of 10 = 2 per sector.
        assert dist["Tech"] == 2
        assert dist["Health"] == 2
        # Highest-tail-event Tech entries kept (T7 and T6).
        kept_tech = sorted(e.ticker for e in trimmed if e.sector == "Tech")
        assert "T7" in kept_tech
        assert "T6" in kept_tech

    def test_unknown_sector_bucketed(self):
        entries = [
            self._entry(f"X{i}", None) for i in range(3)
        ]
        trimmed, dist = apply_sector_cap(entries, self.cfg)
        assert "Unknown" in dist

    def test_empty_input(self):
        trimmed, dist = apply_sector_cap([], self.cfg)
        assert trimmed == []
        assert dist == {}


# ---------------------------------------------------------------------------
# build_universe top-level
# ---------------------------------------------------------------------------


class TestBuildUniverse:

    def setup_method(self):
        self.cfg = ConvexConfig()

    # Deterministic per-ticker seed so test outcomes are stable across
    # Python invocations (Python's built-in hash() is randomized).
    _SEEDS: dict[str, int] = {}

    def _passing_inputs(self, ticker: str, sector: str = "Tech") -> TickerKineticInputs:
        seed = self._SEEDS.setdefault(ticker, len(self._SEEDS) + 1)
        return TickerKineticInputs(
            ticker=ticker,
            closes=random_walk(n=252, sigma=0.025, seed=seed),
            sector=sector,
            market_cap=2e9,
            avg_options_volume_30d=20_000,
            avg_atm_spread_pct=1.5,
        )

    def test_separates_passes_and_rejections(self):
        inputs = [
            self._passing_inputs("NVDA"),
            TickerKineticInputs(  # Will fail (too thin)
                ticker="X",
                closes=[100.0] * 20,
                market_cap=2e9,
                avg_options_volume_30d=10_000,
                avg_atm_spread_pct=1.0,
            ),
        ]
        result = build_universe(inputs, self.cfg)
        assert {e.ticker for e in result.entries} == {"NVDA"}
        assert "X" in result.rejected_tickers
        assert "NVDA" in result.payloads
        assert "X" in result.payloads
        assert result.payloads["X"].result == "FAIL"

    def test_records_sector_cap_drops(self):
        # Force a single-sector heavy universe so cap kicks in. Some
        # individual random walks may fail Stage 1 gates (counted in
        # rejected_tickers); the rest pass and are subject to the sector cap.
        inputs = [self._passing_inputs(f"T{i}", sector="Tech") for i in range(8)]
        inputs += [self._passing_inputs(f"H{i}", sector="Health") for i in range(2)]

        result = build_universe(inputs, self.cfg)
        # 25% of 10 = 2 per sector.
        tech_kept = sum(1 for e in result.entries if e.sector == "Tech")
        assert tech_kept <= 2
        # Cap drops are distinct from gate rejections.
        assert set(result.capped_tickers).isdisjoint(set(result.rejected_tickers))
        # All capped tickers passed Stage 1 (so each appears as PASS in payloads).
        for t in result.capped_tickers:
            assert result.payloads[t].result == "PASS"
