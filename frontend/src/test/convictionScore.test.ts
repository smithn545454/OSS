import { describe, it, expect } from 'vitest'
import {
  normalizeEV,
  normalizeReturnPct,
  calculateCompositePillar,
  getConvergenceBonus,
  getTimeSensitivityBoost,
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
    expect(normalizeEV(250, 500)).toBe(50)
    expect(normalizeEV(500, 500)).toBe(100)
  })

  it('caps at 100 when EV exceeds benchmark', () => {
    expect(normalizeEV(1000, 500)).toBe(100)
  })

  it('uses DEFAULT_EV_BENCHMARK when no benchmark supplied', () => {
    // DEFAULT_EV_BENCHMARK = 15
    expect(normalizeEV(7.5)).toBe(50)
  })

  it('handles very small positive EV', () => {
    expect(normalizeEV(1, 500)).toBeCloseTo(0.2)
  })
})

// ===========================================================================
// normalizeReturnPct
// ===========================================================================
describe('normalizeReturnPct', () => {
  it('returns 0 for zero mid', () => {
    expect(normalizeReturnPct(10, 0)).toBe(0)
  })

  it('returns 0 for negative mid', () => {
    expect(normalizeReturnPct(10, -1)).toBe(0)
  })

  it('returns 0 for zero EV', () => {
    expect(normalizeReturnPct(0, 1)).toBe(0)
  })

  it('returns 0 for negative EV', () => {
    expect(normalizeReturnPct(-5, 1)).toBe(0)
  })

  it('returns 100 for 20% return at default benchmark', () => {
    // $20 EV on $1.00 option = 20% return → normalized 100
    expect(normalizeReturnPct(20, 1)).toBe(100)
  })

  it('returns 50 for 10% return at default benchmark', () => {
    // $5 EV on $0.50 option = 10% return → normalized 50
    expect(normalizeReturnPct(5, 0.5)).toBe(50)
  })

  it('caps at 100 for high return', () => {
    expect(normalizeReturnPct(40, 1)).toBe(100)
  })

  it('uses custom benchmark', () => {
    // $10 EV on $1 option = 10% return / 10% benchmark = 100
    expect(normalizeReturnPct(10, 1, 10)).toBe(100)
  })

  it('uses DEFAULT_RETURN_PCT_BENCHMARK by default', () => {
    expect(DEFAULT_RETURN_PCT_BENCHMARK).toBe(20)
  })
})

// ===========================================================================
// calculateCompositePillar
// ===========================================================================
describe('calculateCompositePillar', () => {
  it('averages all three pillars', () => {
    expect(calculateCompositePillar({
      DIRECTIONAL: 60,
      VOLATILITY: 80,
      STRUCTURE: 100,
    })).toBe(80)
  })

  it('returns 0 when all pillars missing', () => {
    expect(calculateCompositePillar({})).toBe(0)
  })

  it('treats missing pillars as 0', () => {
    expect(calculateCompositePillar({ DIRECTIONAL: 90 })).toBe(30)
  })

  it('handles all-zero pillars', () => {
    expect(calculateCompositePillar({
      DIRECTIONAL: 0,
      VOLATILITY: 0,
      STRUCTURE: 0,
    })).toBe(0)
  })

  it('handles max pillars', () => {
    expect(calculateCompositePillar({
      DIRECTIONAL: 100,
      VOLATILITY: 100,
      STRUCTURE: 100,
    })).toBe(100)
  })
})

// ===========================================================================
// getConvergenceBonus
// ===========================================================================
describe('getConvergenceBonus', () => {
  it('returns 0 for 1 scanner', () => {
    expect(getConvergenceBonus(1)).toBe(0)
  })

  it('returns 50 for 2 scanners', () => {
    expect(getConvergenceBonus(2)).toBe(50)
  })

  it('returns 75 for 3 scanners', () => {
    expect(getConvergenceBonus(3)).toBe(75)
  })

  it('returns 100 for 4 scanners', () => {
    expect(getConvergenceBonus(4)).toBe(100)
  })

  it('returns 100 for more than 4 scanners', () => {
    expect(getConvergenceBonus(5)).toBe(100)
    expect(getConvergenceBonus(10)).toBe(100)
  })

  it('returns 0 for 0 scanners (fallback)', () => {
    expect(getConvergenceBonus(0)).toBe(0)
  })
})

