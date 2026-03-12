import {
  Sparkles,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Clock,
  TrendingUp,
  Shield,
  Target,
  AlertCircle,
  Zap,
  Loader2
} from 'lucide-react'
import type { TradeThesis, ThesisStatus, TakeProfitTarget, StopLossTarget, TimeExitTarget } from '@/lib/types'
import clsx from 'clsx'

// ============================================================================
// Status Badge Component
// ============================================================================

interface StatusBadgeProps {
  status: ThesisStatus
}

function StatusBadge({ status }: StatusBadgeProps) {
  const config = {
    COMPLETED: { 
      icon: CheckCircle, 
      color: 'bg-oss-approve/10 text-oss-approve border-oss-approve/30',
      label: 'Generated'
    },
    FAILED: { 
      icon: XCircle, 
      color: 'bg-oss-reject/10 text-oss-reject border-oss-reject/30',
      label: 'Failed'
    },
    RATE_LIMITED: { 
      icon: Clock, 
      color: 'bg-oss-watch/10 text-oss-watch border-oss-watch/30',
      label: 'Rate Limited'
    },
    PENDING: {
      icon: Clock,
      color: 'bg-oss-muted/10 text-oss-muted border-oss-muted/30',
      label: 'Pending'
    },
    GENERATING: {
      icon: Loader2,
      color: 'bg-oss-accent/10 text-oss-accent border-oss-accent/30',
      label: 'Generating...'
    },
  }[status] || { icon: AlertCircle, color: 'bg-oss-muted/10 text-oss-muted border-oss-muted/30', label: status }

  const Icon = config.icon

  return (
    <span className={clsx(
      'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium',
      config.color
    )}>
      <Icon className="h-3 w-3" />
      {config.label}
    </span>
  )
}

// ============================================================================
// Section Components
// ============================================================================

interface SectionProps {
  title: string
  icon: React.ElementType
  iconColor?: string
  children: React.ReactNode
}

function Section({ title, icon: Icon, iconColor = 'text-oss-accent', children }: SectionProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Icon className={clsx('h-4 w-4', iconColor)} />
        <h4 className="text-sm font-medium text-oss-text">{title}</h4>
      </div>
      {children}
    </div>
  )
}

// ============================================================================
// Evidence List Component
// ============================================================================

interface EvidenceListProps {
  items: string[]
  variant?: 'default' | 'success' | 'warning' | 'danger'
}

function EvidenceList({ items, variant = 'default' }: EvidenceListProps) {
  const colors = {
    default: 'border-oss-border bg-oss-bg',
    success: 'border-oss-approve/30 bg-oss-approve/5',
    warning: 'border-oss-watch/30 bg-oss-watch/5',
    danger: 'border-oss-reject/30 bg-oss-reject/5',
  }

  const bulletColors = {
    default: 'bg-oss-accent',
    success: 'bg-oss-approve',
    warning: 'bg-oss-watch',
    danger: 'bg-oss-reject',
  }

  return (
    <ul className="space-y-2">
      {items.map((item, idx) => (
        <li 
          key={idx}
          className={clsx(
            'flex items-start gap-3 rounded-lg border p-3 text-sm text-oss-text',
            colors[variant]
          )}
        >
          <span className={clsx('mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full', bulletColors[variant])} />
          {item}
        </li>
      ))}
    </ul>
  )
}

// ============================================================================
// Exit Plan Card Component
// ============================================================================

interface ExitPlanCardProps {
  exitPlan: TradeThesis['exit_plan']
}

