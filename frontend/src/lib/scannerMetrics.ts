// Per-scanner rendering config for the Evaluation Detail "Scanner Triggers" panel.
//
// Each scanner emits a free-form `metrics` dict. This module pairs those raw
// fields with:
//   - A one-line `question` the metric answers (so you know why it's on screen)
//   - A set of labeled `zones` (e.g. expensive / fair / cheap / steal) calibrated
//     to THIS scanner's optimal setup, each with a plain-English `note` that
//     explains what being in that zone means for the trade
//   - A `summarize()` function on the scanner config that builds a single-
//     sentence "why this fired" narrative from the raw metrics
//
// When scanner thresholds in the backend change (e.g. CheapOptionsConfig
// defaults in backend/app/core/schemas.py), update this file to match.
//
// Values documented here were extracted from:
//   backend/app/scanners/cheap_options.py
//   backend/app/scanners/breakout.py
//   backend/app/scanners/compression.py
//   backend/app/scanners/historical_uv.py
//   backend/app/scanners/orchestrator.py  (REVALIDATION)

export type MetricFormat = 'number' | 'percent' | 'ratio' | 'integer' | 'currency'

export type ComparisonOp = '<' | '>' | '<=' | '>='

export interface Threshold {
  op: ComparisonOp
  value: number
}

export type ZoneTone = 'bad' | 'neutral' | 'good' | 'great'

export interface MetricZone {
  /** Short label shown on the bar and as a badge: "expensive", "steal", etc. */
  label: string
  /** Zone's numeric lower bound (inclusive). Always <= max. */
  min: number
  /** Zone's numeric upper bound (exclusive except for the last zone). */
  max: number
  /** Drives color — from red (bad) through muted (neutral) to green (great). */
  tone: ZoneTone
  /** Plain-English interpretation: what does it mean to be in this zone? */
  note: string
}

export interface MetricSpec {
  /** Field name in ScannerTrigger.metrics. Used as the primary lookup. */
  key: string
  /** Human-readable label */
  label: string
  format: MetricFormat
  decimals?: number
  /**
   * The question this metric answers in the trader's head. Rendered above the
   * bar so it's clear why this metric is on screen. Keep under ~80 chars.
   */
  question?: string
  /**
   * Ordered list of zones from worst (leftmost on the bar) to best (rightmost).
   * Zone boundaries must be contiguous within [scale.worst, scale.best]. Omit
   * for purely informational metrics that render as label + value only.
   */
  zones?: MetricZone[]
  /**
   * Visual scale for the zoned bar. `worst` is the left edge; `best` is the
   * right edge. For lower-is-better metrics (IV/RV ratio), pass a higher
   * `worst` and lower `best`. Required when `zones` is set.
   */
  scale?: { worst: number; best: number }
  /** Short one-liner explaining what this metric means, shown on hover. */
  hint?: string
  /**
   * Optional derivation from the raw metrics dict, for values the scanner
   * computes but does not persist. If provided, this is used instead of the
   * direct `metrics[key]` lookup. Return null to omit the row.
   */
  derive?: (metrics: Record<string, unknown>) => number | null | undefined
}

export type ScannerRole = 'leading' | 'lagging' | 'mixed' | 'meta'

