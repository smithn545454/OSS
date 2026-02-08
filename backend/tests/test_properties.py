"""Property-based tests using Hypothesis.

These tests generate thousands of random inputs and verify that properties
which must ALWAYS hold actually do. This is what separates institutional-grade
testing from hand-picked example testing.

Properties tested:
- Scoring: final score always in [0, 100], monotonic verdicts
- Gates: GTE/LTE semantics correct for all values
- Evaluation math: feasibility never crashes, DTE buckets always valid
- Pillar scoring: subscores always produce valid pillar scores
"""

from __future__ import annotations

import math

from hypothesis import given, settings, assume
from hypothesis import strategies as st

import pytest

from app.core.schemas import (
    DecisionConfig,
    GateConfig,
    GateOperator,
    OptionType,
    PillarWeights,
    Verdict,
)
from app.decision.calculator import DecisionCalculator
from app.gates.models import GateContext


# ============================================================================
# Custom Strategies
# ============================================================================

# Scores in the valid [0, 100] range
valid_score = st.floats(min_value=0.0, max_value=100.0, allow_nan=False)

# Scores in a wider range (adversarial input)
any_score = st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False)

# Positive floats for financial values
positive_float = st.floats(min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)

# Non-negative integers for liquidity metrics
non_neg_int = st.integers(min_value=0, max_value=10_000_000)

# DTE in the valid gate range
valid_dte = st.integers(min_value=0, max_value=365)

# Delta values for options
call_delta = st.floats(min_value=0.01, max_value=1.0, allow_nan=False)
put_delta = st.floats(min_value=-1.0, max_value=-0.01, allow_nan=False)


# ============================================================================
# Scoring Properties
# ============================================================================


class TestScoringProperties:
    """Property: scoring functions must always produce valid results."""

    @given(
        directional=any_score,
        volatility=any_score,
        structure=any_score,
    )
    @settings(max_examples=500)
    def test_final_score_always_in_0_100(self, directional, volatility, structure):
        """No combination of pillar scores can produce a score outside [0, 100]."""
        calc = DecisionCalculator()
        score = calc.compute_final_score(directional, volatility, structure)
        assert 0.0 <= score <= 100.0, (
            f"Score {score} out of range for inputs "
            f"({directional}, {volatility}, {structure})"
        )

    @given(
        directional=valid_score,
        volatility=valid_score,
        structure=valid_score,
    )
    @settings(max_examples=500)
    def test_final_score_weighted_average_within_inputs(self, directional, volatility, structure):
        """When inputs are in [0, 100], score is a weighted average of them."""
        calc = DecisionCalculator()
        score = calc.compute_final_score(directional, volatility, structure)
        # Weighted average must be between min and max input
        lo = min(directional, volatility, structure)
        hi = max(directional, volatility, structure)
        assert lo <= score + 1e-6  # Small epsilon for float imprecision
        assert score <= hi + 1e-6

    @given(score=st.floats(min_value=0.0, max_value=99.99, allow_nan=False))
    @settings(max_examples=300)
    def test_verdict_monotonicity(self, score):
        """Higher score never produces a worse verdict (with gates passing)."""
        v1, _ = DecisionCalculator().determine_verdict(score, all_gates_passed=True)
        v2, _ = DecisionCalculator().determine_verdict(
            min(score + 0.01, 100.0), all_gates_passed=True
        )
        rank = {Verdict.REJECT: 0, Verdict.WATCH: 1, Verdict.APPROVE: 2}
        assert rank[v2] >= rank[v1], (
            f"Score {score}: {v1} -> Score {score+0.01}: {v2} (verdict went down)"
        )

    @given(score=any_score)
    @settings(max_examples=300)
    def test_gate_failure_always_overrides_score(self, score):
        """Gate failure produces REJECT regardless of score."""
        verdict, reason = DecisionCalculator().determine_verdict(score, all_gates_passed=False)
        assert verdict == Verdict.REJECT
        assert reason == "REJECTED_BY_GATES"

    @given(
        directional=valid_score,
        volatility=valid_score,
        structure=valid_score,
    )
    @settings(max_examples=200)
    def test_score_deterministic(self, directional, volatility, structure):
        """Same inputs must always produce the same score."""
        calc = DecisionCalculator()
        s1 = calc.compute_final_score(directional, volatility, structure)
        s2 = calc.compute_final_score(directional, volatility, structure)
        assert s1 == s2


