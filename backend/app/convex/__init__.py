"""Convex Mode pipeline (four-stage gated scanner, sole production scorer).

Convex Mode targets asymmetric long-premium "exploder" setups via four
binary gates with strength measures and tiered ranking (Tier A/B/C). It
emits Decisions with verdict=CONVEX_APPROVE.

Stages:
    1. Kinetic Universe (monthly refresh) — eligible underlying universe
    2. Catalyst + Direction (daily) — date-known + compression + sympathy
       + 5-day momentum + UV-skew direction resolution
    3. PL Pricing Pre-Screen (daily) — Premium Leverage representative
       score (replaces IV/HV envelope)
    4. Contract Selection + PL recompute (daily) — specific contract +
       PL pillar recomputed on the actual selected contract

Tier mapping (decided after Stage 4):
    Tier A: PL ≥ 80 AND momentum_aligned AND UV detected aligned
    Tier B: PL ≥ 80 AND momentum_aligned
    Tier C: PL ≥ 85 alone, OR PL ≥ 80 + UV detected aligned
"""

from app.convex.backtest import (
    ConvexBacktestConfig,
    ConvexBacktestTrade,
    HistoricalProviders,
    TierStats,
    ValidationReport,
    compute_validation_report,
    report_to_dict,
    resolve_trade_outcome,
    run_backtest_day,
    run_convex_backtest,
    trading_days,
)
from app.convex.historical_providers import (
    HistoricalFuturePriceHistoryProvider,
    HistoricalOptionPriceProvider,
    HistoricalProviderBundle,
    HistoricalStage2InputsProvider,
    HistoricalStage3InputsProvider,
    HistoricalStage4InputsProvider,
)
from app.convex.iv_extraction import (
    CompletenessReport,
    ContractRow,
    IVMetrics,
    extract_iv_metrics,
    summarise_completeness,
)
from app.convex.pipeline import (
    ConvexCandidate,
    ConvexPipeline,
    ConvexPipelineResult,
    Tier,
)
from app.convex.pl_pillar import compute_pl_score
from app.convex.stage1_universe import (
    TickerKineticInputs,
    UniverseBuildResult,
    apply_sector_cap,
    build_universe,
    calculate_realized_volatility,
    count_tail_events,
    evaluate_ticker,
    historical_max_30d_move_pct,
)
from app.convex.stage2_catalyst import (
    CompressionDetection,
    DateKnownDetection,
    MomentumDetection,
    PeerEarningsReaction,
    Stage2Inputs,
    SympathyDetection,
    UVDetection,
    detect_compression_signals,
    detect_date_known_catalyst,
    detect_momentum_signal,
    detect_sympathy,
    detect_unusual_volume,
    evaluate_stage2,
    resolve_direction,
)
from app.convex.stage3_volatility import (
    Stage3Inputs,
    Stage3Result,
    compute_iv_percentile,
    compute_iv_rv_ratio,
    evaluate_stage3,
)
from app.convex.stage4_contract import (
    ConvexContractCandidate,
    RecommendedContract,
    RejectedAlternative,
    Stage4Inputs,
    Stage4Result,
    compute_expected_terminus,
    evaluate_stage4,
    smart_money_confirmation,
)
from app.convex.tier import (
    FinalisedConvexCandidate,
    assign_tier,
    finalise_candidate,
    position_sizing_recommendation,
    within_tier_composite,
)
from app.convex.universe_builder import (
    TickerMetadata,
    TickerMetadataFetcher,
    UniverseConstructor,
)

__all__ = [
    "ConvexCandidate",
    "ConvexPipeline",
    "ConvexPipelineResult",
    "Tier",
    # Stage 1
    "TickerKineticInputs",
    "UniverseBuildResult",
    "apply_sector_cap",
    "build_universe",
    "calculate_realized_volatility",
    "count_tail_events",
    "evaluate_ticker",
    "historical_max_30d_move_pct",
    "TickerMetadata",
    "TickerMetadataFetcher",
    "UniverseConstructor",
    # PL pillar
    "compute_pl_score",
    # Stage 2
    "CompressionDetection",
    "DateKnownDetection",
    "MomentumDetection",
    "PeerEarningsReaction",
    "Stage2Inputs",
    "SympathyDetection",
    "UVDetection",
    "detect_compression_signals",
    "detect_date_known_catalyst",
    "detect_momentum_signal",
    "detect_sympathy",
    "detect_unusual_volume",
    "evaluate_stage2",
    "resolve_direction",
    # Stage 3
    "Stage3Inputs",
    "Stage3Result",
    "compute_iv_percentile",
    "compute_iv_rv_ratio",
    "evaluate_stage3",
    # Stage 4
    "ConvexContractCandidate",
    "RecommendedContract",
    "RejectedAlternative",
    "Stage4Inputs",
    "Stage4Result",
    "compute_expected_terminus",
    "evaluate_stage4",
    "smart_money_confirmation",
    # Tier + Decision emission
    "FinalisedConvexCandidate",
    "assign_tier",
    "finalise_candidate",
    "position_sizing_recommendation",
    "within_tier_composite",
    # Backtest harness (Phase 8)
    "ConvexBacktestConfig",
    "ConvexBacktestTrade",
    "HistoricalProviders",
    "TierStats",
    "ValidationReport",
    "compute_validation_report",
    "report_to_dict",
    "resolve_trade_outcome",
    "run_backtest_day",
    "run_convex_backtest",
    "trading_days",
    # Historical providers (Phase 8 backtest wiring)
    "HistoricalFuturePriceHistoryProvider",
    "HistoricalOptionPriceProvider",
    "HistoricalProviderBundle",
    "HistoricalStage2InputsProvider",
    "HistoricalStage3InputsProvider",
    "HistoricalStage4InputsProvider",
    # IV extraction (Phase 0.5 backfill)
    "ContractRow",
    "IVMetrics",
    "CompletenessReport",
    "extract_iv_metrics",
    "summarise_completeness",
]
