/**
 * v4.1.0 archetype + anti-archetype display metadata.
 *
 * Mirrors the historical stats from
 * backend/app/archetypes/defaults.py — keep in sync.
 */

export interface ArchetypeMeta {
  id: string
  label: string
  shortLabel: string
  thesis: string
  color: string // tailwind text color class
  badgeClass: string // tailwind bg/border classes for badges
  historicalHr200Rate: number
  historicalWinRate: number
  historicalN: number
  isAnti?: boolean
}

export const ARCHETYPE_META: Record<string, ArchetypeMeta> = {
  UV_LOTTERY_CALL: {
    id: 'UV_LOTTERY_CALL',
    label: 'UV Lottery Call',
    shortLabel: 'UV Lottery',
    thesis:
      'Short-DTE (14-21) far-OTM CALL on an unusual-volume stock. Tight gamma turns small moves into outsized % gains.',
    color: 'text-yellow-400',
    badgeClass: 'bg-yellow-500/10 border-yellow-500/40 text-yellow-300',
    historicalHr200Rate: 0.2018,
    historicalWinRate: 0.658,
    historicalN: 114,
  },
  UV_STRUCTURAL: {
    id: 'UV_STRUCTURAL',
    label: 'UV Structural Explosion',
    shortLabel: 'UV Structural',
    thesis:
      'Unusual-volume stock with short-dated option and excellent Trade Structure (TS ≥ 75). Clean microstructure lets moves translate to wins.',
    color: 'text-orange-400',
    badgeClass: 'bg-orange-500/10 border-orange-500/40 text-orange-300',
    historicalHr200Rate: 0.0949,
    historicalWinRate: 0.599,
    historicalN: 274,
  },
  UV_REVERSAL_PUT: {
    id: 'UV_REVERSAL_PUT',
    label: 'UV Reversal Put',
    shortLabel: 'UV Reversal',
    thesis:
      'Unusual-volume PUT on a stock that has been rallying — institutional hedging / reversal flow.',
    color: 'text-rose-400',
    badgeClass: 'bg-rose-500/10 border-rose-500/40 text-rose-300',
    historicalHr200Rate: 0.1045,
    historicalWinRate: 0.545,
    historicalN: 220,
  },
  CHEAP_COMPRESSION: {
    id: 'CHEAP_COMPRESSION',
    label: 'Cheap Options Compression Breakout',
    shortLabel: 'Cheap Compression',
    thesis:
      'Cheap-options entry on a coiled (low-ADX) underlying with mid-range ATR and elevated MP — the spring about to uncoil.',
    color: 'text-emerald-400',
    badgeClass: 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300',
    historicalHr200Rate: 0.0753,
    historicalWinRate: 0.484,
    historicalN: 93,
  },
  CHEAP_VOL_REVERSAL: {
    id: 'CHEAP_VOL_REVERSAL',
    label: 'Cheap Options Volatile Reversal',
    shortLabel: 'Cheap Vol Reversal',
    thesis:
      'High-ATR underlying where options are fairly priced and RS is against the option direction — catches sharp reversals.',
    color: 'text-cyan-400',
    badgeClass: 'bg-cyan-500/10 border-cyan-500/40 text-cyan-300',
    historicalHr200Rate: 0.08,
    historicalWinRate: 0.74,
    historicalN: 50,
  },
  CHEAP_ULTRA_CALL: {
    id: 'CHEAP_ULTRA_CALL',
    label: 'Cheap Options Ultra-Short Call',
    shortLabel: 'Cheap Ultra',
    thesis:
      'Ultra-short-dated (DTE < 14) CALL on a low-IV underlying flagged by the cheap-options scanner. Small sample — treat with caution.',
    color: 'text-fuchsia-400',
    badgeClass: 'bg-fuchsia-500/10 border-fuchsia-500/40 text-fuchsia-300',
    historicalHr200Rate: 0.1081,
    historicalWinRate: 0.865,
    historicalN: 37,
  },
}

export const ANTI_ARCHETYPE_META: Record<string, ArchetypeMeta> = {
  BREAKOUT_MP_ELITE: {
    id: 'BREAKOUT_MP_ELITE',
    label: 'BREAKOUT × MP Elite (rejected)',
    shortLabel: 'AA: Brkt-MP',
    thesis:
      'BREAKOUT already captures momentum; MP ≥ 75 double-counts it. 321 historical trades, 0% win rate.',
    color: 'text-red-400',
    badgeClass: 'bg-red-500/10 border-red-500/40 text-red-300',
    historicalHr200Rate: 0,
    historicalWinRate: 0,
    historicalN: 321,
    isAnti: true,
  },
  UV_LONG_DATED: {
    id: 'UV_LONG_DATED',
    label: 'UV × Long-Dated (rejected)',
    shortLabel: 'AA: UV-LongDTE',
    thesis:
      'Unusual-volume signal decays before long-dated (DTE ≥ 45) options can move. 2,241 trades, 0.6% HR200.',
    color: 'text-red-400',
    badgeClass: 'bg-red-500/10 border-red-500/40 text-red-300',
    historicalHr200Rate: 0.006,
    historicalWinRate: 0.37,
    historicalN: 2241,
    isAnti: true,
  },
  CHEAP_DC_ELITE: {
    id: 'CHEAP_DC_ELITE',
    label: 'CHEAP × DC Elite (rejected)',
    shortLabel: 'AA: Cheap-DC',
    thesis:
      'CHEAP_OPTIONS compression/reversal thesis broken when DC ≥ 75 — stock is already trending. 2,315 trades, 0.1% HR200.',
    color: 'text-red-400',
    badgeClass: 'bg-red-500/10 border-red-500/40 text-red-300',
    historicalHr200Rate: 0.001,
    historicalWinRate: 0.41,
    historicalN: 2315,
    isAnti: true,
  },
}

const UNKNOWN_META: ArchetypeMeta = {
  id: 'UNKNOWN',
  label: 'Unknown archetype',
  shortLabel: 'Unknown',
  thesis: '',
  color: 'text-slate-400',
  badgeClass: 'bg-slate-500/10 border-slate-500/40 text-slate-300',
  historicalHr200Rate: 0,
  historicalWinRate: 0,
  historicalN: 0,
}

export function archetypeMeta(id: string | null | undefined): ArchetypeMeta {
  if (!id) return UNKNOWN_META
  return ARCHETYPE_META[id] ?? { ...UNKNOWN_META, id, label: id, shortLabel: id }
}

export function antiArchetypeMeta(id: string | null | undefined): ArchetypeMeta {
  if (!id) return UNKNOWN_META
  return (
    ANTI_ARCHETYPE_META[id] ?? {
      ...UNKNOWN_META,
      id,
      label: id,
      shortLabel: id,
      isAnti: true,
      color: 'text-red-400',
      badgeClass: 'bg-red-500/10 border-red-500/40 text-red-300',
    }
  )
}
