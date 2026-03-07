/**
 * Tab 1: Performance Overview
 * Equity curve, daily P&L, scanner performance, score band analysis, scatter plot.
 */

import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Cell,
} from 'recharts'
import { useEquityCurve } from '@/hooks/useApi'
import PerformanceBreakdown from './PerformanceBreakdown'
import type { EnrichedPosition, ScannerType } from '@/lib/types'

interface PerformanceOverviewProps {
  positions: EnrichedPosition[]
  period: string
  byScanner?: Record<string, ScannerMetrics>
}

interface ScannerMetrics {
  scanner_type?: string
  count?: number
  closed_count?: number
  win_count?: number
  loss_count?: number
  total_pnl?: number
}

const SCANNER_NAMES: Record<string, string> = {
  BREAKOUT: 'Breakout',
  UNUSUAL_VOLUME: 'Unusual Volume',
  COMPRESSION_EXPANSION: 'Compression',
  CHEAP_OPTIONS: 'Cheap Options',
  UNKNOWN: 'Other Scanners',
}

const SCANNER_KEYS: ScannerType[] = [
  'BREAKOUT',
  'UNUSUAL_VOLUME',
  'COMPRESSION_EXPANSION',
  'CHEAP_OPTIONS',
]

const SCORE_BANDS = [
  { label: '60-64', min: 60, max: 65 },
  { label: '65-69', min: 65, max: 70 },
  { label: '70-74', min: 70, max: 75 },
  { label: '75-79', min: 75, max: 80 },
  { label: '80-84', min: 80, max: 85 },
  { label: '85-89', min: 85, max: 90 },
  { label: '90+', min: 90, max: 101 },
]

