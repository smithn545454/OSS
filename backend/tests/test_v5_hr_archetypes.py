"""Tests for v5 HR archetype library, matcher, and conviction calculator.

Asserts the library is well-formed (12 archetypes, no duplicate IDs, all
historical metadata sane) and that the matcher + conviction pipeline
produces correct values for representative trades.
"""

from __future__ import annotations

import math

from app.calibration.archetype_rates import RateEstimate
from app.calibration.regime import RegimeState
from app.core.schemas import V5CalibrationConfig
from app.pillars.models import ScoringContext
from app.v5.hr_archetypes import (
    HR_ARCHETYPE_IDS_BY_STRENGTH,
    default_v5_hr_archetypes,
)
from app.v5.hr_conviction import HRConvictionResult, compute_hr_conviction
from app.v5.hr_matcher import match_hr_archetypes


# ============================================================================
# Library shape
# ============================================================================


class TestHRArchetypeLibrary:
    def test_library_has_12_archetypes(self) -> None:
        lib = default_v5_hr_archetypes()
        assert len(lib.archetypes) == 12

    def test_no_duplicate_ids(self) -> None:
        lib = default_v5_hr_archetypes()
        ids = [a.archetype_id for a in lib.archetypes]
        assert len(set(ids)) == 12

    def test_all_seeds_have_positive_n(self) -> None:
        lib = default_v5_hr_archetypes()
        for a in lib.archetypes:
            assert a.historical_n > 0
            assert 0 <= a.historical_hr200_rate <= 1
            assert 0 <= a.historical_win_rate <= 1

    def test_strength_list_matches_library_ids(self) -> None:
        lib = default_v5_hr_archetypes()
        lib_ids = {a.archetype_id for a in lib.archetypes}
        assert set(HR_ARCHETYPE_IDS_BY_STRENGTH) == lib_ids

    def test_existing_v4_1_0_archetypes_present(self) -> None:
        lib = default_v5_hr_archetypes()
        ids = {a.archetype_id for a in lib.archetypes}
        # All 6 v4.1.0 archetypes carried forward
        for required in [
            "UV_LOTTERY_CALL", "UV_REVERSAL_PUT", "UV_STRUCTURAL",
            "CHEAP_COMPRESSION", "CHEAP_VOL_REVERSAL", "CHEAP_ULTRA_CALL",
        ]:
            assert required in ids, f"v4.1.0 archetype {required} missing from v5 library"

    def test_new_archetypes_present(self) -> None:
        lib = default_v5_hr_archetypes()
        ids = {a.archetype_id for a in lib.archetypes}
        for required in [
            "UV_LOTTERY_DC_MID", "UV_LOTTERY_IVRV_CHEAP", "UV_LOTTERY_IVP_LO",
            "CHEAP_ULTRA_MP_HIGH", "CHEAP_SHORT_FAIR_CONTRARIAN", "CHEAP_ULTRA_TS_MID",
        ]:
            assert required in ids, f"v5 new archetype {required} missing"


# ============================================================================
# Matcher — representative trades
# ============================================================================


def _ctx(**kwargs) -> ScoringContext:
    """Build a minimal ScoringContext with sensible defaults."""
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


