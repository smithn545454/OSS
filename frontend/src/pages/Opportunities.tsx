/**
 * Opportunities Page
 *
 * Primary decision-making interface for identifying actionable trades.
 * Ranks opportunities by pipeline score with filters for premium, delta,
 * and moneyness to surface the contract profile the trader cares about.
 */

import { useEffect, useMemo, useState } from 'react'
import { useApproveEvaluations, useContractQuotes } from '@/hooks/useApi'
import { filterByConvictionThreshold, filterByOpportunityFilters } from '@/lib/convictionScore'
import type { OpportunityFilters } from '@/lib/types'
import {
  ContextBar,
  ConvictionQueue,
  AllApprovesTable,
  WatchIntelligencePanel,
  EarningsExclusionNotice,
} from '@/components/opportunities'

const DEFAULT_CONVICTION_THRESHOLD = 65

const DEFAULT_FILTERS: OpportunityFilters = {
  premiumMax: null,
  premiumMin: null,
  deltaMax: null,
  deltaMin: null,
  moneyness: 'all',
}

function OpportunityFilterBar({
  filters,
  onChange,
}: {
  filters: OpportunityFilters
  onChange: (filters: OpportunityFilters) => void
}) {
  const hasActiveFilters =
    filters.premiumMax != null ||
    filters.premiumMin != null ||
    filters.deltaMax != null ||
    filters.deltaMin != null ||
    filters.moneyness !== 'all'

  const inputStyle: React.CSSProperties = {
    width: '72px',
    padding: '4px 8px',
    background: 'var(--bg-tertiary)',
    border: '1px solid var(--border-default)',
    borderRadius: '4px',
    fontSize: '12px',
    fontFamily: "'JetBrains Mono', monospace",
    color: 'var(--text-secondary)',
  }

  const labelStyle: React.CSSProperties = {
    fontSize: '10px',
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    fontWeight: 600,
  }

  const parseNum = (v: string): number | null => {
    const n = parseFloat(v)
    return isNaN(n) ? null : n
  }

  return (
    <div style={{
      display: 'flex',
      alignItems: 'flex-end',
      gap: '20px',
      padding: '12px 16px',
      background: 'var(--bg-secondary)',
      border: '1px solid var(--border-default)',
      borderRadius: 'var(--radius-md)',
      flexWrap: 'wrap',
    }}>
      {/* Premium Range */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <span style={labelStyle}>Premium</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>$</span>
          <input
            type="number"
            step="0.25"
            min="0"
            placeholder="min"
            value={filters.premiumMin ?? ''}
            onChange={e => onChange({ ...filters, premiumMin: parseNum(e.target.value) })}
            style={inputStyle}
          />
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>to $</span>
          <input
            type="number"
            step="0.25"
            min="0"
            placeholder="max"
            value={filters.premiumMax ?? ''}
            onChange={e => onChange({ ...filters, premiumMax: parseNum(e.target.value) })}
            style={inputStyle}
          />
        </div>
      </div>

      {/* Delta Range */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <span style={labelStyle}>Delta</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <input
            type="number"
            step="0.05"
            min="0"
            max="1"
            placeholder="min"
            value={filters.deltaMin ?? ''}
            onChange={e => onChange({ ...filters, deltaMin: parseNum(e.target.value) })}
            style={inputStyle}
          />
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>to</span>
          <input
            type="number"
            step="0.05"
            min="0"
            max="1"
            placeholder="max"
            value={filters.deltaMax ?? ''}
            onChange={e => onChange({ ...filters, deltaMax: parseNum(e.target.value) })}
            style={inputStyle}
          />
        </div>
      </div>

      {/* Moneyness */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <span style={labelStyle}>Moneyness</span>
        <select
          value={filters.moneyness}
          onChange={e => onChange({ ...filters, moneyness: e.target.value as OpportunityFilters['moneyness'] })}
          style={{
            padding: '4px 8px',
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--border-default)',
            borderRadius: '4px',
            fontSize: '12px',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
          }}
        >
          <option value="all">All</option>
          <option value="otm">OTM Only</option>
          <option value="atm">ATM Only</option>
          <option value="itm">ITM Only</option>
        </select>
      </div>

      {/* Clear */}
      {hasActiveFilters && (
        <button
          onClick={() => onChange({ ...DEFAULT_FILTERS })}
          style={{
            padding: '4px 10px',
            background: 'none',
            border: '1px solid var(--border-default)',
            borderRadius: '4px',
            fontSize: '11px',
            color: 'var(--text-muted)',
            cursor: 'pointer',
          }}
        >
          Clear
        </button>
      )}
    </div>
  )
}

export default function Opportunities() {
  const convictionThreshold = DEFAULT_CONVICTION_THRESHOLD
  const [maxAgeTradingDays, setMaxAgeTradingDays] = useState(1)
  const [filters, setFilters] = useState<OpportunityFilters>({ ...DEFAULT_FILTERS })

  const {
    data,
    isLoading,
    error,
    refetch,
    dataUpdatedAt,
  } = useApproveEvaluations({
    excludeEarnings: true,
    earningsDays: 7,
    maxAgeTradingDays,
    limit: 100,
  })

  // Poll for new opportunities every 60 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      refetch()
    }, 60000)

    return () => clearInterval(interval)
  }, [refetch])

  const evaluations = useMemo(() => data?.evaluations ?? [], [data?.evaluations])
  const earningsExclusions = data?.excludedForEarnings ?? []

  // Apply opportunity filters (premium, delta, moneyness) to all evaluations
  const filteredEvaluations = useMemo(
    () => filterByOpportunityFilters(evaluations, filters),
    [evaluations, filters],
  )

  // Extract option tickers from high-conviction evaluations for live price refresh
  const highConvictionTickers = useMemo(() => {
    const highConviction = filterByConvictionThreshold(filteredEvaluations, convictionThreshold)
    return highConviction.map(e => e.option_ticker).filter(Boolean)
  }, [filteredEvaluations, convictionThreshold])

  // Fetch live quotes for high-conviction opportunities (refreshes every 10 min)
  const { data: quotesData } = useContractQuotes(highConvictionTickers)
  const liveQuotes = quotesData?.quotes ?? {}

  return (
    <div
      className="opportunities-page"
      style={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: '100%',
      }}
    >
      {/* Context Bar - Fixed at top */}
      <ContextBar />

      {/* Main Content */}
      <main
        style={{
          flex: 1,
          padding: '24px',
          display: 'flex',
          flexDirection: 'column',
          gap: '32px',
          maxWidth: '1400px',
          marginInline: 'auto',
          width: '100%',
        }}
      >
        {/* Page Header */}
        <header style={{ marginBottom: '-8px' }}>
          <h1 style={{
            margin: '0 0 4px',
            fontSize: '28px',
            fontWeight: 700,
            color: 'var(--text-primary)',
            letterSpacing: '-0.02em',
          }}>
            Opportunities
          </h1>
          <p style={{
            margin: 0,
            fontSize: '14px',
            color: 'var(--text-muted)',
          }}>
            Options trades ranked by pipeline score
          </p>
        </header>

        {/* Opportunity Filters */}
        <OpportunityFilterBar filters={filters} onChange={setFilters} />

        {/* Earnings Notice */}
        {earningsExclusions.length > 0 && (
          <EarningsExclusionNotice exclusions={earningsExclusions} />
        )}

        {/* Loading State */}
        {isLoading && (
          <div style={{
            padding: '48px 24px',
            textAlign: 'center',
            background: 'var(--bg-secondary)',
            borderRadius: 'var(--radius-lg)',
            border: '1px solid var(--border-default)',
          }}>
            <p style={{
              margin: 0,
              fontSize: '14px',
              color: 'var(--text-muted)',
            }}>
              Loading opportunities...
            </p>
          </div>
        )}

        {/* Error State */}
        {error && !isLoading && (
          <div style={{
            padding: '48px 24px',
            textAlign: 'center',
            background: 'var(--color-error-bg)',
            borderRadius: 'var(--radius-lg)',
            border: '1px solid var(--color-error)',
          }}>
            <h3 style={{
              margin: '0 0 8px',
              fontSize: '16px',
              fontWeight: 600,
              color: 'var(--color-error-text)',
            }}>
              Unable to Load Opportunities
            </h3>
            <p style={{
              margin: '0 0 16px',
              fontSize: '14px',
              color: 'var(--text-muted)',
            }}>
              {error.message || 'An error occurred while fetching data.'}
            </p>
            <button
              onClick={() => refetch()}
              style={{
                padding: '8px 16px',
                background: 'var(--color-error)',
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                color: 'white',
                fontSize: '14px',
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              Retry
            </button>
          </div>
        )}

        {/* Main Content - when data is loaded */}
        {!isLoading && !error && (
          <>
            {/* Conviction Queue */}
            <ConvictionQueue
              evaluations={filteredEvaluations}
              threshold={convictionThreshold}
              liveQuotes={liveQuotes}
              maxAgeTradingDays={maxAgeTradingDays}
              onMaxAgeTradingDaysChange={setMaxAgeTradingDays}
            />

            {/* All APPROVEs Table */}
            <AllApprovesTable evaluations={filteredEvaluations} />

            {/* WATCH Intelligence Panel */}
            <WatchIntelligencePanel />
          </>
        )}

        {/* Empty State */}
        {!isLoading && !error && evaluations.length === 0 && (
          <div style={{
            padding: '64px 24px',
            textAlign: 'center',
            background: 'var(--bg-secondary)',
            borderRadius: 'var(--radius-lg)',
            border: '1px solid var(--border-default)',
          }}>
            <h3 style={{
              margin: '0 0 8px',
              fontSize: '18px',
              fontWeight: 600,
              color: 'var(--text-primary)',
            }}>
              No Active Opportunities
            </h3>
            <p style={{
              margin: '0 0 24px',
              fontSize: '14px',
              color: 'var(--text-muted)',
              maxWidth: '400px',
              marginInline: 'auto',
            }}>
              No contracts have passed all evaluation gates yet. The pipeline will
              continue scanning for new opportunities.
            </p>
            <button
              onClick={() => refetch()}
              style={{
                padding: '10px 20px',
                background: 'var(--accent-primary)',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                color: 'var(--bg-primary)',
                fontSize: '14px',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Check for Updates
            </button>
          </div>
        )}
      </main>

      {/* Footer with last update time */}
      {dataUpdatedAt && (
        <footer style={{
          padding: '12px 24px',
          textAlign: 'center',
          borderTop: '1px solid var(--border-subtle)',
          fontSize: '12px',
          color: 'var(--text-disabled)',
        }}>
          Last updated: {new Date(dataUpdatedAt).toLocaleTimeString()}
        </footer>
      )}
    </div>
  )
}