export default function PerformanceOverview({
  positions,
  period,
  byScanner,
}: PerformanceOverviewProps) {
  const { data: equityData } = useEquityCurve(period)

  const curve = equityData?.curve ?? []

  // Scanner performance: build from all server-side keys that have data
  const scannerPerf = (() => {
    // Collect all scanner keys: standard ones + any from server data
    const allKeys = new Set<string>(SCANNER_KEYS as readonly string[])
    if (byScanner) {
      Object.keys(byScanner).forEach((k) => allKeys.add(k))
    }

    return Array.from(allKeys)
      .map((key) => {
        const serverData = byScanner?.[key]
        if (serverData && (serverData.count ?? 0) > 0) {
          const count = Number(serverData.count ?? 0)
          const closedCount = Number(serverData.closed_count ?? 0)
          const winCount = Number(serverData.win_count ?? 0)
          const winRate = closedCount > 0 ? (winCount / closedCount) * 100 : null
          const totalPnl = Number(serverData.total_pnl ?? 0)
          return {
            key,
            name: SCANNER_NAMES[key] || key,
            count,
            winRate,
            avgReturn: closedCount > 0 ? totalPnl / closedCount : 0,
            avgScore: null as number | null,
            totalPnl,
          }
        }

        // Fallback: client-side computation
        const group = positions.filter((p) => p.scanner_source === key)
        if (group.length === 0) return null
        const closed = group.filter((p) => p.status === 'CLOSED')
        const wins = closed.filter((p) => p.current_pnl_pct > 0)
        const winRate = closed.length > 0 ? (wins.length / closed.length) * 100 : null
        const avgReturn =
          group.reduce((s, p) => s + p.current_pnl_pct, 0) / group.length
        const scored = group.filter((p) => p.conviction_score != null)
        const avgScore =
          scored.length > 0
            ? scored.reduce((s, p) => s + (p.conviction_score ?? 0), 0) / scored.length
            : null
        const totalPnl = group.reduce((s, p) => {
          const price = p.status === 'CLOSED' && p.exit_price != null ? p.exit_price : p.current_price
          return s + (price - p.entry_price) * 100
        }, 0)

        return {
          key,
          name: SCANNER_NAMES[key] || key,
          count: group.length,
          winRate,
          avgReturn,
          avgScore,
          totalPnl,
        }
      })
      .filter((s): s is NonNullable<typeof s> => s != null)
      .sort((a, b) => b.count - a.count)
  })()

  // Score band analysis computed from positions (no calibration report dependency)
  // Uses "profitable %" — positions with positive current P&L — to include open positions
  const scoreBands = SCORE_BANDS.map((band) => {
    const inBand = positions.filter(
      (p) =>
        p.conviction_score != null &&
        p.conviction_score >= band.min &&
        p.conviction_score < band.max
    )
    const profitable = inBand.filter((p) => p.current_pnl_pct > 0)
    return {
      band: band.label,
      count: inBand.length,
      win_rate: inBand.length > 0 ? (profitable.length / inBand.length) * 100 : 0,
    }
  }).filter((b) => b.count > 0)

  // Scatter plot data
  const scatterData = positions
    .filter((p) => p.conviction_score != null)
    .map((p) => ({
      x: p.conviction_score ?? 0,
      y: p.current_pnl_pct,
      ticker: p.underlying_ticker,
      winner: p.current_pnl_pct > 0,
    }))

  return (
    <div className="space-y-6">
      {/* Row 1: Equity Curve + Daily P&L */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 rounded-lg border border-oss-border bg-oss-surface p-4">
          <h3 className="text-sm font-medium text-oss-text mb-3">Equity Curve</h3>
          {curve.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={curve}>
                <defs>
                  <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--color-oss-accent)" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="var(--color-oss-accent)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-oss-border)" />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--color-oss-muted)' }} />
                <YAxis tick={{ fontSize: 11, fill: 'var(--color-oss-muted)' }} />
                <Tooltip
                  contentStyle={{
                    background: 'var(--color-oss-surface)',
                    border: '1px solid var(--color-oss-border)',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke="var(--color-oss-accent)"
                  fill="url(#equityGrad)"
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[250px] flex items-center justify-center text-oss-muted text-sm">
              No equity data yet. Snapshots will appear after position updates.
            </div>
          )}
        </div>

        <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
          <h3 className="text-sm font-medium text-oss-text mb-3">Daily P&L</h3>
          {curve.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={curve}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-oss-border)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--color-oss-muted)' }} />
                <YAxis tick={{ fontSize: 11, fill: 'var(--color-oss-muted)' }} />
                <Tooltip
                  contentStyle={{
                    background: 'var(--color-oss-surface)',
                    border: '1px solid var(--color-oss-border)',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="daily_pnl">
                  {curve.map((entry, i) => (
                    <Cell
                      key={i}
                      fill={entry.daily_pnl >= 0 ? 'var(--color-oss-approve)' : 'var(--color-oss-reject)'}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[250px] flex items-center justify-center text-oss-muted text-sm">
              No daily P&L data yet.
            </div>
          )}
        </div>
      </div>

      {/* Row 2: Scanner Performance + Score Bands */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
          <h3 className="text-sm font-medium text-oss-text mb-3">Scanner Performance</h3>
          <div className="grid grid-cols-2 gap-3">
            {scannerPerf.map((s) => (
              <div key={s.key} className="rounded-lg border border-oss-border p-3">
                <div className="text-xs font-medium text-oss-text mb-2">{s.name}</div>
                <div className="text-xs text-oss-muted">
                  {s.count} trade{s.count !== 1 ? 's' : ''}
                </div>
                {s.winRate != null && (
                  <div className="mt-2">
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-oss-muted">Win Rate</span>
                      <span className="text-oss-text">{s.winRate.toFixed(0)}%</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-oss-border overflow-hidden">
                      <div
                        className="h-full rounded-full bg-oss-approve"
                        style={{ width: `${Math.min(s.winRate, 100)}%` }}
                      />
                    </div>
                  </div>
                )}
                <div className="mt-1 flex justify-between text-xs">
                  <span className="text-oss-muted">Avg Return</span>
                  <span className={s.avgReturn >= 0 ? 'text-oss-approve' : 'text-oss-reject'}>
                    {s.avgReturn.toFixed(1)}%
                  </span>
                </div>
                {s.avgScore != null && (
                  <div className="flex justify-between text-xs">
                    <span className="text-oss-muted">Avg Score</span>
                    <span className="text-oss-accent">{s.avgScore.toFixed(0)}</span>
                  </div>
                )}
                <div className="flex justify-between text-xs">
                  <span className="text-oss-muted">P&L</span>
                  <span className={s.totalPnl >= 0 ? 'text-oss-approve' : 'text-oss-reject'}>
                    ${s.totalPnl.toFixed(0)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
          <h3 className="text-sm font-medium text-oss-text mb-3">Score Band Analysis <span className="text-oss-muted font-normal">(% profitable)</span></h3>
          {scoreBands.length > 0 ? (
            <div className="space-y-3">
              {scoreBands.map((band, i) => (
                <div key={i} className="rounded border border-oss-border p-2">
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-oss-text font-medium">{band.band}</span>
                    <span className="text-oss-muted">
                      {band.count} trade{band.count !== 1 ? 's' : ''}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-1.5 rounded-full bg-oss-border overflow-hidden">
                      <div
                        className="h-full rounded-full bg-oss-accent"
                        style={{ width: `${Math.min(band.win_rate, 100)}%` }}
                      />
                    </div>
                    <span className="text-xs text-oss-text w-10 text-right">
                      {band.win_rate.toFixed(0)}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="h-[200px] flex items-center justify-center text-oss-muted text-sm">
              No scored positions to analyze.
            </div>
          )}
        </div>
      </div>

      {/* Row 3: Score vs Outcome Scatter */}
      <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
        <h3 className="text-sm font-medium text-oss-text mb-3">Score vs Outcome</h3>
        {scatterData.length > 0 ? (
          <ResponsiveContainer width="100%" height={300}>
            <ScatterChart>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-oss-border)" />
              <XAxis
                dataKey="x"
                name="Score"
                tick={{ fontSize: 11, fill: 'var(--color-oss-muted)' }}
                label={{ value: 'Conviction Score', position: 'insideBottom', offset: -5, fontSize: 11, fill: 'var(--color-oss-muted)' }}
              />
              <YAxis
                dataKey="y"
                name="P&L %"
                tick={{ fontSize: 11, fill: 'var(--color-oss-muted)' }}
                label={{ value: 'P&L %', angle: -90, position: 'insideLeft', fontSize: 11, fill: 'var(--color-oss-muted)' }}
              />
              <Tooltip
                cursor={{ strokeDasharray: '3 3' }}
                contentStyle={{
                  background: 'var(--color-oss-surface)',
                  border: '1px solid var(--color-oss-border)',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(value: number | undefined, name: string | undefined) => [
                  name === 'x' ? (value ?? 0).toFixed(0) : `${(value ?? 0).toFixed(1)}%`,
                  name === 'x' ? 'Score' : 'P&L',
                ]}
              />
              <ReferenceLine y={0} stroke="var(--color-oss-reject)" strokeDasharray="4 4" />
              <ReferenceLine x={75} stroke="var(--color-oss-accent)" strokeDasharray="4 4" />
              <Scatter data={scatterData.filter((d) => d.winner)} fill="var(--color-oss-approve)" />
              <Scatter data={scatterData.filter((d) => !d.winner)} fill="var(--color-oss-reject)" />
            </ScatterChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[300px] flex items-center justify-center text-oss-muted text-sm">
            No scored positions to display.
          </div>
        )}
      </div>

      {/* Row 4: Performance Breakdown */}
      <PerformanceBreakdown />
    </div>
  )
}
