import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Wallet } from 'lucide-react'
import type { LivePosition } from '@/lib/types'
import {
  useClosePosition,
  useLivePositions,
  useLivePositionsSummary,
} from '@/hooks/useApi'
import PortfolioStatStrip from './PortfolioStatStrip'
import ScannerFilterChips from './ScannerFilterChips'
import AttentionQueue from './AttentionQueue'
import PositionsTable from './PositionsTable'

export default function ActivePositionsDashboard() {
  const queryClient = useQueryClient()
  const [scanner, setScanner] = useState<string>('all')
  const [closingId, setClosingId] = useState<string | null>(null)

  // All open positions (scanner filter applied client-side so the chip counts
  // are accurate regardless of the active filter).
  const livePositions = useLivePositions()
  const summary = useLivePositionsSummary()
  const closeMut = useClosePosition()

  const allPositions = useMemo(
    () => livePositions.data?.positions ?? [],
    [livePositions.data]
  )

  const filtered = useMemo(() => {
    if (scanner === 'all') return allPositions
    return allPositions.filter((p) => p.scanner_source === scanner)
  }, [allPositions, scanner])

  const attention = filtered.filter((p) => p.attention_flag !== null)
  const quiet = filtered.filter((p) => p.attention_flag === null)

  const handleClose = (position: LivePosition) => {
    if (!window.confirm(
      `Close ${position.underlying_ticker ?? position.option_ticker} at current price?`
    )) {
      return
    }
    setClosingId(position.position_id)
    closeMut.mutate(
      { positionId: position.position_id },
      {
        onSettled: () => setClosingId(null),
      }
    )
  }

  const handleRefresh = () => {
    queryClient.invalidateQueries({
      queryKey: ['paper-trading', 'positions', 'live'],
    })
  }

  const isFetching = livePositions.isFetching || summary.isFetching

  if (livePositions.isLoading) {
    return (
      <div className="space-y-4">
        <div className="h-28 animate-pulse rounded-xl bg-oss-surface" />
        <div className="h-16 animate-pulse rounded-xl bg-oss-surface" />
        <div className="h-40 animate-pulse rounded-xl bg-oss-surface" />
      </div>
    )
  }

  if (livePositions.isError) {
    return (
      <div className="rounded-xl border border-oss-reject/40 bg-oss-reject/5 p-6 text-sm text-oss-reject-text">
        Failed to load positions. Try again.
      </div>
    )
  }

  const empty = allPositions.length === 0

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
          positions={allPositions}
        />
      )}

      {empty && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-12 text-center">
          <Wallet className="h-12 w-12 text-oss-muted/30 mx-auto mb-4" />
          <p className="text-oss-muted">
            No open positions. The pipeline will enroll new positions on the next run.
          </p>
        </div>
      )}

      {!empty && filtered.length === 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-8 text-center text-sm text-oss-muted">
          No open positions match this scanner filter.
        </div>
      )}

      <AttentionQueue
        positions={attention}
        onClose={handleClose}
        closingId={closingId}
      />
      <PositionsTable
        positions={quiet}
        onClose={handleClose}
        closingId={closingId}
      />
    </div>
  )
}
