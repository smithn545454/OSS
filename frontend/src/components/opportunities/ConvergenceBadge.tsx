/**
 * Convergence Badge Component
 * 
 * Displays when multiple scanners have identified the same underlying.
 * Per Section 9.9 of OSS_Opportunities_Page_Specification.
 */

interface ConvergenceBadgeProps {
  count: number
  className?: string
}

export function ConvergenceBadge({ count, className = '' }: ConvergenceBadgeProps) {
  // Only show if 2+ scanners
  if (count < 2) return null

  return (
    <span 
      className={`convergence-badge ${className}`}
      title={`${count} different scanners identified this underlying`}
      role="status"
      aria-label={`${count} scanner convergence`}
    >
      <span aria-hidden="true">🎯</span>
      <span>{count}x Convergence</span>
    </span>
  )
}

export default ConvergenceBadge
