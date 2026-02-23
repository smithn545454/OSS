/**
 * Results Tab — Phase 3 of the Backtesting Engine
 *
 * Displays comprehensive analysis of a completed backtest run:
 * - Summary metrics (KPIs)
 * - Equity curve chart
 * - Monthly P&L chart
 * - Segmented analysis table (scanner, regime, score, etc.)
 * - Trades table with filtering
 */

import { useState } from 'react'
import {
  BarChart3,
  TrendingUp,
  Target,
  Activity,
  ChevronDown,
  AlertCircle,
  Loader2,
} from 'lucide-react'
import {
  useBacktestRuns,
  useBacktestSummary,
  useBacktestEquityCurve,
  useBacktestMonthlyPnl,
  useBacktestSegments,
} from '@/hooks/useApi'
import type {
  BacktestRun,
  BacktestSummary,
  EquityCurvePoint,
  MonthlyPnl,
  SegmentData,
} from '@/lib/types'

// ============================================================================
// Metric Card
// ============================================================================

function MetricCard({
  label,
  value,
  subtitle,
  trend,
}: {
  label: string
  value: string
  subtitle?: string
  trend?: 'up' | 'down' | 'neutral'
}) {
  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <p className="text-xs text-gray-400 uppercase tracking-wider">{label}</p>
      <p
        className={`text-xl font-mono font-bold mt-1 ${
          trend === 'up'
            ? 'text-green-400'
            : trend === 'down'
              ? 'text-red-400'
              : 'text-white'
        }`}
      >
        {value}
      </p>
      {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
    </div>
  )
}

// ============================================================================
// Summary Metrics Grid
// ============================================================================

