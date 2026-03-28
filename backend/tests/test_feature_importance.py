"""Tests for feature importance statistical engine.

Tests pure computation functions with known datasets where
correct answers are deterministic.
"""

from __future__ import annotations

import math

from app.calibration.feature_importance import (
    FEATURE_REGISTRY,
    _cohens_d,
    _pearson_r,
    _permutation_p_value,
    _win_rate,
    compute_feature_stats,
    compute_interactions,
    compute_temporal_stability,
    compute_weight_validation,
    run_feature_importance_analysis,
)
from app.calibration.feature_importance_models import (
    FeatureImportanceResult,
    FeatureStats,
)
from app.calibration.feature_importance_prompt import build_narrative_prompt
from app.core.schemas import PaperPosition, Verdict


def _make_position(
    pnl: float,
    entry_iv_percentile: float | None = None,
    entry_theta_adjusted_edge: float | None = None,
    entry_rs_20d: float | None = None,
    convergence_count: int | None = None,
    entry_spread_pct: float | None = None,
    entry_open_interest: int | None = None,
    entry_volume: int | None = None,
    entry_iv: float | None = None,
    entry_delta: float | None = None,
    dte_at_entry: int | None = None,
    gate_margin: float | None = None,
    conviction_score: float | None = None,
    option_type: str = "CALL",
    scanner_source: str = "BREAKOUT",
    entry_date: str = "2026-03-01",
    verdict: str = "APPROVE",
    **kwargs: object,
) -> PaperPosition:
    """Create a minimal PaperPosition for testing."""
    return PaperPosition(
        evaluation_id="test-eval",
        option_ticker="O:TEST260401C00100000",
        entry_price=1.50,
        entry_date=entry_date,
        current_price=1.50 * (1 + pnl / 100),
        current_pnl_pct=pnl,
        verdict_at_entry=Verdict(verdict) if verdict in ("APPROVE", "WATCH") else verdict,
        status="CLOSED",
        entry_iv_percentile=entry_iv_percentile,
        entry_theta_adjusted_edge=entry_theta_adjusted_edge,
        entry_rs_20d=entry_rs_20d,
        convergence_count=convergence_count,
        entry_spread_pct=entry_spread_pct,
        entry_open_interest=entry_open_interest,
        entry_volume=entry_volume,
        entry_iv=entry_iv,
        entry_delta=entry_delta,
        dte_at_entry=dte_at_entry,
        gate_margin=gate_margin,
        conviction_score=conviction_score,
        option_type=option_type,
        scanner_source=scanner_source,
    )


# ----------------------------------------------------------------
# Core statistical functions
# ----------------------------------------------------------------


