"""Move Potential Pillar (Policy v4.0.0).

Measures the probability and magnitude of a significant price move during
the option's life. Middle exponent (0.35) in the v4 geometric-mean composite —
strong directional conviction without a catalyst or volatility expansion
won't clear the Sharpshooter bar.

Subscores (weights must sum to 1.0 in the active policy):

- **Move Trigger** (0.35) — via ``move_trigger_score`` derived from
  ``days_to_earnings`` and ``dte``. Peaks when a catalyst falls in the
  option's window. A breakout (near-pivot) with expanding volatility is
  a soft fallback trigger.
- **Historical Post-Event Move Magnitude** (0.20) — via
  ``historical_move_magnitude`` (mean of last 4 quarterly 1-day absolute
  post-earnings returns, in percent).
- **IV/RV Ratio** (0.15) — via ``iv_rv_ratio``. Values << 1 (IV cheap
  vs realized) score best.
- **Volatility Regime** (0.15) — via ``bb_width_percentile``. Low BB
  width percentile = compressed range, higher move potential.
- **Expected vs Required Move** (0.15) — via ``expected_vs_required``
  (computed ratio expected_move / required_move). Values >> 1 score best.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.schemas import PillarConfigV2, PillarId
from app.pillars.composite import apply_v4_rules, count_available
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.scoring_engine import score_pillar

PILLAR_ID = PillarId.MOVE_POTENTIAL


def compute_move_potential_pillar(
    ctx: ScoringContext,
    config: PillarConfigV2,
) -> PillarResult:
    """Compute the Move Potential pillar score.

    Applies the v4 min-subscore rule (< 3 subscores available → score = 0,
    auto-REJECTs via geometric mean).
    """
    if config.pillar_id != PILLAR_ID:
        raise ValueError(
            f"Expected pillar_id={PILLAR_ID}, got {config.pillar_id}"
        )

    accessor = _MovePotentialAccessor(ctx)
    raw_score, subscores = score_pillar(config, accessor)
    pillar_score = apply_v4_rules(raw_score, subscores)
    tags = _generate_tags(ctx, subscores, pillar_score)

    return PillarResult(
        pillar_id=PILLAR_ID,
        evaluation_id=ctx.evaluation_id,
        score=pillar_score,
        subscores=subscores,
        tags=tags,
    )


# ============================================================================
# Accessor — exposes derived feature values to the scoring engine
# ============================================================================


class _MovePotentialAccessor:
    """Translates ScoringContext into feature_field names this pillar reads."""

    def __init__(self, ctx: ScoringContext) -> None:
        self._ctx = ctx

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name, None)

    @property
    def move_trigger_score(self) -> Optional[float]:
        return _move_trigger_score(self._ctx)

    @property
    def historical_move_magnitude(self) -> Optional[float]:
        # The caller may gate on historical_move_confidence — we still return
        # the raw magnitude and let policy breakpoints weight it.
        return self._ctx.historical_move_magnitude

    @property
    def iv_rv_ratio(self) -> Optional[float]:
        return self._ctx.iv_rv_ratio

    @property
    def bb_width_percentile(self) -> Optional[float]:
        return self._ctx.bb_width_percentile

    @property
    def expected_vs_required(self) -> Optional[float]:
        return _expected_vs_required_ratio(self._ctx)


# ============================================================================
# Derived feature computations
# ============================================================================


def _move_trigger_score(ctx: ScoringContext) -> Optional[float]:
    """Score the presence and timing of a move trigger during the option's life.

    - Earnings catalyst in DTE window (days_to_earnings <= dte and >= 0)
      with tight timing (catalyst not too early): highest scores.
    - No earnings in window but underlying near a breakout pivot with
      compressed BB width: moderate score.
    - Neither: low score.
    """
    dte = ctx.dte
    dte_earnings = ctx.days_to_earnings

    if dte_earnings is not None and dte is not None:
        if dte_earnings < 0:
            # Earnings already passed — no catalyst benefit.
            pass
        elif dte_earnings <= dte:
            # Catalyst hits before expiry.
            # Best window: catalyst in 5..21 days — enough IV to sell into /
            # trade the move without rushing; too-early = rushed, too-late
            # near expiry = theta drags.
            if 5 <= dte_earnings <= 21:
                return 90.0
            if dte_earnings <= 4:
                return 70.0
            if dte_earnings <= 35:
                return 75.0
            return 60.0
        elif dte_earnings - dte <= 7:
            # Catalyst just after expiry — some IV lift but no direct play.
            return 45.0

    # No earnings catalyst — fall back to technical breakout trigger.
    close = ctx.close
    bullish = ctx.option_type == "CALL"
    pivot = ctx.high_52w if bullish else ctx.low_52w
    bb_pct = ctx.bb_width_percentile

    if close is not None and pivot is not None and pivot > 0:
        proximity = abs(close - pivot) / pivot * 100.0
        tight_range = bb_pct is not None and bb_pct <= 30.0
        if proximity <= 3.0 and tight_range:
            return 70.0
        if proximity <= 3.0:
            return 55.0
        if tight_range:
            return 45.0

    # No catalyst, no breakout signal — return None so weight redistributes.
    return None


def _expected_vs_required_ratio(ctx: ScoringContext) -> Optional[float]:
    """Expected move / required move ratio.

    Breakeven points at ratio=1.0 (break-even move). Values > 1.0 mean the
    1-sigma expected move reaches beyond breakeven. ratio < 1.0 means the
    expected move doesn't reach breakeven — the contract is unlikely to
    profit on time alone.
    """
    required = ctx.required_move_pct
    expected = ctx.expected_move_pct
    if not required or not expected or required <= 0:
        return None
    return expected / required


# ============================================================================
# Tags
# ============================================================================


def _generate_tags(
    ctx: ScoringContext,
    subscores: list[Subscore],
    pillar_score: float,
) -> list[str]:
    tags: list[str] = []

    if pillar_score == 0.0:
        tags.append("INSUFFICIENT_DATA")
        return tags

    if count_available(subscores) < 5:
        tags.append("PARTIAL_COVERAGE")

    dte_e = ctx.days_to_earnings
    if dte_e is not None and ctx.dte and 0 <= dte_e <= ctx.dte:
        tags.append("CATALYST_IN_WINDOW")

    if ctx.iv_rv_ratio is not None:
        if ctx.iv_rv_ratio < 0.9:
            tags.append("CHEAP_IV_EXPANSION")
        elif ctx.iv_rv_ratio > 1.3:
            tags.append("EXPENSIVE_IV")

    if ctx.bb_width_percentile is not None:
        if ctx.bb_width_percentile <= 20:
            tags.append("VOLATILITY_COMPRESSION")
        elif ctx.bb_width_percentile >= 80:
            tags.append("VOLATILITY_EXPANSION")

    ratio = _expected_vs_required_ratio(ctx)
    if ratio is not None and ratio >= 1.3:
        tags.append("EXPECTED_MOVE_EXCEEDS_REQUIRED")

    if ctx.historical_move_confidence is not None and ctx.historical_move_confidence < 2:
        tags.append("LOW_HISTORICAL_CONFIDENCE")

    return tags
