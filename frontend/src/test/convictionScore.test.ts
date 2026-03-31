import { describe, it, expect } from 'vitest'
import {
  enhanceWithConvictionScores,
  filterByOpportunityFilters,
  sortByConviction,
  filterByConvictionThreshold,
  getConvictionColorClass,
  getConvictionColor,
  formatContractId,
  determineUrgency,
  calculateFreshnessDecay,
  CHEAP_UV_PREMIUM_THRESHOLD,
  FRESHNESS_GRACE_HOURS,
  FRESHNESS_MIN_DECAY,
} from '../lib/convictionScore'
import type { ApproveEvaluation, OpportunityFilters, ScannerType, UrgencyLevel } from '../lib/types'

// ---------------------------------------------------------------------------
// Helper: builds a minimal ApproveEvaluation with overrides
// ---------------------------------------------------------------------------
function makeEval(overrides: Partial<ApproveEvaluation> = {}): ApproveEvaluation {
  return {
    evaluation_id: 'eval-001',
    opportunity_id: 'opp-001',
    underlying_ticker: 'AAPL',
    option_ticker: 'O:AAPL250321C00200000',
    option_type: 'CALL',
    expiration_date: '2025-03-21',
    dte: 30,
    strike: 200,
    underlying_price: 195,
    moneyness_pct: 2.56,
    bid: 5.0,
    ask: 5.5,
    mid: 5.25,
    spread_abs: 0.5,
    spread_pct: 9.52,
    iv: 0.32,
    delta: 0.45,
    gamma: 0.02,
    theta: -0.15,
    vega: 0.35,
    open_interest: 5000,
    volume: 300,
    oi_5d_change_pct: 12,
    breakeven_price: 205.25,
    required_move_pct: 5.26,
    expected_move_pct: 6.0,
    feasibility_ratio: 0.88,
    time_adjusted_feasibility: 0.9,
    dte_bucket: 'B',
    rank_score: 80,
    policy_version: 'v1.0.0',
    evaluated_at: '2025-02-09T12:00:00Z',
    decision: {
      evaluation_id: 'eval-001',
      verdict: 'APPROVE',
      quality_tier: 'TIER_1',
      final_score: 82,
      directional_score: 75,
      volatility_score: 80,
      structure_score: 85,
      primary_reason_code: 'HIGH_CONVICTION',
      supporting_reason_codes: [],
      failed_gates: [],
      concentration_warnings: [],
      policy_version: 'v1.0.0',
      decided_at: '2025-02-09T12:00:00Z',
    },
    pillarScores: {
      DIRECTIONAL: 75,
      VOLATILITY: 80,
      STRUCTURE: 85,
    },
    gateResults: [],
    gateMargin: 60,
    scannerSource: ['BREAKOUT'] as ScannerType[],
    scannerConvergence: 1,
    thetaAdjustedEV: 250,
    urgency: 'act_now' as UrgencyLevel,
    headline: null,
    ...overrides,
  }
}

// ===========================================================================
// enhanceWithConvictionScores — uses pipeline final_score + decay
// ===========================================================================
describe('enhanceWithConvictionScores', () => {
  it('uses decision.final_score as the base score', () => {
    const recentEval = makeEval({
      evaluated_at: new Date().toISOString(),
      decision: { ...makeEval().decision, final_score: 82 },
    })
    const enhanced = enhanceWithConvictionScores([recentEval])
    // Fresh eval (within grace period) should equal final_score
    expect(enhanced[0].convictionScore).toBe(82)
  })

  it('applies freshness decay to final_score', () => {
    const staleDate = new Date(Date.now() - 20 * 3600 * 1000).toISOString() // 20h old
    const staleEval = makeEval({
      evaluated_at: staleDate,
      decision: { ...makeEval().decision, final_score: 80 },
    })
    const enhanced = enhanceWithConvictionScores([staleEval])
    // 20h old = 12h past grace period = 50% decay → 80 * 0.5 = 40
    expect(enhanced[0].convictionScore).toBeLessThan(80)
    expect(enhanced[0].convictionScore).toBeGreaterThan(0)
  })

  it('does not mutate original evaluations', () => {
    const original = makeEval()
    enhanceWithConvictionScores([original])
    expect(original.convictionScore).toBeUndefined()
  })

  it('handles missing decision gracefully', () => {
    const noDecision = makeEval({
      evaluated_at: new Date().toISOString(),
      decision: undefined as unknown as ApproveEvaluation['decision'],
    })
    const enhanced = enhanceWithConvictionScores([noDecision])
    expect(enhanced[0].convictionScore).toBe(0)
  })
})

