/**
 * API client for OSS backend.
 */

import type {
  HealthResponse,
  PoliciesListResponse,
  Policy,
  PolicyConfig,
  PolicyDiff,
  PipelineRunsListResponse,
  PipelineRun,
  PipelineStatsResponse,
  StageEvent,
  EvaluationDetailResponse,
  EvaluationListResponse,
  EvaluationFilters,
  CalibrationReport,
  CalibrationReportsResponse,
  ThresholdSuggestion,
  TradeThesis,
  LLMUsageResponse,
  LLMConfigResponse,
  RepresentativeTracesResponse,
  // Pipeline Monitor types
  TimeRangeOption,
  PipelineMonitorScannerType,
  PipelineMonitorData,
  GetRunsResponse,
  GetAggregateResponse,
  GetRunDetailResponse,
  // Opportunities page types
  MarketContext,
  ApproveEvaluationsResponse,
  WatchInsightsResponse,
  ContractQuotesResponse,
  // Paper Trading Workstation types
  PaperEquityCurveResponse,
  PositionSnapshotsResponse,
  PositionAnalysisResponse,
  PaperTradingSummary,
  PaperTradingPositionsResponse,
  PaperTradingMetricsResponse,
  PaperTradingTiersResponse,
  PaperTradingExitsResponse,
  AIInsightsResponse,
  UpdatePositionsResponse,
  PaginatedPositionsResponse,
  SummaryMetricsResponse,
} from './types'

// Use VITE_API_URL in production (full backend URL), empty string for development (Vite proxy handles it)
const API_BASE = import.meta.env.VITE_API_URL || ''

/** Map frontend scanner type values to backend ScannerType enum values. */
const SCANNER_TYPE_MAP: Record<string, string> = {
  unusual_volume: 'UNUSUAL_VOLUME',
  breakout: 'BREAKOUT',
  compression: 'COMPRESSION_EXPANSION',
  cheap_options: 'CHEAP_OPTIONS',
}

function toBackendScanner(scanner: PipelineMonitorScannerType | undefined): string | undefined {
  if (!scanner || scanner === 'all') return undefined
  return SCANNER_TYPE_MAP[scanner] || scanner
}

class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public body: unknown
  ) {
    super(`API Error: ${status} ${statusText}`)
    this.name = 'ApiError'
  }
}

async function fetchApi<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  // In production, API_BASE is the full backend URL; in dev it's empty (Vite proxy handles /api)
  const url = `${API_BASE}${endpoint}`

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 30000)

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    })

    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new ApiError(response.status, response.statusText, body)
    }

    return response.json()
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError(408, 'Request Timeout', { message: 'Request timed out after 30s' })
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

// Health
export async function getHealth(): Promise<HealthResponse> {
  return fetchApi<HealthResponse>('/health')
}

// Policies
export async function listPolicies(limit = 20): Promise<PoliciesListResponse> {
  return fetchApi<PoliciesListResponse>(`/api/policies?limit=${limit}`)
}

export async function getActivePolicy(): Promise<Policy> {
  return fetchApi<Policy>('/api/policies/active')
}

export async function getPolicy(version: string): Promise<Policy> {
  return fetchApi<Policy>(`/api/policies/${encodeURIComponent(version)}`)
}

export async function createPolicy(
  config: PolicyConfig,
  createdBy: string
): Promise<{ message: string; policy: Policy }> {
  return fetchApi<{ message: string; policy: Policy }>('/api/policies', {
    method: 'POST',
    body: JSON.stringify({ config, created_by: createdBy }),
  })
}

export async function activatePolicy(
  version: string
): Promise<{ message: string; policy: Policy }> {
  return fetchApi<{ message: string; policy: Policy }>(
    `/api/policies/${encodeURIComponent(version)}/activate`,
    { method: 'POST' }
  )
}

export async function diffPolicies(
  v1: string,
  v2: string
): Promise<PolicyDiff> {
  return fetchApi<PolicyDiff>(
    `/api/policies/diff/${encodeURIComponent(v1)}/${encodeURIComponent(v2)}`
  )
}

// Pipeline (Legacy endpoints)
export async function listPipelineRuns(
  limit = 20,
  status?: string
): Promise<PipelineRunsListResponse> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (status) params.set('status', status)
  return fetchApi<PipelineRunsListResponse>(`/api/pipeline/runs?${params}`)
}

