// Convex Mode API client + React Query hooks.
//
// Uses the same fetch wrapper as ./api.ts but lives in a dedicated module
// so the Convex feature can be developed and removed in isolation.

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import type {
  ConvexEvaluationResponse,
  ConvexEvaluationsListResponse,
  ConvexFailedCandidatesResponse,
  ConvexRunsListResponse,
  ConvexStageEventsResponse,
  ConvexTier,
  ConvexUniverseResponse,
} from './convexTypes'

const API_BASE = import.meta.env.VITE_API_URL || ''

class ConvexApiError extends Error {
  constructor(public status: number, public statusText: string, public body: unknown) {
    super(`Convex API: ${status} ${statusText}`)
    this.name = 'ConvexApiError'
  }
}

async function fetchConvex<T>(endpoint: string): Promise<T> {
  const url = `${API_BASE}${endpoint}`
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 30000)
  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new ConvexApiError(response.status, response.statusText, body)
    }
    return response.json() as Promise<T>
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ConvexApiError(408, 'Request Timeout', { message: 'Request timed out' })
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

// ---------------------------------------------------------------------------
// Bare client functions
// ---------------------------------------------------------------------------

export async function listConvexEvaluations(
  tier?: ConvexTier,
  limit = 50,
): Promise<ConvexEvaluationsListResponse> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (tier) params.set('tier', tier)
  return fetchConvex(`/api/convex/evaluations?${params.toString()}`)
}

export async function getConvexEvaluation(
  ticker: string,
  evaluationId: string,
): Promise<ConvexEvaluationResponse> {
  return fetchConvex(
    `/api/convex/evaluations/${encodeURIComponent(ticker)}/${encodeURIComponent(evaluationId)}`,
  )
}

export async function listConvexRuns(
  limit = 20,
): Promise<ConvexRunsListResponse> {
  const params = new URLSearchParams({ limit: String(limit) })
  return fetchConvex(`/api/convex/runs?${params.toString()}`)
}

export async function listConvexStageEvents(
  runId: string,
  ticker?: string,
): Promise<ConvexStageEventsResponse> {
  const params = new URLSearchParams()
  if (ticker) params.set('ticker', ticker)
  const qs = params.toString() ? `?${params.toString()}` : ''
  return fetchConvex(`/api/convex/runs/${encodeURIComponent(runId)}/stage-events${qs}`)
}

export async function listConvexFailedCandidates(
  runId: string,
): Promise<ConvexFailedCandidatesResponse> {
  return fetchConvex(`/api/convex/runs/${encodeURIComponent(runId)}/failed-candidates`)
}

export async function getConvexUniverse(): Promise<ConvexUniverseResponse> {
  return fetchConvex('/api/convex/universe')
}

// ---------------------------------------------------------------------------
// React Query hooks
// ---------------------------------------------------------------------------

export function useConvexEvaluations(
  tier?: ConvexTier,
  limit = 50,
): UseQueryResult<ConvexEvaluationsListResponse> {
  return useQuery({
    queryKey: ['convex', 'evaluations', tier ?? 'all', limit],
    queryFn: () => listConvexEvaluations(tier, limit),
    staleTime: 60_000,
  })
}

export function useConvexEvaluation(
  ticker: string | undefined,
  evaluationId: string | undefined,
): UseQueryResult<ConvexEvaluationResponse> {
  return useQuery({
    queryKey: ['convex', 'evaluation', ticker, evaluationId],
    queryFn: () => getConvexEvaluation(ticker!, evaluationId!),
    enabled: Boolean(ticker && evaluationId),
    staleTime: 60_000,
  })
}

export function useConvexRuns(
  limit = 20,
): UseQueryResult<ConvexRunsListResponse> {
  return useQuery({
    queryKey: ['convex', 'runs', limit],
    queryFn: () => listConvexRuns(limit),
    staleTime: 60_000,
  })
}

export function useConvexStageEvents(
  runId: string | undefined,
  ticker?: string,
): UseQueryResult<ConvexStageEventsResponse> {
  return useQuery({
    queryKey: ['convex', 'stage-events', runId, ticker ?? 'all'],
    queryFn: () => listConvexStageEvents(runId!, ticker),
    enabled: Boolean(runId),
    staleTime: 60_000,
  })
}

export function useConvexFailedCandidates(
  runId: string | undefined,
): UseQueryResult<ConvexFailedCandidatesResponse> {
  return useQuery({
    queryKey: ['convex', 'failed-candidates', runId],
    queryFn: () => listConvexFailedCandidates(runId!),
    enabled: Boolean(runId),
    staleTime: 60_000,
  })
}

export function useConvexUniverse(): UseQueryResult<ConvexUniverseResponse> {
  return useQuery({
    queryKey: ['convex', 'universe'],
    queryFn: getConvexUniverse,
    staleTime: 5 * 60_000,
  })
}

export { ConvexApiError }
