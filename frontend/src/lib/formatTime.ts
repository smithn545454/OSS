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
