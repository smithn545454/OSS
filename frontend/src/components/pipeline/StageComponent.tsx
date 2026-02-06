/**
 * Stage Component
 * 
 * Per spec section 5.6: Individual pipeline stage with flow bar and gates
 */

import { AlertTriangle } from 'lucide-react'
import clsx from 'clsx'
import type { DisplayStage } from '@/lib/types'
import { GateComponent } from './GateComponent'
import { VerdictBreakdown } from './VerdictBreakdown'

interface StageComponentProps {
  stage: DisplayStage
  isLast: boolean
  expandedGates: Set<string>
  expandedOverlaps: Set<string>
  onGateToggle: (gateId: string) => void
  onOverlapToggle: (gateId: string) => void
}

function formatNumber(n: number): string {
  return n.toLocaleString()
}

function calculatePassRate(input: number, output: number): string {
  if (input === 0) return '0.0'
  return ((output / input) * 100).toFixed(1)
}

export function StageComponent({
  stage,
  isLast,
  expandedGates,
  expandedOverlaps,
  onGateToggle,
  onOverlapToggle,
}: StageComponentProps) {
  const hasAnomaly = stage.status === 'anomaly'
  const passRate = calculatePassRate(stage.input, stage.output)
  const flowWidth = stage.input > 0 
    ? Math.max((stage.output / stage.input) * 100, 0.5) 
    : 0

  return (
    <div>
      {/* Stage Card */}
      <div className="space-y-3">
        {/* Header Row */}
        <div className="flex items-center gap-3">
          {/* Stage Number Badge */}
          <div
            className={clsx(
              'w-7 h-7 rounded-full flex items-center justify-center text-[12px] font-semibold',
              hasAnomaly 
                ? 'bg-[var(--color-error-bg)] border border-[var(--border-anomaly)] text-[var(--color-error-text)]'
                : 'bg-[rgba(6,182,212,0.15)] border border-[rgba(6,182,212,0.3)] text-[var(--accent-primary)]'
            )}
          >
            {stage.id}
          </div>

          {/* Stage Title */}
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <span 
                className="text-[14px] font-semibold"
                style={{ color: 'var(--text-primary)' }}
              >
                {stage.name}
              </span>
              
              {/* Anomaly Banner */}
              {hasAnomaly && stage.anomaly_message && (
                <span 
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium"
                  style={{
                    background: 'var(--color-error-bg)',
                    color: 'var(--color-error-text)',
                  }}
                >
                  <AlertTriangle className="w-3 h-3" />
                  {stage.anomaly_message}
                </span>
              )}
            </div>
            <p 
              className="text-[11px]"
              style={{ color: 'var(--text-disabled)' }}
            >
              {stage.description}
            </p>
          </div>

          {/* Stage Metrics */}
          <div className="flex gap-6 text-[12px]">
            <span>
              <span style={{ color: 'var(--text-disabled)' }}>In: </span>
              <span 
                className="font-medium"
                style={{ color: 'var(--text-secondary)' }}
              >
                {formatNumber(stage.input)}
              </span>
            </span>
            <span>
              <span style={{ color: 'var(--text-disabled)' }}>Out: </span>
              <span 
                className="font-medium"
                style={{ color: hasAnomaly ? 'var(--color-error-text)' : 'var(--accent-primary)' }}
              >
                {formatNumber(stage.output)}
              </span>
            </span>
            <span>
              <span style={{ color: 'var(--text-disabled)' }}>Pass: </span>
              <span 
                className="font-medium"
                style={{ color: hasAnomaly ? 'var(--color-error-text)' : 'var(--color-success-text)' }}
              >
                {passRate}%
              </span>
            </span>
          </div>
        </div>

        {/* Flow Bar */}
        <div className="stage-flow-bar">
          <div 
            className={clsx('stage-flow-bar-fill', hasAnomaly && 'anomaly')}
            style={{ width: `${flowWidth}%` }}
          />
        </div>

        {/* Gates Container (if present) */}
        {stage.gates && stage.gates.length > 0 && (
          <div className="flex flex-col gap-1.5 ml-10">
            {stage.gates.map(gate => (
              <GateComponent
                key={gate.id}
                gate={gate}
                isExpanded={expandedGates.has(gate.id)}
                isOverlapExpanded={expandedOverlaps.has(gate.id)}
                onToggle={() => onGateToggle(gate.id)}
                onOverlapToggle={() => onOverlapToggle(gate.id)}
              />
            ))}
          </div>
        )}

        {/* Verdict Breakdown (final stage only) */}
        {stage.breakdown && (
          <div className="ml-10">
            <VerdictBreakdown breakdown={stage.breakdown} />
          </div>
        )}
      </div>

      {/* Connector Line (except for last stage) */}
      {!isLast && <div className="stage-connector" />}
    </div>
  )
}