export interface ScannerConfig {
  label: string
  thesis: string
  role: ScannerRole
  metrics: MetricSpec[]
  /**
   * Build a plain-English "why this fired" sentence from the raw metrics.
   * Returns null when there isn't enough data to summarize. The rendered
   * narrative replaces (or augments) the raw reason_codes for readers.
   */
  summarize?: (metrics: Record<string, unknown>) => string | null
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function compare(value: number, op: ComparisonOp, threshold: number): boolean {
  switch (op) {
    case '<':
      return value < threshold
    case '>':
      return value > threshold
    case '<=':
      return value <= threshold
    case '>=':
      return value >= threshold
  }
}

/**
 * Locate the zone a value falls into. Zones need not be contiguous; the first
 * zone containing the value wins. Returns null for non-finite values. If the
 * value sits outside all zone ranges, snaps to the nearest zone (by center
 * distance) so we never show a value with no interpretation.
 */
export function findZone(value: unknown, zones: MetricZone[] | undefined): MetricZone | null {
  if (!zones || zones.length === 0) return null
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  for (const z of zones) {
    if (value >= z.min && value <= z.max) return z
  }
  let best: MetricZone | null = null
  let bestDist = Infinity
  for (const z of zones) {
    const center = (z.min + z.max) / 2
    const dist = Math.abs(value - center)
    if (dist < bestDist) {
      bestDist = dist
      best = z
    }
  }
  return best
}

/** Narrow an unknown value to a finite number, or null. */
function numOr(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** Format a numeric metric for display. */
export function formatMetricValue(value: unknown, spec: MetricSpec): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number' && Number.isFinite(value)) {
    const decimals = spec.decimals ?? defaultDecimals(spec.format)
    switch (spec.format) {
      case 'percent':
        return `${(value * 100).toFixed(decimals)}%`
      case 'integer':
        return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
      case 'currency':
        return `$${value.toFixed(decimals)}`
      case 'ratio':
      case 'number':
      default:
        return value.toFixed(decimals)
    }
  }
  return String(value)
}

function defaultDecimals(format: MetricFormat): number {
  switch (format) {
    case 'integer':
      return 0
    case 'percent':
    case 'currency':
      return 1
    case 'ratio':
    case 'number':
    default:
      return 2
  }
}

/**
 * Resolve the value for a metric from the raw trigger metrics, honoring
 * `spec.derive` if set. Returns undefined when no value is available so the
 * row can be skipped.
 */
export function resolveMetricValue(
  metrics: Record<string, unknown>,
  spec: MetricSpec,
): unknown {
  if (spec.derive) {
    const derived = spec.derive(metrics)
    if (derived === undefined || derived === null) return undefined
    return derived
  }
  return metrics[spec.key]
}

/**
 * Normalize a value to a 0–1 position on the metric's scale, where 0 = worst
 * end (left) and 1 = best end (right). Clamped to [0, 1]. Returns null when
 * the spec has no scale or the value is not a finite number.
 */
export function scalePosition(value: unknown, spec: MetricSpec): number | null {
  if (!spec.scale) return null
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  const { worst, best } = spec.scale
  if (worst === best) return null
  const raw = (value - worst) / (best - worst)
  if (raw < 0) return 0
  if (raw > 1) return 1
  return raw
}

/**
 * Classify a value against a spec's trigger and ideal thresholds.
 * Kept as a fallback for metrics without zones.
 */
export type FireClass = 'ideal' | 'triggered' | 'below_trigger' | 'na'
export function classifyFire(
  value: unknown,
  trigger?: Threshold,
  ideal?: Threshold,
): FireClass {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'na'
  if (!trigger && !ideal) return 'na'
  if (ideal && compare(value, ideal.op, ideal.value)) return 'ideal'
  if (trigger && compare(value, trigger.op, trigger.value)) return 'triggered'
  return 'below_trigger'
}

// ---------------------------------------------------------------------------
// Zone libraries
// ---------------------------------------------------------------------------

const IV_RV_ZONES: MetricZone[] = [
  {
    label: 'expensive',
    min: 1.1,
    max: 1.5,
    tone: 'bad',
    note: 'Options pricing in MORE movement than this stock has been making. Premium decay works against you — buying vol here is an uphill fight.',
  },
  {
    label: 'fair',
    min: 0.95,
    max: 1.1,
    tone: 'neutral',
    note: 'Options priced roughly in line with recent realized volatility. No statistical edge from the vega side — you need direction to be right.',
  },
  {
    label: 'cheap',
    min: 0.75,
    max: 0.95,
    tone: 'good',
    note: 'Options pricing in 5–25% LESS movement than the stock is actually making. If direction plays out, you get delta plus some vega expansion.',
  },
  {
    label: 'steal',
    min: 0.6,
    max: 0.75,
    tone: 'great',
    note: 'Options pricing in 25%+ less movement than recent realized. Rare asymmetric entry — small premium, big vega + delta upside when it moves.',
  },
]

