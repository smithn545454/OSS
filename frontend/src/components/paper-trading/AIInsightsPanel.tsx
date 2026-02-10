/**
 * AIInsightsPanel — LLM-generated system tuning recommendations.
 */

import { RefreshCw, Sparkles } from 'lucide-react'
import type { AIInsightsResponse, InsightCategory, InsightSeverity } from '@/lib/types'

interface AIInsightsPanelProps {
  insights: AIInsightsResponse | undefined
  onRefresh: () => void
  isLoading: boolean
  isRefreshing: boolean
}

const CATEGORY_COLORS: Record<InsightCategory, { bg: string; text: string }> = {
  GATE_TUNING: { bg: 'rgba(34, 211, 238, 0.15)', text: '#22d3ee' },
  EXIT_STRATEGY: { bg: 'rgba(234, 179, 8, 0.15)', text: '#eab308' },
  TIER_QUALITY: { bg: 'rgba(34, 197, 94, 0.15)', text: '#22c55e' },
  SCORE_OPTIMIZATION: { bg: 'rgba(168, 85, 247, 0.15)', text: '#a855f7' },
  RISK_MANAGEMENT: { bg: 'rgba(239, 68, 68, 0.15)', text: '#ef4444' },
}

const SEVERITY_COLORS: Record<InsightSeverity, string> = {
  HIGH: '#ef4444',
  MEDIUM: '#eab308',
  LOW: '#22c55e',
}

export function AIInsightsPanel({
  insights,
  onRefresh,
  isLoading,
  isRefreshing,
}: AIInsightsPanelProps) {
  const hasInsights = insights?.insights && insights.insights.length > 0
  const hasMessage = insights?.message

  return (
    <div
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-lg)',
        padding: '24px',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '20px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Sparkles
            style={{
              width: 20,
              height: 20,
              color: 'var(--accent-primary)',
            }}
          />
          <h3
            style={{
              fontSize: '16px',
              fontWeight: 700,
              color: 'var(--text-primary)',
              margin: 0,
            }}
          >
            AI System Insights
          </h3>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {insights?.generated_at && (
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              Generated{' '}
              {new Date(insights.generated_at).toLocaleString()}
            </span>
          )}
          <button
            onClick={onRefresh}
            disabled={isLoading || isRefreshing}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 500,
              background: 'var(--bg-tertiary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-sm)',
              cursor:
                isLoading || isRefreshing ? 'not-allowed' : 'pointer',
              opacity: isLoading || isRefreshing ? 0.7 : 1,
            }}
          >
            <RefreshCw
              className={isRefreshing ? 'animate-spin' : ''}
              style={{ width: 12, height: 12 }}
            />
            Refresh
          </button>
        </div>
      </div>

      {/* Content */}
      {isLoading ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '16px',
          }}
        >
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              style={{
                height: '180px',
                background: 'var(--bg-tertiary)',
                borderRadius: 'var(--radius-md)',
                animation: 'pulse 2s ease-in-out infinite',
              }}
            />
          ))}
        </div>
      ) : hasMessage && !hasInsights ? (
        <div
          style={{
            padding: '40px',
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: '14px',
            background: 'var(--bg-tertiary)',
            borderRadius: 'var(--radius-md)',
          }}
        >
          {insights.message}
        </div>
      ) : !hasInsights ? (
        <div
          style={{
            padding: '40px',
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: '14px',
            background: 'var(--bg-tertiary)',
            borderRadius: 'var(--radius-md)',
          }}
        >
          No insights available. Click Refresh to generate insights, or wait
          for more closed positions.
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '16px',
          }}
        >
          {insights!.insights.map((insight, i) => {
            const catColors =
              CATEGORY_COLORS[insight.category] || CATEGORY_COLORS.SCORE_OPTIMIZATION
            const sevColor = SEVERITY_COLORS[insight.severity] || SEVERITY_COLORS.MEDIUM

            return (
              <div
                key={i}
                style={{
                  padding: '20px',
                  background: 'var(--bg-tertiary)',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-subtle)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '12px',
                }}
              >
                {/* Top badges */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                  }}
                >
                  <span
                    style={{
                      fontSize: '10px',
                      fontWeight: 600,
                      padding: '2px 8px',
                      borderRadius: '9999px',
                      background: catColors.bg,
                      color: catColors.text,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                    }}
                  >
                    {insight.category.replace(/_/g, ' ')}
                  </span>
                  <span
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '10px',
                      color: sevColor,
                      fontWeight: 600,
                    }}
                  >
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: sevColor,
                        display: 'inline-block',
                      }}
                    />
                    {insight.severity}
                  </span>
                </div>

                {/* Title */}
                <div
                  style={{
                    fontSize: '14px',
                    fontWeight: 600,
                    color: 'var(--text-primary)',
                  }}
                >
                  {insight.title}
                </div>

                {/* Description */}
                <div
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-muted)',
                    lineHeight: '1.5',
                  }}
                >
                  {insight.description}
                </div>

                {/* Data points */}
                {Object.keys(insight.data_points).length > 0 && (
                  <div
                    style={{
                      padding: '10px 12px',
                      background: 'var(--bg-primary)',
                      borderRadius: 'var(--radius-sm)',
                      display: 'grid',
                      gridTemplateColumns: 'repeat(2, 1fr)',
                      gap: '6px',
                      fontSize: '11px',
                    }}
                  >
                    {Object.entries(insight.data_points).map(
                      ([key, value]) => (
                        <div
                          key={key}
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            gap: '8px',
                          }}
                        >
                          <span style={{ color: 'var(--text-muted)' }}>
                            {key.replace(/_/g, ' ')}
                          </span>
                          <span
                            style={{
                              color: 'var(--text-primary)',
                              fontWeight: 500,
                              fontFamily: 'monospace',
                            }}
                          >
                            {value}
                          </span>
                        </div>
                      )
                    )}
                  </div>
                )}

                {/* Suggested action */}
                <div
                  style={{
                    padding: '10px 12px',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '12px',
                  }}
                >
                  <div
                    style={{
                      fontSize: '10px',
                      fontWeight: 600,
                      color: 'var(--text-muted)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                      marginBottom: '4px',
                    }}
                  >
                    Suggested Action
                  </div>
                  <div style={{ color: 'var(--text-secondary)' }}>
                    {insight.suggested_action}
                  </div>
                </div>

                {/* Estimated impact */}
                <div
                  style={{
                    fontSize: '12px',
                    color: 'var(--accent-primary)',
                    fontWeight: 500,
                  }}
                >
                  Impact: {insight.estimated_impact}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Footer with metadata */}
      {insights?.llm_provider && insights.tokens_used > 0 && (
        <div
          style={{
            marginTop: '16px',
            paddingTop: '12px',
            borderTop: '1px solid var(--border-subtle)',
            display: 'flex',
            gap: '16px',
            fontSize: '11px',
            color: 'var(--text-disabled)',
          }}
        >
          <span>Provider: {insights.llm_provider}</span>
          <span>Tokens: {insights.tokens_used}</span>
          <span>
            Positions analyzed: {insights.data_summary.positions_analyzed}
          </span>
        </div>
      )}
    </div>
  )
}
