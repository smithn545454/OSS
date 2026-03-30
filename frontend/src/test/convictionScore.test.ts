import { describe, it, expect } from 'vitest'
import {
  normalizeEV,
  normalizeReturnPct,
  calculateCompositePillar,
  calculateConvictionScore,
  enhanceWithConvictionScores,
  sortByConviction,
  filterByConvictionThreshold,
  getConvictionColorClass,
  getConvictionColor,
  formatContractId,
  determineUrgency,
  DEFAULT_WEIGHTS,
  DEFAULT_EV_BENCHMARK,
  DEFAULT_RETURN_PCT_BENCHMARK,
  CHEAP_UV_PREMIUM_THRESHOLD,
} from '../lib/convictionScore'
import type { ApproveEvaluation, ScannerType, UrgencyLevel } from '../lib/types'

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
// normalizeEV
// ===========================================================================
describe('normalizeEV', () => {
  it('returns 0 for negative EV', () => {
    expect(normalizeEV(-100)).toBe(0)
  })

  it('returns 0 for zero EV', () => {
    expect(normalizeEV(0)).toBe(0)
  })

  it('linearly scales positive EV up to benchmark', () => {
    expect(normalizeEV(7.5)).toBe(50) // 7.5/15 * 100
  })

  it('caps at 100 when EV exceeds benchmark', () => {
    expect(normalizeEV(30)).toBe(100)
  })
})

// ===========================================================================
// normalizeReturnPct
// ===========================================================================
describe('normalizeReturnPct', () => {
  it('returns 0 for zero mid', () => {
    expect(normalizeReturnPct(10, 0)).toBe(0)
  })

  it('returns 0 for negative EV', () => {
    expect(normalizeReturnPct(-5, 1)).toBe(0)
  })

  it('returns 100 for 20% return at default benchmark', () => {
    expect(normalizeReturnPct(20, 1)).toBe(100)
  })

  it('caps at 100 for high return', () => {
    expect(normalizeReturnPct(40, 1)).toBe(100)
  })
})

// ===========================================================================
// calculateCompositePillar
// ===========================================================================
describe('calculateCompositePillar', () => {
  it('averages all three pillars', () => {
    expect(calculateCompositePillar({
      DIRECTIONAL: 60, VOLATILITY: 80, STRUCTURE: 100,
    })).toBe(80)
  })

  it('returns 0 when all pillars missing', () => {
    expect(calculateCompositePillar({})).toBe(0)
  })
})

// ===========================================================================
// calculateConvictionScore (3-component)
// ===========================================================================
describe('calculateConvictionScore', () => {
  it('calculates with 3 components only', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 15, // normalized = 100
      mid: 1.0,            // return% = 15% → normalized 75
      pillarScores: { DIRECTIONAL: 80, VOLATILITY: 80, STRUCTURE: 80 }, // avg = 80
    })
    const result = calculateConvictionScore(evaluation)

    // EV: 100 * 0.35 = 35
    // Return%: 75 * 0.30 = 22.5
    // Pillar: 80 * 0.35 = 28
    // Total: 85.5
    expect(result.total).toBe(85.5)
  })

  it('only has 3 components in breakdown', () => {
    const result = calculateConvictionScore(makeEval())
    expect(Object.keys(result.components)).toEqual([
      'thetaAdjustedEv', 'returnPct', 'compositePillar'
    ])
  })

  it('gate margin does not affect score', () => {
    const evalA = makeEval({ gateMargin: 10 })
    const evalB = makeEval({ gateMargin: 90 })
    const a = calculateConvictionScore(evalA)
    const b = calculateConvictionScore(evalB)
    expect(a.total).toBe(b.total)
  })

  it('scanner convergence does not affect score', () => {
    const evalA = makeEval({ scannerConvergence: 1 })
    const evalB = makeEval({ scannerConvergence: 4 })
    const a = calculateConvictionScore(evalA)
    const b = calculateConvictionScore(evalB)
    expect(a.total).toBe(b.total)
  })

  it('handles null inputs safely', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: undefined as unknown as number,
      pillarScores: undefined as unknown as Record<string, number>,
      mid: undefined as unknown as number,
    })
    const result = calculateConvictionScore(evaluation)
    expect(result.total).toBe(0)
  })

  it('max score is 100 with all components maxed', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 100, // EV capped at 100
      mid: 0.01,            // Huge return% capped at 100
      pillarScores: { DIRECTIONAL: 100, VOLATILITY: 100, STRUCTURE: 100 },
    })
    const result = calculateConvictionScore(evaluation)
    expect(result.total).toBe(100)
  })

  it('supports custom weights', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 15,
      pillarScores: { DIRECTIONAL: 0, VOLATILITY: 0, STRUCTURE: 0 },
      mid: 0,
    })
    const weights = { thetaAdjustedEv: 1.0, returnPct: 0, compositePillar: 0 }
    const result = calculateConvictionScore(evaluation, weights)
    expect(result.total).toBe(100)
  })
})

// ===========================================================================
// enhanceWithConvictionScores
// ===========================================================================
describe('enhanceWithConvictionScores', () => {
  it('adds convictionScore and convictionBreakdown', () => {
    // Use a recent evaluated_at so decay doesn't alter the score
    const recentEval = makeEval({ evaluated_at: new Date().toISOString() })
    const enhanced = enhanceWithConvictionScores([recentEval])
    expect(enhanced[0].convictionScore).toBeDefined()
    expect(enhanced[0].convictionBreakdown).toBeDefined()
    // Fresh eval (within grace period) has no decay
    expect(enhanced[0].convictionBreakdown!.total).toBe(enhanced[0].convictionScore)
  })

  it('does not mutate original evaluations', () => {
    const original = makeEval()
    enhanceWithConvictionScores([original])
    expect(original.convictionScore).toBeUndefined()
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
  it('filters by default threshold (70)', () => {
    const evals = [
      makeEval({ convictionScore: 80 }),
      makeEval({ convictionScore: 65 }),
      makeEval({ convictionScore: 70 }),
    ]
    const filtered = filterByConvictionThreshold(evals)
    expect(filtered).toHaveLength(2)
    expect(filtered.map(e => e.convictionScore)).toEqual([80, 70])
  })
})

// ===========================================================================
// getConvictionColorClass — thresholds adjusted to 70/85
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
// DEFAULT_WEIGHTS contract
// ===========================================================================
describe('DEFAULT_WEIGHTS', () => {
  it('sum to 1.0', () => {
    const sum = DEFAULT_WEIGHTS.thetaAdjustedEv
      + DEFAULT_WEIGHTS.returnPct
      + DEFAULT_WEIGHTS.compositePillar
    expect(sum).toBeCloseTo(1.0)
  })

  it('has expected values', () => {
    expect(DEFAULT_WEIGHTS.thetaAdjustedEv).toBe(0.35)
    expect(DEFAULT_WEIGHTS.returnPct).toBe(0.30)
    expect(DEFAULT_WEIGHTS.compositePillar).toBe(0.35)
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
