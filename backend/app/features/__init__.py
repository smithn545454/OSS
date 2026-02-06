"""Feature computation module for Stage 4 of the OSS pipeline.

This module computes all features needed for pillar scoring (Stage 5).

Feature Categories (per Section 13 of requirements):
- Category A: Underlying Technical Features
- Category B: Relative Strength Features  
- Category C: Volatility Features
- Category D: Contract-Specific Features
- Category E: Liquidity Features
- Category F: Catalyst Features
"""

from app.features.models import FeatureSet
from app.features.calculator import FeatureComputer
from app.features.stage import FeatureComputationStage, run_feature_computation

__all__ = [
    "FeatureSet",
    "FeatureComputer",
    "FeatureComputationStage",
    "run_feature_computation",
]
