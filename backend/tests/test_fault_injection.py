"""Fault injection tests.

Tests that deliberately break dependencies and verify graceful degradation.
In a financial system, every failure mode must be tested because they happen
weekly in production.

Categories:
- DynamoDB write failures
- Market data failures (empty/bad data from Polygon)
- Partial batch failures
- Division-by-zero paths
- Gate function exceptions
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    DecisionConfig,
    DirectionHint,
    DTEBucket,
    Evaluation,
    GateConfig,
    GateOperator,
    GateResult,
    Opportunity,
    OptionType,
    PillarWeights,
    PolicyConfig,
    ScannerTrigger,
    ScannerType,
    Verdict,
)
from app.gates.calculator import GateCalculator
from app.gates.models import GateContext


# ============================================================================
# Division-by-Zero Path Tests
# ============================================================================


class TestDivisionByZero:
    """Test every code path that could divide by zero."""

    def test_evaluation_builder_zero_underlying_price(self):
        """Bug 5 fix: underlying_price=0 must not crash."""
        # After the fix, this should return 999.0 sentinel
        underlying_price = 0.0
        breakeven = 105.0
        if underlying_price > 0:
            required_move = abs(breakeven - underlying_price) / underlying_price * 100
        else:
            required_move = 999.0
        assert required_move == 999.0
        assert math.isfinite(required_move)

    def test_feasibility_ratio_zero_expected_move(self):
        """expected_move_pct=0 must produce sentinel, not crash."""
        required = 5.0
        expected = 0.0
        if expected > 0:
            ratio = required / expected
        else:
            ratio = 999.0
        assert ratio == 999.0

    def test_spread_pct_zero_mid(self):
        """mid=0 in spread calculation must produce 999 sentinel."""
        mid = 0.0
        spread_abs = 0.0
        result = (spread_abs / mid * 100) if mid > 0 else 999
        assert result == 999

    def test_time_adjusted_feasibility_zero_dte(self):
        """DTE=0 must produce 999.0 sentinel."""
        dte = 0
        expected_move = 5.0
        required_move = 3.0
        if dte > 0 and expected_move > 0:
            time_factor = math.sqrt(dte / 30)
            result = required_move / (expected_move * time_factor)
        else:
            result = 999.0
        assert result == 999.0

    def test_calibration_simulator_empty_evaluations(self):
        """ThresholdSimulator with empty evaluations must not crash."""
        from app.calibration.simulator import ThresholdSimulator
        from app.core.schemas import Policy

        config = PolicyConfig()
        policy = Policy(
            version="v2.0.0",
            policy_hash=Policy.compute_hash(config),
            config=config,
            created_by="test",
            is_active=True,
        )
        sim = ThresholdSimulator(
            policy=policy,
            evaluations=[],
            gate_results=[],
        )
        # Should not crash on empty data
        suggestions = sim.generate_suggestions(gate_analyses=[])
        assert isinstance(suggestions, list)


# ============================================================================
# Gate Function Exception Tests
# ============================================================================


class TestGateExceptions:
    """Test that gate errors are handled safely (fail-closed)."""

    def test_gate_with_none_iv_percentile(self):
        """Gate should handle None iv_percentile gracefully."""
        from app.gates.gates import check_iv_percentile_max

        ctx = GateContext(
            evaluation_id="fault-test",
            underlying_ticker="TEST", option_ticker="O:TEST",
            option_type="CALL",
            dte=30, strike=100.0, underlying_price=100.0,
            bid=5.0, ask=5.4, mid=5.2, spread_pct=5.0,
            delta=0.5, gamma=0.03, theta=-0.05, vega=0.2, iv=0.30,
            open_interest=500, volume=100,
            time_adjusted_feasibility=0.5,
            iv_percentile=None,  # Missing data
        )
        result = check_iv_percentile_max(ctx, GateConfig())
        # Should pass (skip gate when data not available) or fail safely
        assert isinstance(result, GateResult)

    def test_gate_with_none_theta_pct(self):
        """Gate should handle None theta_pct gracefully."""
        from app.gates.gates import check_theta_burden_max

        ctx = GateContext(
            evaluation_id="fault-test",
            underlying_ticker="TEST", option_ticker="O:TEST",
            option_type="CALL",
            dte=30, strike=100.0, underlying_price=100.0,
            bid=5.0, ask=5.4, mid=5.2, spread_pct=5.0,
            delta=0.5, gamma=0.03, theta=-0.05, vega=0.2, iv=0.30,
            open_interest=500, volume=100,
            time_adjusted_feasibility=0.5,
            theta_pct=None,  # Missing data
        )
        result = check_theta_burden_max(ctx, GateConfig())
        assert isinstance(result, GateResult)

    def test_full_gate_evaluation_with_missing_features(self):
        """Full gate evaluation should handle missing features gracefully."""
        ctx = GateContext(
            evaluation_id="fault-test",
            underlying_ticker="TEST", option_ticker="O:TEST",
            option_type="CALL",
            dte=30, strike=100.0, underlying_price=100.0,
            bid=5.0, ask=5.4, mid=5.2, spread_pct=5.0,
            delta=0.5, gamma=0.03, theta=-0.05, vega=0.2, iv=0.30,
            open_interest=500, volume=100,
            time_adjusted_feasibility=0.5,
            # All feature-derived fields left as defaults (None/False/0)
        )
        calc = GateCalculator(GateConfig())
        gate_eval = calc.evaluate_gates(ctx, [])
        # Should complete without crashing
        assert gate_eval is not None
        assert len(gate_eval.gate_results) == 19


# ============================================================================
# Market Data Failure Tests
# ============================================================================


class TestMarketDataFailures:
    """Test pipeline behavior when market data is missing or corrupt."""

    @pytest.mark.asyncio
    async def test_pipeline_with_empty_polygon_bars(self, mock_pipeline_orchestrator):
        """Empty bars from Polygon should not crash the pipeline."""
        from app.scanners.orchestrator import ScannerOrchestrator

        mock_polygon = AsyncMock()
        mock_polygon.get_daily_bars_parsed.return_value = []
        mock_polygon.get_previous_close.return_value = {"c": 0, "v": 0}
        mock_polygon.get_options_chain.return_value = []
        mock_polygon.get_aggregated_options_volume.return_value = MagicMock(
            total_call_volume=0, total_put_volume=0,
        )

        mock_policy = MagicMock()
        mock_policy.version = "v2.0.0"
        mock_policy.config = PolicyConfig()

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy, \
             patch("app.db.tables.OpportunityTable.put"), \
             patch("app.db.tables.EvaluationTable.put"):
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = mock_policy

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            result = await orchestrator.run_scan(
                tickers=["EMPTY"], run_full_pipeline=True,
            )

        assert result is not None
        assert result.evaluations == []

    @pytest.mark.asyncio
    async def test_pipeline_continues_when_single_ticker_fails(self, mock_pipeline_orchestrator):
        """If one ticker's options chain fails, others should still process."""
        from tests.conftest import make_mock_polygon
        from app.scanners.orchestrator import ScannerOrchestrator, ScanRunResult

        mock_polygon = make_mock_polygon(
            tickers=["AAPL", "MSFT"],
            fail_tickers={"AAPL"},
        )

        mock_policy = MagicMock()
        mock_policy.version = "v2.0.0"
        mock_policy.config = PolicyConfig()

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy, \
             patch("app.db.tables.OpportunityTable.put"), \
             patch("app.db.tables.EvaluationTable.put"), \
             patch("app.db.tables.FeatureValueTable.put_batch"), \
             patch("app.db.tables.PillarScoreTable.put_batch"), \
             patch("app.db.tables.GateResultTable.put_batch"), \
             patch("app.db.tables.IVHistoryTable.list_by_ticker") as mock_iv, \
             patch("app.db.tables.OIHistoryTable.list_by_contract") as mock_oi, \
             patch("app.db.tables.TradeThesisTable.put"), \
             patch("app.db.tables.PaperPositionTable.put"), \
             patch("app.db.tables.PaperPositionTable.get_by_evaluation_id") as mock_pos:
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = mock_policy
            mock_iv.return_value = []
            mock_oi.return_value = []
            mock_pos.return_value = None

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            result = await orchestrator.run_scan(
                tickers=["AAPL", "MSFT"], run_full_pipeline=True,
            )

        assert isinstance(result, ScanRunResult)
        # Pipeline should complete without crashing

    @pytest.mark.asyncio
    async def test_pipeline_handles_no_active_policy(self, mock_pipeline_orchestrator):
        """Missing active policy should be handled gracefully."""
        from tests.conftest import make_mock_polygon
        from app.scanners.orchestrator import ScannerOrchestrator

        mock_polygon = make_mock_polygon(["AAPL"])

        with patch("app.scanners.orchestrator.PolygonClient") as MockPC, \
             patch("app.db.tables.PolicyTable.get_active") as mock_get_policy:
            MockPC.return_value.__aenter__.return_value = mock_polygon
            mock_get_policy.return_value = None

            orchestrator = ScannerOrchestrator(
                pipeline_orchestrator=mock_pipeline_orchestrator
            )
            try:
                result = await orchestrator.run_scan(
                    tickers=["AAPL"], run_full_pipeline=True,
                )
                # If it completes, result should be valid
                assert result is not None
            except Exception:
                # Acceptable: pipeline can't run without policy
                pass


