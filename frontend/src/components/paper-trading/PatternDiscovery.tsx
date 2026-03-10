/**
 * Pattern Discovery tab — AI-powered archetype identification + setup rules.
 * Sections: Run Analysis controls, Archetype cards, Saved Setup Rules.
 */

import { useState } from 'react'
import clsx from 'clsx'
import {
  Brain,
  Play,
  Clock,
  ChevronDown,
  ChevronRight,
  ToggleLeft,
  ToggleRight,
  Trash2,
  BookmarkPlus,
} from 'lucide-react'
import {
  useRunPatternDiscovery,
  usePatternAnalyses,
  useSetupRules,
  useCreateSetupRule,
  useToggleSetupRule,
  useDeleteSetupRule,
} from '@/hooks/useApi'
import type { ArchetypeResult, PatternAnalysis, SetupRule } from '@/lib/types'

function fmt(val: number | null | undefined, decimals: number = 1): string {
  if (val == null) return '--'
  return val.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function confidenceColor(confidence: string): string {
  switch (confidence) {
    case 'VERY_HIGH':
      return 'bg-oss-approve/20 text-oss-approve border-oss-approve/30'
    case 'HIGH':
      return 'bg-oss-approve/10 text-oss-approve border-oss-approve/20'
    case 'MODERATE':
      return 'bg-yellow-400/10 text-yellow-400 border-yellow-400/20'
    case 'LOW':
      return 'bg-orange-400/10 text-orange-400 border-orange-400/20'
    default:
      return 'bg-oss-muted/10 text-oss-muted border-oss-border'
  }
}

function CriteriaDisplay({ criteria }: { criteria: Record<string, unknown> }) {
  const entries = Object.entries(criteria).filter(([, v]) => v != null)
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([key, val]) => (
        <span
          key={key}
          className="inline-block rounded bg-oss-bg px-2 py-0.5 text-[10px] font-mono text-oss-muted border border-oss-border"
        >
          {key}: {Array.isArray(val) ? val.join(', ') : String(val)}
        </span>
      ))}
    </div>
  )
}

interface ArchetypeCardProps {
  archetype: ArchetypeResult
  onPromote: () => void
}

