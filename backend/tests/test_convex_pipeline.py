"""Tests for the Convex Mode pipeline scaffold (Phase 1).

In Phase 1 every stage stub returns FAIL and the pipeline produces zero
approvals. These tests lock in the data contract so future phases can fill
in stage logic without breaking the orchestrator shape.
"""

from __future__ import annotations

import pytest

from app.convex import (
    ConvexCandidate,
    ConvexContractCandidate,
    ConvexPipeline,
    ConvexPipelineResult,
    Stage2Inputs,
    Stage3Inputs,
    Stage4Inputs,
    Tier,
)
from app.core.schemas import (
    CatalystCalendarEntry,
    CatalystEventType,
    ConvexConfig,
    ConvexStagePayload,
    ConvexUniverseEntry,
    ConvexUniverseSnapshot,
    IVHistory,
)


class TestConvexCandidate:

    def test_advanced_to_stage_zero_when_stage1_fail(self):
        c = ConvexCandidate(ticker="NVDA")
        c.stages = c.stages.model_copy(
            update={
                "stage_1": ConvexStagePayload(
                    stage=1, stage_name="Kinetic Universe", result="FAIL", summary="x"
                )
            }
        )
        assert c.advanced_to_stage == 0

    def test_advanced_to_stage_increments_on_each_pass(self):
        c = ConvexCandidate(ticker="NVDA")
        c.stages = c.stages.model_copy(
            update={
                "stage_1": ConvexStagePayload(
                    stage=1, stage_name="s1", result="PASS", summary="x"
                )
            }
        )
        assert c.advanced_to_stage == 1
        c.stages = c.stages.model_copy(
            update={
                "stage_2": ConvexStagePayload(
                    stage=2, stage_name="s2", result="PASS", summary="x"
                )
            }
        )
        assert c.advanced_to_stage == 2
        c.stages = c.stages.model_copy(
            update={
                "stage_3": ConvexStagePayload(
                    stage=3, stage_name="s3", result="FAIL", summary="x"
                )
            }
        )
        assert c.advanced_to_stage == 2  # Stops at first FAIL


class TestConvexPipelineDisabled:

    @pytest.mark.asyncio
    async def test_returns_immediately_when_disabled(self):
        cfg = ConvexConfig(enabled=False)
        pipeline = ConvexPipeline(cfg)

        result = await pipeline.run(universe_tickers=["NVDA", "TSLA"])

        assert isinstance(result, ConvexPipelineResult)
        assert result.completed_at is not None
        assert result.candidates == []
        # Universe size still recorded for telemetry visibility
        assert result.universe_size == 2

    @pytest.mark.asyncio
    async def test_default_config_is_disabled(self):
        # Master kill switch: Phase 1 default is False so the pipeline never
        # produces signals until cutover.
        assert ConvexConfig().enabled is False


class TestConvexPipelineEnabledScaffold:

    @pytest.mark.asyncio
    async def test_empty_universe_short_circuits(self):
        pipeline = ConvexPipeline(ConvexConfig(enabled=True))
        result = await pipeline.run(universe_tickers=[])
        assert result.candidates == []
        assert result.universe_size == 0
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_phase1_scaffold_fails_every_candidate_at_stage1(self):
        # Phase 1: every stage stub returns FAIL, so no ticker advances.
        pipeline = ConvexPipeline(ConvexConfig(enabled=True))
        result = await pipeline.run(universe_tickers=["NVDA", "TSLA", "COIN"])

        assert result.universe_size == 3
        assert result.stage2_advancers == 0
        assert result.stage3_advancers == 0
        assert result.stage4_advancers == 0
        assert result.tier_a_count == 0
        assert result.tier_b_count == 0
        assert result.tier_c_count == 0

        # Each candidate has a Stage 1 record explaining the FAIL
        assert len(result.candidates) == 3
        for c in result.candidates:
            assert c.stages.stage_1 is not None
            assert c.stages.stage_1.result == "FAIL"
            assert c.stages.stage_2 is None  # Did not advance

    @pytest.mark.asyncio
    async def test_run_id_is_assigned(self):
        pipeline = ConvexPipeline(ConvexConfig(enabled=True))
        result = await pipeline.run(universe_tickers=["NVDA"], run_id="custom-run-id")
        assert result.run_id == "custom-run-id"

    @pytest.mark.asyncio
    async def test_run_id_auto_generated_when_omitted(self):
        pipeline = ConvexPipeline(ConvexConfig(enabled=True))
        result = await pipeline.run(universe_tickers=["NVDA"])
        # UUID4 string
        assert result.run_id
        assert len(result.run_id) == 36


class TestTierEnum:

    def test_tier_values(self):
        assert Tier.A.value == "A"
        assert Tier.B.value == "B"
        assert Tier.C.value == "C"


