import { describe, it, expect } from 'vitest'
import {
  PILLAR_KEYS_LEGACY,
  PILLAR_KEYS_V4,
  PILLAR_META,
  activePillarKeys,
  compositeFormulaDescription,
  isV4PillarConfig,
  pillarIdFromKey,
  pillarMeta,
  reasonCodeLabel,
} from '../lib/pillarMeta'
import type { PillarConfig, PillarConfigV2 } from '../lib/types'

const dummyConfig: PillarConfigV2 = {
  pillar_id: 'PREMIUM_LEVERAGE',
  display_name: 'Dummy',
  description: '',
  numeric_subscores: [],
  categorical_subscores: [],
}

describe('pillarMeta', () => {
  it('returns legacy metadata for v3 pillar IDs', () => {
    const meta = pillarMeta('PREMIUM_LEVERAGE')
    expect(meta.label).toBe('Premium Leverage')
    expect(meta.legacy).toBe(true)
    expect(meta.pillarKey).toBe('premium_leverage')
  })

  it('returns Sharpshooter metadata for v4 pillar IDs', () => {
    const meta = pillarMeta('DIRECTIONAL_CONVICTION')
    expect(meta.label).toBe('Directional Conviction')
    expect(meta.legacy).toBe(false)
    expect(meta.defaultWeight).toBe(0.40)
  })

  it('falls back gracefully for unknown IDs', () => {
    const meta = pillarMeta('UNKNOWN_NEW_PILLAR' as never)
    expect(meta.label).toBe('UNKNOWN NEW PILLAR')
    expect(meta.icon).toBeDefined()
  })

  it('handles null/undefined IDs without throwing', () => {
    expect(pillarMeta(null).label).toBe('Pillar')
    expect(pillarMeta(undefined).label).toBe('Pillar')
  })

  it('covers all six pillar IDs in PILLAR_META', () => {
    expect(Object.keys(PILLAR_META).length).toBe(6)
    for (const key of PILLAR_KEYS_LEGACY) {
      expect(PILLAR_META[pillarIdFromKey(key)].legacy).toBe(true)
    }
    for (const key of PILLAR_KEYS_V4) {
      expect(PILLAR_META[pillarIdFromKey(key)].legacy).toBe(false)
    }
  })
})

describe('pillarIdFromKey', () => {
  it('maps legacy snake_case keys to v3 PillarId values', () => {
    expect(pillarIdFromKey('premium_leverage')).toBe('PREMIUM_LEVERAGE')
    expect(pillarIdFromKey('underlying_behavior')).toBe('UNDERLYING_BEHAVIOR')
    expect(pillarIdFromKey('setup_quality')).toBe('SETUP_QUALITY')
  })

  it('maps v4 snake_case keys to v4 PillarId values', () => {
    expect(pillarIdFromKey('directional_conviction')).toBe('DIRECTIONAL_CONVICTION')
    expect(pillarIdFromKey('move_potential')).toBe('MOVE_POTENTIAL')
    expect(pillarIdFromKey('trade_structure')).toBe('TRADE_STRUCTURE')
  })
})

describe('isV4PillarConfig / activePillarKeys', () => {
  it('detects v4 via composite_formula', () => {
    const config: PillarConfig = {
      weights: { directional_conviction: 0.4, move_potential: 0.35, trade_structure: 0.25 },
      composite_formula: 'weighted_geometric_mean',
    }
    expect(isV4PillarConfig(config)).toBe(true)
    expect(activePillarKeys(config)).toEqual([...PILLAR_KEYS_V4])
  })

  it('detects v4 via populated v4 pillar slots', () => {
    const config: PillarConfig = {
      weights: {},
      directional_conviction: { ...dummyConfig, pillar_id: 'DIRECTIONAL_CONVICTION' },
    }
    expect(isV4PillarConfig(config)).toBe(true)
  })

  it('returns legacy keys for v3 configs', () => {
    const config: PillarConfig = {
      weights: { premium_leverage: 0.375, underlying_behavior: 0.455, setup_quality: 0.17 },
      composite_formula: 'weighted_sum',
      premium_leverage: dummyConfig,
      underlying_behavior: { ...dummyConfig, pillar_id: 'UNDERLYING_BEHAVIOR' },
      setup_quality: { ...dummyConfig, pillar_id: 'SETUP_QUALITY' },
    }
    expect(isV4PillarConfig(config)).toBe(false)
    expect(activePillarKeys(config)).toEqual([...PILLAR_KEYS_LEGACY])
  })

  it('defaults to legacy for null config', () => {
    expect(isV4PillarConfig(null)).toBe(false)
    expect(activePillarKeys(null)).toEqual([...PILLAR_KEYS_LEGACY])
  })
})

describe('compositeFormulaDescription', () => {
  it('describes the geometric mean formula', () => {
    expect(compositeFormulaDescription('weighted_geometric_mean')).toMatch(/geometric/i)
  })

  it('describes the arithmetic sum formula', () => {
    expect(compositeFormulaDescription('weighted_sum')).toMatch(/arithmetic/i)
  })

  it('defaults to arithmetic for null/undefined', () => {
    expect(compositeFormulaDescription(null)).toMatch(/arithmetic/i)
    expect(compositeFormulaDescription(undefined)).toMatch(/arithmetic/i)
  })
})

describe('reasonCodeLabel', () => {
  it('renders v4 Sharpshooter tier label', () => {
    expect(reasonCodeLabel('SHARPSHOOTER_SETUP')).toBe('Sharpshooter Setup')
  })

  it('renders v4 per-pillar strength labels', () => {
    expect(reasonCodeLabel('STRONG_DIRECTIONAL_CONVICTION')).toBe('Strong Directional Conviction')
    expect(reasonCodeLabel('POOR_MOVE_POTENTIAL')).toBe('Poor Move Potential')
  })

  it('renders v4 insufficient-data labels', () => {
    expect(reasonCodeLabel('INSUFFICIENT_DATA_TRADE_STRUCTURE')).toBe(
      'Insufficient data: Trade Structure',
    )
  })

  it('preserves legacy v3 reason codes', () => {
    expect(reasonCodeLabel('STRONG_PREMIUM_LEVERAGE')).toBe('Strong Premium Leverage')
  })

  it('falls back to snake_case split for unknown codes', () => {
    expect(reasonCodeLabel('SOME_NEW_CODE')).toBe('SOME NEW CODE')
  })
})
