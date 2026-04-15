import { describe, it, expect } from 'vitest'
import {
  findZone,
  formatMetricValue,
  getScannerConfig,
  mergeScannerTriggers,
  resolveMetricValue,
  scalePosition,
  scannerLabel,
  SCANNER_CONFIGS,
  type MetricSpec,
  type MetricZone,
} from '../lib/scannerMetrics'

function spec(overrides: Partial<MetricSpec> = {}): MetricSpec {
  return {
    key: 'test',
    label: 'Test',
    format: 'number',
    ...overrides,
  }
}

const SAMPLE_ZONES: MetricZone[] = [
  { label: 'bad',  min: 1.1, max: 1.5,  tone: 'bad',     note: 'bad'     },
  { label: 'mid',  min: 0.95, max: 1.1, tone: 'neutral', note: 'mid'     },
  { label: 'good', min: 0.75, max: 0.95,tone: 'good',    note: 'good'    },
  { label: 'best', min: 0.6,  max: 0.75,tone: 'great',   note: 'great'   },
]

describe('findZone', () => {
  it('returns null for non-numeric values', () => {
    expect(findZone(null, SAMPLE_ZONES)).toBeNull()
    expect(findZone(undefined, SAMPLE_ZONES)).toBeNull()
    expect(findZone('1', SAMPLE_ZONES)).toBeNull()
    expect(findZone(NaN, SAMPLE_ZONES)).toBeNull()
  })

  it('returns null for empty or missing zones', () => {
    expect(findZone(1, undefined)).toBeNull()
    expect(findZone(1, [])).toBeNull()
  })

  it('matches a value within a zone', () => {
    expect(findZone(1.2, SAMPLE_ZONES)?.label).toBe('bad')
    expect(findZone(1.0, SAMPLE_ZONES)?.label).toBe('mid')
    expect(findZone(0.85, SAMPLE_ZONES)?.label).toBe('good')
    expect(findZone(0.7, SAMPLE_ZONES)?.label).toBe('best')
  })

  it('returns a zone for values below all zones by snapping to nearest', () => {
    const z = findZone(0.4, SAMPLE_ZONES)
    expect(z?.label).toBe('best') // closest center
  })

  it('returns a zone for values above all zones by snapping to nearest', () => {
    const z = findZone(2.0, SAMPLE_ZONES)
    expect(z?.label).toBe('bad') // closest center
  })

  it('respects inclusive boundaries', () => {
    expect(findZone(1.1, SAMPLE_ZONES)?.label).toBe('bad') // first match wins
    expect(findZone(0.95, SAMPLE_ZONES)?.label).toBe('mid')
  })
})

describe('scalePosition', () => {
  it('returns null when the spec has no scale', () => {
    expect(scalePosition(5, spec())).toBeNull()
  })

  it('returns null for non-numeric values', () => {
    expect(scalePosition('foo', spec({ scale: { worst: 0, best: 10 } }))).toBeNull()
    expect(scalePosition(null, spec({ scale: { worst: 0, best: 10 } }))).toBeNull()
  })

  it('maps higher-is-better values to 0..1', () => {
    const s = spec({ scale: { worst: 0, best: 10 } })
    expect(scalePosition(0, s)).toBe(0)
    expect(scalePosition(5, s)).toBe(0.5)
    expect(scalePosition(10, s)).toBe(1)
  })

  it('maps lower-is-better values correctly (worst > best)', () => {
    const s = spec({ scale: { worst: 1.5, best: 0.5 } })
    expect(scalePosition(1.5, s)).toBe(0)
    expect(scalePosition(1.0, s)).toBe(0.5)
    expect(scalePosition(0.5, s)).toBe(1)
  })

  it('clamps beyond the scale', () => {
    const s = spec({ scale: { worst: 0, best: 10 } })
    expect(scalePosition(-5, s)).toBe(0)
    expect(scalePosition(20, s)).toBe(1)
  })

  it('returns null when worst equals best', () => {
    expect(scalePosition(5, spec({ scale: { worst: 5, best: 5 } }))).toBeNull()
  })
})