def _make_snapshot(tickers: list[str]) -> ConvexUniverseSnapshot:
    """Helper: build a snapshot containing the given tickers."""
    entries = [
        ConvexUniverseEntry(
            ticker=t,
            sector="Technology",
            market_cap=2.5e12,
            avg_options_volume_30d=400_000,
            avg_atm_spread_pct=1.5,
            tail_event_count_252d=20,
            hv_regime_ratio=1.05,
            historical_max_30d_move_pct=15.0,
        )
        for t in tickers
    ]
    return ConvexUniverseSnapshot(
        snapshot_date="2026-04-01",
        policy_version="v4.1.1",
        tickers=entries,
        total_count=len(entries),
        sector_distribution={"Technology": len(entries)},
    )


class TestPipelineStage1WithSnapshot:

    @pytest.mark.asyncio
    async def test_universe_member_passes_stage1(self):
        snapshot = _make_snapshot(["NVDA", "TSLA"])
        pipeline = ConvexPipeline(ConvexConfig(enabled=True), universe_snapshot=snapshot)

        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_1 is not None
        assert c.stages.stage_1.result == "PASS"
        assert "kinetic-universe member" in c.stages.stage_1.summary
        # Strength inputs propagated from the snapshot entry.
        assert c.stages.stage_1.strength_inputs["tail_event_count_252d"] == 20

    @pytest.mark.asyncio
    async def test_non_member_fails_stage1(self):
        snapshot = _make_snapshot(["NVDA"])
        pipeline = ConvexPipeline(ConvexConfig(enabled=True), universe_snapshot=snapshot)

        result = await pipeline.run(universe_tickers=["UNKNOWN"])
        c = result.candidates[0]
        assert c.stages.stage_1 is not None
        assert c.stages.stage_1.result == "FAIL"
        assert "not in the current kinetic universe" in c.stages.stage_1.summary

    @pytest.mark.asyncio
    async def test_default_universe_uses_snapshot_tickers(self):
        snapshot = _make_snapshot(["NVDA", "TSLA", "COIN"])
        pipeline = ConvexPipeline(ConvexConfig(enabled=True), universe_snapshot=snapshot)

        # No tickers passed; pipeline should fall back to the snapshot's set.
        result = await pipeline.run()
        assert result.universe_size == 3
        assert {c.ticker for c in result.candidates} == {"NVDA", "TSLA", "COIN"}

    @pytest.mark.asyncio
    async def test_no_snapshot_fails_stage1_for_every_ticker(self):
        # Without a snapshot, daily pipeline cannot validate membership and
        # all candidates fail at Stage 1 with an explanatory message.
        pipeline = ConvexPipeline(ConvexConfig(enabled=True))
        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_1 is not None
        assert c.stages.stage_1.result == "FAIL"
        assert "no universe snapshot loaded" in c.stages.stage_1.summary


class _Stage2Stub:
    """Test stub for the Stage 2 inputs provider protocol."""

    def __init__(self, inputs_by_ticker: dict[str, Stage2Inputs]) -> None:
        self._inputs = inputs_by_ticker

    async def fetch(self, ticker, sector, today_iso):  # noqa: ARG002
        return self._inputs.get(ticker)


class _Stage3Stub:
    """Test stub for the Stage 3 inputs provider protocol."""

    def __init__(self, inputs_by_ticker: dict[str, Stage3Inputs]) -> None:
        self._inputs = inputs_by_ticker

    async def fetch(self, ticker, catalyst_type, today_iso):  # noqa: ARG002
        return self._inputs.get(ticker)


class _Stage4Stub:
    """Test stub for the Stage 4 inputs provider protocol."""

    def __init__(self, inputs_by_ticker: dict[str, Stage4Inputs]) -> None:
        self._inputs = inputs_by_ticker

    async def fetch(  # noqa: PLR0913
        self,
        ticker,
        direction,
        catalyst_type,
        catalyst_date_iso,
        uv_directional_skew,
        today_iso,
    ):  # noqa: ARG002
        return self._inputs.get(ticker)


def _bullish_stage4_inputs(ticker: str) -> Stage4Inputs:
    """Helper: Stage 4 inputs that pass with a bullish thesis."""
    contracts = [
        ConvexContractCandidate(
            option_ticker="O:NVDA260620C00145000",
            option_type="CALL",
            strike=145,
            expiry="2026-06-20",
            dte=42,
            delta=0.32,
            bid=4.75,
            ask=4.95,
            open_interest=8240,
            volume=1850,
        ),
    ]
    return Stage4Inputs(
        ticker=ticker,
        underlying_price=140.0,
        direction="bullish",
        catalyst_type="state_based",
        catalyst_date_iso=None,
        measured_move_pct=4.0,
        historical_event_move_pct=5.0,
        available_contracts=contracts,
        uv_directional_skew=None,
        today_iso="2026-04-26",
    )


