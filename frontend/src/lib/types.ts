/**
 * TypeScript types matching backend Pydantic schemas.
 */

// Enums
export type ScannerType = 
  | 'UNUSUAL_VOLUME'
  | 'BREAKOUT'
  | 'BREAKDOWN'
  | 'COMPRESSION_EXPANSION'
  | 'CHEAP_OPTIONS'

export type DirectionHint = 'CALL' | 'PUT' | 'NONE'

export type OptionType = 'CALL' | 'PUT'

export type DTEBucket = 'A' | 'B' | 'C' | 'D'

export type PillarId = 'PREMIUM_LEVERAGE' | 'UNDERLYING_BEHAVIOR' | 'SETUP_QUALITY'

export type Verdict = 'APPROVE' | 'WATCH' | 'REJECT'

export type QualityTier = 'TIER_1' | 'TIER_2' | 'TIER_3'

export type GateOperator = 'gte' | 'lte' | 'between' | 'equals'

export type ExitReason = 'PROFIT_TARGET' | 'STOP_LOSS' | 'TIME_EXIT' | 'EXPIRATION' | 'MANUAL'

export type PositionStatus = 'OPEN' | 'CLOSED'

export type PipelineStage =
  | 'OPPORTUNITY_DISCOVERY'
  | 'UNDERLYING_FILTERS'
  | 'CONTRACT_SELECTION'
  | 'FEATURE_COMPUTATION'
  | 'PILLAR_SCORING'
  | 'HARD_GATES'
  | 'DECISION_LOGIC'
  | 'PAPER_TRADING'

export type RunStatus = 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'

export type IVRegime =
  | 'IV_LOW_REGIME'
  | 'IV_COMPRESSED_PRE_CATALYST'
  | 'IV_NEUTRAL_REGIME'
  | 'IV_TRENDING_DOWN'
  | 'IV_TRENDING_UP'
  | 'IV_HIGH_REGIME'
  | 'IV_ELEVATED_POST_CATALYST'
  | 'IV_ELEVATED_PRE_CATALYST'

// Scanner Trigger
export interface ScannerTrigger {
  scanner_type: ScannerType
  reason_codes: string[]
  metrics: Record<string, number>
  triggered_at: string
}

// Opportunity
export interface Opportunity {
  opportunity_id: string
  underlying_ticker: string
  timestamp_utc: string
  scanner_triggers: ScannerTrigger[]
  direction_hint: DirectionHint
  priority_score: number
  created_at: string
}

// Evaluation
export interface Evaluation {
  evaluation_id: string
  opportunity_id: string
  underlying_ticker: string
  option_ticker: string
  option_type: OptionType
  expiration_date: string
  dte: number
  strike: number
  underlying_price: number
  moneyness_pct: number
  bid: number
  ask: number
  mid: number
  spread_abs: number
  spread_pct: number
  iv: number
  delta: number
  gamma: number
  theta: number
  vega: number
  open_interest: number
  volume: number
  oi_5d_change_pct: number | null
  breakeven_price: number
  required_move_pct: number
  expected_move_pct: number
  feasibility_ratio: number
  time_adjusted_feasibility: number
  dte_bucket: DTEBucket
  rank_score: number
  policy_version: string
  evaluated_at: string
  // Live-quote refresh fields (populated post-entry; entry-time bid/ask/mid above are immutable)
  current_bid?: number | null
  current_ask?: number | null
  current_mid?: number | null
  quote_refreshed_at?: string | null
  // Scanner metadata populated for direct-to-Stage-4 scanners (e.g. Unusual Volume).
  // For scanners that flow through the Opportunity path (Cheap Options, Breakout,
  // Breakdown, Compression), the equivalent data lives on the linked
  // ScannerTriggerDetail and these fields may be null.
  scanner_source?: ScannerType | null
  scanner_metrics?: Record<string, unknown> | null
  trigger_reasons?: string[] | null
}

// Pillar Score
export interface PillarContributor {
  feature_name: string
  subscore: number
  weight: number
  weighted_contribution: number
  raw_value: unknown
  distance_from_neutral: number
}

export interface PillarScore {
  evaluation_id: string
  pillar_id: PillarId
  score: number
  contributors: PillarContributor[]
  tags: string[]
}

// Gate Result
export interface GateResult {
  evaluation_id: string
  gate_id: string
  enabled: boolean
  passed: boolean
  measured_value: number
  threshold_value: number
  operator: GateOperator
  units: string
  reason_code: string
  notes: string | null
}

// Decision (Policy v3.0.0)
export interface Decision {
  evaluation_id: string
  verdict: Verdict
  quality_tier: QualityTier | null
  final_score: number
  premium_leverage_score: number
  underlying_behavior_score: number
  setup_quality_score: number
  primary_reason_code: string
  supporting_reason_codes: string[]
  failed_gates: string[]
  concentration_warnings: string[]
  policy_version: string
  decided_at: string
}

// Paper Position (includes denormalized enrichment fields from backend)
export interface PaperPosition {
  position_id: string
  evaluation_id: string
  option_ticker: string
  entry_price: number
  entry_date: string
  quantity: number
  verdict_at_entry: Verdict
  quality_tier_at_entry: QualityTier | null
  exit_price: number | null
  exit_date: string | null
  exit_reason: ExitReason | null
  current_price: number
  current_pnl_pct: number
  max_favorable_excursion: number
  max_adverse_excursion: number
  days_held: number
  status: PositionStatus
  last_updated: string
  // Denormalized enrichment fields (may be null for legacy positions)
  underlying_ticker: string | null
  scanner_source: ScannerType | null
  scanner_list: string[] | null
  convergence_count: number | null
  conviction_score: number | null
  pillar_premium_leverage: number | null
  pillar_underlying_behavior: number | null
  pillar_setup_quality: number | null
  strike: number | null
  option_type: OptionType | null
  expiration_date: string | null
  dte_at_entry: number | null
  dte_bucket: string | null
  entry_delta: number | null
  entry_iv: number | null
  entry_theta: number | null
  gate_margin: number | null
  theta_adj_ev: number | null
  matched_rule_ids: string[] | null
  matched_rules: MatchedRule[] | null
}

