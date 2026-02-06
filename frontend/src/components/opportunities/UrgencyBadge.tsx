/**
 * Urgency Badge Component
 * 
 * Displays time-sensitivity urgency level for an opportunity.
 * Per Section 5.1.4 of OSS_Opportunities_Page_Specification.
 */

import type { UrgencyLevel } from '@/lib/types'

interface UrgencyBadgeProps {
  urgency: UrgencyLevel
  size?: 'default' | 'small'
  className?: string
}

const URGENCY_CONFIG: Record<UrgencyLevel, { label: string; icon: string }> = {
  act_now: { label: 'ACT NOW', icon: '🔴' },
  hours: { label: 'HOURS', icon: '🟡' },
  patient: { label: 'PATIENT', icon: '🟢' },
}

export function UrgencyBadge({ urgency, size = 'default', className = '' }: UrgencyBadgeProps) {
  const config = URGENCY_CONFIG[urgency]
  const sizeClass = size === 'small' ? 'urgency-badge--small' : ''
  const urgencyClass = `urgency-badge--${urgency.replace('_', '-')}`

  return (
    <span 
      className={`urgency-badge ${urgencyClass} ${sizeClass} ${className}`}
      role="status"
      aria-label={`Urgency: ${config.label}`}
    >
      <span aria-hidden="true">{config.icon}</span>
      <span>{config.label}</span>
    </span>
  )
}

export default UrgencyBadge
