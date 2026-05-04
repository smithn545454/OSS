import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Zap, ArrowUpRight, Search, Activity } from 'lucide-react'
import clsx from 'clsx'

import { useConvexEvaluations, useConvexRuns } from '@/lib/convexApi'
import type { ConvexEvaluation, ConvexRunSummary, ConvexTier } from '@/lib/convexTypes'
import { usePageTitle } from '@/hooks/usePageTitle'
import { formatDateTime } from '@/lib/formatTime'
import { SmartMoneyBadge } from '@/components/convex/SmartMoneyBadge'

const TIER_BADGE: Record<ConvexTier, string> = {
  A: 'bg-oss-approve/15 text-oss-approve border border-oss-approve/30',
  B: 'bg-oss-watch/15 text-oss-watch border border-oss-watch/30',
  C: 'bg-oss-muted/15 text-oss-muted border border-oss-muted/30',
}

const DIRECTION_LABEL: Record<string, string> = {
  bullish: 'Bullish',
  bearish: 'Bearish',
  ambiguous: 'Straddle',
}

export default function ConvexOpportunities() {
  usePageTitle('Convex')
  const [tier, setTier] = useState<ConvexTier | undefined>(undefined)
  const [filter, setFilter] = useState('')

  const { data, isLoading, error } = useConvexEvaluations(tier, 100)
  const evaluations = data?.evaluations ?? []

  const filtered = filter
    ? evaluations.filter((e) =>
        e.ticker.toLowerCase().includes(filter.toLowerCase()),
      )
    : evaluations

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-oss-text">
            <Zap className="h-5 w-5 text-oss-accent" />
            Convex Mode
          </h1>
          <p className="text-sm text-oss-muted">
            Asymmetric long-premium "exploder" candidates from the four-stage gated pipeline.
          </p>
        </div>
      </header>

      {/* Tier filter */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-oss-muted uppercase tracking-wide">Tier</span>
        {(['all', 'A', 'B', 'C'] as const).map((t) => {
          const active = (t === 'all' && !tier) || tier === t
          return (
            <button
              key={t}
              type="button"
              onClick={() => setTier(t === 'all' ? undefined : (t as ConvexTier))}
              className={clsx(
                'rounded-md px-3 py-1 text-xs font-medium transition-colors',
                active
                  ? 'bg-oss-accent text-white'
                  : 'border border-oss-border text-oss-muted hover:bg-oss-border hover:text-oss-text',
              )}
            >
              {t === 'all' ? 'All Tiers' : `Tier ${t}`}
            </button>
          )
        })}

        <div className="ml-auto flex items-center gap-2 rounded-md border border-oss-border bg-oss-surface px-2 py-1.5">
          <Search className="h-4 w-4 text-oss-muted" />
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter ticker"
            className="bg-transparent text-sm text-oss-text outline-none placeholder:text-oss-muted"
          />
        </div>
      </div>

      {/* Results */}
      {isLoading ? (
        <p className="text-sm text-oss-muted">Loading…</p>
      ) : error ? (
        <p className="text-sm text-oss-reject">Failed to load Convex evaluations.</p>
      ) : filtered.length === 0 ? (
        <EmptyOpportunitiesState />
      ) : (
        <ConvexEvaluationsTable evaluations={filtered} />
      )}
    </div>
  )
}

function EmptyOpportunitiesState() {
  const { data, isLoading } = useConvexRuns(1)
  const latest = data?.runs?.[0]
  return (
    <div className="rounded-lg border border-oss-border bg-oss-surface p-4 text-sm">
      <p className="text-oss-text">
        No CONVEX_APPROVE candidates right now.
      </p>
      <p className="mt-1 text-xs text-oss-muted">
        The pipeline runs daily at 10:00 UTC. Some days legitimately produce zero finalised
        candidates — Convex is selective by design.
      </p>
      {isLoading ? null : latest ? (
        <LatestRunSummary run={latest} />
      ) : (
        <p className="mt-3 text-xs text-oss-muted">No recent pipeline runs found.</p>
      )}
    </div>
  )
}

