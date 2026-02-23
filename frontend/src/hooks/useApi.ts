/**
 * React Query hooks for API data fetching.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import * as api from '@/lib/api'
import { enhanceWithConvictionScores } from '@/lib/convictionScore'
import type {
  PolicyConfig,
  EvaluationFilters,
  TimeRangeOption,
  PipelineMonitorScannerType,
  ApproveEvaluation,
} from '@/lib/types'

// Query Keys
export const queryKeys = {
  health: ['health'] as const,
  policies: ['policies'] as const,
  policy: (version: string) => ['policies', version] as const,
  activePolicy: ['policies', 'active'] as const,
  policyDiff: (v1: string, v2: string) => ['policies', 'diff', v1, v2] as const,
  pipelineRuns: (status?: string) => ['pipeline', 'runs', status] as const,
  pipelineRun: (runId: string) => ['pipeline', 'runs', runId] as const,
  runStages: (runId: string) => ['pipeline', 'runs', runId, 'stages'] as const,
  pipelineStats: (days: number) => ['pipeline', 'stats', days] as const,
  evaluationDetail: (ticker: string, id: string) =>
    ['evaluations', ticker, id, 'detail'] as const,
  evaluations: (filters: EvaluationFilters) => ['evaluations', 'list', filters] as const,
  evaluationsByVerdict: (verdict: string, limit: number) => 
    ['evaluations', 'verdict', verdict, limit] as const,
  calibrationReports: (limit: number) => ['calibration', 'reports', limit] as const,
  calibrationReport: (reportId: string) => ['calibration', 'reports', reportId] as const,
  representativeTraces: ['observability', 'traces'] as const,
  // Pipeline Monitor keys
  pipelineMonitorRuns: (options: {
    time?: TimeRangeOption
    scanner?: PipelineMonitorScannerType
    limit?: number
    start?: string
    end?: string
  }) => ['pipeline', 'monitor', 'runs', options] as const,
  pipelineAggregate: (options: {
    time?: TimeRangeOption
    scanner?: PipelineMonitorScannerType
    start?: string
    end?: string
  }) => ['pipeline', 'monitor', 'aggregate', options] as const,
  pipelineRunDetail: (runId: string) => ['pipeline', 'monitor', 'run', runId] as const,
  // Opportunities page keys
  marketContext: ['market', 'context'] as const,
  approveEvaluations: (options: {
    excludeEarnings?: boolean
    scanner?: string
    verdict?: string
    limit?: number
  }) => ['evaluations', 'approve', options] as const,
  watchInsights: (sinceDays: number) => ['evaluations', 'watch', 'insights', sinceDays] as const,
  contractQuotes: (contractIds: string[]) => ['market', 'quotes', contractIds] as const,
}

// Health
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: api.getHealth,
    refetchInterval: 30000, // Check every 30 seconds
  })
}

// Policies
export function usePolicies(limit = 20) {
  return useQuery({
    queryKey: queryKeys.policies,
    queryFn: () => api.listPolicies(limit),
  })
}

export function usePolicy(version: string) {
  return useQuery({
    queryKey: queryKeys.policy(version),
    queryFn: () => api.getPolicy(version),
    enabled: !!version,
  })
}

export function useActivePolicy() {
  return useQuery({
    queryKey: queryKeys.activePolicy,
    queryFn: api.getActivePolicy,
  })
}

export function usePolicyDiff(v1: string, v2: string) {
  return useQuery({
    queryKey: queryKeys.policyDiff(v1, v2),
    queryFn: () => api.diffPolicies(v1, v2),
    enabled: !!v1 && !!v2,
  })
}

export function useCreatePolicy() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ config, createdBy }: { config: PolicyConfig; createdBy: string }) =>
      api.createPolicy(config, createdBy),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.policies })
    },
  })
}

export function useActivatePolicy() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (version: string) => api.activatePolicy(version),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.policies })
      queryClient.invalidateQueries({ queryKey: queryKeys.activePolicy })
    },
  })
}

// Pipeline
export function usePipelineRuns(limit = 20, status?: string) {
  return useQuery({
    queryKey: queryKeys.pipelineRuns(status),
    queryFn: () => api.listPipelineRuns(limit, status),
    refetchInterval: 10000, // Refresh every 10 seconds for running pipelines
  })
}

export function usePipelineRun(runId: string) {
  return useQuery({
    queryKey: queryKeys.pipelineRun(runId),
    queryFn: () => api.getPipelineRun(runId),
    enabled: !!runId,
  })
}

export function useRunStages(runId: string) {
  return useQuery({
    queryKey: queryKeys.runStages(runId),
    queryFn: () => api.getRunStages(runId),
    enabled: !!runId,
  })
}

export function usePipelineStats(days = 7) {
  return useQuery({
    queryKey: queryKeys.pipelineStats(days),
    queryFn: () => api.getPipelineStats(days),
  })
}

export function useStartPipelineRun() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (policyVersion: string) => api.startPipelineRun(policyVersion),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pipeline', 'runs'] })
    },
  })
}

export function useCompletePipelineRun() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ runId, status }: { runId: string; status?: string }) =>
      api.completePipelineRun(runId, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pipeline', 'runs'] })
      queryClient.invalidateQueries({ queryKey: ['pipeline', 'stats'] })
    },
  })
}

// Evaluations
export function useEvaluationDetail(ticker: string, evaluationId: string) {
  return useQuery({
    queryKey: queryKeys.evaluationDetail(ticker, evaluationId),
    queryFn: () => api.getEvaluationDetail(ticker, evaluationId),
    enabled: !!ticker && !!evaluationId,
  })
}

export function useEvaluations(filters: EvaluationFilters = {}) {
  return useQuery({
    queryKey: queryKeys.evaluations(filters),
    queryFn: () => api.listEvaluations(filters),
  })
}

export function useEvaluationsByVerdict(verdict: string, limit = 50) {
  return useQuery({
    queryKey: queryKeys.evaluationsByVerdict(verdict, limit),
    queryFn: () => api.listEvaluationsByVerdict(verdict, limit),
    enabled: !!verdict,
  })
}

// Calibration
export function useCalibrationReports(limit = 10) {
  return useQuery({
    queryKey: queryKeys.calibrationReports(limit),
    queryFn: () => api.listCalibrationReports(limit),
  })
}

export function useCalibrationReport(reportId: string) {
  return useQuery({
    queryKey: queryKeys.calibrationReport(reportId),
    queryFn: () => api.getCalibrationReport(reportId),
    enabled: !!reportId,
  })
}

export function useRunCalibration() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => api.runCalibration(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calibration', 'reports'] })
    },
  })
}

export function useApproveSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (suggestionId: string) => api.approveSuggestion(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calibration', 'reports'] })
      queryClient.invalidateQueries({ queryKey: ['policies'] })
    },
  })
}

export function useRejectSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (suggestionId: string) => api.rejectSuggestion(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calibration', 'reports'] })
    },
  })
}

// Observability - Representative Trace Sampling (Section 18.3)
export function useRepresentativeTraces() {
  return useQuery({
    queryKey: queryKeys.representativeTraces,
    queryFn: () => api.getRepresentativeTraces(),
    staleTime: 5 * 60 * 1000, // Consider fresh for 5 minutes
  })
}

// ============================================================================
// Pipeline Monitor Hooks (per oss-pipeline-monitor-requirements.md)
// ============================================================================

/**
 * Fetch recent pipeline runs with filters.
 */
