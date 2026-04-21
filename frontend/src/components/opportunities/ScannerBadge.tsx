/**
 * Scanner Badge Component
 * 
 * Displays scanner type that triggered the evaluation.
 * Per Section 9.8 of OSS_Opportunities_Page_Specification.
 */

import type { ScannerType } from '@/lib/types'

interface ScannerBadgeProps {
  scanner: ScannerType
  /**
   * For REVALIDATION scanners, the originating scanner that produced the
   * upstream APPROVE. Renders as a subtitle / tooltip suffix so operators
   * can tell at a glance which real signal is being re-evaluated.
   */
  originatingScanner?: string | null
  className?: string
}

const SCANNER_LABELS: Record<string, string> = {
  BREAKOUT: 'Breakout',
  BREAKDOWN: 'Breakdown',
  UNUSUAL_VOLUME: 'Unusual Vol',
  COMPRESSION_EXPANSION: 'Compression',
  CHEAP_OPTIONS: 'Cheap',
  REVALIDATION: 'Re-evaluation',
}

const SCANNER_ICONS: Record<string, string> = {
  BREAKOUT: '↗',
  BREAKDOWN: '↘',
  UNUSUAL_VOLUME: '📊',
  COMPRESSION_EXPANSION: '⟷',
  CHEAP_OPTIONS: '💰',
  REVALIDATION: '↻',
}

export function ScannerBadge({
  scanner,
  originatingScanner,
  className = '',
}: ScannerBadgeProps) {
  const label = SCANNER_LABELS[scanner] ?? scanner
  const icon = SCANNER_ICONS[scanner] ?? '•'

  // Only show the originating-scanner suffix when this is a re-evaluation
  // and we actually know the upstream source.
  const upstreamLabel =
    scanner === 'REVALIDATION' && originatingScanner
      ? SCANNER_LABELS[originatingScanner] ?? originatingScanner
      : null

  const title =
    upstreamLabel != null
      ? `Re-evaluation of ${upstreamLabel}`
      : `Scanner: ${scanner}`

  return (
    <span className={`scanner-badge ${className}`} title={title}>
      <span aria-hidden="true">{icon}</span> {label}
      {upstreamLabel && (
        <span className="scanner-badge__origin"> ({upstreamLabel})</span>
      )}
    </span>
  )
}

export default ScannerBadge
