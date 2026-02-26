"""Schema contract regression tests.

These tests act as a safety net to catch silent breaking changes to Pydantic
models and enum definitions. If a default value, field name, or enum member
changes, these tests will fail -- forcing an explicit, intentional update.
"""

import json

import pytest

from app.core.schemas import (
    # Enums
    DirectionHint,
    DTEBucket,
    ExitReason,
    GateOperator,
    IVRegime,
    LLMProvider,
    OptionType,
    PillarId,
    PipelineStage,
    PositionStatus,
    QualityTier,
    RunStatus,
    ScannerType,
    ThesisStatus,
    Verdict,
    ScanRunStatus,
    DisplayStageId,
    TimeRangeOption,
    RuleSeverity,
    StageStatus,
    # Models
    PolicyConfig,
    ScannerConfig,
    UnusualVolumeConfig,
    BreakoutConfig,
    CompressionConfig,
    CheapOptionsConfig,
    UnderlyingFilterConfig,
    ContractSelectionConfig,
    GateConfig,
    PillarWeights,
    DecisionConfig,
    TrackingConfig,
    WatchlistConfig,
    FeatureConfig,
    ThesisConfig,
    Evaluation,
    Opportunity,
    Decision,
    GateResult,
    PillarScore,
    PaperPosition,
    PipelineRun,
    StageEvent,
    FeatureValue,
    OSSBaseModel,
    Policy,
    IVHistory,
    OIHistory,
    TradeThesis,
    LLMUsage,
)


# ============================================================================
# Enum Membership Tests
# ============================================================================


class TestEnumStability:
    """Verify enum members haven't been accidentally added or removed."""

    def test_scanner_type_members(self):
        assert set(ScannerType) == {
            ScannerType.UNUSUAL_VOLUME,
            ScannerType.BREAKOUT,
            ScannerType.BREAKDOWN,
            ScannerType.COMPRESSION_EXPANSION,
            ScannerType.CHEAP_OPTIONS,
        }

    def test_direction_hint_members(self):
        assert set(DirectionHint) == {
            DirectionHint.CALL,
            DirectionHint.PUT,
            DirectionHint.NONE,
        }

    def test_option_type_members(self):
        assert set(OptionType) == {OptionType.CALL, OptionType.PUT}

    def test_dte_bucket_members(self):
        assert set(DTEBucket) == {DTEBucket.A, DTEBucket.B, DTEBucket.C, DTEBucket.D}

    def test_pillar_id_members(self):
        assert set(PillarId) == {
            PillarId.DIRECTIONAL,
            PillarId.VOLATILITY,
            PillarId.STRUCTURE,
        }

    def test_verdict_members(self):
        assert set(Verdict) == {Verdict.APPROVE, Verdict.WATCH, Verdict.REJECT}

    def test_quality_tier_members(self):
        assert set(QualityTier) == {
            QualityTier.TIER_1,
            QualityTier.TIER_2,
            QualityTier.TIER_3,
        }

    def test_pipeline_stage_members(self):
        assert set(PipelineStage) == {
            PipelineStage.OPPORTUNITY_DISCOVERY,
            PipelineStage.UNDERLYING_FILTERS,
            PipelineStage.CONTRACT_SELECTION,
            PipelineStage.FEATURE_COMPUTATION,
            PipelineStage.PILLAR_SCORING,
            PipelineStage.HARD_GATES,
            PipelineStage.DECISION_LOGIC,
            PipelineStage.PAPER_TRADING,
        }
        # Ensure exactly 8 stages
        assert len(PipelineStage) == 8

    def test_exit_reason_members(self):
        assert set(ExitReason) == {
            ExitReason.PROFIT_TARGET,
            ExitReason.STOP_LOSS,
            ExitReason.TIME_EXIT,
            ExitReason.EXPIRATION,
            ExitReason.MANUAL,
        }

    def test_run_status_members(self):
        assert set(RunStatus) == {
            RunStatus.RUNNING,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }

    def test_iv_regime_members(self):
        assert len(IVRegime) == 8

    def test_llm_provider_members(self):
        assert set(LLMProvider) == {LLMProvider.ANTHROPIC, LLMProvider.OPENAI}

    def test_gate_operator_members(self):
        assert set(GateOperator) == {
            GateOperator.GTE,
            GateOperator.LTE,
            GateOperator.BETWEEN,
            GateOperator.EQUALS,
        }


# ============================================================================
# PolicyConfig Default Value Stability
# ============================================================================