export function usePipelineMonitorRuns(options: {
  time?: TimeRangeOption
  scanner?: PipelineMonitorScannerType
  limit?: number
  offset?: number
  start?: string
  end?: string
  enabled?: boolean
} = {}) {
  return useQuery({
    queryKey: queryKeys.pipelineMonitorRuns({
      time: options.time,
      scanner: options.scanner,
      limit: options.limit,
      start: options.start,
      end: options.end,
    }),
    queryFn: () => api.getPipelineMonitorRuns({
      time: options.time,
      scanner: options.scanner,
      limit: options.limit,
      offset: options.offset,
      start: options.start,
      end: options.end,
    }),
    enabled: options.enabled !== false,
    refetchInterval: 10000, // Refresh every 10 seconds for live updates
  })
}

/**
 * Fetch aggregated pipeline data for a time range.
 */
export function usePipelineAggregate(options: {
  time?: TimeRangeOption
  start?: string
  end?: string
  scanner?: PipelineMonitorScannerType
  verdict?: string[]
  dte_bucket?: string[]
  option_side?: string[]
  enabled?: boolean
} = {}) {
  return useQuery({
    queryKey: queryKeys.pipelineAggregate({
      time: options.time,
      scanner: options.scanner,
      start: options.start,
      end: options.end,
    }),
    queryFn: () => api.getPipelineAggregateData({
      time: options.time,
      start: options.start,
      end: options.end,
      scanner: options.scanner,
      verdict: options.verdict,
      dte_bucket: options.dte_bucket,
      option_side: options.option_side,
    }),
    enabled: options.enabled !== false,
    staleTime: 30000, // Consider fresh for 30 seconds
  })
}

