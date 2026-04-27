"""Tests for Convex Mode Stage 3 (Volatility Mispricing).

Pure-function metric computations + term-structure + skew + direction
inference + integrator. Pipeline wiring is covered separately in
test_convex_pipeline.py.
"""

from __future__ import annotations

import pytest

from app.convex import (
    Stage3Inputs,
    assess_skew,
    assess_term_structure,
    compute_iv_hv_ratio,
    compute_iv_percentile,
    compute_iv_rank,
    evaluate_stage3,
    infer_direction,
)
from app.convex.stage3_volatility import (
    _classify_skew,
    _classify_term_shape,
    _term_score,
    composite_strength,
)
from app.core.schemas import ConvexConfig, IVHistory


def _iv_history(values: list[float], field: str = "atm_iv") -> list[IVHistory]:
    """Build a list of IVHistory records with a single field populated."""
    out = []
    for i, v in enumerate(values):
        kwargs = {
            "ticker": "X",
            "date": f"2025-01-{(i % 27) + 1:02d}",
            "atm_iv": 0.30,
        }
        if field != "atm_iv":
            kwargs[field] = v
        else:
            kwargs["atm_iv"] = v
        out.append(IVHistory(**kwargs))
    return out


# ---------------------------------------------------------------------------
# Per-metric computations
# ---------------------------------------------------------------------------


class TestComputeIvRank:

    def test_returns_none_when_history_too_short(self):
        history = _iv_history([0.20] * 10)
        assert compute_iv_rank(0.30, history) is None

    def test_full_range(self):
        # 21 values from 0.10 to 0.50; current 0.30 → rank 50.
        history = _iv_history([0.10 + i * 0.02 for i in range(21)])
        rank = compute_iv_rank(0.30, history)
        assert rank == pytest.approx(50.0, abs=1.0)

    def test_at_floor(self):
        history = _iv_history([0.10 + i * 0.02 for i in range(21)])
        # Current at min → rank 0
        assert compute_iv_rank(0.10, history) == 0.0

    def test_clamps_to_100(self):
        history = _iv_history([0.10 + i * 0.02 for i in range(21)])
        # Current above max → clamped to 100
        assert compute_iv_rank(1.00, history) == 100.0

    def test_returns_none_on_flat_history(self):
        history = _iv_history([0.30] * 25)
        assert compute_iv_rank(0.30, history) is None

    def test_uses_specified_field(self):
        # iv_30d field with 25 entries
        history = _iv_history([0.10 + i * 0.02 for i in range(25)], field="iv_30d")
        assert compute_iv_rank(0.30, history, field="iv_30d") is not None


class TestComputeIvPercentile:

    def test_returns_none_when_history_too_short(self):
        assert compute_iv_percentile(0.30, _iv_history([0.20] * 5)) is None

    def test_below_count_50pct(self):
        history = _iv_history([0.10 + i * 0.02 for i in range(20)])
        # Current 0.30 has 10 of 20 below it → 50%
        pct = compute_iv_percentile(0.30, history)
        assert pct == pytest.approx(50.0)


class TestComputeIvHvRatio:

    def test_basic(self):
        assert compute_iv_hv_ratio(0.30, 0.25) == pytest.approx(1.20)

    def test_returns_none_on_zero_hv(self):
        assert compute_iv_hv_ratio(0.30, 0.0) is None
        assert compute_iv_hv_ratio(0.30, None) is None


# ---------------------------------------------------------------------------
# Term structure
# ---------------------------------------------------------------------------


class TestClassifyTermShape:

    def test_flat(self):
        # within 2% diff
        assert _classify_term_shape(0.30, 0.295) == "flat"

    def test_mild_contango(self):
        # front below sixty by 5%
        assert _classify_term_shape(0.285, 0.30) == "mild_contango"

    def test_full_contango(self):
        assert _classify_term_shape(0.20, 0.30) == "contango"

    def test_mild_backwardation(self):
        assert _classify_term_shape(0.315, 0.30) == "mild_backwardation"

    def test_full_backwardation(self):
        assert _classify_term_shape(0.40, 0.30) == "backwardation"


