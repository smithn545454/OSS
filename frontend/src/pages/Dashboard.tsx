import { TrendingUp, Activity, Eye, XCircle, CheckCircle } from 'lucide-react'
import { usePipelineStats, useActivePolicy, usePipelineRuns } from '@/hooks/useApi'
import RepresentativeTraces from '@/components/RepresentativeTraces'
import { formatDateTime, formatDate } from '@/lib/formatTime'
import clsx from 'clsx'

interface StatCardProps {
  label: string
  value: string | number
  subtext?: string
  icon: React.ReactNode
  trend?: 'up' | 'down' | 'neutral'
  color?: 'default' | 'approve' | 'watch' | 'reject'
}

function StatCard({ label, value, subtext, icon, color = 'default' }: StatCardProps) {
  const colorClasses = {
    default: 'text-oss-accent',
    approve: 'text-oss-approve',
    watch: 'text-oss-watch',
    reject: 'text-oss-reject',
  }

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-oss-muted">{label}</p>
          <p className={clsx('mt-2 text-3xl font-semibold', colorClasses[color])}>
            {value}
          </p>
          {subtext && <p className="mt-1 text-xs text-oss-muted">{subtext}</p>}
        </div>
        <div className="rounded-lg bg-oss-bg p-2 text-oss-muted">{icon}</div>
      </div>
    </div>
  )
}

function VerdictDistribution({
  approves,
  watches,
  rejects,
}: {
  approves: number
  watches: number
  rejects: number
}) {
  const total = approves + watches + rejects
  if (total === 0) return null

  const approveWidth = (approves / total) * 100
  const watchWidth = (watches / total) * 100
  const rejectWidth = (rejects / total) * 100

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <h3 className="text-sm font-medium text-oss-text">Verdict Distribution</h3>
      <div className="mt-4 flex h-4 overflow-hidden rounded-full bg-oss-bg">
        {approveWidth > 0 && (
          <div
            className="bg-oss-approve transition-all duration-500"
            style={{ width: `${approveWidth}%` }}
          />
        )}
        {watchWidth > 0 && (
          <div
            className="bg-oss-watch transition-all duration-500"
            style={{ width: `${watchWidth}%` }}
          />
        )}
        {rejectWidth > 0 && (
          <div
            className="bg-oss-reject transition-all duration-500"
            style={{ width: `${rejectWidth}%` }}
          />
        )}
      </div>
      <div className="mt-4 flex justify-between text-xs">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-oss-approve" />
          <span className="text-oss-muted">Approve</span>
          <span className="font-mono text-oss-approve">{approves}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-oss-watch" />
          <span className="text-oss-muted">Watch</span>
          <span className="font-mono text-oss-watch">{watches}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-oss-reject" />
          <span className="text-oss-muted">Reject</span>
          <span className="font-mono text-oss-reject">{rejects}</span>
        </div>
      </div>
    </div>
  )
}

