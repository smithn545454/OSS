/**
 * Performance Breakdown section.
 * Shows win rate and avg return sliced by option type, scanner, and score bucket.
 * Date range selector controls the lookback period (default 5 trading days).
 */

import { useState } from 'react'
import clsx from 'clsx'
import { usePerformanceBreakdown } from '@/hooks/useApi'
import type { PerformanceBreakdownBucket } from '@/lib/types'

const PERIOD_OPTIONS = [
  { label: '5d', days: 5 },
  { label: '10d', days: 10 },
  { label: '20d', days: 20 },
  { label: 'ALL', days: 9999 },
] as const

const SCANNER_NAMES: Record<string, string> = {
  BREAKOUT: 'Breakout',
  BREAKDOWN: 'Breakdown',
  UNUSUAL_VOLUME: 'Unusual Vol',
  UNUSUAL_VOLUME_SCANNER: 'Unusual Vol',
  COMPRESSION_EXPANSION: 'Compression',
  CHEAP_OPTIONS: 'Cheap Opts',
  UNKNOWN: 'Unknown',
}

function fmt(val: number | null, suffix = '%'): string {
  if (val == null) return '--'
  return `${val.toFixed(1)}${suffix}`
}

function BreakdownTable({
  title,
  data,
}: {
  title: string
  data: Record<string, PerformanceBreakdownBucket>
}) {
  const entries = Object.entries(data)
  if (entries.length === 0) {
    return (
      <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
        <h4 className="text-xs font-medium text-oss-text mb-3">{title}</h4>
        <div className="text-xs text-oss-muted text-center py-4">No data</div>
      </div>
    )
  }

  // Find best win rate for highlighting
  const bestWinRate = Math.max(
    ...entries.map(([, b]) => b.win_rate ?? -Infinity)
  )

  return (
    <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
      <h4 className="text-xs font-medium text-oss-text mb-3">{title}</h4>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-oss-muted border-b border-oss-border">
            <th className="text-left py-1.5 font-medium">Category</th>
            <th className="text-right py-1.5 font-medium">Count</th>
            <th className="text-right py-1.5 font-medium">Closed</th>
            <th className="text-right py-1.5 font-medium">Win Rate</th>
            <th className="text-right py-1.5 font-medium">Avg Return</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([key, bucket]) => {
            const displayName =
              title === 'By Scanner' ? (SCANNER_NAMES[key] || key) : key
            const isBest =
              bucket.win_rate != null &&
              bucket.win_rate === bestWinRate &&
              bucket.closed > 0
            return (
              <tr
                key={key}
                className={clsx(
                  'border-b border-oss-border/50 last:border-0',
                  isBest && 'bg-oss-approve/5'
                )}
              >
                <td className="py-1.5 text-oss-text font-medium">
                  {displayName}
                </td>
                <td className="py-1.5 text-right text-oss-muted">
                  {bucket.count}
                </td>
                <td className="py-1.5 text-right text-oss-muted">
                  {bucket.closed}
                </td>
                <td
                  className={clsx(
                    'py-1.5 text-right font-medium',
                    bucket.win_rate == null
                      ? 'text-oss-muted'
                      : bucket.win_rate >= 50
                        ? 'text-oss-approve'
                        : 'text-oss-reject'
                  )}
                >
                  {fmt(bucket.win_rate)}
                </td>
                <td
                  className={clsx(
                    'py-1.5 text-right font-medium',
                    bucket.avg_return == null
                      ? 'text-oss-muted'
                      : bucket.avg_return >= 0
                        ? 'text-oss-approve'
                        : 'text-oss-reject'
                  )}
                >
                  {fmt(bucket.avg_return)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function PerformanceBreakdown() {
  const [days, setDays] = useState(5)
  const { data, isLoading } = usePerformanceBreakdown(days)

  return (
    <div className="space-y-4">
      {/* Header with date range selector */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-oss-text">
          Performance Breakdown
        </h3>
        <div className="flex gap-1">
          {PERIOD_OPTIONS.map((opt) => (
            <button
              key={opt.days}
              onClick={() => setDays(opt.days)}
              className={clsx(
                'px-2.5 py-1 text-xs font-medium rounded transition-colors',
                days === opt.days
                  ? 'bg-oss-accent text-white'
                  : 'text-oss-muted hover:text-oss-text hover:bg-oss-border/50'
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Period summary */}
      {data && (
        <div className="text-xs text-oss-muted">
          {data.period.start} to {data.period.end} — {data.total_positions}{' '}
          positions, {data.total_closed} closed
        </div>
      )}

      {isLoading ? (
        <div className="h-32 flex items-center justify-center text-oss-muted text-sm">
          Loading breakdown...
        </div>
      ) : data ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <BreakdownTable title="Call vs Put" data={data.by_option_type} />
          <BreakdownTable title="By Scanner" data={data.by_scanner} />
          <BreakdownTable title="By Score Bucket" data={data.by_score_bucket} />
        </div>
      ) : null}
    </div>
  )
}
