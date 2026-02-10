/**
 * KPI strip displaying 6 stat cards for paper trading metrics.
 */

import clsx from 'clsx'

interface KPIStripProps {
  totalPnl: number
  winRate: number | null
  avgReturn: number
  avgScore: number | null
  bestTrade: { ticker: string; pnl: number } | null
  activeCount: number
}

function fmt(val: number | null, decimals: number = 1): string {
  if (val == null) return '--'
  return val.toFixed(decimals)
}

interface StatCardProps {
  label: string
  value: string
  sub?: string
  color?: 'green' | 'red' | 'cyan' | 'default'
}

function StatCard({ label, value, sub, color = 'default' }: StatCardProps) {
  const colorClass = {
    green: 'text-oss-approve',
    red: 'text-oss-reject',
    cyan: 'text-oss-accent',
    default: 'text-oss-text',
  }[color]

  return (
    <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
      <div className="text-xs text-oss-muted uppercase tracking-wider mb-1">{label}</div>
      <div className={clsx('text-xl font-semibold', colorClass)}>{value}</div>
      {sub && <div className="text-xs text-oss-muted mt-0.5">{sub}</div>}
    </div>
  )
}

export default function KPIStrip({
  totalPnl,
  winRate,
  avgReturn,
  avgScore,
  bestTrade,
  activeCount,
}: KPIStripProps) {
  const pnlColor = totalPnl >= 0 ? 'green' : 'red'
  const avgColor = avgReturn >= 0 ? 'green' : 'red'

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      <StatCard
        label="Paper P&L"
        value={`$${fmt(totalPnl, 0)}`}
        color={pnlColor}
      />
      <StatCard
        label="Win Rate"
        value={winRate != null ? `${fmt(winRate)}%` : '--'}
        sub="Closed positions"
        color={winRate != null && winRate >= 55 ? 'green' : winRate != null ? 'red' : 'default'}
      />
      <StatCard
        label="Avg Return"
        value={`${fmt(avgReturn)}%`}
        color={avgColor}
      />
      <StatCard
        label="Avg Score"
        value={avgScore != null ? fmt(avgScore, 0) : '--'}
        color="cyan"
      />
      <StatCard
        label="Best Trade"
        value={bestTrade ? `+${fmt(bestTrade.pnl)}%` : '--'}
        sub={bestTrade?.ticker ?? ''}
        color="green"
      />
      <StatCard
        label="Active"
        value={String(activeCount)}
        sub="Open positions"
        color="cyan"
      />
    </div>
  )
}