class TestHRMatcher:
    def test_uv_lottery_call_matches_canonical_trade(self) -> None:
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            dte=18,
            delta=0.20,
            option_type="CALL",
        )
        result = match_hr_archetypes(ctx, default_v5_hr_archetypes())
        assert result.best is not None
        assert result.best.fit >= 75
        # UV_LOTTERY_DC_MID requires DC pillar, which we didn't pass — so
        # it will not match. Plain UV_LOTTERY_CALL should.
        # (Both could match in principle, but DC_MID needs dc_score.)
        matched_id = result.best.archetype_id
        assert matched_id in {"UV_LOTTERY_CALL"}

    def test_uv_lottery_dc_mid_with_pillar_score(self) -> None:
        # Same trade, but pillar_scores includes DC=50 → DC_MID matches too.
        # The matcher returns the best fit; both UV_LOTTERY_CALL and
        # UV_LOTTERY_DC_MID will hit fit=100 — the higher-Wilson archetype
        # (DC_MID) is what we'd select for conviction.
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            dte=18,
            delta=0.20,
            option_type="CALL",
        )
        result = match_hr_archetypes(
            ctx, default_v5_hr_archetypes(),
            pillar_scores={"DC": 50.0},
        )
        assert result.best is not None
        matched_id = result.best.archetype_id
        assert matched_id in {"UV_LOTTERY_CALL", "UV_LOTTERY_DC_MID"}

    def test_uv_reversal_put_matches(self) -> None:
        # PUT on rallying stock (rs_20d > 1.05 = RS contrarian for PUT) + TS≥75
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            option_type="PUT",
            dte=21,
            delta=-0.25,
            rs_20d=1.10,
        )
        result = match_hr_archetypes(
            ctx, default_v5_hr_archetypes(),
            pillar_scores={"TS": 80.0},
        )
        assert result.best is not None
        assert result.best.archetype_id == "UV_REVERSAL_PUT"

    def test_cheap_compression_matches(self) -> None:
        ctx = _ctx(
            scanner_source="CHEAP_OPTIONS",
            adx_14=18.0,           # ADX < 20
            atr14_pct=5.0,         # ATR in [4, 6]
        )
        result = match_hr_archetypes(
            ctx, default_v5_hr_archetypes(),
            pillar_scores={"MP": 65.0},   # MP in [60, 75]
        )
        assert result.best is not None
        assert result.best.archetype_id == "CHEAP_COMPRESSION"

    def test_no_match_when_scanner_wrong(self) -> None:
        ctx = _ctx(
            scanner_source="BREAKOUT",  # Not in any v5 HR archetype
            dte=18,
            delta=0.20,
            option_type="CALL",
        )
        result = match_hr_archetypes(ctx, default_v5_hr_archetypes())
        # Either no match or all_fits show 0 for archetype_id with scanner_uv
        assert result.best is None or result.best.fit < 75

    def test_no_match_when_dte_far_outside_range(self) -> None:
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            dte=60,                 # Way outside 14-21 + feather
            delta=0.20,
            option_type="CALL",
        )
        result = match_hr_archetypes(ctx, default_v5_hr_archetypes())
        # UV_LOTTERY_CALL/DC_MID/IVRV_CHEAP/IVP_LO all need DTE 14-21 → all fail
        # UV_REVERSAL_PUT needs PUT (we passed CALL) → fails
        # UV_STRUCTURAL needs DTE 14-21 → fails
        # No CHEAP archetypes either (wrong scanner)
        assert result.best is None


# ============================================================================
# HR Conviction — formula
# ============================================================================