export async function getPipelineRun(runId: string): Promise<PipelineRun> {
  return fetchApi<PipelineRun>(`/api/pipeline/runs/${encodeURIComponent(runId)}`)
}

export async function getRunStages(
  runId: string
): Promise<{ run_id: string; stages: StageEvent[]; count: number }> {
  return fetchApi<{ run_id: string; stages: StageEvent[]; count: number }>(
    `/api/pipeline/runs/${encodeURIComponent(runId)}/stages`
  )
}

export async function startPipelineRun(
  policyVersion: string
): Promise<{ message: string; run: PipelineRun }> {
  return fetchApi<{ message: string; run: PipelineRun }>('/api/pipeline/runs', {
    method: 'POST',
    body: JSON.stringify({ policy_version: policyVersion }),
  })
}

export async function completePipelineRun(
  runId: string,
  status = 'completed'
): Promise<{ message: string; run: PipelineRun }> {
  return fetchApi<{ message: string; run: PipelineRun }>(
    `/api/pipeline/runs/${encodeURIComponent(runId)}/complete?status=${status}`,
    { method: 'POST' }
  )
}

export async function getPipelineStats(days = 7): Promise<PipelineStatsResponse> {
  return fetchApi<PipelineStatsResponse>(`/api/pipeline/stats?days=${days}`)
}

// Pipeline Monitor API (per oss-pipeline-monitor-requirements.md Section 9)

/**
 * Get recent pipeline runs with filters (spec section 9.1).
 */
export async function getPipelineMonitorRuns(
  options: {
    limit?: number
    offset?: number
    time?: TimeRangeOption
    start?: string
    end?: string
    scanner?: PipelineMonitorScannerType
  } = {}
): Promise<GetRunsResponse> {
  const params = new URLSearchParams()
  if (options.limit) params.set('limit', String(options.limit))
  if (options.offset) params.set('offset', String(options.offset))
  if (options.time) params.set('time', options.time)
  if (options.start) params.set('start', options.start)
  if (options.end) params.set('end', options.end)
  const backendScanner = toBackendScanner(options.scanner)
  if (backendScanner) params.set('scanner', backendScanner)

  return fetchApi<GetRunsResponse>(`/api/pipeline/runs?${params}`)
}

/**
 * Get aggregated pipeline data for a time range (spec section 9.2).
 */
export async function getPipelineAggregateData(
  options: {
    time?: TimeRangeOption
    start?: string
    end?: string
    scanner?: PipelineMonitorScannerType
    verdict?: string[]
    dte_bucket?: string[]
    option_side?: string[]
  } = {}
): Promise<PipelineMonitorData> {
  const params = new URLSearchParams()
  if (options.time) params.set('time', options.time)
  if (options.start) params.set('start', options.start)
  if (options.end) params.set('end', options.end)
  const backendScanner = toBackendScanner(options.scanner)
  if (backendScanner) params.set('scanner', backendScanner)
  if (options.verdict?.length) params.set('verdict', options.verdict.join(','))
  if (options.dte_bucket?.length) params.set('dte_bucket', options.dte_bucket.join(','))
  if (options.option_side?.length) params.set('option_side', options.option_side.join(','))
  
  const response = await fetchApi<GetAggregateResponse>(`/api/pipeline/aggregate?${params}`)
  return response.data
}

/**
 * Get detailed pipeline data for a specific run (spec section 9.3).
 */
export async function getPipelineRunDetail(
  runId: string,
  options: {
    verdict?: string[]
    dte_bucket?: string[]
    option_side?: string[]
  } = {}
): Promise<PipelineMonitorData> {
  const params = new URLSearchParams()
  if (options.verdict?.length) params.set('verdict', options.verdict.join(','))
  if (options.dte_bucket?.length) params.set('dte_bucket', options.dte_bucket.join(','))
  if (options.option_side?.length) params.set('option_side', options.option_side.join(','))
  
  const response = await fetchApi<GetRunDetailResponse>(
    `/api/pipeline/runs/${encodeURIComponent(runId)}?${params}`
  )
  return response.data
}

// Evaluations
export async function getEvaluationDetail(
  ticker: string,
  evaluationId: string
): Promise<EvaluationDetailResponse> {
  return fetchApi<EvaluationDetailResponse>(
    `/api/evaluations/detail/${encodeURIComponent(ticker)}/${encodeURIComponent(evaluationId)}`
  )
}

