import { useState } from 'react'
import { useEdgeBriefing } from '@/hooks/useApi'
import type { DimensionStats, EdgeInsight } from '@/lib/types'
import clsx from 'clsx'
import { Brain, TrendingUp, TrendingDown, Zap, Target, Layers, Clock, Lightbulb } from 'lucide-react'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(val: number | null, decimals = 1): string {
  if (val == null) return '—'
  return val.toFixed(decimals)
}

function fmtDollars(val: number | null): string {
  if (val == null) return '—'
  const sign = val >= 0 ? '+' : ''
  return `${sign}$${val.toFixed(0)}`
}

function wrColor(wr: number | null): string {
  if (wr == null) return 'text-oss-muted'
  if (wr >= 60) return 'text-oss-approve'
  if (wr >= 50) return 'text-oss-watch'
  return 'text-oss-reject'
}

// ---------------------------------------------------------------------------
// Period Selector
// ---------------------------------------------------------------------------

function PeriodSelector({
  value,
  onChange,
}: {
  value: number
  onChange: (days: number) => void
}) {
  const options = [5, 10, 20]
  return (
    <div className="flex gap-1 rounded-lg bg-oss-bg p-1">
      {options.map((d) => (
        <button
          key={d}
          onClick={() => onChange(d)}
          className={clsx(
            'rounded-md px-3 py-1 text-xs font-medium transition-colors',
            value === d
              ? 'bg-oss-accent/15 text-oss-accent'
              : 'text-oss-muted hover:text-oss-text'
          )}
        >
          {d}d
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stat Card
// ---------------------------------------------------------------------------

function StatCard({
  label,
  value,
  subtext,
  color,
}: {
  label: string
  value: string
  subtext?: string
  color?: string
}) {
  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-4">
      <p className="text-xs text-oss-muted mb-1">{label}</p>
      <p className={clsx('font-mono text-2xl font-bold', color || 'text-oss-text')}>{value}</p>
      {subtext && <p className="text-xs text-oss-muted mt-1">{subtext}</p>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Dimension Table
// ---------------------------------------------------------------------------

function DimensionTable({
  title,
  icon: Icon,
  data,
  highlightKey,
}: {
  title: string
  icon: React.ElementType
  data: Record<string, DimensionStats>
  highlightKey?: string | null
}) {
  const entries = Object.entries(data).filter(([k]) => k !== 'UNKNOWN')

  if (entries.length === 0) {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
        <div className="flex items-center gap-2 mb-3">
          <Icon className="h-4 w-4 text-oss-muted" />
          <h3 className="text-sm font-medium text-oss-text">{title}</h3>
        </div>
        <p className="text-xs text-oss-muted">No data</p>
      </div>
    )
  }

  // Sort by win rate descending (null last)
  const sorted = [...entries].sort((a, b) => {
    const aWr = a[1].win_rate ?? -1
    const bWr = b[1].win_rate ?? -1
    return bWr - aWr
  })

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <div className="flex items-center gap-2 mb-4">
        <Icon className="h-4 w-4 text-oss-accent" />
        <h3 className="text-sm font-medium text-oss-text">{title}</h3>
      </div>
      <div className="space-y-2">
        {sorted.map(([key, stats]) => {
          const isHighlight = key === highlightKey
          return (
            <div
              key={key}
              className={clsx(
                'flex items-center justify-between rounded-lg px-3 py-2.5',
                isHighlight ? 'bg-oss-accent/8 border border-oss-accent/20' : 'bg-oss-bg'
              )}
            >
              <div className="flex items-center gap-3">
                <span className="text-sm font-medium text-oss-text min-w-[80px]">
                  {key.replace(/_/g, ' ')}
                </span>
                <span className="text-xs text-oss-muted">
                  {stats.closed} closed
                </span>
              </div>
              <div className="flex items-center gap-4">
                {/* Win Rate */}
                <div className="text-right min-w-[60px]">
                  <span className={clsx('font-mono text-sm font-semibold', wrColor(stats.win_rate))}>
                    {stats.win_rate != null ? `${fmt(stats.win_rate, 0)}%` : '—'}
                  </span>
                  <span className="text-[10px] text-oss-muted ml-1">WR</span>
                </div>
                {/* Avg Return */}
                <div className="text-right min-w-[60px]">
                  <span
                    className={clsx(
                      'font-mono text-xs',
                      stats.avg_return != null && stats.avg_return > 0
                        ? 'text-oss-approve'
                        : stats.avg_return != null && stats.avg_return < 0
                          ? 'text-oss-reject'
                          : 'text-oss-muted'
                    )}
                  >
                    {stats.avg_return != null ? `${fmt(stats.avg_return)}%` : '—'}
                  </span>
                  <span className="text-[10px] text-oss-muted ml-1">avg</span>
                </div>
                {/* Win Rate Bar */}
                <div className="w-16 h-2 rounded-full bg-oss-bg overflow-hidden">
                  <div
                    className={clsx(
                      'h-full rounded-full transition-all',
                      stats.win_rate != null && stats.win_rate >= 60
                        ? 'bg-oss-approve'
                        : stats.win_rate != null && stats.win_rate >= 50
                          ? 'bg-oss-watch'
                          : 'bg-oss-reject'
                    )}
                    style={{ width: `${Math.min(stats.win_rate ?? 0, 100)}%` }}
                  />
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Calls vs Puts Comparison
// ---------------------------------------------------------------------------

function OptionTypeComparison({ data, edge }: {
  data: Record<string, DimensionStats>
  edge: string | null
}) {
  const callStats = data['CALL']
  const putStats = data['PUT']

  if (!callStats && !putStats) {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
        <h3 className="text-sm font-medium text-oss-text mb-3">Calls vs Puts</h3>
        <p className="text-xs text-oss-muted">No data</p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <div className="flex items-center gap-2 mb-4">
        <TrendingUp className="h-4 w-4 text-oss-accent" />
        <h3 className="text-sm font-medium text-oss-text">Calls vs Puts</h3>
        {edge && (
          <span className="text-[10px] font-medium rounded-full px-2 py-0.5 bg-oss-accent/10 text-oss-accent">
            {edge} edge
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-4">
        {/* CALL */}
        <div
          className={clsx(
            'rounded-lg p-4 border',
            edge === 'CALL'
              ? 'border-emerald-400/30 bg-emerald-400/5'
              : 'border-oss-border bg-oss-bg'
          )}
        >
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="h-4 w-4 text-emerald-400" />
            <span className="text-sm font-medium text-emerald-400">CALLS</span>
          </div>
          {callStats ? (
            <div className="space-y-2">
              <div>
                <span className={clsx('font-mono text-2xl font-bold', wrColor(callStats.win_rate))}>
                  {callStats.win_rate != null ? `${fmt(callStats.win_rate, 0)}%` : '—'}
                </span>
                <span className="text-xs text-oss-muted ml-1">WR</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <p className="text-oss-muted">Avg Return</p>
                  <p className="font-mono text-oss-text">{fmt(callStats.avg_return)}%</p>
                </div>
                <div>
                  <p className="text-oss-muted">Trades</p>
                  <p className="font-mono text-oss-text">{callStats.closed}</p>
                </div>
                <div>
                  <p className="text-oss-muted">Total P&L</p>
                  <p className="font-mono text-oss-text">{fmtDollars(callStats.total_pnl_dollars)}</p>
                </div>
                <div>
                  <p className="text-oss-muted">Avg Days</p>
                  <p className="font-mono text-oss-text">{fmt(callStats.avg_days_held)}</p>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-oss-muted">No call trades</p>
          )}
        </div>

        {/* PUT */}
        <div
          className={clsx(
            'rounded-lg p-4 border',
            edge === 'PUT'
              ? 'border-rose-400/30 bg-rose-400/5'
              : 'border-oss-border bg-oss-bg'
          )}
        >
          <div className="flex items-center gap-2 mb-3">
            <TrendingDown className="h-4 w-4 text-rose-400" />
            <span className="text-sm font-medium text-rose-400">PUTS</span>
          </div>
          {putStats ? (
            <div className="space-y-2">
              <div>
                <span className={clsx('font-mono text-2xl font-bold', wrColor(putStats.win_rate))}>
                  {putStats.win_rate != null ? `${fmt(putStats.win_rate, 0)}%` : '—'}
                </span>
                <span className="text-xs text-oss-muted ml-1">WR</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <p className="text-oss-muted">Avg Return</p>
                  <p className="font-mono text-oss-text">{fmt(putStats.avg_return)}%</p>
                </div>
                <div>
                  <p className="text-oss-muted">Trades</p>
                  <p className="font-mono text-oss-text">{putStats.closed}</p>
                </div>
                <div>
                  <p className="text-oss-muted">Total P&L</p>
                  <p className="font-mono text-oss-text">{fmtDollars(putStats.total_pnl_dollars)}</p>
                </div>
                <div>
                  <p className="text-oss-muted">Avg Days</p>
                  <p className="font-mono text-oss-text">{fmt(putStats.avg_days_held)}</p>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-oss-muted">No put trades</p>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Insights Banner
// ---------------------------------------------------------------------------

function InsightsBanner({ insights }: { insights: EdgeInsight[] }) {
  if (insights.length === 0) return null

  return (
    <div className="rounded-xl border border-oss-accent/20 bg-oss-accent/5 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Lightbulb className="h-4 w-4 text-oss-accent" />
        <h3 className="text-sm font-medium text-oss-accent">Key Insights</h3>
      </div>
      <div className="space-y-2">
        {insights.map((insight, i) => (
          <div key={i} className="flex items-start gap-2">
            <span
              className={clsx(
                'mt-0.5 h-2 w-2 rounded-full shrink-0',
                insight.strength === 'strong' ? 'bg-oss-approve' : 'bg-oss-watch'
              )}
            />
            <div>
              <p className="text-sm text-oss-text">{insight.headline}</p>
              {insight.detail && (
                <p className="text-xs text-oss-muted mt-0.5">{insight.detail}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Intelligence Page
// ---------------------------------------------------------------------------

export default function Intelligence() {
  const [days, setDays] = useState(10)
  const { data, isLoading, error } = useEdgeBriefing(days)

  return (
    <div className="flex flex-col gap-6 min-h-full">
      {/* Header */}
      <header className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <Brain className="h-6 w-6 text-oss-accent" />
            <h1 className="text-xl sm:text-2xl font-bold text-oss-text tracking-tight">
              Intelligence
            </h1>
          </div>
          <p className="text-sm text-oss-muted mt-1">
            Rolling-window performance analytics for pre-trade context
          </p>
        </div>
        <PeriodSelector value={days} onChange={setDays} />
      </header>

      {/* Loading */}
      {isLoading && (
        <div className="space-y-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-32 animate-pulse rounded-xl border border-oss-border bg-oss-surface" />
          ))}
        </div>
      )}

      {/* Error */}
      {error && !isLoading && (
        <div className="rounded-xl border border-oss-reject/30 bg-oss-reject/5 p-6 text-center">
          <p className="text-oss-text">Failed to load edge intelligence</p>
          <p className="text-sm text-oss-muted mt-2">
            {error instanceof Error ? error.message : 'Unknown error'}
          </p>
        </div>
      )}

      {/* Content */}
      {data && !isLoading && (
        <>
          {/* Headline Stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="Win Rate"
              value={data.overall_win_rate != null ? `${fmt(data.overall_win_rate, 0)}%` : '—'}
              subtext={`${data.total_closed} closed trades`}
              color={wrColor(data.overall_win_rate)}
            />
            <StatCard
              label="Avg Return"
              value={data.overall_avg_return != null ? `${fmt(data.overall_avg_return)}%` : '—'}
              color={
                data.overall_avg_return != null && data.overall_avg_return > 0
                  ? 'text-oss-approve'
                  : data.overall_avg_return != null && data.overall_avg_return < 0
                    ? 'text-oss-reject'
                    : 'text-oss-muted'
              }
            />
            <StatCard
              label="Expectancy"
              value={data.overall_expectancy != null ? fmt(data.overall_expectancy) : '—'}
              subtext={data.overall_expectancy != null && data.overall_expectancy > 0 ? 'Positive edge' : undefined}
              color={
                data.overall_expectancy != null && data.overall_expectancy > 0
                  ? 'text-oss-approve'
                  : 'text-oss-reject'
              }
            />
            <StatCard
              label="Period"
              value={`${data.trading_days}d`}
              subtext={`${data.period_start} to ${data.period_end}`}
            />
          </div>

          {/* Insights */}
          <InsightsBanner insights={data.insights} />

          {/* Calls vs Puts */}
          <OptionTypeComparison data={data.by_option_type} edge={data.option_type_edge} />

          {/* Scanner & Score side by side */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <DimensionTable
              title="By Scanner"
              icon={Zap}
              data={data.by_scanner}
              highlightKey={data.hot_scanner}
            />
            <DimensionTable
              title="By Score Bucket"
              icon={Target}
              data={data.by_score_bucket}
            />
          </div>

          {/* Tier & Convergence side by side */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <DimensionTable
              title="By Quality Tier"
              icon={Layers}
              data={data.by_quality_tier}
            />
            <DimensionTable
              title="By Scanner Convergence"
              icon={Target}
              data={data.by_convergence}
            />
          </div>

          {/* DTE Buckets */}
          <DimensionTable
            title="By DTE Bucket"
            icon={Clock}
            data={data.by_dte_bucket}
          />

          {/* Market Context Footer */}
          {(data.vix_level || data.spy_direction) && (
            <div className="flex items-center gap-4 text-xs text-oss-muted border-t border-oss-border pt-4">
              {data.vix_level && <span>VIX: {data.vix_level.toFixed(1)}</span>}
              {data.spy_direction && <span>SPY: {data.spy_direction}</span>}
            </div>
          )}
        </>
      )}

      {/* Empty state */}
      {data && !isLoading && data.total_closed === 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-surface p-8 text-center">
          <Brain className="h-12 w-12 text-oss-muted mx-auto mb-4" />
          <p className="text-oss-text">No closed trades in the last {days} trading days</p>
          <p className="text-sm text-oss-muted mt-2">
            Intelligence analytics will appear once paper trading positions start closing
          </p>
        </div>
      )}
    </div>
  )
}
