import {
  Building2,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Clock,
  Shield,
  AlertCircle,
  Loader2,
  Newspaper,
  TrendingUp,
} from 'lucide-react'
import type { StockSummary, StockSummaryStatus, MaterialEvent } from '@/lib/types'
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
// Severity Badge for Material Events
// ============================================================================

function SeverityBadge({ severity }: { severity: string }) {
  const config: Record<string, string> = {
    HIGH: 'bg-oss-reject/10 text-oss-reject border-oss-reject/30',
    MEDIUM: 'bg-oss-watch/10 text-oss-watch border-oss-watch/30',
    LOW: 'bg-oss-muted/10 text-oss-muted border-oss-muted/30',
  }

  return (
    <span className={clsx(
      'inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase',
      config[severity] || config.MEDIUM
    )}>
      {severity}
    </span>
  )
}

// ============================================================================
// Material Events List
// ============================================================================

function MaterialEventsList({ events }: { events: MaterialEvent[] }) {
  if (!events.length) {
    return (
      <div className="rounded-lg border border-oss-approve/30 bg-oss-approve/5 p-3">
        <p className="text-sm text-oss-text flex items-center gap-2">
          <CheckCircle className="h-4 w-4 text-oss-approve" />
          No material events found in the last 30 days
        </p>
      </div>
    )
  }

  return (
    <ul className="space-y-2">
      {events.map((ev, idx) => (
        <li
          key={idx}
          className={clsx(
            'rounded-lg border p-3',
            ev.severity === 'HIGH'
              ? 'border-oss-reject/30 bg-oss-reject/5'
              : ev.severity === 'MEDIUM'
                ? 'border-oss-watch/30 bg-oss-watch/5'
                : 'border-oss-border bg-oss-bg'
          )}
        >
          <div className="flex items-start justify-between gap-2 mb-1">
            <span className="text-sm font-medium text-oss-text">{ev.event}</span>
            <SeverityBadge severity={ev.severity} />
          </div>
          {ev.date && (
            <p className="text-xs text-oss-muted mb-1">{ev.date}</p>
          )}
          <p className="text-sm text-oss-text/80">{ev.impact}</p>
        </li>
      ))}
    </ul>
  )
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

  // Completed — render full summary
  return (
    <div className="rounded-xl border border-sky-500/30 bg-gradient-to-br from-oss-surface to-sky-500/5 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-sky-500/10 p-2">
            <Building2 className="h-5 w-5 text-sky-400" />
          </div>
          <div>
            <h3 className="text-lg font-medium text-oss-text">AI Stock Summary</h3>
            <p className="text-xs text-oss-muted">
              Generated by {summary.llm_provider} · {summary.model_used} · {summary.tokens_used} tokens
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {summary.risk_level && <RiskLevelBadge level={summary.risk_level} />}
          <SummaryStatusBadge status={summary.status} />
        </div>
      </div>

      {/* Company Snapshot */}
      <div className="mb-6 rounded-lg bg-oss-bg border border-oss-border p-4">
        <p className="text-sm text-oss-text leading-relaxed font-medium">
          {summary.company_snapshot}
        </p>
        {summary.sector_context && (
          <p className="text-sm text-oss-text/70 mt-2 leading-relaxed">
            {summary.sector_context}
          </p>
        )}
      </div>

      {/* Material Events */}
      <div className="space-y-3 mb-6">
        <div className="flex items-center gap-2">
          <Newspaper className="h-4 w-4 text-sky-400" />
          <h4 className="text-sm font-medium text-oss-text">Material Events</h4>
        </div>
        <MaterialEventsList events={summary.material_events} />
      </div>

      <div className="border-t border-oss-border my-6" />

      {/* Trading Considerations */}
      {summary.trading_considerations.length > 0 && (
        <>
          <div className="space-y-3 mb-6">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-oss-watch" />
              <h4 className="text-sm font-medium text-oss-text">Trading Considerations</h4>
            </div>
            <ul className="space-y-2">
              {summary.trading_considerations.map((item, idx) => (
                <li
                  key={idx}
                  className="flex items-start gap-3 rounded-lg border border-oss-watch/30 bg-oss-watch/5 p-3 text-sm text-oss-text"
                >
                  <span className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-oss-watch" />
                  {item}
                </li>
              ))}
            </ul>
          </div>

          <div className="border-t border-oss-border my-6" />
        </>
      )}

      {/* Trade Impact Assessment */}
      {summary.trade_impact_assessment && (
        <div className="space-y-3 mb-6">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-oss-accent" />
            <h4 className="text-sm font-medium text-oss-text">Impact on This Trade</h4>
          </div>
          <div className="rounded-lg border border-oss-accent/30 bg-oss-accent/5 p-4">
            <p className="text-sm text-oss-text leading-relaxed">
              {summary.trade_impact_assessment}
            </p>
          </div>
        </div>
      )}

      {/* Risk Level Rationale */}
      {summary.risk_level_rationale && (
        <div className="rounded-lg bg-oss-bg border border-oss-border p-3">
          <p className="text-xs text-oss-muted">
            <span className="font-medium">Risk assessment:</span> {summary.risk_level_rationale}
          </p>
        </div>
      )}

      {/* Footer */}
      <div className="mt-6 pt-4 border-t border-oss-border">
        <p className="text-xs text-oss-muted text-center">
          Generated at {new Date(summary.generated_at).toLocaleString()}
        </p>
      </div>
    </div>
  )
}
