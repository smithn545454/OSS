/**
 * Paper Trading Workstation — Command center for monitoring paper trading
 * performance, validating scoring tiers, and generating AI-driven
 * recommendations for system optimization.
 */

import { useState } from 'react'
import {
  usePaperTradingSummary,
  usePaperTradingPositions,
  usePaperTradingMetrics,
  usePaperTradingTiers,
  usePaperTradingExits,
  useAIInsights,
  useUpdatePositions,
  useClosePosition,
} from '@/hooks/useApi'
import { CommandHeader } from '@/components/paper-trading/CommandHeader'
import { OpenPositionGrid } from '@/components/paper-trading/OpenPositionGrid'
import { PerformanceAnalytics } from '@/components/paper-trading/PerformanceAnalytics'
import { AIInsightsPanel } from '@/components/paper-trading/AIInsightsPanel'
import { ClosedPositionsTable } from '@/components/paper-trading/ClosedPositionsTable'

type AnalyticsTab = 'overview' | 'tiers' | 'exits'

export default function PaperTradingWorkstation() {
  const [analyticsTab, setAnalyticsTab] = useState<AnalyticsTab>('overview')

  // Data hooks
  const summary = usePaperTradingSummary()
  const openPositions = usePaperTradingPositions('open')
  const closedPositions = usePaperTradingPositions('closed')
  const metrics = usePaperTradingMetrics()
  const tiers = usePaperTradingTiers()
  const exits = usePaperTradingExits()
  const aiInsights = useAIInsights()

  // Mutations
  const updatePositions = useUpdatePositions()
  const closePosition = useClosePosition()

  const handleUpdatePrices = () => {
    updatePositions.mutate()
  }

  const handleClosePosition = (positionId: string) => {
    closePosition.mutate({ positionId })
  }

  const handleRefreshInsights = () => {
    aiInsights.refetch()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }}>
      {/* Section 1: Command Header */}
      <CommandHeader
        summary={summary.data}
        onUpdatePrices={handleUpdatePrices}
        isUpdating={updatePositions.isPending}
        isLoading={summary.isLoading}
      />

      {/* Section 2: Open Positions */}
      <OpenPositionGrid
        positions={openPositions.data?.positions ?? []}
        onClose={handleClosePosition}
        isClosing={closePosition.isPending}
        isLoading={openPositions.isLoading}
      />

      {/* Section 3: Performance Analytics */}
      <PerformanceAnalytics
        metrics={metrics.data}
        tiers={tiers.data}
        exits={exits.data}
        activeTab={analyticsTab}
        onTabChange={setAnalyticsTab}
        isLoading={metrics.isLoading || tiers.isLoading || exits.isLoading}
      />

      {/* Section 4: AI Insights */}
      <AIInsightsPanel
        insights={aiInsights.data}
        onRefresh={handleRefreshInsights}
        isLoading={aiInsights.isLoading}
        isRefreshing={aiInsights.isFetching && !aiInsights.isLoading}
      />

      {/* Section 5: Closed Positions */}
      <ClosedPositionsTable
        positions={closedPositions.data?.positions ?? []}
        isLoading={closedPositions.isLoading}
      />
    </div>
  )
}
