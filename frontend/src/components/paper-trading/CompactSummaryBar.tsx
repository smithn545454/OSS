/**
 * Compact summary bar replacing KPIStrip.
 * Single-row inline metrics with vertical dividers.
 */

import clsx from 'clsx'

interface CompactSummaryBarProps {
  totalTrades: number
  winRate: number | null
  avgReturn: number
  bestTrade: { ticker: string; pnl: number } | null
  dateRange?: string
}

function fmt(val: number | null, decimals: number = 1): string {
  if (val == null) return '--'
  return val.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function winRateColor(rate: number | null): string {
  if (rate == null) return 'text-oss-muted'
  if (rate >= 60) return 'text-oss-approve'
  if (rate >= 50) return 'text-yellow-400'
  return 'text-oss-reject'
}

export default function CompactSummaryBar({
  totalTrades,
  winRate,
  avgReturn,
  bestTrade,
  dateRange,
}: CompactSummaryBarProps) {
  return (
    <div className="flex items-center gap-4 rounded-lg border border-oss-border bg-oss-surface px-4 py-2.5 text-sm">
      <div className="flex items-center gap-1.5">
        <span className="text-oss-muted">Trades:</span>
        <span className="font-mono font-medium text-oss-text">{totalTrades}</span>
      </div>

      <div className="h-4 w-px bg-oss-border" />

      <div className="flex items-center gap-1.5">
        <span className="text-oss-muted">Win Rate:</span>
        <span className={clsx('font-mono font-medium', winRateColor(winRate))}>
          {winRate != null ? `${fmt(winRate)}%` : '--'}
        </span>
      </div>

      <div className="h-4 w-px bg-oss-border" />

      <div className="flex items-center gap-1.5">
        <span className="text-oss-muted">Avg Return:</span>
        <span
          className={clsx(
            'font-mono font-medium',
            avgReturn >= 0 ? 'text-oss-approve' : 'text-oss-reject'
          )}
        >
          {avgReturn >= 0 ? '+' : ''}
          {fmt(avgReturn)}%
        </span>
      </div>

      <div className="h-4 w-px bg-oss-border" />

      <div className="flex items-center gap-1.5">
        <span className="text-oss-muted">Best:</span>
        <span className="font-mono font-medium text-oss-approve">
          {bestTrade ? `+${fmt(bestTrade.pnl)}%` : '--'}
        </span>
        {bestTrade && (
          <span className="text-xs text-oss-muted">({bestTrade.ticker})</span>
        )}
      </div>

      {dateRange && (
        <>
          <div className="h-4 w-px bg-oss-border" />
          <span className="text-xs text-oss-muted">{dateRange}</span>
        </>
      )}
    </div>
  )
}
