/**
 * V5 conviction badges — tier, archetype, HR/P inline display.
 *
 * Surfaces v5 dual-conviction scoring on the Opportunities list so sharpshooter
 * trades (TIER_1, HR ≥ 14) are visible at a glance without clicking into detail.
 */

import type { ApproveEvaluation } from '@/lib/types'

const TIER_STYLES: Record<string, { fg: string; bg: string; border: string; label: string }> = {
  TIER_1: {
    fg: '#10B981',
    bg: '#10B98118',
    border: '#10B98166',
    label: 'TIER 1',
  },
  TIER_2: {
    fg: '#38BDF8',
    bg: '#38BDF818',
    border: '#38BDF866',
    label: 'TIER 2',
  },
  TIER_3: {
    fg: '#F59E0B',
    bg: '#F59E0B18',
    border: '#F59E0B66',
    label: 'TIER 3',
  },
}

export function TierBadge({ tier, size = 'sm' }: { tier: string | null | undefined; size?: 'xs' | 'sm' }) {
  if (!tier) return null
  const style = TIER_STYLES[tier]
  if (!style) return null

  const fontSize = size === 'xs' ? '9px' : '10px'
  const padding = size === 'xs' ? '1px 5px' : '2px 6px'

  return (
    <span
      title={`Quality ${style.label}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding,
        fontSize,
        fontWeight: 700,
        letterSpacing: '0.04em',
        fontFamily: "'JetBrains Mono', monospace",
        color: style.fg,
        background: style.bg,
        border: `1px solid ${style.border}`,
        borderRadius: '4px',
        lineHeight: 1,
        whiteSpace: 'nowrap',
      }}
    >
      {style.label}
    </span>
  )
}

/** Is this a sharpshooter trade? HR conviction ≥ 14. */
export function isSharpshooter(ev: ApproveEvaluation): boolean {
  const hr = ev.decision?.hr_conviction
  return typeof hr === 'number' && hr >= 14
}

/**
 * Compact HR/P conviction inline display. Example: `HR 15 · P 68`.
 * Only shows sub-scores that exist. Hidden entirely if both are null.
 */
export function ConvictionInline({
  evaluation,
  size = 'sm',
}: {
  evaluation: ApproveEvaluation
  size?: 'xs' | 'sm'
}) {
  const hr = evaluation.decision?.hr_conviction
  const p = evaluation.decision?.p_conviction
  if (hr == null && p == null) return null

  const fontSize = size === 'xs' ? '10px' : '11px'
  const sharpshooter = typeof hr === 'number' && hr >= 14

  return (
    <span
      title="v5 conviction — HR: P(MFE ≥ 200%), P: P(profitable)"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        fontSize,
        fontFamily: "'JetBrains Mono', monospace",
        fontWeight: 600,
        lineHeight: 1,
        whiteSpace: 'nowrap',
      }}
    >
      {hr != null && (
        <span style={{ color: sharpshooter ? '#10B981' : '#8892A5' }}>
          HR {hr.toFixed(hr >= 10 ? 0 : 1)}
        </span>
      )}
      {hr != null && p != null && (
        <span style={{ color: '#3A4058' }}>·</span>
      )}
      {p != null && (
        <span style={{ color: p >= 70 ? '#38BDF8' : '#8892A5' }}>
          P {p.toFixed(0)}
        </span>
      )}
    </span>
  )
}

/** Short archetype pill. Truncates long names. */
export function ArchetypePill({
  archetype,
  kind,
}: {
  archetype: string | null | undefined
  kind: 'hr' | 'p'
}) {
  if (!archetype) return null

  const isHR = kind === 'hr'
  const fg = isHR ? '#FBBF24' : '#60A5FA'
  const bg = isHR ? '#FBBF2415' : '#60A5FA15'
  const border = isHR ? '#FBBF2440' : '#60A5FA40'

  return (
    <span
      title={`${isHR ? 'HR' : 'P'} archetype: ${archetype}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '2px 6px',
        fontSize: '9px',
        fontWeight: 600,
        letterSpacing: '0.02em',
        fontFamily: "'JetBrains Mono', monospace",
        color: fg,
        background: bg,
        border: `1px solid ${border}`,
        borderRadius: '3px',
        lineHeight: 1,
        maxWidth: '160px',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {archetype}
    </span>
  )
}
