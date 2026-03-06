"""Tests for GateCalculator (gates/calculator.py) and individual gate functions.

Covers evaluate_gates, evaluate_gates_batch, get_failure_summary,
and convenience functions. Also tests individual gate functions from gates.py.
"""

from __future__ import annotations

import pytest

from app.core.schemas import (
    DTEBucket,
    Evaluation,
    GateConfig,
    GateResult,
    OptionType,
)
from app.gates.calculator import (
    GateCalculator,
    check_all_gates_passed,
    evaluate_all_gates,
    get_failed_gates,
)
from app.gates.gates import (
    check_min_open_interest,
    check_min_volume,
    check_max_spread_pct,
    check_dte_range,
    check_move_sufficiency,
    check_iv_percentile_max,
    check_theta_burden_max,
    check_breakout_volume,
    check_greeks_coherence,
)
from app.gates.models import GateContext, GateEvaluation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ctx(
    eval_id="eval-1",
    oi=500,
    volume=200,
    spread_pct=5.0,
    dte=30,
    feasibility=0.8,
    iv_percentile=50,
    theta_pct=2.0,
    delta=0.50,
    gamma=0.03,
    vega=0.25,
    theta=-0.08,
    option_type="CALL",
    scanner_triggers=None,
    volume_ratio=None,
):
    return GateContext(
        evaluation_id=eval_id,
        underlying_ticker="AAPL",
        option_ticker="O:AAPL260320C00185000",
        option_type=option_type,
        strike=185.0,
        underlying_price=180.0,
        bid=4.75,
        ask=5.25,
        mid=5.0,
        iv=0.30,
        open_interest=oi,
        volume=volume,
        spread_pct=spread_pct,
        dte=dte,
        time_adjusted_feasibility=feasibility,
        iv_percentile=iv_percentile,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        theta_pct=theta_pct,
        scanner_triggers=scanner_triggers or [],
        volume_ratio=volume_ratio,
    )


def _config():
    return GateConfig()


def _eval_obj(eval_id="eval-1"):
    return Evaluation(
        evaluation_id=eval_id,
        opportunity_id="opp-1",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL260320C00185000",
        option_type=OptionType.CALL,
        expiration_date="2026-03-20",
        dte=30,
        strike=185.0,
        underlying_price=180.0,
        moneyness_pct=2.78,
        bid=4.0,
        ask=4.5,
        mid=4.25,
        spread_abs=0.5,
        spread_pct=11.76,
        iv=0.30,
        delta=0.50,
        gamma=0.03,
        theta=-0.08,
        vega=0.25,
        open_interest=500,
        volume=200,
        breakeven_price=189.25,
        required_move_pct=5.14,
        expected_move_pct=8.0,
        feasibility_ratio=0.64,
        time_adjusted_feasibility=0.80,
        dte_bucket=DTEBucket.B,
        rank_score=75.0,
        policy_version="v2.0.0",
        policy_hash="hash",
    )


# ---------------------------------------------------------------------------
# Tests: Individual Gate Functions
# ---------------------------------------------------------------------------


class TestCheckMinOpenInterest:

    def test_pass(self):
        result = check_min_open_interest(_make_ctx(oi=500), _config())
        assert result.passed is True

    def test_fail(self):
        result = check_min_open_interest(_make_ctx(oi=100), _config())
        assert result.passed is False

    def test_boundary(self):
        result = check_min_open_interest(_make_ctx(oi=300), _config())
        assert result.passed is True


class TestCheckMinVolume:

    def test_pass(self):
        result = check_min_volume(_make_ctx(volume=200), _config())
        assert result.passed is True

    def test_fail(self):
        result = check_min_volume(_make_ctx(volume=10), _config())
        assert result.passed is False


class TestCheckMaxSpreadPct:

    def test_pass(self):
        result = check_max_spread_pct(_make_ctx(spread_pct=5.0), _config())
        assert result.passed is True

    def test_fail(self):
        result = check_max_spread_pct(_make_ctx(spread_pct=12.0), _config())
        assert result.passed is False


class TestCheckDteRange:

    def test_pass(self):
        result = check_dte_range(_make_ctx(dte=30), _config())
        assert result.passed is True

    def test_fail_too_short(self):
        result = check_dte_range(_make_ctx(dte=3), _config())
        assert result.passed is False

    def test_fail_too_long(self):
        result = check_dte_range(_make_ctx(dte=150), _config())
        assert result.passed is False


