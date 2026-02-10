/**
 * Tab 4: AI Strategy Advisor
 * Shows AI-generated insights with actionable recommendations.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { Sparkles, AlertTriangle, TrendingUp, Shield, Target, ChevronDown, ChevronRight } from 'lucide-react'
import { useAIInsights, usePaperTradingMetrics } from '@/hooks/useApi'
import type { AIInsight, InsightCategory, InsightSeverity } from '@/lib/types'

const CATEGORY_ICONS: Record<InsightCategory, typeof Sparkles> = {
  GATE_TUNING: Target,
  EXIT_STRATEGY: TrendingUp,
  TIER_QUALITY: Shield,
  SCORE_OPTIMIZATION: Sparkles,
  RISK_MANAGEMENT: AlertTriangle,
}

const CATEGORY_LABELS: Record<InsightCategory, string> = {
  GATE_TUNING: 'Gate Tuning',
  EXIT_STRATEGY: 'Exit Strategy',
  TIER_QUALITY: 'Tier Quality',
  SCORE_OPTIMIZATION: 'Score Optimization',
  RISK_MANAGEMENT: 'Risk Management',
}

const SEVERITY_COLORS: Record<InsightSeverity, string> = {
  HIGH: 'bg-oss-reject/20 text-oss-reject border-oss-reject/30',
  MEDIUM: 'bg-oss-watch/20 text-oss-watch border-oss-watch/30',
  LOW: 'bg-oss-accent/20 text-oss-accent border-oss-accent/30',
}

export default function AIStrategyAdvisor() {
  const navigate = useNavigate()
  const { data: insightsData, isLoading, refetch } = useAIInsights()
  const { data: metricsData } = usePaperTradingMetrics()
  const [expandedIdx, setExpandedIdx] = useState<number>(0)
  const [dismissed, setDismissed] = useState<Set<number>>(new Set())

  const insights = insightsData?.insights ?? []
  const metrics = metricsData?.metrics

  // Calculate improvement potential
  const improvementPotential = insights
    .filter((_, i) => !dismissed.has(i))
    .reduce((sum, insight) => {
      const impact = parseFloat(insight.estimated_impact) || 0
      return sum + Math.abs(impact)
    }, 0)

  // Projection cards
  const currentWinRate = metrics?.win_rate ?? 0
  const currentAvgScore =
    metrics?.approve_performance?.avg_score ?? metrics?.expectancy ?? 0
  const falsePositiveRate =
    metrics?.loss_rate ?? (metrics?.win_rate != null ? 100 - metrics.win_rate : 0)

  const projectedWinRate = Math.min(currentWinRate + improvementPotential * 0.3, 100)
  const projectedAvgScore = currentAvgScore + improvementPotential * 0.2
  const projectedFP = Math.max(falsePositiveRate - improvementPotential * 0.2, 0)

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="rounded-lg bg-gradient-to-r from-oss-accent/10 via-oss-accent/5 to-transparent border border-oss-accent/20 p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-oss-accent/20">
              <Sparkles className="h-5 w-5 text-oss-accent" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-oss-text">AI Strategy Advisor</h3>
              <p className="text-xs text-oss-muted">
                {insightsData?.data_summary?.positions_analyzed ?? 0} positions analyzed
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <div className="text-xs text-oss-muted">Improvement Potential</div>
              <div className="text-lg font-semibold text-oss-accent">
                +{improvementPotential.toFixed(1)}%
              </div>
            </div>
            <button
              onClick={() => refetch()}
              disabled={isLoading}
              className="rounded-lg border border-oss-accent/30 bg-oss-accent/10 px-3 py-2 text-xs font-medium text-oss-accent hover:bg-oss-accent/20 transition-colors disabled:opacity-50"
            >
              {isLoading ? 'Analyzing...' : 'Re-analyze'}
            </button>
          </div>
        </div>
      </div>

      {/* Insight Cards */}
      {isLoading && insights.length === 0 ? (
        <div className="rounded-lg border border-oss-border bg-oss-surface p-8 text-center text-oss-muted text-sm">
          <Sparkles className="h-6 w-6 mx-auto mb-2 animate-pulse text-oss-accent" />
          Analyzing trading performance...
        </div>
      ) : insights.length === 0 ? (
        <div className="rounded-lg border border-oss-border bg-oss-surface p-8 text-center text-oss-muted text-sm">
          No insights available. Run more trades or trigger a re-analysis.
        </div>
      ) : (
        <div className="space-y-3">
          {insights.map((insight, idx) => {
            if (dismissed.has(idx)) return null
            const isExpanded = expandedIdx === idx
            const Icon = CATEGORY_ICONS[insight.category] ?? Sparkles

            return (
              <InsightCard
                key={idx}
                insight={insight}
                icon={Icon}
                isExpanded={isExpanded}
                onToggle={() => setExpandedIdx(isExpanded ? -1 : idx)}
                onDismiss={() => setDismissed((prev) => new Set(prev).add(idx))}
                onApplyToPolicy={() => navigate('/config')}
              />
            )
          })}
        </div>
      )}

      {/* Optimization Roadmap */}
      <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
        <h3 className="text-sm font-medium text-oss-text mb-4">Optimization Roadmap</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <ProjectionCard
            label="APPROVE Win Rate"
            current={currentWinRate}
            projected={projectedWinRate}
            suffix="%"
            positive
          />
          <ProjectionCard
            label="Avg Score"
            current={currentAvgScore}
            projected={projectedAvgScore}
            suffix=""
            positive
          />
          <ProjectionCard
            label="False Positive Rate"
            current={falsePositiveRate}
            projected={projectedFP}
            suffix="%"
            positive={false}
          />
        </div>
        <p className="text-xs text-oss-muted mt-3 italic">
          Values are projections based on insight analysis, not guarantees.
        </p>
      </div>
    </div>
  )
}

