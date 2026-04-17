"""Pillar Scoring Module - Stage 5 of the OSS Pipeline.

Two regimes coexist during the v3→v4 transition:

**v3 (Policy v3.x — retained until Phase 9):**
1. Premium Leverage — contract cheapness / asymmetry.
2. Underlying Behavior — trend / feasibility.
3. Setup Quality — scanner convergence / liquidity.

**v4 (Policy v4.x — active from Phase 7):**
1. Directional Conviction — Stage 2 trend + RS + ADX + pivot + OBV + sector.
2. Move Potential — catalyst + historical move + IV/RV + BB width + expected/required.
3. Trade Structure — delta/gamma/theta / DTE / IV rank / strike proximity.

Pillars never reject — rejection is handled by hard gates (Stage 6). The
composite score is a weighted arithmetic sum (v3) or weighted geometric
mean (v4), selected via :attr:`PillarConfig.composite_formula`.
"""

from app.pillars.calculator import (
    PillarCalculator,
    compute_all_pillars,
    compute_final_score,
    compute_final_score_from_results,
    get_pillar_scores_dict,
)
from app.pillars.composite import (
    V4_MIN_SUBSCORES,
    V4_PILLAR_FLOOR,
    apply_v4_rules,
    compute_composite_score,
    weighted_geometric_mean,
    weighted_sum,
)
from app.pillars.directional_conviction import compute_directional_conviction_pillar
from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.move_potential import compute_move_potential_pillar
from app.pillars.premium_leverage import compute_premium_leverage_pillar
from app.pillars.scoring_engine import (
    compute_pillar_score,
    score_categorical_subscore,
    score_numeric_subscore,
    score_pillar,
)
from app.pillars.setup_quality import compute_setup_quality_pillar
from app.pillars.stage import (
    PillarScoringStage,
    run_pillar_scoring,
)
from app.pillars.trade_structure import compute_trade_structure_pillar
from app.pillars.underlying_behavior import compute_underlying_behavior_pillar
from app.pillars.utils import (
    clamp_score,
    distance_from_neutral,
    linear_interpolate,
    linear_map_range,
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
    # Composite (v3 + v4)
    "compute_composite_score",
    "weighted_sum",
    "weighted_geometric_mean",
    "apply_v4_rules",
    "V4_MIN_SUBSCORES",
    "V4_PILLAR_FLOOR",
    # Utils (legacy, still used by stage/tests)
    "linear_interpolate",
    "linear_map_range",
    "clamp_score",
    "distance_from_neutral",
    # v3 pillar computations (retained through Phase 8)
    "compute_premium_leverage_pillar",
    "compute_underlying_behavior_pillar",
    "compute_setup_quality_pillar",
    # v4 pillar computations
    "compute_directional_conviction_pillar",
    "compute_move_potential_pillar",
    "compute_trade_structure_pillar",
    # Calculator
    "PillarCalculator",
    "compute_all_pillars",
    "compute_final_score",
    "compute_final_score_from_results",
    "get_pillar_scores_dict",
    # Stage
    "PillarScoringStage",
    "run_pillar_scoring",
]
