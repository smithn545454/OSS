"""Canonical data schemas for OSS.

All schemas are defined as Pydantic models with:
- Immutable configs (frozen=True)
- to_dynamodb_item() methods for persistence
- Validation rules
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ============================================================================
# Enums
# ============================================================================


class ScannerType(str, Enum):
    """Types of opportunity scanners."""

    UNUSUAL_VOLUME = "UNUSUAL_VOLUME"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"
    COMPRESSION_EXPANSION = "COMPRESSION_EXPANSION"
    CHEAP_OPTIONS = "CHEAP_OPTIONS"


class DirectionHint(str, Enum):
    """Direction hint from scanner."""

    CALL = "CALL"
    PUT = "PUT"
    NONE = "NONE"


class OptionType(str, Enum):
    """Option contract type."""

    CALL = "CALL"
    PUT = "PUT"


class DTEBucket(str, Enum):
    """DTE bucket classification."""

    A = "A"  # 7-21 days (Short-term)
    B = "B"  # 22-45 days (Medium-term)
    C = "C"  # 46-75 days (Intermediate)
    D = "D"  # 76-120 days (Long-term)


class PillarId(str, Enum):
    """Scoring pillar identifiers."""

    DIRECTIONAL = "DIRECTIONAL"
    VOLATILITY = "VOLATILITY"
    STRUCTURE = "STRUCTURE"


class Verdict(str, Enum):
    """Final decision verdict."""

    APPROVE = "APPROVE"
    WATCH = "WATCH"
    REJECT = "REJECT"


class QualityTier(str, Enum):
    """Quality tier for APPROVE decisions."""

    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"


class GateOperator(str, Enum):
    """Gate comparison operator."""

    GTE = "gte"
    LTE = "lte"
    BETWEEN = "between"
    EQUALS = "equals"


class ExitReason(str, Enum):
    """Paper position exit reasons."""

    PROFIT_TARGET = "PROFIT_TARGET"
    STOP_LOSS = "STOP_LOSS"
    TIME_EXIT = "TIME_EXIT"
    MAX_HOLDING = "MAX_HOLDING"
    TRAILING_STOP = "TRAILING_STOP"
    EXPIRATION = "EXPIRATION"
    MANUAL = "MANUAL"


class PositionStatus(str, Enum):
    """Paper position status."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class PipelineStage(str, Enum):
    """Pipeline processing stages."""

    OPPORTUNITY_DISCOVERY = "OPPORTUNITY_DISCOVERY"
    UNDERLYING_FILTERS = "UNDERLYING_FILTERS"
    CONTRACT_SELECTION = "CONTRACT_SELECTION"
    FEATURE_COMPUTATION = "FEATURE_COMPUTATION"
    PILLAR_SCORING = "PILLAR_SCORING"
    HARD_GATES = "HARD_GATES"
    DECISION_LOGIC = "DECISION_LOGIC"
    PAPER_TRADING = "PAPER_TRADING"


class RunStatus(str, Enum):
    """Pipeline run status."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class IVRegime(str, Enum):
    """IV regime classification."""

    IV_LOW_REGIME = "IV_LOW_REGIME"
    IV_COMPRESSED_PRE_CATALYST = "IV_COMPRESSED_PRE_CATALYST"
    IV_NEUTRAL_REGIME = "IV_NEUTRAL_REGIME"
    IV_TRENDING_DOWN = "IV_TRENDING_DOWN"
    IV_TRENDING_UP = "IV_TRENDING_UP"
    IV_HIGH_REGIME = "IV_HIGH_REGIME"
    IV_ELEVATED_POST_CATALYST = "IV_ELEVATED_POST_CATALYST"
    IV_ELEVATED_PRE_CATALYST = "IV_ELEVATED_PRE_CATALYST"


class ThesisStatus(str, Enum):
    """Trade thesis generation status."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    PENDING = "PENDING"


