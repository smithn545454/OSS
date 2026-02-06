/**
 * Multi-Failure Overlap Component
 * 
 * Per spec section 5.9: Shows contracts failing multiple rules simultaneously
 */

import clsx from 'clsx'
import type { DisplayFailureOverlap } from '@/lib/types'

interface MultiFailureOverlapProps {
  overlaps: DisplayFailureOverlap[]
  isExpanded: boolean
  onToggle: () => void
}

function formatNumber(n: number): string {
  return n.toLocaleString()
}

export function MultiFailureOverlap({
  overlaps,
  isExpanded,
  onToggle,
}: MultiFailureOverlapProps) {
  return (
    <div>
      {/* Toggle Button */}
      <button
        onClick={onToggle}
        className={clsx(
          'px-2.5 py-1.5 rounded text-[10px] font-medium uppercase tracking-wide transition-all',
          isExpanded
            ? 'bg-[var(--color-info-bg)] border border-[rgba(99,102,241,0.4)] text-[var(--color-info-text)]'
            : 'bg-[rgba(30,41,59,0.4)] border border-[var(--border-default)] text-[var(--text-muted)] hover:bg-[rgba(30,41,59,0.6)]'
        )}
      >
        {isExpanded ? 'Hide Multi-Failure Overlap' : 'Show Multi-Failure Overlap'}
      </button>

      {/* Overlap Panel */}
      {isExpanded && (
        <div className="overlap-panel mt-3">
          <p 
            className="text-[10px] mb-2"
            style={{ color: 'var(--color-info-text)' }}
          >
            Contracts failing multiple rules:
          </p>

          <div className="space-y-0">
            {overlaps.map((overlap, index) => (
              <div
                key={index}
                className={clsx(
                  'flex justify-between py-1 text-[11px]',
                  index < overlaps.length - 1 && 'border-b border-[rgba(99,102,241,0.1)]'
                )}
              >
                {/* Rule Combination */}
                <span style={{ color: 'var(--text-tertiary)' }}>
                  {overlap.rules.join(' + ')}
                </span>

                {/* Count */}
                <span 
                  className="font-medium"
                  style={{ color: 'var(--color-error-text)' }}
                >
                  {formatNumber(overlap.count)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
