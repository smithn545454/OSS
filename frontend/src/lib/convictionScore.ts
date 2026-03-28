/**
 * Conviction Score Calculator
 *
 * 3-component formula:
 *   conviction = 0.35 × EV_norm + 0.30 × Return%_norm + 0.35 × Pillar_avg
 *
 * Gate Margin and Scanner Convergence are excluded — gates are binary pass/fail,
 * and convergence is too rare to be useful. Urgency is excluded — the
 * time-sensitivity of cheap options is already captured by Return%.
 */

import type {
  ApproveEvaluation,
  ConvictionScoreBreakdown,
  ConvictionScoreWeights,
  UrgencyLevel,
  ScannerType,
} from './types'
import { calculateReturnPct } from './metrics'

export type { ConvictionScoreWeights }

// Default weights — must match backend DEFAULT_WEIGHTS exactly
export const DEFAULT_WEIGHTS: ConvictionScoreWeights = {
  thetaAdjustedEv: 0.35,
  returnPct: 0.30,
  compositePillar: 0.35,
}

// Default EV benchmark for normalization.
// Theta-adjusted EV is per-contract in dollars over a 5-day hold.
// Typical values range from -$5 to +$15, so $15 maps a strong EV to 100%.
export const DEFAULT_EV_BENCHMARK = 15

// Return% benchmark: 20% return on contract cost maps to a normalized score of 100.
export const DEFAULT_RETURN_PCT_BENCHMARK = 20

// Premium threshold for UNUSUAL_VOLUME urgency escalation in alerts.
export const CHEAP_UV_PREMIUM_THRESHOLD = 1.50

/**
 * Normalize theta-adjusted EV to 0-100 scale.
 * EV <= 0: Score = 0. EV > 0: Score = min(100, EV / benchmark × 100)
 */
export function normalizeEV(ev: number, benchmark: number = DEFAULT_EV_BENCHMARK): number {
  if (ev <= 0) return 0
  return Math.min(100, (ev / benchmark) * 100)
}

/**
 * Calculate composite pillar score.
 * Composite = (Directional + Volatility + Structure) / 3
 */
export function calculateCompositePillar(pillarScores: Record<string, number>): number {
  const directional = pillarScores['DIRECTIONAL'] ?? 0
  const volatility = pillarScores['VOLATILITY'] ?? 0
  const structure = pillarScores['STRUCTURE'] ?? 0
  return (directional + volatility + structure) / 3
}

/**
 * Normalize return% to 0-100 scale.
 * Return% = (thetaAdjEV / (mid * 100)) * 100.
 * Normalized: min(100, returnPct / benchmark * 100), clamped at 0.
 */
export function normalizeReturnPct(
  thetaAdjEV: number,
  mid: number,
  benchmark: number = DEFAULT_RETURN_PCT_BENCHMARK,
): number {
  if (mid <= 0 || thetaAdjEV <= 0) return 0
  const returnPct = (thetaAdjEV / (mid * 100)) * 100
  return Math.min(100, (returnPct / benchmark) * 100)
}

/**
 * Calculate the full conviction score with breakdown.
 * Score = (W_ev × EV_norm) + (W_ret × Ret_norm) + (W_pillar × Pillar)
 */
export function calculateConvictionScore(
  evaluation: ApproveEvaluation,
  weights: ConvictionScoreWeights = DEFAULT_WEIGHTS,
  evBenchmark: number = DEFAULT_EV_BENCHMARK,
  returnPctBenchmark: number = DEFAULT_RETURN_PCT_BENCHMARK,
): ConvictionScoreBreakdown {
  const round1 = (v: number) => Math.round(v * 10) / 10

  // 1. Theta-Adjusted EV (normalized)
  const evRaw = evaluation.thetaAdjustedEV ?? 0
  const evNormalized = normalizeEV(evRaw, evBenchmark)
  const evWeighted = evNormalized * weights.thetaAdjustedEv

  // 2. Return% (capital efficiency — cheap options with high return% score well)
  const mid = evaluation.mid ?? 0
  const retRaw = mid > 0 && evRaw > 0 ? (evRaw / (mid * 100)) * 100 : 0
  const retNormalized = normalizeReturnPct(evRaw, mid, returnPctBenchmark)
  const retWeighted = retNormalized * weights.returnPct

  // 3. Composite Pillar Score
  const pillarRaw = calculateCompositePillar(evaluation.pillarScores ?? {})
  const pillarNormalized = pillarRaw // Already 0-100
  const pillarWeighted = pillarNormalized * weights.compositePillar

  // Calculate total
  const total = evWeighted + retWeighted + pillarWeighted

  return {
    total: round1(total),
    components: {
      thetaAdjustedEv: {
        raw: evRaw,
        normalized: round1(evNormalized),
        weighted: round1(evWeighted),
      },
      returnPct: {
        raw: round1(retRaw),
        normalized: round1(retNormalized),
        weighted: round1(retWeighted),
      },
      compositePillar: {
        raw: pillarRaw,
        normalized: round1(pillarNormalized),
        weighted: round1(pillarWeighted),
      },
    },
  }
}

