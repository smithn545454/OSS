/**
 * Conviction Score Calculator
 *
 * Uses the pipeline's decision final_score directly (pillar-weighted composite)
 * with freshness decay applied. No additional frontend scoring formula.
 *
 * The pipeline already evaluates quality through 3 pillars:
 *   final_score = 0.35 × Directional + 0.35 × Volatility + 0.30 × Structure
 *
 * Freshness decay ensures recent opportunities rank higher than stale ones.
 */

import type {
  ApproveEvaluation,
  OpportunityFilters,
  UrgencyLevel,
  ScannerType,
} from './types'

// Premium threshold for UNUSUAL_VOLUME urgency escalation in alerts.
export const CHEAP_UV_PREMIUM_THRESHOLD = 1.50

// Freshness decay: grace period with no penalty, then linear decay.
// First 8 hours: no decay (covers a full trading day).
// After grace period: linear decay over 24 hours to floor.
export const FRESHNESS_GRACE_HOURS = 8

// Hours of linear decay after grace period expires.
export const FRESHNESS_DECAY_WINDOW = 24

// Floor multiplier — stale evals retain at least 75% of base score.
export const FRESHNESS_MIN_DECAY = 0.75

/**
 * Calculate freshness decay multiplier based on evaluation age.
 * Grace period (8h): no decay — today's opportunities compete on pure merit.
 * After grace: linear decay over 24h to 75% floor.
 *
 * 0-8h: 100%, 12h: 83%, 20h: 50%, 32h+: 75% floor.
 */
export function calculateFreshnessDecay(
  evaluatedAt: string,
  now: Date = new Date(),
): number {
  const ageMs = now.getTime() - new Date(evaluatedAt).getTime()
  const ageHours = Math.max(0, ageMs / 3_600_000)
  if (ageHours <= FRESHNESS_GRACE_HOURS) return 1.0
  const decayAge = ageHours - FRESHNESS_GRACE_HOURS
  return Math.max(
    FRESHNESS_MIN_DECAY,
    1.0 - decayAge / FRESHNESS_DECAY_WINDOW,
  )
}

/**
 * Enhance evaluations with conviction scores.
 * Uses the pipeline's decision.final_score directly, with freshness decay
 * so recent evaluations rank higher.
 */
export function enhanceWithConvictionScores(
  evaluations: ApproveEvaluation[],
): ApproveEvaluation[] {
  const now = new Date()
  return evaluations.map(evaluation => {
    const pipelineScore = evaluation.decision?.final_score ?? 0
    const decay = calculateFreshnessDecay(evaluation.evaluated_at, now)
    const decayedScore = Math.round(pipelineScore * decay * 10) / 10
    return {
      ...evaluation,
      convictionScore: decayedScore,
    }
  })
}

/**
 * Filter evaluations by opportunity filters (premium, delta, moneyness).
 */
export function filterByOpportunityFilters(
  evaluations: ApproveEvaluation[],
  filters: OpportunityFilters,
): ApproveEvaluation[] {
  return evaluations.filter(e => {
    const premium = e.mid ?? 0
    if (filters.premiumMax != null && premium > filters.premiumMax) return false
    if (filters.premiumMin != null && premium < filters.premiumMin) return false

    const absDelta = Math.abs(e.delta ?? 0)
    if (filters.deltaMax != null && absDelta > filters.deltaMax) return false
    if (filters.deltaMin != null && absDelta < filters.deltaMin) return false

    if (filters.moneyness !== 'all') {
      const mp = e.moneyness_pct ?? 0
      // moneyness_pct: positive = OTM, negative = ITM, near-zero = ATM
      if (filters.moneyness === 'otm' && mp <= 0) return false
      if (filters.moneyness === 'itm' && mp >= 0) return false
      if (filters.moneyness === 'atm' && Math.abs(mp) > 3) return false
    }
    return true
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
 */
export function filterByConvictionThreshold(
  evaluations: ApproveEvaluation[],
  threshold: number = 65,
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

/** Calculate return% for sorting — imported from metrics.ts */
import { calculateReturnPct } from './metrics'
