/**
 * Scanner Intelligence tab — performance table with one row per scanner.
 * Shows win rate, avg return, total P&L, trend sparkline, expandable top trades.
 */

import { useState } from 'react'
import clsx from 'clsx'
import { ChevronDown, ChevronRight, TrendingUp } from 'lucide-react'
import {
  LineChart,
  Line,
  ResponsiveContainer,
} from 'recharts'
import { useScannerPerformance } from '@/hooks/useApi'
import type { ScannerPerformanceData } from '@/lib/types'

const SCANNER_LABELS: Record<string, string> = {
  BREAKOUT: 'Breakout',
  UNUSUAL_VOLUME: 'Unusual Volume',
  COMPRESSION_EXPANSION: 'Compression',
  CHEAP_OPTIONS: 'Cheap Options',
}

const SCANNER_ORDER = ['BREAKOUT', 'COMPRESSION_EXPANSION', 'CHEAP_OPTIONS', 'UNUSUAL_VOLUME']

function fmt(val: number | null | undefined, decimals: number = 1): string {
  if (val == null) return '--'
  return val.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function winRateColor(rate: number): string {
  if (rate >= 60) return 'text-oss-approve'
  if (rate >= 50) return 'text-yellow-400'
  return 'text-oss-reject'
}

function Sparkline({ data }: { data: Array<{ week: string; win_rate: number }> }) {
  if (!data || data.length < 2) return <span className="text-xs text-oss-muted">--</span>
  return (
    <div className="w-20 h-6">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <Line
            type="monotone"
            dataKey="win_rate"
            stroke="#22d3ee"
            strokeWidth={1.5}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

interface ScannerRowProps {
  scanner: string
  data: ScannerPerformanceData
  isExpanded: boolean
  onToggle: () => void
}

function ScannerRow({ scanner, data, isExpanded, onToggle }: ScannerRowProps) {
  return (
    <>
      <div
        onClick={onToggle}
        className="grid grid-cols-[1fr_80px_80px_90px_90px_80px_100px_80px] gap-2 px-4 py-3 items-center cursor-pointer hover:bg-oss-bg/50 border-b border-oss-border transition-colors"
      >
        <div className="flex items-center gap-2">
          {isExpanded ? (
            <ChevronDown className="h-4 w-4 text-oss-muted" />
          ) : (
            <ChevronRight className="h-4 w-4 text-oss-muted" />
          )}
          <span className="font-medium text-oss-text">
            {SCANNER_LABELS[scanner] ?? scanner}
          </span>
        </div>
        <span className="text-right font-mono text-sm">{data.total}</span>
        <span className={clsx('text-right font-mono text-sm', winRateColor(data.win_rate))}>
          {fmt(data.win_rate)}%
        </span>
        <span
          className={clsx(
            'text-right font-mono text-sm',
            data.avg_return >= 0 ? 'text-oss-approve' : 'text-oss-reject'
          )}
        >
          {data.avg_return >= 0 ? '+' : ''}
          {fmt(data.avg_return)}%
        </span>
        <span
          className={clsx(
            'text-right font-mono text-sm',
            data.total_pnl_dollars >= 0 ? 'text-oss-approve' : 'text-oss-reject'
          )}
        >
          ${fmt(data.total_pnl_dollars, 0)}
        </span>
        <span className="text-right font-mono text-sm text-oss-muted">
          {fmt(data.avg_days_held)}d
        </span>
        <div className={clsx('text-right text-xs', data.best_trade && data.best_trade.return_pct >= 0 ? 'text-oss-approve' : 'text-oss-reject')}>
          {data.best_trade
            ? `${data.best_trade.return_pct >= 0 ? '+' : ''}${fmt(data.best_trade.return_pct)}% ${data.best_trade.ticker}`
            : '--'}
        </div>
        <div className="flex justify-end">
          <Sparkline data={data.weekly_win_rates} />
        </div>
      </div>

      {isExpanded && (
        <div className="bg-oss-bg/30 border-b border-oss-border px-8 py-3">
          <div className="text-xs text-oss-muted mb-2 uppercase tracking-wider">
            Recent Performance
          </div>
          <div className="grid grid-cols-4 gap-4 text-sm">
            <div>
              <span className="text-oss-muted">Win/Loss: </span>
              <span className="font-mono text-oss-text">
                {data.win_count}W / {data.loss_count}L
              </span>
            </div>
            <div>
              <span className="text-oss-muted">Closed: </span>
              <span className="font-mono text-oss-text">{data.closed}</span>
            </div>
            <div>
              <span className="text-oss-muted">Open: </span>
              <span className="font-mono text-oss-text">{data.total - data.closed}</span>
            </div>
            <div>
              <span className="text-oss-muted">Avg Hold: </span>
              <span className="font-mono text-oss-text">{fmt(data.avg_days_held)} days</span>
            </div>
          </div>
          {data.weekly_win_rates.length > 0 && (
            <div className="mt-3">
              <div className="text-xs text-oss-muted mb-1">Weekly Win Rates</div>
              <div className="flex gap-1">
                {data.weekly_win_rates.slice(-8).map((w) => (
                  <div
                    key={w.week}
                    className="flex flex-col items-center gap-0.5"
                  >
                    <div
                      className={clsx(
                        'text-[10px] font-mono',
                        w.win_rate >= 60
                          ? 'text-oss-approve'
                          : w.win_rate >= 50
                            ? 'text-yellow-400'
                            : 'text-oss-reject'
                      )}
                    >
                      {Math.round(w.win_rate)}%
                    </div>
                    <div className="text-[9px] text-oss-muted">{w.week.slice(5)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </>
  )
}

interface ScannerIntelligenceProps {
  period: string
}

export default function ScannerIntelligence({ period }: ScannerIntelligenceProps) {
  const { data, isLoading, error } = useScannerPerformance(period)
  const [expandedScanner, setExpandedScanner] = useState<string | null>(null)

  if (isLoading) {
    return (
      <div className="rounded-lg border border-oss-border bg-oss-surface p-8 text-center">
        <TrendingUp className="h-5 w-5 mx-auto mb-2 text-oss-accent animate-pulse" />
        <p className="text-sm text-oss-muted">Loading scanner performance...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-lg border border-oss-reject/30 bg-oss-reject/5 p-3 text-xs text-oss-reject">
        Failed to load scanner performance: {error.message}
      </div>
    )
  }

  const scanners = data?.scanners ?? {}
  const orderedScanners = SCANNER_ORDER.filter((s) => scanners[s])

  if (orderedScanners.length === 0) {
    return (
      <div className="rounded-lg border border-oss-border bg-oss-surface p-8 text-center">
        <p className="text-sm text-oss-muted">No scanner data available for this period.</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-oss-border bg-oss-surface overflow-hidden">
      {/* Header */}
      <div className="grid grid-cols-[1fr_80px_80px_90px_90px_80px_100px_80px] gap-2 px-4 py-2.5 bg-oss-bg/50 border-b border-oss-border text-xs text-oss-muted uppercase tracking-wider">
        <span>Scanner</span>
        <span className="text-right">Trades</span>
        <span className="text-right">Win Rate</span>
        <span className="text-right">Avg Return</span>
        <span className="text-right">Total P&L</span>
        <span className="text-right">Avg Hold</span>
        <span className="text-right">Best Trade</span>
        <span className="text-right">Trend</span>
      </div>

      {/* Rows */}
      {orderedScanners.map((scanner) => (
        <ScannerRow
          key={scanner}
          scanner={scanner}
          data={scanners[scanner]}
          isExpanded={expandedScanner === scanner}
          onToggle={() =>
            setExpandedScanner(expandedScanner === scanner ? null : scanner)
          }
        />
      ))}
    </div>
  )
}
