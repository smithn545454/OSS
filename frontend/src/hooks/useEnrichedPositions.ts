/**
 * Client-side join hook for enriching paper positions with evaluation data.
 *
 * Joins positions (GET /paper-trading/positions) with evaluations
 * (GET /evaluations/approve?verdict=ALL) by evaluation_id.
 */

import { useMemo } from 'react'
import { usePaperTradingPositions, useApproveEvaluations } from './useApi'
import { parseOCC } from '@/lib/occParser'
import type {
  EnrichedPosition,
  ApproveEvaluation,
  PaperPosition,
  ScannerType,
} from '@/lib/types'

export interface EnrichedPositionsFilters {
  period?: string
  verdict?: string
  scanner?: string
  status?: string
}

export interface EnrichedPositionsResult {
  enrichedPositions: EnrichedPosition[]
  isLoading: boolean
  error: Error | null
  rawMetrics: {
    totalPnl: number
    winRate: number | null
    avgReturn: number
    avgScore: number | null
    bestTrade: { ticker: string; pnl: number } | null
    activeCount: number
    closedCount: number
  }
}

function getDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

export function useEnrichedPositions(
  filters: EnrichedPositionsFilters = {}
): EnrichedPositionsResult {
  const positionsQuery = usePaperTradingPositions()
  const evaluationsQuery = useApproveEvaluations({
    verdict: 'ALL',
    excludeEarnings: false,
    limit: 500,
  })

  const evalMap = useMemo(() => {
    const map = new Map<string, ApproveEvaluation>()
    const evals = evaluationsQuery.data?.evaluations
    if (evals) {
      for (const ev of evals) {
        map.set(ev.evaluation_id, ev)
      }
    }
    return map
  }, [evaluationsQuery.data])

  const enrichedPositions = useMemo(() => {
    const positions: PaperPosition[] = positionsQuery.data?.positions ?? []

    const enriched: EnrichedPosition[] = positions.map((pos) => {
      const ev = evalMap.get(pos.evaluation_id)
      const parsed = parseOCC(pos.option_ticker)
      const decision = ev?.decision

      return {
        ...pos,
        conviction_score: decision?.final_score ?? null,
        pillar_directional: decision?.directional_score ?? null,
        pillar_volatility: decision?.volatility_score ?? null,
        pillar_structure: decision?.structure_score ?? null,
        scanner_source: (ev?.scannerSource?.[0] as ScannerType) ?? null,
        convergence_count: ev?.scannerConvergence ?? 0,
        underlying_ticker:
          ev?.underlying_ticker ?? parsed?.ticker ?? pos.option_ticker,
        strike: ev?.strike ?? parsed?.strike ?? null,
        option_type_parsed:
          (ev?.option_type as 'CALL' | 'PUT') ?? parsed?.type ?? null,
        expiration_date:
          ev?.expiration_date ?? parsed?.expiry ?? null,
        entry_delta: ev?.delta ?? null,
        entry_iv: ev?.iv ?? null,
        entry_theta: ev?.theta ?? null,
        dte_at_entry: ev?.dte ?? null,
        dte_bucket: ev?.dte_bucket ?? null,
        gate_margin: ev?.gateMargin ?? null,
        theta_adj_ev: ev?.thetaAdjustedEV ?? null,
      }
    })

    // Apply filters
    let filtered = enriched

    // Period filter
    if (filters.period && filters.period !== 'all') {
      const daysMap: Record<string, number> = {
        '7d': 7,
        '14d': 14,
        '30d': 30,
        '90d': 90,
      }
      const days = daysMap[filters.period]
      if (days) {
        const cutoff = getDaysAgo(days)
        filtered = filtered.filter((p) => p.entry_date >= cutoff)
      }
    }

    // Verdict filter
    if (filters.verdict && filters.verdict !== 'ALL') {
      filtered = filtered.filter(
        (p) => p.verdict_at_entry === filters.verdict
      )
    }

    // Scanner filter
    if (filters.scanner && filters.scanner !== 'all') {
      const scannerMap: Record<string, string> = {
        UV: 'UNUSUAL_VOLUME',
        BRK: 'BREAKOUT',
        CMP: 'COMPRESSION_EXPANSION',
        CHP: 'CHEAP_OPTIONS',
      }
      const mapped = scannerMap[filters.scanner] ?? filters.scanner
      filtered = filtered.filter((p) => p.scanner_source === mapped)
    }

    // Status filter
    if (filters.status && filters.status !== 'all') {
      filtered = filtered.filter(
        (p) => p.status === filters.status!.toUpperCase()
      )
    }

    return filtered
  }, [positionsQuery.data, evalMap, filters])

  // Compute raw metrics from enriched positions
  const rawMetrics = useMemo(() => {
    const positions = enrichedPositions
    const closed = positions.filter((p) => p.status === 'CLOSED')
    const open = positions.filter((p) => p.status === 'OPEN')

    const totalPnl = positions.reduce((sum, p) => {
      const price = p.status === 'CLOSED' && p.exit_price != null
        ? p.exit_price
        : p.current_price
      return sum + (price - p.entry_price) * 100
    }, 0)

    const wins = closed.filter((p) => p.current_pnl_pct > 0)
    const winRate = closed.length > 0 ? (wins.length / closed.length) * 100 : null

    const avgReturn =
      positions.length > 0
        ? positions.reduce((s, p) => s + p.current_pnl_pct, 0) / positions.length
        : 0

    const scored = positions.filter((p) => p.conviction_score != null)
    const avgScore =
      scored.length > 0
        ? scored.reduce((s, p) => s + (p.conviction_score ?? 0), 0) / scored.length
        : null

    let bestTrade: { ticker: string; pnl: number } | null = null
    if (positions.length > 0) {
      const best = positions.reduce((max, p) =>
        p.current_pnl_pct > max.current_pnl_pct ? p : max
      )
      bestTrade = {
        ticker: best.underlying_ticker,
        pnl: best.current_pnl_pct,
      }
    }

    return {
      totalPnl,
      winRate,
      avgReturn,
      avgScore,
      bestTrade,
      activeCount: open.length,
      closedCount: closed.length,
    }
  }, [enrichedPositions])

  return {
    enrichedPositions,
    isLoading: positionsQuery.isLoading || evaluationsQuery.isLoading,
    error: (positionsQuery.error ?? evaluationsQuery.error) as Error | null,
    rawMetrics,
  }
}
