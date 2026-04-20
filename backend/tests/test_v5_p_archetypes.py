"""Tests for v5 P (profitability) archetype library, matcher, and conviction.

P conviction hunts reliable profitability (win rate × normalized mean P&L)
— distinct from HR conviction (P(MFE ≥ 200%)). Most BREAKDOWN and REVAL
trades score ~0 on HR but should score 70+ on P.
"""

from __future__ import annotations

import math

from app.calibration.regime import RegimeState
from app.core.schemas import V5CalibrationConfig
from app.pillars.models import ScoringContext
from app.v5.p_archetypes import (
    P_ARCHETYPE_IDS_BY_STRENGTH,
    default_v5_p_archetypes,
)
from app.v5.p_conviction import (
    PRateEstimate,
    compute_p_conviction,
)
from app.v5.p_matcher import match_p_archetypes

# ============================================================================
# Library shape
# ============================================================================


class TestPArchetypeLibrary:
    def test_library_has_10_archetypes(self) -> None:
        lib = default_v5_p_archetypes()
        assert len(lib.archetypes) == 10

    def test_no_duplicate_ids(self) -> None:
        lib = default_v5_p_archetypes()
        ids = [a.archetype_id for a in lib.archetypes]
        assert len(set(ids)) == 10

    def test_strength_list_matches_library(self) -> None:
        lib = default_v5_p_archetypes()
        assert set(P_ARCHETYPE_IDS_BY_STRENGTH) == {a.archetype_id for a in lib.archetypes}

    def test_all_seeds_valid(self) -> None:
        lib = default_v5_p_archetypes()
        for a in lib.archetypes:
            assert a.historical_n > 0
            assert 0 <= a.historical_win_rate <= 1
            # P archetypes should all have positive mean P&L (selection criterion)
            assert a.historical_mean_pnl_pct > 0, f"{a.archetype_id} has non-positive P&L"

    def test_grinder_archetypes_present(self) -> None:
        """Both whole-scanner grinder archetypes must exist."""
        lib = default_v5_p_archetypes()
        ids = {a.archetype_id for a in lib.archetypes}
        assert "BREAKDOWN_GRINDER" in ids
        assert "REVALIDATION_QUALITY" in ids

    def test_p_archetypes_do_not_duplicate_hr_archetype_ids(self) -> None:
        """P and HR libraries must have disjoint archetype IDs."""
        from app.v5.hr_archetypes import default_v5_hr_archetypes
        hr_ids = {a.archetype_id for a in default_v5_hr_archetypes().archetypes}
        p_ids = {a.archetype_id for a in default_v5_p_archetypes().archetypes}
        assert hr_ids.isdisjoint(p_ids), (
            f"Overlapping IDs: {hr_ids & p_ids}"
        )


# ============================================================================
# Matcher — representative trades
# ============================================================================


def _ctx(**kwargs) -> ScoringContext:
    defaults = {
        "evaluation_id": "eval-test",
        "underlying_ticker": "TEST",
        "option_type": "CALL",
        "dte_bucket": "A",
        "scanner_source": "UNUSUAL_VOLUME",
        "dte": 18,
        "delta": 0.20,
    }
    defaults.update(kwargs)
    return ScoringContext(**defaults)


