/**
 * v5 dual-conviction display metadata.
 *
 * HR Conviction: 0–20 realistic range. Literally P(MFE ≥ 200%) × fit × regime × 100.
 *   A score of 14 means "we estimate 14% probability this trade hits ≥200% MFE"
 *   — the probability semantics are preserved end-to-end.
 * P Conviction: 0–100 scale. Calibrated profitability (Wilson-lower win rate
 *   × normalized mean P&L). 70+ is strong grinder, 50+ is tradeable.
 */

export interface ConvictionBandMeta {
  label: string
  // Tailwind utility classes for badges (text on matching background)
  badgeClass: string
  // Short description for tooltips
  blurb: string
}

export function hrConvictionBand(value: number | null | undefined): ConvictionBandMeta {
  if (value == null) {
    return {
      label: 'N/A',
      badgeClass: 'border-slate-700 bg-slate-800/50 text-slate-400',
      blurb: 'v5 HR conviction not available (pre-v5 scoring)',
    }
  }
  if (value >= 14) {
    return {
      label: 'Sharpshooter',
      badgeClass: 'border-amber-500/60 bg-amber-500/15 text-amber-300',
      blurb: 'Estimated P(HR200) ≥ 14% — top archetype Wilson lower bound',
    }
  }
  if (value >= 7) {
    return {
      label: 'Conviction',
      badgeClass: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300',
      blurb: 'Estimated P(HR200) ≥ 7% — meaningful home-run candidate',
    }
  }
  if (value >= 3.5) {
    return {
      label: 'Watch',
      badgeClass: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
      blurb: 'HR signal present but below APPROVE floor',
    }
  }
  return {
    label: 'Low',
    badgeClass: 'border-slate-700 bg-slate-800/50 text-slate-400',
    blurb: 'Weak HR signal or no archetype match',
  }
}

export function pConvictionBand(value: number | null | undefined): ConvictionBandMeta {
  if (value == null) {
    return {
      label: 'N/A',
      badgeClass: 'border-slate-700 bg-slate-800/50 text-slate-400',
      blurb: 'v5 P conviction not available (pre-v5 scoring)',
    }
  }
  if (value >= 70) {
    return {
      label: 'Grinder',
      badgeClass: 'border-purple-500/60 bg-purple-500/15 text-purple-300',
      blurb: 'Calibrated P(profit) × P&L — reliable winner pattern',
    }
  }
  if (value >= 50) {
    return {
      label: 'Tradeable',
      badgeClass: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300',
      blurb: 'Above the v5 P-conviction APPROVE threshold',
    }
  }
  if (value >= 25) {
    return {
      label: 'Watch',
      badgeClass: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
      blurb: 'P signal present but below APPROVE floor',
    }
  }
  return {
    label: 'Low',
    badgeClass: 'border-slate-700 bg-slate-800/50 text-slate-400',
    blurb: 'Weak profitability signal or no P-archetype match',
  }
}

/**
 * Returns a friendly label for a v5 archetype ID. Handles both HR and P
 * archetypes. Falls back to the ID itself for unknown values (forward-
 * compatible with archetypes added by the auto-discovery pipeline).
 */
export function v5ArchetypeLabel(id: string | null | undefined): string {
  if (!id) return '—'
  const LABELS: Record<string, string> = {
    // HR archetypes (Phase 2)
    UV_LOTTERY_CALL: 'UV Lottery Call',
    UV_LOTTERY_DC_MID: 'UV Lottery (DC Mid)',
    UV_LOTTERY_IVRV_CHEAP: 'UV Lottery (IV/RV Cheap)',
    UV_LOTTERY_IVP_LO: 'UV Lottery (IV Pctl Low)',
    UV_STRUCTURAL: 'UV Structural',
    UV_REVERSAL_PUT: 'UV Reversal Put',
    CHEAP_COMPRESSION: 'Cheap Compression',
    CHEAP_VOL_REVERSAL: 'Cheap Vol Reversal',
    CHEAP_ULTRA_CALL: 'Cheap Ultra Call',
    CHEAP_ULTRA_MP_HIGH: 'Cheap Ultra MP-High',
    CHEAP_SHORT_FAIR_CONTRARIAN: 'Cheap Short Fair Contrarian',
    CHEAP_ULTRA_TS_MID: 'Cheap Ultra TS-Mid',
    // P archetypes (Phase 3)
    BREAKDOWN_GRINDER: 'Breakdown Grinder',
    REVALIDATION_QUALITY: 'Revalidation Quality',
    REVALIDATION_LOW_MP: 'Revalidation Low-MP',
    REVALIDATION_IVP_LO_CALL: 'Revalidation Low-IV Call',
    UV_VOLATILE_COMPRESSION: 'UV Volatile Compression',
    UV_VOLATILE_CALL: 'UV Volatile Call',
    UV_DEEP_OTM_VOLATILE: 'UV Deep-OTM Volatile',
    CHEAP_CONTRARIAN_CHEAP_VOL: 'Cheap Contrarian on Cheap Vol',
    CHEAP_VOLATILE_CALL: 'Cheap Volatile Call',
    BREAKOUT_CLEAN_ATR_MIDHI: 'Breakout Clean-IV Mid-High ATR',
  }
  return LABELS[id] ?? id
}

/**
 * Format a [0, 1] probability as a percentage with one decimal.
 * Returns '—' for null/undefined. Used for Wilson bound displays.
 */
export function formatPct(p: number | null | undefined): string {
  if (p == null) return '—'
  return `${(p * 100).toFixed(1)}%`
}
