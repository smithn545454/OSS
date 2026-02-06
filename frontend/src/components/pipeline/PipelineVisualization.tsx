/**
 * Pipeline Visualization Component
 * 
 * Per spec section 5.5: Main panel with header stats and stages
 * Always shows the pipeline structure, even with no data (zeros)
 */

import type { PipelineMonitorData, DisplayStage } from '@/lib/types'
import { StageComponent } from './StageComponent'

interface PipelineVisualizationProps {
  data: PipelineMonitorData | undefined
  isLoading: boolean
  expandedGates: Set<string>
  expandedOverlaps: Set<string>
  onGateToggle: (gateId: string) => void
  onOverlapToggle: (gateId: string) => void
}

// Default empty stages with gates to show when no data is available
const DEFAULT_STAGES: DisplayStage[] = [
  {
    id: 1,
    name: 'Discovery',
    description: 'Initial contract universe filtering',
    input: 0,
    output: 0,
    status: 'healthy',
    gates: [
      {
        id: 'liquidity_gate',
        name: 'Liquidity Gate',
        passed: 0,
        failed: 0,
        rules: [
          { name: 'Min Volume ≥ 100', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Open Interest ≥ 500', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Bid-Ask Spread ≤ 15%', passed: 0, failed: 0, severity: 'normal' },
        ],
        overlaps: [],
      },
      {
        id: 'basic_eligibility',
        name: 'Basic Eligibility',
        passed: 0,
        failed: 0,
        rules: [
          { name: 'DTE Range 7-120', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Strike within expected move', passed: 0, failed: 0, severity: 'normal' },
        ],
        overlaps: [],
      },
    ],
  },
  {
    id: 2,
    name: 'Initial Scoring',
    description: 'Directional & volatility signal assessment',
    input: 0,
    output: 0,
    status: 'healthy',
    gates: [
      {
        id: 'directional_confidence',
        name: 'Directional Confidence',
        passed: 0,
        failed: 0,
        rules: [
          { name: 'Trend Alignment Score ≥ 0.6', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Multi-Timeframe Confluence', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Recent Momentum Positive', passed: 0, failed: 0, severity: 'normal' },
        ],
        overlaps: [],
      },
      {
        id: 'volatility_assessment',
        name: 'Volatility Assessment',
        passed: 0,
        failed: 0,
        rules: [
          { name: 'IV Percentile ≤ 85%', passed: 0, failed: 0, severity: 'normal' },
          { name: 'IV/RV Ratio favorable', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Theta Burden ≤ 4%', passed: 0, failed: 0, severity: 'normal' },
        ],
        overlaps: [],
      },
    ],
  },
  {
    id: 3,
    name: 'Structure Analysis',
    description: 'Options-specific quality checks',
    input: 0,
    output: 0,
    status: 'healthy',
    gates: [
      {
        id: 'premium_quality',
        name: 'Premium Quality',
        passed: 0,
        failed: 0,
        rules: [
          { name: 'Time-Adjusted Feasibility ≤ 1.25', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Expected Move Coverage', passed: 0, failed: 0, severity: 'normal' },
        ],
        overlaps: [],
      },
      {
        id: 'risk_parameters',
        name: 'Risk Parameters',
        passed: 0,
        failed: 0,
        rules: [
          { name: 'Delta in valid range', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Greeks coherent', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Max loss acceptable', passed: 0, failed: 0, severity: 'normal' },
        ],
        overlaps: [],
      },
    ],
  },
  {
    id: 4,
    name: 'Final Scoring',
    description: 'Composite score calculation & ranking',
    input: 0,
    output: 0,
    status: 'healthy',
    gates: [
      {
        id: 'composite_threshold',
        name: 'Composite Threshold',
        passed: 0,
        failed: 0,
        rules: [
          { name: 'Combined Score ≥ 75', passed: 0, failed: 0, severity: 'normal' },
          { name: 'No Pillar Below 60', passed: 0, failed: 0, severity: 'normal' },
          { name: 'Confidence Interval Met', passed: 0, failed: 0, severity: 'normal' },
        ],
        overlaps: [],
      },
    ],
  },
  {
    id: 5,
    name: 'Output',
    description: 'Final verdict determination',
    input: 0,
    output: 0,
    status: 'healthy',
  },
]

function formatNumber(n: number): string {
  return n.toLocaleString()
}

function calculatePassRate(input: number, output: number): string {
  if (input === 0) return '0.0'
  return ((output / input) * 100).toFixed(1)
}

export function PipelineVisualization({
  data,
  isLoading,
  expandedGates,
  expandedOverlaps,
  onGateToggle,
  onOverlapToggle,
}: PipelineVisualizationProps) {
  if (isLoading) {
    return (
      <div 
        className="rounded-xl p-6 min-h-[600px]"
        style={{
          background: 'rgba(15, 23, 42, 0.3)',
          border: '1px solid var(--border-subtle)',
        }}
      >
        <div className="animate-pulse">
          <div className="flex gap-8 mb-8 pb-5 border-b border-[var(--border-subtle)]">
            {[1, 2, 3].map(i => (
              <div key={i}>
                <div className="h-3 w-20 bg-[var(--bg-hover)] rounded mb-2" />
                <div className="h-8 w-24 bg-[var(--bg-hover)] rounded" />
              </div>
            ))}
          </div>
          {[1, 2, 3, 4, 5].map(i => (
            <div key={i} className="mb-4">
              <div className="h-24 bg-[var(--bg-hover)] rounded-lg" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  // Use data if available, otherwise use default empty stages
  const stages = data?.stages ?? DEFAULT_STAGES
  const totalInput = data?.total_input ?? 0
  const finalStage = stages[stages.length - 1]
  const finalOutput = finalStage?.output ?? 0
  const passRate = calculatePassRate(totalInput, finalOutput)

  return (
    <div 
      className="rounded-xl p-6 min-h-[600px]"
      style={{
        background: 'rgba(15, 23, 42, 0.3)',
        border: '1px solid var(--border-subtle)',
      }}
    >
      {/* Header Stats Row */}
      <div 
        className="flex gap-8 mb-8 pb-5"
        style={{ borderBottom: '1px solid var(--border-subtle)' }}
      >
        {/* Total Input */}
        <div>
          <p 
            className="text-[11px] uppercase tracking-wide mb-1"
            style={{ color: 'var(--text-disabled)' }}
          >
            Total Input
          </p>
          <p 
            className="text-[24px] font-semibold"
            style={{ color: 'var(--text-primary)' }}
          >
            {formatNumber(totalInput)}
          </p>
        </div>

        {/* Final Output */}
        <div>
          <p 
            className="text-[11px] uppercase tracking-wide mb-1"
            style={{ color: 'var(--text-disabled)' }}
          >
            Final Output
          </p>
          <p 
            className="text-[24px] font-semibold"
            style={{ color: 'var(--accent-primary)' }}
          >
            {formatNumber(finalOutput)}
          </p>
        </div>

        {/* Overall Pass Rate */}
        <div>
          <p 
            className="text-[11px] uppercase tracking-wide mb-1"
            style={{ color: 'var(--text-disabled)' }}
          >
            Overall Pass Rate
          </p>
          <p 
            className="text-[24px] font-semibold"
            style={{ color: 'var(--color-success-text)' }}
          >
            {passRate}%
          </p>
        </div>
      </div>

      {/* Stages Container */}
      <div className="flex flex-col gap-4">
        {stages.map((stage, index) => (
          <StageComponent
            key={stage.id}
            stage={stage}
            isLast={index === stages.length - 1}
            expandedGates={expandedGates}
            expandedOverlaps={expandedOverlaps}
            onGateToggle={onGateToggle}
            onOverlapToggle={onOverlapToggle}
          />
        ))}
      </div>
    </div>
  )
}
