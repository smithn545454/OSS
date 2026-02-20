/**
 * Tab 2: Position Tracker
 * Sortable table with expandable rows showing pillar scores, Greeks, details, AI analysis.
 */

import { useState, useMemo } from 'react'
import clsx from 'clsx'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { usePositionSnapshots, useAnalyzePosition } from '@/hooks/useApi'
import type { EnrichedPosition } from '@/lib/types'

interface PositionTrackerProps {
  positions: EnrichedPosition[]
}

type SortField =
  | 'underlying_ticker'
  | 'option_ticker'
  | 'option_type_parsed'
  | 'conviction_score'
  | 'verdict_at_entry'
  | 'entry_price'
  | 'current_price'
  | 'current_pnl_pct'
  | 'dte'
  | 'scanner_source'
  | 'status'

function computeDTE(expirationDate: string | null): number | null {
  if (!expirationDate) return null
  const diff = new Date(expirationDate).getTime() - Date.now()
  return Math.ceil(diff / 86400000)
}

const SCANNER_SHORT: Record<string, string> = {
  BREAKOUT: 'BRK',
  UNUSUAL_VOLUME: 'UV',
  COMPRESSION_EXPANSION: 'CMP',
  CHEAP_OPTIONS: 'CHP',
}

export default function PositionTracker({ positions }: PositionTrackerProps) {
  const [sortField, setSortField] = useState<SortField>('entry_price')
  const [sortAsc, setSortAsc] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const sorted = useMemo(() => {
    const copy = [...positions]
    copy.sort((a, b) => {
      let aVal: number | string = ''
      let bVal: number | string = ''

      switch (sortField) {
        case 'underlying_ticker':
          aVal = a.underlying_ticker ?? ''
          bVal = b.underlying_ticker ?? ''
          break
        case 'conviction_score':
          aVal = a.conviction_score ?? -1
          bVal = b.conviction_score ?? -1
          break
        case 'entry_price':
          aVal = a.entry_price
          bVal = b.entry_price
          break
        case 'current_price':
          aVal = a.current_price
          bVal = b.current_price
          break
        case 'current_pnl_pct':
          aVal = a.current_pnl_pct
          bVal = b.current_pnl_pct
          break
        case 'dte':
          aVal = computeDTE(a.expiration_date) ?? 999
          bVal = computeDTE(b.expiration_date) ?? 999
          break
        default:
          aVal = String((a as unknown as Record<string, unknown>)[sortField] ?? '')
          bVal = String((b as unknown as Record<string, unknown>)[sortField] ?? '')
      }

      if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
      }
      return sortAsc ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number)
    })
    return copy
  }, [positions, sortField, sortAsc])

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortAsc(!sortAsc)
    } else {
      setSortField(field)
      setSortAsc(false)
    }
  }

  const headers: { field: SortField; label: string }[] = [
    { field: 'underlying_ticker', label: 'Ticker' },
    { field: 'option_ticker', label: 'Contract' },
    { field: 'option_type_parsed', label: 'Side' },
    { field: 'conviction_score', label: 'Score' },
    { field: 'verdict_at_entry', label: 'Verdict' },
    { field: 'entry_price', label: 'Entry' },
    { field: 'current_price', label: 'Current' },
    { field: 'current_pnl_pct', label: 'P&L' },
    { field: 'dte', label: 'DTE' },
    { field: 'scanner_source', label: 'Scanner' },
    { field: 'status', label: 'Status' },
  ]

  return (
    <div className="rounded-lg border border-oss-border bg-oss-surface overflow-hidden">
      {/* Header */}
      <div className="grid grid-cols-[100px_100px_60px_65px_75px_65px_75px_85px_80px_85px_70px] gap-0 border-b border-oss-border bg-oss-bg/50 px-3 py-2">
        {headers.map((h) => (
          <button
            key={h.field}
            onClick={() => toggleSort(h.field)}
            className="text-left text-xs font-medium text-oss-muted uppercase tracking-wider hover:text-oss-text transition-colors"
          >
            {h.label}
            {sortField === h.field && (
              <span className="ml-0.5">{sortAsc ? '\u2191' : '\u2193'}</span>
            )}
          </button>
        ))}
      </div>

      {/* Rows */}
      {sorted.length === 0 ? (
        <div className="px-4 py-12 text-center text-oss-muted text-sm">
          No paper positions tracked yet. Positions will appear here automatically as the pipeline
          generates evaluations.
        </div>
      ) : (
        sorted.map((pos) => {
          const dte = computeDTE(pos.expiration_date)
          const isExpanded = expandedId === pos.position_id

          return (
            <div key={pos.position_id}>
              <button
                onClick={() => setExpandedId(isExpanded ? null : pos.position_id)}
                className="w-full grid grid-cols-[100px_100px_60px_65px_75px_65px_75px_85px_80px_85px_70px] gap-0 px-3 py-2.5 text-left hover:bg-oss-border/30 transition-colors border-b border-oss-border/50"
              >
                <span className="text-xs font-medium text-oss-text flex items-center gap-1">
                  {isExpanded ? (
                    <ChevronDown className="h-3 w-3 text-oss-muted" />
                  ) : (
                    <ChevronRight className="h-3 w-3 text-oss-muted" />
                  )}
                  {pos.underlying_ticker}
                </span>
                <span className="text-xs text-oss-muted truncate">
                  {pos.option_ticker.replace('O:', '')}
                </span>
                <span className="text-xs text-oss-muted">
                  {pos.option_type_parsed ?? '--'}
                </span>
                <span className="text-xs text-oss-accent font-medium">
                  {pos.conviction_score != null ? pos.conviction_score.toFixed(0) : '--'}
                </span>
                <span
                  className={clsx('text-xs font-medium', {
                    'text-oss-approve': pos.verdict_at_entry === 'APPROVE',
                    'text-oss-watch': pos.verdict_at_entry === 'WATCH',
                  })}
                >
                  {pos.verdict_at_entry}
                </span>
                <span className="text-xs text-oss-muted">${pos.entry_price.toFixed(2)}</span>
                <span className="text-xs text-oss-text">${pos.current_price.toFixed(2)}</span>
                <span
                  className={clsx('text-xs font-medium', {
                    'text-oss-approve': pos.current_pnl_pct > 0,
                    'text-oss-reject': pos.current_pnl_pct < 0,
                    'text-oss-muted': pos.current_pnl_pct === 0,
                  })}
                >
                  {pos.current_pnl_pct > 0 ? '+' : ''}
                  {pos.current_pnl_pct.toFixed(1)}%
                </span>
                <span className="text-xs text-oss-muted">
                  {pos.status === 'CLOSED'
                    ? 'EXP'
                    : dte != null
                      ? `${dte}d`
                      : '--'}
                </span>
                <span className="text-xs text-oss-muted">
                  {pos.scanner_source ? SCANNER_SHORT[pos.scanner_source] || pos.scanner_source : '--'}
                </span>
                <span
                  className={clsx('text-xs font-medium', {
                    'text-oss-approve': pos.status === 'OPEN',
                    'text-oss-muted': pos.status === 'CLOSED',
                  })}
                >
                  {pos.status}
                </span>
              </button>

              {isExpanded && <ExpandedPanel position={pos} />}
            </div>
          )
        })
      )}
    </div>
  )
}

