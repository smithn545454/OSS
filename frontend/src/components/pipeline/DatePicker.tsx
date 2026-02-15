/**
 * Calendar Date Picker for Pipeline Monitor.
 * Zero-dependency calendar component for selecting single dates or date ranges.
 */

import { useState, useCallback, useMemo } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'

interface DatePickerProps {
  initialStart?: string // ISO string
  initialEnd?: string // ISO string
  onApply: (start: string, end: string) => void
  onClose: () => void
}

const TZ = 'America/Los_Angeles'

const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']

/** Get today's date parts in Pacific Time */
function getPacificToday(): { year: number; month: number; day: number } {
  const now = new Date()
  const fmt = (opt: Intl.DateTimeFormatOptions) =>
    parseInt(new Intl.DateTimeFormat('en-US', { timeZone: TZ, ...opt }).format(now))
  return { year: fmt({ year: 'numeric' }), month: fmt({ month: 'numeric' }), day: fmt({ day: 'numeric' }) }
}

/** Convert ISO string to Pacific date key "YYYY-MM-DD" */
function isoToPacificKey(iso: string): string {
  const d = new Date(iso)
  const y = new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric' }).format(d)
  const m = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: '2-digit' }).format(d)
  const day = new Intl.DateTimeFormat('en-US', { timeZone: TZ, day: '2-digit' }).format(d)
  return `${y}-${m}-${day}`
}