const IV_PERCENTILE_ZONES: MetricZone[] = [
  {
    label: 'elevated',
    min: 60,
    max: 100,
    tone: 'bad',
    note: 'IV in the upper half of its 252-day range. You’re paying a premium for elevated expected vol — any IV mean-reversion hurts the position.',
  },
  {
    label: 'average',
    min: 40,
    max: 60,
    tone: 'neutral',
    note: 'Mid-range IV for this ticker. No IV-based edge either way.',
  },
  {
    label: 'low',
    min: 20,
    max: 40,
    tone: 'good',
    note: 'IV in the bottom 20–40% of its year. Historically cheap entry for THIS ticker — vega expansion is tailwind if vol mean-reverts up.',
  },
  {
    label: 'very low',
    min: 0,
    max: 20,
    tone: 'great',
    note: 'IV in the bottom 20% of its 252-day range. This is where the asymmetric cheap-premium setups live — your preferred hunting ground.',
  },
]

const BREAKOUT_VOLUME_ZONES: MetricZone[] = [
  {
    label: 'no conviction',
    min: 0,
    max: 1.0,
    tone: 'bad',
    note: 'Today’s volume was BELOW the 20-day average. The break has no participation behind it — high probability of a fakeout back into the range.',
  },
  {
    label: 'modest',
    min: 1.0,
    max: 1.5,
    tone: 'neutral',
    note: 'Volume slightly above average. The break is real but lacks the thrust that separates durable moves from one-day wonders.',
  },
  {
    label: 'confirmed',
    min: 1.5,
    max: 2.5,
    tone: 'good',
    note: 'Volume 1.5–2.5× average. Meets the standard confirmation bar — enough participation to trust the break as a trend signal.',
  },
  {
    label: 'strong',
    min: 2.5,
    max: 8.0,
    tone: 'great',
    note: 'Volume well above 2.5× average. Institutional-flavor participation on the break — highest-probability continuation setups live here.',
  },
]

const COMP_RATIO_ZONES: MetricZone[] = [
  {
    label: 'expanded',
    min: 1.3,
    max: 2.0,
    tone: 'bad',
    note: 'ATR well above its recent floor — volatility has already expanded. No coiled-spring dynamic; you’re chasing movement that already happened.',
  },
  {
    label: 'loose',
    min: 1.1,
    max: 1.3,
    tone: 'neutral',
    note: 'ATR close to but above the compression floor. Not quite coiled — could go either way.',
  },
  {
    label: 'compressed',
    min: 0.9,
    max: 1.1,
    tone: 'good',
    note: 'ATR at or below its 20-day floor. Classic coiled-spring setup — tight range primed to expand in whichever direction breaks.',
  },
  {
    label: 'coiled tight',
    min: 0.5,
    max: 0.9,
    tone: 'great',
    note: 'ATR well BELOW its 20-day floor. Severely compressed — when it breaks, expect rapid vol expansion that feeds both vega and delta.',
  },
]

const BREAK_GAP_ZONES: MetricZone[] = [
  {
    label: 'barely',
    min: 0,
    max: 0.02,
    tone: 'bad',
    note: 'Close barely past the break threshold. High fakeout risk — a single retrace could invalidate the signal.',
  },
  {
    label: 'clear',
    min: 0.02,
    max: 0.05,
    tone: 'good',
    note: 'Close 2–5% beyond the break level. Meaningful follow-through, unlikely to reverse the same day.',
  },
  {
    label: 'decisive',
    min: 0.05,
    max: 0.15,
    tone: 'great',
    note: 'Close 5%+ past the break level. Decisive move — momentum is real and the trend has started.',
  },
]

const UV_VOLUME_ZONES: MetricZone[] = [
  {
    label: 'normal',
    min: 0,
    max: 2.0,
    tone: 'bad',
    note: 'Contract trading at normal volume. Whatever fired this scanner, it wasn’t a volume spike — weak flow signal.',
  },
  {
    label: 'unusual',
    min: 2.0,
    max: 5.0,
    tone: 'neutral',
    note: 'Volume 2–5× average. Triggers the scanner but low-confidence — easily explained by a single sizeable trade.',
  },
  {
    label: 'heavy',
    min: 5.0,
    max: 10.0,
    tone: 'good',
    note: 'Volume 5–10× average. Clear informed positioning — multiple participants, not just one chunky trade.',
  },
  {
    label: 'extreme',
    min: 10.0,
    max: 50.0,
    tone: 'great',
    note: 'Volume 10×+ average. Strong flow signal — someone with conviction is accumulating this exact contract.',
  },
]