// Enriched position — now just an alias since enrichment is on PaperPosition
export interface EnrichedPosition extends PaperPosition {
  // Parsed fallback fields (used by frontend when denormalized fields are null)
  option_type_parsed: 'CALL' | 'PUT' | null
}

// Paginated positions response from /api/paper-trading/positions
export interface PaginatedPositionsResponse {
  positions: PaperPosition[]
  count: number
  next_cursor: string | null
  filter: {
    status: string
    verdict: string | null
    scanner: string | null
    period: string | null
  }
}

// Pre-aggregated summary metrics from /api/paper-trading/summary-metrics
export interface SummaryMetricsResponse {
  global: {
    open_count: number
    closed_count: number
    total_count: number
    win_count: number
    loss_count: number
    total_pnl: number
    win_rate: number
    avg_return: number
    avg_score: number | null
    best_trade_pnl: number | null
    last_updated: string | null
  }
  by_scanner: Record<string, {
    count?: number
    closed_count?: number
    win_count?: number
    loss_count?: number
    total_pnl?: number
  }>
  by_verdict: Record<string, {
    count?: number
    closed_count?: number
    win_count?: number
    loss_count?: number
    total_pnl?: number
  }>
  by_tier: Record<string, {
    count?: number
    closed_count?: number
    win_count?: number
    loss_count?: number
    total_pnl?: number
  }>
  equity_curve: Array<{
    daily_pnl: number
    updated_at?: string
  }>
  by_score_band?: Record<string, { count: number; profitable: number }>
}

// Performance breakdown by option_type, scanner, score bucket
export interface PerformanceBreakdownBucket {
  count: number
  closed: number
  win_rate: number | null
  avg_return: number | null
}

export interface PerformanceBreakdownResponse {
  period: { start: string; end: string; trading_days: number }
  total_positions: number
  total_closed: number
  by_option_type: Record<string, PerformanceBreakdownBucket>
  by_scanner: Record<string, PerformanceBreakdownBucket>
  by_score_bucket: Record<string, PerformanceBreakdownBucket>
}

// Equity curve / snapshots / analysis response types
export interface PaperEquityCurveResponse {
  curve: Array<{ date: string; daily_pnl: number; equity: number }>
  period: string
}

export interface PositionSnapshotsResponse {
  position_id: string
  snapshots: Array<{
    snapshot_date: string
    delta?: number
    theta?: number
    iv?: number
    price?: number
  }>
  count: number
}

export interface PositionAnalysisResponse {
  analysis: string
  cached: boolean
}

// Pipeline Run & Stage Events
export interface StageEvent {
  run_id: string
  stage: PipelineStage
  started_at: string
  completed_at: string | null
  items_in: number
  items_out: number
  items_dropped: number
  drop_reasons: Record<string, number>
  processing_time_ms: number | null
  metadata: Record<string, unknown>
}

export interface PipelineRun {
  run_id: string
  policy_version: string
  status: RunStatus
  started_at: string
  completed_at: string | null
  current_stage: PipelineStage | null
  stages_completed: PipelineStage[]
  total_opportunities: number
  total_evaluations: number
  total_approves: number
  total_watches: number
  total_rejects: number
  error_message: string | null
}

// Policy Configuration
export interface UnusualVolumeConfig {
  volume_ratio_threshold: number
  oi_change_threshold_pct: number
}

export interface BreakoutConfig {
  lookback_days: number
}

export interface CompressionConfig {
  atr_period: number
  compression_multiplier: number
  break_pct: number
}

export interface CheapOptionsConfig {
  iv_rv_ratio_max: number
  iv_percentile_max: number
  require_momentum?: boolean
  rs_5d_threshold?: number
}

export interface ScannerConfig {
  unusual_volume: UnusualVolumeConfig
  breakout: BreakoutConfig
  compression: CompressionConfig
  cheap_options: CheapOptionsConfig
}

export interface UnderlyingFilterConfig {
  min_price: number
  min_avg_dollar_volume: number
  max_missing_bars: number
  exclude_earnings_within_days: number
}

export interface DTEBucketRange {
  min_dte: number
  max_dte: number
}

export interface DeltaBand {
  min_delta: number
  max_delta: number
}

export interface ContractSelectionConfig {
  dte_buckets: Record<string, DTEBucketRange>
  delta_bands: Record<string, DeltaBand>
  top_k: number
  target_delta_call: number
  target_delta_put: number
  min_open_interest: number
  min_volume: number
  max_spread_pct: number
  min_mid_price: number
  rank_weight_liquidity: number
  rank_weight_delta: number
  rank_weight_spread: number
}

export interface GateConfig {
  min_open_interest: number
  min_volume: number
  max_spread_pct: number
  dte_min: number
  dte_max: number
  move_sufficiency_max: number
  iv_percentile_max: number
  breakout_volume_min: number
  theta_burden_max: number
}

// Policy v3.0.0 pillar configuration
export interface PillarWeights {
  premium_leverage: number
  underlying_behavior: number
  setup_quality: number
}

export interface SubscoreBreakpoint {
  value: number
  score: number
}

export interface NumericSubscoreConfig {
  subscore_id: string
  display_name: string
  feature_field: string
  weight: number
  breakpoints: SubscoreBreakpoint[]
  source_tier: string
}

export interface CategoricalSubscoreConfig {
  subscore_id: string
  display_name: string
  feature_field: string
  weight: number
  category_scores: Record<string, number>
  default_score: number
}

export interface PillarConfigV2 {
  pillar_id: PillarId
  display_name: string
  description: string
  numeric_subscores: NumericSubscoreConfig[]
  categorical_subscores: CategoricalSubscoreConfig[]
}

export interface PillarConfig {
  weights: PillarWeights
  premium_leverage: PillarConfigV2
  underlying_behavior: PillarConfigV2
  setup_quality: PillarConfigV2
}

export interface DecisionConfig {
  approve_threshold: number
  watch_threshold: number
  tier_1_min_score: number
  tier_1_min_pillar: number
  tier_1_max_spread: number
  tier_2_min_pillar: number
}

export interface TrackingConfig {
  profit_target_pct: number
  stop_loss_pct: number
  time_exit_dte: number
  shadow_sample_rate: number
  shadow_near_miss_threshold: number
}

