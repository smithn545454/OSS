"""Unit tests for v4.1.0 archetype-aware tier assignment."""

from __future__ import annotations

from app.core.schemas import DecisionConfig, QualityTier
from app.decision.calculator import DecisionCalculator


def _calc() -> DecisionCalculator:
    # Default DecisionConfig: approve=75, tier_1_min_score=85,
    # tier_1_min_pillar=70, tier_2_min_pillar=55,
    # archetype_tier_1_threshold=80, archetype_tier_2_threshold=70.
    return DecisionCalculator(DecisionConfig())


class TestCompositePath:
    def test_composite_tier_1(self) -> None:
        tier = _calc().assign_quality_tier(
            92.5,
            spread_pct=2.0,
            directional_conviction=85.0,
            move_potential=80.0,
            trade_structure=75.0,
            archetype_match_score=None,
        )
        assert tier == QualityTier.TIER_1

    def test_composite_tier_2(self) -> None:
        tier = _calc().assign_quality_tier(
            82.0,
            spread_pct=2.0,
            directional_conviction=60.0,
            move_potential=60.0,
            trade_structure=60.0,
            archetype_match_score=None,
        )
        assert tier == QualityTier.TIER_2

    def test_composite_tier_3(self) -> None:
        tier = _calc().assign_quality_tier(
            76.0,
            spread_pct=2.0,
            directional_conviction=50.0,
            move_potential=40.0,
            trade_structure=60.0,
            archetype_match_score=None,
        )
        assert tier == QualityTier.TIER_3


class TestArchetypePath:
    def test_archetype_path_tier_1(self) -> None:
        # Composite below tier_1 (final_score=80), but archetype=85 promotes.
        tier = _calc().assign_quality_tier(
            80.0,
            spread_pct=2.0,
            directional_conviction=60.0,
            move_potential=60.0,
            trade_structure=60.0,
            archetype_match_score=85.0,
        )
        assert tier == QualityTier.TIER_1

    def test_archetype_path_tier_2(self) -> None:
        # Composite hits APPROVE only; archetype=72 promotes to TIER_2.
        tier = _calc().assign_quality_tier(
            76.0,
            spread_pct=2.0,
            directional_conviction=40.0,
            move_potential=40.0,
            trade_structure=60.0,
            archetype_match_score=72.0,
        )
        assert tier == QualityTier.TIER_2

    def test_archetype_below_thresholds_no_promotion(self) -> None:
        tier = _calc().assign_quality_tier(
            76.0,
            spread_pct=2.0,
            directional_conviction=40.0,
            move_potential=40.0,
            trade_structure=60.0,
            archetype_match_score=65.0,
        )
        assert tier == QualityTier.TIER_3


class TestBackwardsCompat:
    def test_historical_v3_no_archetype(self) -> None:
        # v3 call (positional) — archetype_match_score=None, same tier as pre-v4.1.0.
        tier = _calc().assign_quality_tier(
            82.0,
            premium_leverage=60.0,
            underlying_behavior=60.0,
            setup_quality=60.0,
            spread_pct=2.0,
        )
        assert tier == QualityTier.TIER_2

    def test_below_approve_still_none(self) -> None:
        # Below approve_threshold returns None regardless of archetype score.
        tier = _calc().assign_quality_tier(
            74.0,
            spread_pct=2.0,
            directional_conviction=60.0,
            move_potential=60.0,
            trade_structure=60.0,
            archetype_match_score=95.0,
        )
        assert tier is None