const UV_PRIORITY_ZONES: MetricZone[] = [
  {
    label: 'weak',
    min: 0,
    max: 30,
    tone: 'bad',
    note: 'Quality score below 30. Combination of volume, OI, spread, and liquidity doesn’t meet the bar for a meaningful UV signal.',
  },
  {
    label: 'modest',
    min: 30,
    max: 60,
    tone: 'neutral',
    note: 'Moderate quality fire. Some signals aligned but nothing compelling. Treat as ambiguous.',
  },
  {
    label: 'strong',
    min: 60,
    max: 80,
    tone: 'good',
    note: 'Strong combination of volume, OI, liquidity, and spread. Likely informed flow — worth investigating.',
  },
  {
    label: 'exceptional',
    min: 80,
    max: 100,
    tone: 'great',
    note: 'All signals maximally aligned — the highest-quality UV fires live here. Your paper data shows these pay off most reliably.',
  },
]

const UV_SPREAD_ZONES: MetricZone[] = [
  {
    label: 'untradeable',
    min: 10,
    max: 50,
    tone: 'bad',
    note: 'Spread above 10% of mid. You’d lose 10%+ just on entry. Don’t touch this contract regardless of how unusual the flow looks.',
  },
  {
    label: 'wide',
    min: 5,
    max: 10,
    tone: 'bad',
    note: 'Spread 5–10%. Significant slippage on entry and exit — the flow has to really pay off to clear the round-trip cost.',
  },
  {
    label: 'ok',
    min: 2,
    max: 5,
    tone: 'good',
    note: 'Spread 2–5%. Tradeable, but expect a 2–5% haircut on round-trip.',
  },
  {
    label: 'tight',
    min: 0,
    max: 2,
    tone: 'great',
    note: 'Spread under 2% of mid. Plenty of liquidity — you can get in and out cleanly without moving the market.',
  },
]

// ---------------------------------------------------------------------------
// Fire-summary templates
// ---------------------------------------------------------------------------

function zoneTag(value: number | null, zones: MetricZone[]): string | null {
  if (value === null) return null
  const z = findZone(value, zones)
  return z ? z.label.toUpperCase() : null
}

function fmtRatio(v: number | null, decimals = 2): string {
  return v === null ? '—' : v.toFixed(decimals)
}

function fmtPct(v: number | null, decimals = 1): string {
  return v === null ? '—' : `${v.toFixed(decimals)}%`
}

function summarizeCheapOptions(m: Record<string, unknown>): string | null {
  const iv_rv = numOr(m.iv_rv_ratio)
  const iv_pct = numOr(m.iv_percentile)
  const rs_5d = numOr(m.rs_5d)
  const dir = typeof m.triggered_direction === 'string' ? m.triggered_direction : null
  const parts: string[] = []
  if (iv_rv !== null) {
    const tag = zoneTag(iv_rv, IV_RV_ZONES)
    parts.push(`IV/RV ratio is ${fmtRatio(iv_rv)}${tag ? ` (${tag})` : ''}`)
  }
  if (iv_pct !== null) {
    const tag = zoneTag(iv_pct, IV_PERCENTILE_ZONES)
    parts.push(`IV Percentile is ${fmtPct(iv_pct)}${tag ? ` (${tag})` : ''}`)
  }
  if (parts.length === 0) return null
  let sentence = parts.join(' and ')
  if (dir && rs_5d !== null) {
    const direction = dir === 'UP' ? 'upward' : dir === 'DOWN' ? 'downward' : dir.toLowerCase()
    sentence += `. Directional momentum is ${direction} (${rs_5d >= 0 ? '+' : ''}${rs_5d.toFixed(2)}pp vs SPY over 5 days)`
  }
  return sentence + '.'
}