export async function listEvaluations(
  filters: EvaluationFilters = {}
): Promise<EvaluationListResponse> {
  const params = new URLSearchParams()
  if (filters.verdict) params.set('verdict', filters.verdict)
  if (filters.dte_bucket) params.set('dte_bucket', filters.dte_bucket)
  if (filters.option_type) params.set('option_type', filters.option_type)
  if (filters.limit) params.set('limit', String(filters.limit))
  return fetchApi<EvaluationListResponse>(`/api/evaluations/filtered?${params}`)
}

export async function listEvaluationsByVerdict(
  verdict: string,
  limit = 50
): Promise<{ verdict: string; evaluations: unknown[]; count: number }> {
  return fetchApi(`/api/evaluations/by-verdict/${encodeURIComponent(verdict)}?limit=${limit}`)
}

// Calibration
export async function runCalibration(): Promise<{ message: string; report: CalibrationReport }> {
  return fetchApi<{ message: string; report: CalibrationReport }>('/api/calibration/run', {
    method: 'POST',
  })
}

export async function listCalibrationReports(
  limit = 10
): Promise<CalibrationReportsResponse> {
  return fetchApi<CalibrationReportsResponse>(`/api/calibration/reports?limit=${limit}`)
}

export async function getCalibrationReport(reportId: string): Promise<CalibrationReport> {
  return fetchApi<CalibrationReport>(`/api/calibration/reports/${encodeURIComponent(reportId)}`)
}

export async function approveSuggestion(
  suggestionId: string
): Promise<{ message: string; suggestion: ThresholdSuggestion }> {
  return fetchApi<{ message: string; suggestion: ThresholdSuggestion }>(
    `/api/calibration/suggestions/${encodeURIComponent(suggestionId)}/approve`,
    { method: 'POST' }
  )
}

export async function rejectSuggestion(
  suggestionId: string
): Promise<{ message: string; suggestion: ThresholdSuggestion }> {
  return fetchApi<{ message: string; suggestion: ThresholdSuggestion }>(
    `/api/calibration/suggestions/${encodeURIComponent(suggestionId)}/reject`,
    { method: 'POST' }
  )
}

// LLM / Trade Thesis (Section 21)
export async function getLLMUsage(): Promise<LLMUsageResponse> {
  return fetchApi<LLMUsageResponse>('/api/llm/usage')
}

export async function getLLMConfig(): Promise<LLMConfigResponse> {
  return fetchApi<LLMConfigResponse>('/api/llm/config')
}

export async function getThesis(evaluationId: string): Promise<TradeThesis> {
  return fetchApi<TradeThesis>(`/api/llm/theses/${encodeURIComponent(evaluationId)}`)
}

export async function listTheses(
  date?: string,
  limit = 50
): Promise<{ date: string; theses: Partial<TradeThesis>[]; count: number }> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (date) params.set('date', date)
  return fetchApi(`/api/llm/theses?${params}`)
}

// Observability - Representative Trace Sampling (Section 18.3)
export async function getRepresentativeTraces(
  gateFailureLimit = 10,
  rejectSampleLimit = 10,
  approveSampleLimit = 10
): Promise<RepresentativeTracesResponse> {
  const params = new URLSearchParams({
    gate_failure_limit: String(gateFailureLimit),
    reject_sample_limit: String(rejectSampleLimit),
    approve_sample_limit: String(approveSampleLimit),
  })
  return fetchApi<RepresentativeTracesResponse>(`/api/observability/traces?${params}`)
}

// ============================================================================
// Opportunities Page API (OSS_Opportunities_Page_Specification)
// ============================================================================

/**
 * Get market context for the Context Bar (spec section 7).
 */
export async function getMarketContext(): Promise<MarketContext> {
  return fetchApi<MarketContext>('/api/market/context')
}

/**
 * Get contract quotes for real-time price updates (spec section 19.3).
 */
export async function getContractQuotes(contractIds: string[]): Promise<ContractQuotesResponse> {
  const contracts = contractIds.join(',')
  return fetchApi<ContractQuotesResponse>(`/api/market/quotes?contracts=${encodeURIComponent(contracts)}`)
}

/**
 * Get APPROVE evaluations with enhanced data (spec section 19.1).
 */
