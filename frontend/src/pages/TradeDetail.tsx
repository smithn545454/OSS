import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { usePageTitle } from '@/hooks/usePageTitle'
import {
  ArrowLeft,
  CheckCircle,
  XCircle,
  Eye,
  Clock,
  TrendingUp,
  TrendingDown,
  DollarSign,
  Loader2,
} from 'lucide-react'
import clsx from 'clsx'
import { useTrade, useCloseTrade } from '@/hooks/useApi'
import { formatDateTime } from '@/lib/formatTime'
import type { TradeExitReason, StockTechnicalsResponse, ExitPlanThesis } from '@/lib/types'
import UnderlyingStockDetails from '@/components/evaluation/UnderlyingStockDetails'
import { ExitPlanCard } from '@/components/AITradeThesis'

const EXIT_REASONS: { value: TradeExitReason; label: string }[] = [
  { value: 'PROFIT_TARGET', label: 'Profit Target' },
  { value: 'STOP_LOSS', label: 'Stop Loss' },
  { value: 'TRAILING_STOP', label: 'Trailing Stop' },
  { value: 'TIME_EXIT', label: 'Time Exit' },
  { value: 'EXPIRATION', label: 'Expiration' },
  { value: 'THESIS_INVALIDATED', label: 'Thesis Invalidated' },
  { value: 'MANUAL', label: 'Manual Close' },
  { value: 'OTHER', label: 'Other' },
]