function summarizeUV(m: Record<string, unknown>): string | null {
  const vr = numOr(m.volume_ratio)
  const oi = numOr(m.oi_change_pct)
  const pri = numOr(m.priority_score)
  const spread = numOr(m.spread_pct)
  const parts: string[] = []
  if (vr !== null) {
    const tag = zoneTag(vr, UV_VOLUME_ZONES)
    parts.push(`volume ${fmtRatio(vr, 1)}× average${tag ? ` (${tag})` : ''}`)
  }
  if (oi !== null) {
    const direction = oi > 0 ? 'new positions opening' : oi < 0 ? 'unwinds' : 'flat'
    parts.push(`OI ${oi >= 0 ? '+' : ''}${oi.toFixed(1)}% (${direction})`)
  }
  if (pri !== null) {
    const tag = zoneTag(pri, UV_PRIORITY_ZONES)
    parts.push(`priority score ${pri.toFixed(1)}${tag ? ` (${tag})` : ''}`)
  }
  if (spread !== null) {
    const tag = zoneTag(spread, UV_SPREAD_ZONES)
    parts.push(`spread ${spread.toFixed(2)}%${tag ? ` (${tag})` : ''}`)
  }
  if (parts.length === 0) return null
  return 'Contract-level flow: ' + parts.join(', ') + '.'
}

function summarizeCompression(m: Record<string, unknown>): string | null {
  const atr = numOr(m.atr_today)
  const thresh = numOr(m.compression_threshold)
  const compRatio = atr !== null && thresh !== null && thresh !== 0 ? atr / thresh : null
  const dir = typeof m.triggered_direction === 'string' ? m.triggered_direction : null
  const close = numOr(m.today_close)
  const upThresh = numOr(m.break_up_threshold)
  const dnThresh = numOr(m.break_down_threshold)
  const gap =
    dir === 'UP' && close !== null && upThresh !== null && upThresh !== 0
      ? (close - upThresh) / upThresh
      : dir === 'DOWN' && close !== null && dnThresh !== null && dnThresh !== 0
        ? (dnThresh - close) / dnThresh
        : null
  const parts: string[] = []
  if (compRatio !== null) {
    const tag = zoneTag(compRatio, COMP_RATIO_ZONES)
    parts.push(
      `ATR compressed to ${compRatio.toFixed(2)}× its 20-day floor${tag ? ` (${tag})` : ''}`,
    )
  }
  if (gap !== null) {
    const tag = zoneTag(gap, BREAK_GAP_ZONES)
    const direction = dir === 'UP' ? 'above the prior 10-day high' : 'below the prior 10-day low'
    parts.push(`price closed ${(gap * 100).toFixed(2)}% ${direction}${tag ? ` (${tag})` : ''}`)
  }
  if (parts.length === 0) return null
  return parts.join('; ') + '.'
}

function summarizeBreakout(m: Record<string, unknown>): string | null {
  const vr = numOr(m.volume_ratio)
  const close = numOr(m.today_close)
  const high = numOr(m.prior_N_day_high)
  const N = numOr(m.N)
  const parts: string[] = []
  if (close !== null && high !== null) {
    const pct = high !== 0 ? ((close - high) / high) * 100 : 0
    const nLabel = N !== null ? `${N.toFixed(0)}-day` : 'prior'
    parts.push(`Close $${close.toFixed(2)} broke above the ${nLabel} high of $${high.toFixed(2)} by ${pct.toFixed(2)}%`)
  }
  if (vr !== null) {
    const tag = zoneTag(vr, BREAKOUT_VOLUME_ZONES)
    parts.push(`volume ${vr.toFixed(2)}× the 20-day average${tag ? ` (${tag})` : ''}`)
  }
  if (parts.length === 0) return null
  return parts.join(' on ') + '.'
}