def _bullish_stage3_inputs(ticker: str) -> Stage3Inputs:
    """Helper: Stage 3 inputs that pass all gates with a bullish bias."""
    history = [
        IVHistory(
            ticker=ticker,
            date=f"2025-01-{(i % 27) + 1:02d}",
            atm_iv=0.30,
            iv_30d=0.20 + i * 0.01,
        )
        for i in range(25)
    ]
    return Stage3Inputs(
        ticker=ticker,
        current_iv_30d=0.22,    # Low — IV Rank ~10
        current_iv_60d=0.24,     # Contango (front below sixty)
        current_iv_25d_put=0.26, # Rich put skew
        current_iv_25d_call=0.22,
        iv_history=history,
        rv20=0.30,                # IV/HV = 0.73
        catalyst_type="state_based",
        price_position_pct=80.0,  # High in range → bullish
    )


def _make_stage2_inputs(ticker: str, with_earnings: bool) -> Stage2Inputs:
    """Helper: minimal Stage 2 inputs that produce a deterministic outcome.

    Uses a quiet-then-noisy synthetic series so that recent volatility
    *exceeds* the trailing-year baseline; this keeps the compression
    signals quiet so each test only fires the catalyst it explicitly sets.
    """
    import math
    import random
    rng = random.Random(7)
    closes = [100.0]
    for _ in range(200):
        closes.append(max(0.01, closes[-1] * math.exp(rng.gauss(0, 0.005))))
    for _ in range(60):
        closes.append(max(0.01, closes[-1] * math.exp(rng.gauss(0, 0.05))))
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    volumes = [1_000_000.0 for _ in closes]
    entries = []
    if with_earnings:
        entries.append(
            CatalystCalendarEntry(
                ticker=ticker,
                event_date="2026-05-14",  # 18 days from 2026-04-26
                event_type=CatalystEventType.EARNINGS,
                confirmed=True,
            )
        )
    return Stage2Inputs(
        ticker=ticker,
        sector="Technology",
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        nearest_significant_level_pct=None,
        calendar_entries=entries,
        today_total_options_volume=None,
        avg_options_volume_30d=None,
        today_call_options_volume=None,
        today_put_options_volume=None,
        peer_reactions=[],
    )