function ExpandedPanel({ position: pos }: { position: EnrichedPosition }) {
  const { data: snapshotData } = usePositionSnapshots(pos.position_id)
  const analyzeMutation = useAnalyzePosition()
  const [analysisText, setAnalysisText] = useState<string | null>(null)

  const latestSnapshot = snapshotData?.snapshots?.[0]

  const handleAnalyze = async () => {
    try {
      const result = await analyzeMutation.mutateAsync(pos.position_id)
      setAnalysisText(result.analysis)
    } catch {
      setAnalysisText('Analysis unavailable.')
    }
  }

  // Auto-trigger analysis on first expand
  if (analysisText === null && !analyzeMutation.isPending) {
    handleAnalyze()
  }

  const pillarBar = (label: string, value: number | null) => (
    <div className="flex items-center gap-2">
      <span className="text-xs text-oss-muted w-20">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-oss-border overflow-hidden">
        <div
          className={clsx('h-full rounded-full', {
            'bg-oss-approve': (value ?? 0) >= 70,
            'bg-oss-watch': (value ?? 0) >= 50 && (value ?? 0) < 70,
            'bg-oss-reject': (value ?? 0) < 50,
          })}
          style={{ width: `${Math.min(value ?? 0, 100)}%` }}
        />
      </div>
      <span className="text-xs text-oss-text w-8 text-right">
        {value != null ? value.toFixed(0) : '--'}
      </span>
    </div>
  )

  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-4 p-4 bg-oss-bg/30 border-b border-oss-border">
      {/* Pillar Scores */}
      <div className="space-y-2">
        <h4 className="text-xs font-medium text-oss-text uppercase tracking-wider mb-2">
          Pillar Scores
        </h4>
        {pillarBar('Directional', pos.pillar_directional)}
        {pillarBar('Structure', pos.pillar_structure)}
        {pillarBar('Volatility', pos.pillar_volatility)}
        <div className="flex items-center gap-1 mt-2">
          <span className="text-xs text-oss-muted">Convergence:</span>
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className={clsx('h-2.5 w-2.5 rounded-sm', {
                'bg-oss-accent': i < (pos.convergence_count ?? 0),
                'bg-oss-border': i >= (pos.convergence_count ?? 0),
              })}
            />
          ))}
        </div>
      </div>

      {/* Greeks */}
      <div>
        <h4 className="text-xs font-medium text-oss-text uppercase tracking-wider mb-2">
          Greeks
        </h4>
        {pos.status === 'CLOSED' ? (
          <div className="text-xs text-oss-muted italic">Contract expired</div>
        ) : latestSnapshot ? (
          <div className="space-y-1 text-xs">
            <div className="flex justify-between">
              <span className="text-oss-muted">Delta</span>
              <span className="text-oss-text">{latestSnapshot.delta?.toFixed(3) ?? '--'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-oss-muted">Theta</span>
              <span className="text-oss-text">{latestSnapshot.theta?.toFixed(3) ?? '--'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-oss-muted">IV</span>
              <span className="text-oss-text">
                {latestSnapshot.iv != null ? `${(latestSnapshot.iv * 100).toFixed(1)}%` : '--'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-oss-muted">Theta/Day</span>
              <span className="text-oss-text">
                {latestSnapshot.theta != null
                  ? `$${(Math.abs(latestSnapshot.theta) * 100).toFixed(0)}`
                  : '--'}
              </span>
            </div>
          </div>
        ) : pos.entry_delta != null ? (
          <div className="space-y-1 text-xs">
            <div className="text-oss-muted italic mb-1">Entry Greeks (no snapshots yet)</div>
            <div className="flex justify-between">
              <span className="text-oss-muted">Delta</span>
              <span className="text-oss-text">{pos.entry_delta?.toFixed(3) ?? '--'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-oss-muted">Theta</span>
              <span className="text-oss-text">{pos.entry_theta?.toFixed(3) ?? '--'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-oss-muted">IV</span>
              <span className="text-oss-text">
                {pos.entry_iv != null ? `${(pos.entry_iv * 100).toFixed(1)}%` : '--'}
              </span>
            </div>
          </div>
        ) : (
          <div className="text-xs text-oss-muted italic">No Greek data available</div>
        )}
      </div>

      {/* Trade Details */}
      <div>
        <h4 className="text-xs font-medium text-oss-text uppercase tracking-wider mb-2">
          Trade Details
        </h4>
        <div className="space-y-1 text-xs">
          <div className="flex justify-between">
            <span className="text-oss-muted">Entry Date</span>
            <span className="text-oss-text">{pos.entry_date}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-oss-muted">Expiry</span>
            <span className="text-oss-text">{pos.expiration_date ?? '--'}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-oss-muted">Scanner</span>
            <span className="text-oss-text">
              {pos.scanner_source
                ? ({
                    BREAKOUT: 'Breakout',
                    BREAKDOWN: 'Breakdown',
                    UNUSUAL_VOLUME: 'Unusual Volume',
                    COMPRESSION_EXPANSION: 'Compression',
                    CHEAP_OPTIONS: 'Cheap Options',
                  } as Record<string, string>)[pos.scanner_source] ?? pos.scanner_source
                : '--'}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-oss-muted">$ P&L</span>
            <span
              className={clsx({
                'text-oss-approve': pos.current_pnl_pct > 0,
                'text-oss-reject': pos.current_pnl_pct < 0,
                'text-oss-muted': pos.current_pnl_pct === 0,
              })}
            >
              ${((pos.current_price - pos.entry_price) * 100).toFixed(0)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-oss-muted">Max Risk</span>
            <span className="text-oss-text">${(pos.entry_price * 100).toFixed(0)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-oss-muted">MFE</span>
            <span className="text-oss-approve">+{pos.max_favorable_excursion.toFixed(1)}%</span>
          </div>
          <div className="flex justify-between">
            <span className="text-oss-muted">MAE</span>
            <span className="text-oss-reject">{pos.max_adverse_excursion.toFixed(1)}%</span>
          </div>
          {pos.exit_reason && (
            <div className="flex justify-between">
              <span className="text-oss-muted">Exit Reason</span>
              <span className="text-oss-text">{pos.exit_reason}</span>
            </div>
          )}
        </div>
      </div>

      {/* AI Analysis */}
      <div>
        <h4 className="text-xs font-medium text-oss-text uppercase tracking-wider mb-2">
          AI Analysis
        </h4>
        {analyzeMutation.isPending ? (
          <div className="text-xs text-oss-muted animate-pulse">Generating analysis...</div>
        ) : analysisText ? (
          <p className="text-xs text-oss-text leading-relaxed">{analysisText}</p>
        ) : (
          <div className="text-xs text-oss-muted">Analysis pending...</div>
        )}
      </div>
    </div>
  )
}
