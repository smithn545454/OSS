/**
 * Pattern Discovery tab — AI-powered archetype identification + setup rules.
 * Sections: Run Analysis controls, Archetype cards, Saved Setup Rules.
 */

import { useState, useCallback, useEffect } from 'react'
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
  Check,
  Loader2,
  Radio,
  FlaskConical,
  Plus,
  Crosshair,
} from 'lucide-react'
import {
  useRunPatternDiscovery,
  usePatternAnalyses,
  usePatternAnalysis,
  useSetupRules,
  useCreateSetupRule,
  useToggleSetupRule,
  useDeleteSetupRule,
  useSetupRulePerformance,
} from '@/hooks/useApi'
import type { ArchetypeResult, PatternAnalysis, SetupRule } from '@/lib/types'
import ManualRuleForm from './ManualRuleForm'

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

function ModeBadge({ mode }: { mode: 'production' | 'test' }) {
  return mode === 'production' ? (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium bg-purple-400/15 text-purple-400 border border-purple-400/25">
      <Radio className="h-2.5 w-2.5" />
      Production
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium bg-oss-muted/10 text-oss-muted border border-oss-border">
      <FlaskConical className="h-2.5 w-2.5" />
      Test
    </span>
  )
}

interface ArchetypeCardProps {
  archetype: ArchetypeResult
  onPromote: () => void
  isSaving: boolean
  isSaved: boolean
}

function ArchetypeCard({ archetype, onPromote, isSaving, isSaved }: ArchetypeCardProps) {
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
            if (!isSaved && !isSaving) onPromote()
          }}
          disabled={isSaving || isSaved}
          className={clsx(
            'flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors border',
            isSaved
              ? 'text-oss-approve border-oss-approve/20 bg-oss-approve/10 cursor-default'
              : isSaving
                ? 'text-oss-accent border-oss-accent/20 bg-oss-accent/10 cursor-wait'
                : 'text-oss-accent hover:bg-oss-accent/10 border-oss-accent/20'
          )}
          title={isSaved ? 'Saved as Setup Rule' : 'Save as Setup Rule'}
        >
          {isSaved ? (
            <Check className="h-3.5 w-3.5" />
          ) : isSaving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <BookmarkPlus className="h-3.5 w-3.5" />
          )}
          {isSaved ? 'Saved' : isSaving ? 'Saving...' : 'Save'}
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

