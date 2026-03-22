import { describe, it, expect } from 'vitest'
import {
  normalizeEV,
  calculateCompositePillar,
  getConvergenceBonus,
  getTimeSensitivityBoost,
  calculateSetupRuleScore,
  calculateConvictionScore,
  enhanceWithConvictionScores,
  sortByConviction,
  sortByComposite,
  calculateCompositeScore,
  filterByConvictionThreshold,
  getConvictionColorClass,
  getConvictionColor,
  formatContractId,
  determineUrgency,
  DEFAULT_WEIGHTS,
  DEFAULT_EV_BENCHMARK,
} from '../lib/convictionScore'
import type { ApproveEvaluation, MatchedRule, ScannerType, UrgencyLevel } from '../lib/types'

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
// calculateSetupRuleScore
// ===========================================================================
describe('calculateSetupRuleScore', () => {
  function makeRule(overrides: Partial<MatchedRule> = {}): MatchedRule {
    return {
      rule_id: 'rule-001',
      name: 'Test Rule',
      mode: 'production',
      performance_at_creation: {
        win_rate: 0.7,
        avg_return: 15,
        median_return: 12,
        sample_size: 25,
        avg_days_held: 5,
      },
      ...overrides,
    }
  }

  it('returns 0 for undefined matchedRules', () => {
    expect(calculateSetupRuleScore(undefined)).toBe(0)
  })

  it('returns 0 for empty matchedRules array', () => {
    expect(calculateSetupRuleScore([])).toBe(0)
  })

  it('scores a single rule with high win rate and high sample size', () => {
    const rules = [makeRule({
      performance_at_creation: {
        win_rate: 0.8, avg_return: 20, median_return: 18,
        sample_size: 30, avg_days_held: 5,
      },
    })]
    // quality = min(1, 30/20) = 1.0, score = 0.8 * 100 * 1.0 = 80
    expect(calculateSetupRuleScore(rules)).toBe(80)
  })

  it('reduces score for low sample size (quality ramp)', () => {
    const rules = [makeRule({
      performance_at_creation: {
        win_rate: 0.8, avg_return: 20, median_return: 18,
        sample_size: 5, avg_days_held: 5,
      },
    })]
    // quality = 5/20 = 0.25, score = 0.8 * 100 * 0.25 = 20
    expect(calculateSetupRuleScore(rules)).toBe(20)
  })

  it('sums top 3 rules and caps at 100', () => {
    const rules = [
      makeRule({ rule_id: 'r1', performance_at_creation: { win_rate: 0.6, avg_return: 10, median_return: 8, sample_size: 20, avg_days_held: 5 } }),
      makeRule({ rule_id: 'r2', performance_at_creation: { win_rate: 0.7, avg_return: 15, median_return: 12, sample_size: 20, avg_days_held: 5 } }),
      makeRule({ rule_id: 'r3', performance_at_creation: { win_rate: 0.5, avg_return: 5, median_return: 3, sample_size: 20, avg_days_held: 5 } }),
    ]
    // r1=60, r2=70, r3=50 → top 3 sum = 180, capped at 100
    expect(calculateSetupRuleScore(rules)).toBe(100)
  })

  it('only uses top 3 when more than 3 rules match', () => {
    const rules = [
      makeRule({ rule_id: 'r1', performance_at_creation: { win_rate: 0.3, avg_return: 5, median_return: 3, sample_size: 20, avg_days_held: 5 } }),
      makeRule({ rule_id: 'r2', performance_at_creation: { win_rate: 0.2, avg_return: 3, median_return: 2, sample_size: 20, avg_days_held: 5 } }),
      makeRule({ rule_id: 'r3', performance_at_creation: { win_rate: 0.25, avg_return: 4, median_return: 3, sample_size: 20, avg_days_held: 5 } }),
      makeRule({ rule_id: 'r4', performance_at_creation: { win_rate: 0.1, avg_return: 1, median_return: 0, sample_size: 20, avg_days_held: 5 } }),
    ]
    // r1=30, r2=20, r3=25, r4=10 → top 3 = 30+25+20 = 75
    expect(calculateSetupRuleScore(rules)).toBe(75)
  })

  it('returns 0 when performance_at_creation is null', () => {
    const rules = [makeRule({ performance_at_creation: null })]
    expect(calculateSetupRuleScore(rules)).toBe(0)
  })

  it('returns 0 when performance_at_creation is undefined', () => {
    const rules = [makeRule({ performance_at_creation: undefined })]
    expect(calculateSetupRuleScore(rules)).toBe(0)
  })

  it('handles minimum sample size (3)', () => {
    const rules = [makeRule({
      performance_at_creation: {
        win_rate: 1.0, avg_return: 50, median_return: 40,
        sample_size: 3, avg_days_held: 5,
      },
    })]
    // quality = 3/20 = 0.15, score = 1.0 * 100 * 0.15 = 15
    expect(calculateSetupRuleScore(rules)).toBe(15)
  })

  it('caps quality factor at 1.0 for large sample sizes', () => {
    const rules = [makeRule({
      performance_at_creation: {
        win_rate: 0.9, avg_return: 25, median_return: 20,
        sample_size: 100, avg_days_held: 5,
      },
    })]
    // quality = min(1.0, 100/20) = 1.0, score = 90
    expect(calculateSetupRuleScore(rules)).toBe(90)
  })
})