export async function getApproveEvaluations(options: {
  excludeEarnings?: boolean
  earningsDays?: number
  scanner?: string
  verdict?: string
  limit?: number
} = {}): Promise<ApproveEvaluationsResponse> {
  const params = new URLSearchParams()
  if (options.excludeEarnings !== undefined) params.set('exclude_earnings', String(options.excludeEarnings))
  if (options.earningsDays) params.set('earnings_days', String(options.earningsDays))
  if (options.scanner) params.set('scanner', options.scanner)
  if (options.verdict) params.set('verdict', options.verdict)
  if (options.limit) params.set('limit', String(options.limit))

  return fetchApi<ApproveEvaluationsResponse>(`/api/evaluations/approve?${params}`)
}

/**
 * Get WATCH intelligence insights (spec section 11).
 */
export async function getWatchInsights(sinceDays: number = 7): Promise<WatchInsightsResponse> {
  return fetchApi<WatchInsightsResponse>(`/api/evaluations/watch/insights?since_days=${sinceDays}`)
}

/**
 * Generate thesis for an evaluation (spec section 21).
 */
export async function generateThesis(evaluationId: string): Promise<TradeThesis> {
  return fetchApi<TradeThesis>(`/api/thesis/generate`, {
    method: 'POST',
    body: JSON.stringify({ evaluationId }),
  })
}

// UV Scanner On-Demand Trigger
export async function triggerUVScan(): Promise<{ run_id: string; status: string; message: string }> {
  return fetchApi<{ run_id: string; status: string; message: string }>(
    '/api/scanners/uv-scan-trigger',
    { method: 'POST' }
  )
}

// ============================================================================
// Paper Trading Workstation API
// ============================================================================

export async function getSummaryMetrics(params: {
  status?: string
  verdict?: string
  scanner?: string
  period?: string
} = {}): Promise<SummaryMetricsResponse> {
  const qs = new URLSearchParams()
  if (params.status) qs.set('status', params.status)
  if (params.verdict) qs.set('verdict', params.verdict)
  if (params.scanner) qs.set('scanner', params.scanner)
  if (params.period) qs.set('period', params.period)
  const query = qs.toString()
  return fetchApi<SummaryMetricsResponse>(
    `/api/paper-trading/summary-metrics${query ? `?${query}` : ''}`
  )
}

export async function getPositionsPaginated(params: {
  status?: string
  limit?: number
  cursor?: string
  verdict?: string
  scanner?: string
  period?: string
} = {}): Promise<PaginatedPositionsResponse> {
  const qs = new URLSearchParams()
  if (params.status) qs.set('status', params.status)
  if (params.limit) qs.set('limit', String(params.limit))
  if (params.cursor) qs.set('cursor', params.cursor)
  if (params.verdict) qs.set('verdict', params.verdict)
  if (params.scanner) qs.set('scanner', params.scanner)
  if (params.period) qs.set('period', params.period)
  return fetchApi<PaginatedPositionsResponse>(`/api/paper-trading/positions?${qs}`)
}

export async function getPaperTradingSummary(): Promise<PaperTradingSummary> {
  return fetchApi<PaperTradingSummary>('/api/paper-trading/summary')
}

export async function getPaperTradingPositions(
  status?: string
): Promise<PaperTradingPositionsResponse> {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  return fetchApi<PaperTradingPositionsResponse>(
    `/api/paper-trading/positions?${params}`
  )
}

export async function getPaperTradingMetrics(): Promise<PaperTradingMetricsResponse> {
  return fetchApi<PaperTradingMetricsResponse>('/api/paper-trading/metrics')
}

export async function getPaperTradingTiers(): Promise<PaperTradingTiersResponse> {
  return fetchApi<PaperTradingTiersResponse>('/api/paper-trading/metrics/tiers')
}

export async function getPaperTradingExits(): Promise<PaperTradingExitsResponse> {
  return fetchApi<PaperTradingExitsResponse>('/api/paper-trading/metrics/exits')
}

export async function getAIInsights(): Promise<AIInsightsResponse> {
  return fetchApi<AIInsightsResponse>('/api/paper-trading/ai-insights')
}

export async function updatePaperTradingPositions(): Promise<UpdatePositionsResponse> {
  return fetchApi<UpdatePositionsResponse>('/api/paper-trading/update', {
    method: 'POST',
  })
}

export async function closePaperTradingPosition(
  positionId: string,
  exitPrice?: number
): Promise<{ message: string; position: Record<string, unknown> }> {
  return fetchApi(`/api/paper-trading/positions/${encodeURIComponent(positionId)}/close`, {
    method: 'POST',
    body: JSON.stringify({ exit_price: exitPrice ?? null }),
  })
}