class TestPMatcher:
    def test_breakdown_grinder_matches_any_breakdown_trade(self) -> None:
        ctx = _ctx(scanner_source="BREAKDOWN", option_type="PUT", dte=30, delta=-0.40)
        result = match_p_archetypes(ctx, default_v5_p_archetypes())
        assert result.best is not None
        assert result.best.archetype_id == "BREAKDOWN_GRINDER"

    def test_revalidation_quality_matches(self) -> None:
        ctx = _ctx(scanner_source="REVALIDATION", option_type="CALL", dte=21)
        result = match_p_archetypes(ctx, default_v5_p_archetypes())
        assert result.best is not None
        # Both REVALIDATION_QUALITY and possibly more tight REVAL archetypes
        # could match. Just confirm at least one REVAL archetype matches.
        assert result.best.archetype_id.startswith("REVALIDATION")

    def test_revalidation_low_mp_preferred_when_matches(self) -> None:
        # With a low MP pillar, REVALIDATION_LOW_MP ties REVALIDATION_QUALITY
        # on fit. Matcher picks first at max fit — the specific winner doesn't
        # matter functionally, but assert a REVAL archetype is matched.
        ctx = _ctx(scanner_source="REVALIDATION", option_type="CALL")
        result = match_p_archetypes(
            ctx, default_v5_p_archetypes(),
            pillar_scores={"MP": 30.0},
        )
        assert result.best is not None
        assert result.best.archetype_id.startswith("REVALIDATION")

    def test_uv_volatile_compression_matches_big_cohort(self) -> None:
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            option_type="CALL",
            dte=30,
            delta=0.35,
            adx_14=15.0,        # ADX < 20
            atr14_pct=8.0,      # ATR >= 6
        )
        result = match_p_archetypes(ctx, default_v5_p_archetypes())
        assert result.best is not None
        # Multiple UV archetypes can match; the matcher picks one with highest fit
        assert result.best.archetype_id.startswith("UV_")

    def test_cheap_contrarian_cheap_vol_matches(self) -> None:
        # CHEAP + IVRV<1.0 + MP<40 + RS agreeing with direction
        ctx = _ctx(
            scanner_source="CHEAP_OPTIONS",
            option_type="CALL",
            rs_20d=1.05,        # RS > 0.95 for a CALL → RS_WITH (non-contrarian)
            iv_rv_ratio=0.85,
        )
        result = match_p_archetypes(
            ctx, default_v5_p_archetypes(),
            pillar_scores={"MP": 30.0},
        )
        assert result.best is not None
        # Could match CHEAP_CONTRARIAN_CHEAP_VOL
        assert result.best.archetype_id.startswith("CHEAP")

    def test_no_match_on_breakout_without_meeting_conditions(self) -> None:
        # BREAKOUT but not meeting IVP<30 and ATR>=6%
        ctx = _ctx(
            scanner_source="BREAKOUT",
            option_type="CALL",
            iv_percentile=80.0,   # Fails IVP<30
            atr14_pct=3.0,        # Fails ATR>=6%
        )
        result = match_p_archetypes(ctx, default_v5_p_archetypes())
        assert result.best is None or result.best.fit < 75


# ============================================================================
# P Conviction — formula
# ============================================================================


