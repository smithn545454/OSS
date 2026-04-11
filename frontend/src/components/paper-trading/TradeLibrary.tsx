/**
 * Trade Library tab — server-side paginated/sorted table of all positions.
 * Expandable detail panels with pillar scores, contract info, MFE/MAE.
 */

import { useState } from 'react'
import clsx from 'clsx'
import {
  ChevronDown,
  ChevronRight,
  ChevronLeft,
  ChevronsLeft,
  ChevronsRight,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
} from 'lucide-react'
import { useBrowsePositions, usePositionSnapshots } from '@/hooks/useApi'
import type { PaperPosition } from '@/lib/types'

const SCANNER_SHORT: Record<string, string> = {
  BREAKOUT: 'BRK',
  UNUSUAL_VOLUME: 'UV',
  COMPRESSION_EXPANSION: 'CMP',
  CHEAP_OPTIONS: 'CHP',
}

type SortField =
  | 'entry_date'
  | 'current_pnl_pct'
  | 'conviction_score'
  | 'scanner_source'
  | 'days_held'
  | 'underlying_ticker'

const COLUMNS: Array<{
  key: SortField | 'scanners' | 'contract' | 'status'
  label: string
  sortable: boolean
  className: string
}> = [
  { key: 'underlying_ticker', label: 'Ticker', sortable: true, className: 'w-[80px]' },
  { key: 'scanners', label: 'Scanner(s)', sortable: false, className: 'w-[120px]' },
  { key: 'conviction_score', label: 'Score', sortable: true, className: 'w-[70px] text-right' },
  { key: 'contract', label: 'Contract', sortable: false, className: 'w-[100px]' },
  { key: 'entry_date', label: 'Entry', sortable: true, className: 'w-[90px]' },
  { key: 'days_held', label: 'Days', sortable: true, className: 'w-[60px] text-right' },
  { key: 'current_pnl_pct', label: 'Return', sortable: true, className: 'w-[90px] text-right' },
  { key: 'status', label: 'Status', sortable: false, className: 'w-[70px] text-center' },
]