class TestAssessTermStructure:

    def test_state_based_passes_on_contango(self):
        result = assess_term_structure(0.20, 0.30, "state_based")
        assert result.pass_ is True
        assert result.shape in ("contango", "mild_contango")

    def test_state_based_fails_on_backwardation(self):
        result = assess_term_structure(0.40, 0.30, "state_based")
        assert result.pass_ is False
        assert "already pricing" in result.note

    def test_date_known_accepts_mild_backwardation(self):
        result = assess_term_structure(0.315, 0.30, "date_known")
        assert result.pass_ is True

    def test_date_known_with_excessive_backwardation_and_reference_fails(self):
        # Historical pre-event ratio = 1.05; current 0.75/0.30 = 2.50 — exceeds
        # 2× the historical ratio (2.10) → gate FAILs.
        result = assess_term_structure(
            0.75, 0.30, "date_known", historical_pre_event_backwardation=1.05
        )
        assert result.pass_ is False
        assert "exceeds 2×" in result.note

    def test_date_known_without_reference_passes_anything(self):
        result = assess_term_structure(0.45, 0.30, "date_known")
        assert result.pass_ is True

    def test_missing_data_fails_open(self):
        result = assess_term_structure(None, 0.30, "state_based")
        assert result.pass_ is True
        assert "unavailable" in result.note


# ---------------------------------------------------------------------------
# Skew + direction
# ---------------------------------------------------------------------------


class TestClassifySkew:

    def test_balanced(self):
        assert _classify_skew(0.30, 0.30) == "balanced"

    def test_put_skew_rich(self):
        assert _classify_skew(0.35, 0.30) == "put_skew_rich"

    def test_call_skew_rich(self):
        assert _classify_skew(0.30, 0.35) == "call_skew_rich"

    def test_unknown_on_zero(self):
        assert _classify_skew(0.0, 0.30) == "unknown"


class TestAssessSkew:

    def test_bullish_with_put_skew_rich_favorable(self):
        result = assess_skew(put_25d_iv=0.35, call_25d_iv=0.30, direction="bullish")
        assert result.pass_ is True
        assert result.alignment_with_thesis == "favorable"

    def test_bullish_with_call_skew_rich_unfavorable(self):
        result = assess_skew(put_25d_iv=0.30, call_25d_iv=0.35, direction="bullish")
        assert result.pass_ is False
        assert result.alignment_with_thesis == "unfavorable"

    def test_bearish_with_call_skew_rich_favorable(self):
        result = assess_skew(put_25d_iv=0.30, call_25d_iv=0.35, direction="bearish")
        assert result.pass_ is True
        assert result.alignment_with_thesis == "favorable"

    def test_ambiguous_passes_regardless(self):
        result = assess_skew(put_25d_iv=0.30, call_25d_iv=0.35, direction="ambiguous")
        assert result.pass_ is True
        assert result.alignment_with_thesis == "neutral"

    def test_missing_data_fails_open(self):
        result = assess_skew(None, 0.30, direction="bullish")
        assert result.pass_ is True
        assert result.alignment_with_thesis == "neutral"
        assert "unavailable" in result.note


class TestInferDirection:

    def test_top_of_range_bullish(self):
        assert infer_direction(80.0, "balanced") == "bullish"

    def test_bottom_of_range_bearish(self):
        assert infer_direction(20.0, "balanced") == "bearish"

    def test_mid_range_with_put_skew_rich_bullish(self):
        assert infer_direction(50.0, "put_skew_rich") == "bullish"

    def test_mid_range_with_call_skew_rich_bearish(self):
        assert infer_direction(50.0, "call_skew_rich") == "bearish"

    def test_mid_range_balanced_ambiguous(self):
        assert infer_direction(50.0, "balanced") == "ambiguous"

    def test_no_position_falls_back_to_skew(self):
        assert infer_direction(None, "put_skew_rich") == "bullish"
        assert infer_direction(None, "balanced") == "ambiguous"


# ---------------------------------------------------------------------------
# Strength composite
# ---------------------------------------------------------------------------


class TestCompositeStrength:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_zero_when_all_missing(self):
        # All metrics None except term/skew = 0 too
        result = composite_strength(None, None, None, 0.0, 0.0, self.cfg)
        assert result == pytest.approx(0.0, abs=1e-3)

    def test_higher_when_metrics_strong(self):
        weak = composite_strength(35.0, 30.0, 1.05, 0.6, 0.5, self.cfg)
        strong = composite_strength(10.0, 12.0, 0.85, 1.0, 1.0, self.cfg)
        assert strong > weak


