import {
  Building2,
  CheckCircle,
  XCircle,
  Clock,
  Shield,
  AlertCircle,
  Loader2,
} from 'lucide-react'
import type { StockSummary, StockSummaryStatus } from '@/lib/types'
import clsx from 'clsx'

// ============================================================================
// Risk Level Badge
// ============================================================================

function RiskLevelBadge({ level }: { level: string }) {
  const config: Record<string, { color: string; label: string }> = {
    LOW: { color: 'bg-oss-approve/10 text-oss-approve border-oss-approve/30', label: 'Low Risk' },
    MODERATE: { color: 'bg-oss-watch/10 text-oss-watch border-oss-watch/30', label: 'Moderate Risk' },
    ELEVATED: { color: 'bg-orange-500/10 text-orange-400 border-orange-500/30', label: 'Elevated Risk' },
    HIGH: { color: 'bg-oss-reject/10 text-oss-reject border-oss-reject/30', label: 'High Risk' },
  }

  const cfg = config[level] || config.MODERATE

  return (
    <span className={clsx(
      'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium',
      cfg.color
    )}>
      <Shield className="h-3 w-3" />
      {cfg.label}
    </span>
  )
}

// ============================================================================
// Status Badge
// ============================================================================

function SummaryStatusBadge({ status }: { status: StockSummaryStatus }) {
  const config = {
    COMPLETED: {
      icon: CheckCircle,
      color: 'bg-oss-approve/10 text-oss-approve border-oss-approve/30',
      label: 'Generated',
    },
    FAILED: {
      icon: XCircle,
      color: 'bg-oss-reject/10 text-oss-reject border-oss-reject/30',
      label: 'Failed',
    },
    RATE_LIMITED: {
      icon: Clock,
      color: 'bg-oss-watch/10 text-oss-watch border-oss-watch/30',
      label: 'Rate Limited',
    },
    GENERATING: {
      icon: Loader2,
      color: 'bg-oss-accent/10 text-oss-accent border-oss-accent/30',
      label: 'Generating...',
    },
    NOT_FOUND: {
      icon: AlertCircle,
      color: 'bg-oss-muted/10 text-oss-muted border-oss-muted/30',
      label: 'Not Generated',
    },
  }[status] || {
    icon: AlertCircle,
    color: 'bg-oss-muted/10 text-oss-muted border-oss-muted/30',
    label: status,
  }

  const Icon = config.icon

  return (
    <span className={clsx(
      'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium',
      config.color
    )}>
      <Icon className={clsx('h-3 w-3', status === 'GENERATING' && 'animate-spin')} />
      {config.label}
    </span>
  )
}

// ============================================================================
// Severity dot color
// ============================================================================

function severityDotColor(severity: string): string {
  return severity === 'HIGH'
    ? 'bg-oss-reject'
    : severity === 'MEDIUM'
      ? 'bg-oss-watch'
      : 'bg-oss-muted'
}

// ============================================================================
// Placeholder (not yet generated)
// ============================================================================

interface PlaceholderProps {
  onGenerate?: () => void
  isGenerating?: boolean
  generateError?: Error | null
}