// ===========================================================================
// getTimeSensitivityBoost
// ===========================================================================
describe('getTimeSensitivityBoost', () => {
  it('returns 100 for act_now', () => {
    expect(getTimeSensitivityBoost('act_now')).toBe(100)
  })

  it('returns 50 for hours', () => {
    expect(getTimeSensitivityBoost('hours')).toBe(50)
  })

  it('returns 0 for patient', () => {
    expect(getTimeSensitivityBoost('patient')).toBe(0)
  })

  it('returns 0 for unknown urgency', () => {
    expect(getTimeSensitivityBoost('unknown' as UrgencyLevel)).toBe(0)
  })
})

// ===========================================================================
// calculateConvictionScore
// ===========================================================================
describe('calculateConvictionScore', () => {
  it('calculates score with default weights', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 7.5,
      mid: 2.0,
      pillarScores: { DIRECTIONAL: 75, VOLATILITY: 80, STRUCTURE: 85 },
      gateMargin: 60,
      scannerConvergence: 1,
      urgency: 'act_now',
    })

    const result = calculateConvictionScore(evaluation)

    // EV: normalizeEV(7.5, 15) = 50 → weighted = 50 * 0.25 = 12.5
    // ReturnPct: (7.5/(2*100))*100 = 3.75% → 3.75/20*100 = 18.75 → 18.75 * 0.15 = 2.8125 → 2.8
    // Pillar: (75 + 80 + 85) / 3 = 80 → weighted = 80 * 0.25 = 20
    // Margin: clamp(60, 0, 100) = 60 → weighted = 60 * 0.15 = 9
    // Convergence: 1 scanner = 0 → weighted = 0 * 0.10 = 0
    // Time: act_now = 100 → weighted = 100 * 0.10 = 10
    // Total = 12.5 + 2.8 + 20 + 9 + 0 + 10 = 54.3
    expect(result.total).toBe(54.3)
  })

  it('handles maximum conviction case', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 1000,
      mid: 1.0,
      pillarScores: { DIRECTIONAL: 100, VOLATILITY: 100, STRUCTURE: 100 },
      gateMargin: 100,
      scannerConvergence: 4,
      urgency: 'act_now',
    })

    const result = calculateConvictionScore(evaluation)

    // EV: capped 100 * 0.25 = 25
    // ReturnPct: capped 100 * 0.15 = 15
    // Pillar: 100 * 0.25 = 25
    // Margin: 100 * 0.15 = 15
    // Convergence: 100 * 0.10 = 10
    // Time: 100 * 0.10 = 10
    // Total = 100
    expect(result.total).toBe(100)
  })

  it('handles all-zero / null fields', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: undefined as unknown as number,
      pillarScores: undefined as unknown as Record<string, number>,
      gateMargin: undefined as unknown as number,
      scannerConvergence: undefined as unknown as number,
      urgency: undefined as unknown as UrgencyLevel,
    })

    const result = calculateConvictionScore(evaluation)

    // EV: null → 0 → normalized 0 → weighted 0
    // Pillar: null → {} → 0 → weighted 0
    // Margin: null → 50 (default) → weighted 50 * 0.15 = 7.5
    // Convergence: null → 1 → 0 → weighted 0
    // Time: null → 'patient' → 0 → weighted 0
    expect(result.total).toBe(7.5)
  })

  it('clamps gate margin to 0-100', () => {
    const evalNeg = makeEval({ gateMargin: -20 })
    const evalHigh = makeEval({ gateMargin: 200 })

    const resultNeg = calculateConvictionScore(evalNeg)
    const resultHigh = calculateConvictionScore(evalHigh)

    expect(resultNeg.components.gateMargin.normalized).toBe(0)
    expect(resultHigh.components.gateMargin.normalized).toBe(100)
  })

  it('supports custom weights', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 15,    // normalized = 100 (matches benchmark)
      pillarScores: { DIRECTIONAL: 0, VOLATILITY: 0, STRUCTURE: 0 },
      gateMargin: 0,
      scannerConvergence: 1,  // bonus = 0
      urgency: 'patient',     // boost = 0
    })

    const weights = {
      thetaAdjustedEv: 1.0,
      returnPct: 0,
      compositePillar: 0,
      gateMargin: 0,
      scannerConvergence: 0,
      timeSensitivity: 0,
    }

    const result = calculateConvictionScore(evaluation, weights)
    expect(result.total).toBe(100)
  })

  it('supports custom EV benchmark', () => {
    const evaluation = makeEval({ thetaAdjustedEV: 100 })

    const result100 = calculateConvictionScore(evaluation, DEFAULT_WEIGHTS, 100)
    const result1000 = calculateConvictionScore(evaluation, DEFAULT_WEIGHTS, 1000)

    // With benchmark 100: normalizeEV(100, 100) = 100 → weighted = 25
    // With benchmark 1000: normalizeEV(100, 1000) = 10 → weighted = 2.5
    expect(result100.components.thetaAdjustedEv.normalized).toBe(100)
    expect(result1000.components.thetaAdjustedEv.normalized).toBe(10)
  })

  it('rounds component values to 1 decimal', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 10,  // 10/15*100 = 66.67
      pillarScores: { DIRECTIONAL: 77, VOLATILITY: 88, STRUCTURE: 33 },
      gateMargin: 73,
      scannerConvergence: 2,
      urgency: 'hours',
    })

    const result = calculateConvictionScore(evaluation)

    // Check that values are rounded to 1 decimal
    expect(result.total).toBe(Math.round(result.total * 10) / 10)
    for (const comp of Object.values(result.components)) {
      expect(comp.weighted).toBe(Math.round(comp.weighted * 10) / 10)
    }
  })

  it('populates all breakdown components', () => {
    const evaluation = makeEval()
    const result = calculateConvictionScore(evaluation)

    expect(result).toHaveProperty('total')
    expect(result.components).toHaveProperty('thetaAdjustedEv')
    expect(result.components).toHaveProperty('returnPct')
    expect(result.components).toHaveProperty('compositePillar')
    expect(result.components).toHaveProperty('gateMargin')
    expect(result.components).toHaveProperty('scannerConvergence')
    expect(result.components).toHaveProperty('timeSensitivity')

    // Each component should have raw, normalized, weighted
    for (const comp of Object.values(result.components)) {
      expect(comp).toHaveProperty('raw')
      expect(comp).toHaveProperty('normalized')
      expect(comp).toHaveProperty('weighted')
    }
  })
})