/**
 * Fetch detailed pipeline data for a specific run.
 */
export function usePipelineRunDetail(
  runId: string | null,
  options: {
    verdict?: string[]
    dte_bucket?: string[]
    option_side?: string[]
  } = {}
) {
  return useQuery({
    queryKey: queryKeys.pipelineRunDetail(runId || ''),
    queryFn: () => api.getPipelineRunDetail(runId!, options),
    enabled: !!runId,
    staleTime: 60000, // Consider fresh for 1 minute
  })
}

// ============================================================================
// Opportunities Page Hooks (OSS_Opportunities_Page_Specification)
// ============================================================================

/**
 * Fetch market context for Context Bar.
 * Polls every 10 seconds during market hours.
 */
export function useMarketContext() {
  return useQuery({
    queryKey: queryKeys.marketContext,
    queryFn: api.getMarketContext,
    refetchInterval: 10000, // 10 seconds during market hours
    staleTime: 5000,
  })
}

/**
 * Fetch APPROVE evaluations with conviction scoring.
 * Computes conviction scores client-side.
 */
export function useApproveEvaluations(
  options: {
    excludeEarnings?: boolean
    earningsDays?: number
    scanner?: string
    verdict?: string
    limit?: number
  } = {}
) {
  return useQuery({
    queryKey: queryKeys.approveEvaluations({
      excludeEarnings: options.excludeEarnings,
      scanner: options.scanner,
      verdict: options.verdict,
      limit: options.limit,
    }),
    queryFn: async () => {
      const response = await api.getApproveEvaluations({
        excludeEarnings: options.excludeEarnings,
        earningsDays: options.earningsDays,
        scanner: options.scanner,
        verdict: options.verdict,
        limit: options.limit,
      })

      // Calculate conviction scores client-side (sorting is handled by consuming components)
      const enhanced = enhanceWithConvictionScores(
        response.evaluations as ApproveEvaluation[]
      )

      return {
        ...response,
        evaluations: enhanced,
      }
    },
    staleTime: 30000, // Consider fresh for 30 seconds
  })
}

/**
 * Fetch WATCH intelligence insights.
 */
export function useWatchInsights(sinceDays: number = 7) {
  return useQuery({
    queryKey: queryKeys.watchInsights(sinceDays),
    queryFn: () => api.getWatchInsights(sinceDays),
    staleTime: 60000, // Consider fresh for 1 minute
  })
}

/**
 * Fetch contract quotes for real-time price updates.
 */
export function useContractQuotes(contractIds: string[], enabled: boolean = true) {
  return useQuery({
    queryKey: queryKeys.contractQuotes(contractIds),
    queryFn: () => api.getContractQuotes(contractIds),
    enabled: enabled && contractIds.length > 0,
    refetchInterval: 30000, // 30 seconds during market hours
    staleTime: 15000,
  })
}

/**
 * Trigger an on-demand UV scan.
 * Invokes the Publisher and Aggregator Lambdas outside of the normal schedule.
 */
export function useTriggerUVScan() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.triggerUVScan,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pipeline', 'monitor'] })
    },
  })
}

/**
 * Generate thesis for an evaluation.
 */
export function useGenerateThesis() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (evaluationId: string) => api.generateThesis(evaluationId),
    onSuccess: () => {
      // Invalidate evaluations and detail queries to refresh thesis data
      queryClient.invalidateQueries({ queryKey: ['evaluations'] })
    },
  })
}

// ============================================================================
// Paper Trading Workstation Hooks
// ============================================================================

export function usePaperTradingSummary() {
  return useQuery({
    queryKey: ['paper-trading', 'summary'] as const,
    queryFn: api.getPaperTradingSummary,
    refetchInterval: 30000,
  })
}

export function usePaperTradingPositions(status?: string) {
  return useQuery({
    queryKey: ['paper-trading', 'positions', status] as const,
    queryFn: () => api.getPaperTradingPositions(status),
    refetchInterval: status === 'open' ? 30000 : undefined,
  })
}

export function usePaperTradingMetrics() {
  return useQuery({
    queryKey: ['paper-trading', 'metrics'] as const,
    queryFn: api.getPaperTradingMetrics,
    staleTime: 60000,
  })
}

export function usePaperTradingTiers() {
  return useQuery({
    queryKey: ['paper-trading', 'tiers'] as const,
    queryFn: api.getPaperTradingTiers,
    staleTime: 60000,
  })
}

export function usePaperTradingExits() {
  return useQuery({
    queryKey: ['paper-trading', 'exits'] as const,
    queryFn: api.getPaperTradingExits,
    staleTime: 60000,
  })
}

