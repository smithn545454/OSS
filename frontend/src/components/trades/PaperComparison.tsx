import clsx from 'clsx'
import { Clock, TrendingUp, TrendingDown, ArrowRight } from 'lucide-react'
import type { RealTrade, PaperComparisonResponse } from '@/lib/types'

const EXIT_REASON_LABELS: Record<string, string> = {
  PROFIT_TARGET: 'Profit Target',
  STOP_LOSS: 'Stop Loss',
  TIME_EXIT: 'Time Exit',
  EXPIRATION: 'Expiration',
  MANUAL: 'Manual',
  MAX_HOLDING: 'Max Holding',
  TRAILING_STOP: 'Trailing Stop',
  THESIS_INVALIDATED: 'Thesis Invalidated',
  OTHER: 'Other',
}

function formatPnl(pct: number | null | undefined): string {
  if (pct == null) return '—'
  return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`
}

function formatDollars(val: number | null | undefined): string {
  if (val == null) return '—'
  return `${val > 0 ? '+' : ''}$${val.toFixed(0)}`
}

interface Props {
  trade: RealTrade
  comparison: PaperComparisonResponse
}

export default function PaperComparison({ trade, comparison }: Props) {
  const paper = comparison.paper_position
  if (!paper) return null

  const paperClosed = paper.status === 'CLOSED'
  const realPnl = trade.realized_pnl_pct ?? 0
  const paperPnl = paperClosed
    ? ((paper.exit_price ?? paper.current_price) - paper.entry_price) / paper.entry_price * 100
    : paper.current_pnl_pct

  // Compute days difference
  const realExitDate = trade.closed_at?.slice(0, 10)
  const realDaysHeld = realExitDate && trade.tracked_at
    ? Math.max(1, Math.round(
        (new Date(realExitDate).getTime() - new Date(trade.tracked_at.slice(0, 10)).getTime()) /
          (1000 * 60 * 60 * 24)
      ))
    : null
  const paperDaysHeld = paper.days_held

  const daysDiff = realDaysHeld != null && paperDaysHeld
    ? paperDaysHeld - realDaysHeld
    : null

  const youBeatPaper = realPnl > paperPnl
  const pnlDiff = realPnl - paperPnl

  // Build timeline from snapshots
  const snapshots = [...comparison.snapshots].sort(
    (a, b) => (a.snapshot_date ?? '').localeCompare(b.snapshot_date ?? '')
  )

  return (
    <div className="rounded-xl border border-oss-border bg-oss-card p-6">
      <h2 className="text-lg font-semibold text-oss-text mb-4">Paper Trade Comparison</h2>

      {/* Timing Verdict */}
      <div
        className={clsx(
          'rounded-lg p-4 mb-6 border',
          youBeatPaper
            ? 'bg-oss-approve/5 border-oss-approve/20'
            : 'bg-amber-500/5 border-amber-500/20'
        )}
      >
        <div className="flex items-center gap-3">
          {youBeatPaper ? (
            <TrendingUp className="h-5 w-5 text-oss-approve shrink-0" />
          ) : (
            <TrendingDown className="h-5 w-5 text-amber-400 shrink-0" />
          )}
          <div>
            <p
              className={clsx(
                'font-semibold',
                youBeatPaper ? 'text-oss-approve' : 'text-amber-400'
              )}
            >
              {daysDiff != null && daysDiff > 0
                ? `You exited ${daysDiff} day${daysDiff !== 1 ? 's' : ''} early`
                : daysDiff != null && daysDiff < 0
                  ? `You exited ${Math.abs(daysDiff)} day${Math.abs(daysDiff) !== 1 ? 's' : ''} late`
                  : 'You exited at the same time'}
            </p>
            <p className="text-sm text-oss-muted mt-0.5">
              Your P&L: <span className={clsx('font-mono', realPnl > 0 ? 'text-oss-approve' : 'text-oss-reject')}>{formatPnl(realPnl)}</span>
              {' vs Paper: '}
              <span className={clsx('font-mono', paperPnl > 0 ? 'text-oss-approve' : 'text-oss-reject')}>{formatPnl(paperPnl)}</span>
              {' '}
              <span className={clsx('font-mono', pnlDiff > 0 ? 'text-oss-approve' : 'text-amber-400')}>
                ({pnlDiff > 0 ? '+' : ''}{pnlDiff.toFixed(1)}pp)
              </span>
            </p>
          </div>
        </div>
      </div>

      {/* Side-by-Side Metrics */}
      <div className="grid grid-cols-[1fr_1fr_1fr] gap-0 text-sm mb-6">
        {/* Header */}
        <div className="text-xs text-oss-muted font-medium pb-2 border-b border-oss-border" />
        <div className="text-xs text-oss-muted font-medium pb-2 border-b border-oss-border text-center">
          Your Trade
        </div>
        <div className="text-xs text-oss-muted font-medium pb-2 border-b border-oss-border text-center">
          Paper Trade
        </div>

        <CompRow
          label="Entry Price"
          yours={`$${Number(trade.entry_price).toFixed(2)}`}
          paper={`$${paper.entry_price.toFixed(2)}`}
        />
        <CompRow
          label="Exit Price"
          yours={trade.exit_price != null ? `$${Number(trade.exit_price).toFixed(2)}` : '—'}
          paper={
            paperClosed && paper.exit_price != null
              ? `$${paper.exit_price.toFixed(2)}`
              : `$${paper.current_price.toFixed(2)} (live)`
          }
        />
        <CompRow
          label="Exit Reason"
          yours={trade.exit_reason ? EXIT_REASON_LABELS[trade.exit_reason] ?? trade.exit_reason : '—'}
          paper={
            paperClosed && paper.exit_reason
              ? EXIT_REASON_LABELS[paper.exit_reason] ?? paper.exit_reason
              : paper.status === 'OPEN'
                ? 'Still open'
                : '—'
          }
        />
        <CompRow
          label="Days Held"
          yours={realDaysHeld != null ? String(realDaysHeld) : '—'}
          paper={String(paperDaysHeld)}
        />
        <CompRow
          label="P&L %"
          yours={formatPnl(trade.realized_pnl_pct)}
          paper={formatPnl(paperPnl)}
          yoursColor={Number(trade.realized_pnl_pct ?? 0) > 0}
          paperColor={paperPnl > 0}
        />
        <CompRow
          label="P&L $"
          yours={formatDollars(trade.realized_pnl_dollars)}
          paper={
            paperClosed
              ? formatDollars(
                  ((paper.exit_price ?? paper.current_price) - paper.entry_price) *
                    paper.quantity *
                    100
                )
              : formatDollars(
                  (paper.current_price - paper.entry_price) * paper.quantity * 100
                )
          }
          yoursColor={Number(trade.realized_pnl_dollars ?? 0) > 0}
          paperColor={paperPnl > 0}
        />
        <CompRow
          label="MFE"
          yours="—"
          paper={formatPnl(paper.max_favorable_excursion)}
          paperColor={paper.max_favorable_excursion > 0}
        />
        <CompRow
          label="MAE"
          yours="—"
          paper={formatPnl(paper.max_adverse_excursion)}
          paperColor={false}
        />
      </div>

      {/* Daily P&L Timeline */}
      {snapshots.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-oss-text mb-3">Daily P&L Timeline</h3>
          <div className="space-y-0">
            {/* Entry */}
            <TimelineRow
              date={paper.entry_date}
              pnl={0}
              label="Paper entry"
              isRealExit={false}
              isPaperExit={false}
            />
            {snapshots.map((snap) => {
              const date = snap.snapshot_date
              const pnl = snap.current_pnl_pct ?? 0
              const isRealExit = realExitDate === date
              const isPaperExit = paperClosed && paper.exit_date === date
              return (
                <TimelineRow
                  key={date}
                  date={date}
                  pnl={Number(pnl)}
                  isRealExit={isRealExit}
                  isPaperExit={isPaperExit}
                  label={
                    isRealExit && isPaperExit
                      ? 'Your exit + Paper exit'
                      : isRealExit
                        ? 'Your exit'
                        : isPaperExit
                          ? `Paper exit (${EXIT_REASON_LABELS[paper.exit_reason ?? ''] ?? paper.exit_reason})`
                          : undefined
                  }
                />
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function CompRow({
  label,
  yours,
  paper,
  yoursColor,
  paperColor,
}: {
  label: string
  yours: string
  paper: string
  yoursColor?: boolean
  paperColor?: boolean
}) {
  return (
    <>
      <div className="py-2 border-b border-oss-border/50 text-xs text-oss-muted">{label}</div>
      <div
        className={clsx(
          'py-2 border-b border-oss-border/50 text-center font-mono',
          yoursColor === true
            ? 'text-oss-approve'
            : yoursColor === false
              ? 'text-oss-reject'
              : 'text-oss-text'
        )}
      >
        {yours}
      </div>
      <div
        className={clsx(
          'py-2 border-b border-oss-border/50 text-center font-mono',
          paperColor === true
            ? 'text-oss-approve'
            : paperColor === false
              ? 'text-oss-reject'
              : 'text-oss-text'
        )}
      >
        {paper}
      </div>
    </>
  )
}

function TimelineRow({
  date,
  pnl,
  label,
  isRealExit,
  isPaperExit,
}: {
  date: string
  pnl: number
  label?: string
  isRealExit: boolean
  isPaperExit: boolean
}) {
  const hasMarker = isRealExit || isPaperExit
  return (
    <div
      className={clsx(
        'flex items-center gap-3 px-3 py-1.5 text-sm rounded',
        hasMarker && 'bg-oss-surface'
      )}
    >
      {/* Date */}
      <span className="text-xs text-oss-muted font-mono w-24 shrink-0">{date}</span>

      {/* P&L bar */}
      <div className="flex-1 flex items-center gap-2">
        <div className="w-16 text-right">
          <span
            className={clsx(
              'font-mono text-xs',
              pnl > 0 ? 'text-oss-approve' : pnl < 0 ? 'text-oss-reject' : 'text-oss-muted'
            )}
          >
            {formatPnl(pnl)}
          </span>
        </div>
        <div className="flex-1 h-1.5 bg-oss-border/30 rounded-full overflow-hidden">
          <div
            className={clsx(
              'h-full rounded-full transition-all',
              pnl > 0 ? 'bg-oss-approve' : pnl < 0 ? 'bg-oss-reject' : 'bg-oss-muted'
            )}
            style={{
              width: `${Math.min(100, Math.abs(pnl))}%`,
              marginLeft: pnl < 0 ? 'auto' : undefined,
            }}
          />
        </div>
      </div>

      {/* Marker */}
      {hasMarker && (
        <span
          className={clsx(
            'text-xs font-medium shrink-0 flex items-center gap-1',
            isRealExit && !isPaperExit ? 'text-blue-400' : 'text-amber-400'
          )}
        >
          {isRealExit && <ArrowRight className="h-3 w-3" />}
          {isPaperExit && <Clock className="h-3 w-3" />}
          {label}
        </span>
      )}
    </div>
  )
}