export interface WatchlistConfig {
  tickers: string[]
  universe: 'sp500' | 'russell1000' | 'custom'
  max_concurrent_requests: number
  batch_size: number
}

export interface PolicyConfig {
  scanner: ScannerConfig
  underlying_filter: UnderlyingFilterConfig
  contract_selection: ContractSelectionConfig
  gates: GateConfig
  pillars: PillarConfig
  decision: DecisionConfig
  tracking: TrackingConfig
  watchlist: WatchlistConfig
}

export interface PolicyChangelog {
  field_path: string
  old_value: unknown
  new_value: unknown
  changed_at: string
  changed_by: string
}

export interface Policy {
  version: string
  policy_hash: string
  config: PolicyConfig
  created_at: string
  created_by: string
  is_active: boolean
  changelog: PolicyChangelog[]
}

export interface PolicyDiff {
  version_1: string
  version_2: string
  changes: PolicyChangelog[]
  identical: boolean
}

// API Response Types
export interface HealthResponse {
  status: string
  app: string
  version: string
  timestamp: string
}

export interface PoliciesListResponse {
  policies: Policy[]
  count: number
}

export interface PipelineRunsListResponse {
  runs: PipelineRun[]
  count: number
}

export interface PipelineStatsResponse {
  period_days: number
  total_runs: number
  completed_runs: number
  failed_runs: number
  total_opportunities: number
  total_evaluations: number
  total_approves: number
  total_watches: number
  total_rejects: number
  avg_opportunities_per_run: number
  avg_evaluations_per_run: number
  approve_rate_pct: number
  watch_rate_pct: number
  reject_rate_pct: number
}

// Evaluation Detail Types (Section 19.1)
export interface EvaluationDetailResponse {
  evaluation: Evaluation & { decision?: Decision }
  thetaAdjustedEV: number
  company_name: string | null
  pillar_scores: PillarScoreDetail[]
  gate_results: GateResultDetail[]
  position: PaperPositionDetail | null
  scanner_triggers: ScannerTriggerDetail[]
  features: Record<string, FeatureDetail>
  thesis: TradeThesis | null
  matched_rules?: MatchedRule[]
  summary: EvaluationSummary
}

export interface PillarScoreDetail {
  pillar_id: PillarId
  score: number
  contributors: PillarContributorDetail[]
  tags: string[]
}

export interface PillarContributorDetail {
  feature_name: string
  subscore: number
  weight: number
  weighted_contribution: number
  raw_value: unknown
  distance_from_neutral: number
}

export interface GateResultDetail {
  gate_id: string
  enabled: boolean
  passed: boolean
  measured_value: number
  threshold_value: number
  operator: GateOperator
  units: string
  reason_code: string
  notes: string | null
}

export interface PaperPositionDetail {
  position_id: string
  option_ticker: string
  entry_price: number
  entry_date: string
  quantity: number
  verdict_at_entry: Verdict
  quality_tier_at_entry: QualityTier | null
  exit_price: number | null
  exit_date: string | null
  exit_reason: ExitReason | null
  current_price: number
  current_pnl_pct: number
  max_favorable_excursion: number
  max_adverse_excursion: number
  days_held: number
  status: PositionStatus
  last_updated: string
}

export interface ScannerTriggerDetail {
  scanner_type: ScannerType
  reason_codes: string[]
  metrics: Record<string, unknown>
  triggered_at: string
}

export interface FeatureDetail {
  value: unknown
  units: string
  computed_at: string
}

export interface EvaluationSummary {
  all_gates_passed: boolean
  failed_gates: string[]
  pillar_count: number
  gate_count: number
  has_position: boolean
  feature_count: number
  has_thesis: boolean
}

// Evaluation List Filters
export interface EvaluationFilters {
  verdict?: Verdict
  dte_bucket?: DTEBucket
  option_type?: OptionType
  limit?: number
}

export interface EvaluationListResponse {
  evaluations: (Evaluation & { decision?: Decision })[]
  count: number
  filters: EvaluationFilters
  statistics: {
    total: number
    by_verdict: Record<string, number>
    by_dte_bucket: Record<string, number>
    by_option_type: Record<string, number>
  }
}

// Calibration Types (Section 20)
export interface CalibrationReport {
  report_id: string
  week_start: string
  week_end: string
  generated_at: string
  positions_closed: number
  win_rate: number
  avg_return: number
  gate_analyses: GateAnalysis[]
  suggestions: ThresholdSuggestion[]
  score_band_analysis: ScoreBandAnalysis[]
}

export interface GateAnalysis {
  gate_id: string
  rejection_count: number
  rejection_rate: number
  false_negative_count: number
  false_negative_rate: number
  effectiveness_score: number
  recommendation: 'TIGHTEN' | 'LOOSEN' | 'NO_CHANGE'
}

export interface ThresholdSuggestion {
  suggestion_id: string
  gate_id: string
  field_path: string
  current_value: number
  suggested_value: number
  estimated_impact: {
    additional_approvals: number
    estimated_win_rate_change: number
  }
  status: 'PENDING' | 'APPROVED' | 'REJECTED'
  reason: string
}

export interface ScoreBandAnalysis {
  band: string
  min_score: number
  max_score: number
  count: number
  win_rate: number
  avg_return: number
}

export interface CalibrationReportsResponse {
  reports: CalibrationReport[]
  count: number
}

// Trade Thesis Types (Section 21 - LLM Integration)
export type ThesisStatus = 'COMPLETED' | 'FAILED' | 'RATE_LIMITED' | 'PENDING' | 'GENERATING'

export type LLMProvider = 'anthropic' | 'openai'

export interface TakeProfitTarget {
  tier: number
  option_pnl_pct: number
  underlying_price: number
  rationale: string
}

export interface StopLossTarget {
  option_pnl_pct: number
  underlying_price: number
  rationale: string
}

export interface TimeExitTarget {
  dte_threshold: number
  rationale: string
}

export interface ExitPlanThesis {
  profit_target: string
  stop_loss: string
  time_exit: string
  take_profits?: TakeProfitTarget[]
  stop_loss_level?: StopLossTarget | null
  time_exit_level?: TimeExitTarget | null
}

