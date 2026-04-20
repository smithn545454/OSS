"""v5 pipeline orchestrator — runs archetypes + GBM + conviction combine.

Bundles the per-evaluation v5 scoring work into a single function that
:mod:`app.decision.stage` can call once per evaluation. Produces all 17
v5 fields as a dict, ready to denormalize into :class:`Decision`.

Kept separate from ``hr_conviction.py`` / ``p_conviction.py`` because
those are focused on a single axis each. This module is the axis
combiner — including the GBM max-merge and v5 verdict derivation.

Phase 5 wiring:
  stage.py calls :func:`compute_v5_envelope` with a ScoringContext,
  pillar scores, and the active :class:`PolicyConfig`. The envelope
  then rides through :func:`DecisionCalculator.compute_decisions_batch`
  via the ``v5_results`` parameter, populating Decision fields.

When ``policy.v5_active`` is False, stage.py never calls this function
and all v5 fields remain None — production behavior is unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.calibration.archetype_rates import RateEstimate
from app.calibration.regime import RegimeState
from app.core.schemas import PolicyConfig, QualityTier, Verdict
from app.v5.gbm_scorer import (
    GBMScoreResult,
    build_feature_dict_from_context,
    score_trade,
)
from app.v5.hr_conviction import HRConvictionResult, compute_hr_conviction
from app.v5.p_conviction import PConvictionResult, PRateEstimate, compute_p_conviction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class V5Envelope:
    """Complete per-evaluation v5 scoring output.

    Produced by :func:`compute_v5_envelope`, consumed by the decision
    calculator when threading v5 fields onto a :class:`Decision`.
    """

    # Final convictions (post-GBM-merge, clamped [0, 100])
    hr_conviction: float
    p_conviction: float

    # HR axis provenance
    hr_archetype_matched: Optional[str]
    hr_archetype_fit: float
    hr_p_point: float
    hr_p_lower: float
    hr_p_upper: float
    hr_n_trades: int

    # P axis provenance
    p_archetype_matched: Optional[str]
    p_archetype_fit: float
    p_win_point: float
    p_win_lower: float
    p_mean_pnl_estimate: float

    # Shared context
    regime_alignment: float
    gbm_hr_score: float
    gbm_p_score: float
    v5_scoring_version: str = "v5.0.0"

    # Verdict derivation inputs (not on Decision but useful for logging)
    archetype_hr_conviction: float = 0.0   # Pre-GBM-merge archetype HR
    archetype_p_conviction: float = 0.0    # Pre-GBM-merge archetype P

    def to_decision_fields(self) -> dict[str, Any]:
        """Return fields ready to populate on a :class:`Decision` record."""
        return {
            "hr_conviction": round(self.hr_conviction, 2),
            "hr_archetype_matched": self.hr_archetype_matched,
            "hr_archetype_fit": round(self.hr_archetype_fit, 2),
            "hr_p_point": round(self.hr_p_point, 4),
            "hr_p_lower": round(self.hr_p_lower, 4),
            "hr_p_upper": round(self.hr_p_upper, 4),
            "hr_n_trades": self.hr_n_trades,
            "p_conviction": round(self.p_conviction, 2),
            "p_archetype_matched": self.p_archetype_matched,
            "p_archetype_fit": round(self.p_archetype_fit, 2),
            "p_win_point": round(self.p_win_point, 4),
            "p_win_lower": round(self.p_win_lower, 4),
            "p_mean_pnl_estimate": round(self.p_mean_pnl_estimate, 2),
            "regime_alignment": round(self.regime_alignment, 3),
            "gbm_hr_score": round(self.gbm_hr_score, 2),
            "gbm_p_score": round(self.gbm_p_score, 2),
            "v5_scoring_version": self.v5_scoring_version,
        }


def compute_v5_envelope(
    ctx: Any,  # ScoringContext (loose coupling — uses getattr)
    policy: PolicyConfig,
    *,
    pillar_scores: Optional[dict[str, float]] = None,
    hr_rate_lookup: Optional[dict[str, RateEstimate]] = None,
    p_rate_lookup: Optional[dict[str, PRateEstimate]] = None,
    regime: Optional[RegimeState] = None,
) -> V5Envelope:
    """Run HR archetype + P archetype + GBM scoring and combine into a V5Envelope.

    Args:
        ctx: :class:`app.pillars.models.ScoringContext` with feature data.
        policy: Active :class:`PolicyConfig` (uses ``v5_*`` settings only).
        pillar_scores: Optional pillar-score dict for archetypes using
            ``dc_score`` / ``mp_score`` / ``ts_score`` conditions.
        hr_rate_lookup: Runtime Wilson-bound HR rates per archetype_id.
            Empty/None → each archetype uses its seed_fallback path.
        p_rate_lookup: Runtime P rates. Same fallback behavior.
        regime: Market regime snapshot. Defaults to empty (fail-open 1.0).

    Returns:
        :class:`V5Envelope`. Always returns a valid envelope — any
        component failure degrades gracefully (archetype miss → conviction 0,
        GBM error → 0 contribution, missing rates → seed fallback).
    """
    hr_rates = hr_rate_lookup or {}
    p_rates = p_rate_lookup or {}
    regime = regime or RegimeState()
    cal = policy.v5_calibration  # May be None; conviction funcs use defaults.

    # --- HR axis ---
    hr_archetypes = policy.v5_hr_archetypes
    hr_result: HRConvictionResult
    if hr_archetypes is not None:
        try:
            hr_result = compute_hr_conviction(
                ctx,
                archetypes=hr_archetypes,
                rate_lookup=hr_rates,
                regime=regime,
                pillar_scores=pillar_scores,
                calibration=cal,
            )
        except Exception:
            logger.exception("v5 HR conviction failed for %s",
                             getattr(ctx, "evaluation_id", "?"))
            hr_result = _zero_hr_result()
    else:
        hr_result = _zero_hr_result()

    # --- P axis ---
    p_archetypes = policy.v5_p_archetypes
    p_result: PConvictionResult
    if p_archetypes is not None:
        try:
            p_result = compute_p_conviction(
                ctx,
                archetypes=p_archetypes,
                rate_lookup=p_rates,
                regime=regime,
                pillar_scores=pillar_scores,
                calibration=cal,
            )
        except Exception:
            logger.exception("v5 P conviction failed for %s",
                             getattr(ctx, "evaluation_id", "?"))
            p_result = _zero_p_result()
    else:
        p_result = _zero_p_result()

    # --- GBM co-scorer ---
    gbm_result: GBMScoreResult = GBMScoreResult(
        hr_score=0.0, p_score=0.0, hr_available=False, p_available=False,
    )
    if policy.v5_gbm_enabled:
        try:
            features = build_feature_dict_from_context(ctx, pillar_scores=pillar_scores)
            gbm_result = score_trade(features)
        except Exception:
            logger.exception("v5 GBM scoring failed for %s",
                             getattr(ctx, "evaluation_id", "?"))

    # --- Combine: archetype conviction OR capped GBM contribution ---
    hr_capped = gbm_result.hr_score * policy.v5_gbm_hr_weight if gbm_result.hr_available else 0.0
    p_capped = gbm_result.p_score * policy.v5_gbm_p_weight if gbm_result.p_available else 0.0
    final_hr = max(hr_result.conviction, hr_capped)
    final_p = max(p_result.conviction, p_capped)
    # Final clamp for safety (individual paths already clamp, but defensive)
    final_hr = max(0.0, min(100.0, final_hr))
    final_p = max(0.0, min(100.0, final_p))

    # Regime alignment: use whichever axis computed it (both should agree)
    regime_alignment = hr_result.regime_alignment or p_result.regime_alignment or 1.0

    return V5Envelope(
        hr_conviction=final_hr,
        p_conviction=final_p,
        hr_archetype_matched=hr_result.archetype_id,
        hr_archetype_fit=hr_result.archetype_fit,
        hr_p_point=hr_result.p_point,
        hr_p_lower=hr_result.p_lower,
        hr_p_upper=hr_result.p_upper,
        hr_n_trades=hr_result.n_trades,
        p_archetype_matched=p_result.archetype_id,
        p_archetype_fit=p_result.archetype_fit,
        p_win_point=p_result.win_point,
        p_win_lower=p_result.win_lower,
        p_mean_pnl_estimate=p_result.mean_pnl_pct,
        regime_alignment=regime_alignment,
        gbm_hr_score=gbm_result.hr_score,
        gbm_p_score=gbm_result.p_score,
        archetype_hr_conviction=hr_result.conviction,
        archetype_p_conviction=p_result.conviction,
    )


def derive_v5_verdict(
    envelope: V5Envelope,
    policy: PolicyConfig,
    all_gates_passed: bool,
    anti_archetype_triggered: Optional[str],
) -> tuple[Verdict, str, Optional[QualityTier]]:
    """v5 verdict rules.

    - Any hard gate failed → REJECT (REJECTED_BY_GATES)
    - Anti-archetype fired → REJECT (ANTI_ARCHETYPE_{id})
    - HR conviction >= hr_threshold OR P conviction >= p_threshold → APPROVE
    - Either conviction >= threshold / 2 → WATCH
    - Otherwise → REJECT

    Tier assignment (APPROVE only):
    - TIER_1 (Sharpshooter): hr_conviction >= 14 (top archetype Wilson lower)
    - TIER_2 (Quality): p_conviction >= 70 OR hr_conviction >= 7
    - TIER_3 (Tradeable): APPROVE-but-neither

    Returns:
        (verdict, primary_reason_code, quality_tier)
    """
    if not all_gates_passed:
        return Verdict.REJECT, "REJECTED_BY_GATES", None
    if anti_archetype_triggered:
        return Verdict.REJECT, f"ANTI_ARCHETYPE_{anti_archetype_triggered}", None

    hr_thresh = policy.v5_hr_threshold
    p_thresh = policy.v5_p_threshold

    approve = envelope.hr_conviction >= hr_thresh or envelope.p_conviction >= p_thresh
    if approve:
        if envelope.hr_conviction >= 14.0:
            tier = QualityTier.TIER_1
            reason = "V5_SHARPSHOOTER"
        elif envelope.p_conviction >= 70.0 or envelope.hr_conviction >= 7.0:
            tier = QualityTier.TIER_2
            reason = "V5_QUALITY"
        else:
            tier = QualityTier.TIER_3
            reason = "V5_TRADEABLE"
        return Verdict.APPROVE, reason, tier

    # WATCH bucket
    watch = (
        envelope.hr_conviction >= hr_thresh / 2.0
        or envelope.p_conviction >= p_thresh / 2.0
    )
    if watch:
        return Verdict.WATCH, "V5_WATCH", None

    return Verdict.REJECT, "V5_REJECTED_BY_SCORE", None


# ============================================================================
# Helpers
# ============================================================================


def _zero_hr_result() -> HRConvictionResult:
    return HRConvictionResult(
        conviction=0.0, archetype_id=None, archetype_fit=0.0,
        p_point=0.0, p_lower=0.0, p_upper=0.0, n_trades=0,
        regime_alignment=1.0,
    )


def _zero_p_result() -> PConvictionResult:
    return PConvictionResult(
        conviction=0.0, archetype_id=None, archetype_fit=0.0,
        win_point=0.0, win_lower=0.0, win_upper=0.0,
        mean_pnl_pct=0.0, pnl_multiplier=0.0, n_trades=0,
        regime_alignment=1.0,
    )
