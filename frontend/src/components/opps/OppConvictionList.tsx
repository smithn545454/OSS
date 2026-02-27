import { useState } from 'react'
import type { ApproveEvaluation } from '@/lib/types'
import { OppCard } from './OppCard'
import clsx from 'clsx'

const PAGE_SIZE = 20

interface OppConvictionListProps {
  evaluations: ApproveEvaluation[]
  hasNewData?: boolean
  newCount?: number
  onRefresh?: () => void
}

export function OppConvictionList({
  evaluations,
  hasNewData,
  newCount,
  onRefresh,
}: OppConvictionListProps) {
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)

  const visible = evaluations.slice(0, visibleCount)
  const hasMore = visibleCount < evaluations.length

  return (
    <div className="flex flex-col gap-2 sm:gap-3">
      {/* New data notification */}
      {hasNewData && newCount && newCount > 0 && (
        <button
          onClick={onRefresh}
          className={clsx(
            'w-full py-2 rounded-md text-sm font-medium text-center transition-colors',
            'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]',
            'border border-[var(--accent-primary)]/30',
            'hover:bg-[var(--accent-primary)]/20',
          )}
        >
          {newCount} new opportunit{newCount === 1 ? 'y' : 'ies'} detected — tap to refresh
        </button>
      )}

      {/* Card list */}
      {visible.map((evaluation, index) => (
        <OppCard
          key={evaluation.evaluation_id}
          evaluation={evaluation}
          rank={index + 1}
        />
      ))}

      {/* Show more */}
      {hasMore && (
        <button
          onClick={() => setVisibleCount((v) => v + PAGE_SIZE)}
          className={clsx(
            'w-full py-3 rounded-lg text-sm font-medium transition-colors',
            'bg-[var(--bg-secondary)] text-[var(--text-muted)]',
            'border border-[var(--border-default)]',
            'hover:bg-[var(--bg-hover)] hover:text-[var(--text-secondary)]',
          )}
        >
          Show more ({evaluations.length - visibleCount} remaining)
        </button>
      )}
    </div>
  )
}
