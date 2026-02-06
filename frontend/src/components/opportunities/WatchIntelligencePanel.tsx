/**
 * WATCH Intelligence Panel Component
 * 
 * Displays insights from near-miss (WATCH) evaluations.
 * Per Section 11 of OSS_Opportunities_Page_Specification.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWatchInsights } from '@/hooks/useApi'
import type { WatchInsight } from '@/lib/types'

interface WatchIntelligencePanelProps {
  className?: string
}

function InsightCard({ insight }: { insight: WatchInsight }) {
  const navigate = useNavigate()
  
  // Determine border color based on type
  const getBorderColor = () => {
    switch (insight.type) {
      case 'gate_pressure':
        return 'var(--color-warning)'
      case 'recurring_near_miss':
        return 'var(--color-purple)'
      case 'watch_to_approve':
        return 'var(--color-success)'
      default:
        return 'var(--border-default)'
    }
  }
  
  const getTypeLabel = () => {
    switch (insight.type) {
      case 'gate_pressure':
        return 'Gate Pressure'
      case 'recurring_near_miss':
        return 'Recurring Pattern'
      case 'watch_to_approve':
        return 'Recent Conversion'
      default:
        return 'Insight'
    }
  }
  
  const getTypeIcon = () => {
    switch (insight.type) {
      case 'gate_pressure':
        return '⚠️'
      case 'recurring_near_miss':
        return '🔄'
      case 'watch_to_approve':
        return '✅'
      default:
        return '💡'
    }
  }
  
  return (
    <article
      className={`watch-insight-card watch-insight-card--${insight.type === 'recurring_near_miss' ? 'recurring' : insight.type === 'watch_to_approve' ? 'conversion' : 'gate-pressure'}`}
      style={{ borderLeftColor: getBorderColor() }}
    >
      {/* Type badge */}
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        gap: '8px',
        marginBottom: '8px',
      }}>
        <span aria-hidden="true">{getTypeIcon()}</span>
        <span style={{ 
          fontSize: '11px', 
          fontWeight: 600,
          color: getBorderColor(),
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
        }}>
          {getTypeLabel()}
        </span>
      </div>
      
      {/* Headline */}
      <p style={{ 
        margin: '0 0 8px', 
        fontSize: '14px', 
        fontWeight: 500,
        color: 'var(--text-primary)',
        lineHeight: 1.4,
      }}>
        {insight.headline}
      </p>
      
      {/* Sub-insight if present */}
      {insight.subInsight && (
        <p style={{ 
          margin: '0 0 8px', 
          fontSize: '13px', 
          color: 'var(--text-tertiary)',
        }}>
          {insight.subInsight}
        </p>
      )}
      
      {/* Contracts list if present */}
      {insight.contracts && insight.contracts.length > 0 && (
        <div style={{ 
          display: 'flex', 
          gap: '6px', 
          flexWrap: 'wrap',
          marginBottom: '8px',
        }}>
          {insight.contracts.slice(0, 3).map((contract, i) => (
            <span
              key={i}
              style={{
                padding: '3px 8px',
                background: 'var(--bg-tertiary)',
                border: '1px solid var(--border-default)',
                borderRadius: '10px',
                fontSize: '12px',
                fontFamily: 'var(--font-primary)',
                color: 'var(--text-secondary)',
              }}
            >
              {contract}
            </span>
          ))}
          {insight.contracts.length > 3 && (
            <span style={{ 
              fontSize: '12px', 
              color: 'var(--text-muted)',
              alignSelf: 'center',
            }}>
              +{insight.contracts.length - 3} more
            </span>
          )}
        </div>
      )}
      
      {/* Action link if present */}
      {insight.actionLink && (
        <button
          onClick={() => navigate(insight.actionLink!.url)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            padding: '6px 12px',
            background: 'transparent',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--accent-primary)',
            fontSize: '12px',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          {insight.actionLink.label} →
        </button>
      )}
    </article>
  )
}

export function WatchIntelligencePanel({ className = '' }: WatchIntelligencePanelProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const { data, isLoading, error } = useWatchInsights(7)
  
  const insights = data?.insights ?? []
  const hasInsights = insights.length > 0
  
  return (
    <section 
      className={`watch-panel ${isExpanded ? 'watch-panel--expanded' : ''} ${className}`}
      aria-labelledby="watch-panel-heading"
    >
      {/* Header - always visible */}
      <div
        className="watch-panel-header"
        onClick={() => setIsExpanded(!isExpanded)}
        role="button"
        aria-expanded={isExpanded}
        aria-controls="watch-panel-content"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setIsExpanded(!isExpanded)
          }
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <h2 
            id="watch-panel-heading"
            style={{ 
              margin: 0, 
              fontSize: '16px', 
              fontWeight: 600,
              color: 'var(--text-primary)',
            }}
          >
            WATCH Intelligence
          </h2>
          {hasInsights && (
            <span style={{
              padding: '3px 8px',
              background: 'var(--color-warning-bg)',
              borderRadius: '10px',
              fontSize: '12px',
              fontWeight: 600,
              color: 'var(--color-warning-text)',
            }}>
              {insights.length} insight{insights.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        
        <span 
          className="watch-panel-chevron"
          aria-hidden="true"
        >
          {isExpanded ? '▼' : '▶'}
        </span>
      </div>
      
      {/* Content - shown when expanded */}
      <div
        id="watch-panel-content"
        role="region"
        aria-hidden={!isExpanded}
        style={{
          display: isExpanded ? 'block' : 'none',
          padding: '16px 20px',
        }}
      >
        {isLoading ? (
          <p style={{ 
            color: 'var(--text-muted)', 
            fontSize: '14px',
            margin: 0,
          }}>
            Analyzing WATCH patterns...
          </p>
        ) : error ? (
          <p style={{ 
            color: 'var(--color-error-text)', 
            fontSize: '14px',
            margin: 0,
          }}>
            Unable to load insights
          </p>
        ) : !hasInsights ? (
          <p style={{ 
            color: 'var(--text-muted)', 
            fontSize: '14px',
            margin: 0,
          }}>
            No notable patterns detected in recent WATCH evaluations.
          </p>
        ) : (
          <div style={{ 
            display: 'flex', 
            flexDirection: 'column', 
            gap: '12px' 
          }}>
            {insights.map((insight, index) => (
              <InsightCard key={`${insight.type}-${index}`} insight={insight} />
            ))}
          </div>
        )}
        
        {/* Summary stats */}
        {data && (
          <div style={{ 
            marginTop: '16px',
            paddingTop: '16px',
            borderTop: '1px solid var(--border-default)',
            fontSize: '12px',
            color: 'var(--text-muted)',
          }}>
            Based on {data.watchCount} WATCH evaluations from the past 7 days
          </div>
        )}
      </div>
    </section>
  )
}

export default WatchIntelligencePanel
