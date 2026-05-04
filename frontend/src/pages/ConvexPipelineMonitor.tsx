import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ChevronRight, AlertCircle } from 'lucide-react'

import { useConvexRuns, useConvexStageEvents } from '@/lib/convexApi'
import { usePageTitle } from '@/hooks/usePageTitle'
import type { ConvexStageEventRecord } from '@/lib/convexTypes'

const STAGE_LABELS = ['', 'Kinetic Universe', 'Catalyst + Direction', 'PL Pricing Pre-Screen', 'Contract Selection']

interface StageBreakdown {
  stage: number
  name: string
  in: number
  out: number
  dropped: number
}

function summarizeStages(events: ConvexStageEventRecord[]): StageBreakdown[] {
  const byStage = new Map<number, ConvexStageEventRecord[]>()
  for (const ev of events) {
    if (!byStage.has(ev.stage)) byStage.set(ev.stage, [])
    byStage.get(ev.stage)!.push(ev)
  }

  const breakdown: StageBreakdown[] = []
  for (let stage = 1; stage <= 4; stage++) {
    const stageEvents = byStage.get(stage) ?? []
    const passed = stageEvents.filter((e) => e.payload.result === 'PASS').length
    const failed = stageEvents.filter((e) => e.payload.result === 'FAIL').length
    breakdown.push({
      stage,
      name: STAGE_LABELS[stage],
      in: stageEvents.length,
      out: passed,
      dropped: failed,
    })
  }
  return breakdown
}

export default function ConvexPipelineMonitor() {
  usePageTitle('Pipeline Monitor')
  const { data: runsData, isLoading: runsLoading, error: runsError } = useConvexRuns(20)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const runs = useMemo(() => runsData?.runs ?? [], [runsData])

  // Auto-select the most recent run when the list first loads.
  const effectiveRunId = useMemo(() => {
    if (selectedRunId) return selectedRunId
    return runs[0]?.run_id ?? null
  }, [selectedRunId, runs])

  const { data: stageData, isLoading: stageLoading } = useConvexStageEvents(
    effectiveRunId ?? undefined,
  )

  const stageBreakdown = useMemo(
    () => (stageData ? summarizeStages(stageData.events) : []),
    [stageData],
  )

  const selectedRun = runs.find((r) => r.run_id === effectiveRunId)

  if (runsLoading) {
    return <p className="text-sm text-oss-muted">Loading recent runs…</p>
  }
  if (runsError) {
    return <p className="text-sm text-oss-reject">Failed to load runs.</p>
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-oss-text">
          <Activity className="h-5 w-5 text-oss-accent" />
          Pipeline Monitor
        </h1>
        <p className="text-sm text-oss-muted">
          Every recent Convex pipeline run, including days that produced zero finalised
          candidates. Click a run to inspect its 4-stage funnel.
        </p>
      </header>

      <div className="grid gap-5 lg:grid-cols-[300px_1fr]">
        {/* Sidebar: recent runs */}
        <aside className="rounded-lg border border-oss-border bg-oss-surface">
          <div className="border-b border-oss-border px-3 py-2 text-xs font-medium uppercase tracking-wide text-oss-muted">
            Recent runs
          </div>
          {runs.length === 0 ? (
            <p className="px-3 py-4 text-sm text-oss-muted">No recent runs found.</p>
          ) : (
            <ul className="divide-y divide-oss-border/60">
              {runs.map((run) => {
                const isActive = run.run_id === effectiveRunId
                const ts = run.generated_at.slice(0, 16).replace('T', ' ')
                return (
                  <li key={run.run_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedRunId(run.run_id)}
                      className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors ${
                        isActive
                          ? 'bg-oss-accent/10 text-oss-accent'
                          : 'text-oss-text hover:bg-oss-border/30'
                      }`}
                    >
                      <div>
                        <div className="font-mono text-xs">{run.run_id.slice(0, 8)}…</div>
                        <div className="text-xs text-oss-muted">{ts} UTC</div>
                      </div>
                      <div className="flex items-center gap-2 text-xs">
                        <span className="text-oss-approve">A:{run.tier_a}</span>
                        <span>B:{run.tier_b}</span>
                        <span className="text-oss-muted">C:{run.tier_c}</span>
                        <ChevronRight className="h-3 w-3 text-oss-muted" />
                      </div>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </aside>

        {/* Detail: 4-stage breakdown for selected run */}
        <section className="space-y-4">
          {!effectiveRunId ? (
            <p className="text-sm text-oss-muted">Select a run to inspect.</p>
          ) : (
            <>
              <div className="flex items-baseline justify-between">
                <h2 className="text-base font-semibold text-oss-text">
                  Run <span className="font-mono">{effectiveRunId}</span>
                </h2>
                <Link
                  to={`/convex/runs/${effectiveRunId}/failed`}
                  className="inline-flex items-center gap-1 text-sm text-oss-watch hover:text-oss-watch/80"
                >
                  <AlertCircle className="h-4 w-4" />
                  Failed candidates
                </Link>
              </div>

              {selectedRun && (
                <div className="grid gap-2 text-sm sm:grid-cols-4">
                  <div className="rounded-lg border border-oss-border bg-oss-surface p-3">
                    <div className="text-xs uppercase tracking-wide text-oss-muted">Tier A</div>
                    <div className="mt-1 text-2xl font-semibold text-oss-approve">{selectedRun.tier_a}</div>
                  </div>
                  <div className="rounded-lg border border-oss-border bg-oss-surface p-3">
                    <div className="text-xs uppercase tracking-wide text-oss-muted">Tier B</div>
                    <div className="mt-1 text-2xl font-semibold text-oss-text">{selectedRun.tier_b}</div>
                  </div>
                  <div className="rounded-lg border border-oss-border bg-oss-surface p-3">
                    <div className="text-xs uppercase tracking-wide text-oss-muted">Tier C</div>
                    <div className="mt-1 text-2xl font-semibold text-oss-muted">{selectedRun.tier_c}</div>
                  </div>
                  <div className="rounded-lg border border-oss-border bg-oss-surface p-3">
                    <div className="text-xs uppercase tracking-wide text-oss-muted">Total</div>
                    <div className="mt-1 text-2xl font-semibold text-oss-text">{selectedRun.finalised_count}</div>
                  </div>
                </div>
              )}

              {stageLoading ? (
                <p className="text-sm text-oss-muted">Loading stage events…</p>
              ) : (
                <div className="overflow-hidden rounded-lg border border-oss-border bg-oss-surface">
                  <table className="w-full text-sm">
                    <thead className="border-b border-oss-border bg-oss-bg/50 text-left text-xs uppercase tracking-wide text-oss-muted">
                      <tr>
                        <th className="px-3 py-2 font-medium">Stage</th>
                        <th className="px-3 py-2 font-medium">Name</th>
                        <th className="px-3 py-2 text-right font-medium">In</th>
                        <th className="px-3 py-2 text-right font-medium">Passed</th>
                        <th className="px-3 py-2 text-right font-medium">Dropped</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-oss-border/60">
                      {stageBreakdown.map((s) => {
                        const passRate = s.in > 0 ? (s.out / s.in) * 100 : 0
                        return (
                          <tr key={s.stage}>
                            <td className="px-3 py-2 font-mono">{s.stage}</td>
                            <td className="px-3 py-2">{s.name}</td>
                            <td className="px-3 py-2 text-right">{s.in}</td>
                            <td className="px-3 py-2 text-right text-oss-approve">{s.out}</td>
                            <td className="px-3 py-2 text-right text-oss-muted">
                              {s.dropped} <span className="text-xs">({passRate.toFixed(0)}% pass)</span>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}
