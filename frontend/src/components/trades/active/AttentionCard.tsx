import clsx from 'clsx'
import { Link } from 'react-router-dom'
import { AlertTriangle, Target, X, ExternalLink, Zap } from 'lucide-react'
import type { LiveTrade } from '@/lib/types'
import { formatExpirationDate } from '@/lib/formatTime'
import { fmtPct, fmtUsd, pnlColor } from './format'
import TPSLProgressBar from './TPSLProgressBar'

interface Props {
  trade: LiveTrade
  onClose: (trade: LiveTrade) => void
  closingId: string | null
}

export default function AttentionCard({ trade, onClose, closingId }: Props) {
  const isNearTp = trade.attention_flag === 'near_tp'
  const isNearSl = trade.attention_flag === 'near_sl'

  const borderColor = isNearSl
    ? 'border-oss-reject/60'
    : isNearTp
      ? 'border-oss-approve/60'
      : 'border-oss-border'

  const ticker = trade.underlying_ticker || '—'
  const optionType = trade.option_type || ''
  const strike = trade.strike
  const expiration = trade.expiration_date
  const paperClosed = trade.paper_position_status === 'CLOSED'

  return (
    <div
      className={clsx(
        'rounded-xl border-2 bg-oss-card p-4 space-y-3',
        borderColor
      )}
    >
      {/* Header: ticker + urgency badge */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-base font-semibold text-oss-text">{ticker}</span>
          {optionType && (
            <span
              className={clsx(
                'rounded-md px-1.5 py-0.5 text-xs font-medium',
                optionType === 'CALL'
                  ? 'bg-emerald-500/15 text-emerald-400'
                  : 'bg-rose-500/15 text-rose-400'
              )}
            >
              {optionType}
            </span>
          )}
          {strike != null && (
            <span className="text-xs text-oss-muted font-mono">
              ${strike.toFixed(0)}
            </span>
          )}
          {expiration && (
            <span className="text-xs text-oss-muted">
              {formatExpirationDate(expiration)}
            </span>
          )}
          {trade.scanner_source && (
            <span className="rounded-md bg-oss-surface px-1.5 py-0.5 text-xs text-oss-text-tertiary border border-oss-border">
              {trade.scanner_source.replace(/_/g, ' ')}
            </span>
          )}
        </div>
        <UrgencyBadge isNearSl={isNearSl} isNearTp={isNearTp} />
      </div>

      {/* Hero $ P&L + % */}
      <div className="flex items-baseline gap-3">
        <span
          className={clsx(
            'text-xl font-bold font-mono tracking-tight',
            pnlColor(trade.dollar_pnl_open)
          )}
        >
          {fmtUsd(trade.dollar_pnl_open, { sign: true })}
        </span>
        <span className={clsx('text-sm font-mono', pnlColor(trade.current_pnl_pct))}>
          {fmtPct(trade.current_pnl_pct, { sign: true })}
        </span>
        {trade.dte != null && (
          <span className="ml-auto text-xs text-oss-muted">DTE {trade.dte}</span>
        )}
      </div>

      {/* Progress bar: whichever one is the attention trigger */}
      <div className="space-y-2">
        {isNearTp && (
          <TPSLProgressBar
            kind="tp"
            progress={trade.tp_progress_pct}
            label={`To TP (${trade.thesis_tp1_pct?.toFixed(0)}%)`}
          />
        )}
        {isNearSl && (
          <TPSLProgressBar
            kind="sl"
            progress={trade.sl_progress_pct}
            label={`To SL (\u2212${trade.thesis_sl_pct?.toFixed(0)}%)`}
          />
        )}
      </div>

      {paperClosed && <PaperClosedNotice trade={trade} />}

      {/* Actions */}
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={() => onClose(trade)}
          disabled={closingId === trade.trade_id}
          className={clsx(
            'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium',
            'border border-oss-reject/40 bg-oss-reject/10 text-oss-reject-text',
            'hover:bg-oss-reject/20 hover:border-oss-reject/60',
            'disabled:opacity-50 disabled:cursor-not-allowed transition-colors'
          )}
        >
          <X className="h-3 w-3" />
          {closingId === trade.trade_id ? 'Closing\u2026' : 'Close'}
        </button>
        <Link
          to={`/trades/${trade.trade_id}`}
          className={clsx(
            'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs',
            'border border-oss-border bg-oss-surface text-oss-text-secondary',
            'hover:bg-oss-hover hover:border-oss-border-active transition-colors'
          )}
        >
          <ExternalLink className="h-3 w-3" />
          View
        </Link>
      </div>
    </div>
  )
}

function UrgencyBadge({ isNearSl, isNearTp }: { isNearSl: boolean; isNearTp: boolean }) {
  if (isNearSl) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-oss-reject/10 px-2 py-0.5 text-xs text-oss-reject-text border border-oss-reject/40">
        <AlertTriangle className="h-3 w-3" />
        Near SL
      </span>
    )
  }
  if (isNearTp) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-oss-approve/10 px-2 py-0.5 text-xs text-oss-approve-text border border-oss-approve/40">
        <Target className="h-3 w-3" />
        Near TP
      </span>
    )
  }
  return null
}

function PaperClosedNotice({ trade }: { trade: LiveTrade }) {
  const price = trade.paper_exit_price
  const reason = trade.paper_exit_reason
  const date = trade.paper_exit_date
  return (
    <div className="flex items-start gap-2 rounded-md bg-oss-watch/10 border border-oss-watch/30 p-2 text-xs text-oss-watch-text">
      <Zap className="h-3.5 w-3.5 mt-0.5 shrink-0" />
      <div>
        <span className="font-medium">System closed paper position</span>
        {price != null && <> at ${price.toFixed(2)}</>}
        {reason && <> ({reason.replace(/_/g, ' ').toLowerCase()})</>}
        {date && <> on {date}</>}
        <span className="text-oss-muted"> {'\u2014'} decide whether to close manually.</span>
      </div>
    </div>
  )
}
