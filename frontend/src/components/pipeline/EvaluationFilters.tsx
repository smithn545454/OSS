/**
 * Evaluation Filters Component
 * 
 * Per spec section 5.3: Multi-select filters for Verdict, DTE Bucket, Option Side
 */

import { Filter, X } from 'lucide-react'
import clsx from 'clsx'
import type { Verdict, DTEBucket, OptionType } from '@/lib/types'

interface EvaluationFiltersProps {
  verdictFilters: Verdict[]
  dteBucketFilters: DTEBucket[]
  optionSideFilters: OptionType[]
  onVerdictToggle: (verdict: Verdict) => void
  onDteBucketToggle: (bucket: DTEBucket) => void
  onOptionSideToggle: (side: OptionType) => void
  onClearAll: () => void
}

const VERDICTS: Verdict[] = ['APPROVE', 'WATCH', 'REJECT']
const DTE_BUCKETS: DTEBucket[] = ['A', 'B', 'C', 'D']
const OPTION_SIDES: OptionType[] = ['CALL', 'PUT']

export function EvaluationFilters({
  verdictFilters,
  dteBucketFilters,
  optionSideFilters,
  onVerdictToggle,
  onDteBucketToggle,
  onOptionSideToggle,
  onClearAll,
}: EvaluationFiltersProps) {
  const hasActiveFilters = 
    verdictFilters.length > 0 || 
    dteBucketFilters.length > 0 || 
    optionSideFilters.length > 0

  return (
    <div 
      className="p-5 rounded-xl mb-6"
      style={{
        background: 'var(--bg-elevated)',
        border: '1px solid var(--border-subtle)',
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4" style={{ color: 'var(--accent-primary)' }} />
          <span 
            className="text-[13px] font-medium"
            style={{ color: 'var(--text-muted)' }}
          >
            Evaluation Filters
          </span>
        </div>
        {hasActiveFilters && (
          <button
            onClick={onClearAll}
            className="flex items-center gap-1 text-[11px] hover:opacity-80 transition-opacity"
            style={{ color: 'var(--text-muted)' }}
          >
            <X className="w-3 h-3" />
            Clear All
          </button>
        )}
      </div>

      {/* Filter Groups */}
      <div className="flex flex-wrap gap-12">
        {/* Verdict Filter */}
        <div>
          <p 
            className="text-[11px] uppercase tracking-wide mb-2"
            style={{ color: 'var(--text-disabled)' }}
          >
            Verdict
          </p>
          <div className="flex gap-2">
            {VERDICTS.map(verdict => (
              <button
                key={verdict}
                onClick={() => onVerdictToggle(verdict)}
                aria-pressed={verdictFilters.includes(verdict)}
                className={clsx(
                  'filter-btn',
                  verdictFilters.includes(verdict) && 'selected',
                  verdict === 'APPROVE' && verdictFilters.includes(verdict) && '!bg-[var(--color-success-bg)] !border-[var(--color-success)] !text-[var(--color-success-text)]',
                  verdict === 'WATCH' && verdictFilters.includes(verdict) && '!bg-[var(--color-warning-bg)] !border-[var(--color-warning)] !text-[var(--color-warning-text)]',
                  verdict === 'REJECT' && verdictFilters.includes(verdict) && '!bg-[var(--color-error-bg)] !border-[var(--color-error)] !text-[var(--color-error-text)]',
                )}
              >
                {verdict}
              </button>
            ))}
          </div>
        </div>

        {/* DTE Bucket Filter */}
        <div>
          <p 
            className="text-[11px] uppercase tracking-wide mb-2"
            style={{ color: 'var(--text-disabled)' }}
          >
            DTE Bucket
          </p>
          <div className="flex gap-2">
            {DTE_BUCKETS.map(bucket => (
              <button
                key={bucket}
                onClick={() => onDteBucketToggle(bucket)}
                aria-pressed={dteBucketFilters.includes(bucket)}
                className={clsx(
                  'filter-btn',
                  dteBucketFilters.includes(bucket) && 'selected'
                )}
              >
                {bucket}
              </button>
            ))}
          </div>
        </div>

        {/* Option Side Filter */}
        <div>
          <p 
            className="text-[11px] uppercase tracking-wide mb-2"
            style={{ color: 'var(--text-disabled)' }}
          >
            Option Side
          </p>
          <div className="flex gap-2">
            {OPTION_SIDES.map(side => (
              <button
                key={side}
                onClick={() => onOptionSideToggle(side)}
                aria-pressed={optionSideFilters.includes(side)}
                className={clsx(
                  'filter-btn',
                  optionSideFilters.includes(side) && 'selected',
                  side === 'CALL' && optionSideFilters.includes(side) && '!bg-emerald-500/20 !border-emerald-500/40 !text-emerald-400',
                  side === 'PUT' && optionSideFilters.includes(side) && '!bg-rose-500/20 !border-rose-500/40 !text-rose-400',
                )}
              >
                {side}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