function summarizeBreakdown(m: Record<string, unknown>): string | null {
  const vr = numOr(m.volume_ratio)
  const close = numOr(m.today_close)
  const low = numOr(m.prior_N_day_low)
  const N = numOr(m.N)
  const parts: string[] = []
  if (close !== null && low !== null) {
    const pct = low !== 0 ? ((low - close) / low) * 100 : 0
    const nLabel = N !== null ? `${N.toFixed(0)}-day` : 'prior'
    parts.push(`Close $${close.toFixed(2)} broke below the ${nLabel} low of $${low.toFixed(2)} by ${pct.toFixed(2)}%`)
  }
  if (vr !== null) {
    const tag = zoneTag(vr, BREAKOUT_VOLUME_ZONES)
    parts.push(`volume ${vr.toFixed(2)}× the 20-day average${tag ? ` (${tag})` : ''}`)
  }
  if (parts.length === 0) return null
  return parts.join(' on ') + '.'
}

// ---------------------------------------------------------------------------
// Scanner configs
// ---------------------------------------------------------------------------

export const SCANNER_CONFIGS: Record<string, ScannerConfig> = {
  CHEAP_OPTIONS: {
    label: 'Cheap Options',
    thesis:
      'Options are underpriced relative to realized volatility — buy cheap vega before the market reprices.',
    role: 'leading',
    summarize: summarizeCheapOptions,
    metrics: [
      {
        key: 'iv_rv_ratio',
        label: 'IV / RV Ratio',
        question: 'Are options priced below recent realized movement?',
        format: 'ratio',
        decimals: 2,
        scale: { worst: 1.5, best: 0.6 },
        zones: IV_RV_ZONES,
        hint: 'Implied vol (proxy) divided by 20-day realized vol. <1.0 = options underprice what the stock has been doing.',
      },
      {
        key: 'iv_percentile',
        label: 'IV Percentile',
        question: 'How cheap is IV vs its own 252-day history?',
        format: 'number',
        decimals: 1,
        scale: { worst: 100, best: 0 },
        zones: IV_PERCENTILE_ZONES,
        hint: 'Percentile rank of current IV across the last 252 trading days. Lower = historically cheap FOR THIS TICKER.',
      },
      {
        key: 'rv20',
        label: '20-day Realized Vol',
        format: 'percent',
        decimals: 1,
        hint: 'Annualized stdev of log returns over the past 20 trading days.',
      },
      {
        key: 'iv_proxy',
        label: 'ATM IV (proxy)',
        format: 'percent',
        decimals: 1,
        hint: 'Average of ATM call and put IV, annualized.',
      },
      {
        key: 'rs_5d',
        label: '5-day RS vs SPY (pp)',
        format: 'number',
        decimals: 2,
        hint: 'Underlying 5-day return minus SPY 5-day return, in percentage points. Only populated when directional momentum filter is on.',
      },
      {
        key: 'atm_dte',
        label: 'ATM DTE',
        format: 'integer',
        hint: 'Days to expiration of the ATM contracts used to estimate IV.',
      },
    ],
  },
  BREAKOUT: {
    label: 'Breakout',
    thesis: 'Close broke above the prior 20-day high — continuation higher is the bet.',
    role: 'lagging',
    summarize: summarizeBreakout,
    metrics: [
      {
        key: 'volume_ratio',
        label: 'Volume Ratio',
        question: 'Did the break come with conviction-grade volume?',
        format: 'ratio',
        decimals: 2,
        scale: { worst: 0.5, best: 5.0 },
        zones: BREAKOUT_VOLUME_ZONES,
        hint: "Today's volume divided by the 20-day average. Volume confirms the break is real, not a low-liquidity fakeout.",
      },
      {
        key: 'today_close',
        label: 'Today Close',
        format: 'currency',
        decimals: 2,
      },
      {
        key: 'prior_N_day_high',
        label: 'Prior N-day High',
        format: 'currency',
        decimals: 2,
        hint: 'The break level — close is above this.',
      },
      {
        key: 'prior_N_day_low',
        label: 'Prior N-day Low',
        format: 'currency',
        decimals: 2,
      },
      {
        key: 'N',
        label: 'Lookback (days)',
        format: 'integer',
      },
    ],
  },
  BREAKDOWN: {
    label: 'Breakdown',
    thesis: 'Close broke below the prior 20-day low — continuation lower is the bet.',
    role: 'lagging',
    summarize: summarizeBreakdown,
    metrics: [
      {
        key: 'volume_ratio',
        label: 'Volume Ratio',
        question: 'Did the breakdown come with conviction-grade volume?',
        format: 'ratio',
        decimals: 2,
        scale: { worst: 0.5, best: 5.0 },
        zones: BREAKOUT_VOLUME_ZONES,
        hint: "Today's volume divided by the 20-day average.",
      },
      {
        key: 'today_close',
        label: 'Today Close',
        format: 'currency',
        decimals: 2,
      },
      {
        key: 'prior_N_day_low',
        label: 'Prior N-day Low',
        format: 'currency',
        decimals: 2,
        hint: 'The break level — close is below this.',
      },
      {
        key: 'prior_N_day_high',
        label: 'Prior N-day High',
        format: 'currency',
        decimals: 2,
      },
      {
        key: 'N',
        label: 'Lookback (days)',
        format: 'integer',
      },
    ],
  },
  COMPRESSION_EXPANSION: {
    label: 'Compression',
    thesis:
      'ATR compressed to near its 20-day floor and price broke the recent range — coiled-spring + early expansion.',
    role: 'mixed',
    summarize: summarizeCompression,
    metrics: [
      {
        key: 'comp_ratio',
        label: 'Compression Ratio',
        question: 'How tightly coiled is the recent range?',
        format: 'ratio',
        decimals: 2,
        scale: { worst: 2.0, best: 0.5 },
        zones: COMP_RATIO_ZONES,
        hint: 'ATR today ÷ compression threshold (ATR floor × 1.10). <1 means currently at or below the compression floor.',
        derive: (m) => {
          const atr = numOr(m.atr_today)
          const thresh = numOr(m.compression_threshold)
          if (atr === null || thresh === null || thresh === 0) return null
          return atr / thresh
        },
      },
      {
        key: 'up_gap',
        label: 'Upside Break Gap',
        question: 'How decisively did price close above the range high?',
        format: 'percent',
        decimals: 2,
        scale: { worst: 0, best: 0.1 },
        zones: BREAK_GAP_ZONES,
        hint: 'Distance of close above the prior 10-day high (as a fraction). Bigger = more decisive break.',
        derive: (m) => {
          if (m.break_up !== true) return null
          const close = numOr(m.today_close)
          const thresh = numOr(m.break_up_threshold)
          if (close === null || thresh === null || thresh === 0) return null
          return (close - thresh) / thresh
        },
      },
      {
        key: 'dn_gap',
        label: 'Downside Break Gap',
        question: 'How decisively did price close below the range low?',
        format: 'percent',
        decimals: 2,
        scale: { worst: 0, best: 0.1 },
        zones: BREAK_GAP_ZONES,
        hint: 'Distance of close below the prior 10-day low (as a fraction). Bigger = more decisive break.',
        derive: (m) => {
          if (m.break_down !== true) return null
          const close = numOr(m.today_close)
          const thresh = numOr(m.break_down_threshold)
          if (close === null || thresh === null || thresh === 0) return null
          return (thresh - close) / thresh
        },
      },
      {
        key: 'atr_today',
        label: 'ATR (today)',
        format: 'number',
        decimals: 3,
      },
      {
        key: 'atr_floor',
        label: 'ATR Floor (20d)',
        format: 'number',
        decimals: 3,
        hint: 'Lowest ATR(14) in the prior 20 bars — the compression anchor.',
      },
      {
        key: 'triggered_direction',
        label: 'Break Direction',
        format: 'number',
      },
      {
        key: 'prior_range_high',
        label: 'Prior 10d High',
        format: 'currency',
        decimals: 2,
      },
      {
        key: 'prior_range_low',
        label: 'Prior 10d Low',
        format: 'currency',
        decimals: 2,
      },
      {
        key: 'today_close',
        label: 'Today Close',
        format: 'currency',
        decimals: 2,
      },
    ],
  },
  UNUSUAL_VOLUME: {
    label: 'Unusual Volume',
    thesis:
      'Contract-level flow arrived — volume spike, OI change, or both. Ride the wake of informed positioning.',
    role: 'lagging',
    summarize: summarizeUV,
    metrics: [
      {
        key: 'volume_ratio',
        label: 'Volume Ratio',
        question: 'How unusual is today’s volume on this specific contract?',
        format: 'ratio',
        decimals: 1,
        scale: { worst: 1.0, best: 20.0 },
        zones: UV_VOLUME_ZONES,
        hint: 'Contract volume today vs its own 20-day average. High = real flow, not noise.',
      },
      {
        key: 'priority_score',
        label: 'Priority Score',
        question: 'What’s the combined quality of this flow signal?',
        format: 'number',
        decimals: 1,
        scale: { worst: 0, best: 100 },
        zones: UV_PRIORITY_ZONES,
        hint: 'Weighted 0–100 score: 40% volume ratio, 25% OI change, 15% trigger count, 10% spread, 10% liquidity.',
      },
      {
        key: 'spread_pct',
        label: 'Spread (%)',
        question: 'Can you actually trade this contract without bleeding?',
        format: 'number',
        decimals: 2,
        scale: { worst: 20, best: 0.5 },
        zones: UV_SPREAD_ZONES,
        hint: 'Bid/ask spread as % of mid. Tight spreads mean the flow is real and the contract is liquid enough to trade.',
      },
      {
        key: 'oi_change_pct',
        label: 'OI Change (%)',
        format: 'number',
        decimals: 1,
        hint: 'Change in open interest vs yesterday. Positive = new positions opening; large negative = unwinds. Bi-directional; no zone bar.',
      },
      {
        key: 'contracts_triggered',
        label: 'Contracts Triggered',
        format: 'integer',
        hint: 'How many contracts on this ticker lit up — more simultaneous hits = stronger aggregate flow signal.',
      },
      {
        key: 'today_volume',
        label: 'Today Volume',
        format: 'integer',
      },
      {
        key: 'avg_volume_20d',
        label: '20d Avg Volume',
        format: 'integer',
      },
      {
        key: 'today_oi',
        label: 'Today OI',
        format: 'integer',
      },
      {
        key: 'prior_oi',
        label: 'Prior OI',
        format: 'integer',
      },
    ],
  },
  REVALIDATION: {
    label: 'Revalidation',
    thesis: 'Prior APPROVE refreshed with current prices and Greeks — not a new discovery.',
    role: 'meta',
    metrics: [],
  },
}

