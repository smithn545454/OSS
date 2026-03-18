const TZ = 'America/Los_Angeles'

/** "02/13/2026 19:40:00 PT" */
export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return (
    date.toLocaleString('en-US', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
      timeZone: TZ,
    }) + ' PT'
  )
}

/** "Feb 13, 2026" */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: TZ,
  })
}

/** "7:40 PM" */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: TZ,
  })
}

/**
 * Format expiration dates (pure calendar dates like "2026-03-21").
 * Parses as local date to avoid the UTC midnight → previous-day-in-Pacific bug.
 */
export function formatExpirationDate(dateStr: string): string {
  const [year, month, day] = dateStr.split('-').map(Number)
  const date = new Date(year, month - 1, day)
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

/**
 * Convert a calendar date (year/month/day) to midnight-to-end-of-day
 * Pacific Time boundaries as UTC ISO strings.
 * Handles PST (-08:00) vs PDT (-07:00) automatically.
 */
export function toPacificISORange(
  year: number,
  month: number,
  day: number
): { start: string; end: string } {
  // Build a date at noon Pacific to determine the UTC offset for that day
  const probe = new Date(Date.UTC(year, month - 1, day, 20, 0, 0)) // ~noon PT
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ,
    timeZoneName: 'shortOffset',
  }).formatToParts(probe)
  const offsetPart = parts.find(p => p.type === 'timeZoneName')?.value || 'GMT-8'
  const offsetMatch = offsetPart.match(/GMT([+-]?\d+)/)
  const offsetHours = offsetMatch ? parseInt(offsetMatch[1], 10) : -8

  // Midnight Pacific = midnight - offset in UTC
  const startUtc = new Date(Date.UTC(year, month - 1, day, -offsetHours, 0, 0))
  // End of day = start + 24 hours - 1 millisecond
  const endUtc = new Date(startUtc.getTime() + 24 * 60 * 60 * 1000 - 1)

  return {
    start: startUtc.toISOString(),
    end: endUtc.toISOString(),
  }
}

/**
 * Relative time for evaluation age: "Just now" / "5m ago" / "2h ago" /
 * "Today 2:15 PM" / "Yesterday 9:30 AM" / "Mar 10".
 */
export function formatRelativeTime(iso: string): string {
  const now = new Date()
  const then = new Date(iso)
  const diffMs = now.getTime() - then.getTime()
  const diffMin = Math.floor(diffMs / 60_000)
  const diffHr = Math.floor(diffMs / 3_600_000)

  if (diffMin < 2) return 'Just now'
  if (diffMin < 60) return `${diffMin}m ago`
  if (diffHr < 6) return `${diffHr}h ago`

  // Same calendar day in Pacific?
  const dayFmt = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  const nowDay = dayFmt.format(now)
  const thenDay = dayFmt.format(then)
  if (nowDay === thenDay) return `Today ${formatTime(iso)}`

  // Yesterday?
  const yesterday = new Date(now.getTime() - 86_400_000)
  if (dayFmt.format(yesterday) === thenDay) return `Yesterday ${formatTime(iso)}`

  return formatDateShort(iso)
}

/**
 * Freshness tier for an evaluation timestamp.
 * "fresh" = within 30 min, "recent" = 30-60 min, null = older.
 */
export function getAgeFreshness(iso: string): 'fresh' | 'recent' | null {
  const diffMs = Date.now() - new Date(iso).getTime()
  const diffMin = diffMs / 60_000
  if (diffMin <= 30) return 'fresh'
  if (diffMin <= 60) return 'recent'
  return null
}

/**
 * Compact date display: "Feb 13" (current year) or "Feb 13, 2025" (other year).
 */
export function formatDateShort(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  const pacificYear = parseInt(
    new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric' }).format(now)
  )
  const dateYear = parseInt(
    new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric' }).format(date)
  )

  const opts: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
    timeZone: TZ,
  }
  if (dateYear !== pacificYear) {
    opts.year = 'numeric'
  }
  return date.toLocaleDateString('en-US', opts)
}
