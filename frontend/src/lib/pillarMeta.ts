/**
 * Single source of truth for pillar display metadata.
 *
 * The frontend renders both legacy v3 pillars (PREMIUM_LEVERAGE /
 * UNDERLYING_BEHAVIOR / SETUP_QUALITY) and v4 Sharpshooter pillars
 * (DIRECTIONAL_CONVICTION / MOVE_POTENTIAL / TRADE_STRUCTURE). Every
 * consumer should pull labels, icons, colors, and ordering from here
 * rather than hardcoding strings — this keeps historical v3 evaluations
 * and live v4 evaluations rendering through a single code path.
 *
 * v3 entries are marked `legacy: true` and stay here permanently so
 * 15,505 historical paper positions keep rendering correctly.
 */
import type { ComponentType, SVGProps } from 'react'
import {
  Activity,
  BarChart3,
  Compass,
  Layers,
  Rocket,
  TrendingUp,
  Zap,
} from 'lucide-react'
import type {
  CompositeFormula,
  PillarConfig,
  PillarId,
  PillarIdLegacy,
  PillarIdV4,
  PillarKey,
  PillarKeyLegacy,
  PillarKeyV4,
} from './types'

export type PillarIcon = ComponentType<SVGProps<SVGSVGElement>>

export interface PillarMeta {
  pillarId: PillarId
  pillarKey: PillarKey
  label: string
  shortLabel: string
  /** Tailwind text color class, e.g. 'text-sky-400'. */
  color: string
  /** Tailwind background + text palette for badges / pills. */
  badgeClass: string
  icon: PillarIcon
  /** Whether this pillar belongs to the legacy v3 regime. */
  legacy: boolean
  /** Default exponent / weight used by seeded policies for this regime. */
  defaultWeight: number
  /** One-line human description of what the pillar measures. */
  description: string
}

export const PILLAR_META: Record<PillarId, PillarMeta> = {
  // --- Legacy v3 pillars (retained for historical evaluations) ---
  PREMIUM_LEVERAGE: {
    pillarId: 'PREMIUM_LEVERAGE',
    pillarKey: 'premium_leverage',
    label: 'Premium Leverage',
    shortLabel: 'Premium',
    color: 'text-sky-400',
    badgeClass: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
    icon: Zap,
    legacy: true,
    defaultWeight: 0.375,
    description: 'Theta-adjusted edge and premium efficiency (legacy v3).',
  },
  UNDERLYING_BEHAVIOR: {
    pillarId: 'UNDERLYING_BEHAVIOR',
    pillarKey: 'underlying_behavior',
    label: 'Underlying Behavior',
    shortLabel: 'Behavior',
    color: 'text-purple-400',
    badgeClass: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
    icon: Activity,
    legacy: true,
    defaultWeight: 0.455,
    description: 'Volatility regime and underlying momentum (legacy v3).',
  },
  SETUP_QUALITY: {
    pillarId: 'SETUP_QUALITY',
    pillarKey: 'setup_quality',
    label: 'Setup Quality',
    shortLabel: 'Setup',
    color: 'text-amber-400',
    badgeClass: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    icon: BarChart3,
    legacy: true,
    defaultWeight: 0.170,
    description: 'Contract structure and liquidity (legacy v3).',
  },
  // --- v4 Sharpshooter pillars ---
  DIRECTIONAL_CONVICTION: {
    pillarId: 'DIRECTIONAL_CONVICTION',
    pillarKey: 'directional_conviction',
    label: 'Directional Conviction',
    shortLabel: 'Direction',
    color: 'text-blue-400',
    badgeClass: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
    icon: Compass,
    legacy: false,
    defaultWeight: 0.40,
    description:
      'Stage 2 trend, relative strength, ADX agreement, and proximity to breakout.',
  },
  MOVE_POTENTIAL: {
    pillarId: 'MOVE_POTENTIAL',
    pillarKey: 'move_potential',
    label: 'Move Potential',
    shortLabel: 'Move',
    color: 'text-emerald-400',
    badgeClass: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    icon: Rocket,
    legacy: false,
    defaultWeight: 0.35,
    description:
      'Catalyst trigger, historical move magnitude, IV/RV ratio, and volatility compression.',
  },
  TRADE_STRUCTURE: {
    pillarId: 'TRADE_STRUCTURE',
    pillarKey: 'trade_structure',
    label: 'Trade Structure',
    shortLabel: 'Structure',
    color: 'text-orange-400',
    badgeClass: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
    icon: Layers,
    legacy: false,
    defaultWeight: 0.25,
    description:
      'Delta sweet spot, gamma/theta ratio, DTE, IV rank, and strike proximity.',
  },
}