export function useAIInsights() {
  return useQuery({
    queryKey: ['paper-trading', 'ai-insights'] as const,
    queryFn: api.getAIInsights,
    staleTime: 5 * 60 * 1000,
  })
}

export function useUpdatePositions() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.updatePaperTradingPositions,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['paper-trading'] })
    },
  })
}

export function useClosePosition() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      positionId,
      exitPrice,
    }: {
      positionId: string
      exitPrice?: number
    }) => api.closePaperTradingPosition(positionId, exitPrice),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['paper-trading'] })
    },
  })
}

export function useEquityCurve(period: string = '30d') {
  return useQuery({
    queryKey: ['paper-trading', 'equity-curve', period] as const,
    queryFn: () => api.getEquityCurve(period),
    staleTime: 60000,
  })
}

export function usePositionSnapshots(positionId: string) {
  return useQuery({
    queryKey: ['paper-trading', 'snapshots', positionId] as const,
    queryFn: () => api.getPositionSnapshots(positionId),
    enabled: !!positionId,
    staleTime: 30000,
  })
}

export function useAnalyzePosition() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (positionId: string) => api.analyzePosition(positionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['paper-trading'] })
    },
  })
}

// Summary metrics — pre-aggregated when unfiltered, computed when filtered
export function useSummaryMetrics(params: {
  status?: string
  verdict?: string
  scanner?: string
  period?: string
} = {}) {
  return useQuery({
    queryKey: ['paper-trading', 'summary-metrics', params] as const,
    queryFn: () => api.getSummaryMetrics(params),
    staleTime: 30000,
  })
}

// Paginated positions with server-side filtering
export function usePaginatedPositions(params: {
  status?: string
  limit?: number
  cursor?: string
  verdict?: string
  scanner?: string
  period?: string
} = {}) {
  return useQuery({
    queryKey: ['paper-trading', 'positions-paginated', params] as const,
    queryFn: () => api.getPositionsPaginated(params),
    staleTime: 15000,
  })
}

// ============================================================================
// Backtest Hooks
// ============================================================================

export function useDataStoreStatus() {
  return useQuery({
    queryKey: ['backtest', 'data-store-status'] as const,
    queryFn: api.getDataStoreStatus,
    staleTime: 60000,
  })
}

export function useValidateDataStore() {
  return useMutation({
    mutationFn: api.validateDataStore,
  })
}

export function useBacktestRuns(status?: string) {
  return useQuery({
    queryKey: ['backtest', 'runs', status] as const,
    queryFn: () => api.listBacktestRuns(status),
    staleTime: 30000,
  })
}

export function useBacktestRun(runId: string) {
  return useQuery({
    queryKey: ['backtest', 'runs', runId] as const,
    queryFn: () => api.getBacktestRun(runId),
    enabled: !!runId,
    staleTime: 10000,
  })
}

export function useCreateBacktestRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.createBacktestRun,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['backtest', 'runs'] })
    },
  })
}

export function useDeleteBacktestRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.deleteBacktestRun,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['backtest', 'runs'] })
    },
  })
}

export function useBacktestTrades(
  runId: string,
  params?: { scanner?: string; verdict?: string; limit?: number }
) {
  return useQuery({
    queryKey: ['backtest', 'trades', runId, params] as const,
    queryFn: () => api.listBacktestTrades(runId, params),
    enabled: !!runId,
    staleTime: 30000,
  })
}

// Phase 3: Results hooks

export function useBacktestSummary(runId: string) {
  return useQuery({
    queryKey: ['backtest', 'summary', runId] as const,
    queryFn: () => api.getBacktestSummary(runId),
    enabled: !!runId,
    staleTime: 60000,
  })
}

export function useBacktestEquityCurve(runId: string) {
  return useQuery({
    queryKey: ['backtest', 'equity-curve', runId] as const,
    queryFn: () => api.getBacktestEquityCurve(runId),
    enabled: !!runId,
    staleTime: 60000,
  })
}

export function useBacktestMonthlyPnl(runId: string) {
  return useQuery({
    queryKey: ['backtest', 'monthly-pnl', runId] as const,
    queryFn: () => api.getBacktestMonthlyPnl(runId),
    enabled: !!runId,
    staleTime: 60000,
  })
}

export function useBacktestSegments(runId: string, segmentType: string) {
  return useQuery({
    queryKey: ['backtest', 'segments', runId, segmentType] as const,
    queryFn: () => api.getBacktestSegments(runId, segmentType),
    enabled: !!runId && !!segmentType,
    staleTime: 60000,
  })
}
