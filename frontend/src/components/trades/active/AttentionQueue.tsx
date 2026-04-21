import { AlertTriangle } from 'lucide-react'
import type { LivePosition } from '@/lib/types'
import AttentionCard from './AttentionCard'

interface Props {
  positions: LivePosition[]
  onClose: (position: LivePosition) => void
  closingId: string | null
}

export default function AttentionQueue({ positions, onClose, closingId }: Props) {
  if (positions.length === 0) return null

  return (
    <section className="space-y-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-oss-text-secondary uppercase tracking-wide">
        <AlertTriangle className="h-4 w-4 text-oss-watch" />
        Needs attention
        <span className="text-oss-muted font-mono normal-case">
          ({positions.length})
        </span>
      </h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {positions.map((p) => (
          <AttentionCard
            key={p.position_id}
            position={p}
            onClose={onClose}
            closingId={closingId}
          />
        ))}
      </div>
    </section>
  )
}