const LEGACY_KEYS_TO_IDS: Record<PillarKeyLegacy, PillarIdLegacy> = {
  premium_leverage: 'PREMIUM_LEVERAGE',
  underlying_behavior: 'UNDERLYING_BEHAVIOR',
  setup_quality: 'SETUP_QUALITY',
}

const V4_KEYS_TO_IDS: Record<PillarKeyV4, PillarIdV4> = {
  directional_conviction: 'DIRECTIONAL_CONVICTION',
  move_potential: 'MOVE_POTENTIAL',
  trade_structure: 'TRADE_STRUCTURE',
}

export const PILLAR_KEYS_LEGACY: PillarKeyLegacy[] = [
  'premium_leverage',
  'underlying_behavior',
  'setup_quality',
]

export const PILLAR_KEYS_V4: PillarKeyV4[] = [
  'directional_conviction',
  'move_potential',
  'trade_structure',
]

/**
 * Map a snake_case pillar key (as used on PillarWeights / PillarConfig) to
 * its PillarId enum value. Falls back to uppercase transform for unknown keys.
 */
export function pillarIdFromKey(key: string): PillarId {
  if (key in LEGACY_KEYS_TO_IDS) return LEGACY_KEYS_TO_IDS[key as PillarKeyLegacy]
  if (key in V4_KEYS_TO_IDS) return V4_KEYS_TO_IDS[key as PillarKeyV4]
  return key.toUpperCase() as PillarId
}

const FALLBACK_META: PillarMeta = {
  pillarId: 'PREMIUM_LEVERAGE',
  pillarKey: 'premium_leverage',
  label: 'Pillar',
  shortLabel: 'Pillar',
  color: 'text-oss-accent',
  badgeClass: 'bg-oss-accent/10 text-oss-accent border-oss-accent/30',
  icon: TrendingUp,
  legacy: false,
  defaultWeight: 0,
  description: '',
}

/**
 * Look up display metadata for any pillar ID. Unknown IDs yield a neutral
 * fallback (uppercased label, accent color, generic icon) so the UI never
 * breaks on an unexpected backend value.
 */
export function pillarMeta(id: PillarId | string | null | undefined): PillarMeta {
  if (!id) return FALLBACK_META
  const hit = (PILLAR_META as Record<string, PillarMeta>)[id]
  if (hit) return hit
  return {
    ...FALLBACK_META,
    label: String(id).replace(/_/g, ' '),
    shortLabel: String(id).replace(/_/g, ' '),
  }
}

/**
 * Detect whether a PillarConfig comes from a v4 policy. A config is v4 iff
 * `composite_formula === 'weighted_geometric_mean'` OR any v4 pillar slot is
 * populated. Falls back to v3 for legacy-only configs and bare configs.
 */
export function isV4PillarConfig(config: PillarConfig | null | undefined): boolean {
  if (!config) return false
  if (config.composite_formula === 'weighted_geometric_mean') return true
  return PILLAR_KEYS_V4.some((k) => config[k] != null)
}

/**
 * Return the active pillar keys for a given PillarConfig, in display order.
 * v4 configs return the three Sharpshooter keys; v3 configs return the three
 * legacy keys.
 */
export function activePillarKeys(config: PillarConfig | null | undefined): PillarKey[] {
  return isV4PillarConfig(config) ? [...PILLAR_KEYS_V4] : [...PILLAR_KEYS_LEGACY]
}

/**
 * Composite formula description for display. v3 → weighted arithmetic sum;
 * v4 → weighted geometric mean (with "insufficient data" pillar zeroing).
 */
export function compositeFormulaDescription(
  formula: CompositeFormula | null | undefined,
): string {
  if (formula === 'weighted_geometric_mean') {
    return 'Weighted geometric mean (any pillar at 0 → composite 0)'
  }
  return 'Weighted arithmetic sum'
}

