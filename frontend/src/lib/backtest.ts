import type { BacktestRun } from './types'

/**
 * Format a backtest run's date range as a human-readable label.
 *
 * Reads start_date/end_date from the run's config object and formats them
 * as "Jan 2, 2024 – Jan 31, 2024". Falls back to truncated run_id for
 * legacy runs without config dates.
 */
export function formatRunDateRange(run: BacktestRun): string {
  const cfg = run.config
  const startStr = cfg?.start_date as string | undefined
  const endStr = cfg?.end_date as string | undefined
  if (startStr && endStr) {
    const fmt = (iso: string) => {
      const d = new Date(iso + 'T00:00:00')
      return d.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    }
    return `${fmt(startStr)} – ${fmt(endStr)}`
  }
  return run.run_id.slice(0, 8)
}
