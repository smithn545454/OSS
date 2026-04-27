"""Convex Mode — Stage 3: Volatility Mispricing.

Of the names with kinetic capability and a pending catalyst (Stage 2
PASS), which have options that are actually cheap relative to the setup?
Also infers a directional bias for Stage 4 contract selection.

Five metric gates (all must PASS):
    - IV Rank (per ticker) < 40
    - IV Percentile < 35
    - IV/HV ratio < 1.10
    - Term-structure shape — branched by catalyst type:
        * state-based: front-month IV must be ≤ 60-day IV (flat/contango)
        * date-known: backwardation acceptable but capped vs historical
    - Skew positioning — evaluated against the inferred direction
        * bullish thesis benefits from rich put skew
        * bearish thesis benefits from rich call skew

Direction inference combines price position relative to consolidation
boundaries with skew. When neither is clear the candidate is tagged as
a straddle candidate (handled separately in Stage 4).

Strength composite (within-tier ranking only, never displayed) is the
geometric mean of normalised metric distances from their floors.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional, Sequence

from app.core.schemas import (
    ConvexConfig,
    ConvexStagePayload,
    IVHistory,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-metric computations
# ---------------------------------------------------------------------------


def compute_iv_rank(
    current_iv: float,
    history: Sequence[IVHistory],
    field: str = "atm_iv",
) -> Optional[float]:
    """IV Rank: where current IV sits within the trailing min/max range.

    rank = 100 × (current - min) / (max - min)

    Returns None when fewer than 20 history records carry a usable
    value, mirroring the existing IV Percentile floor.
    """
    values = [getattr(h, field) for h in history]
    values = [v for v in values if v is not None and v > 0]
    if len(values) < 20:
        return None
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return None
    rank = 100.0 * (current_iv - lo) / (hi - lo)
    return max(0.0, min(100.0, round(rank, 2)))


def compute_iv_percentile(
    current_iv: float,
    history: Sequence[IVHistory],
    field: str = "atm_iv",
) -> Optional[float]:
    """% of trailing-window observations strictly below current_iv."""
    values = [getattr(h, field) for h in history]
    values = [v for v in values if v is not None and v > 0]
    if len(values) < 20:
        return None
    below = sum(1 for v in values if v < current_iv)
    return round((below / len(values)) * 100.0, 2)


def compute_iv_hv_ratio(
    current_iv: float, hv20: Optional[float]
) -> Optional[float]:
    """Current IV / 20-day realized volatility. Both expressed as decimals."""
    if hv20 is None or hv20 <= 0:
        return None
    return round(current_iv / hv20, 4)


# ---------------------------------------------------------------------------
# Term structure
# ---------------------------------------------------------------------------


@dataclass
class TermStructureAssessment:
    """Outcome of the term-structure check."""

    pass_: bool
    front_month_iv: Optional[float]
    sixty_day_iv: Optional[float]
    shape: str  # "contango" | "mild_contango" | "flat" | "mild_backwardation" | "backwardation"
    note: str


def _classify_term_shape(front: float, sixty: float) -> str:
    if sixty == 0:
        return "unknown"
    diff = front - sixty
    abs_diff_pct = abs(diff) / sixty
    if abs_diff_pct < 0.02:
        return "flat"
    if diff < 0:
        return "mild_contango" if abs_diff_pct < 0.10 else "contango"
    return "mild_backwardation" if abs_diff_pct < 0.10 else "backwardation"


def assess_term_structure(
    front_month_iv: Optional[float],
    sixty_day_iv: Optional[float],
    catalyst_type: Optional[str],
    historical_pre_event_backwardation: Optional[float] = None,
) -> TermStructureAssessment:
    """Evaluate term-structure shape with branching on catalyst type.

    Args:
        front_month_iv: ~30 DTE IV.
        sixty_day_iv: ~60 DTE IV.
        catalyst_type: ``"date_known"`` (earnings, FDA, etc.),
            ``"state_based"`` (compression / UV / sympathy), or ``None``
            when Stage 2 didn't classify.
        historical_pre_event_backwardation: optional reference value for
            this ticker's typical pre-event front-month-IV / 60-day-IV
            ratio. When provided we cap acceptable backwardation at 2×
            this reference per the source plan.

    For state-based catalysts (no scheduled event), backwardation means
    the market is already pricing the move and the gate FAILS. For
    date-known catalysts, backwardation is normal but excessive
    backwardation (>2× historical) means the move is already priced.
    """
    if front_month_iv is None or sixty_day_iv is None:
        # Fail-open per impact report: missing tenor data does not reject.
        return TermStructureAssessment(
            pass_=True,
            front_month_iv=front_month_iv,
            sixty_day_iv=sixty_day_iv,
            shape="unknown",
            note="Multi-tenor IV unavailable — gate failed open.",
        )

    shape = _classify_term_shape(front_month_iv, sixty_day_iv)

    if catalyst_type == "state_based":
        # Require flat or contango.
        if shape in ("contango", "mild_contango", "flat"):
            return TermStructureAssessment(
                pass_=True,
                front_month_iv=front_month_iv,
                sixty_day_iv=sixty_day_iv,
                shape=shape,
                note="Acceptable for state-based catalyst (no near-term move priced in).",
            )
        return TermStructureAssessment(
            pass_=False,
            front_month_iv=front_month_iv,
            sixty_day_iv=sixty_day_iv,
            shape=shape,
            note="Backwardation indicates the market is already pricing a near-term move.",
        )

    if catalyst_type == "date_known":
        # Backwardation expected; cap at 2× historical when reference present.
        if shape in ("contango", "mild_contango", "flat", "mild_backwardation"):
            return TermStructureAssessment(
                pass_=True,
                front_month_iv=front_month_iv,
                sixty_day_iv=sixty_day_iv,
                shape=shape,
                note="Acceptable for date-known catalyst.",
            )
        # Heavy backwardation — check magnitude if reference provided.
        ref = historical_pre_event_backwardation
        if ref is not None and ref > 0:
            current_backwardation = front_month_iv / sixty_day_iv
            if current_backwardation > 2 * ref:
                return TermStructureAssessment(
                    pass_=False,
                    front_month_iv=front_month_iv,
                    sixty_day_iv=sixty_day_iv,
                    shape=shape,
                    note=(
                        f"Backwardation {current_backwardation:.2f}× exceeds 2× "
                        f"historical pre-event ratio "
                        f"({ref:.2f}); move is "
                        "already priced in."
                    ),
                )
        return TermStructureAssessment(
            pass_=True,
            front_month_iv=front_month_iv,
            sixty_day_iv=sixty_day_iv,
            shape=shape,
            note="Backwardation acceptable for date-known catalyst.",
        )

    # Unknown catalyst type — fail open.
    return TermStructureAssessment(
        pass_=True,
        front_month_iv=front_month_iv,
        sixty_day_iv=sixty_day_iv,
        shape=shape,
        note="Catalyst type unknown — term-structure gate failed open.",
    )


# ---------------------------------------------------------------------------
# Skew + direction inference
# ---------------------------------------------------------------------------


@dataclass
class SkewAssessment:
    """Outcome of the skew alignment check."""

    pass_: bool
    put_25d_iv: Optional[float]
    call_25d_iv: Optional[float]
    skew_direction: str  # "put_skew_rich" | "call_skew_rich" | "balanced" | "unknown"
    alignment_with_thesis: str  # "favorable" | "neutral" | "unfavorable"
    note: str


def _classify_skew(
    put_iv: float, call_iv: float, balanced_threshold: float = 0.02
) -> str:
    """Classify 25Δ put vs call skew shape.

    ``balanced_threshold`` is the absolute fractional difference below
    which we treat the skew as balanced (default ±2%).
    """
    if put_iv <= 0 or call_iv <= 0:
        return "unknown"
    diff = (put_iv - call_iv) / ((put_iv + call_iv) / 2)
    if abs(diff) < balanced_threshold:
        return "balanced"
    return "put_skew_rich" if diff > 0 else "call_skew_rich"


def assess_skew(
    put_25d_iv: Optional[float],
    call_25d_iv: Optional[float],
    direction: str,
) -> SkewAssessment:
    """Evaluate skew alignment with the inferred directional thesis.

    Args:
        put_25d_iv: 25Δ put IV.
        call_25d_iv: 25Δ call IV.
        direction: ``"bullish"``, ``"bearish"``, or ``"ambiguous"`` (straddle).
    """
    if put_25d_iv is None or call_25d_iv is None:
        return SkewAssessment(
            pass_=True,
            put_25d_iv=put_25d_iv,
            call_25d_iv=call_25d_iv,
            skew_direction="unknown",
            alignment_with_thesis="neutral",
            note="25Δ skew unavailable — gate failed open.",
        )

    shape = _classify_skew(put_25d_iv, call_25d_iv)

    if direction == "ambiguous":
        # Straddle candidates don't care about skew alignment.
        return SkewAssessment(
            pass_=True,
            put_25d_iv=put_25d_iv,
            call_25d_iv=call_25d_iv,
            skew_direction=shape,
            alignment_with_thesis="neutral",
            note="Straddle candidate — skew alignment not gated.",
        )

    if direction == "bullish":
        if shape == "put_skew_rich":
            return SkewAssessment(
                pass_=True,
                put_25d_iv=put_25d_iv,
                call_25d_iv=call_25d_iv,
                skew_direction=shape,
                alignment_with_thesis="favorable",
                note="Bullish thesis benefits from rich put skew (calls relatively cheap).",
            )
        if shape == "balanced":
            return SkewAssessment(
                pass_=True,
                put_25d_iv=put_25d_iv,
                call_25d_iv=call_25d_iv,
                skew_direction=shape,
                alignment_with_thesis="neutral",
                note="Balanced skew — neutral for bullish thesis.",
            )
        # call_skew_rich
        return SkewAssessment(
            pass_=False,
            put_25d_iv=put_25d_iv,
            call_25d_iv=call_25d_iv,
            skew_direction=shape,
            alignment_with_thesis="unfavorable",
            note="Bullish thesis suffers from rich call skew (calls already expensive).",
        )

    # bearish
    if shape == "call_skew_rich":
        return SkewAssessment(
            pass_=True,
            put_25d_iv=put_25d_iv,
            call_25d_iv=call_25d_iv,
            skew_direction=shape,
            alignment_with_thesis="favorable",
            note="Bearish thesis benefits from rich call skew (puts relatively cheap).",
        )
    if shape == "balanced":
        return SkewAssessment(
            pass_=True,
            put_25d_iv=put_25d_iv,
            call_25d_iv=call_25d_iv,
            skew_direction=shape,
            alignment_with_thesis="neutral",
            note="Balanced skew — neutral for bearish thesis.",
        )
    return SkewAssessment(
        pass_=False,
        put_25d_iv=put_25d_iv,
        call_25d_iv=call_25d_iv,
        skew_direction=shape,
        alignment_with_thesis="unfavorable",
        note="Bearish thesis suffers from rich put skew (puts already expensive).",
    )


def infer_direction(
    price_position_pct: Optional[float],
    skew_shape: str,
) -> str:
    """Combine price position + skew to infer ``"bullish"``, ``"bearish"``,
    or ``"ambiguous"`` (straddle candidate).

    ``price_position_pct`` is the price's position within its recent
    consolidation range expressed as 0-100 (0 = at lower boundary, 100
    = at upper boundary). ``None`` means we don't know the position.

    Decision logic (matches source plan §7):
        - price > 70th percentile of range → bullish
        - price < 30th percentile of range → bearish
        - mid-range with rich put skew → bullish lean
        - mid-range with rich call skew → bearish lean
        - else ambiguous (straddle)
    """
    if price_position_pct is not None:
        if price_position_pct >= 70:
            return "bullish"
        if price_position_pct <= 30:
            return "bearish"

    # Mid-range or position unknown — lean on skew.
    if skew_shape == "put_skew_rich":
        return "bullish"
    if skew_shape == "call_skew_rich":
        return "bearish"
    return "ambiguous"


# ---------------------------------------------------------------------------
# Strength composite (for within-tier ranking, never displayed)
# ---------------------------------------------------------------------------


def composite_strength(
    iv_rank: Optional[float],
    iv_percentile: Optional[float],
    iv_hv_ratio: Optional[float],
    term_structure_score: float,
    skew_alignment_score: float,
    config: ConvexConfig,
) -> float:
    """Geometric mean of normalised metric scores (range 0..1).

    Per source plan §7 strength formula. None values are treated as 0
    (neutral / failing) so missing data never inflates the composite.
    """
    parts: list[float] = []
    if iv_rank is not None:
        parts.append(max(0.0, (config.vol_iv_rank_max - iv_rank) / config.vol_iv_rank_max))
    if iv_percentile is not None:
        parts.append(
            max(0.0, (config.vol_iv_percentile_max - iv_percentile) / config.vol_iv_percentile_max)
        )
    if iv_hv_ratio is not None:
        parts.append(
            max(0.0, (config.vol_iv_hv_ratio_max - iv_hv_ratio) / config.vol_iv_hv_ratio_max)
        )
    parts.append(term_structure_score)
    parts.append(skew_alignment_score)

    if not parts:
        return 0.0
    # Geometric mean, treating zeros as small to keep the result finite.
    log_sum = sum(math.log(max(p, 1e-9)) for p in parts)
    return math.exp(log_sum / len(parts))


# ---------------------------------------------------------------------------
# Inputs + integrator
# ---------------------------------------------------------------------------


@dataclass
class Stage3Inputs:
    """Per-ticker pre-computed inputs for Stage 3 evaluation."""

    ticker: str
    current_iv_30d: Optional[float]
    current_iv_60d: Optional[float]
    current_iv_25d_put: Optional[float]
    current_iv_25d_call: Optional[float]
    iv_history: Sequence[IVHistory]
    rv20: Optional[float]
    catalyst_type: Optional[str]  # From Stage 2 (date_known | state_based | ...)
    price_position_pct: Optional[float]  # 0-100, position within recent range
    historical_pre_event_backwardation: Optional[float] = None


@dataclass
class Stage3Result:
    payload: ConvexStagePayload
    direction: str  # "bullish" | "bearish" | "ambiguous"


def evaluate_stage3(inputs: Stage3Inputs, config: ConvexConfig) -> Stage3Result:
    """Run all five metric gates and infer direction.

    Returns the ConvexStagePayload plus the inferred direction so Stage 4
    can choose calls / puts / straddle leg structure.
    """
    iv_for_metrics = inputs.current_iv_30d or inputs.current_iv_25d_put or 0.0

    iv_rank = (
        compute_iv_rank(inputs.current_iv_30d, inputs.iv_history, field="iv_30d")
        if inputs.current_iv_30d is not None
        else None
    )
    if iv_rank is None and inputs.current_iv_30d is not None:
        # Fall back to atm_iv-based history when iv_30d hasn't been
        # backfilled for older records.
        iv_rank = compute_iv_rank(
            inputs.current_iv_30d, inputs.iv_history, field="atm_iv"
        )

    iv_percentile = (
        compute_iv_percentile(inputs.current_iv_30d, inputs.iv_history, field="iv_30d")
        if inputs.current_iv_30d is not None
        else None
    )
    if iv_percentile is None and inputs.current_iv_30d is not None:
        iv_percentile = compute_iv_percentile(
            inputs.current_iv_30d, inputs.iv_history, field="atm_iv"
        )

    iv_hv_ratio = compute_iv_hv_ratio(iv_for_metrics, inputs.rv20)

    # Skew + direction inference.
    skew_shape = "unknown"
    if (
        inputs.current_iv_25d_put is not None
        and inputs.current_iv_25d_call is not None
    ):
        skew_shape = _classify_skew(
            inputs.current_iv_25d_put, inputs.current_iv_25d_call
        )
    direction = infer_direction(inputs.price_position_pct, skew_shape)

    # Term structure.
    term = assess_term_structure(
        inputs.current_iv_30d,
        inputs.current_iv_60d,
        inputs.catalyst_type,
        inputs.historical_pre_event_backwardation,
    )

    # Skew alignment.
    skew = assess_skew(
        inputs.current_iv_25d_put, inputs.current_iv_25d_call, direction
    )

    # Gate evaluation.
    iv_rank_pass = iv_rank is not None and iv_rank < config.vol_iv_rank_max
    iv_pct_pass = (
        iv_percentile is not None and iv_percentile < config.vol_iv_percentile_max
    )
    iv_hv_pass = (
        iv_hv_ratio is not None and iv_hv_ratio < config.vol_iv_hv_ratio_max
    )

    all_passed = (
        iv_rank_pass
        and iv_pct_pass
        and iv_hv_pass
        and term.pass_
        and skew.pass_
    )

    # Strength composite — translate term + skew to scores.
    term_score = _term_score(term)
    skew_score = _skew_score(skew)
    strength = composite_strength(
        iv_rank, iv_percentile, iv_hv_ratio, term_score, skew_score, config
    )

    summary = _build_summary(
        inputs.ticker,
        all_passed,
        direction,
        iv_rank,
        iv_percentile,
        iv_hv_ratio,
        term,
        skew,
    )

    payload = ConvexStagePayload(
        stage=3,
        stage_name="Volatility Mispricing",
        result="PASS" if all_passed else "FAIL",
        summary=summary,
        criteria={
            "iv_rank": _metric_dict(
                iv_rank, config.vol_iv_rank_max, iv_rank_pass, op="lt"
            ),
            "iv_percentile": _metric_dict(
                iv_percentile, config.vol_iv_percentile_max, iv_pct_pass, op="lt"
            ),
            "iv_hv_ratio": _metric_dict(
                iv_hv_ratio, config.vol_iv_hv_ratio_max, iv_hv_pass, op="lt"
            ),
            "term_structure": {
                "front_month_iv": term.front_month_iv,
                "sixty_day_iv": term.sixty_day_iv,
                "shape": term.shape,
                "pass": term.pass_,
                "note": term.note,
            },
            "skew": {
                "put_25d_iv": skew.put_25d_iv,
                "call_25d_iv": skew.call_25d_iv,
                "skew_direction": skew.skew_direction,
                "alignment_with_thesis": skew.alignment_with_thesis,
                "pass": skew.pass_,
                "note": skew.note,
            },
        },
        strength=strength if all_passed else 0.0,
        extras={"directional_bias": direction},
    )
    return Stage3Result(payload=payload, direction=direction)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metric_dict(
    value: Optional[float], floor: float, passed: bool, op: str
) -> dict[str, object]:
    return {"value": value, "floor": floor, "pass": passed, "op": op}


def _term_score(t: TermStructureAssessment) -> float:
    if not t.pass_:
        return 0.0
    if t.shape in ("contango", "mild_contango"):
        return 1.0
    if t.shape == "flat":
        return 0.8
    if t.shape == "mild_backwardation":
        return 0.6
    return 0.5


def _skew_score(s: SkewAssessment) -> float:
    if not s.pass_:
        return 0.0
    if s.alignment_with_thesis == "favorable":
        return 1.0
    if s.alignment_with_thesis == "neutral":
        return 0.5
    return 0.2  # unfavorable but technically passing — degrade composite


def _build_summary(
    ticker: str,
    passed: bool,
    direction: str,
    iv_rank: Optional[float],
    iv_percentile: Optional[float],
    iv_hv_ratio: Optional[float],
    term: TermStructureAssessment,
    skew: SkewAssessment,
) -> str:
    if not passed:
        return f"{ticker}: vol mispricing gates not all met."
    parts: list[str] = []
    if iv_rank is not None:
        parts.append(f"IV Rank {iv_rank:.0f}")
    if iv_percentile is not None:
        parts.append(f"IVP {iv_percentile:.0f}")
    if iv_hv_ratio is not None:
        parts.append(f"IV/HV {iv_hv_ratio:.2f}")
    metrics_str = ", ".join(parts) if parts else "core metrics unavailable"
    return (
        f"{ticker}: options are cheap relative to setup ({metrics_str}). "
        f"Term structure {term.shape}; "
        f"skew {skew.skew_direction} "
        f"({skew.alignment_with_thesis} for {direction} thesis)."
    )
