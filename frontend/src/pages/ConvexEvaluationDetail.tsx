import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Zap, ChevronDown, ChevronRight, CheckCircle, XCircle, TrendingUp, Sparkles } from 'lucide-react'
import { useState } from 'react'
import clsx from 'clsx'

import { useConvexEvaluation } from '@/lib/convexApi'
import type {
  ConvexEvaluation,
  ConvexStagePayload,
  ConvexTier,
} from '@/lib/convexTypes'
import { usePageTitle } from '@/hooks/usePageTitle'
import { formatDateTime } from '@/lib/formatTime'

const TIER_BADGE: Record<ConvexTier, string> = {
  A: 'bg-oss-approve/15 text-oss-approve border-oss-approve/30',
  B: 'bg-oss-watch/15 text-oss-watch border-oss-watch/30',
  C: 'bg-oss-muted/15 text-oss-muted border-oss-muted/30',
}

export default function ConvexEvaluationDetail() {
  const { ticker, evaluationId } = useParams<{ ticker: string; evaluationId: string }>()
  usePageTitle(`Convex / ${ticker ?? ''}`)

  const { data, isLoading, error } = useConvexEvaluation(ticker, evaluationId)

  if (isLoading) {
    return <p className="text-sm text-oss-muted">Loading…</p>
  }
  if (error) {
    return <p className="text-sm text-oss-reject">Failed to load Convex evaluation.</p>
  }
  if (!data) {
    return null
  }

  const evaluation = data.evaluation
  const stages = evaluation.decision.convex_stages

  return (
    <div className="space-y-6">
      <Link
        to="/convex"
        className="inline-flex items-center gap-2 text-sm text-oss-muted hover:text-oss-text"
      >
        <ArrowLeft className="h-4 w-4" /> Back to Convex
      </Link>

      <Header evaluation={evaluation} />

      {/* Stage walkthrough */}
      <div className="space-y-3">
        {stages?.stage_1 && (
          <StagePanel
            number={1}
            title="Why this stock can move"
            stage={stages.stage_1}
            tier={evaluation.convex_tier}
          />
        )}
        {stages?.stage_2 && (
          <StagePanel
            number={2}
            title="What's the catalyst"
            stage={stages.stage_2}
            tier={evaluation.convex_tier}
          />
        )}
        {stages?.stage_3 && (
          <StagePanel
            number={3}
            title="Why the options are cheap"
            stage={stages.stage_3}
            tier={evaluation.convex_tier}
          />
        )}
        {stages?.stage_4 && (
          <StagePanel
            number={4}
            title="Why this specific contract"
            stage={stages.stage_4}
            tier={evaluation.convex_tier}
          />
        )}
      </div>

      <FinalSummary evaluation={evaluation} />
    </div>
  )
}

