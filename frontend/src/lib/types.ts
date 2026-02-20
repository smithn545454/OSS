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

export type PillarId = 'DIRECTIONAL' | 'VOLATILITY' | 'STRUCTURE'

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

// Decision
export interface Decision {
  evaluation_id: string
  verdict: Verdict
  quality_tier: QualityTier | null
  final_score: number
  directional_score: number
  volatility_score: number
  structure_score: number
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
  convergence_count: number | null
  conviction_score: number | null
  pillar_directional: number | null
  pillar_volatility: number | null
  pillar_structure: number | null
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
}

// Equity curve / snapshots / analysis response types
export interface EquityCurveResponse {
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

export interface PillarWeights {
  directional: number
  volatility: number
  structure: number
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

export interface PolicyConfig {
  scanner: ScannerConfig
  underlying_filter: UnderlyingFilterConfig
  contract_selection: ContractSelectionConfig
  gates: GateConfig
  pillar_weights: PillarWeights
  decision: DecisionConfig
  tracking: TrackingConfig
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
  metrics: Record<string, number>
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
export type ThesisStatus = 'COMPLETED' | 'FAILED' | 'RATE_LIMITED' | 'PENDING'

export type LLMProvider = 'anthropic' | 'openai'

export interface ExitPlanThesis {
  profit_target: string
  stop_loss: string
  time_exit: string
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
 * Conviction score breakdown (spec section 4).
 */
export interface ConvictionScoreBreakdown {
  total: number
  components: {
    thetaAdjustedEv: ComponentScore
    compositePillar: ComponentScore
    gateMargin: ComponentScore
    scannerConvergence: ComponentScore
    timeSensitivity: ComponentScore
  }
}

export interface ComponentScore {
  raw: number
  normalized: number
  weighted: number
}

/**
 * Conviction scoring weights (spec section 4.1).
 */
export interface ConvictionScoreWeights {
  thetaAdjustedEv: number  // Default 40%
  compositePillar: number  // Default 25%
  gateMargin: number       // Default 15%
  scannerConvergence: number // Default 10%
  timeSensitivity: number  // Default 10%
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
  convictionBreakdown?: ConvictionScoreBreakdown
  alertedAt?: string
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
    weights: ConvictionScoreWeights
    evBenchmark: number
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
  last: number
  volume: number
  openInterest: number
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