// ===========================================================================
// calculateConvictionScore
// ===========================================================================
describe('calculateConvictionScore', () => {
  it('calculates score with default weights', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 7.5,
      pillarScores: { DIRECTIONAL: 75, VOLATILITY: 80, STRUCTURE: 85 },
      gateMargin: 60,
      scannerConvergence: 1,
      urgency: 'act_now',
    })

    const result = calculateConvictionScore(evaluation)

    // EV: normalizeEV(7.5, 15) = 50 → weighted = 50 * 0.37 = 18.5
    // Pillar: (75 + 80 + 85) / 3 = 80 → weighted = 80 * 0.23 = 18.4
    // Margin: clamp(60, 0, 100) = 60 → weighted = 60 * 0.13 = 7.8
    // Convergence: 1 scanner = 0 → weighted = 0 * 0.09 = 0
    // Time: act_now = 100 → weighted = 100 * 0.08 = 8
    // Setup Rules: no matchedRules → 0 * 0.10 = 0
    // Total = 18.5 + 18.4 + 7.8 + 0 + 8 + 0 = 52.7
    expect(result.total).toBe(52.7)
  })

  it('handles maximum conviction case', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 1000,
      pillarScores: { DIRECTIONAL: 100, VOLATILITY: 100, STRUCTURE: 100 },
      gateMargin: 100,
      scannerConvergence: 4,
      urgency: 'act_now',
      matchedRules: [{
        rule_id: 'r1', name: 'Strong Rule', mode: 'production',
        performance_at_creation: { win_rate: 1.0, avg_return: 50, median_return: 40, sample_size: 30, avg_days_held: 5 },
      }],
    })

    const result = calculateConvictionScore(evaluation)

    // EV: 100 * 0.37 = 37
    // Pillar: 100 * 0.23 = 23
    // Margin: 100 * 0.13 = 13
    // Convergence: 100 * 0.09 = 9
    // Time: 100 * 0.08 = 8
    // Setup Rules: 100 * 0.10 = 10
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
    // Margin: null → 50 (default) → weighted 50 * 0.13 = 6.5
    // Convergence: null → 1 → 0 → weighted 0
    // Time: null → 'patient' → 0 → weighted 0
    // Setup Rules: no matchedRules → 0
    expect(result.total).toBe(6.5)
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
      compositePillar: 0,
      gateMargin: 0,
      scannerConvergence: 0,
      timeSensitivity: 0,
      setupRules: 0,
    }

    const result = calculateConvictionScore(evaluation, weights)
    expect(result.total).toBe(100)
  })

  it('supports custom EV benchmark', () => {
    const evaluation = makeEval({ thetaAdjustedEV: 100 })

    const result100 = calculateConvictionScore(evaluation, DEFAULT_WEIGHTS, 100)
    const result1000 = calculateConvictionScore(evaluation, DEFAULT_WEIGHTS, 1000)

    // With benchmark 100: normalizeEV(100, 100) = 100 → weighted = 40
    // With benchmark 1000: normalizeEV(100, 1000) = 10 → weighted = 4
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
    expect(result.components).toHaveProperty('compositePillar')
    expect(result.components).toHaveProperty('gateMargin')
    expect(result.components).toHaveProperty('scannerConvergence')
    expect(result.components).toHaveProperty('timeSensitivity')
    expect(result.components).toHaveProperty('setupRules')

    // Each component should have raw, normalized, weighted
    for (const comp of Object.values(result.components)) {
      expect(comp).toHaveProperty('raw')
      expect(comp).toHaveProperty('normalized')
      expect(comp).toHaveProperty('weighted')
    }
  })

  it('includes setup rules in total score', () => {
    const evaluation = makeEval({
      thetaAdjustedEV: 15,  // normalized = 100
      pillarScores: { DIRECTIONAL: 100, VOLATILITY: 100, STRUCTURE: 100 },
      gateMargin: 100,
      scannerConvergence: 4,  // bonus = 100
      urgency: 'act_now',     // boost = 100
      matchedRules: [{
        rule_id: 'r1', name: 'Strong Pattern', mode: 'production' as const,
        performance_at_creation: { win_rate: 0.8, avg_return: 20, median_return: 18, sample_size: 40, avg_days_held: 5 },
      }],
    })
    const result = calculateConvictionScore(evaluation)
    // Setup rules: 0.8 * 100 * 1.0 = 80 → weighted = 80 * 0.10 = 8
    // All other components at 100 → 37 + 23 + 13 + 9 + 8 = 90
    // Total = 90 + 8 = 98
    expect(result.total).toBe(98)
    expect(result.components.setupRules.raw).toBe(80)
    expect(result.components.setupRules.normalized).toBe(80)
    expect(result.components.setupRules.weighted).toBe(8)
  })

  it('scores 0 for setup rules when no rules match', () => {
    const evaluation = makeEval({ matchedRules: undefined })
    const result = calculateConvictionScore(evaluation)
    expect(result.components.setupRules.raw).toBe(0)
    expect(result.components.setupRules.weighted).toBe(0)
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
    const weights = { ...DEFAULT_WEIGHTS, thetaAdjustedEv: 1.0, compositePillar: 0, gateMargin: 0, scannerConvergence: 0, timeSensitivity: 0, setupRules: 0 }

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

  it('returns hours for UNUSUAL_VOLUME without breakout', () => {
    expect(determineUrgency(['UNUSUAL_VOLUME'])).toBe('hours')
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
// calculateCompositeScore
// ===========================================================================
describe('calculateCompositeScore', () => {
  // Helper to compute expected composite: conviction * (1 + returnPct / 100)
  // where returnPct = (thetaAdjEV / (mid * 100)) * 100 = thetaAdjEV / mid
  function expected(conviction: number, mid: number, ev: number): number {
    const returnPct = (ev / (mid * 100)) * 100
    return Math.round(conviction * (1 + returnPct / 100) * 10) / 10
  }

  it('computes correct composite for the 7-row reference table', () => {
    const rows: { ticker: string; conviction: number; mid: number; ev: number }[] = [
      { ticker: 'ENPH', conviction: 81, mid: 3.30, ev: 34 },
      { ticker: 'INTC', conviction: 81, mid: 2.70, ev: 25 },
      { ticker: 'CF',   conviction: 80, mid: 2.33, ev: 21 },
      { ticker: 'INTC', conviction: 81, mid: 2.49, ev: 18 },
      { ticker: 'HAS',  conviction: 82, mid: 3.30, ev: 16 },
      { ticker: 'PWR',  conviction: 80, mid: 30.70, ev: 230 },
      { ticker: 'INTC', conviction: 82, mid: 5.00, ev: 17 },
    ]

    const results = rows.map(r => {
      const evaluation = makeEval({
        underlying_ticker: r.ticker,
        convictionScore: r.conviction,
        mid: r.mid,
        thetaAdjustedEV: r.ev,
      })
      return calculateCompositeScore(evaluation)
    })

    // Verify each row matches the formula
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i]
      expect(results[i]).toBe(expected(r.conviction, r.mid, r.ev))
    }

    // Verify descending order (composite ranking)
    for (let i = 1; i < results.length; i++) {
      expect(results[i - 1]).toBeGreaterThanOrEqual(results[i])
    }
  })

  it('falls back to conviction when premium is null', () => {
    const evaluation = makeEval({ convictionScore: 80, mid: undefined as unknown as number, thetaAdjustedEV: 20 })
    expect(calculateCompositeScore(evaluation)).toBe(80)
  })

  it('falls back to conviction when premium is zero', () => {
    const evaluation = makeEval({ convictionScore: 80, mid: 0, thetaAdjustedEV: 20 })
    expect(calculateCompositeScore(evaluation)).toBe(80)
  })

  it('falls back to conviction when thetaAdjustedEV is null', () => {
    const evaluation = makeEval({ convictionScore: 80, mid: 5, thetaAdjustedEV: undefined as unknown as number })
    expect(calculateCompositeScore(evaluation)).toBe(80)
  })

  it('produces composite < conviction when EV is negative', () => {
    const evaluation = makeEval({ convictionScore: 80, mid: 5.0, thetaAdjustedEV: -10 })
    expect(calculateCompositeScore(evaluation)).toBeLessThan(80)
  })

  it('uses custom alpha parameter', () => {
    const evaluation = makeEval({ convictionScore: 80, mid: 2.0, thetaAdjustedEV: 20 })
    const alpha0 = calculateCompositeScore(evaluation, 0)
    const alpha2 = calculateCompositeScore(evaluation, 2)
    expect(alpha0).toBe(80) // alpha=0 means no return% boost
    expect(alpha2).toBeGreaterThan(calculateCompositeScore(evaluation, 1))
  })
})

// ===========================================================================
// sortByComposite
// ===========================================================================
describe('sortByComposite', () => {
  it('sorts by composite score descending', () => {
    const evals = [
      makeEval({ evaluation_id: 'low', convictionScore: 80, mid: 5.0, thetaAdjustedEV: 5 }),
      makeEval({ evaluation_id: 'high', convictionScore: 80, mid: 2.0, thetaAdjustedEV: 20 }),
    ]
    const sorted = sortByComposite(evals)
    expect(sorted[0].evaluation_id).toBe('high')
    expect(sorted[1].evaluation_id).toBe('low')
  })

  it('breaks ties by conviction descending', () => {
    // Same composite but different conviction (arrange mid/EV to match composite)
    const evals = [
      makeEval({ evaluation_id: 'low-conv', convictionScore: 75, mid: 3.0, thetaAdjustedEV: 0 }),
      makeEval({ evaluation_id: 'high-conv', convictionScore: 80, mid: 3.0, thetaAdjustedEV: 0 }),
    ]
    const sorted = sortByComposite(evals)
    expect(sorted[0].evaluation_id).toBe('high-conv')
  })

  it('breaks conviction ties by thetaAdjustedEV descending', () => {
    const evals = [
      makeEval({ evaluation_id: 'low-ev', convictionScore: 80, mid: undefined as unknown as number, thetaAdjustedEV: 10 }),
      makeEval({ evaluation_id: 'high-ev', convictionScore: 80, mid: undefined as unknown as number, thetaAdjustedEV: 20 }),
    ]
    const sorted = sortByComposite(evals)
    expect(sorted[0].evaluation_id).toBe('high-ev')
  })

  it('does not mutate original array', () => {
    const evals = [
      makeEval({ evaluation_id: 'a', convictionScore: 70 }),
      makeEval({ evaluation_id: 'b', convictionScore: 90 }),
    ]
    sortByComposite(evals)
    expect(evals[0].evaluation_id).toBe('a')
  })

  it('handles empty array', () => {
    expect(sortByComposite([])).toEqual([])
  })
})

// ===========================================================================
// DEFAULT_WEIGHTS contract
// ===========================================================================
describe('DEFAULT_WEIGHTS', () => {
  it('sum to 1.0', () => {
    const sum = DEFAULT_WEIGHTS.thetaAdjustedEv
      + DEFAULT_WEIGHTS.compositePillar
      + DEFAULT_WEIGHTS.gateMargin
      + DEFAULT_WEIGHTS.scannerConvergence
      + DEFAULT_WEIGHTS.timeSensitivity
      + DEFAULT_WEIGHTS.setupRules
    expect(sum).toBeCloseTo(1.0)
  })

  it('has expected values per spec', () => {
    expect(DEFAULT_WEIGHTS.thetaAdjustedEv).toBe(0.37)
    expect(DEFAULT_WEIGHTS.compositePillar).toBe(0.23)
    expect(DEFAULT_WEIGHTS.gateMargin).toBe(0.13)
    expect(DEFAULT_WEIGHTS.scannerConvergence).toBe(0.09)
    expect(DEFAULT_WEIGHTS.timeSensitivity).toBe(0.08)
    expect(DEFAULT_WEIGHTS.setupRules).toBe(0.10)
  })
})

describe('DEFAULT_EV_BENCHMARK', () => {
  it('is 15', () => {
    expect(DEFAULT_EV_BENCHMARK).toBe(15)
  })
})
