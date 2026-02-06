/**
 * Gate Component
 * 
 * Per spec section 5.7: Collapsible gate with rule breakdown
 */

import { ChevronDown, CheckCircle, XCircle } from 'lucide-react'
import clsx from 'clsx'
import type { DisplayGate } from '@/lib/types'
import { RuleBreakdown } from './RuleBreakdown'
import { MultiFailureOverlap } from './MultiFailureOverlap'

interface GateComponentProps {
  gate: DisplayGate
  isExpanded: boolean
  isOverlapExpanded: boolean
  onToggle: () => void
  onOverlapToggle: () => void
}

function formatNumber(n: number): string {
  return n.toLocaleString()
}

function calculatePassRate(passed: number, failed: number): string {
  const total = passed + failed
  if (total === 0) return '0.0'
  return ((passed / total) * 100).toFixed(1)
}

export function GateComponent({
  gate,
  isExpanded,
  isOverlapExpanded,
  onToggle,
  onOverlapToggle,
}: GateComponentProps) {
  const passRate = calculatePassRate(gate.passed, gate.failed)

  return (
    <div>
      {/* Gate Header */}
      <button
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onToggle()
          }
          if (e.key === 'Escape' && isExpanded) {
            onToggle()
          }
        }}
        className={clsx(
          'gate-header',
          isExpanded && 'expanded'
        )}
        aria-expanded={isExpanded}
        aria-controls={`gate-${gate.id}-content`}
      >
        {/* Left Content */}
        <div className="flex items-center gap-2.5">
          <ChevronDown 
            className={clsx(
              'w-4 h-4 transition-transform duration-200',
              isExpanded && 'rotate-180'
            )}
            style={{ color: 'var(--text-muted)' }}
          />
          <span 
            className="text-[12px] font-medium"
            style={{ color: 'var(--text-secondary)' }}
          >
            {gate.name}
          </span>
        </div>

        {/* Right Content - Stats */}
        <div className="flex items-center gap-4 text-[11px]">
          {/* Pass Count */}
          <span className="flex items-center gap-1" style={{ color: 'var(--color-success-text)' }}>
            <CheckCircle className="w-3.5 h-3.5" style={{ stroke: 'var(--color-success)' }} />
            {formatNumber(gate.passed)}
          </span>

          {/* Fail Count */}
          <span className="flex items-center gap-1" style={{ color: 'var(--color-error-text)' }}>
            <XCircle className="w-3.5 h-3.5" style={{ stroke: 'var(--color-error)' }} />
            {formatNumber(gate.failed)}
          </span>

          {/* Pass Rate */}
          <span style={{ color: 'var(--text-disabled)' }}>
            ({passRate}%)
          </span>
        </div>
      </button>

      {/* Gate Detail Panel */}
      {isExpanded && (
        <div
          id={`gate-${gate.id}-content`}
          role="region"
          aria-labelledby={`gate-${gate.id}-header`}
          className="gate-detail"
        >
          {/* Rule Breakdown */}
          <RuleBreakdown rules={gate.rules} />

          {/* Multi-Failure Overlap Toggle */}
          {gate.overlaps && gate.overlaps.length > 0 && (
            <MultiFailureOverlap
              overlaps={gate.overlaps}
              isExpanded={isOverlapExpanded}
              onToggle={onOverlapToggle}
            />
          )}
        </div>
      )}
    </div>
  )
}
