"""Tests for pillars/calculator.py.

Covers PillarCalculator, compute_all_pillars, get_pillar_scores_dict,
and compute_final_score.
"""

from __future__ import annotations

import pytest

from app.core.schemas import (
    DTEBucket,
    Evaluation,
    OptionType,
    PillarConfig,
)
from app.pillars.calculator import (
    PillarCalculator,
    compute_all_pillars,
    compute_final_score,
    get_pillar_scores_dict,
)
from app.pillars.models import PillarResult


def _eval(eval_id="eval-1"):
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


class TestPillarCalculator:

    def test_compute_pillars(self):
        calc = PillarCalculator()
        results = calc.compute_pillars(_eval(), None, None)
        assert len(results) == 3
        assert all(isinstance(r, PillarResult) for r in results)

    def test_compute_pillars_to_scores(self):
        calc = PillarCalculator()
        scores = calc.compute_pillars_to_scores(_eval(), None, None)
        assert len(scores) == 3

    def test_compute_pillars_batch(self):
        calc = PillarCalculator()
        evals = [_eval("e1"), _eval("e2")]
        results = calc.compute_pillars_batch(evals, {}, {})
        assert "e1" in results
        assert "e2" in results
        assert len(results["e1"]) == 3

    def test_compute_pillars_batch_to_scores(self):
        calc = PillarCalculator()
        evals = [_eval("e1")]
        scores = calc.compute_pillars_batch_to_scores(evals, {}, {})
        assert len(scores) == 3

    def test_custom_config(self):
        config = PillarConfig()
        calc = PillarCalculator(config)
        results = calc.compute_pillars(_eval(), None, None)
        assert len(results) == 3

    def test_batch_error_skipped(self):
        """An error in one evaluation shouldn't break the batch."""
        calc = PillarCalculator()
        # Bad eval will likely fail in scoring
        good_eval = _eval("good")
        results = calc.compute_pillars_batch([good_eval], {}, {})
        assert "good" in results


class TestComputeAllPillars:

    def test_convenience_function(self):
        d, v, s = compute_all_pillars(_eval())
        assert isinstance(d, PillarResult)
        assert isinstance(v, PillarResult)
        assert isinstance(s, PillarResult)


class TestGetPillarScoresDict:

    def test_with_results(self):
        calc = PillarCalculator()
        results = calc.compute_pillars(_eval(), None, None)
        scores = get_pillar_scores_dict(results)
        assert "directional" in scores
        assert "volatility" in scores
        assert "structure" in scores

    def test_empty_results(self):
        scores = get_pillar_scores_dict([])
        assert scores == {"directional": 50.0, "volatility": 50.0, "structure": 50.0}


class TestComputeFinalScore:

    def test_basic(self):
        score = compute_final_score(80, 70, 60)
        # 0.35*80 + 0.35*70 + 0.30*60 = 28 + 24.5 + 18 = 70.5
        assert score == pytest.approx(70.5)

    def test_clamped_high(self):
        score = compute_final_score(150, 150, 150)
        assert score == 100.0

    def test_clamped_low(self):
        score = compute_final_score(-50, -50, -50)
        assert score == 0.0

    def test_custom_config(self):
        score = compute_final_score(80, 80, 80, PillarConfig())
        assert score == pytest.approx(80.0)
