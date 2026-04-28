import { Sparkles, TrendingUp, TrendingDown } from 'lucide-react'
import clsx from 'clsx'

import type { ConvexUVSignal } from '@/lib/convexTypes'

interface SmartMoneyBadgeProps {
  /** Whether UV alignment with thesis was confirmed (from backend). */
  confirmed: boolean
  /** Aggregated UV signal payload (null if scanner has no detections). */
  signal?: ConvexUVSignal | null
  /** Compact mode — for table rows. */
  compact?: boolean
}

/**
 * Renders the Smart Money badge with rich detail when a UV signal is
 * present. Three visual states:
 *
 * 1. **Confirmed** (green): UV is unusual AND directionally aligned with
 *    the thesis. Shows volume ratio + skew label.
 * 2. **Detected, not aligned** (muted): UV scanner flagged contracts but
 *    skew opposes the thesis (or signal is balanced). Displays as a
 *    tooltip-grade chip without the "confirmed" sparkle.
 * 3. **No signal** (hidden in compact, dash in table): nothing to show.
 */
export function SmartMoneyBadge({
  confirmed,
  signal,
  compact = false,
}: SmartMoneyBadgeProps) {
  if (!signal || signal.detection_count === 0) {
    return compact ? <span className="text-oss-muted">—</span> : null
  }

  const ratio = signal.volume_ratio
  const skewLabel = SKEW_LABEL[signal.directional_skew]
  const SkewIcon = SKEW_ICON[signal.directional_skew]

  if (confirmed) {
    return (
      <span
        className={clsx(
          'inline-flex items-center gap-1.5 rounded-md border border-oss-accent/40 bg-oss-accent/10 px-2.5 py-1 text-xs',
          compact ? '' : 'text-sm',
        )}
        title={`${signal.detection_count} flagged contracts · today ${ratio?.toFixed(1)}× baseline · ${skewLabel}`}
      >
        <Sparkles className="h-3.5 w-3.5 text-oss-accent" />
        <span className="font-semibold text-oss-accent">Smart Money</span>
        {ratio != null && (
          <span className="font-mono text-oss-accent/80">{ratio.toFixed(1)}×</span>
        )}
        <SkewIcon className="h-3 w-3 text-oss-accent/70" />
      </span>
    )
  }

  // UV detected but NOT aligned — show as muted chip so the user can see
  // there's volume noise but it doesn't confirm the thesis.
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-md border border-oss-border bg-oss-bg/40 px-2.5 py-1 text-xs text-oss-muted',
      )}
      title={`UV detected on ${signal.detection_count} contracts but ${skewLabel.toLowerCase()} skew does not align with thesis`}
    >
      <SkewIcon className="h-3 w-3" />
      <span>UV {ratio != null ? `${ratio.toFixed(1)}×` : 'flagged'}</span>
    </span>
  )
}

const SKEW_LABEL: Record<ConvexUVSignal['directional_skew'], string> = {
  call_heavy: 'Call-heavy',
  put_heavy: 'Put-heavy',
  balanced: 'Balanced',
}

const SKEW_ICON: Record<ConvexUVSignal['directional_skew'], React.ElementType> = {
  call_heavy: TrendingUp,
  put_heavy: TrendingDown,
  balanced: Sparkles,
}
