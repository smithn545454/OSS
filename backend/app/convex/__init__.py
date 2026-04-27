"""Convex Mode pipeline (parallel four-stage gated scanner).

Convex Mode targets asymmetric long-premium "exploder" setups via four
binary gates with strength measures and tiered ranking (Tier A/B/C). It
runs as a parallel pipeline alongside the legacy 8-stage scanner pipeline
and emits Decisions with verdict=CONVEX_APPROVE.

Stages:
    1. Kinetic Universe (monthly refresh) — eligible underlying universe
    2. Catalyst Layer (daily) — date-known + state-based + UV + sympathy
    3. Volatility Mispricing (daily) — IV cheap relative to setup, direction
    4. Contract Selection (daily) — specific contract recommendation

See:
    - docs/convex-mode-impact-report.md — pre-flight integration analysis
    - Source plan: ~/Downloads/convex-mode-implementation-plan.md
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
    PeerEarningsReaction,
    Stage2Inputs,
    SympathyDetection,
    UVDetection,
    detect_compression_signals,
    detect_date_known_catalyst,
    detect_sympathy,
    detect_unusual_volume,
    evaluate_stage2,
)
from app.convex.stage3_volatility import (
    SkewAssessment,
    Stage3Inputs,
    Stage3Result,
    TermStructureAssessment,
    assess_skew,
    assess_term_structure,
    compute_iv_hv_ratio,
    compute_iv_percentile,
    compute_iv_rank,
    evaluate_stage3,
    infer_direction,
)
from app.convex.stage4_contract import (
    ConvexContractCandidate,
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
    # Stage 2
    "CompressionDetection",
    "DateKnownDetection",
    "PeerEarningsReaction",
    "Stage2Inputs",
    "SympathyDetection",
    "UVDetection",
    "detect_compression_signals",
    "detect_date_known_catalyst",
    "detect_sympathy",
    "detect_unusual_volume",
    "evaluate_stage2",
    # Stage 3
    "SkewAssessment",
    "Stage3Inputs",
    "Stage3Result",
    "TermStructureAssessment",
    "assess_skew",
    "assess_term_structure",
    "compute_iv_hv_ratio",
    "compute_iv_percentile",
    "compute_iv_rank",
    "evaluate_stage3",
    "infer_direction",
    # Stage 4
    "ConvexContractCandidate",
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