export interface TradeThesis {
  thesis_id: string
  evaluation_id: string
  status: ThesisStatus
  setup_summary: string
  thesis: string
  supporting_evidence: string[]
  risks: string[]
  invalidation_conditions: string[]
  exit_plan: ExitPlanThesis
  llm_provider: LLMProvider
  model_used: string
  tokens_used: number
  generated_at: string
  error_message: string | null
}

// Stock Summary Types (AI-generated underlying stock context)
export type StockSummaryStatus = 'COMPLETED' | 'FAILED' | 'RATE_LIMITED' | 'GENERATING' | 'NOT_FOUND'

export interface MaterialEvent {
  event: string
  date: string
  impact: string
  severity: 'HIGH' | 'MEDIUM' | 'LOW'
}

export interface StockSummary {
  summary_id: string
  ticker: string
  status: StockSummaryStatus
  company_snapshot: string
  sector_context: string
  material_events: MaterialEvent[]
  trading_considerations: string[]
  trade_impact_assessment: string
  risk_level: string
  risk_level_rationale: string
  llm_provider: LLMProvider
  model_used: string
  tokens_used: number
  generated_at: string
  error_message: string | null
}

export interface LLMUsageStats {
  date: string
  calls_made: number
  tokens_used: number
  remaining: number
  limit: number
}

export interface LLMUsageResponse {
  today: LLMUsageStats
  recent: Array<{
    date: string
    calls_made: number
    tokens_used: number
  }>
}

export interface LLMConfigResponse {
  enabled: boolean
  max_daily_calls: number
  output_token_limit: number
  preferred_provider: LLMProvider
  fallback_enabled: boolean
}

// Representative Trace Sampling Types (Section 18.3)
export interface TraceSample {
  evaluation_id: string
  ticker: string
  option_ticker: string
  option_type: OptionType
  strike: number
  dte: number
  dte_bucket: DTEBucket
  final_score: number
  verdict: Verdict
  quality_tier: QualityTier | null
  evaluated_at: string
  timestamp: string
  failed_gates: string[]
  primary_reason: string | null
}

export interface GateFailureSample {
  gate_id: string
  failure_count: number
  sample_evaluations: TraceSample[]
}

export interface RepresentativeTracesSummary {
  total_rejects_sampled: number
  total_approves_sampled: number
  total_tier_1: number
  unique_gates_failed: number
}

export interface RepresentativeTracesResponse {
  common_gate_failures: GateFailureSample[]
  highest_reject_scores: TraceSample[]
  lowest_approve_scores: TraceSample[]
  tier_1_approvals: TraceSample[]
  summary: RepresentativeTracesSummary
}

// ============================================================================
// Pipeline Monitor Types (oss-pipeline-monitor-requirements.md)
// ============================================================================

/**
 * Time range filter options for pipeline monitor.
 */
export type TimeRangeOption =
  | 'last_hour'
  | 'today'
  | 'yesterday'
  | 'last_7_days'
  | 'last_30_days'
  | 'custom'

/**
 * Pipeline scanner types that can produce runs.
 */
export type PipelineMonitorScannerType =
  | 'unusual_volume'
  | 'breakout'
  | 'compression'
  | 'cheap_options'
  | 'all'

/**
 * Severity level for rule failures.
 */
export type RuleSeverity = 'normal' | 'critical'

/**
 * Health status for a stage.
 */
export type StageStatus = 'healthy' | 'anomaly'

/**
 * Individual rule within a gate (spec section 8.5).
 */
export interface DisplayRule {
  name: string
  passed: number
  failed: number
  severity: RuleSeverity
}

/**
 * Multi-rule failure combination (spec section 8.6).
 */
export interface DisplayFailureOverlap {
  rules: string[]
  count: number
}

/**
 * Gate within a stage (spec section 8.4).
 */
export interface DisplayGate {
  id: string
  name: string
  passed: number
  failed: number
  rules: DisplayRule[]
  overlaps: DisplayFailureOverlap[]
}

/**
 * Verdict distribution for the final stage (spec section 8.7).
 */
export interface VerdictBreakdown {
  approve: number
  watch: number
  reject: number
}

/**
 * Pipeline stage for display (spec section 8.3).
 */
export interface DisplayStage {
  id: number
  name: string
  description: string
  input: number
  output: number
  status: StageStatus
  anomaly_message?: string | null
  gates?: DisplayGate[] | null
  breakdown?: VerdictBreakdown | null
}

/**
 * Complete pipeline data for the monitor view (spec section 8.2).
 */
export interface PipelineMonitorData {
  time_range: string
  scanner_type: string
  total_input: number
  stages: DisplayStage[]
}

/**
 * Enhanced pipeline run for the recent runs list (spec section 5.4).
 */
export interface PipelineRunListItem {
  id: string
  timestamp: string
  scanner_type: ScannerType | null
  total_contracts: number
  approved_count: number
  status: StageStatus
}

/**
 * Response for GET /api/pipeline/runs (spec section 9.1).
 */
export interface GetRunsResponse {
  runs: PipelineRunListItem[]
  total: number
  has_more: boolean
}

/**
 * Response for GET /api/pipeline/aggregate (spec section 9.2).
 */
export interface GetAggregateResponse {
  data: PipelineMonitorData
}

/**
 * Response for GET /api/pipeline/runs/{runId} (spec section 9.3).
 */
export interface GetRunDetailResponse {
  run: PipelineRunListItem
  data: PipelineMonitorData
}

/**
 * Pipeline Monitor state (spec section 7.1).
 */
export interface PipelineMonitorState {
  // Filter state
  timeRange: TimeRangeOption
  customDateRange: { start: Date; end: Date } | null
  scannerFilter: PipelineMonitorScannerType
  verdictFilters: Verdict[]
  dteBucketFilters: DTEBucket[]
  optionSideFilters: OptionType[]

  // Selection state
  selectedRunId: string | null

  // UI state
  expandedGates: Set<string>
  expandedOverlaps: Set<string>

  // Data state
  isLoading: boolean
  error: Error | null
}

// ============================================================================
// Opportunities Page Types (OSS_Opportunities_Page_Specification)
// ============================================================================

/**
 * Market status for Context Bar (spec section 7).
 */
export type MarketStatus = 'pre' | 'open' | 'after' | 'closed'