function Header({ evaluation }: { evaluation: ConvexEvaluation }) {
  const contract = evaluation.selected_call ?? evaluation.selected_put
  return (
    <header className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-oss-muted">
            <Zap className="h-4 w-4 text-oss-accent" /> Convex Mode
          </div>
          <h1 className="mt-1 text-2xl font-semibold text-oss-text">{evaluation.ticker}</h1>
          {contract && (
            <p className="mt-1 text-sm text-oss-muted">
              {contract.option_type} ${contract.strike} expiring {contract.expiry} (Δ{' '}
              {contract.delta.toFixed(2)}, {contract.dte} DTE)
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span
            className={clsx(
              'inline-flex items-center rounded-md border px-3 py-1 text-sm font-bold',
              TIER_BADGE[evaluation.convex_tier],
            )}
          >
            Tier {evaluation.convex_tier}
          </span>
          <DirectionBadge direction={evaluation.direction} />
          {evaluation.smart_money_confirmation && (
            <span className="inline-flex items-center gap-1 rounded-md border border-oss-accent/40 bg-oss-accent/10 px-3 py-1 text-sm text-oss-accent">
              <Sparkles className="h-3.5 w-3.5" /> Smart Money
            </span>
          )}
        </div>
      </div>

      <p className="mt-3 text-xs text-oss-muted">
        Generated {formatDateTime(evaluation.generated_at)} · Run{' '}
        <span className="font-mono">{evaluation.run_id.slice(0, 8)}…</span>
      </p>
    </header>
  )
}

function DirectionBadge({ direction }: { direction: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    bullish: { label: 'Bullish', cls: 'border-oss-approve/40 bg-oss-approve/10 text-oss-approve' },
    bearish: { label: 'Bearish', cls: 'border-oss-reject/40 bg-oss-reject/10 text-oss-reject' },
    ambiguous: { label: 'Straddle', cls: 'border-oss-muted/40 bg-oss-muted/10 text-oss-muted' },
  }
  const meta = map[direction] ?? { label: direction, cls: 'border-oss-muted/40 text-oss-muted' }
  return (
    <span className={clsx('inline-flex items-center gap-1 rounded-md border px-3 py-1 text-sm', meta.cls)}>
      <TrendingUp className="h-3.5 w-3.5" /> {meta.label}
    </span>
  )
}

function StagePanel({
  number,
  title,
  stage,
  tier,
}: {
  number: number
  title: string
  stage: ConvexStagePayload
  tier: ConvexTier
}) {
  // Default expanded for Tier A; collapsed for B/C.
  const [expanded, setExpanded] = useState(tier === 'A')
  const passed = stage.result === 'PASS'
  return (
    <div
      className={clsx(
        'rounded-xl border bg-oss-surface',
        passed ? 'border-oss-border' : 'border-oss-reject/40',
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div className="flex items-center gap-3">
          {passed ? (
            <CheckCircle className="h-5 w-5 text-oss-approve" />
          ) : (
            <XCircle className="h-5 w-5 text-oss-reject" />
          )}
          <div>
            <p className="text-xs uppercase tracking-wide text-oss-muted">
              Stage {number} · {stage.stage_name}
            </p>
            <p className="text-sm font-medium text-oss-text">{title}</p>
          </div>
        </div>
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-oss-muted" />
        ) : (
          <ChevronRight className="h-4 w-4 text-oss-muted" />
        )}
      </button>

      {expanded && (
        <div className="space-y-3 border-t border-oss-border px-4 pb-4 pt-3">
          <p className="text-sm leading-relaxed text-oss-text">{stage.summary}</p>

          {Object.keys(stage.criteria).length > 0 && (
            <div className="rounded-lg border border-oss-border/60 bg-oss-bg/30 p-3">
              <p className="mb-2 text-xs uppercase tracking-wide text-oss-muted">Criteria</p>
              <CriteriaList criteria={stage.criteria} />
            </div>
          )}

          {stage.strength !== null && (
            <p className="text-xs text-oss-muted">
              Stage strength: <span className="font-mono">{stage.strength.toFixed(2)}</span>
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function CriteriaList({ criteria }: { criteria: Record<string, unknown> }) {
  // Render top-level criteria entries; nested objects are shown JSON-style.
  return (
    <dl className="space-y-1.5 text-xs">
      {Object.entries(criteria).map(([key, value]) => (
        <div key={key} className="grid grid-cols-[140px_1fr] gap-3">
          <dt className="text-oss-muted">{humaniseKey(key)}</dt>
          <dd className="font-mono text-oss-text">{formatCriterionValue(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function humaniseKey(key: string): string {
  return key
    .split('_')
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ')
}

function formatCriterionValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '✓ pass' : '✗ fail'
  if (typeof value === 'number') return value.toString()
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'object') {
    // Common shape: {pass: bool, value: string}
    const obj = value as Record<string, unknown>
    if ('pass' in obj && 'value' in obj) {
      return `${obj.pass ? '✓' : '✗'} ${obj.value}`
    }
    return JSON.stringify(obj)
  }
  return String(value)
}

function FinalSummary({ evaluation }: { evaluation: ConvexEvaluation }) {
  const sizing = evaluation.decision.position_sizing_recommendation
  const composite = evaluation.composite_strength
  return (
    <section className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <h2 className="text-sm font-semibold text-oss-text">Final Summary</h2>
      <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <dt className="text-xs uppercase tracking-wide text-oss-muted">Tier</dt>
          <dd className="text-base font-semibold text-oss-text">Tier {evaluation.convex_tier}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-oss-muted">Position sizing</dt>
          <dd className="text-base text-oss-text">{sizing ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-oss-muted">Composite strength</dt>
          <dd className="font-mono text-sm text-oss-text">{composite.toFixed(3)}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-oss-muted">Smart Money</dt>
          <dd className="text-base text-oss-text">
            {evaluation.smart_money_confirmation ? 'Confirmed' : 'Not confirmed'}
          </dd>
        </div>
      </dl>
    </section>
  )
}