class TestPolicyConfigDefaults:
    """Verify PolicyConfig default values haven't drifted.

    These are the thresholds that govern trading decisions. Changing them
    without updating these tests (and the policy seed) risks silent
    changes to production behavior.
    """

    def test_underlying_filter_defaults(self):
        cfg = UnderlyingFilterConfig()
        assert cfg.min_price == 5.00
        assert cfg.min_avg_dollar_volume == 20_000_000
        assert cfg.max_missing_bars == 2
        assert cfg.exclude_earnings_within_days == 5

    def test_gate_config_defaults(self):
        cfg = GateConfig()
        assert cfg.min_open_interest == 300
        assert cfg.min_volume == 75
        assert cfg.max_spread_pct == 8.0
        assert cfg.dte_min == 7
        assert cfg.dte_max == 120
        assert cfg.move_sufficiency_max == 1.25
        assert cfg.iv_percentile_max == 85
        assert cfg.breakout_volume_min == 1.0
        assert cfg.theta_burden_max == 4.0
        assert cfg.trend_alignment_min == 0.6
        assert cfg.iv_rv_ratio_max == 1.5
        assert cfg.feasibility_ratio_max == 1.5
        assert cfg.delta_min == 0.20
        assert cfg.delta_max == 0.70
        assert cfg.max_premium == 20.0
        assert cfg.combined_score_min == 60.0
        assert cfg.pillar_minimum == 45.0
        assert cfg.pillar_spread_max == 40.0

    def test_decision_config_defaults(self):
        cfg = DecisionConfig()
        assert cfg.approve_threshold == 75
        assert cfg.watch_threshold == 65
        assert cfg.tier_1_min_score == 85
        assert cfg.tier_1_min_pillar == 70
        assert cfg.tier_1_max_spread == 5.0
        assert cfg.tier_2_min_pillar == 55

    def test_pillar_weights_defaults(self):
        cfg = PillarWeights()
        assert cfg.directional == 0.35
        assert cfg.volatility == 0.35
        assert cfg.structure == 0.30
        # Weights must sum to 1.0
        assert abs(cfg.directional + cfg.volatility + cfg.structure - 1.0) < 1e-9

    def test_tracking_config_defaults(self):
        cfg = TrackingConfig()
        assert cfg.profit_target_pct == 50.0
        assert cfg.stop_loss_pct == 50.0
        assert cfg.time_exit_dte == 5
        assert cfg.shadow_sample_rate == 0.05
        assert cfg.shadow_near_miss_threshold == 60

    def test_contract_selection_defaults(self):
        cfg = ContractSelectionConfig()
        assert cfg.top_k == 3
        assert cfg.target_delta_call == 0.45
        assert cfg.target_delta_put == -0.45
        assert cfg.min_open_interest == 200
        assert cfg.min_volume == 50
        assert cfg.max_spread_pct == 10.0
        assert cfg.min_mid_price == 0.20
        # Ranking weights must sum to 1.0
        total = cfg.rank_weight_liquidity + cfg.rank_weight_delta + cfg.rank_weight_spread
        assert abs(total - 1.0) < 1e-9

    def test_dte_bucket_ranges(self):
        cfg = ContractSelectionConfig()
        buckets = cfg.dte_buckets
        assert buckets["A"].min_dte == 7
        assert buckets["A"].max_dte == 21
        assert buckets["B"].min_dte == 22
        assert buckets["B"].max_dte == 45
        assert buckets["C"].min_dte == 46
        assert buckets["C"].max_dte == 75
        assert buckets["D"].min_dte == 76
        assert buckets["D"].max_dte == 120

    def test_scanner_config_defaults(self):
        cfg = ScannerConfig()
        assert cfg.unusual_volume.volume_ratio_threshold == 2.0
        assert cfg.unusual_volume.oi_change_threshold_pct == 15.0
        assert cfg.breakout.lookback_days == 20
        assert cfg.compression.atr_period == 14
        assert cfg.compression.compression_multiplier == 1.10
        assert cfg.compression.break_pct == 2.0
        assert cfg.cheap_options.iv_rv_ratio_max == 1.10
        assert cfg.cheap_options.iv_percentile_max == 40

    def test_thesis_config_defaults(self):
        cfg = ThesisConfig()
        assert cfg.enabled is True
        assert cfg.max_daily_calls == 50
        assert cfg.output_token_limit == 1000
        assert cfg.preferred_provider == LLMProvider.OPENAI
        assert cfg.fallback_enabled is True

    def test_feature_config_defaults(self):
        cfg = FeatureConfig()
        assert cfg.iv_percentile_lookback_days == 252
        assert cfg.iv_percentile_min_days == 20
        assert cfg.rs_benchmark_ticker == "SPY"

    def test_watchlist_config_defaults(self):
        cfg = WatchlistConfig()
        assert cfg.tickers == []
        assert cfg.max_concurrent_requests == 50
        assert cfg.batch_size == 100


# ============================================================================
# Model Serialization Round-Trip Tests
# ============================================================================


