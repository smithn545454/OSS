import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Wallet } from 'lucide-react'
import type { LiveTrade } from '@/lib/types'
import {
  useCloseTrade,
  useLiveTrades,
  useLiveTradesSummary,
} from '@/hooks/useApi'
import PortfolioStatStrip from './PortfolioStatStrip'
import ScannerFilterChips from './ScannerFilterChips'
import AttentionQueue from './AttentionQueue'
import PositionsTable from './PositionsTable'

export default function ActivePositionsDashboard() {
  const queryClient = useQueryClient()
  const [scanner, setScanner] = useState<string>('all')
  const [closingId, setClosingId] = useState<string | null>(null)

  const liveTrades = useLiveTrades()
  const summary = useLiveTradesSummary()
  const closeMut = useCloseTrade()

  const allTrades = useMemo(
    () => liveTrades.data?.trades ?? [],
    [liveTrades.data]
  )

  const filtered = useMemo(() => {
    if (scanner === 'all') return allTrades
    return allTrades.filter((t) => t.scanner_source === scanner)
  }, [allTrades, scanner])

  const attention = filtered.filter((t) => t.attention_flag !== null)
  const quiet = filtered.filter((t) => t.attention_flag === null)

  const handleClose = (trade: LiveTrade) => {
    const confirmed = window.confirm(
      `Close ${trade.underlying_ticker ?? trade.option_ticker} at $${trade.current_price.toFixed(
        2
      )}?`
    )
    if (!confirmed) return
    setClosingId(trade.trade_id)
    closeMut.mutate(
      {
        tradeId: trade.trade_id,
        exit_price: trade.current_price,
        exit_reason: 'MANUAL',
      },
      {
        onSettled: () => setClosingId(null),
      }
    )
  }

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: ['trades', 'live'] })
  }

  const isFetching = liveTrades.isFetching || summary.isFetching

  if (liveTrades.isLoading) {
    return (
      <div className="space-y-4">
        <div className="h-28 animate-pulse rounded-xl bg-oss-surface" />
        <div className="h-16 animate-pulse rounded-xl bg-oss-surface" />
        <div className="h-40 animate-pulse rounded-xl bg-oss-surface" />
      </div>
    )
  }

  if (liveTrades.isError) {
    return (
      <div className="rounded-xl border border-oss-reject/40 bg-oss-reject/5 p-6 text-sm text-oss-reject-text">
        Failed to load trades. Try again.
      </div>
    )
  }

  const empty = allTrades.length === 0

  return (
    <div className="space-y-5">
      <PortfolioStatStrip
        summary={summary.data}
        isFetching={isFetching}
        onRefresh={handleRefresh}
      />

      {!empty && (
        <ScannerFilterChips
          value={scanner}
          onChange={setScanner}
          trades={allTrades}
        />
      )}

      {empty && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-12 text-center">
          <Wallet className="h-12 w-12 text-oss-muted/30 mx-auto mb-4" />
          <p className="text-oss-muted">
            No active trades. Track one from an Evaluation Detail page to start monitoring it here.
          </p>
        </div>
      )}

      {!empty && filtered.length === 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-8 text-center text-sm text-oss-muted">
          No active trades match this scanner filter.
        </div>
      )}

      <AttentionQueue
        trades={attention}
        onClose={handleClose}
        closingId={closingId}
      />
      <PositionsTable
        trades={quiet}
        onClose={handleClose}
        closingId={closingId}
      />
    </div>
  )
}