class TestPipelineStage2:

    @pytest.mark.asyncio
    async def test_stage2_passes_with_earnings_catalyst(self):
        snapshot = _make_snapshot(["NVDA"])
        provider = _Stage2Stub({"NVDA": _make_stage2_inputs("NVDA", with_earnings=True)})
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=provider,
            as_of_date="2026-04-26",
        )

        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_2 is not None
        assert c.stages.stage_2.result == "PASS"
        assert c.stages.stage_2.extras["selected_catalyst_type"] == "date_known"
        assert result.stage2_advancers == 1

    @pytest.mark.asyncio
    async def test_stage2_fails_without_catalyst(self):
        snapshot = _make_snapshot(["NVDA"])
        provider = _Stage2Stub({"NVDA": _make_stage2_inputs("NVDA", with_earnings=False)})
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=provider,
            as_of_date="2026-04-26",
        )

        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_2 is not None
        assert c.stages.stage_2.result == "FAIL"
        assert result.stage2_advancers == 0

    @pytest.mark.asyncio
    async def test_stage2_fails_when_provider_returns_none(self):
        snapshot = _make_snapshot(["NVDA"])
        provider = _Stage2Stub({})  # Empty — fetch returns None
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=provider,
            as_of_date="2026-04-26",
        )

        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_2 is not None
        assert c.stages.stage_2.result == "FAIL"
        assert "data unavailable" in c.stages.stage_2.summary

    @pytest.mark.asyncio
    async def test_stage2_fails_when_no_provider_configured(self):
        snapshot = _make_snapshot(["NVDA"])
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
        )
        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_2 is not None
        assert c.stages.stage_2.result == "FAIL"
        assert "no Stage 2 inputs provider" in c.stages.stage_2.summary

    @pytest.mark.asyncio
    async def test_stage3_inferred_direction_propagates_to_candidate(self):
        # Stage 1 + Stage 2 PASS via a date-known catalyst; Stage 3 then
        # infers a bullish direction and the pipeline records it on the
        # candidate.
        snapshot = _make_snapshot(["NVDA"])
        stage2_provider = _Stage2Stub({"NVDA": _make_stage2_inputs("NVDA", with_earnings=True)})
        stage3_provider = _Stage3Stub({"NVDA": _bullish_stage3_inputs("NVDA")})
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=stage2_provider,
            stage3_inputs_provider=stage3_provider,
            as_of_date="2026-04-26",
        )

        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_3 is not None
        assert c.stages.stage_3.result == "PASS"
        assert c.direction == "bullish"
        assert result.stage3_advancers == 1

    @pytest.mark.asyncio
    async def test_stage3_fails_when_provider_returns_none(self):
        snapshot = _make_snapshot(["NVDA"])
        stage2_provider = _Stage2Stub({"NVDA": _make_stage2_inputs("NVDA", with_earnings=True)})
        stage3_provider = _Stage3Stub({})  # Empty
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=stage2_provider,
            stage3_inputs_provider=stage3_provider,
            as_of_date="2026-04-26",
        )

        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_3 is not None
        assert c.stages.stage_3.result == "FAIL"
        assert "data unavailable" in c.stages.stage_3.summary

    @pytest.mark.asyncio
    async def test_stage3_fails_when_no_provider_configured(self):
        snapshot = _make_snapshot(["NVDA"])
        stage2_provider = _Stage2Stub({"NVDA": _make_stage2_inputs("NVDA", with_earnings=True)})
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=stage2_provider,
        )
        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_3 is not None
        assert c.stages.stage_3.result == "FAIL"
        assert "no Stage 3 inputs provider" in c.stages.stage_3.summary

    @pytest.mark.asyncio
    async def test_full_pipeline_pass_propagates_contract_to_candidate(self):
        # All four stages PASS end-to-end; Stage 4 contract is preserved
        # on the candidate for downstream tier assignment / Decision
        # emission.
        snapshot = _make_snapshot(["NVDA"])
        stage2 = _Stage2Stub({"NVDA": _make_stage2_inputs("NVDA", with_earnings=True)})
        stage3 = _Stage3Stub({"NVDA": _bullish_stage3_inputs("NVDA")})
        stage4 = _Stage4Stub({"NVDA": _bullish_stage4_inputs("NVDA")})
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=stage2,
            stage3_inputs_provider=stage3,
            stage4_inputs_provider=stage4,
            as_of_date="2026-04-26",
        )
        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.advanced_to_stage == 4
        assert c.stages.stage_4 is not None
        assert c.stages.stage_4.result == "PASS"
        assert c.selected_call is not None
        assert c.selected_call.strike == 145
        assert result.stage4_advancers == 1

    @pytest.mark.asyncio
    async def test_stage4_fails_when_provider_returns_none(self):
        snapshot = _make_snapshot(["NVDA"])
        stage2 = _Stage2Stub({"NVDA": _make_stage2_inputs("NVDA", with_earnings=True)})
        stage3 = _Stage3Stub({"NVDA": _bullish_stage3_inputs("NVDA")})
        stage4 = _Stage4Stub({})  # empty
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=stage2,
            stage3_inputs_provider=stage3,
            stage4_inputs_provider=stage4,
            as_of_date="2026-04-26",
        )
        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_4 is not None
        assert c.stages.stage_4.result == "FAIL"
        assert "data unavailable" in c.stages.stage_4.summary

    @pytest.mark.asyncio
    async def test_stage4_fails_when_no_provider_configured(self):
        snapshot = _make_snapshot(["NVDA"])
        stage2 = _Stage2Stub({"NVDA": _make_stage2_inputs("NVDA", with_earnings=True)})
        stage3 = _Stage3Stub({"NVDA": _bullish_stage3_inputs("NVDA")})
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=stage2,
            stage3_inputs_provider=stage3,
        )
        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_4 is not None
        assert c.stages.stage_4.result == "FAIL"
        assert "no Stage 4 inputs provider" in c.stages.stage_4.summary

    @pytest.mark.asyncio
    async def test_uv_alone_no_longer_admits_stage2_pass(self):
        """UV is evidence, not a catalyst — must combine with compression
        / date-known / sympathy to admit a Stage 2 PASS. UV-only inputs
        FAIL Stage 2 even though UV detector fires."""
        inputs = _make_stage2_inputs("NVDA", with_earnings=False)
        # Inject UV: 6× threshold, call-heavy.
        inputs.today_total_options_volume = 600
        inputs.avg_options_volume_30d = 100
        inputs.today_call_options_volume = 500
        inputs.today_put_options_volume = 100

        snapshot = _make_snapshot(["NVDA"])
        provider = _Stage2Stub({"NVDA": inputs})
        pipeline = ConvexPipeline(
            ConvexConfig(enabled=True),
            universe_snapshot=snapshot,
            stage2_inputs_provider=provider,
            as_of_date="2026-04-26",
        )

        result = await pipeline.run(universe_tickers=["NVDA"])
        c = result.candidates[0]
        assert c.stages.stage_2 is not None
        # UV alone — no real catalyst — Stage 2 FAILS.
        assert c.stages.stage_2.result == "FAIL"
        # UV detection still preserved on the candidate for Stage 4 reference.
        assert c.uv_detection is not None
        assert c.uv_detection.detected is True
        assert c.uv_detection.directional_skew == "call_heavy"