function TakeProfitRow({ target, isActive }: { target: TakeProfitTarget; isActive: boolean }) {
  const tierColors = {
    1: 'border-oss-approve/30 bg-oss-approve/5',
    2: 'border-oss-approve/40 bg-oss-approve/8',
    3: 'border-oss-approve/50 bg-oss-approve/10',
  }

  return (
    <div className={clsx(
      'rounded-lg border p-4',
      tierColors[target.tier as 1 | 2 | 3] || tierColors[1]
    )}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-oss-approve" />
          <span className="text-xs font-medium text-oss-approve">TP{target.tier}</span>
          {isActive && (
            <span className="inline-flex items-center rounded-full bg-oss-approve/20 px-2 py-0.5 text-[10px] font-medium text-oss-approve">
              Active Target
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-oss-approve">
            +{target.option_pnl_pct.toFixed(0)}%
          </span>
          <span className="text-sm font-medium text-oss-text">
            ${target.underlying_price.toFixed(2)}
          </span>
        </div>
      </div>
      <p className="text-xs text-oss-muted">{target.rationale}</p>
    </div>
  )
}

function StopLossRow({ target }: { target: StopLossTarget }) {
  return (
    <div className="rounded-lg border border-oss-reject/30 bg-oss-reject/5 p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-oss-reject" />
          <span className="text-xs font-medium text-oss-reject">Stop Loss</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-oss-reject">
            {target.option_pnl_pct.toFixed(0)}%
          </span>
          <span className="text-sm font-medium text-oss-text">
            ${target.underlying_price.toFixed(2)}
          </span>
        </div>
      </div>
      <p className="text-xs text-oss-muted">{target.rationale}</p>
    </div>
  )
}

function TimeExitRow({ target }: { target: TimeExitTarget }) {
  return (
    <div className="rounded-lg border border-oss-watch/30 bg-oss-watch/5 p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Clock className="h-4 w-4 text-oss-watch" />
          <span className="text-xs font-medium text-oss-watch">Time Exit</span>
        </div>
        <span className="text-sm font-semibold text-oss-watch">
          DTE &le; {target.dte_threshold}
        </span>
      </div>
      <p className="text-xs text-oss-muted">{target.rationale}</p>
    </div>
  )
}

function LegacyExitPlanCard({ exitPlan }: ExitPlanCardProps) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div className="rounded-lg border border-oss-approve/30 bg-oss-approve/5 p-4">
        <div className="flex items-center gap-2 mb-2">
          <TrendingUp className="h-4 w-4 text-oss-approve" />
          <span className="text-xs font-medium text-oss-approve">Profit Target</span>
        </div>
        <p className="text-sm text-oss-text">{exitPlan.profit_target}</p>
      </div>
      <div className="rounded-lg border border-oss-reject/30 bg-oss-reject/5 p-4">
        <div className="flex items-center gap-2 mb-2">
          <Shield className="h-4 w-4 text-oss-reject" />
          <span className="text-xs font-medium text-oss-reject">Stop Loss</span>
        </div>
        <p className="text-sm text-oss-text">{exitPlan.stop_loss}</p>
      </div>
      <div className="rounded-lg border border-oss-watch/30 bg-oss-watch/5 p-4">
        <div className="flex items-center gap-2 mb-2">
          <Clock className="h-4 w-4 text-oss-watch" />
          <span className="text-xs font-medium text-oss-watch">Time Exit</span>
        </div>
        <p className="text-sm text-oss-text">{exitPlan.time_exit}</p>
      </div>
    </div>
  )
}

function ExitPlanCard({ exitPlan }: ExitPlanCardProps) {
  const isStructured = exitPlan.take_profits && exitPlan.take_profits.length > 0

  if (!isStructured) {
    return <LegacyExitPlanCard exitPlan={exitPlan} />
  }

  return (
    <div className="space-y-3">
      {exitPlan.take_profits!.map((tp) => (
        <TakeProfitRow key={tp.tier} target={tp} isActive={tp.tier === 1} />
      ))}
      {exitPlan.stop_loss_level && (
        <StopLossRow target={exitPlan.stop_loss_level} />
      )}
      {exitPlan.time_exit_level && (
        <TimeExitRow target={exitPlan.time_exit_level} />
      )}
    </div>
  )
}

// ============================================================================
// Error State Component
// ============================================================================

interface ThesisErrorProps {
  status: ThesisStatus
  errorMessage: string | null
}