function ArchetypeCard({ archetype, onPromote }: ArchetypeCardProps) {
  const [expanded, setExpanded] = useState(false)
  const perf = archetype.performance

  return (
    <div className="rounded-lg border border-oss-border bg-oss-surface overflow-hidden">
      {/* Card Header */}
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex items-start justify-between p-4 cursor-pointer hover:bg-oss-bg/30 transition-colors"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            {expanded ? (
              <ChevronDown className="h-4 w-4 text-oss-muted flex-shrink-0" />
            ) : (
              <ChevronRight className="h-4 w-4 text-oss-muted flex-shrink-0" />
            )}
            <h3 className="font-medium text-oss-text truncate">{archetype.name}</h3>
          </div>
          <div className="flex items-center gap-3 ml-6 text-sm">
            <span
              className={clsx(
                'inline-block rounded px-2 py-0.5 text-[10px] font-medium border',
                confidenceColor(archetype.confidence)
              )}
            >
              {archetype.confidence_label}
            </span>
            <span className="font-mono text-oss-approve">
              {fmt(perf.win_rate * 100)}% WR
            </span>
            <span className="font-mono text-oss-text">
              {fmt(perf.avg_return)}% avg
            </span>
            <span className="text-oss-muted">n={perf.sample_size}</span>
          </div>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation()
            onPromote()
          }}
          className="flex items-center gap-1 rounded px-2 py-1 text-xs text-oss-accent hover:bg-oss-accent/10 border border-oss-accent/20 transition-colors"
          title="Save as Setup Rule"
        >
          <BookmarkPlus className="h-3.5 w-3.5" />
          Save
        </button>
      </div>

      {/* Expanded Content */}
      {expanded && (
        <div className="border-t border-oss-border px-4 py-3 space-y-3">
          {/* Criteria */}
          <div>
            <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-1.5">
              Defining Criteria
            </h4>
            <CriteriaDisplay criteria={archetype.criteria} />
          </div>

          {/* Performance Grid */}
          <div>
            <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-1.5">
              Performance
            </h4>
            <div className="grid grid-cols-5 gap-3">
              <div className="text-center">
                <div className="text-xs text-oss-muted">Win Rate</div>
                <div className="font-mono text-sm text-oss-approve">
                  {fmt(perf.win_rate * 100)}%
                </div>
              </div>
              <div className="text-center">
                <div className="text-xs text-oss-muted">Avg Return</div>
                <div className="font-mono text-sm text-oss-text">{fmt(perf.avg_return)}%</div>
              </div>
              <div className="text-center">
                <div className="text-xs text-oss-muted">Median Return</div>
                <div className="font-mono text-sm text-oss-text">{fmt(perf.median_return)}%</div>
              </div>
              <div className="text-center">
                <div className="text-xs text-oss-muted">Sample Size</div>
                <div className="font-mono text-sm text-oss-text">{perf.sample_size}</div>
              </div>
              <div className="text-center">
                <div className="text-xs text-oss-muted">Avg Days Held</div>
                <div className="font-mono text-sm text-oss-text">{fmt(perf.avg_days_held)}</div>
              </div>
            </div>
          </div>

          {/* Reasoning */}
          {archetype.reasoning && (
            <div>
              <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-1">Reasoning</h4>
              <p className="text-sm text-oss-muted">{archetype.reasoning}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function SetupRuleRow({ rule }: { rule: SetupRule }) {
  const toggleMutation = useToggleSetupRule()
  const deleteMutation = useDeleteSetupRule()

  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-oss-border last:border-b-0">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm text-oss-text truncate">{rule.name}</span>
          <span
            className={clsx(
              'inline-block rounded px-1.5 py-0.5 text-[10px] font-medium border',
              rule.is_active
                ? 'bg-oss-approve/10 text-oss-approve border-oss-approve/20'
                : 'bg-oss-muted/10 text-oss-muted border-oss-border'
            )}
          >
            {rule.is_active ? 'Active' : 'Inactive'}
          </span>
        </div>
        <div className="flex items-center gap-3 mt-0.5 text-xs text-oss-muted">
          <span>
            WR: {fmt(rule.performance_at_creation?.win_rate ? rule.performance_at_creation.win_rate * 100 : null)}%
          </span>
          <span>n={rule.performance_at_creation?.sample_size ?? '--'}</span>
          <span>Created: {rule.created_at?.slice(0, 10)}</span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() =>
            toggleMutation.mutate({ ruleId: rule.rule_id, isActive: !rule.is_active })
          }
          className="p-1.5 rounded hover:bg-oss-bg text-oss-muted transition-colors"
          title={rule.is_active ? 'Deactivate' : 'Activate'}
        >
          {rule.is_active ? (
            <ToggleRight className="h-4 w-4 text-oss-approve" />
          ) : (
            <ToggleLeft className="h-4 w-4" />
          )}
        </button>
        <button
          onClick={() => deleteMutation.mutate(rule.rule_id)}
          className="p-1.5 rounded hover:bg-oss-reject/10 text-oss-muted hover:text-oss-reject transition-colors"
          title="Delete rule"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}

interface PatternDiscoveryProps {
  period: string
  verdict?: string
  scanner?: string
}

export default function PatternDiscovery({ period, verdict, scanner }: PatternDiscoveryProps) {
  const runAnalysis = useRunPatternDiscovery()
  const { data: analyses } = usePatternAnalyses()
  const { data: rules } = useSetupRules()
  const createRule = useCreateSetupRule()

  const [result, setResult] = useState<PatternAnalysis | null>(null)

  const handleRun = async () => {
    try {
      const data = await runAnalysis.mutateAsync({ period, verdict, scanner })
      setResult(data)
    } catch {
      // Error handled by mutation state
    }
  }

  const handlePromote = (archetype: ArchetypeResult, analysisId: string) => {
    createRule.mutate({
      name: archetype.name,
      criteria: archetype.criteria,
      source_analysis_id: analysisId,
      performance_at_creation: archetype.performance as unknown as Record<string, unknown>,
    })
  }

  const latestAnalysis = analyses?.[0]
  const displayResult = result ?? null

  return (
    <div className="space-y-6">
      {/* Analysis Controls */}
      <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Brain className="h-5 w-5 text-oss-accent" />
            <div>
              <h3 className="font-medium text-oss-text">Pattern Discovery</h3>
              <p className="text-xs text-oss-muted">
                AI identifies statistically significant trade archetypes from your history.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {latestAnalysis && (
              <span className="flex items-center gap-1 text-xs text-oss-muted">
                <Clock className="h-3 w-3" />
                Last: {latestAnalysis.created_at?.slice(0, 10)} ({latestAnalysis.archetype_count}{' '}
                archetypes)
              </span>
            )}
            <button
              onClick={handleRun}
              disabled={runAnalysis.isPending}
              className={clsx(
                'flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors',
                runAnalysis.isPending
                  ? 'bg-oss-accent/30 text-oss-accent cursor-wait'
                  : 'bg-oss-accent text-oss-bg hover:bg-oss-accent/90'
              )}
            >
              <Play className="h-4 w-4" />
              {runAnalysis.isPending ? 'Analyzing...' : 'Run Analysis'}
            </button>
          </div>
        </div>

        {runAnalysis.isError && (
          <div className="mt-3 text-xs text-oss-reject">
            Analysis failed: {runAnalysis.error?.message ?? 'Unknown error'}
          </div>
        )}
      </div>

      {/* Results */}
      {displayResult && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-oss-text">
              Discovered Archetypes ({displayResult.archetypes.length})
            </h3>
            <span className="text-xs text-oss-muted">
              {displayResult.positions_analyzed} trades analyzed
            </span>
          </div>

          {displayResult.status === 'insufficient_data' && (
            <div className="rounded-lg border border-yellow-400/20 bg-yellow-400/5 p-3 text-xs text-yellow-400">
              {displayResult.message}
            </div>
          )}

          {displayResult.archetypes.map((arch, i) => (
            <ArchetypeCard
              key={arch.name + i}
              archetype={arch}
              onPromote={() => handlePromote(arch, displayResult.analysis_id)}
            />
          ))}
        </div>
      )}

      {/* Saved Setup Rules */}
      {rules && rules.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-oss-text mb-2">
            Saved Setup Rules ({rules.length})
          </h3>
          <div className="rounded-lg border border-oss-border bg-oss-surface overflow-hidden">
            {rules.map((rule) => (
              <SetupRuleRow key={rule.rule_id} rule={rule} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
