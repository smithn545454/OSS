/**
 * AI Advisor Tab — Phase 4 of the Backtesting Engine
 *
 * Displays:
 * - Go-Live Readiness Grid (pass/fail criteria)
 * - AI-generated Insights (pattern detection, regime analysis, etc.)
 */

import { useState } from 'react'
import {
  ShieldCheck,
  ShieldAlert,
  ShieldX,
  ChevronDown,
  AlertCircle,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Brain,
} from 'lucide-react'
import {
  useBacktestRuns,
  useReadinessAssessment,
  useBacktestInsights,
} from '@/hooks/useApi'
import type {
  BacktestRun,
  ReadinessAssessment,
  ReadinessCheck,
  BacktestInsight,
} from '@/lib/types'

// ============================================================================
// Readiness Grid
// ============================================================================

function ReadinessGrid({ readiness }: { readiness: ReadinessAssessment }) {
  const verdictConfig = {
    READY: {
      icon: ShieldCheck,
      color: 'text-green-400',
      bg: 'bg-green-500/10 border-green-500/30',
      label: 'Ready for Live Trading',
    },
    CONDITIONAL: {
      icon: ShieldAlert,
      color: 'text-yellow-400',
      bg: 'bg-yellow-500/10 border-yellow-500/30',
      label: 'Conditional — Review Warnings',
    },
    NOT_READY: {
      icon: ShieldX,
      color: 'text-red-400',
      bg: 'bg-red-500/10 border-red-500/30',
      label: 'Not Ready — Needs Improvement',
    },
  }

  const config = verdictConfig[readiness.verdict]
  const VerdictIcon = config.icon

  return (
    <div className="space-y-4">
      {/* Verdict Banner */}
      <div className={`rounded-lg border p-4 ${config.bg}`}>
        <div className="flex items-center gap-3">
          <VerdictIcon className={`w-8 h-8 ${config.color}`} />
          <div>
            <h3 className={`text-lg font-semibold ${config.color}`}>
              {config.label}
            </h3>
            <p className="text-sm text-gray-400 mt-1">{readiness.message}</p>
          </div>
          <div className="ml-auto text-right">
            <span className={`text-2xl font-bold font-mono ${config.color}`}>
              {readiness.passed_count}/{readiness.total_checks}
            </span>
            <p className="text-xs text-gray-500">criteria passed</p>
          </div>
        </div>
      </div>

      {/* Criteria Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
        {readiness.checks.map((check, idx) => (
          <CriterionCard key={idx} check={check} />
        ))}
      </div>
    </div>
  )
}

function CriterionCard({ check }: { check: ReadinessCheck }) {
  const isPassed = check.passed
  const isCritical = check.severity === 'critical'

  return (
    <div
      className={`rounded-lg border p-3 ${
        isPassed
          ? 'bg-gray-800 border-green-600/30'
          : isCritical
            ? 'bg-gray-800 border-red-600/30'
            : 'bg-gray-800 border-yellow-600/30'
      }`}
    >
      <div className="flex items-start justify-between mb-2">
        <span className="text-xs font-medium text-gray-300">
          {check.criterion}
        </span>
        {isPassed ? (
          <CheckCircle2 className="w-4 h-4 text-green-400 flex-shrink-0" />
        ) : isCritical ? (
          <XCircle className="w-4 h-4 text-red-400 flex-shrink-0" />
        ) : (
          <AlertTriangle className="w-4 h-4 text-yellow-400 flex-shrink-0" />
        )}
      </div>
      <p className="text-xs text-gray-500">
        Target: {check.threshold}
      </p>
      <p
        className={`text-sm font-mono mt-1 ${
          isPassed ? 'text-green-400' : isCritical ? 'text-red-400' : 'text-yellow-400'
        }`}
      >
        {check.actual != null ? String(check.actual) : 'N/A'}
      </p>
    </div>
  )
}

// ============================================================================
// Insights List
// ============================================================================

function InsightsList({ insights }: { insights: BacktestInsight[] }) {
  if (!insights.length) {
    return (
      <div className="text-center py-8 text-gray-500">
        <Brain className="w-8 h-8 mx-auto mb-2 opacity-50" />
        <p className="text-sm">No AI insights generated yet.</p>
        <p className="text-xs mt-1 text-gray-600">
          Insights will appear after AI analysis is triggered on a completed run.
        </p>
      </div>
    )
  }

  const severityColors: Record<string, string> = {
    critical: 'border-red-600/40 bg-red-500/5',
    warning: 'border-yellow-600/40 bg-yellow-500/5',
    info: 'border-blue-600/40 bg-blue-500/5',
  }

  const severityIcons: Record<string, string> = {
    critical: 'text-red-400',
    warning: 'text-yellow-400',
    info: 'text-blue-400',
  }

  return (
    <div className="space-y-3">
      {insights.map((insight) => (
        <div
          key={insight.insight_id}
          className={`rounded-lg border p-4 ${severityColors[insight.severity] || severityColors.info}`}
        >
          <div className="flex items-start gap-3">
            <AlertCircle
              className={`w-5 h-5 mt-0.5 flex-shrink-0 ${severityIcons[insight.severity] || severityIcons.info}`}
            />
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <h4 className="text-sm font-medium text-gray-200">
                  {insight.title}
                </h4>
                <span className="text-[10px] uppercase tracking-wider text-gray-500 bg-gray-700/50 px-1.5 py-0.5 rounded">
                  {insight.insight_type}
                </span>
                {insight.confidence && (
                  <span className="text-[10px] text-gray-500">
                    {(insight.confidence * 100).toFixed(0)}% confidence
                  </span>
                )}
              </div>
              <p className="text-sm text-gray-400 mt-1">{insight.summary}</p>
              {insight.recommended_action && (
                <p className="text-xs text-cyan-400 mt-2">
                  Recommendation: {insight.recommended_action}
                </p>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

// ============================================================================
// Run Selector (shared with Results)
// ============================================================================

function RunSelector({
  selectedRunId,
  onSelect,
}: {
  selectedRunId: string | null
  onSelect: (runId: string) => void
}) {
  const { data: runsData } = useBacktestRuns()
  const completedRuns = (runsData?.runs || []).filter(
    (r: BacktestRun) => r.status === 'COMPLETED'
  )

  if (!completedRuns.length) {
    return (
      <div className="flex items-center gap-2 text-gray-500 text-sm">
        <AlertCircle className="w-4 h-4" />
        <span>No completed runs available.</span>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm text-gray-400">Select Run:</label>
      <div className="relative">
        <select
          value={selectedRunId || ''}
          onChange={(e) => onSelect(e.target.value)}
          className="bg-gray-800 text-gray-200 border border-gray-600 rounded-lg px-3 py-2 pr-8 text-sm appearance-none cursor-pointer hover:border-gray-500 focus:border-cyan-500 focus:outline-none"
        >
          <option value="">Choose a run...</option>
          {completedRuns.map((run: BacktestRun) => (
            <option key={run.run_id} value={run.run_id}>
              {run.config && typeof run.config === 'object' && 'name' in run.config
                ? String(run.config.name)
                : run.run_id.slice(0, 8)}{' '}
              ({run.summary
                ? `${(run.summary as Record<string, number>).total_trades || 0} trades`
                : 'no summary'})
            </option>
          ))}
        </select>
        <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
      </div>
    </div>
  )
}

// ============================================================================
// Main AI Advisor Tab
// ============================================================================

export default function AIAdvisorTab() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const { data: readinessData, isLoading: readinessLoading } =
    useReadinessAssessment(selectedRunId || '')
  const { data: insightsData, isLoading: insightsLoading } =
    useBacktestInsights(selectedRunId || '')

  const isLoading = readinessLoading || insightsLoading

  return (
    <div className="space-y-6">
      {/* Run Selector */}
      <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
        <RunSelector selectedRunId={selectedRunId} onSelect={setSelectedRunId} />
      </div>

      {!selectedRunId && (
        <div className="text-center py-16 text-gray-500">
          <Brain className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="text-lg">Select a completed run for AI analysis</p>
          <p className="text-sm mt-1">
            The advisor will evaluate readiness criteria and provide insights
          </p>
        </div>
      )}

      {selectedRunId && isLoading && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-cyan-400" />
          <span className="ml-2 text-gray-400">Analyzing results...</span>
        </div>
      )}

      {selectedRunId && !isLoading && (
        <>
          {/* Readiness Assessment */}
          {readinessData?.readiness && (
            <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
              <div className="flex items-center gap-2 mb-4">
                <ShieldCheck className="w-5 h-5 text-cyan-400" />
                <h2 className="text-lg font-semibold text-white">
                  Go-Live Readiness
                </h2>
              </div>
              <ReadinessGrid readiness={readinessData.readiness} />
            </div>
          )}

          {/* AI Insights */}
          <div className="bg-gray-900 rounded-lg border border-gray-700 p-4">
            <div className="flex items-center gap-2 mb-4">
              <Brain className="w-5 h-5 text-cyan-400" />
              <h2 className="text-lg font-semibold text-white">
                AI Insights
              </h2>
            </div>
            <InsightsList insights={insightsData?.insights || []} />
          </div>
        </>
      )}
    </div>
  )
}