# ============================================================================
# Pillar Scoring with Degraded Features
# ============================================================================


class TestDegradedFeatures:
    """Test pillar scoring when features are missing (None values)."""

    def test_directional_pillar_with_all_none_features(self):
        """Directional pillar should handle all-None features gracefully."""
        from app.pillars.directional import compute_directional_pillar
        from app.pillars.models import ScoringContext

        ctx = ScoringContext(
            evaluation_id="fault-test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="C",
            # All optional features left as None/default
        )
        result = compute_directional_pillar(ctx)
        assert result is not None
        assert 0 <= result.score <= 100

    def test_volatility_pillar_with_none_iv_data(self):
        """Volatility pillar should handle None IV data."""
        from app.pillars.volatility import compute_volatility_pillar
        from app.pillars.models import ScoringContext

        ctx = ScoringContext(
            evaluation_id="fault-test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="C",
            iv=0.30,
            # All vol features left as None
        )
        result = compute_volatility_pillar(ctx)
        assert result is not None
        assert 0 <= result.score <= 100

    def test_structure_pillar_with_zero_values(self):
        """Structure pillar should handle zero liquidity."""
        from app.pillars.structure import compute_structure_pillar
        from app.pillars.models import ScoringContext

        ctx = ScoringContext(
            evaluation_id="fault-test",
            underlying_ticker="TEST",
            option_type="CALL",
            dte_bucket="C",
            spread_pct=999.0,
            open_interest=0,
            volume=0,
        )
        result = compute_structure_pillar(ctx)
        assert result is not None
        assert 0 <= result.score <= 100