export async function getEquityCurve(period: string = '30d'): Promise<PaperEquityCurveResponse> {
  return fetchApi<PaperEquityCurveResponse>(
    `/api/paper-trading/equity-curve?period=${encodeURIComponent(period)}`
  )
}

export async function getPositionSnapshots(
  positionId: string
): Promise<PositionSnapshotsResponse> {
  return fetchApi<PositionSnapshotsResponse>(
    `/api/paper-trading/positions/${encodeURIComponent(positionId)}/snapshots`
  )
}

export async function analyzePosition(
  positionId: string
): Promise<PositionAnalysisResponse> {
  return fetchApi<PositionAnalysisResponse>(
    `/api/paper-trading/positions/${encodeURIComponent(positionId)}/analyze`,
    { method: 'POST' }
  )
}

// ============================================================================
// Backtest API
// ============================================================================

export async function getDataStoreStatus(): Promise<
  import('./types').DataStoreStatusResponse
> {
  return fetchApi('/api/backtest/data-store/status')
}

export async function validateDataStore(): Promise<
  import('./types').DataStoreValidationResponse
> {
  return fetchApi('/api/backtest/data-store/validate', { method: 'POST' })
}

export async function listBacktestRuns(
  status?: string
): Promise<import('./types').BacktestRunsResponse> {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  const query = params.toString()
  return fetchApi(`/api/backtest/runs${query ? `?${query}` : ''}`)
}

export async function getBacktestRun(
  runId: string
): Promise<import('./types').BacktestRun> {
  return fetchApi(`/api/backtest/runs/${encodeURIComponent(runId)}`)
}

export async function createBacktestRun(
  request: import('./types').CreateBacktestRunRequest
): Promise<import('./types').CreateBacktestRunResponse> {
  return fetchApi('/api/backtest/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function deleteBacktestRun(
  runId: string
): Promise<import('./types').DeleteBacktestRunResponse> {
  return fetchApi(`/api/backtest/runs/${encodeURIComponent(runId)}`, {
    method: 'DELETE',
  })
}

export async function listBacktestTrades(
  runId: string,
  params?: { scanner?: string; verdict?: string; limit?: number }
): Promise<import('./types').BacktestTradesResponse> {
  const searchParams = new URLSearchParams()
  if (params?.scanner) searchParams.set('scanner', params.scanner)
  if (params?.verdict) searchParams.set('verdict', params.verdict)
  if (params?.limit) searchParams.set('limit', String(params.limit))
  const query = searchParams.toString()
  return fetchApi(
    `/api/backtest/runs/${encodeURIComponent(runId)}/trades${query ? `?${query}` : ''}`
  )
}

// Phase 3: Results API

export async function getBacktestSummary(
  runId: string
): Promise<import('./types').BacktestSummaryResponse> {
  return fetchApi(`/api/backtest/runs/${encodeURIComponent(runId)}/summary`)
}

export async function getBacktestEquityCurve(
  runId: string
): Promise<import('./types').EquityCurveResponse> {
  return fetchApi(`/api/backtest/runs/${encodeURIComponent(runId)}/equity-curve`)
}

export async function getBacktestMonthlyPnl(
  runId: string
): Promise<import('./types').MonthlyPnlResponse> {
  return fetchApi(`/api/backtest/runs/${encodeURIComponent(runId)}/monthly-pnl`)
}

export async function getBacktestSegments(
  runId: string,
  segmentType: string
): Promise<import('./types').SegmentResponse> {
  return fetchApi(
    `/api/backtest/runs/${encodeURIComponent(runId)}/segments/${encodeURIComponent(segmentType)}`
  )
}

// Phase 4: AI Advisor API

export async function getReadinessAssessment(
  runId: string
): Promise<import('./types').ReadinessResponse> {
  return fetchApi(`/api/backtest/runs/${encodeURIComponent(runId)}/readiness`)
}

export async function getBacktestInsights(
  runId: string
): Promise<import('./types').InsightsResponse> {
  return fetchApi(`/api/backtest/runs/${encodeURIComponent(runId)}/insights`)
}

export async function chatWithBacktest(
  runId: string,
  request: import('./types').BacktestChatRequest
): Promise<import('./types').BacktestChatResponse> {
  return fetchApi(
    `/api/backtest/runs/${encodeURIComponent(runId)}/chat`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    }
  )
}

// Export the ApiError for error handling
export { ApiError }