// ===========================================================================
// calculateFreshnessDecay
// ===========================================================================
describe('calculateFreshnessDecay', () => {
  it('returns 1.0 within grace period', () => {
    const now = new Date()
    const recent = new Date(now.getTime() - 4 * 3600 * 1000).toISOString() // 4h ago
    expect(calculateFreshnessDecay(recent, now)).toBe(1.0)
  })

  it('decays after grace period', () => {
    const now = new Date()
    const old = new Date(now.getTime() - 12 * 3600 * 1000).toISOString() // 12h ago = 4h past grace
    const decay = calculateFreshnessDecay(old, now)
    expect(decay).toBeLessThan(1.0)
    expect(decay).toBeGreaterThan(FRESHNESS_MIN_DECAY)
  })

  it('floors at minimum decay', () => {
    const now = new Date()
    const veryOld = new Date(now.getTime() - 48 * 3600 * 1000).toISOString()
    expect(calculateFreshnessDecay(veryOld, now)).toBe(FRESHNESS_MIN_DECAY)
  })
})

// ===========================================================================
// filterByOpportunityFilters
// ===========================================================================
describe('filterByOpportunityFilters', () => {
  const noFilters: OpportunityFilters = {
    premiumMax: null, premiumMin: null,
    deltaMax: null, deltaMin: null,
    moneyness: 'all',
  }

  it('returns all evaluations with no filters', () => {
    const evals = [makeEval(), makeEval()]
    expect(filterByOpportunityFilters(evals, noFilters)).toHaveLength(2)
  })

  it('filters by premium max', () => {
    const evals = [
      makeEval({ mid: 0.50 }),
      makeEval({ mid: 5.25 }),
      makeEval({ mid: 1.00 }),
    ]
    const result = filterByOpportunityFilters(evals, { ...noFilters, premiumMax: 2.0 })
    expect(result).toHaveLength(2)
    expect(result.map(e => e.mid)).toEqual([0.50, 1.00])
  })

  it('filters by premium min', () => {
    const evals = [
      makeEval({ mid: 0.50 }),
      makeEval({ mid: 5.25 }),
    ]
    const result = filterByOpportunityFilters(evals, { ...noFilters, premiumMin: 1.0 })
    expect(result).toHaveLength(1)
  })

  it('filters by delta max (uses absolute value)', () => {
    const evals = [
      makeEval({ delta: 0.15 }),
      makeEval({ delta: -0.45 }),
      makeEval({ delta: 0.25 }),
    ]
    const result = filterByOpportunityFilters(evals, { ...noFilters, deltaMax: 0.30 })
    expect(result).toHaveLength(2)
  })

  it('filters by delta min', () => {
    const evals = [
      makeEval({ delta: 0.10 }),
      makeEval({ delta: 0.45 }),
    ]
    const result = filterByOpportunityFilters(evals, { ...noFilters, deltaMin: 0.20 })
    expect(result).toHaveLength(1)
  })

  it('filters by OTM moneyness', () => {
    const evals = [
      makeEval({ moneyness_pct: 5.0 }),   // OTM
      makeEval({ moneyness_pct: -3.0 }),  // ITM
      makeEval({ moneyness_pct: 1.0 }),   // OTM
    ]
    const result = filterByOpportunityFilters(evals, { ...noFilters, moneyness: 'otm' })
    expect(result).toHaveLength(2)
  })

  it('filters by ATM moneyness (within 3%)', () => {
    const evals = [
      makeEval({ moneyness_pct: 1.0 }),
      makeEval({ moneyness_pct: 5.0 }),
      makeEval({ moneyness_pct: -1.5 }),
    ]
    const result = filterByOpportunityFilters(evals, { ...noFilters, moneyness: 'atm' })
    expect(result).toHaveLength(2) // 1.0 and -1.5 are within 3%
  })

  it('filters by ITM moneyness', () => {
    const evals = [
      makeEval({ moneyness_pct: 5.0 }),   // OTM
      makeEval({ moneyness_pct: -3.0 }),  // ITM
    ]
    const result = filterByOpportunityFilters(evals, { ...noFilters, moneyness: 'itm' })
    expect(result).toHaveLength(1)
  })

  it('combines multiple filters', () => {
    const evals = [
      makeEval({ mid: 0.50, delta: 0.15, moneyness_pct: 8.0 }),  // cheap OTM low delta
      makeEval({ mid: 5.25, delta: 0.45, moneyness_pct: -1.0 }), // expensive ITM high delta
      makeEval({ mid: 1.00, delta: 0.20, moneyness_pct: 3.5 }),  // cheap OTM low delta
    ]
    const result = filterByOpportunityFilters(evals, {
      premiumMax: 2.0,
      premiumMin: null,
      deltaMax: 0.30,
      deltaMin: null,
      moneyness: 'otm',
    })
    expect(result).toHaveLength(2) // first and third
  })
})