class LLMProvider(str, Enum):
    """Supported LLM providers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


# ============================================================================
# Base Model
# ============================================================================


class OSSBaseModel(BaseModel):
    """Base model with common configuration and methods."""

    model_config = ConfigDict(frozen=True, use_enum_values=True)

    def to_dynamodb_item(self) -> dict[str, Any]:
        """Convert model to DynamoDB item format."""
        data = self.model_dump(mode="json")
        return self._convert_floats_to_decimal(data)

    @staticmethod
    def _convert_floats_to_decimal(obj: Any) -> Any:
        """Recursively convert floats to Decimal for DynamoDB."""
        if isinstance(obj, float):
            return Decimal(str(obj))
        elif isinstance(obj, dict):
            return {k: OSSBaseModel._convert_floats_to_decimal(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [OSSBaseModel._convert_floats_to_decimal(item) for item in obj]
        return obj


# ============================================================================
# Scanner Trigger (Section 8.1)
# ============================================================================


class ScannerTrigger(OSSBaseModel):
    """Scanner trigger details."""

    scanner_type: ScannerType
    reason_codes: list[str]
    metrics: dict[str, Any]  # Can contain floats, ints, strings, bools
    triggered_at: str


# ============================================================================
# Opportunity (Section 8.1)
# ============================================================================


class Opportunity(OSSBaseModel):
    """Ticker-level opportunity from Stage 1."""

    opportunity_id: str = Field(default_factory=lambda: str(uuid4()))
    underlying_ticker: str
    timestamp_utc: str
    scanner_triggers: list[ScannerTrigger]
    direction_hint: DirectionHint
    priority_score: int = Field(ge=0, le=100)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @field_validator("priority_score")
    @classmethod
    def validate_priority_score(cls, v: int) -> int:
        """Ensure priority score is within valid range."""
        if not 0 <= v <= 100:
            raise ValueError("priority_score must be between 0 and 100")
        return v


# ============================================================================
# Evaluation (Section 8.2)
# ============================================================================


class Evaluation(OSSBaseModel):
    """One evaluation per contract."""

    evaluation_id: str = Field(default_factory=lambda: str(uuid4()))
    opportunity_id: str
    underlying_ticker: str
    option_ticker: str
    option_type: OptionType
    expiration_date: str
    dte: int
    strike: float
    underlying_price: float
    moneyness_pct: float
    bid: float
    ask: float
    mid: float
    spread_abs: float
    spread_pct: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    open_interest: int
    volume: int
    oi_5d_change_pct: Optional[float] = None
    breakeven_price: float
    required_move_pct: float
    expected_move_pct: float
    feasibility_ratio: float
    time_adjusted_feasibility: float
    dte_bucket: DTEBucket
    rank_score: float
    policy_version: str
    policy_hash: str  # SHA-256 hash of policy config per Section 7.3
    policy_snapshot_id: Optional[str] = None  # Foreign key to archived policy per Section 7.3
    # Scanner source fields (for direct-to-Stage-4 scanners like Unusual Volume)
    scanner_source: Optional[str] = None  # e.g., "UNUSUAL_VOLUME_SCANNER"
    scanner_metrics: Optional[dict[str, Any]] = None  # Scanner-specific metrics
    trigger_reasons: Optional[list[str]] = None  # Reason codes from scanner
    evaluated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ============================================================================
# Pillar Score (Section 8.3)
# ============================================================================


class PillarContributor(OSSBaseModel):
    """Individual contributor to a pillar score."""

    feature_name: str
    subscore: float
    weight: float
    weighted_contribution: float
    raw_value: Any
    distance_from_neutral: float


class PillarScore(OSSBaseModel):
    """Pillar scoring result."""

    evaluation_id: str
    pillar_id: PillarId
    score: int = Field(ge=0, le=100)
    contributors: list[PillarContributor]
    tags: list[str] = Field(default_factory=list)


# ============================================================================
# Gate Result (Section 8.4)
# ============================================================================


class GateResult(OSSBaseModel):
    """Hard gate evaluation result."""

    evaluation_id: str
    gate_id: str
    enabled: bool
    passed: bool
    measured_value: float
    threshold_value: float
    operator: GateOperator
    units: str
    reason_code: str
    notes: Optional[str] = None
    run_id: Optional[str] = None  # Pipeline run ID for GSI-based retrieval


# ============================================================================
# Decision (Section 8.5)
# ============================================================================


class Decision(OSSBaseModel):
    """Final decision for an evaluation."""

    evaluation_id: str
    verdict: Verdict
    quality_tier: Optional[QualityTier] = None
    final_score: float
    directional_score: float
    volatility_score: float
    structure_score: float
    primary_reason_code: str
    supporting_reason_codes: list[str]
    failed_gates: list[str]
    concentration_warnings: list[str]
    policy_version: str
    decided_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ============================================================================
# Paper Position (Section 8.6)
# ============================================================================


class PaperPosition(OSSBaseModel):
    """Paper trading position."""

    model_config = ConfigDict(frozen=False, use_enum_values=True)  # Mutable for updates

    position_id: str = Field(default_factory=lambda: str(uuid4()))
    evaluation_id: str
    option_ticker: str
    entry_price: float
    entry_date: str
    quantity: int = 1
    verdict_at_entry: Verdict
    quality_tier_at_entry: Optional[QualityTier] = None
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    exit_reason: Optional[ExitReason] = None
    current_price: float
    current_pnl_pct: float
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    days_held: int = 0
    status: PositionStatus = PositionStatus.OPEN
    last_updated: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ai_analysis: Optional[str] = None
    ai_analysis_at: Optional[str] = None

    # Denormalized enrichment fields (populated at creation, avoids eval join)
    underlying_ticker: Optional[str] = None
    scanner_source: Optional[str] = None
    convergence_count: Optional[int] = None
    conviction_score: Optional[float] = None
    pillar_directional: Optional[float] = None
    pillar_volatility: Optional[float] = None
    pillar_structure: Optional[float] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None
    expiration_date: Optional[str] = None
    dte_at_entry: Optional[int] = None
    dte_bucket: Optional[str] = None
    entry_delta: Optional[float] = None
    entry_iv: Optional[float] = None
    entry_theta: Optional[float] = None
    gate_margin: Optional[float] = None
    theta_adj_ev: Optional[float] = None


# ============================================================================
# Pipeline Run & Stage Events
# ============================================================================


class StageEvent(OSSBaseModel):
    """Event record for a pipeline stage."""

    run_id: str
    stage: PipelineStage
    started_at: str
    completed_at: Optional[str] = None
    items_in: int = 0
    items_out: int = 0
    items_dropped: int = 0
    drop_reasons: dict[str, int] = Field(default_factory=dict)
    processing_time_ms: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineRun(OSSBaseModel):
    """Pipeline execution record."""

    model_config = ConfigDict(frozen=False, use_enum_values=True)  # Mutable for updates

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    policy_version: str
    status: RunStatus = RunStatus.RUNNING
    started_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    current_stage: Optional[PipelineStage] = None
    stages_completed: list[PipelineStage] = Field(default_factory=list)
    total_opportunities: int = 0
    total_evaluations: int = 0
    total_approves: int = 0
    total_watches: int = 0
    total_rejects: int = 0
    scanner_type: Optional[str] = None
    error_message: Optional[str] = None
    total_chunks: int = 0
    chunks_completed: int = 0


class PipelineRunCreate(BaseModel):
    """Request to create a pipeline run."""

    policy_version: str


# ============================================================================
# Policy Configuration (Section 7 & Appendix B)
# ============================================================================


class UnusualVolumeConfig(OSSBaseModel):
    """Unusual Volume scanner configuration."""

    volume_ratio_threshold: float = 2.0
    oi_change_threshold_pct: float = 15.0


class BreakoutConfig(OSSBaseModel):
    """Breakout/Breakdown scanner configuration."""

    lookback_days: int = 20


class CompressionConfig(OSSBaseModel):
    """Compression→Expansion scanner configuration."""

    atr_period: int = 14
    compression_multiplier: float = 1.10
    break_pct: float = 2.0


class CheapOptionsConfig(OSSBaseModel):
    """Cheap Options scanner configuration."""

    iv_rv_ratio_max: float = 1.10
    iv_percentile_max: int = 40


class ScannerConfig(OSSBaseModel):
    """All scanner configurations."""

    unusual_volume: UnusualVolumeConfig = Field(default_factory=UnusualVolumeConfig)
    breakout: BreakoutConfig = Field(default_factory=BreakoutConfig)
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    cheap_options: CheapOptionsConfig = Field(default_factory=CheapOptionsConfig)


class UnderlyingFilterConfig(OSSBaseModel):
    """Underlying filter configuration."""

    min_price: float = 5.00
    min_avg_dollar_volume: float = 20_000_000
    max_missing_bars: int = 2
    exclude_earnings_within_days: int = 5  # Days before earnings to exclude (0 = disabled)


class DTEBucketRange(OSSBaseModel):
    """DTE bucket min/max range."""

    min_dte: int
    max_dte: int


class DeltaBand(OSSBaseModel):
    """Delta band min/max."""

    min_delta: float
    max_delta: float


class ContractSelectionConfig(OSSBaseModel):
    """Contract selection configuration."""

    dte_buckets: dict[str, DTEBucketRange] = Field(
        default_factory=lambda: {
            "A": DTEBucketRange(min_dte=7, max_dte=21),
            "B": DTEBucketRange(min_dte=22, max_dte=45),
            "C": DTEBucketRange(min_dte=46, max_dte=75),
            "D": DTEBucketRange(min_dte=76, max_dte=120),
        }
    )
    delta_bands: dict[str, DeltaBand] = Field(
        default_factory=lambda: {
            "CALL": DeltaBand(min_delta=0.20, max_delta=0.75),
            "PUT": DeltaBand(min_delta=-0.75, max_delta=-0.20),
        }
    )
    top_k: int = 3
    target_delta_call: float = 0.45
    target_delta_put: float = -0.45
    min_open_interest: int = 200
    min_volume: int = 50
    max_spread_pct: float = 10.0
    min_mid_price: float = 0.20
    # Ranking weights (Section 12.3) - must sum to 1.0
    rank_weight_liquidity: float = 0.40
    rank_weight_delta: float = 0.35
    rank_weight_spread: float = 0.25


class GateConfig(OSSBaseModel):
    """Hard gate configuration."""

    # Existing thresholds
    min_open_interest: int = 300
    min_volume: int = 75
    max_spread_pct: float = 8.0
    dte_min: int = 7
    dte_max: int = 120
    move_sufficiency_max: float = 1.25
    iv_percentile_max: int = 85
    breakout_volume_min: float = 1.5
    theta_burden_max: float = 4.0

    # Directional confidence thresholds
    trend_alignment_min: float = 0.6

    # Volatility / structure thresholds
    iv_rv_ratio_max: float = 1.5
    feasibility_ratio_max: float = 1.5

    # Risk parameter thresholds
    delta_min: float = 0.20
    delta_max: float = 0.70
    max_premium: float = 20.0

    # Composite threshold gate thresholds
    combined_score_min: float = 75.0
    pillar_minimum: float = 60.0
    pillar_spread_max: float = 30.0

    # Gates to skip entirely (by gate_id, e.g. "GATE_GREEKS_COHERENCE")
    disabled_gates: list[str] = Field(default_factory=list)


class PillarWeights(OSSBaseModel):
    """Pillar weight configuration. Weights must sum to 1.0."""

    directional: float = 0.35
    volatility: float = 0.35
    structure: float = 0.30

    @model_validator(mode="after")
    def _weights_must_sum_to_one(self) -> "PillarWeights":
        total = self.directional + self.volatility + self.structure
        if abs(total - 1.0) > 1e-4:
            raise ValueError(
                f"Pillar weights must sum to 1.0, got {total:.6f} "
                f"(directional={self.directional}, volatility={self.volatility}, "
                f"structure={self.structure})"
            )
        return self


class DirectionalPillarConfig(OSSBaseModel):
    """Directional pillar subscore weights (Section 14.2).
    
    Weights must sum to 1.0.
    """

    # Subscore weights
    trend_alignment_weight: float = 0.30
    momentum_weight: float = 0.25
    signal_confirmation_weight: float = 0.20
    relative_strength_weight: float = 0.15
    catalyst_weight: float = 0.10
    
    # DTE bucket momentum blending weights (return_5d weight, return_20d is 1 - this)
    momentum_blend_bucket_a: float = 0.70  # 7-21 DTE: 70% 5d, 30% 20d
    momentum_blend_bucket_b: float = 0.50  # 22-45 DTE: 50% each
    momentum_blend_bucket_c: float = 0.30  # 46-75 DTE: 30% 5d, 70% 20d
    momentum_blend_bucket_d: float = 0.20  # 76-120 DTE: 20% 5d, 80% 20d

    @model_validator(mode="after")
    def _subscore_weights_must_sum_to_one(self) -> "DirectionalPillarConfig":
        total = (
            self.trend_alignment_weight + self.momentum_weight
            + self.signal_confirmation_weight + self.relative_strength_weight
            + self.catalyst_weight
        )
        if abs(total - 1.0) > 1e-4:
            raise ValueError(
                f"Directional subscore weights must sum to 1.0, got {total:.6f}"
            )
        return self


class VolatilityPillarConfig(OSSBaseModel):
    """Volatility pillar subscore weights (Section 14.3).
    
    Weights must sum to 1.0.
    """

    # Subscore weights
    iv_vs_rv_weight: float = 0.35
    iv_percentile_weight: float = 0.25
    iv_regime_weight: float = 0.20
    theta_adjusted_edge_weight: float = 0.20

    @model_validator(mode="after")
    def _subscore_weights_must_sum_to_one(self) -> "VolatilityPillarConfig":
        total = (
            self.iv_vs_rv_weight + self.iv_percentile_weight
            + self.iv_regime_weight + self.theta_adjusted_edge_weight
        )
        if abs(total - 1.0) > 1e-4:
            raise ValueError(
                f"Volatility subscore weights must sum to 1.0, got {total:.6f}"
            )
        return self


class StructurePillarConfig(OSSBaseModel):
    """Structure pillar subscore weights (Section 14.4).
    
    Weights must sum to 1.0.
    """

    # Subscore weights
    spread_weight: float = 0.30
    open_interest_weight: float = 0.25
    volume_weight: float = 0.20
    theta_burden_weight: float = 0.15
    liquidity_trend_weight: float = 0.10

    @model_validator(mode="after")
    def _subscore_weights_must_sum_to_one(self) -> "StructurePillarConfig":
        total = (
            self.spread_weight + self.open_interest_weight
            + self.volume_weight + self.theta_burden_weight
            + self.liquidity_trend_weight
        )
        if abs(total - 1.0) > 1e-4:
            raise ValueError(
                f"Structure subscore weights must sum to 1.0, got {total:.6f}"
            )
        return self


class PillarConfig(OSSBaseModel):
    """Complete pillar scoring configuration (Section 14)."""

    # Final score weights
    weights: PillarWeights = Field(default_factory=PillarWeights)
    
    # Individual pillar configs
    directional: DirectionalPillarConfig = Field(default_factory=DirectionalPillarConfig)
    volatility: VolatilityPillarConfig = Field(default_factory=VolatilityPillarConfig)
    structure: StructurePillarConfig = Field(default_factory=StructurePillarConfig)


class DecisionConfig(OSSBaseModel):
    """Decision threshold configuration."""

    approve_threshold: int = 75
    watch_threshold: int = 65
    tier_1_min_score: int = 85
    tier_1_min_pillar: int = 70
    tier_1_max_spread: float = 5.0
    tier_2_min_pillar: int = 55


class TrackingConfig(OSSBaseModel):
    """Paper trading configuration."""

    profit_target_pct: float = 50.0
    stop_loss_pct: float = 50.0
    time_exit_dte: int = 5
    shadow_sample_rate: float = 0.05  # 5% of rejects
    shadow_near_miss_threshold: int = 60


class WatchlistConfig(OSSBaseModel):
    """Watchlist configuration for scanners."""

    tickers: list[str] = Field(default_factory=list)
    max_concurrent_requests: int = 50  # Increased from 10 for better throughput
    batch_size: int = 100  # Increased from 25 for larger ticker lists


class FeatureConfig(OSSBaseModel):
    """Feature computation configuration (Section 13)."""

    # IV percentile settings
    iv_percentile_lookback_days: int = 252
    iv_percentile_min_days: int = 20  # Min days required for valid percentile
    
    # IV regime thresholds
    iv_regime_low_percentile: int = 30
    iv_regime_high_percentile: int = 70
    iv_trending_change_threshold: float = 10.0  # 10% change = trending
    
    # Relative strength benchmark
    rs_benchmark_ticker: str = "SPY"
    
    # ATM IV proxy settings (for historical data extraction)
    atm_iv_min_dte: int = 30
    atm_iv_max_dte: int = 45
    atm_delta_target_call: float = 0.50
    atm_delta_target_put: float = -0.50


class ThesisConfig(OSSBaseModel):
    """LLM trade thesis generation configuration (Section 21).

    Controls thesis generation for APPROVE evaluations.
    """

    enabled: bool = True
    max_daily_calls: int = 50
    output_token_limit: int = 1000
    preferred_provider: LLMProvider = LLMProvider.ANTHROPIC
    fallback_enabled: bool = True  # Try alternate provider if preferred fails


class PolicyConfig(OSSBaseModel):
    """Complete policy configuration."""

    scanner: ScannerConfig = Field(default_factory=ScannerConfig)
    underlying_filter: UnderlyingFilterConfig = Field(default_factory=UnderlyingFilterConfig)
    contract_selection: ContractSelectionConfig = Field(default_factory=ContractSelectionConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    gates: GateConfig = Field(default_factory=GateConfig)
    pillars: PillarConfig = Field(default_factory=PillarConfig)
    pillar_weights: PillarWeights = Field(default_factory=PillarWeights)  # Kept for backward compatibility
    decision: DecisionConfig = Field(default_factory=DecisionConfig)
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)
    watchlist: WatchlistConfig = Field(default_factory=WatchlistConfig)
    thesis: ThesisConfig = Field(default_factory=ThesisConfig)  # Phase 10: LLM Integration


class PolicyChangelog(OSSBaseModel):
    """Record of a policy change."""

    field_path: str
    old_value: Any
    new_value: Any
    changed_at: str
    changed_by: str


class Policy(OSSBaseModel):
    """Complete policy with metadata."""

    model_config = ConfigDict(frozen=False, use_enum_values=True)

    version: str
    policy_hash: str
    config: PolicyConfig
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    created_by: str
    is_active: bool = False
    changelog: list[PolicyChangelog] = Field(default_factory=list)

    @staticmethod
    def compute_hash(config: PolicyConfig) -> str:
        """Compute SHA-256 hash of policy configuration."""
        config_json = json.dumps(config.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(config_json.encode()).hexdigest()


class PolicyCreate(BaseModel):
    """Request to create a new policy."""

    config: PolicyConfig
    created_by: str


class PolicyDiff(OSSBaseModel):
    """Diff between two policy versions."""

    version_1: str
    version_2: str
    changes: list[PolicyChangelog]
    identical: bool


# ============================================================================
# Feature Value
# ============================================================================


class FeatureValue(OSSBaseModel):
    """Computed feature value for an evaluation."""

    evaluation_id: str
    feature_name: str
    value: Any
    units: Optional[str] = None
    computed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ============================================================================
# IV History (for IV percentile calculation)
# ============================================================================


class IVHistory(OSSBaseModel):
    """Historical IV data for a ticker.

    Used to calculate IV percentile (252-day rank) for the Cheap Options scanner.
    Records are stored daily and retained for at least 252 trading days.
    """

    ticker: str  # Partition key
    date: str  # Sort key (YYYY-MM-DD)
    atm_iv: float  # ATM IV proxy for that day
    atm_call_iv: Optional[float] = None  # Individual ATM call IV
    atm_put_iv: Optional[float] = None  # Individual ATM put IV
    rv20: Optional[float] = None  # 20-day realized volatility
    iv_rv_ratio: Optional[float] = None  # IV/RV ratio
    recorded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class IVPercentile(OSSBaseModel):
    """Computed IV percentile for a ticker.

    Calculated from IVHistory data over a 252-day window.
    """

    ticker: str
    date: str
    current_iv: float
    percentile: float  # 0-100
    lookback_days: int  # Actual number of days used
    computed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ============================================================================
# OI History (for OI 5-day change calculation)
# ============================================================================


class OIHistory(OSSBaseModel):
    """Historical Open Interest data for a contract.

    Used to calculate oi_5d_change_pct feature.
    Records are stored daily per option contract.
    """

    option_ticker: str  # Partition key (e.g., "O:AAPL240119C00190000")
    date: str  # Sort key (YYYY-MM-DD)
    open_interest: int
    volume: Optional[int] = None
    recorded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ============================================================================
# Unusual Volume Scanner Schemas
# ============================================================================


class SP500Ticker(OSSBaseModel):
    """S&P 500 ticker for unusual volume scanning."""

    ticker: str
    company_name: str
    sector: str
    added_date: str
    is_active: bool = True


class VolumeStatContract(OSSBaseModel):
    """Contract-level volume statistics for unusual volume detection."""

    option_ticker: str  # OCC symbol (e.g., "AAPL250321C00200000")
    underlying_ticker: str
    as_of_date: str
    volume_history_days: int
    avg_volume_20d: Optional[float] = None
    avg_volume_10d: Optional[float] = None
    volume_stddev_20d: Optional[float] = None
    last_volume: Optional[float] = None
    last_oi: Optional[float] = None
    prior_oi: Optional[float] = None
    moneyness_bucket: str  # "DEEP_ITM", "ITM", "ATM", "OTM", "DEEP_OTM"
    dte_bucket: str  # "A", "B", "C", "D", "X"


class VolumeStatBucket(OSSBaseModel):
    """Bucket-level volume statistics for fallback averaging."""

    underlying_ticker: str
    option_type: str  # "CALL" or "PUT"
    moneyness_bucket: str
    dte_bucket: str
    as_of_date: str
    avg_volume_20d: float
    contract_count: int


class UnusualVolumeCandidate(OSSBaseModel):
    """Contract identified as having unusual volume."""

    model_config = ConfigDict(frozen=False, use_enum_values=True)

    scan_id: str
    scan_timestamp: str
    option_ticker: str
    underlying_ticker: str
    option_type: str  # "CALL" or "PUT"
    strike: float
    expiration_date: str
    dte: int
    today_volume: int
    avg_volume_20d: float
    volume_ratio: float
    today_oi: int
    prior_oi: float
    oi_change_pct: float
    trigger_reasons: list[str]
    underlying_price: float
    bid: float
    ask: float
    mid: float
    spread_pct: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    volume_source: str  # "CONTRACT_SPECIFIC" or "BUCKET_FALLBACK"
    priority_score: float
    handoff_status: str = "PENDING"  # "PENDING", "PROCESSED", "FILTERED"
    handoff_filter_reason: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ScanRunStatus(str, Enum):
    """Status of a scan run."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ScanRun(OSSBaseModel):
    """Metadata about an unusual volume scan run."""

    model_config = ConfigDict(frozen=False, use_enum_values=True)

    scan_id: str
    started_at: str
    completed_at: Optional[str] = None
    status: ScanRunStatus = ScanRunStatus.IN_PROGRESS
    tickers_queued: int = 0
    tickers_processed: int = 0
    tickers_failed: int = 0
    candidates_found: int = 0
    candidates_passed_handoff: int = 0
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None


