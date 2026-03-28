/**
 * Conviction Score Calculator
 * 
 * Client-side calculation of conviction scores for the Opportunities page.
 * Per Section 4 of OSS_Opportunities_Page_Specification.
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

// Default weights (Section 4.1)
export const DEFAULT_WEIGHTS: ConvictionScoreWeights = {
  thetaAdjustedEv: 0.25,
  returnPct: 0.15,
  compositePillar: 0.25,
  gateMargin: 0.15,
  scannerConvergence: 0.10,
  timeSensitivity: 0.10,
}

// Default EV benchmark for normalization.
// Theta-adjusted EV is per-contract in dollars over a 5-day hold.
// Typical values range from -$5 to +$15, so $15 maps a strong EV to 100%.
export const DEFAULT_EV_BENCHMARK = 15

// Return% benchmark: 20% return on contract cost maps to a normalized score of 100.
// Cheap options with high return% score comparably to expensive options with high absolute EV.
export const DEFAULT_RETURN_PCT_BENCHMARK = 20

// Premium threshold for UNUSUAL_VOLUME urgency escalation.
// UV opportunities on cheap options (mid <= $1.50) are effectively "act now".
export const CHEAP_UV_PREMIUM_THRESHOLD = 1.50

// Scanner urgency mapping (Section 4.2.5)
const URGENCY_BOOST: Record<UrgencyLevel, number> = {
  act_now: 100,
  hours: 50,
  patient: 0,
}

// Scanner convergence bonus (Section 4.2.4)
const CONVERGENCE_BONUS: Record<number, number> = {
  1: 0,
  2: 50,
  3: 75,
  4: 100,
}

/**
 * Normalize theta-adjusted EV to 0-100 scale.
 * Per Section 4.2.1:
 * - EV <= 0: Score = 0
 * - EV > 0: Score = min(100, EV / EV_benchmark × 100)
 */
export function normalizeEV(ev: number, benchmark: number = DEFAULT_EV_BENCHMARK): number {
  if (ev <= 0) return 0
  return Math.min(100, (ev / benchmark) * 100)
}

/**
 * Calculate composite pillar score.
 * Per Section 4.2.2:
 * Composite = (Directional + Volatility + Structure) / 3
 */
export function calculateCompositePillar(pillarScores: Record<string, number>): number {
  const directional = pillarScores['DIRECTIONAL'] ?? 0
  const volatility = pillarScores['VOLATILITY'] ?? 0
  const structure = pillarScores['STRUCTURE'] ?? 0
  
  return (directional + volatility + structure) / 3
}

/**
 * Get scanner convergence bonus.
 * Per Section 4.2.4:
 * 1 scanner: 0, 2 scanners: 50, 3 scanners: 75, 4+: 100
 */
export function getConvergenceBonus(convergenceCount: number): number {
  if (convergenceCount >= 4) return 100
  return CONVERGENCE_BONUS[convergenceCount] ?? 0
}

/**
 * Get time sensitivity boost based on urgency.
 * Per Section 4.2.5:
 * - Act Now (Breakout/Breakdown): 100
 * - Hours (Unusual Volume): 50
 * - Patient (Compression, Cheap Options): 0
 */
export function getTimeSensitivityBoost(urgency: UrgencyLevel): number {
  return URGENCY_BOOST[urgency] ?? 0
}

