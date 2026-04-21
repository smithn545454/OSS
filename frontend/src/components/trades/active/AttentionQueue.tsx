import { AlertTriangle } from 'lucide-react'
import type { LiveTrade } from '@/lib/types'
import AttentionCard from './AttentionCard'

interface Props {
  trades: LiveTrade[]
  onClose: (trade: LiveTrade) => void
  closingId: string | null
}

export default function AttentionQueue({ trades, onClose, closingId }: Props) {
  if (trades.length === 0) return null

  return (
    <section className="space-y-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-oss-text-secondary uppercase tracking-wide">
        <AlertTriangle className="h-4 w-4 text-oss-watch" />
        Needs attention
        <span className="text-oss-muted font-mono normal-case">
          ({trades.length})
        </span>
      </h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {trades.map((t) => (
          <AttentionCard
            key={t.trade_id}
            trade={t}
            onClose={onClose}
            closingId={closingId}
          />
        ))}
      </div>
    </section>
  )
}