class UnderlyingStats(OSSBaseModel):
    """Pre-computed underlying statistics for Stage 2 filters."""

    ticker: str
    as_of_date: str
    avg_dollar_volume_20d: float
    missing_bars_30d: int = 0
    last_close: Optional[float] = None


# ============================================================================
# Trade Thesis (Section 21 - LLM Integration)
# ============================================================================


class ExitPlanThesis(OSSBaseModel):
    """Exit plan section of trade thesis."""

    profit_target: str
    stop_loss: str
    time_exit: str


class TradeThesis(OSSBaseModel):
    """LLM-generated trade thesis for APPROVE evaluations.

    Per Section 21 of OSS_Complete_Requirements.md.
    Generated only for APPROVE verdicts to explain the trade rationale.
    """

    thesis_id: str = Field(default_factory=lambda: str(uuid4()))
    evaluation_id: str
    setup_summary: str
    thesis: str
    supporting_evidence: list[str]
    risks: list[str]
    invalidation_conditions: list[str]
    exit_plan: ExitPlanThesis
    llm_provider: LLMProvider
    model_used: str
    tokens_used: int
    status: ThesisStatus
    error_message: Optional[str] = None
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class LLMUsage(OSSBaseModel):
    """Daily LLM usage tracking for rate limiting.

    Per Section 21.4: Max 50 LLM calls per day.
    """

    date: str  # YYYY-MM-DD format
    calls_made: int = 0
    tokens_used: int = 0
    last_updated: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ============================================================================
