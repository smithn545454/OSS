/**
 * Filter Bar Component
 * 
 * Per spec section 5.2: Time Range Selector | Scanner Type Filter
 */

import { Calendar, Filter } from 'lucide-react'
import type { TimeRangeOption, PipelineMonitorScannerType } from '@/lib/types'

interface FilterBarProps {
  timeRange: TimeRangeOption
  scannerFilter: PipelineMonitorScannerType
  onTimeRangeChange: (value: TimeRangeOption) => void
  onScannerChange: (value: PipelineMonitorScannerType) => void
}

const TIME_RANGE_OPTIONS: { value: TimeRangeOption; label: string }[] = [
  { value: 'last_hour', label: 'Last Hour' },
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: 'last_7_days', label: 'Last 7 Days' },
  { value: 'last_30_days', label: 'Last 30 Days' },
  { value: 'custom', label: 'Custom Range...' },
]

const SCANNER_OPTIONS: { value: PipelineMonitorScannerType; label: string }[] = [
  { value: 'all', label: 'All Scanners' },
  { value: 'unusual_volume', label: 'Unusual Volume' },
  { value: 'breakout', label: 'Breakout Detection' },
  { value: 'compression', label: 'Compression' },
  { value: 'cheap_options', label: 'Cheap Options' },
]

export function FilterBar({
  timeRange,
  scannerFilter,
  onTimeRangeChange,
  onScannerChange,
}: FilterBarProps) {
  const handleTimeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const value = e.target.value as TimeRangeOption
    if (value === 'custom') {
      // For MVP, show alert and revert
      alert('Custom date range coming soon')
      return
    }
    onTimeRangeChange(value)
  }

  const handleScannerChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    onScannerChange(e.target.value as PipelineMonitorScannerType)
  }

  return (
    <div className="flex flex-wrap gap-4 mb-6">
      {/* Time Range Selector */}
      <div 
        className="flex items-center gap-2 px-3 py-2 rounded-lg"
        style={{ 
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--border-default)',
        }}
      >
        <Calendar 
          className="w-4 h-4" 
          style={{ color: 'var(--text-secondary)' }}
        />
        <select
          value={timeRange}
          onChange={handleTimeChange}
          className="bg-transparent border-none outline-none text-[13px] cursor-pointer"
          style={{ 
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-primary)',
          }}
        >
          {TIME_RANGE_OPTIONS.map(opt => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Scanner Type Selector */}
      <div 
        className="flex items-center gap-2 px-3 py-2 rounded-lg"
        style={{ 
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--border-default)',
        }}
      >
        <Filter 
          className="w-4 h-4" 
          style={{ color: 'var(--text-secondary)' }}
        />
        <select
          value={scannerFilter}
          onChange={handleScannerChange}
          className="bg-transparent border-none outline-none text-[13px] cursor-pointer"
          style={{ 
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-primary)',
          }}
        >
          {SCANNER_OPTIONS.map(opt => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