// ===========================================================================
// sortByConviction
// ===========================================================================
describe('sortByConviction', () => {
  it('sorts descending by conviction score', () => {
    const evals = [
      makeEval({ convictionScore: 50 }),
      makeEval({ convictionScore: 90 }),
      makeEval({ convictionScore: 70 }),
    ]
    const sorted = sortByConviction(evals)
    expect(sorted[0].convictionScore).toBe(90)
    expect(sorted[2].convictionScore).toBe(50)
  })
})

// ===========================================================================
// filterByConvictionThreshold
// ===========================================================================
describe('filterByConvictionThreshold', () => {
  it('filters by default threshold (65)', () => {
    const evals = [
      makeEval({ convictionScore: 80 }),
      makeEval({ convictionScore: 60 }),
      makeEval({ convictionScore: 65 }),
    ]
    const filtered = filterByConvictionThreshold(evals)
    expect(filtered).toHaveLength(2)
    expect(filtered.map(e => e.convictionScore)).toEqual([80, 65])
  })
})

// ===========================================================================
// getConvictionColorClass
// ===========================================================================
describe('getConvictionColorClass', () => {
  it('returns conviction-high for >= 85', () => {
    expect(getConvictionColorClass(85)).toBe('conviction-high')
  })

  it('returns conviction-medium for >= 70 and < 85', () => {
    expect(getConvictionColorClass(70)).toBe('conviction-medium')
    expect(getConvictionColorClass(84.9)).toBe('conviction-medium')
  })

  it('returns conviction-low for < 70', () => {
    expect(getConvictionColorClass(69.9)).toBe('conviction-low')
  })
})

// ===========================================================================
// determineUrgency
// ===========================================================================
describe('determineUrgency', () => {
  it('returns act_now for BREAKOUT', () => {
    expect(determineUrgency(['BREAKOUT'])).toBe('act_now')
  })

  it('returns hours for UNUSUAL_VOLUME without mid', () => {
    expect(determineUrgency(['UNUSUAL_VOLUME'])).toBe('hours')
  })

  it('returns act_now for cheap UNUSUAL_VOLUME', () => {
    expect(determineUrgency(['UNUSUAL_VOLUME'], 1.0)).toBe('act_now')
    expect(determineUrgency(['UNUSUAL_VOLUME'], CHEAP_UV_PREMIUM_THRESHOLD)).toBe('act_now')
  })

  it('returns hours for expensive UNUSUAL_VOLUME', () => {
    expect(determineUrgency(['UNUSUAL_VOLUME'], 5.0)).toBe('hours')
  })

  it('returns patient for other scanners', () => {
    expect(determineUrgency(['COMPRESSION_EXPANSION'])).toBe('patient')
    expect(determineUrgency([])).toBe('patient')
  })
})

// ===========================================================================
// formatContractId
// ===========================================================================
describe('formatContractId', () => {
  it('formats CALL contract', () => {
    const result = formatContractId('AAPL', 200, 'CALL', '2025-03-21')
    expect(result).toMatch(/^AAPL 200C \d{1,2}\/\d{1,2}$/)
  })

  it('formats PUT contract', () => {
    const result = formatContractId('TSLA', 150.5, 'PUT', '2025-06-20')
    expect(result).toMatch(/^TSLA 150\.5P \d{1,2}\/\d{1,2}$/)
  })
})
