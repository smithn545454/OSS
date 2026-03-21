/**
 * Conviction Gauge Component
 * 
 * Circular SVG gauge displaying conviction score.
 * Per Section 9.4 of OSS_Opportunities_Page_Specification.
 */

import { getConvictionColorClass } from '@/lib/convictionScore'

interface ConvictionGaugeProps {
  score: number
  size?: 'default' | 'mini' | 'compact'
  className?: string
}

// Score color for compact size (spec §2.2): cyan for ≥75, amber 60-74, red <60
function getCompactScoreColor(score: number): string {
  if (score >= 75) return '#00E5CC'
  if (score >= 60) return '#FFB800'
  return '#FF4D6A'
}

const SIZE_CONFIG = {
  default: { diameter: 56, strokeWidth: 4 },
  mini: { diameter: 32, strokeWidth: 3 },
  compact: { diameter: 44, strokeWidth: 3 },
} as const

export function ConvictionGauge({ score, size = 'default', className = '' }: ConvictionGaugeProps) {
  const { diameter, strokeWidth } = SIZE_CONFIG[size]
  const radius = (diameter - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const progress = Math.max(0, Math.min(100, score)) / 100
  const strokeDashoffset = circumference * (1 - progress)
  const isCompact = size === 'compact'

  // Get color class based on score
  const colorClass = getConvictionColorClass(score)
  const fillClass = `conviction-gauge-fill conviction-gauge-fill--${colorClass.split('-')[1]}`

  // Compact size uses spec-defined colors and inline glow
  const compactColor = isCompact ? getCompactScoreColor(score) : undefined

  const sizeClass = size === 'mini' ? 'conviction-gauge--mini'
    : size === 'compact' ? 'conviction-gauge--compact'
    : ''

  return (
    <div
      className={`conviction-gauge ${sizeClass} ${className}`}
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
          style={isCompact ? { stroke: '#1A1F2E' } : undefined}
        />
        {/* Progress fill */}
        <circle
          className={isCompact ? undefined : fillClass}
          cx={diameter / 2}
          cy={diameter / 2}
          r={radius}
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          style={{
            transformOrigin: 'center',
            ...(isCompact ? {
              fill: 'none',
              stroke: compactColor,
              strokeWidth,
              strokeLinecap: 'round' as const,
              transform: 'rotate(-90deg)',
              filter: `drop-shadow(0 0 4px ${compactColor}40)`,
            } : {}),
          }}
        />
      </svg>
      <span
        className="conviction-gauge-value"
        style={{
          color: isCompact
            ? compactColor
            : score >= 85
              ? 'var(--color-conviction-high)'
              : score >= 75
                ? 'var(--color-conviction-medium)'
                : 'var(--color-conviction-low)',
          ...(isCompact ? { fontSize: '14px', fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" } : {}),
        }}
      >
        {Math.round(score)}
      </span>
    </div>
  )
}

export default ConvictionGauge