class TestCheckMoveSufficiency:

    def test_pass(self):
        result = check_move_sufficiency(_make_ctx(feasibility=0.8), _config())
        assert result.passed is True

    def test_fail(self):
        result = check_move_sufficiency(_make_ctx(feasibility=2.0), _config())
        assert result.passed is False


class TestCheckIVPercentileMax:

    def test_pass(self):
        result = check_iv_percentile_max(_make_ctx(iv_percentile=60), _config())
        assert result.passed is True

    def test_fail(self):
        result = check_iv_percentile_max(_make_ctx(iv_percentile=95), _config())
        assert result.passed is False

    def test_none_fails(self):
        result = check_iv_percentile_max(_make_ctx(iv_percentile=None), _config())
        assert result.passed is False


class TestCheckThetaBurdenMax:

    def test_pass(self):
        result = check_theta_burden_max(_make_ctx(theta_pct=2.0), _config())
        assert result.passed is True

    def test_fail(self):
        result = check_theta_burden_max(_make_ctx(theta_pct=6.0), _config())
        assert result.passed is False

    def test_calculate_from_theta_and_mid(self):
        """When theta_pct is None but mid > 0, calculates from theta and mid."""
        result = check_theta_burden_max(_make_ctx(theta_pct=None), _config())
        # abs(-0.08) / 5.0 * 100 = 1.6%, which passes the 4% threshold
        assert result.passed is True


class TestCheckBreakoutVolume:

    def test_not_breakout_skips(self):
        result = check_breakout_volume(_make_ctx(scanner_triggers=[]), _config())
        assert result.passed is True
        assert result.enabled is False

    def test_breakout_passes(self):
        result = check_breakout_volume(
            _make_ctx(scanner_triggers=["BREAKOUT"], volume_ratio=2.0), _config()
        )
        assert result.passed is True

    def test_breakout_fails(self):
        result = check_breakout_volume(
            _make_ctx(scanner_triggers=["BREAKOUT"], volume_ratio=0.5), _config()
        )
        assert result.passed is False

    def test_breakout_no_volume_data(self):
        result = check_breakout_volume(
            _make_ctx(scanner_triggers=["BREAKDOWN"], volume_ratio=None), _config()
        )
        assert result.passed is False


class TestCheckGreeksCoherence:

    def test_coherent_call(self):
        result = check_greeks_coherence(
            _make_ctx(delta=0.5, theta=-0.08, vega=0.25, gamma=0.03), _config()
        )
        assert result.passed is True

    def test_bad_delta_call(self):
        result = check_greeks_coherence(
            _make_ctx(delta=-0.5, theta=-0.08, vega=0.25, gamma=0.03), _config()
        )
        assert result.passed is False

    def test_positive_theta(self):
        result = check_greeks_coherence(
            _make_ctx(delta=0.5, theta=0.08, vega=0.25, gamma=0.03), _config()
        )
        assert result.passed is False


# ---------------------------------------------------------------------------
# Tests: GateCalculator
# ---------------------------------------------------------------------------


class TestGateCalculator:

    def test_evaluate_gates(self):
        calc = GateCalculator()
        result = calc.evaluate_gates(_eval_obj())
        assert isinstance(result, GateEvaluation)
        assert len(result.gate_results) > 0

    def test_evaluate_gates_to_results(self):
        calc = GateCalculator()
        results = calc.evaluate_gates_to_results(_eval_obj())
        assert isinstance(results, list)
        assert all(isinstance(r, GateResult) for r in results)

    def test_evaluate_gates_batch(self):
        calc = GateCalculator()
        evals = [_eval_obj("e1"), _eval_obj("e2")]
        result = calc.evaluate_gates_batch(evals, {}, {})
        assert "e1" in result
        assert "e2" in result

    def test_evaluate_gates_batch_to_results(self):
        calc = GateCalculator()
        evals = [_eval_obj("e1")]
        result = calc.evaluate_gates_batch_to_results(evals, {}, {})
        assert isinstance(result, list)

    def test_get_failure_summary(self):
        calc = GateCalculator()
        evals = [_eval_obj("e1")]
        batch = calc.evaluate_gates_batch(evals, {}, {})

        summary = calc.get_failure_summary(batch)
        assert isinstance(summary, dict)


# ---------------------------------------------------------------------------
# Tests: Convenience Functions
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:

    def test_evaluate_all_gates(self):
        result = evaluate_all_gates(_eval_obj())
        assert isinstance(result, GateEvaluation)

    def test_check_all_gates_passed(self):
        result = check_all_gates_passed(_eval_obj())
        assert isinstance(result, bool)

    def test_get_failed_gates(self):
        result = get_failed_gates(_eval_obj())
        assert isinstance(result, list)