class TestSerializationRoundTrips:
    """Verify models serialize and deserialize without data loss."""

    def test_policy_config_roundtrip(self):
        original = PolicyConfig()
        dumped = original.model_dump(mode="json")
        restored = PolicyConfig(**dumped)
        assert original == restored

    def test_evaluation_roundtrip(self, sample_evaluation):
        dumped = sample_evaluation.model_dump(mode="json")
        restored = Evaluation(**dumped)
        assert restored.evaluation_id == sample_evaluation.evaluation_id
        assert restored.underlying_ticker == sample_evaluation.underlying_ticker
        assert restored.strike == sample_evaluation.strike

    def test_decision_roundtrip(self, sample_decision):
        dumped = sample_decision.model_dump(mode="json")
        restored = Decision(**dumped)
        assert restored.verdict == sample_decision.verdict
        assert restored.final_score == sample_decision.final_score

    def test_opportunity_roundtrip(self, sample_opportunity):
        dumped = sample_opportunity.model_dump(mode="json")
        restored = Opportunity(**dumped)
        assert restored.underlying_ticker == sample_opportunity.underlying_ticker
        assert restored.priority_score == sample_opportunity.priority_score

    def test_paper_position_roundtrip(self, sample_paper_position):
        dumped = sample_paper_position.model_dump(mode="json")
        restored = PaperPosition(**dumped)
        assert restored.position_id == sample_paper_position.position_id
        assert restored.entry_price == sample_paper_position.entry_price

    def test_policy_hash_stability(self):
        """Same config must always produce the same hash."""
        config1 = PolicyConfig()
        config2 = PolicyConfig()
        assert Policy.compute_hash(config1) == Policy.compute_hash(config2)

    def test_policy_hash_sensitivity(self):
        """Different configs must produce different hashes."""
        config1 = PolicyConfig()
        config2 = PolicyConfig(decision=DecisionConfig(approve_threshold=80))
        assert Policy.compute_hash(config1) != Policy.compute_hash(config2)


# ============================================================================
# DynamoDB Conversion Tests
# ============================================================================


class TestDynamoDBConversion:
    """Verify to_dynamodb_item() produces valid DynamoDB items."""

    def test_floats_become_decimals(self):
        from decimal import Decimal

        cfg = GateConfig()
        item = cfg.to_dynamodb_item()
        assert isinstance(item["max_spread_pct"], Decimal)
        assert isinstance(item["theta_burden_max"], Decimal)

    def test_evaluation_to_dynamodb(self, sample_evaluation):
        item = sample_evaluation.to_dynamodb_item()
        assert isinstance(item, dict)
        assert item["underlying_ticker"] == "AAPL"
        assert "evaluation_id" in item

    def test_nested_config_to_dynamodb(self):
        from decimal import Decimal

        config = PolicyConfig()
        item = config.to_dynamodb_item()
        assert isinstance(item["gates"]["max_spread_pct"], Decimal)


# ============================================================================
# Model Field Presence Tests
# ============================================================================


class TestRequiredFields:
    """Verify critical model fields exist and are typed correctly."""

    def test_evaluation_has_all_required_fields(self):
        fields = Evaluation.model_fields
        required_fields = [
            "evaluation_id", "opportunity_id", "underlying_ticker",
            "option_ticker", "option_type", "expiration_date", "dte",
            "strike", "underlying_price", "bid", "ask", "mid",
            "iv", "delta", "gamma", "theta", "vega",
            "open_interest", "volume", "breakeven_price",
            "required_move_pct", "expected_move_pct",
            "feasibility_ratio", "time_adjusted_feasibility",
            "dte_bucket", "rank_score", "policy_version", "policy_hash",
        ]
        for field_name in required_fields:
            assert field_name in fields, f"Missing required field: {field_name}"

    def test_decision_has_all_required_fields(self):
        fields = Decision.model_fields
        required_fields = [
            "evaluation_id", "verdict", "final_score",
            "directional_score", "volatility_score", "structure_score",
            "primary_reason_code", "supporting_reason_codes",
            "failed_gates", "concentration_warnings", "policy_version",
        ]
        for field_name in required_fields:
            assert field_name in fields, f"Missing required field: {field_name}"

    def test_pipeline_run_has_all_required_fields(self):
        fields = PipelineRun.model_fields
        required_fields = [
            "run_id", "policy_version", "status", "started_at",
            "total_opportunities", "total_evaluations",
            "total_approves", "total_watches", "total_rejects",
        ]
        for field_name in required_fields:
            assert field_name in fields, f"Missing required field: {field_name}"

    def test_opportunity_priority_score_validation(self):
        """priority_score must be 0-100."""
        with pytest.raises(ValueError):
            Opportunity(
                underlying_ticker="AAPL",
                timestamp_utc="2026-01-17T16:00:00+00:00",
                scanner_triggers=[],
                direction_hint=DirectionHint.CALL,
                priority_score=101,  # Invalid
            )

        with pytest.raises(ValueError):
            Opportunity(
                underlying_ticker="AAPL",
                timestamp_utc="2026-01-17T16:00:00+00:00",
                scanner_triggers=[],
                direction_hint=DirectionHint.CALL,
                priority_score=-1,  # Invalid
            )

    def test_pillar_score_range_validation(self):
        """Pillar score must be 0-100."""
        with pytest.raises(ValueError):
            PillarScore(
                evaluation_id="test",
                pillar_id=PillarId.DIRECTIONAL,
                score=101,  # Invalid
                contributors=[],
            )