function RecentRuns() {
  const { data, isLoading } = usePipelineRuns(5)

  if (isLoading) {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
        <h3 className="text-sm font-medium text-oss-text">Recent Pipeline Runs</h3>
        <div className="mt-4 animate-pulse space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-12 rounded-lg bg-oss-bg" />
          ))}
        </div>
      </div>
    )
  }

  const runs = data?.runs || []

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <h3 className="text-sm font-medium text-oss-text">Recent Pipeline Runs</h3>
      <div className="mt-4 space-y-3">
        {runs.length === 0 ? (
          <p className="text-sm text-oss-muted">No pipeline runs yet</p>
        ) : (
          runs.map((run) => (
            <div
              key={run.run_id}
              className="flex items-center justify-between rounded-lg bg-oss-bg p-3"
            >
              <div className="flex items-center gap-3">
                <div
                  className={clsx(
                    'h-2 w-2 rounded-full',
                    run.status === 'COMPLETED' && 'bg-oss-approve',
                    run.status === 'RUNNING' && 'bg-oss-accent animate-pulse',
                    run.status === 'FAILED' && 'bg-oss-reject'
                  )}
                />
                <div>
                  <p className="text-sm font-medium text-oss-text">
                    {run.policy_version}
                  </p>
                  <p className="text-xs text-oss-muted">
                    {formatDateTime(run.started_at)}
                  </p>
                </div>
              </div>
              <div className="text-right">
                <p className="text-sm font-mono text-oss-muted">
                  {run.total_evaluations} evals
                </p>
                <p className="text-xs text-oss-approve">
                  {run.total_approves} approves
                </p>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { data: stats, isLoading: statsLoading } = usePipelineStats(7)
  const { data: policy, isLoading: policyLoading } = useActivePolicy()

  const isLoading = statsLoading || policyLoading

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold text-oss-text">Dashboard</h1>
        <p className="mt-1 text-sm text-oss-muted">
          Option Scanner System overview and statistics
        </p>
      </div>

      {/* Active Policy Banner */}
      {policy && (
        <div className="rounded-xl border border-oss-accent/30 bg-oss-accent/5 p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="rounded-lg bg-oss-accent/10 p-2 text-oss-accent">
                <CheckCircle className="h-5 w-5" />
              </div>
              <div>
                <p className="text-sm font-medium text-oss-text">
                  Active Policy: {policy.version}
                </p>
                <p className="text-xs text-oss-muted">
                  Created by {policy.created_by} on{' '}
                  {formatDate(policy.created_at)}
                </p>
              </div>
            </div>
            <span className="rounded-full bg-oss-accent/10 px-3 py-1 text-xs font-medium text-oss-accent">
              Active
            </span>
          </div>
        </div>
      )}

      {/* Stats Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="h-32 animate-pulse rounded-xl border border-oss-border bg-oss-surface"
            />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Pipeline Runs (7d)"
            value={stats?.total_runs || 0}
            subtext={`${stats?.completed_runs || 0} completed`}
            icon={<Activity className="h-5 w-5" />}
          />
          <StatCard
            label="Approve Rate"
            value={`${stats?.approve_rate_pct?.toFixed(1) || 0}%`}
            subtext={`${stats?.total_approves || 0} total`}
            icon={<TrendingUp className="h-5 w-5" />}
            color="approve"
          />
          <StatCard
            label="Watch Rate"
            value={`${stats?.watch_rate_pct?.toFixed(1) || 0}%`}
            subtext={`${stats?.total_watches || 0} total`}
            icon={<Eye className="h-5 w-5" />}
            color="watch"
          />
          <StatCard
            label="Reject Rate"
            value={`${stats?.reject_rate_pct?.toFixed(1) || 0}%`}
            subtext={`${stats?.total_rejects || 0} total`}
            icon={<XCircle className="h-5 w-5" />}
            color="reject"
          />
        </div>
      )}

      {/* Charts Row */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <VerdictDistribution
          approves={stats?.total_approves || 0}
          watches={stats?.total_watches || 0}
          rejects={stats?.total_rejects || 0}
        />
        <RecentRuns />
      </div>

      {/* Representative Traces - Section 18.3 */}
      <RepresentativeTraces />

      {/* Additional Stats */}
      {stats && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          <StatCard
            label="Total Opportunities"
            value={stats.total_opportunities.toLocaleString()}
            subtext={`~${stats.avg_opportunities_per_run.toFixed(0)} per run`}
            icon={<TrendingUp className="h-5 w-5" />}
          />
          <StatCard
            label="Total Evaluations"
            value={stats.total_evaluations.toLocaleString()}
            subtext={`~${stats.avg_evaluations_per_run.toFixed(0)} per run`}
            icon={<Activity className="h-5 w-5" />}
          />
          <StatCard
            label="Failed Runs"
            value={stats.failed_runs}
            subtext={stats.failed_runs === 0 ? 'All runs successful' : 'Needs attention'}
            icon={<XCircle className="h-5 w-5" />}
            color={stats.failed_runs > 0 ? 'reject' : 'default'}
          />
        </div>
      )}
    </div>
  )
}