/**
 * Urgency level for opportunity cards (spec section 4.2.5).
 */
export type UrgencyLevel = 'act_now' | 'hours' | 'patient'

/**
 * Market context for the Context Bar component.
 */
export interface MarketContext {
  spy: {
    price: number
    change: number
    changePercent: number
  }
  vix: {
    price: number
    change: number
    direction: 'up' | 'down'
  }
  marketStatus: MarketStatus
  lastPipelineRun: string | null
}

/**
 * Opportunity filters for premium, delta, and moneyness.
 */
export interface OpportunityFilters {
  premiumMax: number | null
  premiumMin: number | null
  deltaMax: number | null
  deltaMin: number | null
  moneyness: 'all' | 'otm' | 'atm' | 'itm'
}

/**
 * Enhanced APPROVE evaluation for Opportunities page.
 */
export interface ApproveEvaluation extends Evaluation {
  decision: Decision
  pillarScores: Record<PillarId, number>
  gateResults: GateResultDetail[]
  gateMargin: number
  scannerSource: ScannerType[]
  scannerConvergence: number
  thetaAdjustedEV: number
  urgency: UrgencyLevel
  headline: string | null
  approvalCount?: number
  // Computed client-side
  convictionScore?: number
  alertedAt?: string
  // Setup rule matches (from backend)
  matchedRules?: MatchedRule[]
}

/**
 * Response for GET /api/evaluations/approve.
 */
export interface ApproveEvaluationsResponse {
  evaluations: ApproveEvaluation[]
  excludedForEarnings: EarningsExclusion[]
  meta: {
    total: number
    excludedCount: number
    generatedAt: string
    maxAgeTradingDays?: number
    cutoffTimestamp?: string
  }
}

/**
 * Earnings exclusion info.
 */
export interface EarningsExclusion {
  ticker: string
  earningsDate: string
  contractCount: number
}

/**
 * WATCH insight types (spec section 11.8).
 */
export type WatchInsightType = 'gate_pressure' | 'recurring_near_miss' | 'watch_to_approve'

/**
 * Base WATCH insight.
 */
export interface WatchInsight {
  type: WatchInsightType
  headline: string
  subInsight?: string
  contracts?: string[]
  actionLink?: {
    label: string
    url: string
  }
}

/**
 * Gate pressure insight.
 */
export interface GatePressureInsight extends WatchInsight {
  type: 'gate_pressure'
  gateName: string
  failCount: number
  percentage: number
  highPotentialCount: number
}

/**
 * Recurring near-miss insight.
 */
export interface RecurringNearMissInsight extends WatchInsight {
  type: 'recurring_near_miss'
  contractId: string
  ticker: string
  strike: number
  expiration: string
  occurrences: number
  failingGate: string | null
}

/**
 * WATCH to APPROVE conversion insight.
 */
export interface WatchToApproveInsight extends WatchInsight {
  type: 'watch_to_approve'
  conversions: Array<{
    contractId: string
    ticker: string
    strike: number
    expiration: string
    gatePassed: string | null
  }>
}

/**
 * Response for GET /api/evaluations/watch/insights.
 */
export interface WatchInsightsResponse {
  insights: WatchInsight[]
  watchCount: number
  generatedAt: string
}

/**
 * Opportunities page configuration (spec section 17).
 */
export interface OpportunitiesConfig {
  scoring: {
    holdingPeriodMomentum: number
    holdingPeriodStructural: number
  }
  convictionQueue: {
    threshold: number
  }
  slackAlerts: {
    enabled: boolean
    scoreThreshold: number
    requireUrgencyOrConvergence: boolean
    cooldownMinutes: number
    dailyCap: number
    quietHoursStart: string
    quietHoursEnd: string
  }
  earningsFilter: {
    exclusionDays: number
    showNotice: boolean
  }
}

/**
 * Contract quotes for real-time price updates (spec section 19.3).
 */
export interface ContractQuote {
  bid: number
  ask: number
  mid: number
  last?: number
  iv: number | null
  delta: number | null
  theta: number | null
  volume: number
  openInterest: number
  updatedAt: string
}

export interface ContractQuotesResponse {
  quotes: Record<string, ContractQuote>
}

// ============================================================================
// Paper Trading Workstation Types
// ============================================================================

export interface PaperTradingSummary {
  positions: { open: number; closed: number; total: number }
  open_positions_summary: {
    total_pnl_pct: number
    avg_pnl_pct: number
    positions_in_profit: number
    positions_in_loss: number
  }
  performance: {
    win_rate: number
    avg_win_pct: number
    avg_loss_pct: number
    expectancy: number
  }
  recent_closes: Array<{
    option_ticker: string
    exit_date: string | null
    exit_reason: string | null
    pnl_pct: number
  }>
}

export interface PaperTradingPositionsResponse {
  positions: PaperPosition[]
  count: number
  filter: { status: string } | null
}

export interface PerformanceMetricsData {
  total_positions: number
  open_positions: number
  closed_positions: number
  win_count: number
  loss_count: number
  win_rate: number
  loss_rate: number
  avg_win_pct: number
  avg_loss_pct: number
  best_trade_pct: number
  worst_trade_pct: number
  expectancy: number
  profit_factor: number | null
  avg_mfe: number
  avg_mae: number
  max_mfe: number
  max_mae: number
  mfe_capture_ratio: number
  avg_days_held: number
  max_days_held: number
  exit_distribution: Record<string, number>
  tier_performance: Record<
    string,
    {
      win_count: number
      loss_count: number
      win_rate: number
      avg_return_pct: number
    }
  >
  approve_performance: Record<string, number>
  watch_performance: Record<string, number>
}

export interface PaperTradingMetricsResponse {
  metrics: PerformanceMetricsData
  targets: Record<string, string>
}

export interface PaperTradingTiersResponse {
  tier_comparison: Record<
    string,
    {
      total: number
      closed: number
      wins: number
      losses: number
      win_rate: number
      avg_return: number
    }
  >
  expectation: string
}

export interface PaperTradingExitsResponse {
  exit_analysis: Record<
    string,
    {
      count: number
      avg_return: number
      avg_mfe: number
      avg_mae: number
      avg_days_held: number
      mfe_left_on_table?: number
    }
  >
  insights: string[]
}

