import { useMarketContext } from '@/hooks/useApi'
import type { MarketContext, MarketStatus } from '@/lib/types'
import clsx from 'clsx'

const STATUS_LABELS: Record<MarketStatus, string> = {
  pre: 'Pre-Market',
  open: 'Market Open',
  after: 'After Hours',
  closed: 'Closed',
}

function formatPipelineTime(timestamp: string | null): string {
  if (!timestamp) return 'Never'
  const diffMs = Date.now() - new Date(timestamp).getTime()
  const diffMins = Math.floor(diffMs / 60000)
  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  return new Date(timestamp).toLocaleDateString()
}

function MarketBadge({ status }: { status: MarketStatus }) {
  const isOpen = status === 'open'
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs font-semibold shrink-0',
        isOpen
          ? 'bg-[rgba(16,185,129,0.15)] text-[var(--color-success-text)] border border-[var(--color-success)]'
          : 'bg-[var(--bg-tertiary)] text-[var(--text-muted)] border border-[var(--border-default)]',
      )}
    >
      {isOpen && <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-success)] animate-pulse" />}
      {STATUS_LABELS[status]}
    </span>
  )
}

function SPY({ data }: { data: MarketContext['spy'] }) {
  const up = data.changePercent >= 0
  return (
    <div className="flex items-center gap-1.5 text-sm font-mono">
      <span className="text-[var(--text-muted)] text-xs">SPY</span>
      <span className="font-semibold">${data.price.toFixed(2)}</span>
      <span
        className="font-medium text-xs"
        style={{ color: up ? 'var(--color-success-text)' : 'var(--color-error-text)' }}
      >
        {up ? '+' : ''}{data.changePercent.toFixed(2)}%
      </span>
    </div>
  )
}

function VIX({ data }: { data: MarketContext['vix'] }) {
  const color =
    data.price >= 35 ? 'var(--color-error-text)' :
    data.price >= 25 ? 'var(--color-warning-text)' :
    'var(--text-secondary)'
  return (
    <div className="flex items-center gap-1.5 text-sm font-mono">
      <span className="text-[var(--text-muted)] text-xs">VIX</span>
      <span className="font-semibold" style={{ color }}>{data.price.toFixed(2)}</span>
    </div>
  )
}

export function OppContextBar() {
  const { data, isLoading, error } = useMarketContext()

  if (isLoading) {
    return (
      <div className="flex items-center px-3 py-2 bg-[var(--bg-tertiary)] rounded-lg border border-[var(--border-default)] text-sm text-[var(--text-muted)]">
        Loading market data...
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex items-center px-3 py-2 bg-[var(--bg-tertiary)] rounded-lg border border-[var(--border-default)] text-sm text-[var(--text-muted)]">
        Unable to load market data
      </div>
    )
  }

  const pipelineTime = formatPipelineTime(data.lastPipelineRun)
  const isStale = data.lastPipelineRun
    ? (Date.now() - new Date(data.lastPipelineRun).getTime()) >= 30 * 60000
    : true

  return (
    <div
      className="flex items-center gap-3 sm:gap-4 px-3 py-2 bg-[var(--bg-tertiary)] rounded-lg border border-[var(--border-default)] flex-wrap"
      role="status"
      aria-label="Market context"
    >
      <MarketBadge status={data.marketStatus} />
      <SPY data={data.spy} />
      <VIX data={data.vix} />
      <div className="hidden sm:flex items-center gap-1.5 ml-auto text-xs text-[var(--text-muted)]">
        <span>Pipeline</span>
        <span className={clsx('font-medium', isStale ? 'text-[var(--color-warning-text)]' : 'text-[var(--color-success-text)]')}>
          {pipelineTime}
        </span>
      </div>
    </div>
  )
}
