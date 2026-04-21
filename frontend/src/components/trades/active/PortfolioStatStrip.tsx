import clsx from 'clsx'
import { RefreshCw, AlertTriangle, Wallet, Clock } from 'lucide-react'
import type { LiveTradesSummary } from '@/lib/types'
import { formatRelativeTime } from '@/lib/formatTime'
import { fmtPct, fmtUsd, pnlColor } from './format'

interface Props {
  summary: LiveTradesSummary | undefined
  isFetching: boolean
  onRefresh: () => void
}

export default function PortfolioStatStrip({ summary, isFetching, onRefresh }: Props) {
  const pnl = summary?.dollar_pnl_open_total ?? 0
  const pnlPct = summary?.pnl_pct_weighted ?? 0
  const premium = summary?.premium_at_risk_total ?? 0
  const openCount = summary?.open_count ?? 0
  const attentionCount = summary?.attention_count ?? 0
  const intraday = summary?.quote_sources?.intraday ?? 0
  const daily = summary?.quote_sources?.daily_batch ?? 0
  const snapshot = summary?.quote_sources?.snapshot ?? 0
  const paperClosed = summary?.paper_closed_count ?? 0
  const lastUpdated = summary?.last_updated

  const staleCount = daily + snapshot
  const hasQuoteDrift = staleCount > 0 && intraday > 0

  return (
    <div className="rounded-xl border border-oss-border bg-oss-card p-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        {/* Hero $ P&L */}
        <div className="flex-1 min-w-[240px]">
          <div className="flex items-center gap-2 text-oss-muted text-xs mb-1">
            <Wallet className="h-3.5 w-3.5" />
            OPEN P&L
          </div>
          <div className="flex items-baseline gap-3">
            <span
              className={clsx(
                'text-2xl font-bold font-mono tracking-tight',
                pnlColor(pnl)
              )}
            >
              {openCount === 0 ? '—' : fmtUsd(pnl, { sign: true })}
            </span>
            {openCount > 0 && (
              <span className={clsx('text-sm font-mono', pnlColor(pnlPct))}>
                {fmtPct(pnlPct, { sign: true })}
              </span>
            )}
          </div>
        </div>

        {/* Premium at risk */}
        <StatBlock
          label="Premium at Risk"
          value={openCount === 0 ? '—' : fmtUsd(premium)}
        />

        {/* Open count */}
        <StatBlock
          label="Open"
          value={openCount.toString()}
          sub={
            attentionCount > 0 ? (
              <span className="inline-flex items-center gap-1 text-oss-watch-text">
                <AlertTriangle className="h-3 w-3" />
                {attentionCount} need{attentionCount === 1 ? 's' : ''} attention
              </span>
            ) : paperClosed > 0 ? (
              <span
                className="inline-flex items-center gap-1 text-oss-watch-text"
                title="System auto-closed the paper position for these trades — decide whether to close manually"
              >
                <AlertTriangle className="h-3 w-3" />
                {paperClosed} system-closed
              </span>
            ) : openCount > 0 ? (
              <span className="text-oss-muted">book is quiet</span>
            ) : null
          }
        />

        {/* Refresh controls */}
        <div className="flex flex-col items-end gap-1 text-xs">
          <button
            onClick={onRefresh}
            disabled={isFetching}
            className={clsx(
              'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5',
              'border border-oss-border bg-oss-surface text-oss-text-secondary',
              'hover:bg-oss-hover hover:border-oss-border-active',
              'disabled:opacity-50 disabled:cursor-not-allowed transition-colors'
            )}
            title="Refresh quotes"
          >
            <RefreshCw
              className={clsx('h-3.5 w-3.5', isFetching && 'animate-spin')}
            />
            Refresh
          </button>
          {lastUpdated && (
            <span className="inline-flex items-center gap-1 text-oss-muted">
              <Clock className="h-3 w-3" />
              {formatRelativeTime(lastUpdated)}
            </span>
          )}
          {hasQuoteDrift && (
            <span
              className="text-oss-muted italic"
              title={`${intraday} live quote${intraday === 1 ? '' : 's'}, ${staleCount} stale`}
            >
              {staleCount} stale
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

function StatBlock({
  label,
  value,
  sub,
}: {
  label: string
  value: string
  sub?: React.ReactNode
}) {
  return (
    <div className="min-w-[120px]">
      <div className="text-xs text-oss-muted mb-1">{label}</div>
      <div className="text-lg font-semibold font-mono text-oss-text">{value}</div>
      {sub && <div className="text-xs mt-0.5">{sub}</div>}
    </div>
  )
}