function ThesisError({ status, errorMessage }: ThesisErrorProps) {
  const isRateLimited = status === 'RATE_LIMITED'
  
  return (
    <div className={clsx(
      'rounded-xl border p-6',
      isRateLimited 
        ? 'border-oss-watch/30 bg-oss-watch/5' 
        : 'border-oss-reject/30 bg-oss-reject/5'
    )}>
      <div className="flex items-center gap-3 mb-4">
        <Sparkles className={clsx('h-5 w-5', isRateLimited ? 'text-oss-watch' : 'text-oss-reject')} />
        <h3 className="text-lg font-medium text-oss-text">AI Trade Thesis</h3>
        <StatusBadge status={status} />
      </div>
      
      <div className="flex items-start gap-3">
        <AlertTriangle className={clsx('h-5 w-5 flex-shrink-0', isRateLimited ? 'text-oss-watch' : 'text-oss-reject')} />
        <div>
          <p className="text-sm text-oss-text font-medium">
            {isRateLimited 
              ? 'Daily LLM limit reached' 
              : 'Thesis generation failed'
            }
          </p>
          {errorMessage && (
            <p className="text-xs text-oss-muted mt-1">{errorMessage}</p>
          )}
          {isRateLimited && (
            <p className="text-xs text-oss-muted mt-1">
              The thesis will be available when the daily limit resets.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Placeholder Component (no thesis yet)
// ============================================================================

interface ThesisPlaceholderProps {
  onGenerate?: () => void
  isGenerating?: boolean
  generateError?: Error | null
}

function ThesisPlaceholder({ onGenerate, isGenerating, generateError }: ThesisPlaceholderProps) {
  return (
    <div className="rounded-xl border border-oss-border border-dashed bg-oss-surface/50 p-6">
      <div className="flex items-center gap-3 mb-4">
        <Sparkles className="h-5 w-5 text-oss-muted" />
        <h3 className="text-lg font-medium text-oss-muted">AI Trade Thesis</h3>
      </div>
      <div className="flex flex-col items-center gap-3 py-4">
        {isGenerating ? (
          <>
            <Loader2 className="h-6 w-6 text-oss-accent animate-spin" />
            <p className="text-sm text-oss-muted">Generating thesis...</p>
          </>
        ) : (
          <>
            <p className="text-sm text-oss-muted">
              No thesis generated yet.
            </p>
            {onGenerate && (
              <button
                onClick={onGenerate}
                className="inline-flex items-center gap-2 rounded-lg bg-oss-accent/10 px-4 py-2 text-sm font-medium text-oss-accent hover:bg-oss-accent/20 transition-colors"
              >
                <Sparkles className="h-4 w-4" />
                Generate AI Thesis
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
// Main AITradeThesis Component
// ============================================================================

interface AITradeThesisProps {
  thesis: TradeThesis | null
  onGenerate?: () => void
  isGenerating?: boolean
  generateError?: Error | null
}

export default function AITradeThesis({ thesis, onGenerate, isGenerating, generateError }: AITradeThesisProps) {
  // Show spinner when async worker is generating (polling will pick up COMPLETED)
  if (thesis?.status === 'GENERATING' || isGenerating) {
    return <ThesisPlaceholder onGenerate={undefined} isGenerating={true} generateError={null} />
  }

  // Show placeholder with Generate button when no thesis or failed (allows retry)
  if (!thesis || thesis.status === 'FAILED' || thesis.status === 'PENDING') {
    return <ThesisPlaceholder onGenerate={onGenerate} isGenerating={false} generateError={generateError} />
  }

  // Rate limited — show specific message
  if (thesis.status === 'RATE_LIMITED') {
    return <ThesisError status={thesis.status} errorMessage={thesis.error_message} />
  }

  // Successful thesis
  return (
    <div className="rounded-xl border border-oss-accent/30 bg-gradient-to-br from-oss-surface to-oss-accent/5 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-oss-accent/10 p-2">
            <Sparkles className="h-5 w-5 text-oss-accent" />
          </div>
          <div>
            <h3 className="text-lg font-medium text-oss-text">AI Trade Thesis</h3>
            <p className="text-xs text-oss-muted">
              Generated by {thesis.llm_provider} · {thesis.model_used} · {thesis.tokens_used} tokens
            </p>
          </div>
        </div>
        <StatusBadge status={thesis.status} />
      </div>

      {/* Setup Summary */}
      <div className="mb-6 rounded-lg bg-oss-bg border border-oss-border p-4">
        <p className="text-sm text-oss-text leading-relaxed font-medium">
          {thesis.setup_summary}
        </p>
      </div>

      {/* Main Thesis */}
      <Section title="Trade Thesis" icon={Zap} iconColor="text-oss-accent">
        <div className="rounded-lg bg-oss-bg border border-oss-border p-4">
          <p className="text-sm text-oss-text leading-relaxed whitespace-pre-wrap">
            {thesis.thesis}
          </p>
        </div>
      </Section>

      <div className="my-6 border-t border-oss-border" />

      {/* Supporting Evidence */}
      <Section title="Supporting Evidence" icon={CheckCircle} iconColor="text-oss-approve">
        <EvidenceList items={thesis.supporting_evidence} variant="success" />
      </Section>

      <div className="my-6 border-t border-oss-border" />

      {/* Risks */}
      <Section title="Key Risks" icon={AlertTriangle} iconColor="text-oss-watch">
        <EvidenceList items={thesis.risks} variant="warning" />
      </Section>

      <div className="my-6 border-t border-oss-border" />

      {/* Invalidation Conditions */}
      <Section title="Invalidation Conditions" icon={XCircle} iconColor="text-oss-reject">
        <EvidenceList items={thesis.invalidation_conditions} variant="danger" />
      </Section>

      <div className="my-6 border-t border-oss-border" />

      {/* Exit Plan */}
      <Section title="Exit Strategy" icon={Target} iconColor="text-sky-400">
        <ExitPlanCard exitPlan={thesis.exit_plan} />
      </Section>

      {/* Footer */}
      <div className="mt-6 pt-4 border-t border-oss-border">
        <p className="text-xs text-oss-muted text-center">
          Generated at {new Date(thesis.generated_at).toLocaleString()}
        </p>
      </div>
    </div>
  )
}
