import clsx from 'clsx'
import type { LivePosition } from '@/lib/types'

// v5 active scanners + legacy fallbacks (positions may still be open from v4).
// Kept as a static list so a scanner with zero open positions still shows as
// a disabled-looking chip — you know nothing's currently open in that bucket.
const SCANNER_OPTIONS: { value: string; label: string }[] = [
  { value: 'UNUSUAL_VOLUME', label: 'UV' },
  { value: 'CHEAP_OPTIONS', label: 'CHEAP' },
  { value: 'BREAKDOWN', label: 'BREAKDOWN' },
  { value: 'REVALIDATION', label: 'REVAL' },
  { value: 'BREAKOUT', label: 'BREAKOUT' },
  { value: 'COMPRESSION_EXPANSION', label: 'COMP' },
]

interface Props {
  value: string
  onChange: (value: string) => void
  positions: LivePosition[]
}

export default function ScannerFilterChips({ value, onChange, positions }: Props) {
  // Count open positions per scanner so chips show "UV (3)".
  const counts = positions.reduce<Record<string, number>>((acc, p) => {
    const key = p.scanner_source || 'UNKNOWN'
    acc[key] = (acc[key] || 0) + 1
    return acc
  }, {})

  const totalCount = positions.length
  const visibleOptions = SCANNER_OPTIONS.filter(
    (o) => counts[o.value] > 0 || value === o.value
  )

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-oss-muted mr-1">Scanner</span>
      <Chip
        active={value === 'all'}
        label="ALL"
        count={totalCount}
        onClick={() => onChange('all')}
      />
      {visibleOptions.map((opt) => (
        <Chip
          key={opt.value}
          active={value === opt.value}
          label={opt.label}
          count={counts[opt.value] || 0}
          onClick={() => onChange(opt.value)}
        />
      ))}
    </div>
  )
}

function Chip({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean
  label: string
  count: number
  onClick: () => void
}) {
  const disabled = count === 0 && !active
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        'rounded-full px-3 py-1 text-xs font-medium transition-colors border',
        active
          ? 'bg-oss-accent/15 text-oss-accent border-oss-border-active'
          : 'bg-oss-surface text-oss-muted border-oss-border hover:text-oss-text-secondary hover:border-oss-border-active',
        disabled && 'opacity-40 cursor-not-allowed hover:border-oss-border'
      )}
    >
      {label}
      <span className="ml-1.5 font-mono text-oss-muted">{count}</span>
    </button>
  )
}
