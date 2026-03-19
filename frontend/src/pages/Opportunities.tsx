/**
 * Opportunities Page
 * 
 * Primary decision-making interface for identifying actionable trades.
 * Per Section 5 of OSS_Opportunities_Page_Specification.
 */

import { useEffect } from 'react'
import { useApproveEvaluations } from '@/hooks/useApi'
import {
  ContextBar,
  ConvictionQueue,
  AllApprovesTable,
  WatchIntelligencePanel,
  EarningsExclusionNotice,
} from '@/components/opportunities'

// Default conviction threshold for the queue
const DEFAULT_CONVICTION_THRESHOLD = 75

export default function Opportunities() {
  const convictionThreshold = DEFAULT_CONVICTION_THRESHOLD

  // Fetch APPROVE evaluations with conviction scoring
  const {
    data,
    isLoading,
    error,
    refetch,
    dataUpdatedAt,
  } = useApproveEvaluations({
    excludeEarnings: true,
    earningsDays: 7,
    maxAgeTradingDays: 2,
    limit: 100,
  })

  // Poll for new opportunities every 60 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      refetch()
    }, 60000)

    return () => clearInterval(interval)
  }, [refetch])
  
  const evaluations = data?.evaluations ?? []
  const earningsExclusions = data?.excludedForEarnings ?? []
  
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
            High-conviction options trades ranked by signal strength
          </p>
        </header>
        
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
            <div style={{ 
              fontSize: '32px', 
              marginBottom: '12px',
              animation: 'pulse 1.5s ease-in-out infinite',
            }}>
              📊
            </div>
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
            <div style={{ 
              fontSize: '32px', 
              marginBottom: '12px',
            }}>
              ⚠️
            </div>
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
              evaluations={evaluations}
              threshold={convictionThreshold}
            />
            
            {/* All APPROVEs Table */}
            <AllApprovesTable evaluations={evaluations} />
            
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
            <div style={{ 
              fontSize: '48px', 
              marginBottom: '16px',
              opacity: 0.5,
            }}>
              🔍
            </div>
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