export type InsightCategory =
  | 'GATE_TUNING'
  | 'EXIT_STRATEGY'
  | 'TIER_QUALITY'
  | 'SCORE_OPTIMIZATION'
  | 'RISK_MANAGEMENT'

export type InsightSeverity = 'HIGH' | 'MEDIUM' | 'LOW'

export interface AIInsight {
  category: InsightCategory
  severity: InsightSeverity
  title: string
  description: string
  data_points: Record<string, string | number>
  suggested_action: string
  estimated_impact: string
}

export interface AIInsightsResponse {
  insights: AIInsight[]
  generated_at: string
  data_summary: {
    positions_analyzed: number
    win_rate: number
    closed_positions: number
    calibration_report_used: string | null
  }
  message?: string
  llm_provider: string | null
  tokens_used: number
}

export interface UpdatePositionsResponse {
  success: boolean
  positions_updated: number
  exits_triggered: number
  exit_details: Array<{
    position_id: string
    option_ticker: string
    exit_reason: string | null
    final_pnl_pct: number
  }>
  errors: number
}

// ============================================================================
// Backtest Types
// ============================================================================

export type DatasetStatusLevel = 'complete' | 'partial' | 'missing' | 'error' | 'unknown'

export interface DatasetStatus {
  name: string
  prefix: string
  file_count: number
  date_count: number
  earliest_date: string | null
  latest_date: string | null
  total_size_mb: number
  status: DatasetStatusLevel
}

export interface DataStoreStatusResponse {
  bucket: string
  datasets: DatasetStatus[]
  overall_status: 'ready' | 'incomplete' | 'empty'
  total_size_mb: number
  timestamp: string
}

export interface ValidationCheck {
  check_name: string
  passed: boolean
  message: string
  details: Record<string, unknown> | null
}

export interface DataStoreValidationResponse {
  passed: boolean
  checks: ValidationCheck[]
  timestamp: string
}

export type BacktestRunStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'EVALUATING'
  | 'RESOLVING'
  | 'FINALIZING'
  | 'COMPLETED'
  | 'FAILED'

export interface BacktestRun {
  run_id: string
  name: string
  status: BacktestRunStatus
  config: Record<string, unknown>
  progress: {
    phase?: string
    days_completed: number
    days_total: number
    trades_found?: number
    trades_resolved?: number
    phase1_workers_total?: number
    phase1_workers_completed?: number
    phase2_workers_total?: number
    phase2_workers_completed?: number
  }
  summary: Record<string, unknown> | null
  created_at: string
  completed_at: string | null
}

export interface BacktestRunsResponse {
  runs: BacktestRun[]
  count: number
}

export interface BacktestTrade {
  trade_id: string
  run_id: string
  entry_date: string
  exit_date: string | null
  ticker: string
  option_ticker: string
  option_type: string
  strike: number
  expiration_date: string
  scanner_type: string
  verdict: string
  combined_score: number
  entry_price: number
  exit_price: number | null
  exit_reason: string | null
  pnl_dollars: number | null
  pnl_pct: number | null
  days_held: number | null
  mfe_pct: number | null
  mae_pct: number | null
  peak_price: number | null
  market_regime: string | null
}

export interface BacktestTradesResponse {
  run_id: string
  trades: BacktestTrade[]
  count: number
  total_in_run: number
}

export interface BacktestGateOverrides {
  disabled_gates?: string[]
  threshold_overrides?: Record<string, number>
}

export interface CreateBacktestRunRequest {
  name: string
  start_date: string
  end_date: string
  policy_snapshot?: Record<string, unknown>
  scanners_enabled?: string[]
  slippage_model?: string
  slippage_pct?: number
  exit_rules?: {
    stop_loss_pct?: number
    profit_target_pct?: number
    min_dte_at_exit?: number
    max_holding_days?: number
    trailing_stop_pct?: number | null
  }
  starting_capital?: number
  gate_overrides?: BacktestGateOverrides
}

export interface CreateBacktestRunResponse {
  status: string
  run_id: string
  mode: string
  trading_days: number
}

export interface DeleteBacktestRunResponse {
  status: string
  run_id: string
  trades_deleted: number
}

// Phase 3: Results Types

export interface BacktestSummary {
  total_trades: number
  winners: number
  losers: number
  breakevens: number
  win_rate: number
  net_pnl: number
  gross_profit: number
  gross_loss: number
  total_return_pct: number
  profit_factor: number | null
  avg_win_pct: number
  avg_loss_pct: number
  avg_pnl_per_trade: number
  avg_days_held: number
  trades_per_day: number
  sharpe_ratio: number | null
  max_drawdown_pct: number
  max_drawdown_duration_days: number
  expectancy: number
  avg_mfe_pct: number
  avg_mae_pct: number
  exit_reasons: Record<string, number>
  starting_capital: number
  first_trade_date: string | null
  last_trade_date: string | null
}

export interface BacktestSummaryResponse {
  run_id: string
  status: string
  summary: BacktestSummary | null
  message?: string
}

export interface EquityCurvePoint {
  date: string
  equity: number
  daily_pnl: number
  drawdown_pct: number
  peak_equity: number
  trades_closed: number
  return_pct: number
}

export interface EquityCurveResponse {
  run_id: string
  starting_capital: number
  curve: EquityCurvePoint[]
  data_points: number
}

export interface MonthlyPnl {
  month: string
  pnl: number
  trades: number
  winners: number
  losers: number
  win_rate: number
}

export interface MonthlyPnlResponse {
  run_id: string
  months: MonthlyPnl[]
  total_months: number
}

export interface SegmentData {
  segment: string
  segment_type: string
  trades: number
  winners: number
  losers: number
  win_rate: number
  net_pnl: number
  avg_return_pct: number
  avg_win_pct: number
  avg_loss_pct: number
  profit_factor: number | null
  sharpe: number | null
  avg_days_held: number
}

export interface SegmentResponse {
  run_id: string
  segment_type: string
  segments: SegmentData[]
  total_segments: number
}

// Phase 4: AI Advisor Types

export interface ReadinessCheck {
  criterion: string
  threshold: string
  actual: string | number | null
  passed: boolean
  severity: 'critical' | 'warning'
}

