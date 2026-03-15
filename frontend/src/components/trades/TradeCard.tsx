import { Link } from 'react-router-dom'
import { CheckCircle, Eye, Clock, TrendingUp, TrendingDown } from 'lucide-react'
import clsx from 'clsx'
import type { RealTrade } from '@/lib/types'
import { formatDateTime } from '@/lib/formatTime'

interface TradeCardProps {
  trade: RealTrade
}

export default function TradeCard({ trade }: TradeCardProps) {
  const snapshot = trade.snapshot as Record<string, unknown> | undefined
  const ticker = (snapshot?.underlying_ticker as string) || '—'
  const optionType = (snapshot?.option_type as string) || ''
  const strike = snapshot?.strike as number | undefined
  const expiration = snapshot?.expiration_date as string | undefined
  const verdict = (snapshot?.verdict as string) || ''
  const scannerSource = (snapshot?.scanner_source as string) || ''
  const finalScore = snapshot?.final_score as number | undefined

  const isCall = optionType === 'CALL'
  const isOpen = trade.status === 'OPEN'
  const hasPnl = trade.realized_pnl_pct != null
  const isWin = hasPnl && (trade.realized_pnl_pct ?? 0) > 0

  return (
    <Link
      to={`/trades/${trade.trade_id}`}
      className="block rounded-xl border border-oss-border bg-oss-card p-4 hover:border-oss-accent/40 transition-colors"
    >
      {/* Header: Ticker + Status */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg font-semibold text-oss-text">{ticker}</span>
          <span className={clsx(
            'rounded-md px-2 py-0.5 text-xs font-medium',
            isCall ? 'bg-emerald-500/15 text-emerald-400' : 'bg-rose-500/15 text-rose-400'
          )}>
            {optionType}
          </span>
          {verdict === 'APPROVE' && (
            <span className="inline-flex items-center gap-1 rounded-full bg-oss-approve/10 px-2 py-0.5 text-xs text-oss-approve border border-oss-approve/30">
              <CheckCircle className="h-3 w-3" />
              APPROVE
            </span>
          )}
          {verdict === 'WATCH' && (
            <span className="inline-flex items-center gap-1 rounded-full bg-oss-watch/10 px-2 py-0.5 text-xs text-oss-watch border border-oss-watch/30">
              <Eye className="h-3 w-3" />
              WATCH
            </span>
          )}
        </div>
        {isOpen ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-2 py-0.5 text-xs text-blue-400 border border-blue-500/30">
            <Clock className="h-3 w-3" />
            OPEN
          </span>
        ) : (
          <span className={clsx(
            'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs border',
            isWin
              ? 'bg-oss-approve/10 text-oss-approve border-oss-approve/30'
              : 'bg-oss-reject/10 text-oss-reject border-oss-reject/30'
          )}>
            {isWin ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
            {trade.realized_pnl_pct != null
              ? `${trade.realized_pnl_pct > 0 ? '+' : ''}${Number(trade.realized_pnl_pct).toFixed(1)}%`
              : 'CLOSED'}
          </span>
        )}
      </div>

      {/* Contract details */}
      <div className="text-sm text-oss-muted mb-3">
        ${strike?.toFixed(0)} Strike
        {expiration && ` · Exp ${expiration}`}
        {scannerSource && ` · ${scannerSource.replace('_', ' ')}`}
      </div>

      {/* Metrics row */}
      <div className="flex items-center gap-4 text-xs">
        <div>
          <span className="text-oss-muted">Entry </span>
          <span className="font-mono text-oss-text">${Number(trade.entry_price).toFixed(2)}</span>
        </div>
        <div>
          <span className="text-oss-muted">Qty </span>
          <span className="font-mono text-oss-text">{trade.quantity}</span>
        </div>
        {finalScore != null && (
          <div>
            <span className="text-oss-muted">Score </span>
            <span className={clsx(
              'font-mono',
              finalScore >= 75 ? 'text-oss-approve' :
              finalScore >= 65 ? 'text-oss-watch' : 'text-oss-reject'
            )}>{Number(finalScore).toFixed(0)}</span>
          </div>
        )}
        {trade.exit_price != null && (
          <div>
            <span className="text-oss-muted">Exit </span>
            <span className="font-mono text-oss-text">${Number(trade.exit_price).toFixed(2)}</span>
          </div>
        )}
        <div className="ml-auto text-oss-muted">
          {formatDateTime(trade.tracked_at)}
        </div>
      </div>
    </Link>
  )
}