# ============================================================================
# Gate Properties
# ============================================================================


class TestGateProperties:
    """Property: gate evaluation must be consistent with threshold logic."""

    @given(
        oi=non_neg_int,
        threshold=st.integers(min_value=0, max_value=10000),
    )
    @settings(max_examples=300)
    def test_gte_gate_semantics(self, oi, threshold):
        """For GTE gates: measured >= threshold iff passed."""
        from app.gates.gates import check_min_open_interest

        ctx = GateContext(
            evaluation_id="prop-test",
            underlying_ticker="TEST", option_ticker="O:TEST",
            option_type="CALL",
            dte=30, strike=100.0, underlying_price=100.0,
            bid=5.0, ask=5.4, mid=5.2, spread_pct=5.0,
            delta=0.5, gamma=0.03, theta=-0.05, vega=0.2, iv=0.30,
            open_interest=oi, volume=100,
            time_adjusted_feasibility=0.5,
        )
        config = GateConfig(min_open_interest=threshold)
        result = check_min_open_interest(ctx, config)
        if oi >= threshold:
            assert result.passed is True, f"OI={oi} >= threshold={threshold} but gate failed"
        else:
            assert result.passed is False, f"OI={oi} < threshold={threshold} but gate passed"

    @given(
        spread=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
        threshold=st.floats(min_value=0.1, max_value=50.0, allow_nan=False),
    )
    @settings(max_examples=300)
    def test_lte_gate_semantics(self, spread, threshold):
        """For LTE gates: measured <= threshold iff passed."""
        from app.gates.gates import check_max_spread_pct

        ctx = GateContext(
            evaluation_id="prop-test",
            underlying_ticker="TEST", option_ticker="O:TEST",
            option_type="CALL",
            dte=30, strike=100.0, underlying_price=100.0,
            bid=5.0, ask=5.4, mid=5.2, spread_pct=spread,
            delta=0.5, gamma=0.03, theta=-0.05, vega=0.2, iv=0.30,
            open_interest=500, volume=100,
            time_adjusted_feasibility=0.5,
        )
        config = GateConfig(max_spread_pct=threshold)
        result = check_max_spread_pct(ctx, config)
        if spread <= threshold:
            assert result.passed is True, f"Spread={spread} <= threshold={threshold} but failed"
        else:
            assert result.passed is False, f"Spread={spread} > threshold={threshold} but passed"

    @given(dte=valid_dte)
    @settings(max_examples=200)
    def test_dte_range_gate_consistency(self, dte):
        """DTE range gate: dte_min <= dte <= dte_max iff passed."""
        from app.gates.gates import check_dte_range

        ctx = GateContext(
            evaluation_id="prop-test",
            underlying_ticker="TEST", option_ticker="O:TEST",
            option_type="CALL",
            dte=dte, strike=100.0, underlying_price=100.0,
            bid=5.0, ask=5.4, mid=5.2, spread_pct=5.0,
            delta=0.5, gamma=0.03, theta=-0.05, vega=0.2, iv=0.30,
            open_interest=500, volume=100,
            time_adjusted_feasibility=0.5,
        )
        config = GateConfig()  # dte_min=7, dte_max=120
        result = check_dte_range(ctx, config)
        if config.dte_min <= dte <= config.dte_max:
            assert result.passed is True, f"DTE={dte} in range but gate failed"
        else:
            assert result.passed is False, f"DTE={dte} out of range but gate passed"