function LatestRunSummary({ run }: { run: ConvexRunSummary }) {
  const ts = run.started_at ?? run.generated_at
  const universe = run.universe_size ?? 0
  const s2 = run.stage2_advancers ?? 0
  const s3 = run.stage3_advancers ?? 0
  const s4 = run.stage4_advancers ?? 0
  return (
    <div className="mt-3 flex flex-col gap-2 rounded-md border border-oss-border bg-oss-bg/40 p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="text-xs">
        <div className="flex items-center gap-2 text-oss-muted">
          <Activity className="h-3 w-3" />
          Latest run <span className="font-mono text-oss-text">{run.run_id.slice(0, 8)}…</span>
          <span>{formatDateTime(ts)}</span>
        </div>
        <div className="mt-1 font-mono text-oss-text">
          {universe} in universe → {s2} caught a catalyst → {s3} had cheap convexity → {s4}{' '}
          finalised
        </div>
      </div>
      <Link
        to={`/convex/runs/${run.run_id}/failed`}
        className="inline-flex items-center gap-1 text-xs text-oss-accent hover:underline"
      >
        See why <ArrowUpRight className="h-3 w-3" />
      </Link>
    </div>
  )
}

function ConvexEvaluationsTable({ evaluations }: { evaluations: ConvexEvaluation[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-oss-border bg-oss-surface">
      <table className="w-full text-sm">
        <thead className="border-b border-oss-border bg-oss-bg/50 text-left text-xs uppercase tracking-wide text-oss-muted">
          <tr>
            <th className="px-3 py-2 font-medium">Ticker</th>
            <th className="px-3 py-2 font-medium">Tier</th>
            <th className="px-3 py-2 font-medium">PL</th>
            <th className="px-3 py-2 font-medium">Direction</th>
            <th className="px-3 py-2 font-medium">Contract</th>
            <th className="px-3 py-2 font-medium">Δ</th>
            <th className="px-3 py-2 font-medium">DTE</th>
            <th className="px-3 py-2 font-medium">Sizing</th>
            <th className="px-3 py-2 font-medium">Smart $</th>
            <th className="px-3 py-2 font-medium">Generated</th>
            <th className="px-3 py-2 font-medium" />
          </tr>
        </thead>
        <tbody className="divide-y divide-oss-border/60">
          {evaluations.map((ev) => (
            <ConvexEvaluationRow key={ev.evaluation_id} evaluation={ev} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ConvexEvaluationRow({ evaluation }: { evaluation: ConvexEvaluation }) {
  const contract = evaluation.selected_call ?? evaluation.selected_put
  const sizing = evaluation.decision.position_sizing_recommendation
  return (
    <tr className="hover:bg-oss-border/40 transition-colors">
      <td className="px-3 py-2 font-mono font-semibold text-oss-text">{evaluation.ticker}</td>
      <td className="px-3 py-2">
        <span
          className={clsx(
            'inline-flex rounded px-2 py-0.5 text-xs font-bold',
            TIER_BADGE[evaluation.convex_tier],
          )}
        >
          {evaluation.convex_tier}
        </span>
      </td>
      <td className="px-3 py-2 font-mono text-xs font-semibold">
        {evaluation.pl_score != null ? evaluation.pl_score.toFixed(0) : '—'}
      </td>
      <td className="px-3 py-2 text-oss-muted">
        {DIRECTION_LABEL[evaluation.direction] ?? evaluation.direction}
      </td>
      <td className="px-3 py-2 font-mono text-xs text-oss-muted">
        {contract
          ? `${contract.option_type} $${contract.strike} ${contract.expiry}`
          : '—'}
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        {contract ? contract.delta.toFixed(2) : '—'}
      </td>
      <td className="px-3 py-2 font-mono text-xs">{contract ? contract.dte : '—'}</td>
      <td className="px-3 py-2 text-xs text-oss-muted">{sizing ?? '—'}</td>
      <td className="px-3 py-2 text-xs">
        <SmartMoneyBadge
          confirmed={evaluation.smart_money_confirmation}
          signal={evaluation.decision.convex_uv_signal}
          compact
        />
      </td>
      <td className="px-3 py-2 text-xs text-oss-muted">
        {formatDateTime(evaluation.generated_at)}
      </td>
      <td className="px-3 py-2">
        <Link
          to={`/convex/${evaluation.ticker}/${evaluation.evaluation_id}`}
          className="inline-flex items-center gap-1 rounded text-xs text-oss-accent hover:underline"
        >
          Detail <ArrowUpRight className="h-3 w-3" />
        </Link>
      </td>
    </tr>
  )
}