export default function TradeDetail() {
  const { tradeId } = useParams<{ tradeId: string }>()
  const { data: trade, isLoading, error } = useTrade(tradeId || '')
  const closeTrade = useCloseTrade()

  const tradeTicker = (trade?.snapshot as Record<string, unknown>)?.underlying_ticker as string | undefined
  usePageTitle(tradeTicker ? `${tradeTicker} Trade Detail` : 'Trade Detail')

  const [showCloseForm, setShowCloseForm] = useState(false)
  const [exitPrice, setExitPrice] = useState('')
  const [exitReason, setExitReason] = useState<TradeExitReason>('MANUAL')
  const [exitNotes, setExitNotes] = useState('')

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="h-6 w-48 animate-pulse rounded bg-oss-surface" />
        <div className="h-40 animate-pulse rounded-xl bg-oss-surface" />
      </div>
    )
  }

  if (error || !trade) {
    return (
      <div className="space-y-6">
        <Link to="/trades" className="inline-flex items-center gap-2 text-sm text-oss-muted hover:text-oss-text">
          <ArrowLeft className="h-4 w-4" /> Back to My Trades
        </Link>
        <div className="rounded-xl border border-oss-reject/30 bg-oss-reject/5 p-8 text-center">
          <XCircle className="h-10 w-10 text-oss-reject mx-auto mb-3" />
          <p className="text-oss-text">Trade not found</p>
        </div>
      </div>
    )
  }

  const snapshot = trade.snapshot as Record<string, unknown>
  const ticker = (snapshot?.underlying_ticker as string) || '—'
  const optionType = (snapshot?.option_type as string) || ''
  const strike = snapshot?.strike as number | undefined
  const expiration = snapshot?.expiration_date as string | undefined
  const verdict = (snapshot?.verdict as string) || ''
  const finalScore = snapshot?.final_score as number | undefined
  const scannerSource = (snapshot?.scanner_source as string) || ''

  const pillarScores = (snapshot?.pillar_scores as Record<string, unknown>[]) || []
  const gateResults = (snapshot?.gate_results as Record<string, unknown>[]) || []
  const features = (snapshot?.features as Record<string, unknown>) || {}
  const thesis = snapshot?.thesis as Record<string, unknown> | null
  const scannerTriggers = (snapshot?.scanner_triggers as Record<string, unknown>[]) || []
  const underlyingTechnicals = snapshot?.underlying_technicals as StockTechnicalsResponse | undefined
  const exitPlan = thesis?.exit_plan as ExitPlanThesis | undefined
  const decidedAt = snapshot?.decided_at as string | undefined

  const isOpen = trade.status === 'OPEN'
  const isCall = optionType === 'CALL'
  const hasPnl = trade.realized_pnl_pct != null
  const isWin = hasPnl && (trade.realized_pnl_pct ?? 0) > 0

  const handleClose = (e: React.FormEvent) => {
    e.preventDefault()
    if (!tradeId) return
    closeTrade.mutate(
      {
        tradeId,
        exit_price: parseFloat(exitPrice),
        exit_reason: exitReason,
        exit_notes: exitNotes || undefined,
      },
      { onSuccess: () => setShowCloseForm(false) }
    )
  }

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link
        to="/trades"
        className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-oss-muted hover:bg-oss-surface hover:text-oss-text transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to My Trades
      </Link>

      {/* Hero */}
      <div className="rounded-xl border border-oss-border bg-oss-card p-6">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <span className="text-3xl font-bold text-oss-text">{ticker}</span>
              <span className={clsx(
                'rounded-lg px-3 py-1 text-sm font-semibold',
                isCall ? 'bg-emerald-500/15 text-emerald-400' : 'bg-rose-500/15 text-rose-400'
              )}>
                {optionType}
              </span>
              {verdict === 'APPROVE' && (
                <span className="inline-flex items-center gap-1 rounded-full bg-oss-approve/10 px-2.5 py-1 text-xs text-oss-approve border border-oss-approve/30">
                  <CheckCircle className="h-3 w-3" /> APPROVE
                </span>
              )}
              {verdict === 'WATCH' && (
                <span className="inline-flex items-center gap-1 rounded-full bg-oss-watch/10 px-2.5 py-1 text-xs text-oss-watch border border-oss-watch/30">
                  <Eye className="h-3 w-3" /> WATCH
                </span>
              )}
            </div>
            <p className="text-oss-muted">
              ${strike?.toFixed(0)} Strike · Exp {expiration}
              {scannerSource && ` · ${scannerSource.replace('_', ' ')}`}
            </p>
          </div>

          {/* Status */}
          <div className="text-right">
            {isOpen ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/10 px-3 py-1.5 text-sm text-blue-400 border border-blue-500/30">
                <Clock className="h-4 w-4" /> OPEN
              </span>
            ) : (
              <div>
                <span className={clsx(
                  'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm border',
                  isWin
                    ? 'bg-oss-approve/10 text-oss-approve border-oss-approve/30'
                    : 'bg-oss-reject/10 text-oss-reject border-oss-reject/30'
                )}>
                  {isWin ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
                  {trade.realized_pnl_pct != null
                    ? `${Number(trade.realized_pnl_pct) > 0 ? '+' : ''}${Number(trade.realized_pnl_pct).toFixed(1)}%`
                    : 'CLOSED'}
                </span>
                {trade.realized_pnl_dollars != null && (
                  <p className={clsx(
                    'text-sm font-mono mt-1',
                    Number(trade.realized_pnl_dollars) > 0 ? 'text-oss-approve' : 'text-oss-reject'
                  )}>
                    {Number(trade.realized_pnl_dollars) > 0 ? '+' : ''}${Number(trade.realized_pnl_dollars).toFixed(0)}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Execution Details */}
      <div className="rounded-xl border border-oss-border bg-oss-card p-6">
        <h2 className="text-lg font-semibold text-oss-text mb-4">Execution Details</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <MetricItem label="Entry Price" value={`$${Number(trade.entry_price).toFixed(2)}`} />
          <MetricItem label="Quantity" value={`${trade.quantity} contract${trade.quantity !== 1 ? 's' : ''}`} />
          <MetricItem label="Trader" value={trade.trader || '—'} />
          <MetricItem
            label="Total Cost"
            value={`$${(Number(trade.entry_price) * trade.quantity * 100).toLocaleString()}`}
          />
          <MetricItem label="Tracked At" value={formatDateTime(trade.tracked_at)} />
          {trade.exit_price != null && (
            <MetricItem label="Exit Price" value={`$${Number(trade.exit_price).toFixed(2)}`} />
          )}
          {trade.exit_reason && (
            <MetricItem label="Exit Reason" value={trade.exit_reason.replace('_', ' ')} />
          )}
          {trade.closed_at && (
            <MetricItem label="Closed At" value={formatDateTime(trade.closed_at)} />
          )}
        </div>
        {trade.entry_notes && (
          <div className="mt-4 rounded-lg bg-oss-bg p-3">
            <p className="text-xs text-oss-muted mb-1">Notes</p>
            <p className="text-sm text-oss-text">{trade.entry_notes}</p>
          </div>
        )}
        {trade.exit_notes && (
          <div className="mt-2 rounded-lg bg-oss-bg p-3">
            <p className="text-xs text-oss-muted mb-1">Exit Notes</p>
            <p className="text-sm text-oss-text">{trade.exit_notes}</p>
          </div>
        )}
      </div>

      {/* Underlying Stock Details (at entry) */}
      {underlyingTechnicals && (
        <UnderlyingStockDetails ticker={ticker} data={underlyingTechnicals} />
      )}

      {/* Exit Plan */}
      {exitPlan && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-6">
          <h2 className="text-lg font-semibold text-oss-text mb-4">Exit Plan (at entry)</h2>
          <ExitPlanCard exitPlan={exitPlan} />
        </div>
      )}

      {/* Close Trade (for open trades) */}
      {isOpen && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-6">
          {!showCloseForm ? (
            <button
              onClick={() => setShowCloseForm(true)}
              className="w-full rounded-lg border border-oss-border py-3 text-sm font-medium text-oss-text hover:bg-oss-surface transition-colors flex items-center justify-center gap-2"
            >
              <DollarSign className="h-4 w-4" />
              Close This Trade
            </button>
          ) : (
            <form onSubmit={handleClose} className="space-y-4">
              <h3 className="text-lg font-semibold text-oss-text">Close Trade</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-oss-muted mb-1.5">Exit Price</label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-oss-muted">$</span>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      value={exitPrice}
                      onChange={(e) => setExitPrice(e.target.value)}
                      className="w-full rounded-lg border border-oss-border bg-oss-bg pl-7 pr-3 py-2 text-oss-text font-mono focus:border-oss-accent focus:outline-none focus:ring-1 focus:ring-oss-accent"
                      required
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-oss-muted mb-1.5">Exit Reason</label>
                  <select
                    value={exitReason}
                    onChange={(e) => setExitReason(e.target.value as TradeExitReason)}
                    className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-oss-text focus:border-oss-accent focus:outline-none focus:ring-1 focus:ring-oss-accent"
                  >
                    {EXIT_REASONS.map((r) => (
                      <option key={r.value} value={r.value}>{r.label}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-oss-muted mb-1.5">Notes (optional)</label>
                <textarea
                  value={exitNotes}
                  onChange={(e) => setExitNotes(e.target.value)}
                  rows={2}
                  className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-oss-text text-sm focus:border-oss-accent focus:outline-none focus:ring-1 focus:ring-oss-accent resize-none"
                />
              </div>
              {exitPrice && (
                <div className="rounded-lg bg-oss-bg p-3 flex items-center justify-between">
                  <span className="text-sm text-oss-muted">Estimated P&L</span>
                  {(() => {
                    const pnl = ((parseFloat(exitPrice) - Number(trade.entry_price)) / Number(trade.entry_price)) * 100
                    const dollars = (parseFloat(exitPrice) - Number(trade.entry_price)) * trade.quantity * 100
                    return (
                      <span className={clsx('font-mono font-semibold', pnl > 0 ? 'text-oss-approve' : 'text-oss-reject')}>
                        {pnl > 0 ? '+' : ''}{pnl.toFixed(1)}% ({dollars > 0 ? '+' : ''}${dollars.toFixed(0)})
                      </span>
                    )
                  })()}
                </div>
              )}
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => setShowCloseForm(false)}
                  className="flex-1 rounded-lg border border-oss-border py-2.5 text-sm font-medium text-oss-muted hover:bg-oss-surface transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={closeTrade.isPending}
                  className="flex-1 rounded-lg bg-oss-accent py-2.5 text-sm font-semibold text-black hover:bg-oss-accent/90 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
                >
                  {closeTrade.isPending ? (
                    <><Loader2 className="h-4 w-4 animate-spin" /> Closing...</>
                  ) : (
                    'Close Trade'
                  )}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* Snapshot: Pillar Scores */}
      {pillarScores.length > 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-6">
          <h2 className="text-lg font-semibold text-oss-text mb-4">Pillar Scores (at entry)</h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {pillarScores.map((ps) => {
              const score = Number(ps.score ?? 0)
              return (
                <div key={String(ps.pillar_id)} className="rounded-lg bg-oss-bg p-4">
                  <p className="text-sm font-medium text-oss-muted mb-2">{String(ps.pillar_id)}</p>
                  <p className={clsx(
                    'text-2xl font-bold font-mono',
                    score >= 75 ? 'text-oss-approve' : score >= 65 ? 'text-oss-watch' : 'text-oss-reject'
                  )}>
                    {score.toFixed(0)}
                  </p>
                  {/* Contributors */}
                  {(ps.contributors as Record<string, unknown>[] | undefined)?.slice(0, 3).map((c, i) => (
                    <div key={i} className="flex justify-between text-xs mt-1">
                      <span className="text-oss-muted">{String(c.feature_name ?? '')}</span>
                      <span className="text-oss-text font-mono">{Number(c.subscore ?? 0).toFixed(0)}</span>
                    </div>
                  ))}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Snapshot: Gate Results */}
      {gateResults.length > 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-6">
          <h2 className="text-lg font-semibold text-oss-text mb-4">Gate Results (at entry)</h2>
          <div className="space-y-2">
            {gateResults.map((gr) => {
              const passed = gr.passed as boolean
              return (
                <div
                  key={String(gr.gate_id)}
                  className="flex items-center justify-between rounded-lg bg-oss-bg px-4 py-2.5"
                >
                  <div className="flex items-center gap-2">
                    {passed ? (
                      <CheckCircle className="h-4 w-4 text-oss-approve" />
                    ) : (
                      <XCircle className="h-4 w-4 text-oss-reject" />
                    )}
                    <span className="text-sm text-oss-text">{String(gr.gate_id)}</span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-oss-muted">
                    <span className="font-mono">
                      {gr.measured_value != null ? Number(gr.measured_value).toFixed(2) : '—'}
                    </span>
                    <span>{String(gr.operator ?? '')} {gr.threshold_value != null ? Number(gr.threshold_value).toFixed(2) : ''}</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Snapshot: AI Trade Thesis */}
      {thesis && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-6">
          <h2 className="text-lg font-semibold text-oss-text mb-4">AI Trade Thesis (at entry)</h2>
          {thesis.setup_summary != null && (
            <p className="text-sm text-oss-text mb-3">{String(thesis.setup_summary)}</p>
          )}
          {thesis.thesis != null && (
            <p className="text-sm text-oss-muted mb-3">{String(thesis.thesis)}</p>
          )}
          {(thesis.supporting_evidence as string[] | undefined)?.length ? (
            <div className="mb-3">
              <p className="text-xs font-medium text-oss-muted mb-1">Supporting Evidence</p>
              <ul className="list-disc list-inside text-sm text-oss-text space-y-0.5">
                {(thesis.supporting_evidence as string[]).map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {(thesis.risks as string[] | undefined)?.length ? (
            <div>
              <p className="text-xs font-medium text-oss-muted mb-1">Risks</p>
              <ul className="list-disc list-inside text-sm text-oss-reject/80 space-y-0.5">
                {(thesis.risks as string[]).map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}

      {/* Snapshot: Scanner Triggers */}
      {scannerTriggers.length > 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-6">
          <h2 className="text-lg font-semibold text-oss-text mb-4">Scanner Triggers (at entry)</h2>
          <div className="space-y-2">
            {scannerTriggers.map((st, i) => (
              <div key={i} className="rounded-lg bg-oss-bg p-3">
                <p className="text-sm font-medium text-oss-text">{String(st.scanner_type)}</p>
                {(st.reason_codes as string[] | undefined)?.map((rc, j) => (
                  <span key={j} className="inline-block rounded bg-oss-surface px-2 py-0.5 text-xs text-oss-muted mr-1 mt-1">
                    {rc}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Snapshot: Features */}
      {Object.keys(features).length > 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-6">
          <h2 className="text-lg font-semibold text-oss-text mb-4">Features (at entry)</h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {Object.entries(features).map(([name, data]) => {
              const feat = data as Record<string, unknown>
              return (
                <div key={name} className="rounded-lg bg-oss-bg p-3">
                  <p className="text-xs text-oss-muted truncate">{name}</p>
                  <p className="text-sm font-mono text-oss-text">
                    {feat.value != null ? Number(feat.value).toFixed(3) : '—'}
                    {feat.units != null && <span className="text-xs text-oss-muted ml-1">{String(feat.units)}</span>}
                  </p>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Final Score */}
      {finalScore != null && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-6">
          <h2 className="text-lg font-semibold text-oss-text mb-4">Decision Summary (at entry)</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <MetricItem label="Final Score" value={Number(finalScore).toFixed(0)} />
            <MetricItem label="Directional" value={snapshot?.premium_leverage_score != null ? Number(snapshot.premium_leverage_score).toFixed(0) : '—'} />
            <MetricItem label="Volatility" value={snapshot?.underlying_behavior_score != null ? Number(snapshot.underlying_behavior_score).toFixed(0) : '—'} />
            <MetricItem label="Entry Quality" value={snapshot?.setup_quality_score != null ? Number(snapshot.setup_quality_score).toFixed(0) : '—'} />
            {decidedAt && <MetricItem label="Decided At" value={formatDateTime(decidedAt)} />}
            {snapshot?.theta_adjusted_ev != null && (
              <MetricItem label="θ-Adj EV" value={`$${Number(snapshot.theta_adjusted_ev).toFixed(2)}`} />
            )}
            {snapshot?.conviction_score != null && (
              <MetricItem label="Conviction" value={Number(snapshot.conviction_score).toFixed(0)} />
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function MetricItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-oss-muted mb-0.5">{label}</p>
      <p className="text-sm font-mono text-oss-text">{value}</p>
    </div>
  )
}
