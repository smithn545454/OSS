"""v5 P conviction calculator.

Combines the matched P-archetype's Wilson-lower WIN rate, normalized mean
P&L, matcher fit score, and regime alignment multiplier into a 0–100 scale
P conviction value.

Formula:

    p_conviction = clamp(
        100 × Wilson_lower(P_win) × normalize_pnl(mean_pnl) × (fit/100)
              × regime_alignment,
        0, 100,
    )

where ``normalize_pnl`` maps mean P&L into [0, 2]: floor (-50%) → 0,
midpoint (0%) → 1, ceiling (+50%) → 2 — see
:func:`app.calibration.archetype_rates.normalize_pnl_pct`.

Examples:

- BREAKDOWN_GRINDER: 66% win, +30% mean P&L, n=232
    Wilson_lower ≈ 0.60, pnl_norm = 1.60 (from 30% mean)
    Perfect match conviction: 100 × 0.60 × 1.60 × 1.0 × 1.0 ≈ 96

- REVALIDATION_QUALITY: 70% win, +47% mean P&L, n=112
    Wilson_lower ≈ 0.61, pnl_norm ≈ 1.94
    Perfect match conviction: 100 × 0.61 × 1.94 × 1.0 × 1.0 ≈ 118 → capped 100

- A trade matching no P archetype: conviction = 0

This scale is designed so that 50+ is a meaningfully profitable pattern
and 70+ is a strong grinder. Distinct from HR conviction (0–20 scale
where 10+ is exceptional).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.calibration.archetype_rates import (
    normalize_pnl_pct,
)
from app.calibration.regime import RegimeState, compute_regime_alignment
from app.core.schemas import ArchetypeConfig, V5CalibrationConfig
from app.pillars.models import ScoringContext
from app.v5.p_matcher import match_p_archetypes

# Sentinel
NO_MATCH_CONVICTION = 0.0


@dataclass(frozen=True)
class PConvictionResult:
    """Result of P conviction computation for a single trade."""

    conviction: float                          # 0–100, clamped
    archetype_id: Optional[str]                # Matched P-archetype, or None
    archetype_fit: float                       # Matcher's fit score (0–100)
    win_point: float                           # Point estimate of P(win), [0, 1]
    win_lower: float                           # Wilson lower bound, [0, 1]
    win_upper: float                           # Wilson upper bound, [0, 1]
    mean_pnl_pct: float                        # Mean P&L % used for normalization
    pnl_multiplier: float                      # Normalized P&L multiplier [0, 2]
    n_trades: int                              # Effective sample size
    regime_alignment: float                    # Regime multiplier applied
    all_fits: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PRateEstimate:
    """Extended rate estimate for P conviction (win rate + mean P&L).

    Runtime equivalent of RateEstimate but for P-archetype rates. Carries
    both the Wilson-bound win rate AND the observed mean P&L (since P
    conviction multiplies by the normalized mean). Phase 5 will populate
    this from a live oss-dev-archetype-rates table.
    """
    win_point: float
    win_lower: float
    win_upper: float
    mean_pnl_pct: float
    n_effective: int


def compute_p_conviction(
    ctx: ScoringContext,
    *,
    archetypes: ArchetypeConfig,
    rate_lookup: dict[str, PRateEstimate],
    regime: RegimeState,
    pillar_scores: Optional[dict[str, float]] = None,
    calibration: Optional[V5CalibrationConfig] = None,
) -> PConvictionResult:
    """Compute P conviction for a single trade.

    Args:
        ctx: Scoring context (features, scanner, option type).
        archetypes: P archetype library (typically ``policy.v5_p_archetypes``).
        rate_lookup: Pre-computed P-rate estimates keyed by archetype_id.
            Built once per Lambda init by the rate-estimator job and
            refreshed every 15 minutes.
        regime: Current market regime snapshot.
        pillar_scores: Optional pillar-score dict for archetypes that
            reference ``dc_score`` / ``mp_score`` / ``ts_score``.
        calibration: V5 calibration parameters. Defaults used if None.

    Returns:
        :class:`PConvictionResult`. When no archetype matches, returns
        a zero-conviction result with ``archetype_id=None`` and
        ``regime_alignment`` still computed (for observability).
    """
    cal = calibration or V5CalibrationConfig()
    regime_mult = compute_regime_alignment(
        regime,
        option_type=ctx.option_type,
        multiplier_min=cal.regime_multiplier_min,
        multiplier_max=cal.regime_multiplier_max,
    )

    match_result = match_p_archetypes(ctx, archetypes, pillar_scores=pillar_scores)

    if match_result.best is None:
        return PConvictionResult(
            conviction=NO_MATCH_CONVICTION,
            archetype_id=None,
            archetype_fit=0.0,
            win_point=0.0,
            win_lower=0.0,
            win_upper=0.0,
            mean_pnl_pct=0.0,
            pnl_multiplier=0.0,
            n_trades=0,
            regime_alignment=regime_mult,
            all_fits=dict(match_result.all_fits),
        )

    archetype_id = match_result.best.archetype_id
    fit = match_result.best.fit

    rate = rate_lookup.get(archetype_id)
    if rate is None:
        # Seed fallback: use archetype definition's historical_win_rate
        # and historical_mean_pnl_pct, with a conservative half-discount on
        # the win rate to reflect the missing Wilson lower.
        seed = next(
            (a for a in archetypes.archetypes if a.archetype_id == archetype_id),
            None,
        )
        if seed is None:
            return PConvictionResult(
                conviction=NO_MATCH_CONVICTION,
                archetype_id=archetype_id,
                archetype_fit=fit,
                win_point=0.0,
                win_lower=0.0,
                win_upper=0.0,
                mean_pnl_pct=0.0,
                pnl_multiplier=0.0,
                n_trades=0,
                regime_alignment=regime_mult,
                all_fits=dict(match_result.all_fits),
            )
        win_point = float(seed.historical_win_rate)
        # Conservative floor: 0.8 × point estimate (reflects missing Wilson data)
        win_lower = max(0.0, win_point * 0.8)
        win_upper = min(1.0, win_point * 1.1)
        mean_pnl = float(seed.historical_mean_pnl_pct)
        n_trades = int(seed.historical_n)
    else:
        win_point = rate.win_point
        win_lower = rate.win_lower
        win_upper = rate.win_upper
        mean_pnl = rate.mean_pnl_pct
        n_trades = rate.n_effective

    pnl_mult = normalize_pnl_pct(
        mean_pnl,
        floor_pct=cal.pnl_normalize_floor_pct,
        ceiling_pct=cal.pnl_normalize_ceiling_pct,
    )

    raw_conviction = 100.0 * win_lower * pnl_mult * (fit / 100.0) * regime_mult
    conviction = max(0.0, min(100.0, raw_conviction))

    return PConvictionResult(
        conviction=conviction,
        archetype_id=archetype_id,
        archetype_fit=fit,
        win_point=win_point,
        win_lower=win_lower,
        win_upper=win_upper,
        mean_pnl_pct=mean_pnl,
        pnl_multiplier=pnl_mult,
        n_trades=n_trades,
        regime_alignment=regime_mult,
        all_fits=dict(match_result.all_fits),
    )
