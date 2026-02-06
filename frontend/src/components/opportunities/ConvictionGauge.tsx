/**
 * Conviction Gauge Component
 * 
 * Circular SVG gauge displaying conviction score.
 * Per Section 9.4 of OSS_Opportunities_Page_Specification.
 */

import { getConvictionColorClass } from '@/lib/convictionScore'

interface ConvictionGaugeProps {
  score: number
  size?: 'default' | 'mini'
  className?: string
}

export function ConvictionGauge({ score, size = 'default', className = '' }: ConvictionGaugeProps) {
  const isMini = size === 'mini'
  const diameter = isMini ? 32 : 56
  const strokeWidth = isMini ? 3 : 4
  const radius = (diameter - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const progress = Math.max(0, Math.min(100, score)) / 100
  const strokeDashoffset = circumference * (1 - progress)
  
  // Get color class based on score
  const colorClass = getConvictionColorClass(score)
  const fillClass = `conviction-gauge-fill conviction-gauge-fill--${colorClass.split('-')[1]}`

  return (
    <div 
      className={`conviction-gauge ${isMini ? 'conviction-gauge--mini' : ''} ${className}`}
      role="meter"
      aria-valuenow={Math.round(score)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`Conviction score: ${Math.round(score)}`}
    >
      <svg 
        width={diameter} 
        height={diameter} 
        viewBox={`0 0 ${diameter} ${diameter}`}
      >
        {/* Background track */}
        <circle
          className="conviction-gauge-track"
          cx={diameter / 2}
          cy={diameter / 2}
          r={radius}
        />
        {/* Progress fill */}
        <circle
          className={fillClass}
          cx={diameter / 2}
          cy={diameter / 2}
          r={radius}
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          style={{
            transformOrigin: 'center',
          }}
        />
      </svg>
      <span 
        className="conviction-gauge-value"
        style={{
          color: score >= 85 
            ? 'var(--color-conviction-high)' 
            : score >= 75 
              ? 'var(--color-conviction-medium)' 
              : 'var(--color-conviction-low)',
        }}
      >
        {Math.round(score)}
      </span>
    </div>
  )
}

export default ConvictionGauge
