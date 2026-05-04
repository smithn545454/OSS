"""Convex Mode — Stage 3: PL Pricing Pre-Screen.

Replaces the legacy IV/HV mispricing envelope with a Premium-Leverage
pre-screen. Walk-forward analysis on 3 months of paper trades showed
``PL >= 85`` alone is the strongest single signal; combined with momentum
and UV scanner alignment it lifts to ~65-70% win rate stably across
regime halves. The IV/HV envelope under-performed PL because PL also
incorporates contract structure (delta, premium, expected payoff).

Stage 3 here computes a *representative* PL using ATM-ish chain inputs
already available before contract selection so the pipeline can fail
fast. Stage 4 re-computes PL on the actual selected contract for the
tier-determining cutoff.

Inputs are kept narrow: current 30-day IV, IV history (for percentile),
and 20-day realized vol (for IV/RV ratio). Direction inference no longer
lives here — Stage 2 owns it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from app.convex.pl_pillar import compute_pl_score
from app.core.schemas import (
    ConvexConfig,
    ConvexStagePayload,
    IVHistory,
)

logger = logging.getLogger(__name__)


# Synthetic |delta| representative of the Stage 4 band centre. Used only
# in the Stage 3 pre-screen — Stage 4 recomputes PL on the actual contract.
_PRE_SCREEN_ABS_DELTA = 0.30


# ---------------------------------------------------------------------------
# IV-percentile helper (kept; used by both pre-screen and tests)
# ---------------------------------------------------------------------------


def compute_iv_percentile(
    current_iv: float,
    history: Sequence[IVHistory],
    field: str = "atm_iv",
) -> Optional[float]:
    """% of trailing-window observations strictly below current_iv.

    Returns None when fewer than 20 history records carry a usable value.
    """
    values = [getattr(h, field, None) for h in history]
    values = [v for v in values if v is not None and v > 0]
    if len(values) < 20:
        return None
    below = sum(1 for v in values if v < current_iv)
    return round((below / len(values)) * 100.0, 2)


def compute_iv_rv_ratio(
    current_iv: float, rv20: Optional[float]
) -> Optional[float]:
    """Current IV / 20-day realized volatility. Both expressed as decimals."""
    if rv20 is None or rv20 <= 0:
        return None
    return round(current_iv / rv20, 4)


# ---------------------------------------------------------------------------
# Inputs + integrator
# ---------------------------------------------------------------------------


@dataclass
class Stage3Inputs:
    """Per-ticker pre-computed inputs for Stage 3 PL pre-screen."""

    ticker: str
    current_iv_30d: Optional[float]
    iv_history: Sequence[IVHistory]
    rv20: Optional[float]


@dataclass
class Stage3Result:
    payload: ConvexStagePayload
    pl_pre_score: float


def evaluate_stage3(inputs: Stage3Inputs, config: ConvexConfig) -> Stage3Result:
    """Compute the representative PL pre-screen and PASS/FAIL.

    PASS when ``pl_pre_score >= config.pl_pre_screen_min`` (default 70).
    The tier-determining PL cutoffs (80 / 85) live in Stage 4 / tier
    mapping where the actual selected contract's delta is known.
    """
    if inputs.current_iv_30d is None:
        return Stage3Result(
            payload=ConvexStagePayload(
                stage=3,
                stage_name="PL Pricing Pre-Screen",
                result="FAIL",
                summary=(
                    f"{inputs.ticker}: no 30-day IV available — cannot "
                    "compute PL pre-screen."
                ),
            ),
            pl_pre_score=0.0,
        )

    iv_percentile = compute_iv_percentile(
        inputs.current_iv_30d, inputs.iv_history, field="iv_30d"
    )
    if iv_percentile is None:
        # Fall back to atm_iv-based history when iv_30d isn't backfilled.
        iv_percentile = compute_iv_percentile(
            inputs.current_iv_30d, inputs.iv_history, field="atm_iv"
        )

    iv_rv_ratio = compute_iv_rv_ratio(inputs.current_iv_30d, inputs.rv20)

    pl_score, subscores = compute_pl_score(
        iv=inputs.current_iv_30d,
        abs_delta=_PRE_SCREEN_ABS_DELTA,
        iv_percentile=iv_percentile,
        iv_rv_ratio=iv_rv_ratio,
    )

    passed = pl_score >= config.pl_pre_screen_min

    summary = (
        f"{inputs.ticker}: PL pre-screen {pl_score:.1f} "
        f"({'PASS' if passed else 'FAIL'} ≥ {config.pl_pre_screen_min:.1f})"
    )

    payload = ConvexStagePayload(
        stage=3,
        stage_name="PL Pricing Pre-Screen",
        result="PASS" if passed else "FAIL",
        summary=summary,
        criteria={
            "pl_pre_score": pl_score,
            "pl_pre_screen_min": config.pl_pre_screen_min,
            "subscores": subscores,
            "inputs": {
                "iv_30d": inputs.current_iv_30d,
                "iv_percentile": iv_percentile,
                "iv_rv_ratio": iv_rv_ratio,
                "abs_delta_proxy": _PRE_SCREEN_ABS_DELTA,
            },
        },
        strength=round(pl_score / 100.0, 4) if passed else 0.0,
        extras={"pl_pre_score": pl_score},
    )
    return Stage3Result(payload=payload, pl_pre_score=pl_score)