# Pipeline Monitor Display Schemas (oss-pipeline-monitor-requirements.md)
# ============================================================================


class DisplayStageId(int, Enum):
    """Display stage IDs for the 5-stage pipeline view.
    
    Maps the internal 8 stages to 5 user-facing stages.
    """

    DISCOVERY = 1      # OPPORTUNITY_DISCOVERY
    QUALITY_FILTERS = 2  # UNDERLYING_FILTERS
    SELECTION = 3      # CONTRACT_SELECTION
    EVALUATION = 4     # FEATURE_COMPUTATION + PILLAR_SCORING + HARD_GATES
    OUTPUT = 5         # DECISION_LOGIC


class TimeRangeOption(str, Enum):
    """Time range filter options for pipeline monitor."""

    LAST_HOUR = "last_hour"
    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    CUSTOM = "custom"


class RuleSeverity(str, Enum):
    """Severity level for rule failures."""

    NORMAL = "normal"
    CRITICAL = "critical"


class StageStatus(str, Enum):
    """Health status for a stage."""

    HEALTHY = "healthy"
    ANOMALY = "anomaly"


class DisplayRule(OSSBaseModel):
    """Individual rule within a gate (spec section 8.5).
    
    Shows pass/fail counts for a specific rule threshold check.
    """

    name: str  # e.g., "Min Volume ≥ 100"
    passed: int
    failed: int
    severity: RuleSeverity = RuleSeverity.NORMAL


