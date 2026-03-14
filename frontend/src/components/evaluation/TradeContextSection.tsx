import { useTradeContext } from '@/hooks/useApi'
import type { DimensionStats, TradeContextResponse } from '@/lib/types'
import clsx from 'clsx'
import { BarChart3 } from 'lucide-react'

function fmt(val: number | null, decimals = 1): string {
  if (val == null) return '—'
  return val.toFixed(decimals)
}

function wrColor(wr: number | null): string {
  if (wr == null) return 'text-oss-muted'
  if (wr >= 60) return 'text-oss-approve'
  if (wr >= 50) return 'text-oss-watch'
  return 'text-oss-reject'
}

function BroaderSlice({ label, stats }: { label: string; stats: DimensionStats | null }) {
  if (!stats || stats.closed < 1) return null
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-oss-muted">{label}</span>
      <span className="flex items-center gap-2">
        <span className={clsx('font-mono font-medium', wrColor(stats.win_rate))}>
          {stats.win_rate != null ? `${fmt(stats.win_rate, 0)}% WR` : '—'}
        </span>
        <span className="text-oss-muted">({stats.closed} trades)</span>
      </span>
    </div>
  )
}

function borderColor(flag: TradeContextResponse['confidence_flag']): string {
  switch (flag) {
    case 'high_confidence':
      return 'border-l-oss-approve'
    case 'moderate':
      return 'border-l-oss-accent'
    case 'low_sample':
      return 'border-l-oss-watch'
    default:
      return 'border-l-oss-muted'
  }
}

interface TradeContextSectionProps {
  optionType: string
  scanner?: string
  score?: number
  dteBucket?: string
}

export default function TradeContextSection({
  optionType,
  scanner,
  score,
  dteBucket,
}: TradeContextSectionProps) {
  const { data, isLoading, error } = useTradeContext({
    option_type: optionType,
    scanner,
    score,
    dte_bucket: dteBucket,
  })

  if (isLoading) {
    return (
      <div className="h-24 animate-pulse rounded-xl border border-oss-border bg-oss-surface" />
    )
  }

  if (error || !data) return null

  if (data.confidence_flag === 'insufficient') {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-5 border-l-4 border-l-oss-muted">
        <div className="flex items-center gap-2 mb-2">
          <BarChart3 className="h-4 w-4 text-oss-muted" />
          <h3 className="text-sm font-medium text-oss-text">Trades Like This One</h3>
        </div>
        <p className="text-xs text-oss-muted">
          Not enough closed trades yet for historical context
        </p>
      </div>
    )
  }

  const exact = data.exact_match

  return (
    <div
      className={clsx(
        'rounded-xl border border-oss-border bg-oss-surface p-5 border-l-4',
        borderColor(data.confidence_flag)
      )}
    >
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 className="h-4 w-4 text-oss-accent" />
        <h3 className="text-sm font-medium text-oss-text">Trades Like This One</h3>
        {data.confidence_flag === 'low_sample' && (
          <span className="text-[10px] text-oss-watch bg-oss-watch/10 px-1.5 py-0.5 rounded">
            Limited sample
          </span>
        )}
      </div>

      {/* Main summary */}
      <p className="text-sm text-oss-text mb-3">{data.summary}</p>

      {/* Exact match details */}
      {exact && exact.closed >= 3 && (
        <div className="rounded-lg bg-oss-bg p-3 mb-3">
          <p className="text-xs text-oss-muted mb-2">{data.exact_match_description}</p>
          <div className="flex items-center gap-6">
            <div>
              <span className={clsx('font-mono text-lg font-bold', wrColor(exact.win_rate))}>
                {exact.win_rate != null ? `${fmt(exact.win_rate, 0)}%` : '—'}
              </span>
              <span className="text-[10px] text-oss-muted ml-1">WR</span>
            </div>
            <div className="text-xs">
              <span className="text-oss-muted">Avg Return: </span>
              <span className="font-mono text-oss-text">{fmt(exact.avg_return)}%</span>
            </div>
            <div className="text-xs">
              <span className="text-oss-muted">Avg Days: </span>
              <span className="font-mono text-oss-text">{fmt(exact.avg_days_held)}</span>
            </div>
            <div className="text-xs">
              <span className="text-oss-muted">W/L: </span>
              <span className="font-mono text-oss-text">
                {exact.wins}/{exact.losses}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Broader context */}
      <div className="space-y-1.5">
        <p className="text-[10px] text-oss-muted uppercase tracking-wider">Broader context</p>
        <BroaderSlice label={`All ${optionType}s`} stats={data.by_option_type} />
        <BroaderSlice
          label={`All ${(scanner || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}`}
          stats={data.by_scanner}
        />
        <BroaderSlice
          label={data.by_score_range?.label ? `Score ${data.by_score_range.label}` : 'Score bucket'}
          stats={data.by_score_range}
        />
      </div>
    </div>
  )
}