/**
 * Enhance evaluations with conviction scores.
 */
export function enhanceWithConvictionScores(
  evaluations: ApproveEvaluation[],
  weights: ConvictionScoreWeights = DEFAULT_WEIGHTS,
  evBenchmark: number = DEFAULT_EV_BENCHMARK,
): ApproveEvaluation[] {
  return evaluations.map(evaluation => {
    const breakdown = calculateConvictionScore(evaluation, weights, evBenchmark)
    return {
      ...evaluation,
      convictionScore: breakdown.total,
      convictionBreakdown: breakdown,
    }
  })
}

/**
 * Sort evaluations by conviction score (descending).
 */
export function sortByConviction(evaluations: ApproveEvaluation[]): ApproveEvaluation[] {
  return [...evaluations].sort((a, b) => {
    const scoreA = a.convictionScore ?? 0
    const scoreB = b.convictionScore ?? 0
    return scoreB - scoreA
  })
}

/**
 * Sort evaluations by cheapest premium first.
 * Tie-break: return % descending, then conviction descending.
 */
export function sortByCheap(evaluations: ApproveEvaluation[]): ApproveEvaluation[] {
  return [...evaluations].sort((a, b) => {
    const costA = (a.mid ?? 0) * 100
    const costB = (b.mid ?? 0) * 100
    if (costA !== costB) return costA - costB
    const retA = calculateReturnPct(a.thetaAdjustedEV ?? null, a.mid ?? null) ?? 0
    const retB = calculateReturnPct(b.thetaAdjustedEV ?? null, b.mid ?? null) ?? 0
    if (retB !== retA) return retB - retA
    return (b.convictionScore ?? 0) - (a.convictionScore ?? 0)
  })
}

/**
 * Filter evaluations by conviction threshold.
 * Default threshold: 70. Range: 50-95.
 */
export function filterByConvictionThreshold(
  evaluations: ApproveEvaluation[],
  threshold: number = 70,
): ApproveEvaluation[] {
  return evaluations.filter(e => (e.convictionScore ?? 0) >= threshold)
}

/**
 * Get conviction score color class based on value.
 */
export function getConvictionColorClass(score: number): string {
  if (score >= 85) return 'conviction-high'
  if (score >= 70) return 'conviction-medium'
  return 'conviction-low'
}

/**
 * Get conviction score CSS color variable.
 */
export function getConvictionColor(score: number): string {
  if (score >= 85) return 'var(--color-conviction-high)'
  if (score >= 70) return 'var(--color-conviction-medium)'
  return 'var(--color-conviction-low)'
}

/**
 * Format contract identifier for display.
 * "{TICKER} {STRIKE}{C/P} {MM/DD}"
 */
export function formatContractId(
  ticker: string,
  strike: number,
  optionType: 'CALL' | 'PUT',
  expiration: string,
): string {
  const type = optionType === 'CALL' ? 'C' : 'P'
  const date = new Date(expiration)
  const month = (date.getMonth() + 1).toString()
  const day = date.getDate().toString()
  return `${ticker} ${strike}${type} ${month}/${day}`
}

/**
 * Determine urgency from scanner types.
 * Used by alert service for urgency display — not part of conviction score.
 */
export function determineUrgency(scannerTypes: ScannerType[], mid?: number): UrgencyLevel {
  const hasBreakout = scannerTypes.some(s =>
    s === 'BREAKOUT' || s === 'BREAKDOWN'
  )
  if (hasBreakout) return 'act_now'

  const hasUnusualVolume = scannerTypes.some(s => s === 'UNUSUAL_VOLUME')
  if (hasUnusualVolume) {
    if (mid != null && mid <= CHEAP_UV_PREMIUM_THRESHOLD) return 'act_now'
    return 'hours'
  }

  return 'patient'
}