describe('formatMetricValue', () => {
  it('formats percent with default decimals', () => {
    expect(formatMetricValue(0.1234, spec({ format: 'percent' }))).toBe('12.3%')
  })

  it('formats percent with explicit decimals', () => {
    expect(formatMetricValue(0.1234, spec({ format: 'percent', decimals: 2 }))).toBe('12.34%')
  })

  it('formats integer with no decimals', () => {
    expect(formatMetricValue(1234567, spec({ format: 'integer' }))).toBe('1,234,567')
  })

  it('formats currency with $ prefix', () => {
    expect(formatMetricValue(150.42, spec({ format: 'currency', decimals: 2 }))).toBe('$150.42')
  })

  it('formats ratio with 2 decimals by default', () => {
    expect(formatMetricValue(1.234, spec({ format: 'ratio' }))).toBe('1.23')
  })

  it('returns em-dash for null/undefined', () => {
    expect(formatMetricValue(null, spec())).toBe('—')
    expect(formatMetricValue(undefined, spec())).toBe('—')
  })

  it('returns string representation for non-numeric values', () => {
    expect(formatMetricValue('UP', spec())).toBe('UP')
    expect(formatMetricValue(true, spec())).toBe('true')
  })
})

describe('resolveMetricValue', () => {
  it('reads from metrics[key] when no derive is set', () => {
    expect(resolveMetricValue({ foo: 42 }, spec({ key: 'foo' }))).toBe(42)
  })

  it('uses derive when provided', () => {
    const s = spec({
      key: 'ratio',
      derive: (m) => {
        const a = typeof m.a === 'number' ? m.a : null
        const b = typeof m.b === 'number' ? m.b : null
        if (a === null || b === null || b === 0) return null
        return a / b
      },
    })
    expect(resolveMetricValue({ a: 10, b: 4 }, s)).toBe(2.5)
  })

  it('returns undefined when derive returns null', () => {
    const s = spec({ derive: () => null })
    expect(resolveMetricValue({}, s)).toBeUndefined()
  })
})

describe('mergeScannerTriggers', () => {
  it('prefers the scanner_triggers array when non-empty', () => {
    const triggers = [
      { scanner_type: 'CHEAP_OPTIONS', reason_codes: [], metrics: {}, triggered_at: 't' },
    ]
    const result = mergeScannerTriggers(triggers, { scanner_source: 'BREAKOUT' })
    expect(result).toBe(triggers)
  })

  it('synthesizes from evaluation-level fields when scanner_triggers is empty', () => {
    const result = mergeScannerTriggers([], {
      scanner_source: 'UNUSUAL_VOLUME',
      scanner_metrics: { volume_ratio: 3.2 },
      trigger_reasons: ['VOL_SPIKE'],
      evaluated_at: '2026-04-14T00:00:00Z',
    })
    expect(result).toEqual([
      {
        scanner_type: 'UNUSUAL_VOLUME',
        reason_codes: ['VOL_SPIKE'],
        metrics: { volume_ratio: 3.2 },
        triggered_at: '2026-04-14T00:00:00Z',
      },
    ])
  })

  it('returns [] when both sources are empty/missing', () => {
    expect(mergeScannerTriggers(null, null)).toEqual([])
    expect(mergeScannerTriggers([], {})).toEqual([])
    expect(mergeScannerTriggers(undefined, undefined)).toEqual([])
  })
})

