// Convex Mode types — mirror backend Pydantic models in app/convex/ + app/core/schemas.py.
// Kept in a dedicated module so the Convex feature stays self-contained
// and doesn't bloat lib/types.ts.

export type ConvexTier = 'A' | 'B' | 'C'
export type ConvexDirection = 'bullish' | 'bearish' | 'ambiguous'
export type ConvexStageResult = 'PASS' | 'FAIL'

/** Per-stage payload (one of stage_1..stage_4). */
export interface ConvexStagePayload {
  stage: number
  stage_name: string
  result: ConvexStageResult
  summary: string
  criteria: Record<string, unknown>
  strength_inputs: Record<string, unknown>
  strength: number | null
  extras: Record<string, unknown>
}

/** Bundle of all four stage payloads (any may be null when the candidate
 * dropped out earlier). */
export interface ConvexStagesPayload {
  stage_1: ConvexStagePayload | null
  stage_2: ConvexStagePayload | null
  stage_3: ConvexStagePayload | null
  stage_4: ConvexStagePayload | null
}

/** Slim contract record (call or put leg of a Convex evaluation). */
export interface ConvexSelectedContract {
  option_ticker: string
  option_type: 'CALL' | 'PUT'
  strike: number
  expiry: string // YYYY-MM-DD
  dte: number
  delta: number
  bid: number
  ask: number
  open_interest: number
  volume: number
}

/** Aggregated UV signal payload — populated at Stage 4 from the
 * legacy UV scanner's per-contract detections via the underlying-ticker
 * GSI. ``directional_skew`` agreeing with the candidate's direction +
 * ``is_unusual = true`` is what flips smart_money_confirmation true. */
export interface ConvexUVSignal {
  detection_count: number
  total_today_volume: number
  total_avg_volume: number
  volume_ratio: number | null
  call_volume: number
  put_volume: number
  directional_skew: 'call_heavy' | 'put_heavy' | 'balanced'
  is_unusual: boolean
  lookback_hours: number
}

/** Decision payload as persisted on a ConvexEvaluation.
 * Only Convex-relevant fields are typed here; legacy fields are kept as
 * passthrough so we don't have to re-list the dozens of v3/v4/v5 columns. */
export interface ConvexDecision {
  evaluation_id: string
  verdict: 'CONVEX_APPROVE' | 'APPROVE' | 'WATCH' | 'REJECT'
  primary_reason_code: string
  supporting_reason_codes: string[]
  policy_version: string
  decided_at: string
  // Convex-specific
  convex_tier: ConvexTier | null
  convex_stages: ConvexStagesPayload | null
  convex_strength_composite: number | null
  smart_money_confirmation: boolean | null
  convex_uv_signal: ConvexUVSignal | null
  position_sizing_recommendation: string | null
  // Legacy fields (untyped passthrough)
  [key: string]: unknown
}

/** Final per-candidate Convex APPROVE record. */
export interface ConvexEvaluation {
  evaluation_id: string
  run_id: string
  ticker: string
  direction: ConvexDirection
  convex_tier: ConvexTier
  composite_strength: number
  smart_money_confirmation: boolean
  selected_call: ConvexSelectedContract | null
  selected_put: ConvexSelectedContract | null
  decision: ConvexDecision
  generated_at: string
}

/** /evaluations response. */
export interface ConvexEvaluationsListResponse {
  tier: ConvexTier | null
  evaluations: ConvexEvaluation[]
  count: number
}

/** /evaluations/{ticker}/{eval_id} response. */
export interface ConvexEvaluationResponse {
  evaluation: ConvexEvaluation
}

/** /runs/{run_id}/stage-events response. */
export interface ConvexStageEventRecord {
  run_id: string
  ticker: string
  stage: number
  payload: ConvexStagePayload
  recorded_at: string
}

export interface ConvexStageEventsResponse {
  run_id: string
  ticker: string | null
  events: ConvexStageEventRecord[]
  count: number
}

/** /runs/{run_id}/failed-candidates response. */
export interface ConvexFailedCandidate {
  ticker: string
  highest_stage_passed: number
  failed_at_stage: number
  failed_stage_name: string
  summary: string
}

export interface ConvexFailedCandidatesResponse {
  run_id: string
  failures: ConvexFailedCandidate[]
  count: number
}

/** /universe response. */
export interface ConvexUniverseEntry {
  ticker: string
  sector: string | null
  market_cap: number | null
  avg_options_volume_30d: number | null
  avg_atm_spread_pct: number | null
  tail_event_count_252d: number
  hv_regime_ratio: number | null
  historical_max_30d_move_pct: number | null
}

export interface ConvexUniverseSnapshot {
  snapshot_date: string
  policy_version: string
  tickers: ConvexUniverseEntry[]
  total_count: number
  sector_distribution: Record<string, number>
  generated_at: string
}

export interface ConvexUniverseResponse {
  snapshot: ConvexUniverseSnapshot | null
}

/** /runs response (Pipeline Monitor sidebar). */
export interface ConvexRunSummary {
  run_id: string
  generated_at: string
  /** First stage event timestamp (start of run). */
  started_at?: string
  /** Last stage event timestamp (end of run). */
  completed_at?: string
  /** Stage 1 PASS count = tickers in the kinetic universe. */
  universe_size?: number
  /** Stage 2 PASS count (catalyst layer). */
  stage2_advancers?: number
  /** Stage 3 PASS count (volatility mispricing). */
  stage3_advancers?: number
  /** Stage 4 PASS count = finalised candidates. */
  stage4_advancers?: number
  tier_a: number
  tier_b: number
  tier_c: number
  finalised_count: number
}

export interface ConvexRunsListResponse {
  runs: ConvexRunSummary[]
  count: number
}
