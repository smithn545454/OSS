/**
 * Conviction Queue Component
 * 
 * Displays top opportunities ranked by conviction score.
 * Per Section 8 of OSS_Opportunities_Page_Specification.
 */

import type { ApproveEvaluation } from '@/lib/types'
import { filterByConvictionThreshold } from '@/lib/convictionScore'
import { OpportunityCard } from './OpportunityCard'

interface ConvictionQueueProps {
  evaluations: ApproveEvaluation[]
  threshold?: number
  className?: string
  onRefresh?: () => void
  hasNewData?: boolean
  newCount?: number
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

function RefreshNotification({ 
  count, 
  onClick 
}: { 
  count: number
  onClick: () => void 
}) {
  return (
    <button
      onClick={onClick}
      className="refresh-notification"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '8px',
        width: '100%',
        padding: '12px 16px',
        background: 'var(--bg-active)',
        border: '1px solid var(--border-active)',
        borderRadius: 'var(--radius-md)',
        cursor: 'pointer',
        fontSize: '14px',
        fontWeight: 500,
        color: 'var(--accent-primary)',
        marginBottom: '16px',
      }}
    >
      <span>🔄</span>
      <span>{count} new opportunities available</span>
    </button>
  )
}

export function ConvictionQueue({ 
  evaluations,
  threshold = 75,
  className = '',
  onRefresh,
  hasNewData = false,
  newCount = 0,
}: ConvictionQueueProps) {
  // Filter to high-conviction only
  const highConviction = filterByConvictionThreshold(evaluations, threshold)
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
        
        <span style={{ 
          fontSize: '12px', 
          color: 'var(--text-muted)',
        }}>
          Score ≥ {threshold}
        </span>
      </div>
      
      {/* Refresh notification */}
      {hasNewData && newCount > 0 && onRefresh && (
        <RefreshNotification count={newCount} onClick={onRefresh} />
      )}
      
      {/* Cards or Empty State */}
      {isEmpty ? (
        <EmptyState />
      ) : (
        <div 
          style={{ 
            display: 'flex', 
            flexDirection: 'column', 
            gap: '12px' 
          }}
          role="list"
          aria-label="High conviction opportunities"
        >
          {highConviction.map((evaluation, index) => (
            <OpportunityCard
              key={evaluation.evaluation_id}
              evaluation={evaluation}
              rank={index + 1}
            />
          ))}
        </div>
      )}
    </section>
  )
}

export default ConvictionQueue
