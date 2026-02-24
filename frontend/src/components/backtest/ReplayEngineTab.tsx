import { useState, useEffect, useMemo } from 'react'
import { Play, Trash2, ChevronDown, ChevronUp, Clock, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import clsx from 'clsx'
import {
  useBacktestRuns,
  useBacktestRun,
  useCreateBacktestRun,
  useDeleteBacktestRun,
  useBacktestTrades,
} from '@/hooks/useApi'
import type { BacktestRun, CreateBacktestRunRequest } from '@/lib/types'

// ============================================================================
// Run Config Form
// ============================================================================

const DEFAULT_SCANNERS = ['breakout', 'compression', 'cheap_options', 'unusual_volume']

function RunConfigForm({ onSubmit, isLoading }: {
  onSubmit: (req: CreateBacktestRunRequest) => void
  isLoading: boolean
}) {
  const [name, setName] = useState('')
  const [startDate, setStartDate] = useState('2024-01-02')
  const [endDate, setEndDate] = useState('2026-01-31')
  const [scanners, setScanners] = useState<string[]>(DEFAULT_SCANNERS)
  const [slippageModel, setSlippageModel] = useState('ask_plus_pct')
  const [slippagePct, setSlippagePct] = useState(0.05)
  const [stopLoss, setStopLoss] = useState(50)
  const [profitTarget, setProfitTarget] = useState(100)
  const [maxHolding, setMaxHolding] = useState(21)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSubmit({
      name: name || `Backtest ${new Date().toISOString().split('T')[0]}`,
      start_date: startDate,
      end_date: endDate,
      scanners_enabled: scanners,
      slippage_model: slippageModel,
      slippage_pct: slippagePct,
      exit_rules: {
        stop_loss_pct: stopLoss,
        profit_target_pct: profitTarget,
        max_holding_days: maxHolding,
      },
    })
  }

  const toggleScanner = (s: string) => {
    setScanners((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* Name */}
      <div>
        <label className="block text-xs font-medium text-oss-muted mb-1">Run Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g., Full Range Q1 2024 - Q1 2026"
          className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text placeholder:text-oss-muted/50 focus:border-oss-accent focus:outline-none"
        />
      </div>

      {/* Date Range */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-oss-muted mb-1">Start Date</label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-oss-muted mb-1">End Date</label>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
          />
        </div>
      </div>

      {/* Scanners */}
      <div>
        <label className="block text-xs font-medium text-oss-muted mb-1">Scanners</label>
        <div className="flex flex-wrap gap-2">
          {DEFAULT_SCANNERS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => toggleScanner(s)}
              className={clsx(
                'rounded-md px-3 py-1.5 text-xs font-medium border transition-colors',
                scanners.includes(s)
                  ? 'border-oss-accent bg-oss-accent/10 text-oss-accent'
                  : 'border-oss-border bg-oss-bg text-oss-muted hover:border-oss-text/30'
              )}
            >
              {s.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
      </div>

      {/* Advanced Toggle */}
      <button
        type="button"
        onClick={() => setShowAdvanced(!showAdvanced)}
        className="flex items-center gap-1 text-xs text-oss-muted hover:text-oss-text transition-colors"
      >
        {showAdvanced ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        Advanced Settings
      </button>

      {showAdvanced && (
        <div className="space-y-4 rounded-lg border border-oss-border/50 bg-oss-bg/50 p-4">
          {/* Slippage */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-oss-muted mb-1">
                Slippage Model
              </label>
              <select
                value={slippageModel}
                onChange={(e) => setSlippageModel(e.target.value)}
                className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
              >
                <option value="ask_plus_pct">Ask + %</option>
                <option value="ask">Ask</option>
                <option value="mid">Mid</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-oss-muted mb-1">
                Slippage %
              </label>
              <input
                type="number"
                step="0.01"
                value={slippagePct}
                onChange={(e) => setSlippagePct(Number(e.target.value))}
                className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
              />
            </div>
          </div>

          {/* Exit Rules */}
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-medium text-oss-muted mb-1">
                Stop Loss %
              </label>
              <input
                type="number"
                value={stopLoss}
                onChange={(e) => setStopLoss(Number(e.target.value))}
                className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-oss-muted mb-1">
                Profit Target %
              </label>
              <input
                type="number"
                value={profitTarget}
                onChange={(e) => setProfitTarget(Number(e.target.value))}
                className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-oss-muted mb-1">
                Max Holding Days
              </label>
              <input
                type="number"
                value={maxHolding}
                onChange={(e) => setMaxHolding(Number(e.target.value))}
                className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
              />
            </div>
          </div>
        </div>
      )}

      {/* Submit */}
      <button
        type="submit"
        disabled={isLoading || scanners.length === 0}
        className="flex items-center gap-2 rounded-lg bg-oss-accent px-4 py-2.5 text-sm font-medium text-white hover:bg-oss-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Play className="h-4 w-4" />
        )}
        {isLoading ? 'Starting...' : 'Start Backtest'}
      </button>
    </form>
  )
}

// ============================================================================
// Status Badge
// ============================================================================

function StatusBadge({ status }: { status: string }) {
  const config = {
    PENDING: { icon: Clock, color: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20' },
    RUNNING: { icon: Loader2, color: 'text-blue-400 bg-blue-400/10 border-blue-400/20' },
    COMPLETED: { icon: CheckCircle2, color: 'text-green-400 bg-green-400/10 border-green-400/20' },
    FAILED: { icon: XCircle, color: 'text-red-400 bg-red-400/10 border-red-400/20' },
  }[status] ?? { icon: Clock, color: 'text-oss-muted bg-oss-bg border-oss-border' }

  const Icon = config.icon

  return (
    <span className={clsx('inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium', config.color)}>
      <Icon className={clsx('h-3 w-3', status === 'RUNNING' && 'animate-spin')} />
      {status}
    </span>
  )
}

// ============================================================================
// Progress Bar
// ============================================================================

function ProgressBar({ completed, total }: { completed: number; total: number }) {
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-oss-border overflow-hidden">
        <div
          className="h-full rounded-full bg-oss-accent transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-oss-muted tabular-nums">
        {completed}/{total}
      </span>
    </div>
  )
}

// ============================================================================
// Runs Table
// ============================================================================

function RunsTable({
  runs,
  selectedRunId,
  onSelect,
  onDelete,
}: {
  runs: BacktestRun[]
  selectedRunId: string | null
  onSelect: (runId: string) => void
  onDelete: (runId: string) => void
}) {
  if (runs.length === 0) {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-8 text-center">
        <p className="text-sm text-oss-muted">No backtest runs yet. Create one above.</p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-oss-border text-left">
            <th className="px-4 py-3 text-xs font-medium text-oss-muted">Name</th>
            <th className="px-4 py-3 text-xs font-medium text-oss-muted">Status</th>
            <th className="px-4 py-3 text-xs font-medium text-oss-muted">Progress</th>
            <th className="px-4 py-3 text-xs font-medium text-oss-muted">Trades</th>
            <th className="px-4 py-3 text-xs font-medium text-oss-muted">Created</th>
            <th className="px-4 py-3 text-xs font-medium text-oss-muted w-10" />
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr
              key={run.run_id}
              onClick={() => onSelect(run.run_id)}
              className={clsx(
                'border-b border-oss-border/50 cursor-pointer transition-colors',
                selectedRunId === run.run_id
                  ? 'bg-oss-accent/5'
                  : 'hover:bg-oss-bg/50'
              )}
            >
              <td className="px-4 py-3 font-medium text-oss-text">
                {run.name || run.run_id.slice(0, 8)}
              </td>
              <td className="px-4 py-3">
                <StatusBadge status={run.status} />
              </td>
              <td className="px-4 py-3 w-48">
                <ProgressBar
                  completed={run.progress?.days_completed ?? 0}
                  total={run.progress?.days_total ?? 0}
                />
              </td>
              <td className="px-4 py-3 tabular-nums text-oss-text">
                {run.progress?.trades_found ?? 0}
              </td>
              <td className="px-4 py-3 text-oss-muted">
                {run.created_at ? new Date(run.created_at).toLocaleString() : '-'}
              </td>
              <td className="px-4 py-3">
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    onDelete(run.run_id)
                  }}
                  className="p-1 rounded hover:bg-red-500/10 text-oss-muted hover:text-red-400 transition-colors"
                  title="Delete run"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ============================================================================
// Trade Details Panel
// ============================================================================

function TradeDetailsPanel({ runId }: { runId: string }) {
  const { data: runData } = useBacktestRun(runId)
  const { data: tradesData, isLoading } = useBacktestTrades(runId, { limit: 200 })
  const [scannerFilter, setScannerFilter] = useState<string>('')

  const trades = tradesData?.trades ?? []
  const filteredTrades = scannerFilter
    ? trades.filter((t) => t.scanner_type === scannerFilter)
    : trades

  const scannerTypes = [...new Set(trades.map((t) => t.scanner_type))].sort()

  // Quick stats
  const completedTrades = trades.filter((t) => t.exit_price != null)
  const winners = completedTrades.filter((t) => (t.pnl_pct ?? 0) > 0)
  const winRate = completedTrades.length > 0
    ? ((winners.length / completedTrades.length) * 100).toFixed(1)
    : '-'
  const avgPnl = completedTrades.length > 0
    ? (completedTrades.reduce((s, t) => s + (t.pnl_pct ?? 0), 0) / completedTrades.length).toFixed(1)
    : '-'

  return (
    <div className="space-y-4">
      {/* Run Summary */}
      {runData && (
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: 'Status', value: runData.status },
            { label: 'Total Trades', value: String(trades.length) },
            { label: 'Win Rate', value: winRate === '-' ? '-' : `${winRate}%` },
            { label: 'Avg Return', value: avgPnl === '-' ? '-' : `${avgPnl}%` },
          ].map((stat) => (
            <div
              key={stat.label}
              className="rounded-lg border border-oss-border bg-oss-bg p-3"
            >
              <p className="text-[10px] font-medium uppercase tracking-wider text-oss-muted">
                {stat.label}
              </p>
              <p className="mt-1 text-lg font-semibold text-oss-text tabular-nums">
                {stat.value}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Scanner Filter */}
      {scannerTypes.length > 1 && (
        <div className="flex gap-2">
          <button
            onClick={() => setScannerFilter('')}
            className={clsx(
              'rounded-md px-2.5 py-1 text-xs font-medium border transition-colors',
              !scannerFilter
                ? 'border-oss-accent bg-oss-accent/10 text-oss-accent'
                : 'border-oss-border text-oss-muted hover:text-oss-text'
            )}
          >
            All
          </button>
          {scannerTypes.map((s) => (
            <button
              key={s}
              onClick={() => setScannerFilter(s)}
              className={clsx(
                'rounded-md px-2.5 py-1 text-xs font-medium border transition-colors',
                scannerFilter === s
                  ? 'border-oss-accent bg-oss-accent/10 text-oss-accent'
                  : 'border-oss-border text-oss-muted hover:text-oss-text'
              )}
            >
              {s.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
      )}

      {/* Trades Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-oss-muted" />
        </div>
      ) : filteredTrades.length === 0 ? (
        <div className="rounded-xl border border-oss-border bg-oss-surface p-8 text-center">
          <p className="text-sm text-oss-muted">
            {trades.length === 0 ? 'No trades generated yet.' : 'No trades match the filter.'}
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-oss-border bg-oss-surface overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-oss-border text-left">
                <th className="px-3 py-2 text-xs font-medium text-oss-muted">Ticker</th>
                <th className="px-3 py-2 text-xs font-medium text-oss-muted">Type</th>
                <th className="px-3 py-2 text-xs font-medium text-oss-muted">Scanner</th>
                <th className="px-3 py-2 text-xs font-medium text-oss-muted">Entry</th>
                <th className="px-3 py-2 text-xs font-medium text-oss-muted">Exit</th>
                <th className="px-3 py-2 text-xs font-medium text-oss-muted">P&L %</th>
                <th className="px-3 py-2 text-xs font-medium text-oss-muted">Exit Reason</th>
                <th className="px-3 py-2 text-xs font-medium text-oss-muted">Days</th>
                <th className="px-3 py-2 text-xs font-medium text-oss-muted">Score</th>
              </tr>
            </thead>
            <tbody>
              {filteredTrades.map((trade) => (
                <tr
                  key={trade.trade_id}
                  className="border-b border-oss-border/30 hover:bg-oss-bg/50"
                >
                  <td className="px-3 py-2 font-medium text-oss-text">{trade.ticker}</td>
                  <td className="px-3 py-2 text-oss-muted">{trade.option_type}</td>
                  <td className="px-3 py-2 text-oss-muted">{trade.scanner_type.replace(/_/g, ' ')}</td>
                  <td className="px-3 py-2 tabular-nums text-oss-text">
                    ${trade.entry_price.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-oss-text">
                    {trade.exit_price != null ? `$${trade.exit_price.toFixed(2)}` : '-'}
                  </td>
                  <td
                    className={clsx(
                      'px-3 py-2 tabular-nums font-medium',
                      (trade.pnl_pct ?? 0) > 0
                        ? 'text-green-400'
                        : (trade.pnl_pct ?? 0) < 0
                          ? 'text-red-400'
                          : 'text-oss-muted'
                    )}
                  >
                    {trade.pnl_pct != null ? `${trade.pnl_pct > 0 ? '+' : ''}${trade.pnl_pct.toFixed(1)}%` : '-'}
                  </td>
                  <td className="px-3 py-2 text-oss-muted text-xs">
                    {trade.exit_reason?.replace(/_/g, ' ') ?? '-'}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-oss-muted">
                    {trade.days_held ?? '-'}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-oss-text">
                    {trade.combined_score.toFixed(0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ============================================================================
// Main Component
// ============================================================================

export default function ReplayEngineTab() {
  const { data, isLoading: runsLoading, refetch } = useBacktestRuns()
  const createRun = useCreateBacktestRun()
  const deleteRun = useDeleteBacktestRun()
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const runs = useMemo(() => data?.runs ?? [], [data?.runs])

  // Auto-select first completed run
  useEffect(() => {
    if (!selectedRunId && runs.length > 0) {
      const completed = runs.find((r) => r.status === 'COMPLETED')
      if (completed) {
        setSelectedRunId(completed.run_id)
      } else if (runs[0]) {
        setSelectedRunId(runs[0].run_id)
      }
    }
  }, [runs, selectedRunId])

  // Poll running runs every 5s
  const hasRunning = runs.some((r) => r.status === 'RUNNING' || r.status === 'PENDING')

  useEffect(() => {
    if (!hasRunning) return
    const interval = setInterval(() => refetch(), 5000)
    return () => clearInterval(interval)
  }, [hasRunning, refetch])

  const handleCreate = (req: CreateBacktestRunRequest) => {
    createRun.mutate(req, {
      onSuccess: (result) => {
        setSelectedRunId(result.run_id)
      },
    })
  }

  const handleDelete = (runId: string) => {
    if (selectedRunId === runId) setSelectedRunId(null)
    deleteRun.mutate(runId)
  }

  return (
    <div className="space-y-6">
      {/* Config Form */}
      <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
        <h3 className="text-sm font-semibold text-oss-text mb-4">New Backtest Run</h3>
        <RunConfigForm onSubmit={handleCreate} isLoading={createRun.isPending} />
      </div>

      {/* Runs Table */}
      <div>
        <h3 className="text-sm font-semibold text-oss-text mb-3">
          Runs {runs.length > 0 && <span className="text-oss-muted font-normal">({runs.length})</span>}
        </h3>
        {runsLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-oss-muted" />
          </div>
        ) : (
          <RunsTable
            runs={runs}
            selectedRunId={selectedRunId}
            onSelect={setSelectedRunId}
            onDelete={handleDelete}
          />
        )}
      </div>

      {/* Selected Run Details */}
      {selectedRunId && (
        <div>
          <h3 className="text-sm font-semibold text-oss-text mb-3">Run Details</h3>
          <TradeDetailsPanel runId={selectedRunId} />
        </div>
      )}
    </div>
  )
}