function SummaryPlaceholder({ onGenerate, isGenerating, generateError }: PlaceholderProps) {
  return (
    <div className="rounded-xl border border-oss-border border-dashed bg-oss-surface/50 p-6">
      <div className="flex items-center gap-3 mb-4">
        <Building2 className="h-5 w-5 text-oss-muted" />
        <h3 className="text-lg font-medium text-oss-muted">AI Stock Summary</h3>
      </div>
      <div className="flex flex-col items-center gap-3 py-4">
        {isGenerating ? (
          <>
            <Loader2 className="h-6 w-6 text-oss-accent animate-spin" />
            <p className="text-sm text-oss-muted">Analyzing underlying stock...</p>
          </>
        ) : (
          <>
            <p className="text-sm text-oss-muted">
              No stock summary generated yet.
            </p>
            {onGenerate && (
              <button
                onClick={onGenerate}
                className="inline-flex items-center gap-2 rounded-lg bg-oss-accent/10 px-4 py-2 text-sm font-medium text-oss-accent hover:bg-oss-accent/20 transition-colors"
              >
                <Building2 className="h-4 w-4" />
                Generate Stock Summary
              </button>
            )}
            {generateError && (
              <p className="text-xs text-oss-reject mt-1">
                {generateError.message}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ============================================================================
// Main Component
// ============================================================================

interface AIStockSummaryProps {
  summary: StockSummary | null | undefined
  onGenerate?: () => void
  isGenerating?: boolean
  generateError?: Error | null
}

export default function AIStockSummary({
  summary,
  onGenerate,
  isGenerating,
  generateError,
}: AIStockSummaryProps) {
  // Generating state
  if (summary?.status === 'GENERATING' || isGenerating) {
    return <SummaryPlaceholder onGenerate={undefined} isGenerating={true} generateError={null} />
  }

  // Not found / no summary / failed — show placeholder with generate button
  if (!summary || summary.status === 'NOT_FOUND' || summary.status === 'FAILED') {
    return (
      <SummaryPlaceholder
        onGenerate={onGenerate}
        isGenerating={false}
        generateError={generateError}
      />
    )
  }

  // Rate limited
  if (summary.status === 'RATE_LIMITED') {
    return (
      <div className="rounded-xl border border-oss-watch/30 bg-oss-watch/5 p-6">
        <div className="flex items-center gap-3">
          <Clock className="h-5 w-5 text-oss-watch" />
          <div>
            <h3 className="text-lg font-medium text-oss-text">AI Stock Summary</h3>
            <p className="text-sm text-oss-muted">{summary.error_message || 'Daily rate limit reached'}</p>
          </div>
        </div>
      </div>
    )
  }

  // Completed — render compact summary
  const generatedDate = new Date(summary.generated_at).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })

  return (
    <div className="rounded-xl border border-sky-500/30 bg-gradient-to-br from-oss-surface to-sky-500/5 p-5">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-sky-500/10 p-1.5">
            <Building2 className="h-4 w-4 text-sky-400" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-oss-text">AI Stock Summary</h3>
            <p className="text-[11px] text-oss-muted">
              {summary.llm_provider} · {summary.model_used} · {summary.tokens_used} tokens · {generatedDate}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {summary.risk_level && <RiskLevelBadge level={summary.risk_level} />}
          <SummaryStatusBadge status={summary.status} />
        </div>
      </div>

      {/* Company Snapshot + Sector Context — plain text */}
      <p className="text-sm text-oss-text leading-relaxed mb-1">
        {summary.company_snapshot}
      </p>
      {summary.sector_context && (
        <p className="text-sm text-oss-text/70 leading-relaxed mb-4">
          {summary.sector_context}
        </p>
      )}
      {!summary.sector_context && <div className="mb-4" />}

      {/* Material Events — compact list */}
      {summary.material_events.length > 0 && (
        <div className="mb-4">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-oss-muted mb-2">
            Material Events
          </h4>
          <div className="space-y-2">
            {summary.material_events.map((ev, idx) => (
              <div key={idx} className="pl-3">
                <div className="flex items-center gap-2">
                  <span className={clsx('h-1.5 w-1.5 flex-shrink-0 rounded-full', severityDotColor(ev.severity))} />
                  <span className="text-sm font-medium text-oss-text">{ev.event}</span>
                  {ev.date && (
                    <span className="text-xs text-oss-muted">{ev.date}</span>
                  )}
                </div>
                <p className="text-sm text-oss-text/70 pl-3.5 leading-snug mt-0.5">
                  {ev.impact}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
      {summary.material_events.length === 0 && (
        <p className="text-sm text-oss-text/70 mb-4 flex items-center gap-2">
          <CheckCircle className="h-3.5 w-3.5 text-oss-approve" />
          No material events in the last 30 days
        </p>
      )}

      {/* Trading Considerations — plain bullet list */}
      {summary.trading_considerations.length > 0 && (
        <div className="mb-4">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-oss-muted mb-2">
            Trading Considerations
          </h4>
          <ul className="space-y-1 pl-3">
            {summary.trading_considerations.map((item, idx) => (
              <li key={idx} className="flex items-start gap-2 text-sm text-oss-text/80">
                <span className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-oss-watch" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Trade Impact — blockquote style */}
      {summary.trade_impact_assessment && (
        <div className="border-l-2 border-oss-accent/40 pl-3 mb-3">
          <p className="text-sm text-oss-text leading-relaxed">
            <span className="font-semibold text-oss-accent text-xs uppercase tracking-wide">Impact </span>
            {summary.trade_impact_assessment}
          </p>
        </div>
      )}

      {/* Risk Rationale — inline muted */}
      {summary.risk_level_rationale && (
        <p className="text-xs text-oss-muted">
          <span className="font-medium">Risk:</span> {summary.risk_level_rationale}
        </p>
      )}
    </div>
  )
}
