import { useState } from 'react'
import { Wallet, TrendingUp, TrendingDown, BarChart3, Brain } from 'lucide-react'
import { usePageTitle } from '@/hooks/usePageTitle'
import clsx from 'clsx'
import { useTrades, useTradeStats } from '@/hooks/useApi'
import TradeCard from '@/components/trades/TradeCard'
import TradeAnalysisView from '@/components/trades/TradeAnalysisView'

type Tab = 'open' | 'closed' | 'analysis'

export default function MyTrades() {
  usePageTitle('My Trades')
  const [activeTab, setActiveTab] = useState<Tab>('open')
  const { data: stats } = useTradeStats()
  const { data: tradesData, isLoading } = useTrades({
    status: activeTab === 'analysis' ? undefined : activeTab.toUpperCase(),
  })

  const trades = tradesData?.trades ?? []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-oss-accent/10">
          <Wallet className="h-5 w-5 text-oss-accent" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-oss-text">My Trades</h1>
          <p className="text-sm text-oss-muted">Manually tracked real trades</p>
        </div>
      </div>

      {/* Stats Bar */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard label="Open" value={stats.open_count} />
          <StatCard label="Closed" value={stats.closed_count} />
          <StatCard
            label="Win Rate"
            value={stats.closed_count > 0 ? `${stats.win_rate}%` : '—'}
            color={stats.win_rate >= 50 ? 'text-oss-approve' : stats.win_rate > 0 ? 'text-oss-watch' : undefined}
            icon={stats.win_rate >= 50 ? TrendingUp : TrendingDown}
          />
          <StatCard
            label="Avg Return"
            value={stats.closed_count > 0 ? `${stats.avg_return_pct > 0 ? '+' : ''}${stats.avg_return_pct}%` : '—'}
            color={stats.avg_return_pct > 0 ? 'text-oss-approve' : stats.avg_return_pct < 0 ? 'text-oss-reject' : undefined}
            icon={BarChart3}
          />
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 rounded-lg bg-oss-surface p-1">
        {(['open', 'closed', 'analysis'] as Tab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors capitalize',
              activeTab === tab
                ? 'bg-oss-card text-oss-text shadow-sm'
                : 'text-oss-muted hover:text-oss-text'
            )}
          >
            {tab === 'analysis' ? (
              <span className="inline-flex items-center gap-1.5">
                <Brain className="h-3.5 w-3.5" />
                AI Analysis
              </span>
            ) : (
              <>
                {tab} Trades
                {stats && (
                  <span className="ml-1.5 text-xs text-oss-muted">
                    ({tab === 'open' ? stats.open_count : stats.closed_count})
                  </span>
                )}
              </>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      {activeTab === 'analysis' ? (
        <TradeAnalysisView />
      ) : isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-28 animate-pulse rounded-xl bg-oss-surface" />
          ))}
        </div>
      ) : trades.length === 0 ? (
        <div className="rounded-xl border border-oss-border bg-oss-card p-12 text-center">
          <Wallet className="h-12 w-12 text-oss-muted/30 mx-auto mb-4" />
          <p className="text-oss-muted">
            {activeTab === 'open'
              ? 'No open trades. Track a trade from the Evaluation Detail page.'
              : 'No closed trades yet.'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {trades.map((trade) => (
            <TradeCard key={trade.trade_id} trade={trade} />
          ))}
        </div>
      )}
    </div>
  )
}

function StatCard({
  label,
  value,
  color,
  icon: Icon,
}: {
  label: string
  value: number | string
  color?: string
  icon?: typeof TrendingUp
}) {
  return (
    <div className="rounded-xl border border-oss-border bg-oss-card p-4">
      <p className="text-xs text-oss-muted mb-1">{label}</p>
      <div className="flex items-center gap-2">
        {Icon && <Icon className={clsx('h-4 w-4', color || 'text-oss-muted')} />}
        <span className={clsx('text-xl font-bold font-mono', color || 'text-oss-text')}>
          {value}
        </span>
      </div>
    </div>
  )
}