class DisplayFailureOverlap(OSSBaseModel):
    """Multi-rule failure combination (spec section 8.6).
    
    Shows contracts that failed multiple rules simultaneously.
    """

    rules: list[str]  # Array of rule names that failed together
    count: int  # Number of contracts with this failure combination


class DisplayGate(OSSBaseModel):
    """Gate within a stage (spec section 8.4).
    
    Contains multiple rules and overlap analysis.
    """

    id: str  # e.g., "g1a"
    name: str  # e.g., "Liquidity Gate"
    passed: int
    failed: int
    rules: list[DisplayRule]
    overlaps: list[DisplayFailureOverlap] = Field(default_factory=list)


class VerdictBreakdown(OSSBaseModel):
    """Verdict distribution for the final stage (spec section 8.7)."""

    approve: int
    watch: int
    reject: int


class DisplayStage(OSSBaseModel):
    """Pipeline stage for display (spec section 8.3).
    
    The 5-stage display model with optional gates and verdict breakdown.
    """

    id: int  # 1-5
    name: str  # Display name
    description: str
    input: int  # Contracts entering this stage
    output: int  # Contracts passing this stage
    status: StageStatus = StageStatus.HEALTHY
    anomaly_message: Optional[str] = None
    gates: Optional[list[DisplayGate]] = None  # Not present for all stages
    breakdown: Optional[VerdictBreakdown] = None  # Only for final stage