/**
 * All v4 pillar-score snake_case field names on Decision / PaperPosition /
 * EvaluationSnapshot — useful for iterating without repeating the list.
 */
export const V4_SCORE_FIELDS_ON_DECISION = [
  'directional_conviction_score',
  'move_potential_score',
  'trade_structure_score',
] as const

export const V3_SCORE_FIELDS_ON_DECISION = [
  'premium_leverage_score',
  'underlying_behavior_score',
  'setup_quality_score',
] as const

export const V4_PILLAR_FIELDS_ON_POSITION = [
  'pillar_directional_conviction',
  'pillar_move_potential',
  'pillar_trade_structure',
] as const

export const V3_PILLAR_FIELDS_ON_POSITION = [
  'pillar_premium_leverage',
  'pillar_underlying_behavior',
  'pillar_setup_quality',
] as const

/**
 * Human-readable reason-code labels. v4 adds Sharpshooter-specific codes
 * emitted by the decision calculator (Phase 3); this map renders them nicely
 * in the Decision Explanation panel instead of the default snake_case split.
 */
export const REASON_CODE_LABELS: Record<string, string> = {
  // v4 tier codes
  SHARPSHOOTER_SETUP: 'Sharpshooter Setup',
  HIGH_CONVICTION: 'High Conviction',
  // v4 per-pillar strength codes
  STRONG_DIRECTIONAL_CONVICTION: 'Strong Directional Conviction',
  DECENT_DIRECTIONAL_CONVICTION: 'Decent Directional Conviction',
  WEAK_DIRECTIONAL_CONVICTION: 'Weak Directional Conviction',
  POOR_DIRECTIONAL_CONVICTION: 'Poor Directional Conviction',
  STRONG_MOVE_POTENTIAL: 'Strong Move Potential',
  DECENT_MOVE_POTENTIAL: 'Decent Move Potential',
  WEAK_MOVE_POTENTIAL: 'Weak Move Potential',
  POOR_MOVE_POTENTIAL: 'Poor Move Potential',
  STRONG_TRADE_STRUCTURE: 'Strong Trade Structure',
  DECENT_TRADE_STRUCTURE: 'Decent Trade Structure',
  WEAK_TRADE_STRUCTURE: 'Weak Trade Structure',
  POOR_TRADE_STRUCTURE: 'Poor Trade Structure',
  // v4 insufficient-data codes (min-subscore rule zeroes the pillar)
  INSUFFICIENT_DATA_DIRECTIONAL_CONVICTION: 'Insufficient data: Directional Conviction',
  INSUFFICIENT_DATA_MOVE_POTENTIAL: 'Insufficient data: Move Potential',
  INSUFFICIENT_DATA_TRADE_STRUCTURE: 'Insufficient data: Trade Structure',
  // Legacy v3 codes (preserved for historical decisions)
  STRONG_PREMIUM_LEVERAGE: 'Strong Premium Leverage',
  DECENT_PREMIUM_LEVERAGE: 'Decent Premium Leverage',
  WEAK_PREMIUM_LEVERAGE: 'Weak Premium Leverage',
  POOR_PREMIUM_LEVERAGE: 'Poor Premium Leverage',
  STRONG_UNDERLYING_BEHAVIOR: 'Strong Underlying Behavior',
  DECENT_UNDERLYING_BEHAVIOR: 'Decent Underlying Behavior',
  WEAK_UNDERLYING_BEHAVIOR: 'Weak Underlying Behavior',
  POOR_UNDERLYING_BEHAVIOR: 'Poor Underlying Behavior',
  STRONG_SETUP_QUALITY: 'Strong Setup Quality',
  DECENT_SETUP_QUALITY: 'Decent Setup Quality',
  WEAK_SETUP_QUALITY: 'Weak Setup Quality',
  POOR_SETUP_QUALITY: 'Poor Setup Quality',
}

/**
 * Display a reason code nicely. Known codes map to a proper label; unknown
 * codes fall back to the existing snake_case-split behavior.
 */
export function reasonCodeLabel(code: string): string {
  return REASON_CODE_LABELS[code] ?? code.replace(/_/g, ' ')
}
