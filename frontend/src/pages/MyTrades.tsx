import { useState } from 'react'
import { Wallet, Brain, Download } from 'lucide-react'
import { usePageTitle } from '@/hooks/usePageTitle'
import clsx from 'clsx'
import { useTrades, useTradeStats } from '@/hooks/useApi'
import TradeCard from '@/components/trades/TradeCard'
import TradeAnalysisView from '@/components/trades/TradeAnalysisView'
import ActivePositionsDashboard from '@/components/trades/active/ActivePositionsDashboard'

type Tab = 'open' | 'closed' | 'analysis'

const API_BASE = import.meta.env.VITE_API_URL || ''

function isoToday(): string {
  return new Date().toISOString().slice(0, 10)
}

function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

function ExportCsvControl() {
  const [start, setStart] = useState(isoDaysAgo(90))
  const [end, setEnd] = useState(isoToday())

  const invalid = !start || !end || start > end

  const handleExport = () => {
    if (invalid) return
    const url = `${API_BASE}/api/trades/export.csv?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`
    // Use a hidden anchor click so the browser treats the response as a download
    const a = document.createElement('a')
    a.href = url
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  return (
    <div className="flex items-center gap-2">
      <input
        type="date"
        value={start}
        onChange={(e) => setStart(e.target.value)}
        className="rounded-lg border border-oss-border bg-oss-bg px-2 py-1.5 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
        aria-label="Export start date"
      />
      <span className="text-xs text-oss-muted">to</span>
      <input
        type="date"
        value={end}
        onChange={(e) => setEnd(e.target.value)}
        className="rounded-lg border border-oss-border bg-oss-bg px-2 py-1.5 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
        aria-label="Export end date"
      />
      <button
        onClick={handleExport}
        disabled={invalid}
        className="inline-flex items-center gap-1.5 rounded-lg bg-oss-accent px-3 py-1.5 text-sm font-semibold text-black hover:bg-oss-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        title={invalid ? 'Start must be on or before end' : 'Download trades as CSV'}
      >
        <Download className="h-4 w-4" />
        Export CSV
      </button>
    </div>
  )
}

export default function MyTrades() {
  usePageTitle('My Trades')
  const [activeTab, setActiveTab] = useState<Tab>('open')
  const { data: stats } = useTradeStats()
  // Only CLOSED tab reads the manually-tracked trade log. The OPEN tab is
  // powered by the Active Positions dashboard (paper-trading positions).
  const { data: tradesData, isLoading } = useTrades({
    status: activeTab === 'closed' ? 'CLOSED' : undefined,
  })

  const trades = tradesData?.trades ?? []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-oss-accent/10">
            <Wallet className="h-5 w-5 text-oss-accent" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-oss-text">My Trades</h1>
            <p className="text-sm text-oss-muted">
              {activeTab === 'open'
                ? 'Active positions across the book'
                : 'Manually tracked real trades'}
            </p>
          </div>
        </div>
        <ExportCsvControl />
      </div>

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
                {tab === 'open' ? 'Active' : 'Closed'}
                {tab === 'closed' && stats && (
                  <span className="ml-1.5 text-xs text-oss-muted">
                    ({stats.closed_count})
                  </span>
                )}
              </>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      {activeTab === 'open' ? (
        <ActivePositionsDashboard />
      ) : activeTab === 'analysis' ? (
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
          <p className="text-oss-muted">No closed trades yet.</p>
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
