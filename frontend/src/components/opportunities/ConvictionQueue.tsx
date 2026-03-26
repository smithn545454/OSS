/**
 * Conviction Queue Component
 * 
 * Displays top opportunities ranked by conviction score.
 * Per Section 8 of OSS_Opportunities_Page_Specification.
 */

import { useState } from 'react'
import type { ApproveEvaluation, ContractQuote } from '@/lib/types'
import { filterByConvictionThreshold, sortByConviction, sortByComposite } from '@/lib/convictionScore'
import { CompactRowCard } from './CompactRowCard'

interface ConvictionQueueProps {
  evaluations: ApproveEvaluation[]
  threshold?: number
  liveQuotes?: Record<string, ContractQuote>
  className?: string
}

function EmptyState() {
  return (
    <div style={{
      padding: '48px 24px',
      textAlign: 'center',
      background: 'var(--bg-tertiary)',
      borderRadius: 'var(--radius-lg)',
      border: '1px solid var(--border-default)',
    }}>
      <div style={{ 
        fontSize: '48px', 
        marginBottom: '16px',
        opacity: 0.5,
      }}>
        📊
      </div>
      <h3 style={{ 
        margin: '0 0 8px', 
        fontSize: '16px', 
        fontWeight: 600,
        color: 'var(--text-primary)',
      }}>
        No High-Conviction Opportunities
      </h3>
      <p style={{ 
        margin: 0, 
        fontSize: '14px', 
        color: 'var(--text-muted)',
        maxWidth: '400px',
        marginInline: 'auto',
      }}>
        No contracts currently meet the conviction threshold. Check the All APPROVEs table 
        below for opportunities with lower conviction scores.
      </p>
    </div>
  )
}

export function ConvictionQueue({
  evaluations,
  threshold = 75,
  liveQuotes = {},
  className = '',
}: ConvictionQueueProps) {
  const [sortMode, setSortMode] = useState<'composite' | 'conviction'>('composite')

  // Filter to high-conviction only, then sort
  const filtered = filterByConvictionThreshold(evaluations, threshold)
  const highConviction = sortMode === 'composite' ? sortByComposite(filtered) : sortByConviction(filtered)
  const isEmpty = highConviction.length === 0
  
  return (
    <section 
      className={className}
      aria-labelledby="conviction-queue-heading"
    >
      {/* Section Header */}
      <div style={{ 
        display: 'flex', 
        justifyContent: 'space-between', 
        alignItems: 'center',
        marginBottom: '16px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <h2 
            id="conviction-queue-heading"
            style={{ 
              margin: 0, 
              fontSize: '18px', 
              fontWeight: 700,
              color: 'var(--text-primary)',
            }}
          >
            Conviction Queue
          </h2>
          <span style={{
            padding: '4px 10px',
            background: 'var(--bg-tertiary)',
            borderRadius: '12px',
            fontSize: '13px',
            fontWeight: 600,
            color: 'var(--accent-primary)',
          }}>
            {highConviction.length}
          </span>
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Ranked by {sortMode === 'composite' ? 'Composite' : 'Conviction'}
          </span>
          <span style={{
            display: 'inline-flex',
            gap: '2px',
            fontSize: '11px',
          }}>
            <button
              onClick={() => setSortMode('composite')}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                padding: '2px 6px',
                fontSize: '11px',
                color: sortMode === 'composite' ? 'var(--text-secondary)' : 'var(--text-muted)',
                borderBottom: sortMode === 'composite' ? '1px solid var(--text-secondary)' : '1px solid transparent',
                opacity: sortMode === 'composite' ? 1 : 0.6,
              }}
            >
              Composite
            </button>
            <button
              onClick={() => setSortMode('conviction')}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                padding: '2px 6px',
                fontSize: '11px',
                color: sortMode === 'conviction' ? 'var(--text-secondary)' : 'var(--text-muted)',
                borderBottom: sortMode === 'conviction' ? '1px solid var(--text-secondary)' : '1px solid transparent',
                opacity: sortMode === 'conviction' ? 1 : 0.6,
              }}
            >
              Conviction
            </button>
          </span>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Score ≥ {threshold}
          </span>
        </div>
      </div>
      
      {/* Cards or Empty State */}
      {isEmpty ? (
        <EmptyState />
      ) : (
        <div 
          style={{ 
            display: 'flex', 
            flexDirection: 'column', 
            gap: '8px'
          }}
          role="list"
          aria-label="High conviction opportunities"
        >
          {highConviction.map((evaluation, index) => (
            <CompactRowCard
              key={evaluation.evaluation_id}
              evaluation={evaluation}
              rank={index + 1}
              liveQuote={liveQuotes[evaluation.option_ticker]}
            />
          ))}
        </div>
      )}
    </section>
  )
}

export default ConvictionQueue
