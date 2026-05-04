"""Tests for Convex Mode Stage 3 (PL Pricing Pre-Screen).

Stage 3 was rewritten to compute a representative PL score (replacing the
legacy IV/HV envelope). Direction inference moved to Stage 2.
"""

from __future__ import annotations

import pytest

from app.convex.stage3_volatility import (
    Stage3Inputs,
    compute_iv_percentile,
    compute_iv_rv_ratio,
    evaluate_stage3,
)
from app.core.schemas import ConvexConfig, IVHistory


def _hist(values: list[float], field: str = "iv_30d") -> list[IVHistory]:
    """Build a list of IVHistory rows with the given values on the chosen field."""
    out: list[IVHistory] = []
    for i, v in enumerate(values):
        kwargs = {
            "ticker": "TEST",
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "atm_iv": v,  # always populated as fallback
        }
        if field == "iv_30d":
            kwargs["iv_30d"] = v
        out.append(IVHistory(**kwargs))
    return out


def test_iv_percentile_basic() -> None:
    history = _hist([0.20, 0.25, 0.30, 0.35, 0.40] * 6)  # 30 records
    pct = compute_iv_percentile(0.30, history, field="iv_30d")
    # 12 of 30 strictly below 0.30 → 40.0
    assert pct == pytest.approx(40.0)


def test_iv_percentile_returns_none_when_thin() -> None:
    history = _hist([0.20, 0.25, 0.30])
    assert compute_iv_percentile(0.30, history) is None


def test_iv_rv_ratio_basic() -> None:
    assert compute_iv_rv_ratio(0.30, 0.20) == pytest.approx(1.5)


def test_iv_rv_ratio_handles_missing_rv() -> None:
    assert compute_iv_rv_ratio(0.30, None) is None
    assert compute_iv_rv_ratio(0.30, 0.0) is None


def test_pl_pre_screen_pass() -> None:
    """A clean Stage 3 input → PASS with a high pl_pre_score."""
    history = _hist([0.30] * 30)  # current IV = its own median → ~0% percentile
    inputs = Stage3Inputs(
        ticker="TEST",
        current_iv_30d=0.30,
        iv_history=history,
        rv20=0.32,  # IV/RV ratio ≈ 0.94 — near the peak
    )
    result = evaluate_stage3(inputs, ConvexConfig())
    assert result.payload.result == "PASS"
    assert result.pl_pre_score >= 70.0
    assert result.payload.criteria["pl_pre_score"] == pytest.approx(result.pl_pre_score)


def test_pl_pre_screen_fail_when_iv_high() -> None:
    history = _hist([0.30] * 30)
    inputs = Stage3Inputs(
        ticker="EXPENSIVE",
        current_iv_30d=2.0,  # Very expensive
        iv_history=history,
        rv20=0.30,
    )
    result = evaluate_stage3(inputs, ConvexConfig())
    assert result.payload.result == "FAIL"
    assert result.pl_pre_score < 70.0


def test_pl_pre_screen_fail_when_iv_missing() -> None:
    inputs = Stage3Inputs(
        ticker="MISSING",
        current_iv_30d=None,
        iv_history=[],
        rv20=None,
    )
    result = evaluate_stage3(inputs, ConvexConfig())
    assert result.payload.result == "FAIL"
    assert "no 30-day iv" in result.payload.summary.lower()


def test_threshold_is_config_driven() -> None:
    """Lowering pl_pre_screen_min should let an otherwise-borderline pass."""
    history = _hist([0.30] * 30)
    inputs = Stage3Inputs(
        ticker="MID",
        current_iv_30d=0.45,
        iv_history=history,
        rv20=0.30,
    )
    strict = ConvexConfig()  # default 70
    lenient = ConvexConfig(pl_pre_screen_min=10.0)
    strict_result = evaluate_stage3(inputs, strict)
    lenient_result = evaluate_stage3(inputs, lenient)
    # The same PL score, different gate verdicts.
    assert strict_result.pl_pre_score == lenient_result.pl_pre_score
    assert lenient_result.payload.result == "PASS"


def test_payload_carries_inputs_for_stage4_reuse() -> None:
    """Stage 4 needs iv_percentile + iv_rv_ratio in criteria.inputs."""
    history = _hist([0.20, 0.25, 0.30] * 10)
    inputs = Stage3Inputs(
        ticker="REUSE",
        current_iv_30d=0.30,
        iv_history=history,
        rv20=0.30,
    )
    result = evaluate_stage3(inputs, ConvexConfig())
    inputs_dict = result.payload.criteria["inputs"]
    assert "iv_percentile" in inputs_dict
    assert "iv_rv_ratio" in inputs_dict
    assert inputs_dict["iv_30d"] == pytest.approx(0.30)