function SummaryGrid({ summary }: { summary: BacktestSummary }) {
  const fmt = (n: number | null, decimals = 2) =>
    n != null ? n.toFixed(decimals) : '-'

  return (
    <div className="space-y-4">
      {/* Core P&L */}
      <div>
        <h3 className="text-sm font-medium text-gray-400 mb-2">Core P&L</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
          <MetricCard
            label="Net P&L"
            value={`$${fmt(summary.net_pnl)}`}
            trend={summary.net_pnl > 0 ? 'up' : summary.net_pnl < 0 ? 'down' : 'neutral'}
          />
          <MetricCard
            label="Return %"
            value={`${fmt(summary.total_return_pct)}%`}
            trend={summary.total_return_pct > 0 ? 'up' : 'down'}
          />
          <MetricCard
            label="Win Rate"
            value={`${fmt(summary.win_rate, 1)}%`}
            subtitle={`${summary.winners}W / ${summary.losers}L`}
            trend={summary.win_rate > 50 ? 'up' : 'down'}
          />
          <MetricCard
            label="Profit Factor"
            value={summary.profit_factor ? fmt(summary.profit_factor) : 'N/A'}
            trend={
              summary.profit_factor
                ? summary.profit_factor > 1.5
                  ? 'up'
                  : 'down'
                : 'neutral'
            }
          />
          <MetricCard
            label="Total Trades"
            value={String(summary.total_trades)}
            subtitle={`${fmt(summary.trades_per_day, 1)}/day`}
          />
          <MetricCard
            label="Avg Days Held"
            value={fmt(summary.avg_days_held, 1)}
          />
        </div>
      </div>

      {/* Risk */}
      <div>
        <h3 className="text-sm font-medium text-gray-400 mb-2">Risk</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricCard
            label="Sharpe Ratio"
            value={summary.sharpe_ratio ? fmt(summary.sharpe_ratio) : 'N/A'}
            trend={
              summary.sharpe_ratio
                ? summary.sharpe_ratio > 1.5
                  ? 'up'
                  : summary.sharpe_ratio > 0
                    ? 'neutral'
                    : 'down'
                : 'neutral'
            }
          />
          <MetricCard
            label="Max Drawdown"
            value={`${fmt(summary.max_drawdown_pct)}%`}
            subtitle={`${summary.max_drawdown_duration_days}d duration`}
            trend={summary.max_drawdown_pct > 10 ? 'down' : 'neutral'}
          />
          <MetricCard
            label="Expectancy"
            value={`${fmt(summary.expectancy)}%`}
            trend={summary.expectancy > 0 ? 'up' : 'down'}
          />
          <MetricCard
            label="Avg P&L/Trade"
            value={`$${fmt(summary.avg_pnl_per_trade)}`}
            trend={summary.avg_pnl_per_trade > 0 ? 'up' : 'down'}
          />
        </div>
      </div>

      {/* Trade Quality */}
      <div>
        <h3 className="text-sm font-medium text-gray-400 mb-2">
          Trade Quality
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricCard
            label="Avg Win"
            value={`${fmt(summary.avg_win_pct)}%`}
            trend="up"
          />
          <MetricCard
            label="Avg Loss"
            value={`${fmt(summary.avg_loss_pct)}%`}
            trend="down"
          />
          <MetricCard
            label="Avg MFE"
            value={`${fmt(summary.avg_mfe_pct)}%`}
          />
          <MetricCard
            label="Avg MAE"
            value={`${fmt(summary.avg_mae_pct)}%`}
          />
        </div>
      </div>

      {/* Exit Reasons */}
      {summary.exit_reasons && Object.keys(summary.exit_reasons).length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-400 mb-2">
            Exit Reasons
          </h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(summary.exit_reasons)
              .sort(([, a], [, b]) => b - a)
              .map(([reason, count]) => (
                <span
                  key={reason}
                  className="px-3 py-1 bg-gray-700 rounded-full text-sm text-gray-300"
                >
                  {reason.replace(/_/g, ' ')}: {count}
                </span>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ============================================================================
// Equity Curve (text-based since we can't use Recharts easily)
// ============================================================================

function EquityCurveDisplay({ curve }: { curve: EquityCurvePoint[] }) {
  if (!curve.length) {
    return <p className="text-gray-500 text-sm">No equity data available.</p>
  }

  const maxEquity = Math.max(...curve.map((p) => p.equity))
  const minEquity = Math.min(...curve.map((p) => p.equity))
  const startEquity = curve[0].equity
  const endEquity = curve[curve.length - 1].equity
  const maxDrawdown = Math.max(...curve.map((p) => p.drawdown_pct))

  // Subsample for display (show ~60 points max)
  const step = Math.max(1, Math.floor(curve.length / 60))
  const sampled = curve.filter((_, i) => i % step === 0 || i === curve.length - 1)

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-4 gap-3">
        <MetricCard label="Start" value={`$${startEquity.toFixed(0)}`} />
        <MetricCard
          label="End"
          value={`$${endEquity.toFixed(0)}`}
          trend={endEquity > startEquity ? 'up' : 'down'}
        />
        <MetricCard label="Peak" value={`$${maxEquity.toFixed(0)}`} trend="up" />
        <MetricCard
          label="Max DD"
          value={`${maxDrawdown.toFixed(1)}%`}
          trend="down"
        />
      </div>

      {/* Simple bar visualization */}
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <h4 className="text-xs text-gray-400 mb-3">Equity Curve</h4>
        <div className="flex items-end gap-px h-32">
          {sampled.map((point, idx) => {
            const range = maxEquity - minEquity || 1
            const height = ((point.equity - minEquity) / range) * 100
            const isGain = point.equity >= startEquity
            return (
              <div
                key={idx}
                className={`flex-1 rounded-t-sm ${
                  isGain ? 'bg-green-500/70' : 'bg-red-500/70'
                }`}
                style={{ height: `${Math.max(height, 2)}%` }}
                title={`${point.date}: $${point.equity.toFixed(0)}`}
              />
            )
          })}
        </div>
        <div className="flex justify-between text-xs text-gray-500 mt-1">
          <span>{sampled[0]?.date}</span>
          <span>{sampled[sampled.length - 1]?.date}</span>
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Monthly P&L Chart
// ============================================================================

function MonthlyPnlDisplay({ months }: { months: MonthlyPnl[] }) {
  if (!months.length) {
    return <p className="text-gray-500 text-sm">No monthly data available.</p>
  }

  const maxPnl = Math.max(...months.map((m) => Math.abs(m.pnl)), 1)

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <h4 className="text-xs text-gray-400 mb-3">Monthly P&L</h4>
      <div className="space-y-2">
        {months.map((month) => {
          const width = (Math.abs(month.pnl) / maxPnl) * 100
          const isPositive = month.pnl >= 0
          return (
            <div key={month.month} className="flex items-center gap-2">
              <span className="text-xs text-gray-400 w-16 font-mono">
                {month.month}
              </span>
              <div className="flex-1 flex items-center">
                <div
                  className={`h-5 rounded ${
                    isPositive ? 'bg-green-500/70' : 'bg-red-500/70'
                  }`}
                  style={{ width: `${Math.max(width, 2)}%` }}
                />
              </div>
              <span
                className={`text-xs font-mono w-20 text-right ${
                  isPositive ? 'text-green-400' : 'text-red-400'
                }`}
              >
                ${month.pnl.toFixed(0)}
              </span>
              <span className="text-xs text-gray-500 w-16 text-right">
                {month.win_rate.toFixed(0)}% WR
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ============================================================================
// Segmented Analysis Table
// ============================================================================

const SEGMENT_OPTIONS = [
  { value: 'scanner', label: 'Scanner' },
  { value: 'verdict', label: 'Verdict' },
  { value: 'score_bucket', label: 'Score Bucket' },
  { value: 'regime', label: 'Market Regime' },
  { value: 'holding_period', label: 'Holding Period' },
  { value: 'exit_reason', label: 'Exit Reason' },
  { value: 'month', label: 'Month' },
]

function SegmentTable({ segments }: { segments: SegmentData[] }) {
  if (!segments.length) {
    return <p className="text-gray-500 text-sm">No segment data.</p>
  }

  const fmt = (n: number | null, d = 2) => (n != null ? n.toFixed(d) : '-')

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-400 border-b border-gray-700">
            <th className="pb-2 pr-4">Segment</th>
            <th className="pb-2 pr-3 text-right">Trades</th>
            <th className="pb-2 pr-3 text-right">Win Rate</th>
            <th className="pb-2 pr-3 text-right">Net P&L</th>
            <th className="pb-2 pr-3 text-right">Avg Return</th>
            <th className="pb-2 pr-3 text-right">PF</th>
            <th className="pb-2 pr-3 text-right">Sharpe</th>
            <th className="pb-2 text-right">Avg Days</th>
          </tr>
        </thead>
        <tbody>
          {segments.map((seg) => (
            <tr
              key={seg.segment}
              className="border-b border-gray-800 hover:bg-gray-800/50"
            >
              <td className="py-2 pr-4 font-mono text-gray-200">
                {seg.segment}
              </td>
              <td className="py-2 pr-3 text-right text-gray-300">
                {seg.trades}
              </td>
              <td
                className={`py-2 pr-3 text-right font-mono ${
                  seg.win_rate >= 50 ? 'text-green-400' : 'text-red-400'
                }`}
              >
                {fmt(seg.win_rate, 1)}%
              </td>
              <td
                className={`py-2 pr-3 text-right font-mono ${
                  seg.net_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                }`}
              >
                ${fmt(seg.net_pnl, 0)}
              </td>
              <td
                className={`py-2 pr-3 text-right font-mono ${
                  seg.avg_return_pct >= 0 ? 'text-green-400' : 'text-red-400'
                }`}
              >
                {fmt(seg.avg_return_pct)}%
              </td>
              <td className="py-2 pr-3 text-right font-mono text-gray-300">
                {seg.profit_factor ? fmt(seg.profit_factor) : '-'}
              </td>
              <td className="py-2 pr-3 text-right font-mono text-gray-300">
                {seg.sharpe ? fmt(seg.sharpe) : '-'}
              </td>
              <td className="py-2 text-right font-mono text-gray-300">
                {fmt(seg.avg_days_held, 1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ============================================================================
// Run Selector
// ============================================================================

function RunSelector({
  selectedRunId,
  onSelect,
}: {
  selectedRunId: string | null
  onSelect: (runId: string) => void
}) {
  const { data: runsData } = useBacktestRuns()
  const completedRuns = (runsData?.runs || []).filter(
    (r: BacktestRun) => r.status === 'COMPLETED'
  )

  if (!completedRuns.length) {
    return (
      <div className="flex items-center gap-2 text-gray-500 text-sm">
        <AlertCircle className="w-4 h-4" />
        <span>No completed runs available. Start a backtest in the Replay Engine tab.</span>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm text-gray-400">Select Run:</label>
      <div className="relative">
        <select
          value={selectedRunId || ''}
          onChange={(e) => onSelect(e.target.value)}
          className="bg-gray-800 text-gray-200 border border-gray-600 rounded-lg px-3 py-2 pr-8 text-sm appearance-none cursor-pointer hover:border-gray-500 focus:border-cyan-500 focus:outline-none"
        >
          <option value="">Choose a run...</option>
          {completedRuns.map((run: BacktestRun) => (
            <option key={run.run_id} value={run.run_id}>
              {run.config && typeof run.config === 'object' && 'name' in run.config
                ? String(run.config.name)
                : run.run_id.slice(0, 8)}{' '}
              ({run.summary
                ? `${(run.summary as Record<string, number>).total_trades || 0} trades`
                : 'no summary'})
            </option>
          ))}
        </select>
        <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
      </div>
    </div>
  )
}

// ============================================================================
// Main Results Tab
// ============================================================================

export default function ResultsTab() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [segmentType, setSegmentType] = useState('scanner')

  const { data: summaryData, isLoading: summaryLoading } = useBacktestSummary(
    selectedRunId || ''
  )
  const { data: curveData, isLoading: curveLoading } = useBacktestEquityCurve(
    selectedRunId || ''
  )
  const { data: monthlyData, isLoading: monthlyLoading } = useBacktestMonthlyPnl(
    selectedRunId || ''
  )
  const { data: segmentData, isLoading: segmentLoading } = useBacktestSegments(
    selectedRunId || '',
    segmentType
  )

  const summary = summaryData?.summary || null
  const isLoading = summaryLoading || curveLoading || monthlyLoading

  return (
    <div className="space-y-6">
      {/* Run Selector */}
      <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
        <RunSelector selectedRunId={selectedRunId} onSelect={setSelectedRunId} />
      </div>

      {!selectedRunId && (
        <div className="text-center py-16 text-gray-500">
          <BarChart3 className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="text-lg">Select a completed run to view results</p>
          <p className="text-sm mt-1">
            Completed runs will appear in the dropdown above
          </p>
        </div>
      )}

      {selectedRunId && isLoading && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-cyan-400" />
          <span className="ml-2 text-gray-400">Loading results...</span>
        </div>
      )}

      {selectedRunId && !isLoading && summary && (
        <>
          {/* Summary Metrics */}
          <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
            <div className="flex items-center gap-2 mb-4">
              <Activity className="w-5 h-5 text-cyan-400" />
              <h2 className="text-lg font-semibold text-white">
                Performance Summary
              </h2>
            </div>
            <SummaryGrid summary={summary} />
          </div>

          {/* Equity Curve */}
          {curveData?.curve && curveData.curve.length > 0 && (
            <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
              <div className="flex items-center gap-2 mb-4">
                <TrendingUp className="w-5 h-5 text-cyan-400" />
                <h2 className="text-lg font-semibold text-white">
                  Equity Curve
                </h2>
              </div>
              <EquityCurveDisplay curve={curveData.curve} />
            </div>
          )}

          {/* Monthly P&L */}
          {monthlyData?.months && monthlyData.months.length > 0 && (
            <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
              <div className="flex items-center gap-2 mb-4">
                <BarChart3 className="w-5 h-5 text-cyan-400" />
                <h2 className="text-lg font-semibold text-white">
                  Monthly P&L
                </h2>
              </div>
              <MonthlyPnlDisplay months={monthlyData.months} />
            </div>
          )}

          {/* Segmented Analysis */}
          <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Target className="w-5 h-5 text-cyan-400" />
                <h2 className="text-lg font-semibold text-white">
                  Segmented Analysis
                </h2>
              </div>
              <div className="flex gap-1">
                {SEGMENT_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setSegmentType(opt.value)}
                    className={`px-3 py-1 rounded-lg text-xs transition-colors ${
                      segmentType === opt.value
                        ? 'bg-cyan-600 text-white'
                        : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
            {segmentLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-5 h-5 animate-spin text-gray-500" />
              </div>
            ) : segmentData?.segments ? (
              <SegmentTable segments={segmentData.segments} />
            ) : (
              <p className="text-gray-500 text-sm">No segment data available.</p>
            )}
          </div>
        </>
      )}

      {selectedRunId && !isLoading && !summary && (
        <div className="text-center py-16 text-gray-500">
          <AlertCircle className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="text-lg">No summary available for this run</p>
          <p className="text-sm mt-1">
            The run may still be processing or may not have generated trades.
          </p>
        </div>
      )}
    </div>
  )
}
