import { useState } from 'react'
import { X, CheckCircle, AlertCircle, Loader2 } from 'lucide-react'
import { useTrackTrade } from '@/hooks/useApi'
import { ApiError } from '@/lib/api'

interface TrackTradeModalProps {
  isOpen: boolean
  onClose: () => void
  ticker: string
  evaluationId: string
  askPrice: number
  optionTicker: string
  convictionScore?: number
}

export default function TrackTradeModal({
  isOpen,
  onClose,
  ticker,
  evaluationId,
  askPrice,
  optionTicker,
  convictionScore,
}: TrackTradeModalProps) {
  const [entryPrice, setEntryPrice] = useState(askPrice.toFixed(2))
  const [quantity, setQuantity] = useState('1')
  const [notes, setNotes] = useState('')
  const trackTrade = useTrackTrade()

  if (!isOpen) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    trackTrade.mutate(
      {
        ticker,
        evaluation_id: evaluationId,
        entry_price: parseFloat(entryPrice),
        quantity: parseInt(quantity, 10),
        entry_notes: notes || undefined,
        conviction_score: convictionScore,
      },
      {
        onSuccess: () => {
          // Keep modal open briefly to show success
          setTimeout(onClose, 1500)
        },
      }
    )
  }

  const isDuplicate =
    trackTrade.error instanceof ApiError && trackTrade.error.status === 409

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative z-10 w-full max-w-md rounded-2xl border border-oss-border bg-oss-card p-6 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold text-oss-text">
            Track This Trade
          </h2>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-oss-muted hover:bg-oss-surface hover:text-oss-text transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Success State */}
        {trackTrade.isSuccess && (
          <div className="flex flex-col items-center gap-3 py-8">
            <CheckCircle className="h-12 w-12 text-oss-approve" />
            <p className="text-oss-text font-medium">Trade Tracked</p>
            <p className="text-sm text-oss-muted">
              {ticker} {optionTicker.split(':').pop()} added to My Trades
            </p>
          </div>
        )}

        {/* Error State */}
        {trackTrade.isError && !trackTrade.isSuccess && (
          <div className="mb-4 rounded-lg border border-oss-reject/30 bg-oss-reject/10 p-3">
            <div className="flex items-start gap-2">
              <AlertCircle className="h-4 w-4 text-oss-reject mt-0.5 shrink-0" />
              <p className="text-sm text-oss-reject">
                {isDuplicate
                  ? 'This evaluation is already tracked.'
                  : 'Failed to track trade. Please try again.'}
              </p>
            </div>
          </div>
        )}

        {/* Form */}
        {!trackTrade.isSuccess && (
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Contract Info */}
            <div className="rounded-lg bg-oss-bg p-3">
              <p className="text-sm font-medium text-oss-text">{ticker}</p>
              <p className="text-xs text-oss-muted mt-0.5">
                {optionTicker.split(':').pop()}
              </p>
            </div>

            {/* Entry Price */}
            <div>
              <label className="block text-sm font-medium text-oss-muted mb-1.5">
                Entry Price
              </label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-oss-muted">
                  $
                </span>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={entryPrice}
                  onChange={(e) => setEntryPrice(e.target.value)}
                  className="w-full rounded-lg border border-oss-border bg-oss-bg pl-7 pr-3 py-2 text-oss-text font-mono focus:border-oss-accent focus:outline-none focus:ring-1 focus:ring-oss-accent"
                  required
                />
              </div>
              <p className="text-xs text-oss-muted mt-1">
                Pre-filled with ask price (${askPrice.toFixed(2)})
              </p>
            </div>

            {/* Quantity */}
            <div>
              <label className="block text-sm font-medium text-oss-muted mb-1.5">
                Quantity (contracts)
              </label>
              <input
                type="number"
                min="1"
                step="1"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-oss-text font-mono focus:border-oss-accent focus:outline-none focus:ring-1 focus:ring-oss-accent"
                required
              />
            </div>

            {/* Notes */}
            <div>
              <label className="block text-sm font-medium text-oss-muted mb-1.5">
                Notes (optional)
              </label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Why are you taking this trade?"
                rows={2}
                className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-oss-text text-sm placeholder:text-oss-muted/50 focus:border-oss-accent focus:outline-none focus:ring-1 focus:ring-oss-accent resize-none"
              />
            </div>

            {/* Total Cost */}
            <div className="rounded-lg bg-oss-bg p-3 flex justify-between items-center">
              <span className="text-sm text-oss-muted">Est. Total Cost</span>
              <span className="font-mono font-semibold text-oss-text">
                ${(parseFloat(entryPrice || '0') * parseInt(quantity || '0', 10) * 100).toLocaleString()}
              </span>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={trackTrade.isPending || isDuplicate}
              className="w-full rounded-lg bg-oss-accent py-2.5 text-sm font-semibold text-black hover:bg-oss-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
            >
              {trackTrade.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Tracking...
                </>
              ) : (
                'Track Trade'
              )}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
