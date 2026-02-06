/**
 * Option Type Badge Component
 * 
 * Displays CALL or PUT badge with directional coloring.
 * Per Section 9.7 of OSS_Opportunities_Page_Specification.
 */

import type { OptionType } from '@/lib/types'

interface OptionTypeBadgeProps {
  type: OptionType
  className?: string
}

export function OptionTypeBadge({ type, className = '' }: OptionTypeBadgeProps) {
  const isCall = type === 'CALL'
  
  const style: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    padding: '3px 10px',
    borderRadius: '10px',
    fontSize: '12px',
    fontWeight: 600,
    fontFamily: 'var(--font-primary)',
    border: '1px solid',
    backgroundColor: isCall 
      ? 'rgba(16, 185, 129, 0.15)' 
      : 'rgba(239, 68, 68, 0.15)',
    borderColor: isCall 
      ? 'var(--color-success)' 
      : 'var(--color-error)',
    color: isCall 
      ? 'var(--color-success-text)' 
      : 'var(--color-error-text)',
  }

  return (
    <span 
      className={className}
      style={style}
      title={type}
    >
      {type}
    </span>
  )
}

export default OptionTypeBadge
