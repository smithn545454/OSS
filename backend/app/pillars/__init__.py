"""Pillar Scoring Module - Stage 5 of the OSS Pipeline (Policy v3.0.0).

This module computes three pillar scores (0-100) for each evaluation using
empirically-derived breakpoint scoring:

1. **Premium Leverage** - How cheap and asymmetric the option contract is
   (entry delta, IV, IV percentile, IV/RV ratio).
2. **Underlying Behavior** - How the underlying stock is behaving and
   whether the required move is realistic (ADX, realized vol, feasibility,
   ATR).
3. **Setup Quality** - Meta-indicators of a high-quality entry (volume,
   open interest, scanner convergence, DTE bucket).

Pillars never reject — they only score. Rejection is handled by hard
gates (Stage 6). The composite/conviction score is a weighted average of
the three pillar scores (weights from `PillarConfig.weights`).

See `scripts/output/final_pillar_design.json` for the analytical
derivation of weights and breakpoints.
"""

from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.scoring_engine import (
    compute_pillar_score,
    score_categorical_subscore,
    score_numeric_subscore,
    score_pillar,
)
from app.pillars.premium_leverage import compute_premium_leverage_pillar
from app.pillars.underlying_behavior import compute_underlying_behavior_pillar
from app.pillars.setup_quality import compute_setup_quality_pillar
from app.pillars.utils import (
    clamp_score,
    distance_from_neutral,
    linear_interpolate,
    linear_map_range,
)
from app.pillars.calculator import (
    PillarCalculator,
    compute_all_pillars,
    compute_final_score,
    get_pillar_scores_dict,
)
from app.pillars.stage import (
    PillarScoringStage,
    run_pillar_scoring,
)

__all__ = [
    # Models
    "PillarResult",
    "ScoringContext",
    "Subscore",
    # Scoring engine
    "score_numeric_subscore",
    "score_categorical_subscore",
    "compute_pillar_score",
    "score_pillar",
    # Utils (legacy, still used by stage/tests)
    "linear_interpolate",
    "linear_map_range",
    "clamp_score",
    "distance_from_neutral",
    # Pillar computations
    "compute_premium_leverage_pillar",
    "compute_underlying_behavior_pillar",
    "compute_setup_quality_pillar",
    # Calculator
    "PillarCalculator",
    "compute_all_pillars",
    "compute_final_score",
    "get_pillar_scores_dict",
    # Stage
    "PillarScoringStage",
    "run_pillar_scoring",
]