function PerformanceComparison({ ruleId, creation }: {
  ruleId: string
  creation: { win_rate?: number; avg_return?: number; sample_size?: number; avg_days_held?: number } | null
}) {
  const { data, isLoading } = useSetupRulePerformance(ruleId, true)

  if (isLoading) {
    return <div className="text-xs text-oss-muted py-2">Loading performance data...</div>
  }

  const current = data?.performance
  if (!current || data.sample_size === 0) {
    return (
      <div className="text-xs text-oss-muted py-2">
        No matched positions yet. Performance tracking begins on the next pipeline run.
      </div>
    )
  }

  const hasCreationData = creation && (creation.win_rate != null || creation.avg_return != null)

  const rows = [
    { label: 'Win Rate', creation: creation?.win_rate != null ? creation.win_rate * 100 : null, current: current.win_rate * 100, suffix: '%' },
    { label: 'Avg Return', creation: creation?.avg_return ?? null, current: current.avg_return, suffix: '%' },
    { label: 'Sample Size', creation: creation?.sample_size ?? null, current: current.sample_size, suffix: '' },
    { label: 'Avg Days Held', creation: creation?.avg_days_held ?? null, current: current.avg_days_held, suffix: '' },
  ]

  if (!hasCreationData) {
    return (
      <div className="grid grid-cols-[1fr_80px] gap-x-4 gap-y-1 text-xs">
        <div className="text-oss-muted font-medium" />
        <div className="text-oss-muted font-medium text-right">Current</div>
        {rows.map(({ label, current: cur, suffix }) => (
          <div key={label} className="contents">
            <div className="text-oss-muted">{label}</div>
            <div className="font-mono text-oss-text text-right">
              {fmt(cur)}{suffix}
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-[1fr_80px_80px] gap-x-4 gap-y-1 text-xs">
      <div className="text-oss-muted font-medium" />
      <div className="text-oss-muted font-medium text-right">At Creation</div>
      <div className="text-oss-muted font-medium text-right">Current</div>
      {rows.map(({ label, creation: c, current: cur, suffix }) => (
        <div key={label} className="contents">
          <div className="text-oss-muted">{label}</div>
          <div className="font-mono text-oss-text text-right">
            {c != null ? `${fmt(c)}${suffix}` : '--'}
          </div>
          <div className={clsx(
            'font-mono text-right',
            c != null && cur > c ? 'text-oss-approve' : c != null && cur < c ? 'text-oss-reject' : 'text-oss-text'
          )}>
            {fmt(cur)}{suffix}
          </div>
        </div>
      ))}
    </div>
  )
}

function SetupRuleRow({ rule }: { rule: SetupRule }) {
  const toggleMutation = useToggleSetupRule()
  const deleteMutation = useDeleteSetupRule()
  const [expanded, setExpanded] = useState(false)

  const mode = rule.mode ?? 'production'

  return (
    <div className="border-b border-oss-border last:border-b-0">
      <div className="flex items-center justify-between px-4 py-3">
        <div
          className="flex-1 min-w-0 cursor-pointer"
          onClick={() => setExpanded(!expanded)}
        >
          <div className="flex items-center gap-2">
            {expanded ? (
              <ChevronDown className="h-3 w-3 text-oss-muted flex-shrink-0" />
            ) : (
              <ChevronRight className="h-3 w-3 text-oss-muted flex-shrink-0" />
            )}
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
            {(rule.source ?? 'ai') === 'manual' && (
              <span className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-medium bg-oss-accent/10 text-oss-accent border border-oss-accent/20">
                <Crosshair className="h-2.5 w-2.5" />
                Manual
              </span>
            )}
            <ModeBadge mode={mode} />
          </div>
          <div className="flex items-center gap-3 mt-0.5 ml-5 text-xs text-oss-muted">
            {rule.performance_at_creation && rule.performance_at_creation.win_rate != null ? (
              <>
                <span>
                  WR: {fmt(rule.performance_at_creation.win_rate * 100)}%
                </span>
                <span>
                  Avg: {fmt(rule.performance_at_creation.avg_return)}%
                </span>
                <span>n={rule.performance_at_creation.sample_size ?? '--'}</span>
              </>
            ) : (
              <span className="italic">No historical data</span>
            )}
            <span>Created: {rule.created_at?.slice(0, 10)}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() =>
              toggleMutation.mutate({
                ruleId: rule.rule_id,
                mode: mode === 'production' ? 'test' : 'production',
              })
            }
            className={clsx(
              'rounded px-2 py-1 text-[10px] font-medium border transition-colors',
              mode === 'production'
                ? 'text-purple-400 border-purple-400/20 hover:bg-purple-400/10'
                : 'text-oss-muted border-oss-border hover:bg-oss-bg'
            )}
            title={mode === 'production' ? 'Switch to test mode' : 'Switch to production mode'}
          >
            {mode === 'production' ? 'Prod' : 'Test'}
          </button>
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
      {expanded && (
        <div className="px-4 pb-3 ml-5 space-y-3">
          <div>
            <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-1.5">Criteria</h4>
            <CriteriaDisplay criteria={rule.criteria} />
          </div>
          <div>
            <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-1.5">
              Performance Tracking
            </h4>
            <PerformanceComparison
              ruleId={rule.rule_id}
              creation={rule.performance_at_creation ?? null}
            />
          </div>
        </div>
      )}
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
  const [pollingId, setPollingId] = useState<string | null>(null)
  const [savedArchetypes, setSavedArchetypes] = useState<Set<string>>(new Set())
  const [savingArchetype, setSavingArchetype] = useState<string | null>(null)
  const [showManualForm, setShowManualForm] = useState(false)

  // Poll for results while analysis is running
  const { data: polledResult } = usePatternAnalysis(pollingId ?? '', pollingId != null)

  useEffect(() => {
    if (polledResult && polledResult.status !== 'running') {
      setResult(polledResult)
      setPollingId(null)
    }
  }, [polledResult])

  const isRunning = runAnalysis.isPending || pollingId != null

  const handleRun = async () => {
    try {
      const data = await runAnalysis.mutateAsync({ period, verdict, scanner })
      setSavedArchetypes(new Set())
      if (data.status === 'running' && data.analysis_id) {
        setPollingId(data.analysis_id)
      } else {
        setResult(data)
      }
    } catch {
      // Error handled by mutation state
    }
  }

  const handlePromote = useCallback(
    (archetype: ArchetypeResult, analysisId: string) => {
      const key = archetype.name
      setSavingArchetype(key)
      createRule.mutate(
        {
          name: archetype.name,
          criteria: archetype.criteria,
          source: 'ai',
          source_analysis_id: analysisId,
          performance_at_creation: archetype.performance as unknown as Record<string, unknown>,
        },
        {
          onSuccess: () => {
            setSavedArchetypes((prev) => new Set(prev).add(key))
            setSavingArchetype(null)
          },
          onError: () => {
            setSavingArchetype(null)
          },
        }
      )
    },
    [createRule]
  )

  const latestAnalysis = analyses?.[0]
  const displayResult = result ?? null

  // Check which archetypes are already saved as rules
  const ruleNames = new Set(rules?.map((r) => r.name) ?? [])

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
              disabled={isRunning}
              className={clsx(
                'flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors',
                isRunning
                  ? 'bg-oss-accent/30 text-oss-accent cursor-wait'
                  : 'bg-oss-accent text-oss-bg hover:bg-oss-accent/90'
              )}
            >
              {isRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              {isRunning ? 'Analyzing...' : 'Run Analysis'}
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

          {displayResult.status === 'error' && (
            <div className="rounded-lg border border-oss-reject/20 bg-oss-reject/5 p-3 text-xs text-oss-reject">
              {displayResult.message}
            </div>
          )}

          {displayResult.archetypes.map((arch, i) => (
            <ArchetypeCard
              key={arch.name + i}
              archetype={arch}
              onPromote={() => handlePromote(arch, displayResult.analysis_id)}
              isSaving={savingArchetype === arch.name}
              isSaved={savedArchetypes.has(arch.name) || ruleNames.has(arch.name)}
            />
          ))}
        </div>
      )}

      {/* Saved Setup Rules */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-oss-text">
            Saved Setup Rules ({rules?.length ?? 0})
          </h3>
          <button
            onClick={() => setShowManualForm(true)}
            className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-oss-accent border border-oss-accent/20 hover:bg-oss-accent/10 transition-colors"
          >
            <Plus className="h-3.5 w-3.5" />
            Create Rule
          </button>
        </div>
        {rules && rules.length > 0 ? (
          <div className="rounded-lg border border-oss-border bg-oss-surface overflow-hidden">
            {rules.map((rule) => (
              <SetupRuleRow key={rule.rule_id} rule={rule} />
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-oss-border bg-oss-surface p-4 text-center text-xs text-oss-muted">
            No setup rules yet. Run AI analysis or create a manual rule to get started.
          </div>
        )}
      </div>

      <ManualRuleForm isOpen={showManualForm} onClose={() => setShowManualForm(false)} />
    </div>
  )
}
