"""Pillar Scoring Module - Stage 5 of the OSS Pipeline.

This module computes three pillar scores (0-100) for each evaluation:
1. Directional Pillar - Measures likelihood of underlying moving in option's direction
2. Volatility Pillar - Measures if option is fairly priced relative to volatility
3. Structure Pillar - Measures tradability, liquidity, and execution quality

Pillars never reject - they only score. Rejection is handled by hard gates (Stage 6).
"""

from app.pillars.models import PillarResult, ScoringContext, Subscore
from app.pillars.utils import (
    linear_interpolate,
    linear_map_range,
    clamp_score,
    distance_from_neutral,
)
from app.pillars.directional import compute_directional_pillar
from app.pillars.volatility import compute_volatility_pillar
from app.pillars.structure import compute_structure_pillar
from app.pillars.calculator import (
    PillarCalculator,
    compute_all_pillars,
    compute_final_score,
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
    # Utils
    "linear_interpolate",
    "linear_map_range",
    "clamp_score",
    "distance_from_neutral",
    # Pillar computations
    "compute_directional_pillar",
    "compute_volatility_pillar",
    "compute_structure_pillar",
    # Calculator
    "PillarCalculator",
    "compute_all_pillars",
    "compute_final_score",
    # Stage
    "PillarScoringStage",
    "run_pillar_scoring",
]
