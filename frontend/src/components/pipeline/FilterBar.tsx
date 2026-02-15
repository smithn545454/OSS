/**
 * Filter Bar Component
 *
 * Per spec section 5.2: Time Range Selector | Scanner Type Filter
 * Custom dropdown with integrated calendar for date selection.
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { Calendar, Filter, ChevronDown, X } from 'lucide-react'
import type { TimeRangeOption, PipelineMonitorScannerType } from '@/lib/types'
import { formatDateShort } from '@/lib/formatTime'
import { DatePicker } from './DatePicker'

interface FilterBarProps {
  timeRange: TimeRangeOption
  scannerFilter: PipelineMonitorScannerType
  onTimeRangeChange: (value: TimeRangeOption) => void
  onScannerChange: (value: PipelineMonitorScannerType) => void
  customDateRange: { start: string; end: string } | null
  onCustomDateChange: (range: { start: string; end: string } | null) => void
}

const PRESET_OPTIONS: { value: TimeRangeOption; label: string }[] = [
  { value: 'last_hour', label: 'Last Hour' },
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: 'last_7_days', label: 'Last 7 Days' },
  { value: 'last_30_days', label: 'Last 30 Days' },
]

const SCANNER_OPTIONS: { value: PipelineMonitorScannerType; label: string }[] = [
  { value: 'all', label: 'All Scanners' },
  { value: 'unusual_volume', label: 'Unusual Volume' },
  { value: 'breakout', label: 'Breakout Detection' },
  { value: 'compression', label: 'Compression' },
  { value: 'cheap_options', label: 'Cheap Options' },
]

/** Format the custom range for display */
function formatRangeLabel(range: { start: string; end: string }): string {
  const startLabel = formatDateShort(range.start)
  const endLabel = formatDateShort(range.end)
  // Check if same day by comparing just the date portion
  if (startLabel === endLabel) return startLabel
  return `${startLabel} - ${endLabel}`
}

export function FilterBar({
  timeRange,
  scannerFilter,
  onTimeRangeChange,
  onScannerChange,
  customDateRange,
  onCustomDateChange,
}: FilterBarProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') setDropdownOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [dropdownOpen])

  const handlePresetClick = useCallback(
    (value: TimeRangeOption) => {
      onTimeRangeChange(value)
      onCustomDateChange(null)
      setDropdownOpen(false)
    },
    [onTimeRangeChange, onCustomDateChange]
  )

  const handleCalendarApply = useCallback(
    (start: string, end: string) => {
      onCustomDateChange({ start, end })
      onTimeRangeChange('custom')
      setDropdownOpen(false)
    },
    [onCustomDateChange, onTimeRangeChange]
  )

  const handleClearCustom = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      onCustomDateChange(null)
      onTimeRangeChange('today')
    },
    [onCustomDateChange, onTimeRangeChange]
  )

  const handleScannerChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    onScannerChange(e.target.value as PipelineMonitorScannerType)
  }

  // Determine the display label
  const isCustom = timeRange === 'custom' && customDateRange
  const displayLabel = isCustom
    ? formatRangeLabel(customDateRange)
    : PRESET_OPTIONS.find(o => o.value === timeRange)?.label || 'Today'

  return (
    <div className="relative flex flex-wrap gap-4 mb-6" style={{ zIndex: 20 }}>
      {/* Time Range Selector — custom dropdown */}
      <div
        ref={dropdownRef}
        className="relative flex items-center gap-2 px-3 py-2 rounded-lg"
        style={{
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--border-default)',
        }}
      >
        <Calendar
          className="w-4 h-4 flex-shrink-0"
          style={{ color: 'var(--text-secondary)' }}
        />

        <button
          className="time-range-trigger"
          onClick={() => setDropdownOpen(prev => !prev)}
        >
          <span>{displayLabel}</span>
          {isCustom ? null : (
            <ChevronDown
              className="w-3 h-3"
              style={{
                color: 'var(--text-muted)',
                transform: dropdownOpen ? 'rotate(180deg)' : undefined,
                transition: 'transform 150ms ease',
              }}
            />
          )}
        </button>

        {isCustom && (
          <button className="time-range-clear" onClick={handleClearCustom} aria-label="Clear custom range">
            <X className="w-3 h-3" />
          </button>
        )}

        {/* Dropdown popover */}
        {dropdownOpen && (
          <div className="time-range-dropdown">
            {/* Presets */}
            <div className="time-range-presets">
              {PRESET_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  className={`time-range-preset${timeRange === opt.value && !isCustom ? ' active' : ''}`}
                  onClick={() => handlePresetClick(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <div className="time-range-divider" />
            {/* Calendar */}
            <DatePicker
              initialStart={customDateRange?.start}
              initialEnd={customDateRange?.end}
              onApply={handleCalendarApply}
              onClose={() => setDropdownOpen(false)}
            />
          </div>
        )}
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