function InsightCard({
  insight,
  icon: Icon,
  isExpanded,
  onToggle,
  onDismiss,
  onApplyToPolicy,
}: {
  insight: AIInsight
  icon: typeof Sparkles
  isExpanded: boolean
  onToggle: () => void
  onDismiss: () => void
  onApplyToPolicy: () => void
}) {
  return (
    <div className="rounded-lg border border-oss-border bg-oss-surface overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 p-4 text-left hover:bg-oss-border/20 transition-colors"
      >
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-oss-accent/10">
          <Icon className="h-4 w-4 text-oss-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xs font-medium text-oss-text">{insight.title}</span>
            <span
              className={clsx(
                'inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium border',
                SEVERITY_COLORS[insight.severity]
              )}
            >
              {insight.severity}
            </span>
            <span className="text-[10px] text-oss-muted rounded border border-oss-border px-1.5 py-0.5">
              {CATEGORY_LABELS[insight.category] ?? insight.category}
            </span>
          </div>
          <p className="text-xs text-oss-muted truncate">{insight.description}</p>
        </div>
        {isExpanded ? (
          <ChevronDown className="h-4 w-4 text-oss-muted shrink-0" />
        ) : (
          <ChevronRight className="h-4 w-4 text-oss-muted shrink-0" />
        )}
      </button>

      {isExpanded && (
        <div className="border-t border-oss-border p-4 space-y-3">
          <p className="text-xs text-oss-text leading-relaxed">{insight.description}</p>

          {Object.keys(insight.data_points).length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {Object.entries(insight.data_points).map(([key, value]) => (
                <div key={key} className="rounded border border-oss-border p-2">
                  <div className="text-[10px] text-oss-muted uppercase">{key.replace(/_/g, ' ')}</div>
                  <div className="text-xs font-medium text-oss-text">{String(value)}</div>
                </div>
              ))}
            </div>
          )}

          <div className="rounded-lg bg-oss-accent/5 border border-oss-accent/20 p-3">
            <div className="text-[10px] text-oss-accent uppercase font-medium mb-1">
              Recommended Action
            </div>
            <p className="text-xs text-oss-text">{insight.suggested_action}</p>
          </div>

          <div className="rounded-lg bg-oss-approve/5 border border-oss-approve/20 p-3">
            <div className="text-[10px] text-oss-approve uppercase font-medium mb-1">
              Expected Impact
            </div>
            <p className="text-xs text-oss-text">{insight.estimated_impact}</p>
          </div>

          <div className="flex gap-2">
            <button
              onClick={onApplyToPolicy}
              className="rounded-lg bg-oss-accent/10 border border-oss-accent/30 px-3 py-1.5 text-xs font-medium text-oss-accent hover:bg-oss-accent/20 transition-colors"
            >
              Apply to Policy
            </button>
            <button
              onClick={onDismiss}
              className="rounded-lg border border-oss-border px-3 py-1.5 text-xs text-oss-muted hover:bg-oss-border hover:text-oss-text transition-colors"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function ProjectionCard({
  label,
  current,
  projected,
  suffix,
  positive,
}: {
  label: string
  current: number
  projected: number
  suffix: string
  positive: boolean
}) {
  const improved = positive ? projected > current : projected < current
  return (
    <div className="rounded-lg border border-oss-border p-3">
      <div className="text-xs text-oss-muted mb-2">{label}</div>
      <div className="flex items-end gap-2">
        <div>
          <div className="text-[10px] text-oss-muted uppercase">Current</div>
          <div className="text-sm font-medium text-oss-text">
            {current.toFixed(1)}{suffix}
          </div>
        </div>
        <div className="text-oss-muted text-xs pb-0.5">&rarr;</div>
        <div>
          <div className="text-[10px] text-oss-muted uppercase">Projected</div>
          <div
            className={clsx('text-sm font-medium', {
              'text-oss-approve': improved,
              'text-oss-text': !improved,
            })}
          >
            {projected.toFixed(1)}{suffix}
          </div>
        </div>
      </div>
    </div>
  )
}