/**
 * Returns the scanner config for a given type string, or null if unknown.
 */
export function getScannerConfig(scannerType: string): ScannerConfig | null {
  return SCANNER_CONFIGS[scannerType] ?? null
}

/** Human-readable label for a scanner, falling back to the raw type. */
export function scannerLabel(scannerType: string): string {
  return SCANNER_CONFIGS[scannerType]?.label ?? scannerType.replace(/_/g, ' ')
}

// ---------------------------------------------------------------------------
// Trigger merging (UV path)
// ---------------------------------------------------------------------------

export interface SyntheticTrigger {
  scanner_type: string
  reason_codes: string[]
  metrics: Record<string, unknown>
  triggered_at: string
}

/**
 * Unified trigger list for the Evaluation Detail panel.
 *
 * Scanners that flow through the Opportunity path populate the top-level
 * `scanner_triggers[]` array. Direct-to-Stage-4 scanners (Unusual Volume)
 * store their metadata on the evaluation itself as scanner_source +
 * scanner_metrics + trigger_reasons. This helper synthesizes a single
 * trigger from those fields so the panel renders for both paths.
 */
export function mergeScannerTriggers(
  scannerTriggers: SyntheticTrigger[] | null | undefined,
  evaluation:
    | {
        scanner_source?: string | null
        scanner_metrics?: Record<string, unknown> | null
        trigger_reasons?: string[] | null
        evaluated_at?: string | null
      }
    | null
    | undefined,
): SyntheticTrigger[] {
  if (scannerTriggers && scannerTriggers.length > 0) return scannerTriggers
  if (!evaluation?.scanner_source) return []
  return [
    {
      scanner_type: evaluation.scanner_source,
      reason_codes: evaluation.trigger_reasons ?? [],
      metrics: evaluation.scanner_metrics ?? {},
      triggered_at: evaluation.evaluated_at ?? '',
    },
  ]
}