export interface ReadinessAssessment {
  verdict: 'READY' | 'CONDITIONAL' | 'NOT_READY'
  message: string
  checks: ReadinessCheck[]
  passed_count: number
  total_checks: number
  criteria_used: Record<string, number>
}

export interface ReadinessResponse {
  run_id: string
  status: string
  readiness: ReadinessAssessment | null
  message?: string
}

export interface BacktestInsight {
  insight_id: string
  run_id: string
  insight_type: string
  severity: string
  title: string
  summary: string
  recommended_action: string | null
  confidence: number | null
  data_evidence: Record<string, unknown> | null
  created_at: string
}

export interface InsightsResponse {
  run_id: string
  insights: BacktestInsight[]
  count: number
}

export interface BacktestChatRequest {
  message: string
  conversation_history?: { role: string; content: string }[]
}

export interface BacktestChatResponse {
  run_id: string
  response: string
  tokens_used?: number
  model?: string
  error?: string
}

// ============================================================================
// Scanner Intelligence types
// ============================================================================

export interface ScannerPerformanceData {
  total: number
  closed: number
  win_count: number
  loss_count: number
  win_rate: number
  avg_return: number
  total_pnl_dollars: number
  avg_days_held: number
  best_trade: { ticker: string; return_pct: number; position_id: string } | null
  weekly_win_rates: Array<{ week: string; closed: number; wins: number; win_rate: number }>
}

export interface ScannerPerformanceResponse {
  scanners: Record<string, ScannerPerformanceData>
  period: string
  generated_at: string
}

// ============================================================================
// Trade Library types (browse endpoint)
// ============================================================================

export interface BrowsePositionsResponse {
  positions: PaperPosition[]
  total_count: number
  page: number
  page_size: number
  total_pages: number
  sort_by: string
  sort_order: string
}

// ============================================================================
// Pattern Discovery types
// ============================================================================

export interface ArchetypePerformance {
  win_rate: number
  avg_return: number
  median_return: number
  sample_size: number
  avg_days_held: number
}

export interface ArchetypeResult {
  name: string
  criteria: Record<string, unknown>
  performance: ArchetypePerformance
  matching_trade_indices?: number[]
  reasoning?: string
  confidence: string
  confidence_label: string
}

export interface PatternAnalysis {
  analysis_id: string
  status: string
  created_at?: string
  positions_analyzed: number
  context?: Record<string, unknown>
  archetypes: ArchetypeResult[]
  message?: string
}

export interface PatternAnalysisSummary {
  analysis_id: string
  status?: string
  created_at: string
  positions_analyzed: number
  archetype_count: number
  period: string
}

// ============================================================================
// Custom Analysis types
// ============================================================================

export interface CustomAnalysisSuggestedRule {
  name: string
  criteria: Record<string, unknown>
  performance: ArchetypePerformance
  reasoning?: string
  confidence: string
  confidence_label: string
}

export interface CustomAnalysis {
  analysis_id: string
  status: string
  created_at?: string
  user_prompt: string
  positions_analyzed: number
  analysis?: string
  suggested_rules?: CustomAnalysisSuggestedRule[]
  context?: Record<string, unknown>
  message?: string
}

export interface CustomAnalysisSummary {
  analysis_id: string
  status?: string
  created_at: string
  user_prompt: string
  positions_analyzed: number
  suggested_rule_count: number
  period: string
}

// ============================================================================
// Setup Rules types
// ============================================================================

export interface SetupRule {
  rule_id: string
  name: string
  criteria: Record<string, unknown>
  is_active: boolean
  mode: 'production' | 'test'
  source: 'ai' | 'manual'
  regime?: string
  is_stale?: boolean
  created_at: string
  source_analysis_id: string | null
  performance_at_creation: ArchetypePerformance | null
}

export interface MatchedRule {
  rule_id: string
  name: string
  mode: 'production' | 'test'
  source?: 'ai' | 'manual'
  regime?: string
  is_stale?: boolean
  criteria?: Record<string, unknown>
  performance_at_creation?: ArchetypePerformance | null
}

// Edge Intelligence types

export interface DimensionStats {
  label: string
  total: number
  closed: number
  wins: number
  losses: number
  win_rate: number | null
  avg_return: number | null
  avg_days_held: number | null
  total_pnl_dollars: number
  expectancy: number | null
}

export interface EdgeInsight {
  category: string
  headline: string
  detail: string | null
  strength: 'strong' | 'moderate'
}

export interface EdgeBriefingResponse {
  period_start: string
  period_end: string
  trading_days: number
  total_positions: number
  total_closed: number
  overall_win_rate: number | null
  overall_avg_return: number | null
  overall_expectancy: number | null
  by_option_type: Record<string, DimensionStats>
  option_type_edge: string | null
  by_scanner: Record<string, DimensionStats>
  hot_scanner: string | null
  by_score_bucket: Record<string, DimensionStats>
  by_dte_bucket: Record<string, DimensionStats>
  by_quality_tier: Record<string, DimensionStats>
  by_convergence: Record<string, DimensionStats>
  insights: EdgeInsight[]
  vix_level: number | null
  spy_direction: string | null
}

export interface TradeContextResponse {
  exact_match: DimensionStats | null
  exact_match_description: string
  sample_size: number
  by_option_type: DimensionStats | null
  by_scanner: DimensionStats | null
  by_score_range: DimensionStats | null
  summary: string
  confidence_flag: 'high_confidence' | 'moderate' | 'low_sample' | 'insufficient'
}

// ============================================================================
// Alert Configuration
// ============================================================================

export interface WebhookChannel {
  channel_name: string
  url: string
  url_masked?: string
}

export interface AlertConfig {
  enabled: boolean
  score_threshold: number
  max_premium?: number | null
  require_urgency_or_convergence: boolean
  cooldown_minutes: number
  daily_cap: number
  quiet_hours_start: string
  quiet_hours_end: string
  webhook_channels: WebhookChannel[]
  setup_rule_filter_ids: string[]
  verdicts: Verdict[]
  cheap_gem_enabled?: boolean
  cheap_gem_threshold?: number
  cheap_gem_max_premium?: number
  updated_at?: string
}

