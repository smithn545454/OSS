import { useState } from 'react'
import type { ScannerType, UrgencyLevel, OptionType } from '@/lib/types'
import clsx from 'clsx'

export interface OppFilters {
  scanner: 'all' | ScannerType
  urgency: 'all' | UrgencyLevel
  optionType: 'all' | OptionType
}

export type SortMode = 'composite' | 'conviction'

interface OppFilterBarProps {
  sortMode: SortMode
  onSortChange: (mode: SortMode) => void
  filters: OppFilters
  onFilterChange: (filters: OppFilters) => void
  totalCount: number
}

const SCANNER_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All Scanners' },
  { value: 'BREAKOUT', label: 'Breakout' },
  { value: 'BREAKDOWN', label: 'Breakdown' },
  { value: 'UNUSUAL_VOLUME', label: 'Unusual Vol' },
  { value: 'COMPRESSION_EXPANSION', label: 'Compression' },
  { value: 'CHEAP_OPTIONS', label: 'Cheap' },
]

const URGENCY_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All Urgency' },
  { value: 'act_now', label: 'Act Now' },
  { value: 'hours', label: 'Hours' },
  { value: 'patient', label: 'Patient' },
]

const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All Types' },
  { value: 'CALL', label: 'Calls' },
  { value: 'PUT', label: 'Puts' },
]

function SortToggle({ mode, onChange }: { mode: SortMode; onChange: (m: SortMode) => void }) {
  return (
    <div className="flex items-center rounded-md bg-[var(--bg-tertiary)] border border-[var(--border-default)] text-xs font-medium">
      <button
        onClick={() => onChange('composite')}
        className={clsx(
          'px-3 py-1.5 rounded-l-md transition-colors',
          mode === 'composite'
            ? 'bg-[var(--accent-primary)] text-[var(--bg-primary)]'
            : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
        )}
      >
        Composite
      </button>
      <button
        onClick={() => onChange('conviction')}
        className={clsx(
          'px-3 py-1.5 rounded-r-md transition-colors',
          mode === 'conviction'
            ? 'bg-[var(--accent-primary)] text-[var(--bg-primary)]'
            : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
        )}
      >
        Conviction
      </button>
    </div>
  )
}

function FilterSelect({
  value,
  options,
  onChange,
}: {
  value: string
  options: { value: string; label: string }[]
  onChange: (val: string) => void
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-2 py-1.5 text-xs font-medium rounded-md bg-[var(--bg-tertiary)] border border-[var(--border-default)] text-[var(--text-secondary)] appearance-none cursor-pointer focus:outline-none focus:ring-1 focus:ring-[var(--accent-primary)]"
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  )
}

export function OppFilterBar({
  sortMode,
  onSortChange,
  filters,
  onFilterChange,
  totalCount,
}: OppFilterBarProps) {
  const [showFilters, setShowFilters] = useState(false)

  const activeFilterCount = [
    filters.scanner !== 'all',
    filters.urgency !== 'all',
    filters.optionType !== 'all',
  ].filter(Boolean).length

  return (
    <div className="flex flex-col gap-2">
      {/* Top row: sort toggle, filter button (mobile), count */}
      <div className="flex items-center gap-2 sm:gap-3">
        <h2 className="text-base font-semibold text-[var(--text-primary)]">
          Conviction Queue
        </h2>
        <span className="flex items-center justify-center h-5 min-w-[20px] px-1.5 rounded-full bg-[var(--accent-primary)] text-[10px] font-bold text-[var(--bg-primary)]">
          {totalCount}
        </span>

        <div className="ml-auto flex items-center gap-2">
          <SortToggle mode={sortMode} onChange={onSortChange} />

          {/* Mobile filter toggle */}
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={clsx(
              'sm:hidden px-3 py-1.5 rounded-md text-xs font-medium border transition-colors',
              activeFilterCount > 0
                ? 'bg-[var(--accent-primary)]/10 border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'bg-[var(--bg-tertiary)] border-[var(--border-default)] text-[var(--text-muted)]',
            )}
          >
            Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}
          </button>

          {/* Desktop inline filters */}
          <div className="hidden sm:flex items-center gap-2">
            <FilterSelect
              value={filters.scanner}
              options={SCANNER_OPTIONS}
              onChange={(val) => onFilterChange({ ...filters, scanner: val as OppFilters['scanner'] })}
            />
            <FilterSelect
              value={filters.urgency}
              options={URGENCY_OPTIONS}
              onChange={(val) => onFilterChange({ ...filters, urgency: val as OppFilters['urgency'] })}
            />
            <FilterSelect
              value={filters.optionType}
              options={TYPE_OPTIONS}
              onChange={(val) => onFilterChange({ ...filters, optionType: val as OppFilters['optionType'] })}
            />
          </div>
        </div>
      </div>

      {/* Mobile filter panel */}
      {showFilters && (
        <div className="flex gap-2 sm:hidden">
          <FilterSelect
            value={filters.scanner}
            options={SCANNER_OPTIONS}
            onChange={(val) => onFilterChange({ ...filters, scanner: val as OppFilters['scanner'] })}
          />
          <FilterSelect
            value={filters.urgency}
            options={URGENCY_OPTIONS}
            onChange={(val) => onFilterChange({ ...filters, urgency: val as OppFilters['urgency'] })}
          />
          <FilterSelect
            value={filters.optionType}
            options={TYPE_OPTIONS}
            onChange={(val) => onFilterChange({ ...filters, optionType: val as OppFilters['optionType'] })}
          />
        </div>
      )}
    </div>
  )
}