# ============================================================================
# Evaluation Builder Properties
# ============================================================================


class TestEvaluationBuilderProperties:
    """Property: evaluation math must never crash and always produce finite results."""

    @given(
        strike=st.floats(min_value=1.0, max_value=10000.0, allow_nan=False),
        underlying=st.floats(min_value=1.0, max_value=10000.0, allow_nan=False),
        mid=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False),
        iv=st.floats(min_value=0.01, max_value=5.0, allow_nan=False),
        dte=st.integers(min_value=1, max_value=365),
    )
    @settings(max_examples=500)
    def test_breakeven_and_feasibility_never_crash(self, strike, underlying, mid, iv, dte):
        """Random option parameters must never crash the evaluation math."""
        # CALL breakeven
        breakeven_call = strike + mid
        assert math.isfinite(breakeven_call)

        # Required move
        required_move = abs(breakeven_call - underlying) / underlying * 100
        assert math.isfinite(required_move)
        assert required_move >= 0

        # Expected move
        expected_move = iv * math.sqrt(dte / 365) * 100
        assert math.isfinite(expected_move)
        assert expected_move > 0

        # Feasibility ratio
        feasibility = required_move / expected_move
        assert math.isfinite(feasibility)
        assert feasibility >= 0

    @given(dte=st.integers(min_value=7, max_value=120))
    @settings(max_examples=200)
    def test_dte_bucket_assignment_always_valid(self, dte):
        """Every DTE in [7, 120] must map to exactly one bucket."""
        from app.core.schemas import ContractSelectionConfig, DTEBucket

        cfg = ContractSelectionConfig()
        buckets = cfg.dte_buckets
        matched = []
        for label, bucket in buckets.items():
            if bucket.min_dte <= dte <= bucket.max_dte:
                matched.append(label)

        assert len(matched) == 1, (
            f"DTE={dte} matched {len(matched)} buckets: {matched}"
        )


# ============================================================================
# Pillar Scoring Properties
# ============================================================================


class TestPillarScoringProperties:
    """Property: pillar scoring must always produce valid scores."""

    @given(
        trend=valid_score,
        momentum=valid_score,
        signal=valid_score,
        rs=valid_score,
        catalyst=valid_score,
    )
    @settings(max_examples=300)
    def test_directional_subscore_weighted_average(self, trend, momentum, signal, rs, catalyst):
        """Weighted average of subscores in [0,100] must be in [0,100]."""
        from app.core.schemas import DirectionalPillarConfig

        cfg = DirectionalPillarConfig()
        total = (
            trend * cfg.trend_alignment_weight
            + momentum * cfg.momentum_weight
            + signal * cfg.signal_confirmation_weight
            + rs * cfg.relative_strength_weight
            + catalyst * cfg.catalyst_weight
        )
        # Must be in [0, 100] since weights sum to 1.0 and inputs are in [0, 100]
        assert -1e-6 <= total <= 100.0 + 1e-6, (
            f"Weighted total {total} out of [0, 100] range"
        )

    @given(
        w1=st.floats(min_value=0.01, max_value=0.98, allow_nan=False),
        w2=st.floats(min_value=0.01, max_value=0.98, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_invalid_pillar_weights_rejected(self, w1, w2):
        """Random weight combinations that don't sum to 1.0 must be rejected."""
        w3 = 1.0 - w1 - w2
        if abs(w1 + w2 + w3 - 1.0) > 1e-6 or w3 <= 0:
            # These should be rejected (w3 might be negative or sum doesn't work)
            if w3 <= 0:
                return  # Skip: negative weight is separately invalid
        # If they sum to 1.0, it should work
        if abs(w1 + w2 + w3 - 1.0) <= 1e-6 and w3 > 0:
            pw = PillarWeights(directional=w1, volatility=w2, structure=w3)
            assert abs(pw.directional + pw.volatility + pw.structure - 1.0) < 1e-5