export interface AlertPreviewBreakdown {
  totalEvaluations: number
  belowScoreThreshold: number
  aboveMaxPremium: number
  failedUrgencyConvergence: number
  noMatchingSetupRule: number
  wouldAlert: number
}

export interface AlertPreview {
  estimatedAlertsPerDay: number
  daysAnalyzed: number
  breakdown: AlertPreviewBreakdown
}

export interface AlertHistoryEntry {
  contract_id: string
  ticker: string
  conviction_score: number
  channel: string
  status: 'sent' | 'failed'
  timestamp: string
}

export interface SetupRule {
  rule_id: string
  name: string
  criteria: Record<string, unknown>
  is_active: boolean
  mode: 'production' | 'test'
  regime?: string
  is_stale?: boolean
  created_at: string
}

// ============================================================================
// Real Trade Tracking
// ============================================================================

export type TradeExitReason =
  | 'PROFIT_TARGET'
  | 'STOP_LOSS'
  | 'TIME_EXIT'
  | 'TRAILING_STOP'
  | 'EXPIRATION'
  | 'THESIS_INVALIDATED'
  | 'MANUAL'
  | 'OTHER'

export type TradeStatus = 'OPEN' | 'CLOSED'

export interface RealTrade {
  trade_id: string
  status: TradeStatus
  entry_price: number
  quantity: number
  trader: string
  entry_notes?: string | null
  exit_price?: number | null
  exit_reason?: TradeExitReason | null
  exit_notes?: string | null
  realized_pnl_pct?: number | null
  realized_pnl_dollars?: number | null
  tracked_at: string
  closed_at?: string | null
  snapshot: Record<string, unknown>
}

export interface TrackTradeResponse {
  trade_id: string
  status: string
  ticker: string
  option_ticker: string
  entry_price: number
  tracked_at: string
}

export interface TradeListResponse {
  trades: RealTrade[]
  count: number
}

export interface TradeStatsResponse {
  open_count: number
  closed_count: number
  total_count: number
  win_rate: number
  avg_return_pct: number
}

// ============================================================================
// Scanner Performance Analysis (AI-Powered)
// ============================================================================

export interface ScannerAnalysisRootCause {
  cause: string
  evidence: string
  severity: 'high' | 'medium' | 'low'
}

export interface ScannerAnalysisGateRec {
  gate_id: string
  gate_name: string
  current_value: number
  suggested_value: number
  direction: 'tighten' | 'loosen'
  rationale: string
  expected_impact: string
}

export interface ScannerAnalysisFilter {
  description: string
  rationale: string
}

export interface ScannerAnalysisResult {
  summary: string
  root_causes: ScannerAnalysisRootCause[]
  gate_recommendations: ScannerAnalysisGateRec[]
  additional_filters: ScannerAnalysisFilter[]
  confidence: 'high' | 'medium' | 'low'
  confidence_rationale: string
}

export interface ScannerAnalysisResponse {
  scanner_name: string
  analysis: ScannerAnalysisResult
  metadata: {
    model_used: string
    tokens_used: number
    cached: boolean
    generated_at: string
    positions_analyzed: number
    remaining_llm_calls: number
  }
  data_snapshot: {
    win_rate: number | null
    avg_return: number | null
    closed_trades: number
    winners: number
    losers: number
  }
}

// ---------------------------------------------------------------------------
// Underlying Stock Technicals
// ---------------------------------------------------------------------------

export interface TapeSignal {
  name: string
  reading: string
  signal: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
  weight: number
}

export type TapeVerdict =
  | 'BULLISH'
  | 'LEAN_BULLISH'
  | 'NEUTRAL'
  | 'LEAN_BEARISH'
  | 'BEARISH'

export interface StockTechnicalsResponse {
  ticker: string
  company_name: string | null
  sector: string | null
  market_cap: number | null
  homepage_url: string | null

  price: number
  prev_close: number | null
  change_dollar: number | null
  change_pct: number | null
  high_52w: number | null
  low_52w: number | null
  pct_from_52w_high: number | null
  volume: number | null
  avg_volume_20d: number | null
  relative_volume: number | null

  ema_9: number | null
  ema_21: number | null
  ema_50: number | null
  ema_200: number | null
  ema_alignment: string | null

  rsi_14: number | null
  macd: number | null
  macd_signal: number | null
  macd_histogram: number | null
  adx_14: number | null
  plus_di: number | null
  minus_di: number | null
  obv_trend: string | null

  tape_signals: TapeSignal[]
  tape_verdict: TapeVerdict
  tape_bullish_pct: number
}

// ============================================================================
// Feature Importance Analysis
// ============================================================================

export interface QuintileStats {
  quintile: number
  range_low: number
  range_high: number
  n: number
  win_rate: number
  avg_return: number
  median_return: number
}

export interface FeatureStats {
  feature_name: string
  display_name: string
  pillar: string | null
  n_valid: number
  pearson_r: number
  p_value: number
  is_significant: boolean
  win_mean: number | null
  loss_mean: number | null
  difference: number | null
  effect_size: number | null
  direction: string
  quintiles: QuintileStats[]
}

export interface FeaturePairStats {
  feature_a: string
  feature_a_display: string
  feature_b: string
  feature_b_display: string
  both_high_wr: number
  both_high_avg_return: number
  both_high_n: number
  a_only_high_wr: number
  a_only_high_n: number
  b_only_high_wr: number
  b_only_high_n: number
  both_low_wr: number
  both_low_n: number
  interaction_lift: number
  a_direction?: string
  b_direction?: string
}

export interface WeightComparison {
  pillar: string
  subscore: string
  display_name: string
  current_weight: number
  empirical_importance: number
  delta: number
  recommendation: string
}

export interface TemporalWindow {
  window_start: string
  window_end: string
  n_positions: number
  feature_correlations: Record<string, number | null>
}

export interface FeatureImportanceResult {
  analysis_id: string
  created_at: string
  period: string
  outcome: string
  n_positions: number
  overall_win_rate: number
  overall_avg_return: number
  features: FeatureStats[]
  interactions: FeaturePairStats[]
  weight_comparisons: WeightComparison[]
  temporal_windows: TemporalWindow[]
  segments: Record<string, unknown>
  narrative: string | null
  status: string
}
