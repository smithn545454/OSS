/**
 * Conviction Queue Component
 * 
 * Displays top opportunities ranked by conviction score.
 * Per Section 8 of OSS_Opportunities_Page_Specification.
 */

import { useState } from 'react'
import type { ApproveEvaluation, ContractQuote } from '@/lib/types'
import { filterByConvictionThreshold, sortByConviction, sortByCheap } from '@/lib/convictionScore'
import { CompactRowCard } from './CompactRowCard'

const TIME_WINDOWS = [
  { label: 'Today', value: 1 },
  { label: '2 Days', value: 2 },
  { label: 'This Week', value: 5 },
] as const

interface ConvictionQueueProps {
  evaluations: ApproveEvaluation[]
  threshold?: number
  liveQuotes?: Record<string, ContractQuote>
  maxAgeTradingDays?: number
  onMaxAgeTradingDaysChange?: (days: number) => void
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
  threshold = 70,
  liveQuotes = {},
  maxAgeTradingDays = 1,
  onMaxAgeTradingDaysChange,
  className = '',
}: ConvictionQueueProps) {
  const [sortMode, setSortMode] = useState<'conviction' | 'cheap'>('conviction')
  const [rulesOnly, setRulesOnly] = useState(false)

  // Filter to high-conviction only, optionally require matched rules, then sort
  let filtered = filterByConvictionThreshold(evaluations, threshold)
  if (rulesOnly) {
    filtered = filtered.filter(e => e.matchedRules && e.matchedRules.length > 0)
  }
  const highConviction = sortMode === 'cheap'
    ? sortByCheap(filtered)
    : sortByConviction(filtered)
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
          <select
            value={maxAgeTradingDays}
            onChange={e => onMaxAgeTradingDaysChange?.(Number(e.target.value))}
            style={{
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border-default)',
              borderRadius: '4px',
              padding: '2px 6px',
              fontSize: '11px',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            {TIME_WINDOWS.map(w => (
              <option key={w.value} value={w.value}>{w.label}</option>
            ))}
          </select>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Ranked by {sortMode === 'cheap' ? 'Cheap' : 'Conviction'}
          </span>
          <span style={{
            display: 'inline-flex',
            gap: '2px',
            fontSize: '11px',
          }}>
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
            <button
              onClick={() => setSortMode('cheap')}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                padding: '2px 6px',
                fontSize: '11px',
                color: sortMode === 'cheap' ? 'var(--text-secondary)' : 'var(--text-muted)',
                borderBottom: sortMode === 'cheap' ? '1px solid var(--text-secondary)' : '1px solid transparent',
                opacity: sortMode === 'cheap' ? 1 : 0.6,
              }}
            >
              Cheap
            </button>
          </span>
          <button
            onClick={() => setRulesOnly(!rulesOnly)}
            style={{
              background: rulesOnly ? 'var(--accent-primary)' : 'none',
              border: rulesOnly ? '1px solid var(--accent-primary)' : '1px solid var(--border-default)',
              borderRadius: '4px',
              cursor: 'pointer',
              padding: '2px 8px',
              fontSize: '11px',
              color: rulesOnly ? 'var(--bg-primary)' : 'var(--text-muted)',
              fontWeight: rulesOnly ? 600 : 400,
            }}
          >
            Has Rules
          </button>
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
