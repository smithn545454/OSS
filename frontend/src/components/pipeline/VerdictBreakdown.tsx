/**
 * Verdict Breakdown Component
 * 
 * Per spec section 5.10: Final stage verdict distribution display
 */

import type { VerdictBreakdown as VerdictBreakdownType } from '@/lib/types'

interface VerdictBreakdownProps {
  breakdown: VerdictBreakdownType
}

function formatNumber(n: number): string {
  return n.toLocaleString()
}

export function VerdictBreakdown({ breakdown }: VerdictBreakdownProps) {
  return (
    <div 
      className="flex gap-4 mt-3 p-3 rounded-md"
      style={{ background: 'var(--bg-elevated)' }}
    >
      {/* Approve */}
      <div className="flex items-center gap-2">
        <span 
          className="w-2 h-2 rounded-full"
          style={{ background: 'var(--color-success)' }}
        />
        <span 
          className="text-[12px]"
          style={{ color: 'var(--text-muted)' }}
        >
          Approve:
        </span>
        <span 
          className="text-[12px] font-semibold"
          style={{ color: 'var(--color-success-text)' }}
        >
          {formatNumber(breakdown.approve)}
        </span>
      </div>

      {/* Watch */}
      <div className="flex items-center gap-2">
        <span 
          className="w-2 h-2 rounded-full"
          style={{ background: 'var(--color-warning)' }}
        />
        <span 
          className="text-[12px]"
          style={{ color: 'var(--text-muted)' }}
        >
          Watch:
        </span>
        <span 
          className="text-[12px] font-semibold"
          style={{ color: 'var(--color-warning-text)' }}
        >
          {formatNumber(breakdown.watch)}
        </span>
      </div>

      {/* Reject */}
      <div className="flex items-center gap-2">
        <span 
          className="w-2 h-2 rounded-full"
          style={{ background: 'var(--color-error)' }}
        />
        <span 
          className="text-[12px]"
          style={{ color: 'var(--text-muted)' }}
        >
          Reject:
        </span>
        <span 
          className="text-[12px] font-semibold"
          style={{ color: 'var(--color-error-text)' }}
        >
          {formatNumber(breakdown.reject)}
        </span>
      </div>
    </div>
  )
}