// ===========================================================================
// enhanceWithConvictionScores
// ===========================================================================
describe('enhanceWithConvictionScores', () => {
  it('adds convictionScore and convictionBreakdown to each evaluation', () => {
    const evals = [makeEval(), makeEval({ evaluation_id: 'eval-002' })]
    const enhanced = enhanceWithConvictionScores(evals)

    expect(enhanced).toHaveLength(2)
    for (const e of enhanced) {
      expect(e.convictionScore).toBeDefined()
      expect(typeof e.convictionScore).toBe('number')
      expect(e.convictionBreakdown).toBeDefined()
      expect(e.convictionBreakdown!.total).toBe(e.convictionScore)
    }
  })

  it('returns empty array for empty input', () => {
    expect(enhanceWithConvictionScores([])).toEqual([])
  })

  it('does not mutate original evaluations', () => {
    const original = makeEval()
    const evals = [original]
    enhanceWithConvictionScores(evals)

    expect(original.convictionScore).toBeUndefined()
  })

  it('passes custom weights and benchmark', () => {
    const evals = [makeEval({ thetaAdjustedEV: 100 })]
    const weights = { ...DEFAULT_WEIGHTS, thetaAdjustedEv: 1.0, returnPct: 0, compositePillar: 0, gateMargin: 0, scannerConvergence: 0, timeSensitivity: 0 }

    const enhanced100 = enhanceWithConvictionScores(evals, weights, 100)
    const enhanced1000 = enhanceWithConvictionScores(evals, weights, 1000)

    expect(enhanced100[0].convictionScore).toBe(100)
    expect(enhanced1000[0].convictionScore).toBe(10)
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
    expect(sorted[1].convictionScore).toBe(70)
    expect(sorted[2].convictionScore).toBe(50)
  })

  it('treats missing conviction score as 0', () => {
    const evals = [
      makeEval({ convictionScore: 50 }),
      makeEval({ convictionScore: undefined }),
    ]

    const sorted = sortByConviction(evals)
    expect(sorted[0].convictionScore).toBe(50)
    expect(sorted[1].convictionScore).toBeUndefined()
  })

  it('does not mutate original array', () => {
    const evals = [
      makeEval({ convictionScore: 20 }),
      makeEval({ convictionScore: 80 }),
    ]
    const sorted = sortByConviction(evals)

    expect(evals[0].convictionScore).toBe(20)
    expect(sorted[0].convictionScore).toBe(80)
  })

  it('handles empty array', () => {
    expect(sortByConviction([])).toEqual([])
  })
})

