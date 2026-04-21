import clsx from 'clsx'
import { Link } from 'react-router-dom'
import { X, Zap } from 'lucide-react'
import type { LiveTrade } from '@/lib/types'
import { formatExpirationDate } from '@/lib/formatTime'
import { fmtPct, fmtUsd, pnlColor } from './format'

interface Props {
  trades: LiveTrade[]
  onClose: (trade: LiveTrade) => void
  closingId: string | null
}

export default function PositionsTable({ trades, onClose, closingId }: Props) {
  if (trades.length === 0) return null

  return (
    <section className="space-y-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-oss-text-secondary uppercase tracking-wide">
        All active trades
        <span className="text-oss-muted font-mono normal-case">
          ({trades.length})
        </span>
      </h2>
      <div className="rounded-xl border border-oss-border bg-oss-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-oss-border">
              <tr className="text-left text-xs text-oss-muted uppercase tracking-wide">
                <Th>Ticker</Th>
                <Th>Contract</Th>
                <Th>Scanner</Th>
                <Th align="right">$ P&L</Th>
                <Th align="right">%</Th>
                <Th align="right">DTE</Th>
                <Th align="right">TP / SL</Th>
                <Th align="right" className="w-8"></Th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <Row
                  key={t.trade_id}
                  trade={t}
                  onClose={onClose}
                  closingId={closingId}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}

function Th({
  children,
  align = 'left',
  className = '',
}: {
  children?: React.ReactNode
  align?: 'left' | 'right'
  className?: string
}) {
  return (
    <th
      className={clsx(
        'px-3 py-2 font-medium',
        align === 'right' && 'text-right',
        className
      )}
    >
      {children}
    </th>
  )
}

function Row({
  trade,
  onClose,
  closingId,
}: {
  trade: LiveTrade
  onClose: (trade: LiveTrade) => void
  closingId: string | null
}) {
  const optionType = trade.option_type || ''
  const typeChar = optionType === 'CALL' ? 'C' : optionType === 'PUT' ? 'P' : '?'
  const tp = trade.tp_progress_pct
  const sl = trade.sl_progress_pct
  const paperClosed = trade.paper_position_status === 'CLOSED'

  return (
    <tr className="border-t border-oss-border-subtle hover:bg-oss-hover transition-colors">
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          <Link
            to={`/trades/${trade.trade_id}`}
            className="font-semibold text-oss-text hover:text-oss-accent"
          >
            {trade.underlying_ticker || '—'}
          </Link>
          {paperClosed && (
            <span
              title="System closed paper position — decide whether to close manually"
              aria-label="System closed paper position"
            >
              <Zap className="h-3 w-3 text-oss-watch-text" />
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-2.5 text-oss-muted">
        <span className={clsx(
          'mr-1 font-mono font-medium',
          optionType === 'CALL' ? 'text-emerald-400' : 'text-rose-400'
        )}>
          {typeChar}
        </span>
        <span className="font-mono">
          {trade.strike != null ? `$${trade.strike.toFixed(0)}` : '—'}
        </span>
        {trade.expiration_date && (
          <span className="ml-1.5 text-xs">
            {formatExpirationDate(trade.expiration_date)}
          </span>
        )}
      </td>
      <td className="px-3 py-2.5">
        {trade.scanner_source ? (
          <span className="rounded bg-oss-surface px-1.5 py-0.5 text-xs text-oss-text-tertiary border border-oss-border">
            {trade.scanner_source.replace(/_/g, ' ')}
          </span>
        ) : (
          <span className="text-oss-muted text-xs">—</span>
        )}
      </td>
      <td
        className={clsx(
          'px-3 py-2.5 text-right font-mono font-semibold',
          pnlColor(trade.dollar_pnl_open)
        )}
      >
        {fmtUsd(trade.dollar_pnl_open, { sign: true })}
      </td>
      <td
        className={clsx(
          'px-3 py-2.5 text-right font-mono',
          pnlColor(trade.current_pnl_pct)
        )}
      >
        {fmtPct(trade.current_pnl_pct, { sign: true })}
      </td>
      <td className="px-3 py-2.5 text-right font-mono text-oss-text-tertiary">
        {trade.dte ?? '—'}
      </td>
      <td className="px-3 py-2.5 text-right font-mono text-xs text-oss-muted">
        {tp != null || sl != null
          ? `${tp != null ? `${tp.toFixed(0)}%` : '—'} / ${sl != null ? `${sl.toFixed(0)}%` : '—'}`
          : 'pending'}
      </td>
      <td className="px-3 py-2.5 text-right">
        <button
          onClick={() => onClose(trade)}
          disabled={closingId === trade.trade_id}
          className={clsx(
            'inline-flex items-center justify-center rounded-md p-1.5',
            'text-oss-muted hover:text-oss-reject-text hover:bg-oss-reject/10',
            'disabled:opacity-50 disabled:cursor-not-allowed transition-colors'
          )}
          title="Close position"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </td>
    </tr>
  )
}
