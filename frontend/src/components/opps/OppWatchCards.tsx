import { useNavigate } from 'react-router-dom'
import { useWatchInsights } from '@/hooks/useApi'
import type { WatchInsight } from '@/lib/types'
import clsx from 'clsx'

const TYPE_CONFIG: Record<string, { icon: string; label: string; borderColor: string }> = {
  gate_pressure: {
    icon: '⚠️',
    label: 'Gate Pressure',
    borderColor: 'border-l-[var(--color-warning)]',
  },
  recurring_near_miss: {
    icon: '🔄',
    label: 'Recurring Pattern',
    borderColor: 'border-l-purple-500',
  },
  watch_to_approve: {
    icon: '✅',
    label: 'Recent Conversion',
    borderColor: 'border-l-[var(--color-success)]',
  },
}

function InsightCard({ insight }: { insight: WatchInsight }) {
  const navigate = useNavigate()
  const config = TYPE_CONFIG[insight.type] ?? {
    icon: '💡',
    label: 'Insight',
    borderColor: 'border-l-[var(--border-default)]',
  }

  return (
    <article
      className={clsx(
        'rounded-lg border border-[var(--border-default)] bg-[var(--bg-secondary)] p-3 sm:p-4',
        'border-l-2',
        config.borderColor,
      )}
    >
      {/* Type badge */}
      <div className="flex items-center gap-2 mb-2">
        <span aria-hidden="true">{config.icon}</span>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {config.label}
        </span>
      </div>

      {/* Headline */}
      <p className="text-sm font-medium text-[var(--text-primary)] leading-snug mb-1">
        {insight.headline}
      </p>

      {/* Sub-insight */}
      {insight.subInsight && (
        <p className="text-xs text-[var(--text-tertiary)] mb-2">{insight.subInsight}</p>
      )}

      {/* Contracts */}
      {insight.contracts && insight.contracts.length > 0 && (
        <div className="flex gap-1.5 flex-wrap mb-2">
          {insight.contracts.slice(0, 3).map((contract, i) => (
            <span
              key={i}
              className="px-2 py-0.5 bg-[var(--bg-tertiary)] border border-[var(--border-default)] rounded-full text-xs font-mono text-[var(--text-secondary)]"
            >
              {contract}
            </span>
          ))}
          {insight.contracts.length > 3 && (
            <span className="text-xs text-[var(--text-muted)] self-center">
              +{insight.contracts.length - 3} more
            </span>
          )}
        </div>
      )}

      {/* Action */}
      {insight.actionLink && (
        <button
          onClick={() => navigate(insight.actionLink!.url)}
          className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-[var(--accent-primary)] border border-[var(--border-default)] rounded hover:bg-[var(--bg-hover)] transition-colors"
        >
          {insight.actionLink.label} →
        </button>
      )}
    </article>
  )
}

export function OppWatchCards() {
  const { data, isLoading, error } = useWatchInsights(7)
  const insights = data?.insights ?? []

  if (isLoading) {
    return (
      <div className="text-sm text-[var(--text-muted)] py-4">
        Analyzing WATCH patterns...
      </div>
    )
  }

  if (error) return null
  if (insights.length === 0) return null

  return (
    <section aria-labelledby="watch-heading">
      <div className="flex items-center gap-3 mb-3">
        <h2 id="watch-heading" className="text-base font-semibold text-[var(--text-primary)]">
          WATCH Intelligence
        </h2>
        <span className="px-2 py-0.5 rounded-full bg-[var(--color-warning-bg)] text-[11px] font-semibold text-[var(--color-warning-text)]">
          {insights.length} insight{insights.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 sm:gap-3">
        {insights.map((insight, index) => (
          <InsightCard key={`${insight.type}-${index}`} insight={insight} />
        ))}
      </div>

      {data && (
        <p className="mt-3 text-xs text-[var(--text-muted)] border-t border-[var(--border-subtle)] pt-3">
          Based on {data.watchCount} WATCH evaluations from the past 7 days
        </p>
      )}
    </section>
  )
}