// ===========================================================================
// filterByConvictionThreshold
// ===========================================================================
describe('filterByConvictionThreshold', () => {
  it('filters by default threshold (75)', () => {
    const evals = [
      makeEval({ convictionScore: 80 }),
      makeEval({ convictionScore: 70 }),
      makeEval({ convictionScore: 75 }),
    ]

    const filtered = filterByConvictionThreshold(evals)
    expect(filtered).toHaveLength(2)
    expect(filtered.map(e => e.convictionScore)).toEqual([80, 75])
  })

  it('filters by custom threshold', () => {
    const evals = [
      makeEval({ convictionScore: 90 }),
      makeEval({ convictionScore: 50 }),
    ]

    const filtered = filterByConvictionThreshold(evals, 60)
    expect(filtered).toHaveLength(1)
    expect(filtered[0].convictionScore).toBe(90)
  })

  it('treats missing conviction score as 0', () => {
    const evals = [makeEval({ convictionScore: undefined })]
    const filtered = filterByConvictionThreshold(evals, 0)
    expect(filtered).toHaveLength(1) // 0 >= 0
  })

  it('returns empty for empty input', () => {
    expect(filterByConvictionThreshold([])).toEqual([])
  })
})

// ===========================================================================
// getConvictionColorClass
// ===========================================================================
describe('getConvictionColorClass', () => {
  it('returns conviction-high for >= 85', () => {
    expect(getConvictionColorClass(85)).toBe('conviction-high')
    expect(getConvictionColorClass(100)).toBe('conviction-high')
  })

  it('returns conviction-medium for >= 75 and < 85', () => {
    expect(getConvictionColorClass(75)).toBe('conviction-medium')
    expect(getConvictionColorClass(84.9)).toBe('conviction-medium')
  })

  it('returns conviction-low for < 75', () => {
    expect(getConvictionColorClass(74.9)).toBe('conviction-low')
    expect(getConvictionColorClass(0)).toBe('conviction-low')
  })
})

// ===========================================================================
// getConvictionColor
// ===========================================================================
describe('getConvictionColor', () => {
  it('returns high color for >= 85', () => {
    expect(getConvictionColor(85)).toBe('var(--color-conviction-high)')
  })

  it('returns medium color for >= 75 and < 85', () => {
    expect(getConvictionColor(80)).toBe('var(--color-conviction-medium)')
  })

  it('returns low color for < 75', () => {
    expect(getConvictionColor(50)).toBe('var(--color-conviction-low)')
  })
})

