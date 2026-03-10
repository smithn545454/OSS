/**
 * Paper Trading page — Trade Intelligence view.
 *
 * Three tabs: Scanner Intelligence, Trade Library, Pattern Discovery.
 * CompactSummaryBar replaces the old 6-card KPI strip.
 */

import { useState } from 'react'
import clsx from 'clsx'
import { Clock, Sparkles, RefreshCw } from 'lucide-react'
import FilterBar, { useFilterParams } from '@/components/paper-trading/FilterBar'
import CompactSummaryBar from '@/components/paper-trading/CompactSummaryBar'
import ScannerIntelligence from '@/components/paper-trading/ScannerIntelligence'
import TradeLibrary from '@/components/paper-trading/TradeLibrary'
import PatternDiscovery from '@/components/paper-trading/PatternDiscovery'
import { useSummaryMetrics } from '@/hooks/useApi'

const TABS = [
  { id: 'scanners', label: 'Scanner Intelligence' },
  { id: 'library', label: 'Trade Library' },
  { id: 'patterns', label: 'Pattern Discovery' },
] as const

type TabId = (typeof TABS)[number]['id']

function formatLastSynced(iso: string | null | undefined): string {
  if (!iso) return 'Never'
  try {
    const d = new Date(iso)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return 'Just now'
    if (diffMin < 60) return `${diffMin}m ago`
    const diffHr = Math.floor(diffMin / 60)
    if (diffHr < 24) return `${diffHr}h ago`
    return d.toLocaleDateString()
  } catch {
    return iso
  }
}

export default function PaperTrading() {
  const [filters, setFilters] = useFilterParams()
  const [activeTab, setActiveTab] = useState<TabId>('scanners')

  const summaryMetrics = useSummaryMetrics({
    period: filters.period,
    verdict: filters.verdict,
    scanner: filters.scanner,
    status: filters.status,
  })

  const g = summaryMetrics.data?.global
  const lastSynced = g?.last_updated
  const totalTrades = (g?.open_count ?? 0) + (g?.closed_count ?? 0)
  const winRate = g?.win_rate ?? null
  const avgReturn = g?.avg_return ?? 0
  const bestTradePnl = g?.best_trade_pnl ?? null

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-oss-text">Paper Trading</h1>
          <span className="inline-flex items-center gap-1 rounded-md bg-oss-accent/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-oss-accent border border-oss-accent/20">
            <Sparkles className="h-3 w-3" />
            Trade Intelligence
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1 text-xs text-oss-muted">
            <Clock className="h-3 w-3" />
            Synced: {formatLastSynced(lastSynced)}
          </span>
        </div>
      </div>

      {/* Subtitle */}
      <p className="text-sm text-oss-muted -mt-4">
        Analyze scanner performance, browse your trade history, and discover winning patterns.
      </p>

      {/* Filter Bar */}
      <FilterBar values={filters} onChange={setFilters} />

      {/* Compact Summary Bar */}
      <CompactSummaryBar
        totalTrades={totalTrades}
        winRate={winRate}
        avgReturn={avgReturn}
        bestTrade={bestTradePnl != null ? { ticker: '', pnl: bestTradePnl } : null}
      />

      {/* Loading State */}
      {summaryMetrics.isLoading && (
        <div className="rounded-lg border border-oss-border bg-oss-surface p-12 text-center">
          <RefreshCw className="h-6 w-6 mx-auto mb-2 animate-spin text-oss-accent" />
          <p className="text-sm text-oss-muted">Loading paper trading data...</p>
        </div>
      )}

      {/* Error State */}
      {summaryMetrics.error && (
        <div className="rounded-lg border border-oss-reject/30 bg-oss-reject/5 p-3 text-xs text-oss-reject">
          Error loading data: {summaryMetrics.error.message}.{' '}
          <button
            onClick={() => window.location.reload()}
            className="underline hover:no-underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* Tab Navigation */}
      <div className="flex gap-1 border-b border-oss-border">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={clsx(
              'px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px',
              activeTab === tab.id
                ? 'border-oss-accent text-oss-accent'
                : 'border-transparent text-oss-muted hover:text-oss-text hover:border-oss-border'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {!summaryMetrics.isLoading && (
        <>
          {activeTab === 'scanners' && (
            <ScannerIntelligence period={filters.period} />
          )}
          {activeTab === 'library' && (
            <TradeLibrary
              period={filters.period}
              verdict={filters.verdict}
              scanner={filters.scanner}
            />
          )}
          {activeTab === 'patterns' && (
            <PatternDiscovery
              period={filters.period}
              verdict={filters.verdict}
              scanner={filters.scanner}
            />
          )}
        </>
      )}
    </div>
  )
}
