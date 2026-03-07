/**
 * Enriched positions hook — server-side filtering + denormalized data.
 *
 * Positions now carry enrichment fields directly from the backend.
 * No client-side join with evaluations needed.
 */

import { useMemo } from 'react'
import { usePaginatedPositions, useSummaryMetrics } from './useApi'
import { parseOCC } from '@/lib/occParser'
import type { EnrichedPosition, PaperPosition } from '@/lib/types'

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
  nextCursor: string | null
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

/** Map short scanner filter codes to backend values. */
const SCANNER_MAP: Record<string, string> = {
  UV: 'UNUSUAL_VOLUME',
  BRK: 'BREAKOUT',
  CMP: 'COMPRESSION_EXPANSION',
  CHP: 'CHEAP_OPTIONS',
}

export function useEnrichedPositions(
  filters: EnrichedPositionsFilters = {}
): EnrichedPositionsResult {
  // Map scanner filter to backend value
  const mappedScanner =
    filters.scanner && filters.scanner !== 'all'
      ? SCANNER_MAP[filters.scanner] ?? filters.scanner
      : undefined

  // Map verdict filter
  const mappedVerdict =
    filters.verdict && filters.verdict !== 'ALL' ? filters.verdict : undefined

  // Use paginated positions with server-side filtering
  const positionsQuery = usePaginatedPositions({
    status: filters.status || 'open',
    limit: 100,
    verdict: mappedVerdict,
    scanner: mappedScanner,
    period: filters.period !== 'all' ? filters.period : undefined,
  })

  // Use summary metrics — pre-aggregated when unfiltered, computed when filtered
  const metricsQuery = useSummaryMetrics({
    status: filters.status || 'open',
    verdict: mappedVerdict,
    scanner: mappedScanner,
    period: filters.period !== 'all' ? filters.period : undefined,
  })

  const enrichedPositions = useMemo(() => {
    const positions: PaperPosition[] = positionsQuery.data?.positions ?? []

    return positions.map((pos): EnrichedPosition => {
      const parsed = parseOCC(pos.option_ticker)

      return {
        ...pos,
        // Use denormalized underlying_ticker, fallback to parsed OCC
        underlying_ticker:
          pos.underlying_ticker ?? parsed?.ticker ?? pos.option_ticker,
        // Parsed fallback for option_type_parsed
        option_type_parsed:
          (pos.option_type as 'CALL' | 'PUT') ?? parsed?.type ?? null,
        // Use denormalized fields directly, fallback to parsed where applicable
        strike: pos.strike ?? parsed?.strike ?? null,
        expiration_date: pos.expiration_date ?? parsed?.expiry ?? null,
      }
    })
  }, [positionsQuery.data])

  // Compute raw metrics from pre-aggregated summary or from positions as fallback
  const rawMetrics = useMemo(() => {
    const global = metricsQuery.data?.global
    const positions = enrichedPositions

    if (global) {
      // Compute avgScore from loaded positions when backend counters aren't populated
      let avgScore = global.avg_score
      if (avgScore == null && positions.length > 0) {
        const scored = positions.filter((p) => p.conviction_score != null)
        if (scored.length > 0) {
          avgScore =
            scored.reduce((s, p) => s + (p.conviction_score ?? 0), 0) /
            scored.length
        }
      }

      // Compute bestTrade from loaded positions when backend hasn't tracked it yet
      let bestTrade: { ticker: string; pnl: number } | null = null
      if (global.best_trade_pnl != null) {
        bestTrade = { ticker: '', pnl: global.best_trade_pnl }
      } else if (positions.length > 0) {
        const best = positions.reduce((best, p) =>
          p.current_pnl_pct > (best?.current_pnl_pct ?? -Infinity) ? p : best
        )
        if (best && best.current_pnl_pct !== 0) {
          bestTrade = {
            ticker: best.underlying_ticker ?? '',
            pnl: best.current_pnl_pct,
          }
        }
      }

      return {
        totalPnl: global.total_pnl,
        winRate: global.closed_count > 0 ? global.win_rate : null,
        avgReturn: global.avg_return,
        avgScore,
        bestTrade,
        activeCount: global.open_count,
        closedCount: global.closed_count,
      }
    }

    // Fallback: compute from current page of positions
    const closed = positions.filter((p) => p.status === 'CLOSED')
    const open = positions.filter((p) => p.status === 'OPEN')

    const totalPnl = positions.reduce((sum, p) => {
      const price =
        p.status === 'CLOSED' && p.exit_price != null
          ? p.exit_price
          : p.current_price
      return sum + (price - p.entry_price) * 100
    }, 0)

    const wins = closed.filter((p) => p.current_pnl_pct > 0)
    const winRate =
      closed.length > 0 ? (wins.length / closed.length) * 100 : null

    const avgReturn =
      positions.length > 0
        ? positions.reduce((s, p) => s + p.current_pnl_pct, 0) /
          positions.length
        : 0

    return {
      totalPnl,
      winRate,
      avgReturn,
      avgScore: null,
      bestTrade: null,
      activeCount: open.length,
      closedCount: closed.length,
    }
  }, [metricsQuery.data, enrichedPositions])

  return {
    enrichedPositions,
    isLoading: positionsQuery.isLoading,
    error: (positionsQuery.error ?? metricsQuery.error) as Error | null,
    nextCursor: positionsQuery.data?.next_cursor ?? null,
    rawMetrics,
  }
}
