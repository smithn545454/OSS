"""v5 HR conviction calculator.

Combines the matched archetype's Wilson-lower HR200 rate, the matcher's
fit score, and the regime alignment multiplier into a single 0–20 scale
HR conviction value.

Formula:

    hr_conviction = clamp(
        100 × Wilson_lower(P(MFE ≥ 200%)) × (fit / 100) × regime_alignment,
        0, 100,
    )

The output is bounded by the strongest archetype's Wilson lower bound.
With the current 12-archetype library, the maximum any single trade
can score is ~27 (UV_LOTTERY_DC_MID at 18% Wilson lower × fit=100 ×
regime=1.5). Realistic top-decile scores are 10–20.

This is by design: conviction = probability. A score of 14 means
"we estimate 14% chance this trade hits ≥200% MFE." If you want
score-of-75 semantics, look at P conviction (Phase 3) which captures
consistent profitability on a 0–100 scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.calibration.archetype_rates import RateEstimate
from app.calibration.regime import RegimeState, compute_regime_alignment
from app.core.schemas import ArchetypeConfig, V5CalibrationConfig
from app.pillars.models import ScoringContext
from app.v5.hr_matcher import match_hr_archetypes

# Sentinel for "no archetype matched"
NO_MATCH_CONVICTION = 0.0


@dataclass(frozen=True)
class HRConvictionResult:
    """Result of HR conviction computation for a single trade."""

    conviction: float                          # 0–100, clamped
    archetype_id: Optional[str]                # Matched archetype, or None
    archetype_fit: float                       # Matcher's fit score (0–100)
    p_point: float                             # Point estimate of P(HR200), [0, 1]
    p_lower: float                             # Wilson lower bound, [0, 1]
    p_upper: float                             # Wilson upper bound, [0, 1]
    n_trades: int                              # Effective sample size
    regime_alignment: float                    # Regime multiplier applied
    all_fits: dict[str, float] = field(default_factory=dict)


def compute_hr_conviction(
    ctx: ScoringContext,
    *,
    archetypes: ArchetypeConfig,
    rate_lookup: dict[str, RateEstimate],
    regime: RegimeState,
    pillar_scores: Optional[dict[str, float]] = None,
    calibration: Optional[V5CalibrationConfig] = None,
) -> HRConvictionResult:
    """Compute HR conviction for a single trade.

    Args:
        ctx: Scoring context (features, scanner, option type).
        archetypes: HR archetype library (typically ``policy.v5_hr_archetypes``).
        rate_lookup: Pre-computed Wilson bounds keyed by archetype_id.
            Built once per Lambda init by the rate-estimator job and
            refreshed every 15 minutes.
        regime: Current market regime snapshot.
        pillar_scores: Optional pillar-score dict for archetypes that
            reference ``dc_score`` / ``mp_score`` / ``ts_score``.
        calibration: V5 calibration parameters. If None, defaults are used.

    Returns:
        :class:`HRConvictionResult`. When no archetype matches, returns
        a zero-conviction result with ``archetype_id=None`` and
        ``regime_alignment=1.0`` (regime is computed even on misses for
        observability).
    """
    cal = calibration or V5CalibrationConfig()
    # Always compute regime — useful in logs even when no match
    regime_mult = compute_regime_alignment(
        regime,
        option_type=ctx.option_type,
        multiplier_min=cal.regime_multiplier_min,
        multiplier_max=cal.regime_multiplier_max,
    )

    match_result = match_hr_archetypes(ctx, archetypes, pillar_scores=pillar_scores)

    if match_result.best is None:
        return HRConvictionResult(
            conviction=NO_MATCH_CONVICTION,
            archetype_id=None,
            archetype_fit=0.0,
            p_point=0.0,
            p_lower=0.0,
            p_upper=0.0,
            n_trades=0,
            regime_alignment=regime_mult,
            all_fits=dict(match_result.all_fits),
        )

    archetype_id = match_result.best.archetype_id
    fit = match_result.best.fit

    # Look up the Wilson bounds. If the archetype isn't in the rates table
    # yet (e.g., newly added, no historical data), fall back to seed values
    # from the archetype definition.
    rate = rate_lookup.get(archetype_id)
    if rate is None:
        # Seed-fallback: use the historical_hr200_rate from the definition
        # but with a very wide CI to reflect the missing rolling estimate.
        seed = next(
            (a for a in archetypes.archetypes if a.archetype_id == archetype_id),
            None,
        )
        if seed is None:
            # Truly unknown archetype — return 0
            return HRConvictionResult(
                conviction=NO_MATCH_CONVICTION,
                archetype_id=archetype_id,
                archetype_fit=fit,
                p_point=0.0,
                p_lower=0.0,
                p_upper=0.0,
                n_trades=0,
                regime_alignment=regime_mult,
                all_fits=dict(match_result.all_fits),
            )
        # Use the seed rate as point estimate, halve it as conservative lower
        p_point = float(seed.historical_hr200_rate)
        p_lower = max(0.0, p_point * 0.5)
        p_upper = min(1.0, p_point * 1.5)
        n_trades = int(seed.historical_n)
    else:
        p_point = rate.point
        p_lower = rate.lower
        p_upper = rate.upper
        n_trades = rate.n_effective

    raw_conviction = 100.0 * p_lower * (fit / 100.0) * regime_mult
    conviction = max(0.0, min(100.0, raw_conviction))

    return HRConvictionResult(
        conviction=conviction,
        archetype_id=archetype_id,
        archetype_fit=fit,
        p_point=p_point,
        p_lower=p_lower,
        p_upper=p_upper,
        n_trades=n_trades,
        regime_alignment=regime_mult,
        all_fits=dict(match_result.all_fits),
    )