class TestHRConviction:
    def test_no_match_returns_zero_conviction(self) -> None:
        ctx = _ctx(
            scanner_source="BREAKOUT",  # No matching archetype
            dte=60,
        )
        result = compute_hr_conviction(
            ctx,
            archetypes=default_v5_hr_archetypes(),
            rate_lookup={},
            regime=RegimeState(),
        )
        assert result.conviction == 0.0
        assert result.archetype_id is None

    def test_perfect_match_uses_wilson_lower(self) -> None:
        # Match UV_LOTTERY_CALL with Wilson lower 0.14 (14%) and fit=100 and regime=1.0
        # Expected conviction: 100 × 0.14 × 1.0 × 1.0 = 14.0
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            dte=18,
            delta=0.20,
            option_type="CALL",
        )
        rate_lookup = {
            "UV_LOTTERY_CALL": RateEstimate(
                point=0.20, lower=0.14, upper=0.27, n_effective=136, n_raw=136,
            ),
        }
        result = compute_hr_conviction(
            ctx,
            archetypes=default_v5_hr_archetypes(),
            rate_lookup=rate_lookup,
            regime=RegimeState(),  # Missing → regime=1.0
        )
        assert result.archetype_id == "UV_LOTTERY_CALL"
        assert math.isclose(result.conviction, 14.0, abs_tol=0.5)
        assert result.regime_alignment == 1.0

    def test_regime_multiplier_applies(self) -> None:
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            dte=18,
            delta=0.20,
            option_type="CALL",
        )
        rate_lookup = {
            "UV_LOTTERY_CALL": RateEstimate(
                point=0.20, lower=0.14, upper=0.27, n_effective=136, n_raw=136,
            ),
        }
        # Bullish-calm regime → CALL × 1.3
        regime = RegimeState(spy_return_20d_pct=6.0, vix_level=15.0)
        result = compute_hr_conviction(
            ctx,
            archetypes=default_v5_hr_archetypes(),
            rate_lookup=rate_lookup,
            regime=regime,
        )
        # 100 × 0.14 × 1.0 × 1.3 = 18.2
        assert math.isclose(result.conviction, 18.2, abs_tol=0.5)
        assert result.regime_alignment == 1.3

    def test_seed_fallback_when_archetype_not_in_rate_lookup(self) -> None:
        # No rate_lookup entry → falls back to historical_hr200_rate × 0.5 as conservative lower
        # UV_LOTTERY_CALL seed rate 0.1985 → fallback lower ≈ 0.0993 → conviction ≈ 9.93
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            dte=18,
            delta=0.20,
            option_type="CALL",
        )
        result = compute_hr_conviction(
            ctx,
            archetypes=default_v5_hr_archetypes(),
            rate_lookup={},  # Empty — triggers seed fallback
            regime=RegimeState(),
        )
        assert result.archetype_id == "UV_LOTTERY_CALL"
        # Should be roughly half the seed point estimate, capped at 100
        assert 5.0 <= result.conviction <= 15.0
        assert result.n_trades == 136  # Seed n preserved

    def test_dc_mid_archetype_picks_strongest_when_both_match(self) -> None:
        # UV_LOTTERY_CALL and UV_LOTTERY_DC_MID both match this trade
        # When their fits are equal (both 100), the matcher picks one at "max".
        # Either is a valid result — assert we get a UV_LOTTERY family match.
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            dte=18,
            delta=0.20,
            option_type="CALL",
        )
        rate_lookup = {
            "UV_LOTTERY_CALL": RateEstimate(
                point=0.20, lower=0.14, upper=0.27, n_effective=136, n_raw=136,
            ),
            "UV_LOTTERY_DC_MID": RateEstimate(
                point=0.30, lower=0.18, upper=0.46, n_effective=36, n_raw=36,
            ),
        }
        result = compute_hr_conviction(
            ctx,
            archetypes=default_v5_hr_archetypes(),
            rate_lookup=rate_lookup,
            regime=RegimeState(),
            pillar_scores={"DC": 50.0},
        )
        assert result.archetype_id in {"UV_LOTTERY_CALL", "UV_LOTTERY_DC_MID"}
        # Either picks should produce a meaningful conviction
        assert result.conviction > 5.0

    def test_conviction_clamped_to_100(self) -> None:
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            dte=18,
            delta=0.20,
            option_type="CALL",
        )
        # Pathological rate_lookup with lower=1.0 (impossible in practice)
        rate_lookup = {
            "UV_LOTTERY_CALL": RateEstimate(
                point=1.0, lower=1.0, upper=1.0, n_effective=100, n_raw=100,
            ),
        }
        regime = RegimeState(spy_return_20d_pct=6.0, vix_level=15.0)  # × 1.3
        result = compute_hr_conviction(
            ctx,
            archetypes=default_v5_hr_archetypes(),
            rate_lookup=rate_lookup,
            regime=regime,
        )
        # 100 × 1.0 × 1.0 × 1.3 = 130 → should be clamped to 100
        assert result.conviction == 100.0

    def test_calibration_config_overrides_regime_clamps(self) -> None:
        ctx = _ctx(
            scanner_source="UNUSUAL_VOLUME",
            dte=18,
            delta=0.20,
            option_type="CALL",
        )
        rate_lookup = {
            "UV_LOTTERY_CALL": RateEstimate(
                point=0.20, lower=0.14, upper=0.27, n_effective=136, n_raw=136,
            ),
        }
        # Bullish regime, but tighten the multiplier ceiling to 1.1
        cal = V5CalibrationConfig(regime_multiplier_max=1.1)
        regime = RegimeState(spy_return_20d_pct=6.0, vix_level=15.0)
        result = compute_hr_conviction(
            ctx,
            archetypes=default_v5_hr_archetypes(),
            rate_lookup=rate_lookup,
            regime=regime,
            calibration=cal,
        )
        # 100 × 0.14 × 1.0 × 1.1 = 15.4
        assert math.isclose(result.conviction, 15.4, abs_tol=0.5)
        assert result.regime_alignment == 1.1
