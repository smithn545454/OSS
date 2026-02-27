import { useState } from 'react'
import type { EarningsExclusion } from '@/lib/types'

interface OppEarningsNoticeProps {
  exclusions: EarningsExclusion[]
}

export function OppEarningsNotice({ exclusions }: OppEarningsNoticeProps) {
  const [dismissed, setDismissed] = useState(false)

  if (dismissed || exclusions.length === 0) return null

  const totalContracts = exclusions.reduce((sum, e) => sum + e.contractCount, 0)
  const tickers = exclusions.map((e) => e.ticker)
  const shown = tickers.slice(0, 5)
  const remaining = tickers.length - shown.length

  return (
    <div className="flex items-start gap-3 px-3 py-2 sm:px-4 sm:py-3 rounded-md bg-[rgba(245,158,11,0.08)] border border-[rgba(245,158,11,0.3)] text-sm">
      <span className="shrink-0 mt-0.5">📅</span>
      <div className="flex-1 min-w-0">
        <span className="text-[var(--color-warning-text)] font-medium">
          {totalContracts} contract{totalContracts !== 1 ? 's' : ''} excluded
        </span>
        <span className="text-[var(--text-muted)]"> due to upcoming earnings: </span>
        <span className="text-[var(--text-secondary)] font-mono text-xs">
          {shown.join(', ')}
          {remaining > 0 && (
            <span className="text-[var(--text-muted)]"> +{remaining} more</span>
          )}
        </span>
      </div>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors text-lg leading-none"
        aria-label="Dismiss notice"
      >
        ✕
      </button>
    </div>
  )
}