/**
 * Normalize return% to 0-100 scale.
 * Return% = (thetaAdjEV / (mid * 100)) * 100.
 * Normalized: min(100, returnPct / benchmark * 100), clamped at 0 when negative or mid <= 0.
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
 * Score = (W_ev × EV_norm) + (W_ret × Ret_norm) + (W_pillar × Pillar) +
 *         (W_margin × Margin) + (W_convergence × Convergence) + (W_time × TimeSensitivity)
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

  // 4. Gate Margin Score
  const marginRaw = evaluation.gateMargin ?? 50
  const marginNormalized = Math.max(0, Math.min(100, marginRaw)) // Clamp to 0-100
  const marginWeighted = marginNormalized * weights.gateMargin

  // 5. Scanner Convergence Bonus
  const convergenceCount = evaluation.scannerConvergence ?? 1
  const convergenceRaw = getConvergenceBonus(convergenceCount)
  const convergenceNormalized = convergenceRaw // Already 0-100
  const convergenceWeighted = convergenceNormalized * weights.scannerConvergence

  // 6. Time Sensitivity Boost
  const urgency = evaluation.urgency ?? 'patient'
  const timeRaw = getTimeSensitivityBoost(urgency)
  const timeNormalized = timeRaw // Already 0-100
  const timeWeighted = timeNormalized * weights.timeSensitivity

  // Calculate total
  const total = evWeighted + retWeighted + pillarWeighted + marginWeighted + convergenceWeighted + timeWeighted

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
      gateMargin: {
        raw: marginRaw,
        normalized: round1(marginNormalized),
        weighted: round1(marginWeighted),
      },
      scannerConvergence: {
        raw: convergenceCount,
        normalized: convergenceNormalized,
        weighted: round1(convergenceWeighted),
      },
      timeSensitivity: {
        raw: timeRaw,
        normalized: timeNormalized,
        weighted: round1(timeWeighted),
      },
    },
  }
}

/**
 * Enhance evaluations with conviction scores.
 * Calculates conviction score for each evaluation and adds it to the object.
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
    // Primary: cheapest premium first
    const costA = (a.mid ?? 0) * 100
    const costB = (b.mid ?? 0) * 100
    if (costA !== costB) return costA - costB
    // Tiebreak: highest return % first
    const retA = calculateReturnPct(a.thetaAdjustedEV ?? null, a.mid ?? null) ?? 0
    const retB = calculateReturnPct(b.thetaAdjustedEV ?? null, b.mid ?? null) ?? 0
    if (retB !== retA) return retB - retA
    // Final tiebreak: conviction descending
    return (b.convictionScore ?? 0) - (a.convictionScore ?? 0)
  })
}

/**
 * Filter evaluations by conviction threshold.
 * Per Section 4.4:
 * - Default threshold: 75
 * - Range: 50-95
 */
export function filterByConvictionThreshold(
  evaluations: ApproveEvaluation[],
  threshold: number = 75,
): ApproveEvaluation[] {
  return evaluations.filter(e => (e.convictionScore ?? 0) >= threshold)
}

/**
 * Get conviction score color class based on value.
 * Per Section 2.1.3:
 * - Score >= 85: High (cyan)
 * - Score >= 75: Medium (green)
 * - Score < 75: Low (amber)
 */
export function getConvictionColorClass(score: number): string {
  if (score >= 85) return 'conviction-high'
  if (score >= 75) return 'conviction-medium'
  return 'conviction-low'
}

/**
 * Get conviction score CSS color variable.
 */
export function getConvictionColor(score: number): string {
  if (score >= 85) return 'var(--color-conviction-high)'
  if (score >= 75) return 'var(--color-conviction-medium)'
  return 'var(--color-conviction-low)'
}

/**
 * Format contract identifier for display.
 * Per Section 9.6: "{TICKER} {STRIKE}{C/P} {MM/DD}"
 */
export function formatContractId(
  ticker: string,
  strike: number,
  optionType: 'CALL' | 'PUT',
  expiration: string,
): string {
  const type = optionType === 'CALL' ? 'C' : 'P'
  // Parse expiration date (YYYY-MM-DD) to MM/DD
  const date = new Date(expiration)
  const month = (date.getMonth() + 1).toString()
  const day = date.getDate().toString()
  
  return `${ticker} ${strike}${type} ${month}/${day}`
}

/**
 * Determine urgency from scanner types.
 * Per Section 4.2.5.
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