/** Date key for a year/month/day */
function dateKey(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

export function DatePicker({ initialStart, initialEnd, onApply, onClose }: DatePickerProps) {
  const today = useMemo(() => getPacificToday(), [])
  const todayKey = dateKey(today.year, today.month, today.day)

  // Initialize viewing month from initialStart or today
  const initMonth = useMemo(() => {
    if (initialStart) {
      const d = new Date(initialStart)
      return {
        year: parseInt(new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric' }).format(d)),
        month: parseInt(new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'numeric' }).format(d)),
      }
    }
    return { year: today.year, month: today.month }
  }, [initialStart, today])

  const [viewYear, setViewYear] = useState(initMonth.year)
  const [viewMonth, setViewMonth] = useState(initMonth.month)

  // Selection state: start and end as "YYYY-MM-DD" keys
  const [startKey, setStartKey] = useState<string | null>(
    initialStart ? isoToPacificKey(initialStart) : null
  )
  const [endKey, setEndKey] = useState<string | null>(
    initialEnd ? isoToPacificKey(initialEnd) : null
  )
  const [hoverKey, setHoverKey] = useState<string | null>(null)

  // Month navigation
  const prevMonth = useCallback(() => {
    setViewMonth(m => {
      if (m === 1) {
        setViewYear(y => y - 1)
        return 12
      }
      return m - 1
    })
  }, [])

  const nextMonth = useCallback(() => {
    // Don't navigate beyond current month
    if (viewYear === today.year && viewMonth === today.month) return
    setViewMonth(m => {
      if (m === 12) {
        setViewYear(y => y + 1)
        return 1
      }
      return m + 1
    })
  }, [viewYear, viewMonth, today])

  const canGoNext = viewYear < today.year || (viewYear === today.year && viewMonth < today.month)

  // Build calendar grid
  const calendarDays = useMemo(() => {
    const firstDay = new Date(viewYear, viewMonth - 1, 1).getDay() // 0=Sun
    const daysInMonth = new Date(viewYear, viewMonth, 0).getDate()
    const cells: { day: number; key: string; disabled: boolean }[] = []

    // Empty cells for days before the 1st
    for (let i = 0; i < firstDay; i++) {
      cells.push({ day: 0, key: `empty-${i}`, disabled: true })
    }

    for (let d = 1; d <= daysInMonth; d++) {
      const key = dateKey(viewYear, viewMonth, d)
      const isFuture = key > todayKey
      cells.push({ day: d, key, disabled: isFuture })
    }

    return cells
  }, [viewYear, viewMonth, todayKey])

  // Click a day
  const handleDayClick = useCallback(
    (key: string) => {
      if (!startKey || (startKey && endKey)) {
        // Start fresh selection
        setStartKey(key)
        setEndKey(null)
      } else {
        // Complete the range
        let s = startKey
        let e = key
        if (s > e) [s, e] = [e, s]
        setStartKey(s)
        setEndKey(e)
      }
    },
    [startKey, endKey]
  )

  // Determine if a day is in the preview range
  const getSelectionState = useCallback(
    (key: string): 'start' | 'end' | 'in-range' | 'preview' | null => {
      if (!startKey) return null

      if (endKey) {
        // Completed range
        if (key === startKey) return 'start'
        if (key === endKey) return 'end'
        if (key > startKey && key < endKey) return 'in-range'
        return null
      }

      // Single selection with hover preview
      if (key === startKey) return 'start'
      if (hoverKey && hoverKey !== startKey) {
        const lo = startKey < hoverKey ? startKey : hoverKey
        const hi = startKey < hoverKey ? hoverKey : startKey
        if (key > lo && key < hi) return 'preview'
        if (key === hoverKey) return 'preview'
      }
      return null
    },
    [startKey, endKey, hoverKey]
  )

  // Apply selection
  const handleApply = useCallback(() => {
    if (!startKey) return
    const effectiveEnd = endKey || startKey
    // Parse the date keys and emit ISO ranges
    const [sy, sm, sd] = startKey.split('-').map(Number)
    const [ey, em, ed] = effectiveEnd.split('-').map(Number)

    // Import toPacificISORange logic inline to avoid circular deps
    const rangeStart = toPacificISO(sy, sm, sd, true)
    const rangeEnd = toPacificISO(ey, em, ed, false)
    onApply(rangeStart, rangeEnd)
  }, [startKey, endKey, onApply])

  const monthLabel = new Date(viewYear, viewMonth - 1, 1).toLocaleDateString('en-US', {
    month: 'long',
    year: 'numeric',
  })

  const hasSelection = !!startKey

  return (
    <div className="date-picker" onClick={e => e.stopPropagation()}>
      {/* Month navigation */}
      <div className="date-picker-header">
        <button className="date-picker-nav" onClick={prevMonth} aria-label="Previous month">
          <ChevronLeft className="w-4 h-4" />
        </button>
        <span className="date-picker-month">{monthLabel}</span>
        <button
          className="date-picker-nav"
          onClick={nextMonth}
          disabled={!canGoNext}
          aria-label="Next month"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      {/* Weekday headers */}
      <div className="date-picker-grid">
        {WEEKDAYS.map(wd => (
          <div key={wd} className="date-picker-weekday">
            {wd}
          </div>
        ))}

        {/* Day cells */}
        {calendarDays.map(cell => {
          if (cell.day === 0) {
            return <div key={cell.key} className="date-picker-cell" />
          }

          const sel = getSelectionState(cell.key)
          const isToday = cell.key === todayKey
          const classes = [
            'date-picker-cell',
            'date-picker-day',
            cell.disabled && 'disabled',
            isToday && 'today',
            sel === 'start' && 'selected-start',
            sel === 'end' && 'selected-end',
            (sel === 'start' && !endKey && !hoverKey) && 'selected-single',
            sel === 'in-range' && 'in-range',
            sel === 'preview' && 'preview',
          ]
            .filter(Boolean)
            .join(' ')

          return (
            <button
              key={cell.key}
              className={classes}
              disabled={cell.disabled}
              onClick={() => handleDayClick(cell.key)}
              onMouseEnter={() => setHoverKey(cell.key)}
              onMouseLeave={() => setHoverKey(null)}
            >
              {cell.day}
            </button>
          )
        })}
      </div>

      {/* Footer */}
      <div className="date-picker-footer">
        <button className="date-picker-btn date-picker-btn-cancel" onClick={onClose}>
          Cancel
        </button>
        <button
          className="date-picker-btn date-picker-btn-apply"
          disabled={!hasSelection}
          onClick={handleApply}
        >
          Apply
        </button>
      </div>
    </div>
  )
}

/**
 * Convert a Pacific calendar date to a UTC ISO string.
 * @param isStart - if true, returns midnight; if false, returns 23:59:59.999
 */
function toPacificISO(year: number, month: number, day: number, isStart: boolean): string {
  // Probe the UTC offset for this date in Pacific Time
  const probe = new Date(Date.UTC(year, month - 1, day, 20, 0, 0))
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ,
    timeZoneName: 'shortOffset',
  }).formatToParts(probe)
  const offsetPart = parts.find(p => p.type === 'timeZoneName')?.value || 'GMT-8'
  const offsetMatch = offsetPart.match(/GMT([+-]?\d+)/)
  const offsetHours = offsetMatch ? parseInt(offsetMatch[1], 10) : -8

  if (isStart) {
    return new Date(Date.UTC(year, month - 1, day, -offsetHours, 0, 0)).toISOString()
  } else {
    const midnight = new Date(Date.UTC(year, month - 1, day, -offsetHours, 0, 0))
    return new Date(midnight.getTime() + 24 * 60 * 60 * 1000 - 1).toISOString()
  }
}
