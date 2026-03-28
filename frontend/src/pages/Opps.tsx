import { useState, useEffect, useMemo } from 'react'
import { useApproveEvaluations } from '@/hooks/useApi'
import { sortByConviction } from '@/lib/convictionScore'
import type { ApproveEvaluation } from '@/lib/types'
import { OppContextBar } from '@/components/opps/OppContextBar'
import { OppFilterBar, type OppFilters } from '@/components/opps/OppFilterBar'
import { OppConvictionList } from '@/components/opps/OppConvictionList'
import { OppWatchCards } from '@/components/opps/OppWatchCards'
import { OppEarningsNotice } from '@/components/opps/OppEarningsNotice'
import { OppEmptyState } from '@/components/opps/OppEmptyState'

function applyFilters(evaluations: ApproveEvaluation[], filters: OppFilters): ApproveEvaluation[] {
  return evaluations.filter((e) => {
    if (filters.scanner !== 'all' && !e.scannerSource.includes(filters.scanner)) return false
    if (filters.urgency !== 'all' && e.urgency !== filters.urgency) return false
    if (filters.optionType !== 'all' && e.option_type !== filters.optionType) return false
    return true
  })
}

export default function Opps() {
  const [filters, setFilters] = useState<OppFilters>({
    scanner: 'all',
    urgency: 'all',
    optionType: 'all',
  })
  const [hasNewData, setHasNewData] = useState(false)
  const [newCount, setNewCount] = useState(0)

  const {
    data,
    isLoading,
    error,
    refetch,
    dataUpdatedAt,
  } = useApproveEvaluations({
    excludeEarnings: true,
    earningsDays: 7,
    limit: 100,
  })

  // Detect new opportunities via session storage
  useEffect(() => {
    if (data?.evaluations) {
      const storedCount = sessionStorage.getItem('oss_opps_count')
      const currentCount = data.evaluations.length

      if (storedCount && parseInt(storedCount) < currentCount) {
        setHasNewData(true)
        setNewCount(currentCount - parseInt(storedCount))
      }

      sessionStorage.setItem('oss_opps_count', String(currentCount))
    }
  }, [data])

  // Poll every 60s
  useEffect(() => {
    const interval = setInterval(() => { refetch() }, 60000)
    return () => clearInterval(interval)
  }, [refetch])

  const handleRefresh = () => {
    setHasNewData(false)
    setNewCount(0)
    refetch()
  }

  const evaluations = useMemo(() => data?.evaluations ?? [], [data?.evaluations])
  const earningsExclusions = data?.excludedForEarnings ?? []

  // Filter then sort — no threshold cutoff
  const sorted = useMemo(() => {
    const filtered = applyFilters(evaluations, filters)
    return sortByConviction(filtered)
  }, [evaluations, filters])

  return (
    <div className="flex flex-col gap-4 sm:gap-6 min-h-full">
      <OppContextBar />

      {/* Page header */}
      <header>
        <h1 className="text-xl sm:text-2xl font-bold text-[var(--text-primary)] tracking-tight">
          Opportunities
        </h1>
        <p className="text-sm text-[var(--text-muted)]">
          Options trades ranked by signal strength
        </p>
      </header>

      {/* Earnings notice */}
      {earningsExclusions.length > 0 && (
        <OppEarningsNotice exclusions={earningsExclusions} />
      )}

      {/* States */}
      {isLoading && <OppEmptyState type="loading" />}
      {error && !isLoading && (
        <OppEmptyState type="error" message={error.message} onRetry={() => refetch()} />
      )}

      {!isLoading && !error && evaluations.length === 0 && (
        <OppEmptyState type="empty" onRetry={() => refetch()} />
      )}

      {/* Main content */}
      {!isLoading && !error && evaluations.length > 0 && (
        <>
          <OppFilterBar
            filters={filters}
            onFilterChange={setFilters}
            totalCount={sorted.length}
          />

          <OppConvictionList
            evaluations={sorted}
            hasNewData={hasNewData}
            newCount={newCount}
            onRefresh={handleRefresh}
          />

          <OppWatchCards />
        </>
      )}

      {/* Footer */}
      {dataUpdatedAt != null && dataUpdatedAt > 0 && (
        <footer className="text-center text-xs text-[var(--text-disabled)] py-3 border-t border-[var(--border-subtle)]">
          Last updated: {new Date(dataUpdatedAt).toLocaleTimeString()}
        </footer>
      )}
    </div>
  )
}