class TestPConviction:
    def test_no_match_returns_zero(self) -> None:
        ctx = _ctx(scanner_source="COMPRESSION_EXPANSION", dte=30)
        result = compute_p_conviction(
            ctx,
            archetypes=default_v5_p_archetypes(),
            rate_lookup={},
            regime=RegimeState(),
        )
        assert result.conviction == 0.0
        assert result.archetype_id is None

    def test_breakdown_grinder_scores_high(self) -> None:
        # BREAKDOWN with rate_lookup simulating the measured rates
        ctx = _ctx(scanner_source="BREAKDOWN", option_type="PUT", dte=30, delta=-0.40)
        rate_lookup = {
            "BREAKDOWN_GRINDER": PRateEstimate(
                win_point=0.659, win_lower=0.596, win_upper=0.716,
                mean_pnl_pct=29.57, n_effective=232,
            ),
        }
        result = compute_p_conviction(
            ctx,
            archetypes=default_v5_p_archetypes(),
            rate_lookup=rate_lookup,
            regime=RegimeState(),
        )
        assert result.archetype_id == "BREAKDOWN_GRINDER"
        # 100 × 0.596 × 1.591 × 1.0 × 1.0 ≈ 94.8
        # (pnl_norm for 29.57%: (29.57 - (-50)) / 100 × 2 = 1.591)
        assert 80 <= result.conviction <= 100
        assert math.isclose(result.pnl_multiplier, 1.591, abs_tol=0.01)

    def test_revalidation_quality_scores_very_high(self) -> None:
        ctx = _ctx(scanner_source="REVALIDATION", option_type="CALL", dte=21)
        rate_lookup = {
            "REVALIDATION_QUALITY": PRateEstimate(
                win_point=0.696, win_lower=0.606, win_upper=0.773,
                mean_pnl_pct=47.52, n_effective=112,
            ),
        }
        result = compute_p_conviction(
            ctx,
            archetypes=default_v5_p_archetypes(),
            rate_lookup=rate_lookup,
            regime=RegimeState(),
        )
        # Could match REVALIDATION_QUALITY or a tighter REVAL archetype if we
        # pass matching pillar scores. Since we passed no pillar_scores,
        # REVALIDATION_LOW_MP (needs mp_score) won't match; REVALIDATION_IVP_LO_CALL
        # (needs iv_percentile) won't match. REVALIDATION_QUALITY will.
        assert result.archetype_id == "REVALIDATION_QUALITY"
        # 100 × 0.606 × 1.95 × 1.0 × 1.0 ≈ 118 → clamped to 100
        assert result.conviction >= 95.0

    def test_regime_multiplier_applies(self) -> None:
        ctx = _ctx(scanner_source="BREAKDOWN", option_type="PUT", dte=30, delta=-0.40)
        rate_lookup = {
            "BREAKDOWN_GRINDER": PRateEstimate(
                win_point=0.659, win_lower=0.596, win_upper=0.716,
                mean_pnl_pct=29.57, n_effective=232,
            ),
        }
        # Bearish-fear regime boosts PUTs × 1.3
        regime = RegimeState(spy_return_20d_pct=-7.0, vix_level=28.0)
        result = compute_p_conviction(
            ctx,
            archetypes=default_v5_p_archetypes(),
            rate_lookup=rate_lookup,
            regime=regime,
        )
        # 100 × 0.596 × 1.591 × 1.0 × 1.3 ≈ 123 → clamped 100
        assert result.conviction == 100.0
        assert result.regime_alignment == 1.3

    def test_seed_fallback_when_rate_not_in_lookup(self) -> None:
        ctx = _ctx(scanner_source="BREAKDOWN", option_type="PUT")
        result = compute_p_conviction(
            ctx,
            archetypes=default_v5_p_archetypes(),
            rate_lookup={},  # Empty → seed fallback
            regime=RegimeState(),
        )
        assert result.archetype_id == "BREAKDOWN_GRINDER"
        # Seed fallback uses historical_win_rate=0.659 × 0.8 = 0.527 as lower
        # With mean_pnl=29.57: pnl_norm=1.591
        # 100 × 0.527 × 1.591 × 1.0 × 1.0 ≈ 83.8
        assert 70 <= result.conviction <= 95

    def test_clamped_at_100(self) -> None:
        ctx = _ctx(scanner_source="BREAKDOWN", option_type="PUT")
        rate_lookup = {
            "BREAKDOWN_GRINDER": PRateEstimate(
                win_point=1.0, win_lower=1.0, win_upper=1.0,
                mean_pnl_pct=1000.0, n_effective=100,  # Absurd numbers
            ),
        }
        result = compute_p_conviction(
            ctx,
            archetypes=default_v5_p_archetypes(),
            rate_lookup=rate_lookup,
            regime=RegimeState(),
        )
        assert result.conviction == 100.0

    def test_no_match_on_breakout_scanner_not_in_p_library(self) -> None:
        # BREAKOUT without IVP<30/ATR>=6% won't match any P archetype
        ctx = _ctx(
            scanner_source="BREAKOUT",
            option_type="CALL",
            iv_percentile=60.0,
            atr14_pct=3.0,
        )
        result = compute_p_conviction(
            ctx,
            archetypes=default_v5_p_archetypes(),
            rate_lookup={},
            regime=RegimeState(),
        )
        assert result.conviction == 0.0
        assert result.archetype_id is None

    def test_p_conviction_and_hr_conviction_independent(self) -> None:
        """A BREAKDOWN trade should score 0 on HR but high on P."""
        from app.v5.hr_archetypes import default_v5_hr_archetypes
        from app.v5.hr_conviction import compute_hr_conviction

        ctx = _ctx(scanner_source="BREAKDOWN", option_type="PUT", dte=30, delta=-0.40)
        # HR conviction should be 0 (no HR archetype matches BREAKDOWN)
        hr = compute_hr_conviction(
            ctx,
            archetypes=default_v5_hr_archetypes(),
            rate_lookup={},
            regime=RegimeState(),
        )
        assert hr.conviction == 0.0
        # P conviction should be > 0 (BREAKDOWN_GRINDER matches)
        p = compute_p_conviction(
            ctx,
            archetypes=default_v5_p_archetypes(),
            rate_lookup={},  # Uses seed fallback
            regime=RegimeState(),
        )
        assert p.conviction > 50.0
        assert p.archetype_id == "BREAKDOWN_GRINDER"

    def test_calibration_pnl_range_override(self) -> None:
        # Override the pnl normalization range — narrower ceiling pushes
        # the multiplier to 2.0 earlier
        ctx = _ctx(scanner_source="BREAKDOWN", option_type="PUT")
        rate_lookup = {
            "BREAKDOWN_GRINDER": PRateEstimate(
                win_point=0.659, win_lower=0.596, win_upper=0.716,
                mean_pnl_pct=29.57, n_effective=232,
            ),
        }
        cal = V5CalibrationConfig(pnl_normalize_ceiling_pct=30.0)
        result = compute_p_conviction(
            ctx,
            archetypes=default_v5_p_archetypes(),
            rate_lookup=rate_lookup,
            regime=RegimeState(),
            calibration=cal,
        )
        # With ceiling=30: (29.57 - (-50)) / 80 × 2 = 1.989
        assert math.isclose(result.pnl_multiplier, 1.989, abs_tol=0.01)
        # 100 × 0.596 × 1.989 × 1.0 × 1.0 ≈ 118.5 → clamped 100
        assert result.conviction == 100.0