class TestPearsonR:
    def test_perfect_positive(self) -> None:
        r = _pearson_r([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
        assert abs(r - 1.0) < 1e-10

    def test_perfect_negative(self) -> None:
        r = _pearson_r([1, 2, 3, 4, 5], [10, 8, 6, 4, 2])
        assert abs(r - (-1.0)) < 1e-10

    def test_no_correlation(self) -> None:
        # These values have zero linear correlation
        r = _pearson_r([1, 2, 3, 4, 5], [3, 3, 3, 3, 3])
        assert abs(r) < 1e-10

    def test_short_list(self) -> None:
        r = _pearson_r([1, 2], [3, 4])
        assert r == 0.0  # Degenerate, returns 0


class TestPermutationPValue:
    def test_significant_correlation(self) -> None:
        xs = list(range(100))
        ys = [x * 2 + 1 for x in xs]  # perfect correlation
        r = _pearson_r(xs, ys)
        p = _permutation_p_value(xs, ys, r)
        assert p < 0.05  # should be highly significant

    def test_random_data_not_significant(self) -> None:
        import random

        rng = random.Random(123)
        xs = [rng.random() for _ in range(50)]
        ys = [rng.random() for _ in range(50)]
        r = _pearson_r(xs, ys)
        p = _permutation_p_value(xs, ys, r)
        # Random data should NOT be significant (most of the time)
        # With seed 123 we should get p > 0.05
        assert p > 0.01  # Very loose bound since random


class TestCohensD:
    def test_large_effect(self) -> None:
        group_a = [10.0, 11.0, 12.0, 10.5, 11.5]
        group_b = [1.0, 2.0, 1.5, 2.5, 1.0]
        d = _cohens_d(group_a, group_b)
        assert d > 2.0  # Very large effect

    def test_no_effect(self) -> None:
        group = [5.0, 5.0, 5.0, 5.0]
        d = _cohens_d(group, group)
        assert abs(d) < 1e-10

    def test_small_groups(self) -> None:
        d = _cohens_d([1.0], [2.0])
        assert d == 0.0  # Not enough data


class TestWinRate:
    def test_all_winners(self) -> None:
        assert _win_rate([10, 5, 3]) == 100.0

    def test_all_losers(self) -> None:
        assert _win_rate([-10, -5, -3]) == 0.0

    def test_half_and_half(self) -> None:
        assert _win_rate([10, -10, 5, -5]) == 50.0

    def test_empty(self) -> None:
        assert _win_rate([]) == 0.0


# ----------------------------------------------------------------
# Feature stats computation
# ----------------------------------------------------------------


class TestComputeFeatureStats:
    def _make_correlated_positions(self, n: int = 100) -> list[PaperPosition]:
        """Create positions where theta_adjusted_edge correlates with P&L."""
        import random

        rng = random.Random(42)
        positions = []
        for i in range(n):
            edge = rng.uniform(0.5, 3.0)
            # P&L positively correlated with edge + noise
            pnl = (edge - 1.5) * 20 + rng.gauss(0, 10)
            positions.append(
                _make_position(
                    pnl=pnl,
                    entry_theta_adjusted_edge=edge,
                    entry_iv_percentile=rng.uniform(10, 90),
                    convergence_count=rng.randint(1, 3),
                    entry_spread_pct=rng.uniform(1, 15),
                    entry_date=f"2026-03-{(i % 28) + 1:02d}",
                )
            )
        return positions

    def test_finds_correlated_feature(self) -> None:
        positions = self._make_correlated_positions(200)
        stats = compute_feature_stats(positions)

        # theta_adjusted_edge should rank highly (positive correlation with P&L)
        edge_stat = next(
            (s for s in stats if s.feature_name == "entry_theta_adjusted_edge"), None
        )
        assert edge_stat is not None
        assert edge_stat.pearson_r > 0.1  # positive correlation
        assert edge_stat.is_significant  # should be significant with 200 samples

    def test_quintiles_computed(self) -> None:
        positions = self._make_correlated_positions(200)
        stats = compute_feature_stats(positions)

        edge_stat = next(
            (s for s in stats if s.feature_name == "entry_theta_adjusted_edge"), None
        )
        assert edge_stat is not None
        assert len(edge_stat.quintiles) == 5
        assert edge_stat.quintiles[0].quintile == 1
        assert edge_stat.quintiles[4].quintile == 5

    def test_sorted_by_absolute_correlation(self) -> None:
        positions = self._make_correlated_positions(200)
        stats = compute_feature_stats(positions)

        for i in range(len(stats) - 1):
            assert abs(stats[i].pearson_r) >= abs(stats[i + 1].pearson_r)

    def test_skips_features_with_too_few_values(self) -> None:
        # Only 10 positions — below MIN_SAMPLE of 20
        positions = [_make_position(pnl=i * 2, entry_iv_percentile=float(i)) for i in range(10)]
        stats = compute_feature_stats(positions)
        assert len(stats) == 0

    def test_win_loss_split(self) -> None:
        positions = self._make_correlated_positions(200)
        stats = compute_feature_stats(positions)

        for s in stats:
            if s.win_mean is not None and s.loss_mean is not None:
                assert s.difference is not None
                assert abs(s.difference - (s.win_mean - s.loss_mean)) < 1e-10


# ----------------------------------------------------------------
# Interactions
# ----------------------------------------------------------------


class TestComputeInteractions:
    def test_finds_interactions(self) -> None:
        import random

        rng = random.Random(99)
        positions = []
        for _ in range(300):
            edge = rng.uniform(0.5, 3.0)
            conv = rng.choice([1, 2, 3])
            # PNL depends on BOTH features
            pnl = (edge - 1.5) * 15 + (conv - 1) * 10 + rng.gauss(0, 8)
            positions.append(
                _make_position(
                    pnl=pnl,
                    entry_theta_adjusted_edge=edge,
                    convergence_count=conv,
                    entry_iv_percentile=rng.uniform(10, 90),
                    entry_spread_pct=rng.uniform(1, 15),
                    entry_date=f"2026-03-{(rng.randint(1, 28)):02d}",
                )
            )

        stats = compute_feature_stats(positions)
        interactions = compute_interactions(positions, stats)
        # Should find at least one interaction
        assert len(interactions) > 0
        # Sorted by lift
        for i in range(len(interactions) - 1):
            assert interactions[i].interaction_lift >= interactions[i + 1].interaction_lift


# ----------------------------------------------------------------
# Weight validation
# ----------------------------------------------------------------


class TestWeightValidation:
    def test_produces_comparisons(self) -> None:
        # Create fake feature stats with known correlations
        stats = [
            FeatureStats(
                feature_name="entry_iv_percentile",
                display_name="IV Percentile",
                pillar="volatility",
                n_valid=100,
                pearson_r=-0.30,
                p_value=0.001,
                is_significant=True,
                win_mean=30.0,
                loss_mean=55.0,
                difference=-25.0,
                effect_size=-0.8,
                direction="lower_is_better",
            ),
            FeatureStats(
                feature_name="entry_theta_adjusted_edge",
                display_name="Theta-Adjusted Edge",
                pillar="volatility",
                n_valid=100,
                pearson_r=0.25,
                p_value=0.005,
                is_significant=True,
                win_mean=2.0,
                loss_mean=1.0,
                difference=1.0,
                effect_size=0.5,
                direction="higher_is_better",
            ),
        ]

        comparisons = compute_weight_validation(stats)
        assert len(comparisons) > 0

        for c in comparisons:
            assert c.pillar in ("directional", "volatility", "structure")
            assert c.recommendation in ("increase", "decrease", "aligned")


# ----------------------------------------------------------------
# Temporal stability
# ----------------------------------------------------------------


class TestTemporalStability:
    def test_produces_windows(self) -> None:
        import random
        from datetime import datetime, timedelta

        rng = random.Random(55)
        positions = []
        base = datetime(2025, 10, 1)
        for i in range(300):
            # Spread positions across 120 days (larger than 60-day window)
            day_offset = i % 120
            date = base + timedelta(days=day_offset)
            # Make edge correlated with P&L so feature is significant
            edge = rng.uniform(0.5, 3.0)
            pnl = (edge - 1.5) * 20 + rng.gauss(0, 10)
            positions.append(
                _make_position(
                    pnl=pnl,
                    entry_theta_adjusted_edge=edge,
                    entry_iv_percentile=rng.uniform(10, 90),
                    entry_date=date.strftime("%Y-%m-%d"),
                )
            )

        stats = compute_feature_stats(positions)
        windows = compute_temporal_stability(positions, stats)
        # With 120 days of data and 60-day windows stepping by 14, should get windows
        assert len(windows) >= 1


# ----------------------------------------------------------------
# Full analysis runner
# ----------------------------------------------------------------


class TestRunFeatureImportanceAnalysis:
    def test_end_to_end(self) -> None:
        import random

        rng = random.Random(77)
        positions = []
        for i in range(150):
            edge = rng.uniform(0.5, 3.0)
            pnl = (edge - 1.5) * 20 + rng.gauss(0, 10)
            positions.append(
                _make_position(
                    pnl=pnl,
                    entry_theta_adjusted_edge=edge,
                    entry_iv_percentile=rng.uniform(10, 90),
                    convergence_count=rng.randint(1, 3),
                    entry_spread_pct=rng.uniform(1, 15),
                    entry_open_interest=rng.randint(50, 5000),
                    entry_volume=rng.randint(10, 500),
                    entry_iv=rng.uniform(0.15, 0.80),
                    entry_delta=rng.uniform(0.2, 0.8),
                    dte_at_entry=rng.randint(7, 60),
                    gate_margin=rng.uniform(0, 1),
                    conviction_score=rng.uniform(50, 100),
                    entry_date=f"2026-{rng.randint(1, 3):02d}-{rng.randint(1, 28):02d}",
                )
            )

        result = run_feature_importance_analysis(positions, period="all", outcome="pnl")

        assert isinstance(result, FeatureImportanceResult)
        assert result.n_positions == 150
        assert len(result.features) > 0
        assert result.overall_win_rate >= 0
        assert result.analysis_id is not None

        # Check to_dict() works
        d = result.to_dict()
        assert d["n_positions"] == 150
        assert isinstance(d["features"], list)
        assert isinstance(d["interactions"], list)
        assert isinstance(d["weight_comparisons"], list)


# ----------------------------------------------------------------
# Prompt builder
# ----------------------------------------------------------------


class TestNarrativePrompt:
    def test_builds_prompt(self) -> None:
        result = FeatureImportanceResult(
            analysis_id="test-123",
            created_at="2026-03-28T00:00:00Z",
            period="all",
            outcome="pnl",
            n_positions=1000,
            overall_win_rate=62.5,
            overall_avg_return=8.3,
            features=[
                FeatureStats(
                    feature_name="entry_theta_adjusted_edge",
                    display_name="Theta-Adjusted Edge",
                    pillar="volatility",
                    n_valid=950,
                    pearson_r=0.32,
                    p_value=0.001,
                    is_significant=True,
                    win_mean=2.1,
                    loss_mean=0.9,
                    difference=1.2,
                    effect_size=0.6,
                    direction="higher_is_better",
                ),
            ],
        )

        prompt = build_narrative_prompt(result)
        assert "1000 closed positions" in prompt
        assert "62.5% win rate" in prompt
        assert "Theta-Adjusted Edge" in prompt
        assert "r=+0.320" in prompt