class PipelineMonitorData(OSSBaseModel):
    """Complete pipeline data for the monitor view (spec section 8.2).
    
    Contains the 5-stage pipeline with all nested data.
    """

    time_range: str  # Display string for current range
    scanner_type: str  # Display string for scanner filter
    total_input: int  # Total contracts entering Stage 1
    stages: list[DisplayStage]


class PipelineRunListItem(OSSBaseModel):
    """Enhanced pipeline run for the recent runs list (spec section 5.4).
    
    Adds scanner_type and status indicator for anomaly detection.
    """

    id: str  # run_id
    timestamp: str  # ISO 8601 datetime
    scanner_type: Optional[ScannerType] = None  # Which scanner produced this run
    total_contracts: int
    approved_count: int
    status: StageStatus = StageStatus.HEALTHY


class GetRunsResponse(OSSBaseModel):
    """Response for GET /api/pipeline/runs (spec section 9.1)."""

    runs: list[PipelineRunListItem]
    total: int
    has_more: bool


class GetAggregateResponse(OSSBaseModel):
    """Response for GET /api/pipeline/aggregate (spec section 9.2)."""

    data: PipelineMonitorData


class GetRunDetailResponse(OSSBaseModel):
    """Response for GET /api/pipeline/runs/{runId} (spec section 9.3)."""

    run: PipelineRunListItem
    data: PipelineMonitorData


