/**
 * Tab 3: Score Calibration
 * APPROVE vs WATCH comparison, pillar radar chart, score distribution.
 */

import {
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import clsx from 'clsx'
import type { EnrichedPosition } from '@/lib/types'

interface ScoreCalibrationProps {
  positions: EnrichedPosition[]
}

export default function ScoreCalibration({ positions }: ScoreCalibrationProps) {
  const closed = positions.filter((p) => p.status === 'CLOSED')

  // APPROVE vs WATCH comparison
  const approves = closed.filter((p) => p.verdict_at_entry === 'APPROVE')
  const watches = closed.filter((p) => p.verdict_at_entry === 'WATCH')

  const calcStats = (group: EnrichedPosition[]) => {
    const wins = group.filter((p) => p.current_pnl_pct > 0)
    const winRate = group.length > 0 ? (wins.length / group.length) * 100 : null
    const avgReturn =
      group.length > 0
        ? group.reduce((s, p) => s + p.current_pnl_pct, 0) / group.length
        : 0
    const scored = group.filter((p) => p.conviction_score != null)
    const avgScore =
      scored.length > 0
        ? scored.reduce((s, p) => s + (p.conviction_score ?? 0), 0) / scored.length
        : null
    return { count: group.length, winRate, avgReturn, avgScore }
  }

  const approveStats = calcStats(approves)
  const watchStats = calcStats(watches)

  const winRateDelta =
    approveStats.winRate != null && watchStats.winRate != null
      ? approveStats.winRate - watchStats.winRate
      : null

  // Pillar radar data
  const winners = positions.filter((p) => p.current_pnl_pct > 0)
  const losers = positions.filter((p) => p.current_pnl_pct <= 0)

  const avg = (arr: EnrichedPosition[], fn: (p: EnrichedPosition) => number | null) => {
    const valid = arr.map(fn).filter((v): v is number => v != null)
    return valid.length > 0 ? valid.reduce((s, v) => s + v, 0) / valid.length : 0
  }

  const maxEv = Math.max(
    ...positions.map((p) => Math.abs(p.theta_adj_ev ?? 0)),
    1
  )

  const radarData = [
    {
      axis: 'Directional',
      winners: avg(winners, (p) => p.pillar_directional),
      losers: avg(losers, (p) => p.pillar_directional),
    },
    {
      axis: 'Structure',
      winners: avg(winners, (p) => p.pillar_structure),
      losers: avg(losers, (p) => p.pillar_structure),
    },
    {
      axis: 'Volatility',
      winners: avg(winners, (p) => p.pillar_volatility),
      losers: avg(losers, (p) => p.pillar_volatility),
    },
    {
      axis: 'Convergence',
      winners: avg(winners, (p) => (p.convergence_count / 4) * 100),
      losers: avg(losers, (p) => (p.convergence_count / 4) * 100),
    },
    {
      axis: 'Theta-Adj EV',
      winners: avg(winners, (p) =>
        p.theta_adj_ev != null ? (p.theta_adj_ev / maxEv) * 100 : null
      ),
      losers: avg(losers, (p) =>
        p.theta_adj_ev != null ? (p.theta_adj_ev / maxEv) * 100 : null
      ),
    },
  ]

  // Score distribution
  const bands = [
    { label: '60-64', min: 60, max: 64 },
    { label: '65-69', min: 65, max: 69 },
    { label: '70-74', min: 70, max: 74 },
    { label: '75-79', min: 75, max: 79 },
    { label: '80-84', min: 80, max: 84 },
    { label: '85-89', min: 85, max: 89 },
    { label: '90+', min: 90, max: 100 },
  ]

  const distData = bands.map((band) => {
    const inBand = positions.filter(
      (p) =>
        p.conviction_score != null &&
        p.conviction_score >= band.min &&
        p.conviction_score <= band.max
    )
    const bandWinners = inBand.filter((p) => p.current_pnl_pct > 0).length
    const bandLosers = inBand.length - bandWinners
    const winRate = inBand.length > 0 ? (bandWinners / inBand.length) * 100 : 0
    return {
      band: band.label,
      winners: bandWinners,
      losers: bandLosers,
      winRate,
    }
  })

  const VerdictCard = ({
    label,
    stats,
    color,
  }: {
    label: string
    stats: { count: number; winRate: number | null; avgReturn: number; avgScore: number | null }
    color: string
  }) => (
    <div className="rounded-lg border border-oss-border p-4">
      <div className={clsx('text-sm font-medium mb-3', color)}>{label}</div>
      <div className="space-y-2 text-xs">
        <div className="flex justify-between">
          <span className="text-oss-muted">Count</span>
          <span className="text-oss-text">{stats.count}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-oss-muted">Win Rate</span>
          <span className="text-oss-text">
            {stats.winRate != null ? `${stats.winRate.toFixed(1)}%` : '--'}
          </span>
        </div>
        {stats.winRate != null && (
          <div className="h-1.5 rounded-full bg-oss-border overflow-hidden">
            <div
              className="h-full rounded-full bg-oss-approve"
              style={{ width: `${Math.min(stats.winRate, 100)}%` }}
            />
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-oss-muted">Avg Return</span>
          <span className={stats.avgReturn >= 0 ? 'text-oss-approve' : 'text-oss-reject'}>
            {stats.avgReturn.toFixed(1)}%
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-oss-muted">Avg Score</span>
          <span className="text-oss-accent">
            {stats.avgScore != null ? stats.avgScore.toFixed(0) : '--'}
          </span>
        </div>
      </div>
    </div>
  )

  return (
    <div className="space-y-6">
      {/* Row 1: APPROVE vs WATCH + Radar */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
          <h3 className="text-sm font-medium text-oss-text mb-3">APPROVE vs WATCH Comparison</h3>
          {closed.length === 0 ? (
            <div className="h-[200px] flex items-center justify-center text-oss-muted text-sm">
              No closed positions yet for comparison.
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4">
                <VerdictCard label="APPROVE" stats={approveStats} color="text-oss-approve" />
                <VerdictCard label="WATCH" stats={watchStats} color="text-oss-watch" />
              </div>
              {winRateDelta != null && winRateDelta < 10 && (
                <div className="mt-3 rounded-lg bg-oss-watch/10 border border-oss-watch/30 p-2 text-xs text-oss-watch">
                  Win rate delta between APPROVE and WATCH is only {winRateDelta.toFixed(1)}pp
                  (target: &gt;10pp). Consider tightening approval thresholds.
                </div>
              )}
            </>
          )}
        </div>

        <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
          <h3 className="text-sm font-medium text-oss-text mb-3">Pillar Contribution</h3>
          {positions.length === 0 ? (
            <div className="h-[280px] flex items-center justify-center text-oss-muted text-sm">
              No position data for radar chart.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <RadarChart data={radarData}>
                <PolarGrid stroke="var(--color-oss-border)" />
                <PolarAngleAxis
                  dataKey="axis"
                  tick={{ fontSize: 11, fill: 'var(--color-oss-muted)' }}
                />
                <PolarRadiusAxis
                  angle={90}
                  domain={[0, 100]}
                  tick={{ fontSize: 10, fill: 'var(--color-oss-muted)' }}
                />
                <Radar
                  name="Winners"
                  dataKey="winners"
                  stroke="var(--color-oss-approve)"
                  fill="var(--color-oss-approve)"
                  fillOpacity={0.2}
                />
                <Radar
                  name="Losers"
                  dataKey="losers"
                  stroke="var(--color-oss-reject)"
                  fill="var(--color-oss-reject)"
                  fillOpacity={0.2}
                />
                <Legend
                  wrapperStyle={{ fontSize: 11 }}
                />
              </RadarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Row 2: Score Distribution */}
      <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
        <h3 className="text-sm font-medium text-oss-text mb-3">Score Distribution</h3>
        {positions.filter((p) => p.conviction_score != null).length === 0 ? (
          <div className="h-[250px] flex items-center justify-center text-oss-muted text-sm">
            No scored positions to display.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={distData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-oss-border)" />
              <XAxis dataKey="band" tick={{ fontSize: 11, fill: 'var(--color-oss-muted)' }} />
              <YAxis tick={{ fontSize: 11, fill: 'var(--color-oss-muted)' }} />
              <Tooltip
                contentStyle={{
                  background: 'var(--color-oss-surface)',
                  border: '1px solid var(--color-oss-border)',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(value: number | undefined, name: string | undefined) => [
                  value ?? 0,
                  name === 'winners' ? 'Winners' : 'Losers',
                ]}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="winners" stackId="a" fill="var(--color-oss-approve)" name="Winners" />
              <Bar dataKey="losers" stackId="a" fill="var(--color-oss-reject)" name="Losers" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