// ===========================================================================
// formatContractId
// ===========================================================================
describe('formatContractId', () => {
  // Note: new Date('YYYY-MM-DD') parses as UTC midnight, but getMonth/getDate
  // return local-time values, so expected results depend on the runtime timezone.
  // We compute expected values the same way the function does.
  function expectedFormat(ticker: string, strike: number, optionType: 'CALL' | 'PUT', expiration: string) {
    const type = optionType === 'CALL' ? 'C' : 'P'
    const d = new Date(expiration)
    return `${ticker} ${strike}${type} ${d.getMonth() + 1}/${d.getDate()}`
  }

  it('formats CALL contract', () => {
    const result = formatContractId('AAPL', 200, 'CALL', '2025-03-21')
    expect(result).toBe(expectedFormat('AAPL', 200, 'CALL', '2025-03-21'))
    expect(result).toMatch(/^AAPL 200C \d{1,2}\/\d{1,2}$/)
  })

  it('formats PUT contract', () => {
    const result = formatContractId('TSLA', 150.5, 'PUT', '2025-06-20')
    expect(result).toBe(expectedFormat('TSLA', 150.5, 'PUT', '2025-06-20'))
    expect(result).toMatch(/^TSLA 150\.5P \d{1,2}\/\d{1,2}$/)
  })

  it('uses C for CALL and P for PUT', () => {
    const call = formatContractId('AAPL', 500, 'CALL', '2025-01-15')
    const put = formatContractId('AAPL', 500, 'PUT', '2025-01-15')
    expect(call).toMatch(/500C /)
    expect(put).toMatch(/500P /)
  })
})

// ===========================================================================
// determineUrgency
// ===========================================================================
describe('determineUrgency', () => {
  it('returns act_now for BREAKOUT', () => {
    expect(determineUrgency(['BREAKOUT'])).toBe('act_now')
  })

  it('returns act_now for BREAKDOWN', () => {
    expect(determineUrgency(['BREAKDOWN'])).toBe('act_now')
  })

  it('returns act_now when BREAKOUT is among multiple scanners', () => {
    expect(determineUrgency(['UNUSUAL_VOLUME', 'BREAKOUT'])).toBe('act_now')
  })

  it('returns hours for UNUSUAL_VOLUME without breakout (no mid)', () => {
    expect(determineUrgency(['UNUSUAL_VOLUME'])).toBe('hours')
  })

  it('returns act_now for cheap UNUSUAL_VOLUME (mid <= threshold)', () => {
    expect(determineUrgency(['UNUSUAL_VOLUME'], 1.0)).toBe('act_now')
    expect(determineUrgency(['UNUSUAL_VOLUME'], CHEAP_UV_PREMIUM_THRESHOLD)).toBe('act_now')
  })

  it('returns hours for expensive UNUSUAL_VOLUME', () => {
    expect(determineUrgency(['UNUSUAL_VOLUME'], 5.0)).toBe('hours')
  })

  it('returns patient for COMPRESSION_EXPANSION', () => {
    expect(determineUrgency(['COMPRESSION_EXPANSION'])).toBe('patient')
  })

  it('returns patient for CHEAP_OPTIONS', () => {
    expect(determineUrgency(['CHEAP_OPTIONS'])).toBe('patient')
  })

  it('returns patient for empty scanner list', () => {
    expect(determineUrgency([])).toBe('patient')
  })

  it('breakout takes priority over unusual volume', () => {
    expect(determineUrgency(['UNUSUAL_VOLUME', 'BREAKDOWN', 'CHEAP_OPTIONS'])).toBe('act_now')
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
      + DEFAULT_WEIGHTS.gateMargin
      + DEFAULT_WEIGHTS.scannerConvergence
      + DEFAULT_WEIGHTS.timeSensitivity
    expect(sum).toBeCloseTo(1.0)
  })

  it('has expected values', () => {
    expect(DEFAULT_WEIGHTS.thetaAdjustedEv).toBe(0.25)
    expect(DEFAULT_WEIGHTS.returnPct).toBe(0.15)
    expect(DEFAULT_WEIGHTS.compositePillar).toBe(0.25)
    expect(DEFAULT_WEIGHTS.gateMargin).toBe(0.15)
    expect(DEFAULT_WEIGHTS.scannerConvergence).toBe(0.10)
    expect(DEFAULT_WEIGHTS.timeSensitivity).toBe(0.10)
  })
})

describe('DEFAULT_EV_BENCHMARK', () => {
  it('is 15', () => {
    expect(DEFAULT_EV_BENCHMARK).toBe(15)
  })
})