# ============================================================================
# Backtest Models
# ============================================================================


class BacktestExitConfig(OSSBaseModel):
    """Exit rule configuration for backtesting."""

    stop_loss_pct: float = 50.0
    profit_target_pct: float = 100.0
    min_dte_at_exit: int = 7
    max_holding_days: int = 21
    trailing_stop_pct: Optional[float] = None  # Enable at e.g. 30%


class BacktestGateOverrides(OSSBaseModel):
    """Per-gate overrides for backtesting.

    Allows disabling specific gates or adjusting thresholds when running
    against historical data that may lack real-time bid/ask or liquidity.
    """

    disabled_gates: list[str] = Field(default_factory=list)
    threshold_overrides: dict[str, float] = Field(default_factory=dict)


# Sensible defaults for historical data: relax spread/liquidity gates
# and disable gates that require real-time data unavailable in backtesting
BACKTEST_DEFAULT_GATE_OVERRIDES = BacktestGateOverrides(
    disabled_gates=["GATE_GREEKS_COHERENCE"],
    threshold_overrides={
        "max_spread_pct": 15.0,         # Historical data uses synthetic spreads
        "min_open_interest": 10,         # Historical OI may be incomplete
        "min_volume": 5,                 # Historical volume data sparser
        "combined_score_min": 40.0,      # Relax composite to allow more signals through
        "pillar_minimum": 30.0,          # Relax pillar floor for historical data
        "pillar_spread_max": 50.0,       # Allow wider pillar spread
        "breakout_volume_min": 1.0,      # Relax breakout volume requirement
        "theta_burden_max": 8.0,         # Relax theta burden for historical
        "move_sufficiency_max": 2.0,     # More permissive on move sufficiency
    },
)


