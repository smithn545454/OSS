/**
 * Scanner Badge Component
 * 
 * Displays scanner type that triggered the evaluation.
 * Per Section 9.8 of OSS_Opportunities_Page_Specification.
 */

import type { ScannerType } from '@/lib/types'

interface ScannerBadgeProps {
  scanner: ScannerType
  className?: string
}

const SCANNER_LABELS: Record<string, string> = {
  BREAKOUT: 'Breakout',
  BREAKDOWN: 'Breakdown',
  UNUSUAL_VOLUME: 'Unusual Vol',
  COMPRESSION_EXPANSION: 'Compression',
  CHEAP_OPTIONS: 'Cheap',
  REVALIDATION: 'Revalidation',
}

const SCANNER_ICONS: Record<string, string> = {
  BREAKOUT: '↗',
  BREAKDOWN: '↘',
  UNUSUAL_VOLUME: '📊',
  COMPRESSION_EXPANSION: '⟷',
  CHEAP_OPTIONS: '💰',
  REVALIDATION: '↻',
}

export function ScannerBadge({ scanner, className = '' }: ScannerBadgeProps) {
  const label = SCANNER_LABELS[scanner] ?? scanner
  const icon = SCANNER_ICONS[scanner] ?? '•'

  return (
    <span 
      className={`scanner-badge ${className}`}
      title={`Scanner: ${scanner}`}
    >
      <span aria-hidden="true">{icon}</span> {label}
    </span>
  )
}

export default ScannerBadge
