/**
 * Paper Trading Workstation page.
 *
 * Four tabs: Performance Overview, Position Tracker, Score Calibration, AI Strategy Advisor.
 * Uses server-side filtering and pre-aggregated metrics.
 */

import { useState } from 'react'
import clsx from 'clsx'
import { Clock, Sparkles, RefreshCw } from 'lucide-react'
import FilterBar, { useFilterParams } from '@/components/paper-trading/FilterBar'
import KPIStrip from '@/components/paper-trading/KPIStrip'
import PerformanceOverview from '@/components/paper-trading/PerformanceOverview'
import PositionTracker from '@/components/paper-trading/PositionTracker'
import ScoreCalibration from '@/components/paper-trading/ScoreCalibration'
import AIStrategyAdvisor from '@/components/paper-trading/AIStrategyAdvisor'
import { useEnrichedPositions } from '@/hooks/useEnrichedPositions'
import { useSummaryMetrics } from '@/hooks/useApi'

const TABS = [
  { id: 'overview', label: 'Performance Overview' },
  { id: 'positions', label: 'Position Tracker' },
  { id: 'calibration', label: 'Score Calibration' },
  { id: 'advisor', label: 'AI Strategy Advisor' },
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
  const [activeTab, setActiveTab] = useState<TabId>('overview')

  const { enrichedPositions, isLoading, error, rawMetrics } = useEnrichedPositions({
    period: filters.period,
    verdict: filters.verdict,
    scanner: filters.scanner,
    status: filters.status,
  })

  const summaryMetrics = useSummaryMetrics()
  const lastSynced = summaryMetrics.data?.global?.last_updated

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-oss-text">Paper Trading</h1>
          <span className="inline-flex items-center gap-1 rounded-md bg-oss-accent/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-oss-accent border border-oss-accent/20">
            <Sparkles className="h-3 w-3" />
            AI Workstation
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-oss-muted">
            {enrichedPositions.length} position{enrichedPositions.length !== 1 ? 's' : ''}
          </span>
          <span className="flex items-center gap-1 text-xs text-oss-muted">
            <Clock className="h-3 w-3" />
            Synced: {formatLastSynced(lastSynced)}
          </span>
        </div>
      </div>

      {/* Subtitle */}
      <p className="text-sm text-oss-muted -mt-4">
        Track paper trading performance, analyze score calibration, and get AI-powered optimization insights.
      </p>

      {/* Filter Bar */}
      <FilterBar values={filters} onChange={setFilters} />

      {/* KPI Strip */}
      <KPIStrip
        totalPnl={rawMetrics.totalPnl}
        winRate={rawMetrics.winRate}
        avgReturn={rawMetrics.avgReturn}
        avgScore={rawMetrics.avgScore}
        bestTrade={rawMetrics.bestTrade}
        activeCount={rawMetrics.activeCount}
      />

      {/* Error State */}
      {error && (
        <div className="rounded-lg border border-oss-reject/30 bg-oss-reject/5 p-3 text-xs text-oss-reject">
          Error loading data: {error.message}.{' '}
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

      {/* Loading State */}
      {isLoading ? (
        <div className="rounded-lg border border-oss-border bg-oss-surface p-12 text-center">
          <RefreshCw className="h-6 w-6 mx-auto mb-2 animate-spin text-oss-accent" />
          <p className="text-sm text-oss-muted">Loading paper trading data...</p>
        </div>
      ) : (
        <>
          {/* Tab Content */}
          {activeTab === 'overview' && (
            <PerformanceOverview positions={enrichedPositions} period={filters.period} />
          )}
          {activeTab === 'positions' && <PositionTracker positions={enrichedPositions} />}
          {activeTab === 'calibration' && <ScoreCalibration positions={enrichedPositions} />}
          {activeTab === 'advisor' && <AIStrategyAdvisor />}
        </>
      )}
    </div>
  )
}