class BacktestRunConfig(OSSBaseModel):
    """Configuration for a backtest run."""

    name: str
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    policy_snapshot: PolicyConfig  # Frozen at run creation
    scanners_enabled: list[str] = Field(
        default_factory=lambda: [
            "breakout", "compression", "cheap_options",
        ]
    )
    slippage_model: str = "ask_plus_pct"  # "mid" | "ask" | "ask_plus_pct"
    slippage_pct: float = 0.05  # 5% slippage on ask price
    exit_rules: BacktestExitConfig = Field(default_factory=BacktestExitConfig)
    gate_overrides: BacktestGateOverrides = Field(
        default_factory=lambda: BACKTEST_DEFAULT_GATE_OVERRIDES.model_copy()
    )
    min_option_volume: int = 50
    min_open_interest: int = 200
    starting_capital: float = 10_000.0


class BacktestRun(OSSBaseModel):
    """A backtest run record."""

    model_config = ConfigDict(frozen=False, use_enum_values=True)  # Mutable for status updates

    run_id: str
    config: BacktestRunConfig
    status: str = "PENDING"  # PENDING | RUNNING | COMPLETED | FAILED
    progress: dict = Field(default_factory=lambda: {
        "days_completed": 0,
        "days_total": 0,
        "trades_found": 0,
    })
    summary: Optional[dict] = None
    error: Optional[str] = None
    created_at: str = ""
    completed_at: Optional[str] = None


class BacktestTrade(OSSBaseModel):
    """A single trade from a backtest run."""

    trade_id: str
    run_id: str
    entry_date: str  # YYYY-MM-DD
    exit_date: Optional[str] = None
    ticker: str
    option_ticker: str
    option_type: str  # CALL or PUT
    strike: float
    expiration_date: str
    scanner_type: str
    verdict: str  # APPROVE or WATCH
    combined_score: float
    entry_price: float
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl_dollars: Optional[float] = None
    pnl_pct: Optional[float] = None
    days_held: Optional[int] = None
    mfe_pct: Optional[float] = None  # Maximum favorable excursion
    mae_pct: Optional[float] = None  # Maximum adverse excursion
    peak_price: Optional[float] = None
    market_regime: Optional[str] = None  # bull_trend, low_vol, high_vol, choppy


class BacktestInsight(OSSBaseModel):
    """An AI-generated insight for a backtest run."""

    insight_id: str
    run_id: str
    insight_type: str  # pattern | regime | overfitting | risk | readiness
    severity: str  # info | warning | critical
    title: str
    summary: str
    recommended_action: Optional[str] = None
    confidence: Optional[float] = None
    data_evidence: Optional[dict] = None
    created_at: str = ""