function fmt(val: number | null | undefined, decimals: number = 1): string {
  if (val == null) return '--'
  return val.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function ScannerPills({ position }: { position: PaperPosition }) {
  const scanners = position.scanner_list ?? (position.scanner_source ? [position.scanner_source] : [])
  return (
    <div className="flex gap-1 flex-wrap">
      {scanners.map((s) => (
        <span
          key={s}
          className="inline-block rounded bg-oss-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-oss-accent border border-oss-accent/20"
        >
          {SCANNER_SHORT[s] ?? s}
        </span>
      ))}
    </div>
  )
}

function ExpandedDetail({ position }: { position: PaperPosition }) {
  const { data: snapshots } = usePositionSnapshots(position.position_id)

  return (
    <div className="bg-oss-bg/30 border-b border-oss-border px-6 py-4">
      <div className="grid grid-cols-4 gap-6 text-sm">
        {/* Section A: Contract Details */}
        <div>
          <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-2">Contract</h4>
          <div className="space-y-1">
            <div>
              <span className="text-oss-muted">Option: </span>
              <span className="font-mono text-oss-text">{position.option_ticker}</span>
            </div>
            <div>
              <span className="text-oss-muted">Type: </span>
              <span className="text-oss-text">{position.option_type ?? '--'}</span>
            </div>
            <div>
              <span className="text-oss-muted">Strike: </span>
              <span className="font-mono text-oss-text">${fmt(position.strike, 2)}</span>
            </div>
            <div>
              <span className="text-oss-muted">DTE at Entry: </span>
              <span className="font-mono text-oss-text">{position.dte_at_entry ?? '--'}</span>
            </div>
            <div>
              <span className="text-oss-muted">Entry IV: </span>
              <span className="font-mono text-oss-text">
                {position.entry_iv != null ? `${fmt(position.entry_iv * 100)}%` : '--'}
              </span>
            </div>
          </div>
        </div>

        {/* Section B: Pillar Scores */}
        <div>
          <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-2">Evaluation</h4>
          <div className="space-y-1">
            <div>
              <span className="text-oss-muted">Conviction: </span>
              <span className="font-mono text-oss-accent">{position.conviction_score ?? '--'}</span>
            </div>
            <div>
              <span className="text-oss-muted">Prem. Leverage: </span>
              <span className="font-mono text-oss-text">{position.pillar_premium_leverage ?? '--'}</span>
            </div>
            <div>
              <span className="text-oss-muted">Underlying: </span>
              <span className="font-mono text-oss-text">{position.pillar_underlying_behavior ?? '--'}</span>
            </div>
            <div>
              <span className="text-oss-muted">Setup Qual.: </span>
              <span className="font-mono text-oss-text">{position.pillar_setup_quality ?? '--'}</span>
            </div>
            <div>
              <span className="text-oss-muted">Verdict: </span>
              <span className="text-oss-text">{position.verdict_at_entry}</span>
            </div>
          </div>
        </div>

        {/* Section C: Pricing */}
        <div>
          <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-2">Pricing</h4>
          <div className="space-y-1">
            <div>
              <span className="text-oss-muted">Entry: </span>
              <span className="font-mono text-oss-text">${fmt(position.entry_price, 2)}</span>
            </div>
            <div>
              <span className="text-oss-muted">Current: </span>
              <span className="font-mono text-oss-text">${fmt(position.current_price, 2)}</span>
            </div>
            {position.exit_price != null && (
              <div>
                <span className="text-oss-muted">Exit: </span>
                <span className="font-mono text-oss-text">${fmt(position.exit_price, 2)}</span>
              </div>
            )}
            <div>
              <span className="text-oss-muted">Delta: </span>
              <span className="font-mono text-oss-text">{fmt(position.entry_delta, 3)}</span>
            </div>
            <div>
              <span className="text-oss-muted">Theta: </span>
              <span className="font-mono text-oss-text">{fmt(position.entry_theta, 4)}</span>
            </div>
          </div>
        </div>

        {/* Section D: Outcome */}
        <div>
          <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-2">Outcome</h4>
          <div className="space-y-1">
            <div>
              <span className="text-oss-muted">Return: </span>
              <span
                className={clsx(
                  'font-mono font-medium',
                  position.current_pnl_pct >= 0 ? 'text-oss-approve' : 'text-oss-reject'
                )}
              >
                {position.current_pnl_pct >= 0 ? '+' : ''}
                {fmt(position.current_pnl_pct)}%
              </span>
            </div>
            <div>
              <span className="text-oss-muted">Days Held: </span>
              <span className="font-mono text-oss-text">{position.days_held}</span>
            </div>
            <div>
              <span className="text-oss-muted">MFE: </span>
              <span className="font-mono text-oss-approve">
                +{fmt(position.max_favorable_excursion)}%
              </span>
            </div>
            <div>
              <span className="text-oss-muted">MAE: </span>
              <span className="font-mono text-oss-reject">
                {fmt(position.max_adverse_excursion)}%
              </span>
            </div>
            {position.exit_reason && (
              <div>
                <span className="text-oss-muted">Exit: </span>
                <span className="text-oss-text">{position.exit_reason}</span>
              </div>
            )}
          </div>
          {snapshots?.snapshots && snapshots.snapshots.length > 0 && (
            <div className="mt-2 text-[10px] text-oss-muted">
              {snapshots.snapshots.length} snapshots recorded
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

interface TradeLibraryProps {
  period: string
  verdict?: string
  scanner?: string
}

export default function TradeLibrary({ period, verdict, scanner }: TradeLibraryProps) {
  const [sortBy, setSortBy] = useState<string>('entry_date')
  const [sortOrder, setSortOrder] = useState<string>('desc')
  const [page, setPage] = useState(1)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const pageSize = 50

  const { data, isLoading, error } = useBrowsePositions({
    sort_by: sortBy,
    sort_order: sortOrder,
    page,
    page_size: pageSize,
    period,
    verdict,
    scanner,
  })

  const handleSort = (field: string) => {
    if (sortBy === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(field)
      setSortOrder(field === 'entry_date' ? 'desc' : 'desc')
    }
    setPage(1)
  }

  const SortIcon = ({ field }: { field: string }) => {
    if (sortBy !== field) return <ArrowUpDown className="h-3 w-3 text-oss-muted/50" />
    return sortOrder === 'asc' ? (
      <ArrowUp className="h-3 w-3 text-oss-accent" />
    ) : (
      <ArrowDown className="h-3 w-3 text-oss-accent" />
    )
  }

  if (error) {
    return (
      <div className="rounded-lg border border-oss-reject/30 bg-oss-reject/5 p-3 text-xs text-oss-reject">
        Failed to load trades: {error.message}
      </div>
    )
  }

  const positions = data?.positions ?? []
  const totalPages = data?.total_pages ?? 1
  const totalCount = data?.total_count ?? 0

  return (
    <div className="space-y-3">
      {/* Table */}
      <div className="rounded-lg border border-oss-border bg-oss-surface overflow-hidden">
        {/* Header */}
        <div className="grid grid-cols-[80px_120px_70px_100px_90px_60px_90px_70px] gap-2 px-4 py-2.5 bg-oss-bg/50 border-b border-oss-border">
          {COLUMNS.map((col) => (
            <div
              key={col.key}
              className={clsx(
                'text-xs text-oss-muted uppercase tracking-wider flex items-center gap-1',
                col.className,
                col.sortable && 'cursor-pointer hover:text-oss-text'
              )}
              onClick={() => col.sortable && handleSort(col.key)}
            >
              {col.label}
              {col.sortable && <SortIcon field={col.key} />}
            </div>
          ))}
        </div>

        {/* Loading */}
        {isLoading && (
          <div className="px-4 py-8 text-center text-sm text-oss-muted">Loading trades...</div>
        )}

        {/* Rows */}
        {!isLoading &&
          positions.map((p) => (
            <div key={p.position_id}>
              <div
                onClick={() =>
                  setExpandedId(expandedId === p.position_id ? null : p.position_id)
                }
                className="grid grid-cols-[80px_120px_70px_100px_90px_60px_90px_70px] gap-2 px-4 py-2.5 items-center cursor-pointer hover:bg-oss-bg/50 border-b border-oss-border text-sm transition-colors"
              >
                {/* Ticker */}
                <div className="flex items-center gap-1.5">
                  {expandedId === p.position_id ? (
                    <ChevronDown className="h-3.5 w-3.5 text-oss-muted flex-shrink-0" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 text-oss-muted flex-shrink-0" />
                  )}
                  <span className="font-medium text-oss-text truncate">
                    {p.underlying_ticker ?? p.option_ticker.slice(0, 4)}
                  </span>
                </div>

                {/* Scanners */}
                <ScannerPills position={p} />

                {/* Score */}
                <span className="text-right font-mono text-oss-accent">
                  {p.conviction_score ?? '--'}
                </span>

                {/* Contract */}
                <span className="text-xs font-mono text-oss-muted truncate">
                  {p.option_type ?? ''} {p.strike ? `$${fmt(p.strike, 0)}` : ''}
                  {p.dte_bucket ? ` ${p.dte_bucket}` : ''}
                </span>

                {/* Entry Date */}
                <span className="text-xs text-oss-muted">{p.entry_date?.slice(0, 10)}</span>

                {/* Days Held */}
                <span className="text-right font-mono text-sm text-oss-muted">{p.days_held}</span>

                {/* Return */}
                <span
                  className={clsx(
                    'text-right font-mono font-medium',
                    p.current_pnl_pct >= 0 ? 'text-oss-approve' : 'text-oss-reject'
                  )}
                >
                  {p.current_pnl_pct >= 0 ? '+' : ''}
                  {fmt(p.current_pnl_pct)}%
                </span>

                {/* Status */}
                <div className="text-center">
                  <span
                    className={clsx(
                      'inline-block rounded px-1.5 py-0.5 text-[10px] font-medium',
                      p.status === 'OPEN'
                        ? 'bg-oss-accent/10 text-oss-accent border border-oss-accent/20'
                        : 'bg-oss-muted/10 text-oss-muted border border-oss-border'
                    )}
                  >
                    {p.status}
                  </span>
                </div>
              </div>

              {expandedId === p.position_id && <ExpandedDetail position={p} />}
            </div>
          ))}

        {/* Empty State */}
        {!isLoading && positions.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-oss-muted">
            No trades found for the current filters.
          </div>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-oss-muted">
            {totalCount} trades &middot; Page {page} of {totalPages}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(1)}
              disabled={page === 1}
              className="p-1.5 rounded hover:bg-oss-surface disabled:opacity-30 disabled:cursor-not-allowed text-oss-muted"
            >
              <ChevronsLeft className="h-4 w-4" />
            </button>
            <button
              onClick={() => setPage(page - 1)}
              disabled={page === 1}
              className="p-1.5 rounded hover:bg-oss-surface disabled:opacity-30 disabled:cursor-not-allowed text-oss-muted"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              onClick={() => setPage(page + 1)}
              disabled={page >= totalPages}
              className="p-1.5 rounded hover:bg-oss-surface disabled:opacity-30 disabled:cursor-not-allowed text-oss-muted"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
            <button
              onClick={() => setPage(totalPages)}
              disabled={page >= totalPages}
              className="p-1.5 rounded hover:bg-oss-surface disabled:opacity-30 disabled:cursor-not-allowed text-oss-muted"
            >
              <ChevronsRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