describe('SCANNER_CONFIGS integrity', () => {
  it('every metric spec has a non-empty key and label', () => {
    for (const config of Object.values(SCANNER_CONFIGS)) {
      for (const metric of config.metrics) {
        expect(metric.key.length).toBeGreaterThan(0)
        expect(metric.label.length).toBeGreaterThan(0)
      }
    }
  })

  it('has an entry for each scanner type the backend can emit', () => {
    for (const type of [
      'CHEAP_OPTIONS',
      'BREAKOUT',
      'BREAKDOWN',
      'COMPRESSION_EXPANSION',
      'UNUSUAL_VOLUME',
      'REVALIDATION',
    ]) {
      expect(SCANNER_CONFIGS[type]).toBeDefined()
    }
  })

  it('zones within a scanner config are in ordered worst-to-best direction', () => {
    // A zoned spec must have its first zone on the "worst" side of scale
    // and last zone on the "best" side — check via scalePosition.
    for (const config of Object.values(SCANNER_CONFIGS)) {
      for (const m of config.metrics) {
        if (!m.zones || !m.scale) continue
        const firstCenter = (m.zones[0].min + m.zones[0].max) / 2
        const lastCenter =
          (m.zones[m.zones.length - 1].min + m.zones[m.zones.length - 1].max) / 2
        const firstPos = scalePosition(firstCenter, m)
        const lastPos = scalePosition(lastCenter, m)
        expect(firstPos).not.toBeNull()
        expect(lastPos).not.toBeNull()
        expect(firstPos!).toBeLessThan(lastPos!)
      }
    }
  })

  it('summarize functions are defined for the main scanner types', () => {
    expect(SCANNER_CONFIGS.CHEAP_OPTIONS.summarize).toBeDefined()
    expect(SCANNER_CONFIGS.UNUSUAL_VOLUME.summarize).toBeDefined()
    expect(SCANNER_CONFIGS.COMPRESSION_EXPANSION.summarize).toBeDefined()
    expect(SCANNER_CONFIGS.BREAKOUT.summarize).toBeDefined()
    expect(SCANNER_CONFIGS.BREAKDOWN.summarize).toBeDefined()
  })

  it('CHEAP_OPTIONS summarize includes value + zone for iv_rv_ratio', () => {
    const summary = SCANNER_CONFIGS.CHEAP_OPTIONS.summarize!({
      iv_rv_ratio: 0.83,
      iv_percentile: 19,
    })
    expect(summary).toContain('0.83')
    expect(summary?.toLowerCase()).toContain('cheap')
    expect(summary).toContain('19')
  })

  it('UV summarize includes volume and priority tags', () => {
    const summary = SCANNER_CONFIGS.UNUSUAL_VOLUME.summarize!({
      volume_ratio: 1.6,
      oi_change_pct: 30.75,
      priority_score: 23.9,
      spread_pct: 3.5,
    })
    expect(summary).toContain('1.6')
    expect(summary?.toLowerCase()).toContain('weak')
    expect(summary).toContain('30.7')
  })
})

describe('scannerLabel', () => {
  it('returns friendly label for known scanners', () => {
    expect(scannerLabel('CHEAP_OPTIONS')).toBe('Cheap Options')
    expect(scannerLabel('UNUSUAL_VOLUME')).toBe('Unusual Volume')
  })

  it('falls back to underscore-stripped raw type', () => {
    expect(scannerLabel('NEW_SCANNER_X')).toBe('NEW SCANNER X')
  })
})

describe('getScannerConfig', () => {
  it('returns config for known scanners', () => {
    expect(getScannerConfig('CHEAP_OPTIONS')?.role).toBe('leading')
    expect(getScannerConfig('BREAKOUT')?.role).toBe('lagging')
    expect(getScannerConfig('BREAKDOWN')?.role).toBe('lagging')
    expect(getScannerConfig('COMPRESSION_EXPANSION')?.role).toBe('mixed')
    expect(getScannerConfig('UNUSUAL_VOLUME')?.role).toBe('lagging')
    expect(getScannerConfig('REVALIDATION')?.role).toBe('meta')
  })

  it('returns null for unknown scanners', () => {
    expect(getScannerConfig('MYSTERY_SCANNER')).toBeNull()
  })
})
