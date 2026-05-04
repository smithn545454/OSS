"""Convex Mode pipeline orchestrator (Phase 1 scaffold).

This module wires the four Convex stages into a single pipeline. In Phase 1
all stage logic is stubbed: each stage returns a ConvexStagePayload with
``result="FAIL"`` and a stub summary, so the data contract between stages is
exercised end-to-end without producing real signals.

Stage logic is filled in by subsequent phases:
    - Phase 2: stage1_universe.py — Kinetic Universe Construction
    - Phase 3: stage2_catalyst.py — Catalyst Layer
    - Phase 4: stage3_volatility.py — Volatility Mispricing
    - Phase 5: stage4_contract.py — Contract Selection
    - Phase 6: tier.py — Tier assignment + final Decision emission

Until those phases land, ConvexPipeline.run() returns an empty result
without writing to DynamoDB. The pipeline is gated by
``policy_config.convex.enabled`` (default False).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol
from uuid import uuid4

from app.convex._types import Tier
from app.convex.stage2_catalyst import (
    Stage2Inputs,
    UVDetection,
    evaluate_stage2,
)
from app.convex.stage3_volatility import (
    Stage3Inputs,
    evaluate_stage3,
)
from app.convex.stage4_contract import (
    ConvexContractCandidate,
    Stage4Inputs,
    evaluate_stage4,
)
from app.convex.tier import assign_tier, within_tier_composite
from app.core.schemas import (
    ConvexConfig,
    ConvexStagePayload,
    ConvexStagesPayload,
    ConvexUniverseEntry,
    ConvexUniverseSnapshot,
)

logger = logging.getLogger(__name__)


class Stage2InputsProvider(Protocol):
    """Protocol for fetching per-ticker Stage 2 inputs.

    The daily-pipeline implementation fetches:
        - Trailing 252 OHLCV bars from PriceHistoryTable
        - Catalyst calendar entries from CatalystCalendarTable
        - Today's options chain volume from a live Polygon snapshot
        - Trailing-30d options volume from OIHistory aggregation
        - Peer earnings reactions from EarningsHistory
    Tests inject a stub that returns a Stage2Inputs directly.
    """

    async def fetch(
        self, ticker: str, sector: Optional[str], today_iso: str
    ) -> Optional[Stage2Inputs]:
        ...


class Stage3InputsProvider(Protocol):
    """Protocol for fetching per-ticker Stage 3 inputs.

    The daily-pipeline implementation fetches:
        - Latest IVHistory for current iv_30d
        - Trailing 252-day IVHistory for IV Percentile
        - HV20 (rv20) derived from PriceHistoryTable closes
    Stage 3 is now a PL pricing pre-screen — no skew, no term structure,
    no direction inference (direction is set by Stage 2).
    """

    async def fetch(
        self,
        ticker: str,
        today_iso: str,
    ) -> Optional[Stage3Inputs]:
        ...


class Stage4InputsProvider(Protocol):
    """Protocol for fetching per-ticker Stage 4 inputs.

    The daily-pipeline implementation fetches:
        - Live options chain via Polygon (filtered to DTE band)
        - Underlying price (from chain snapshot or live quote)
        - Measured-move estimates (from Stage 2 compression details)
        - Historical event-move estimates (from earnings_history)
    """

    async def fetch(
        self,
        ticker: str,
        direction: str,
        catalyst_type: Optional[str],
        catalyst_date_iso: Optional[str],
        uv_directional_skew: Optional[str],
        today_iso: str,
    ) -> Optional[Stage4Inputs]:
        ...


@dataclass
class ConvexCandidate:
    """In-flight pipeline candidate as it advances stage by stage."""

    ticker: str
    stages: ConvexStagesPayload = field(default_factory=ConvexStagesPayload)
    direction: Optional[str] = None  # "bullish" | "bearish" | "ambiguous"
    smart_money_confirmation: bool = False
    tier: Optional[Tier] = None
    composite_strength: Optional[float] = None
    # UV detection from Stage 2 is preserved on the candidate so Stage 4 can
    # set the Smart Money Confirmation flag when the directional skew aligns
    # with the chosen thesis.
    uv_detection: Optional[UVDetection] = None
    # Sector copied from the kinetic-universe entry so Stage 2's sympathy
    # detector can filter peer reactions without re-querying.
    sector: Optional[str] = None
    # Stage 4 outputs preserved on the candidate for downstream tier
    # assignment and Decision emission (Phase 6).
    selected_call: Optional[ConvexContractCandidate] = None
    selected_put: Optional[ConvexContractCandidate] = None
    # Catalyst date for Stage 4's post-event DTE buffer rule (Phase 6
    # populates this from the Stage 2 date-known detection).
    catalyst_date_iso: Optional[str] = None
    # Carried forward from Stage 3 PL pre-screen — Stage 4 reuses these
    # without re-fetching IVHistory.
    iv_percentile_for_pl: Optional[float] = None
    iv_rv_ratio_for_pl: Optional[float] = None
    # UV signal looked up at tier-assignment time (production UV scanner
    # GSI). Carried onto the candidate so the downstream finaliser doesn't
    # double-fetch.
    uv_signal_for_tier: Optional[object] = None  # UVSignal at runtime

    @property
    def advanced_to_stage(self) -> int:
        """Highest stage this candidate has passed (0 if dropped at Stage 1)."""
        passed = 0
        for n, payload in enumerate(
            (self.stages.stage_1, self.stages.stage_2, self.stages.stage_3, self.stages.stage_4),
            start=1,
        ):
            if payload is None or payload.result != "PASS":
                break
            passed = n
        return passed


@dataclass
class ConvexPipelineResult:
    """Result of a single Convex pipeline daily run."""

    run_id: str
    started_at: str
    completed_at: Optional[str] = None
    universe_size: int = 0
    stage2_advancers: int = 0
    stage3_advancers: int = 0
    stage4_advancers: int = 0
    tier_a_count: int = 0
    tier_b_count: int = 0
    tier_c_count: int = 0
    candidates: list[ConvexCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ConvexPipeline:
    """Orchestrator for the four Convex Mode stages.

    Stages 1 and 2 are implemented; Stages 3-4 remain stubbed pending
    later phases.
    """

    def __init__(
        self,
        config: ConvexConfig,
        universe_snapshot: Optional[ConvexUniverseSnapshot] = None,
        stage2_inputs_provider: Optional[Stage2InputsProvider] = None,
        stage3_inputs_provider: Optional[Stage3InputsProvider] = None,
        stage4_inputs_provider: Optional[Stage4InputsProvider] = None,
        as_of_date: Optional[str] = None,
    ) -> None:
        self.config = config
        # The pipeline consults a pre-built kinetic-universe snapshot; the
        # daily run injects the latest snapshot read from
        # ConvexUniverseSnapshotTable. When ``None`` every Stage 1 gate fails.
        self._universe_snapshot = universe_snapshot
        self._universe_index: dict[str, ConvexUniverseEntry] = {
            e.ticker: e for e in (universe_snapshot.tickers if universe_snapshot else [])
        }
        # Per-ticker stage inputs fetched via these providers. When a
        # provider is ``None`` the corresponding stage always FAILs with an
        # explanatory message.
        self._stage2_provider = stage2_inputs_provider
        self._stage3_provider = stage3_inputs_provider
        self._stage4_provider = stage4_inputs_provider
        self._as_of_date = as_of_date or datetime.now(timezone.utc).date().isoformat()

    async def run(
        self,
        universe_tickers: Optional[list[str]] = None,
        run_id: Optional[str] = None,
    ) -> ConvexPipelineResult:
        """Run all four stages for the given universe.

        Args:
            universe_tickers: Tickers to evaluate. When omitted, defaults to
                the snapshot's tickers.
            run_id: Optional run ID for telemetry; UUID generated otherwise.
        """
        run_id = run_id or str(uuid4())
        if universe_tickers is None and self._universe_snapshot is not None:
            universe_tickers = [e.ticker for e in self._universe_snapshot.tickers]

        result = ConvexPipelineResult(
            run_id=run_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            universe_size=len(universe_tickers or []),
        )

        if not self.config.enabled:
            logger.info("Convex pipeline disabled (config.enabled=False); skipping.")
            result.completed_at = datetime.now(timezone.utc).isoformat()
            return result

        if not universe_tickers:
            logger.info("Convex pipeline: empty universe; nothing to evaluate.")
            result.completed_at = datetime.now(timezone.utc).isoformat()
            return result

        logger.info(
            "Convex pipeline run %s starting on %d tickers",
            run_id, len(universe_tickers),
        )

        for ticker in universe_tickers:
            candidate = ConvexCandidate(ticker=ticker)
            # Carry sector forward from the snapshot so Stage 2 sympathy
            # detection can run without re-querying.
            entry = self._universe_index.get(ticker)
            if entry is not None:
                candidate.sector = entry.sector
            # ConvexStagesPayload is frozen (OSSBaseModel.frozen=True), so we
            # rebuild the payload via model_copy as each stage completes.
            candidate.stages = candidate.stages.model_copy(
                update={"stage_1": await self._stage1(ticker)}
            )
            if candidate.advanced_to_stage < 1:
                result.candidates.append(candidate)
                continue

            candidate.stages = candidate.stages.model_copy(
                update={"stage_2": await self._stage2(candidate)}
            )
            if candidate.advanced_to_stage < 2:
                result.candidates.append(candidate)
                continue
            result.stage2_advancers += 1

            candidate.stages = candidate.stages.model_copy(
                update={"stage_3": await self._stage3(candidate)}
            )
            if candidate.advanced_to_stage < 3:
                result.candidates.append(candidate)
                continue
            result.stage3_advancers += 1

            candidate.stages = candidate.stages.model_copy(
                update={"stage_4": await self._stage4(candidate)}
            )
            if candidate.advanced_to_stage < 4:
                result.candidates.append(candidate)
                continue
            result.stage4_advancers += 1

            # Tier assignment + within-tier composite. The UV scanner GSI
            # is consulted here so Tier A (which requires UV detected) can
            # be tallied accurately. The daily-pipeline Lambda handler will
            # call finalise_candidate with the same uv_signal to emit the
            # Decision.
            uv_signal = await self._lookup_uv_for_tier(candidate)
            candidate.uv_signal_for_tier = uv_signal
            tier = assign_tier(candidate, self.config, uv_signal=uv_signal)
            if tier is not None:
                candidate.tier = tier
                candidate.composite_strength = within_tier_composite(candidate)
                if tier == Tier.A:
                    result.tier_a_count += 1
                elif tier == Tier.B:
                    result.tier_b_count += 1
                else:
                    result.tier_c_count += 1
            result.candidates.append(candidate)

        result.completed_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            "Convex pipeline run %s done: u=%d s2=%d s3=%d s4=%d (a=%d b=%d c=%d)",
            run_id,
            result.universe_size,
            result.stage2_advancers,
            result.stage3_advancers,
            result.stage4_advancers,
            result.tier_a_count,
            result.tier_b_count,
            result.tier_c_count,
        )
        return result

    # ---- Stage stubs ------------------------------------------------------

    async def _stage1(self, ticker: str) -> ConvexStagePayload:
        """Stage 1: Kinetic Universe membership check.

        The daily Convex pipeline does not re-run universe gates each day —
        that's the monthly UniverseConstructor's job. Here we look up the
        ticker in the snapshot loaded at pipeline construction time and
        record a PASS with the cached strength inputs, or FAIL when the
        ticker isn't in the universe.
        """
        if self._universe_snapshot is None:
            return ConvexStagePayload(
                stage=1,
                stage_name="Kinetic Universe",
                result="FAIL",
                summary=(
                    f"{ticker}: no universe snapshot loaded — run "
                    "UniverseConstructor before invoking the daily pipeline."
                ),
            )

        entry = self._universe_index.get(ticker)
        if entry is None:
            return ConvexStagePayload(
                stage=1,
                stage_name="Kinetic Universe",
                result="FAIL",
                summary=f"{ticker} is not in the current kinetic universe.",
            )

        return ConvexStagePayload(
            stage=1,
            stage_name="Kinetic Universe",
            result="PASS",
            summary=(
                f"{ticker} is a kinetic-universe member "
                f"(snapshot {self._universe_snapshot.snapshot_date})."
            ),
            strength_inputs={
                "tail_event_count_252d": entry.tail_event_count_252d,
                "hv_regime_ratio": entry.hv_regime_ratio,
                "historical_max_30d_move_pct": entry.historical_max_30d_move_pct,
                "avg_options_volume_30d": entry.avg_options_volume_30d,
                "sector": entry.sector,
                "market_cap": entry.market_cap,
            },
        )

    async def _stage2(self, candidate: ConvexCandidate) -> ConvexStagePayload:
        """Stage 2: Catalyst Layer — date-known + compression + UV + sympathy.

        Fetches per-ticker inputs via the injected provider, then runs the
        four detectors. UV detection is preserved on the candidate so
        Stage 4 can flag Smart Money Confirmation.
        """
        if self._stage2_provider is None:
            return ConvexStagePayload(
                stage=2,
                stage_name="Catalyst Layer",
                result="FAIL",
                summary=(
                    f"{candidate.ticker}: no Stage 2 inputs provider "
                    "configured — daily-pipeline wiring incomplete."
                ),
            )

        inputs = await self._stage2_provider.fetch(
            ticker=candidate.ticker,
            sector=candidate.sector,
            today_iso=self._as_of_date,
        )
        if inputs is None:
            return ConvexStagePayload(
                stage=2,
                stage_name="Catalyst Layer",
                result="FAIL",
                summary=(
                    f"{candidate.ticker}: data unavailable for Stage 2 "
                    "(missing price-history or catalyst calendar entries)."
                ),
            )

        payload, detections = evaluate_stage2(inputs, self._as_of_date, self.config)
        # Preserve UV detection on the candidate (used by Stage 4's smart
        # money flag derived from the live chain skew).
        uv = detections.get("unusual_volume")
        if isinstance(uv, UVDetection):
            candidate.uv_detection = uv
        # Stage 2 now owns direction resolution (momentum + UV skew). Carry
        # the resolved direction onto the candidate so Stages 3/4 read it.
        direction = detections.get("direction")
        if isinstance(direction, str):
            candidate.direction = direction
        # Propagate the date-known catalyst's date so Stage 4 can apply the
        # post-event +14 DTE buffer rule.
        date_known = detections.get("date_known")
        if date_known is not None:
            event_date = getattr(date_known, "event_date", None)
            if event_date:
                candidate.catalyst_date_iso = event_date
        return payload

    async def _stage3(self, candidate: ConvexCandidate) -> ConvexStagePayload:
        """Stage 3: PL Pricing Pre-Screen.

        Computes a representative PL using ATM-ish chain inputs so the
        pipeline can fail fast before Stage 4 selects a contract. Direction
        is no longer inferred here — Stage 2 owns that.
        """
        if self._stage3_provider is None:
            return ConvexStagePayload(
                stage=3,
                stage_name="PL Pricing Pre-Screen",
                result="FAIL",
                summary=(
                    f"{candidate.ticker}: no Stage 3 inputs provider "
                    "configured — daily-pipeline wiring incomplete."
                ),
            )

        inputs = await self._stage3_provider.fetch(
            ticker=candidate.ticker,
            today_iso=self._as_of_date,
        )
        if inputs is None:
            return ConvexStagePayload(
                stage=3,
                stage_name="PL Pricing Pre-Screen",
                result="FAIL",
                summary=(
                    f"{candidate.ticker}: data unavailable for Stage 3 "
                    "(missing IV history or 30-day IV)."
                ),
            )

        result = evaluate_stage3(inputs, self.config)
        # Preserve the IV-percentile / IV-RV ratio computed during the
        # pre-screen so Stage 4 can reuse them for the per-contract PL
        # recompute without re-fetching IVHistory.
        candidate.iv_percentile_for_pl = (
            result.payload.criteria.get("inputs", {}).get("iv_percentile")
            if result.payload.criteria
            else None
        )
        candidate.iv_rv_ratio_for_pl = (
            result.payload.criteria.get("inputs", {}).get("iv_rv_ratio")
            if result.payload.criteria
            else None
        )
        return result.payload

    async def _stage4(self, candidate: ConvexCandidate) -> ConvexStagePayload:
        """Stage 4: Contract Selection — strike + DTE + liquidity + Smart Money.

        Pulls live chain inputs via the injected provider, runs strike
        selection against the expected-move terminus, validates liquidity,
        and sets the Smart Money Confirmation flag when Stage 2 UV
        directional skew aligns with the chosen thesis.
        """
        if self._stage4_provider is None:
            return ConvexStagePayload(
                stage=4,
                stage_name="Contract Selection",
                result="FAIL",
                summary=(
                    f"{candidate.ticker}: no Stage 4 inputs provider "
                    "configured — daily-pipeline wiring incomplete."
                ),
            )

        direction = candidate.direction or "ambiguous"
        catalyst_type: Optional[str] = None
        if candidate.stages.stage_2 is not None:
            catalyst_type = candidate.stages.stage_2.extras.get(
                "selected_catalyst_type"
            )
        uv_skew: Optional[str] = None
        if candidate.uv_detection is not None and candidate.uv_detection.detected:
            uv_skew = candidate.uv_detection.directional_skew

        inputs = await self._stage4_provider.fetch(
            ticker=candidate.ticker,
            direction=direction,
            catalyst_type=catalyst_type,
            catalyst_date_iso=candidate.catalyst_date_iso,
            uv_directional_skew=uv_skew,
            today_iso=self._as_of_date,
        )
        if inputs is None:
            return ConvexStagePayload(
                stage=4,
                stage_name="Contract Selection",
                result="FAIL",
                summary=(
                    f"{candidate.ticker}: data unavailable for Stage 4 "
                    "(no chain snapshot or expected-move estimate)."
                ),
            )
        # Forward Stage 3's IV-percentile / IV-RV ratio so Stage 4 can
        # recompute the PL pillar on the actual selected contract.
        inputs.iv_percentile_for_pl = candidate.iv_percentile_for_pl
        inputs.iv_rv_ratio_for_pl = candidate.iv_rv_ratio_for_pl

        result = evaluate_stage4(inputs, self.config)
        if result.payload.result == "PASS":
            candidate.selected_call = result.selected_call
            candidate.selected_put = result.selected_put
            candidate.smart_money_confirmation = result.smart_money_confirmation
        return result.payload

    async def _lookup_uv_for_tier(self, candidate: ConvexCandidate):
        """Look up the production UV scanner GSI for tier-A determination.

        Failures are logged and treated as "no UV signal" — the candidate
        can still earn Tier B/C without UV. Imported lazily so test
        environments without boto/dynamodb don't fail at import.
        """
        try:
            from app.convex.uv_lookup import lookup_uv_signal
            return await lookup_uv_signal(candidate.ticker)
        except Exception as exc:
            logger.warning(
                "UV lookup failed for %s during tier assignment: %s",
                candidate.ticker,
                exc,
            )
            return None