# ---------------------------------------------------------------------------
# evaluate_stage3 — integrator
# ---------------------------------------------------------------------------


class TestEvaluateStage3:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def _strong_inputs(self) -> Stage3Inputs:
        # 25-day history of iv_30d rising from 0.20 → 0.45; current 0.22 → low
        history_values = [0.20 + i * 0.01 for i in range(25)]
        history = _iv_history(history_values, field="iv_30d")
        return Stage3Inputs(
            ticker="NVDA",
            current_iv_30d=0.22,
            current_iv_60d=0.24,  # contango
            current_iv_25d_put=0.26,  # put-skew rich
            current_iv_25d_call=0.22,
            iv_history=history,
            rv20=0.30,  # IV/HV = 0.22/0.30 = 0.73 < 1.10
            catalyst_type="state_based",
            price_position_pct=80.0,  # high in range → bullish
        )

    def test_full_pass(self):
        result = evaluate_stage3(self._strong_inputs(), self.cfg)
        assert result.payload.result == "PASS"
        assert result.direction == "bullish"
        assert result.payload.extras["directional_bias"] == "bullish"
        assert result.payload.criteria["iv_rank"]["pass"] is True
        assert result.payload.criteria["iv_percentile"]["pass"] is True
        assert result.payload.criteria["iv_hv_ratio"]["pass"] is True
        assert result.payload.criteria["term_structure"]["pass"] is True
        assert result.payload.criteria["skew"]["pass"] is True
        assert result.payload.strength > 0

    def test_fails_on_high_iv_hv_ratio(self):
        inputs = self._strong_inputs()
        inputs.rv20 = 0.15  # IV/HV = 1.47 — too high
        result = evaluate_stage3(inputs, self.cfg)
        assert result.payload.result == "FAIL"
        assert result.payload.criteria["iv_hv_ratio"]["pass"] is False

    def test_fails_on_state_based_backwardation(self):
        inputs = self._strong_inputs()
        inputs.current_iv_60d = 0.18  # front 0.22 > sixty 0.18 → backwardation
        result = evaluate_stage3(inputs, self.cfg)
        assert result.payload.result == "FAIL"
        assert result.payload.criteria["term_structure"]["pass"] is False

    def test_fails_on_unfavorable_skew_for_bullish(self):
        inputs = self._strong_inputs()
        # Make call skew rich for an explicitly bullish thesis
        inputs.current_iv_25d_put = 0.20
        inputs.current_iv_25d_call = 0.30
        inputs.price_position_pct = 80.0  # forces bullish
        result = evaluate_stage3(inputs, self.cfg)
        assert result.payload.result == "FAIL"
        assert result.payload.criteria["skew"]["pass"] is False

    def test_short_history_fails_iv_rank_gate(self):
        inputs = self._strong_inputs()
        inputs.iv_history = _iv_history([0.20] * 10, field="iv_30d")  # too short
        result = evaluate_stage3(inputs, self.cfg)
        assert result.payload.result == "FAIL"
        # iv_rank None → gate fails
        assert result.payload.criteria["iv_rank"]["value"] is None
        assert result.payload.criteria["iv_rank"]["pass"] is False

    def test_falls_back_to_atm_iv_when_iv_30d_missing(self):
        inputs = self._strong_inputs()
        # Use atm_iv-only history so iv_30d field is empty
        inputs.iv_history = _iv_history(
            [0.20 + i * 0.01 for i in range(25)], field="atm_iv"
        )
        result = evaluate_stage3(inputs, self.cfg)
        # Should still compute iv_rank using the atm_iv fallback
        assert result.payload.criteria["iv_rank"]["value"] is not None

    def test_term_score_helper(self):
        from app.convex.stage3_volatility import TermStructureAssessment

        good = TermStructureAssessment(
            pass_=True, front_month_iv=0.20, sixty_day_iv=0.30,
            shape="contango", note="",
        )
        flat = TermStructureAssessment(
            pass_=True, front_month_iv=0.30, sixty_day_iv=0.30,
            shape="flat", note="",
        )
        failed = TermStructureAssessment(
            pass_=False, front_month_iv=0.40, sixty_day_iv=0.30,
            shape="backwardation", note="",
        )
        assert _term_score(good) > _term_score(flat) > _term_score(failed)
