/**
 * Filter bar for Paper Trading Workstation.
 * Four toggle groups: Period, Verdict, Scanner, Status.
 * Reads/writes URL search params for persistence.
 */

import { useSearchParams } from 'react-router-dom'
import clsx from 'clsx'

const PERIOD_OPTIONS = [
  { value: '7d', label: '7D' },
  { value: '14d', label: '14D' },
  { value: '30d', label: '30D' },
  { value: '90d', label: 'QTR' },
  { value: 'all', label: 'ALL' },
]

const VERDICT_OPTIONS = [
  { value: 'ALL', label: 'ALL' },
  { value: 'APPROVE', label: 'APPROVE' },
  { value: 'WATCH', label: 'WATCH' },
]

const SCANNER_OPTIONS = [
  { value: 'all', label: 'ALL' },
  { value: 'UV', label: 'UV' },
  { value: 'BRK', label: 'BRK' },
  { value: 'CMP', label: 'CMP' },
  { value: 'CHP', label: 'CHP' },
]

const STATUS_OPTIONS = [
  { value: 'all', label: 'ALL' },
  { value: 'open', label: 'OPEN' },
  { value: 'closed', label: 'CLOSED' },
]

interface ToggleGroupProps {
  label: string
  options: { value: string; label: string }[]
  value: string
  onChange: (value: string) => void
}

function ToggleGroup({ label, options, value, onChange }: ToggleGroupProps) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-oss-muted uppercase tracking-wider">{label}</span>
      <div className="flex rounded-lg border border-oss-border overflow-hidden">
        {options.map((opt) => (
          <button
            key={opt.value}
            onClick={() => onChange(opt.value)}
            className={clsx(
              'px-3 py-1.5 text-xs font-medium transition-colors',
              value === opt.value
                ? 'bg-oss-accent/20 text-oss-accent'
                : 'text-oss-muted hover:bg-oss-border hover:text-oss-text'
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  )
}

export interface FilterValues {
  period: string
  verdict: string
  scanner: string
  status: string
}

interface FilterBarProps {
  values: FilterValues
  onChange: (values: FilterValues) => void
}

export default function FilterBar({ values, onChange }: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-center gap-4 rounded-lg border border-oss-border bg-oss-surface p-3">
      <ToggleGroup
        label="Period"
        options={PERIOD_OPTIONS}
        value={values.period}
        onChange={(v) => onChange({ ...values, period: v })}
      />
      <ToggleGroup
        label="Verdict"
        options={VERDICT_OPTIONS}
        value={values.verdict}
        onChange={(v) => onChange({ ...values, verdict: v })}
      />
      <ToggleGroup
        label="Scanner"
        options={SCANNER_OPTIONS}
        value={values.scanner}
        onChange={(v) => onChange({ ...values, scanner: v })}
      />
      <ToggleGroup
        label="Status"
        options={STATUS_OPTIONS}
        value={values.status}
        onChange={(v) => onChange({ ...values, status: v })}
      />
    </div>
  )
}

export function useFilterParams(): [FilterValues, (v: FilterValues) => void] {
  const [searchParams, setSearchParams] = useSearchParams()

  const values: FilterValues = {
    period: searchParams.get('period') || '30d',
    verdict: searchParams.get('verdict') || 'ALL',
    scanner: searchParams.get('scanner') || 'all',
    status: searchParams.get('status') || 'all',
  }

  const setValues = (v: FilterValues) => {
    const params = new URLSearchParams()
    if (v.period !== '30d') params.set('period', v.period)
    if (v.verdict !== 'ALL') params.set('verdict', v.verdict)
    if (v.scanner !== 'all') params.set('scanner', v.scanner)
    if (v.status !== 'all') params.set('status', v.status)
    setSearchParams(params, { replace: true })
  }

  return [values, setValues]
}
