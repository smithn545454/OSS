import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, AlertCircle } from 'lucide-react'

import { useConvexFailedCandidates } from '@/lib/convexApi'
import { usePageTitle } from '@/hooks/usePageTitle'

const STAGE_LABELS = ['', 'Kinetic Universe', 'Catalyst + Direction', 'PL Pricing Pre-Screen', 'Contract Selection']

export default function ConvexFailedCandidates() {
  const { runId } = useParams<{ runId: string }>()
  usePageTitle(`Convex / failed candidates`)
  const { data, isLoading, error } = useConvexFailedCandidates(runId)

  if (isLoading) return <p className="text-sm text-oss-muted">Loading…</p>
  if (error) return <p className="text-sm text-oss-reject">Failed to load.</p>
  if (!data) return null

  const failures = data.failures

  return (
    <div className="space-y-5">
      <Link
        to="/convex"
        className="inline-flex items-center gap-2 text-sm text-oss-muted hover:text-oss-text"
      >
        <ArrowLeft className="h-4 w-4" /> Back to Convex
      </Link>

      <header>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-oss-text">
          <AlertCircle className="h-5 w-5 text-oss-watch" />
          Why didn't they make it?
        </h1>
        <p className="text-sm text-oss-muted">
          Run <span className="font-mono">{runId}</span> · {failures.length} candidates dropped before Stage 4.
        </p>
      </header>

      {failures.length === 0 ? (
        <p className="text-sm text-oss-muted">
          No failures recorded for this run — every candidate either advanced through all four stages
          or never entered the pipeline.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-oss-border bg-oss-surface">
          <table className="w-full text-sm">
            <thead className="border-b border-oss-border bg-oss-bg/50 text-left text-xs uppercase tracking-wide text-oss-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Ticker</th>
                <th className="px-3 py-2 font-medium">Failed at</th>
                <th className="px-3 py-2 font-medium">Highest passed</th>
                <th className="px-3 py-2 font-medium">Reason</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-oss-border/60">
              {failures.map((f) => (
                <tr key={f.ticker} className="hover:bg-oss-border/30">
                  <td className="px-3 py-2 font-mono font-semibold">{f.ticker}</td>
                  <td className="px-3 py-2">
                    <span className="rounded bg-oss-reject/15 px-2 py-0.5 text-xs text-oss-reject">
                      Stage {f.failed_at_stage}: {f.failed_stage_name}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs text-oss-muted">
                    {f.highest_stage_passed > 0
                      ? `Stage ${f.highest_stage_passed} (${STAGE_LABELS[f.highest_stage_passed]})`
                      : '—'}
                  </td>
                  <td className="px-3 py-2 text-xs text-oss-muted max-w-prose">{f.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
