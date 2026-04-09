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
  MessageSquare,
  Sparkles,
  History,
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
  useSetupRuleBatchPerformance,
  useRunCustomAnalysis,
  useCustomAnalyses,
  useCustomAnalysis,
} from '@/hooks/useApi'
import type { ArchetypeResult, PatternAnalysis, SetupRule, CustomAnalysis, CustomAnalysisSuggestedRule } from '@/lib/types'
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

function SetupRuleRow({ rule, livePerformance }: { rule: SetupRule; livePerformance: { sample_size: number; performance: import('@/lib/types').ArchetypePerformance | null } | null }) {
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
            {rule.is_stale && (
              <span
                className="inline-block rounded px-1.5 py-0.5 text-[10px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20"
                title={`Created under scoring regime ${rule.regime || 'v1'}. Scoring has changed — consider recalibrating.`}
              >
                {rule.regime?.toUpperCase() || 'V1'}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-0.5 ml-5 text-xs text-oss-muted">
            {(() => {
              const livePerf = livePerformance?.performance
              const perf = livePerf ?? rule.performance_at_creation
              if (perf && perf.win_rate != null) {
                return (
                  <>
                    <span>WR: {fmt(perf.win_rate * 100)}%</span>
                    <span>Avg: {fmt(perf.avg_return)}%</span>
                    <span>n={perf.sample_size ?? '--'}</span>
                  </>
                )
              }
              return <span className="italic">No historical data</span>
            })()}
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

const PROMPT_SUGGESTIONS = [
  "Identify all trades with 200%+ returns. What characteristics do they share? What distinguishes them from average winners?",
  "Which scanner + IV percentile + pillar score combinations produce the best risk-adjusted returns?",
  "Analyze trades where 3+ scanners converged. How do they compare to single-scanner trades?",
  "What characteristics predict trades that go profitable within 1-2 days and continue running higher?",
  "Find the optimal 'grand slam' profile — the rarest, highest-returning trade setup that surfaces 1-2 trades per week.",
]

function SuggestedRuleCard({
  rule,
  onSave,
  isSaving,
  isSaved,
}: {
  rule: CustomAnalysisSuggestedRule
  onSave: () => void
  isSaving: boolean
  isSaved: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const perf = rule.performance

  return (
    <div className="rounded-lg border border-oss-border bg-oss-surface overflow-hidden">
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex items-start justify-between p-3 cursor-pointer hover:bg-oss-bg/30 transition-colors"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5 text-oss-muted flex-shrink-0" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-oss-muted flex-shrink-0" />
            )}
            <h4 className="font-medium text-sm text-oss-text truncate">{rule.name}</h4>
          </div>
          <div className="flex items-center gap-3 ml-5 text-xs">
            <span
              className={clsx(
                'inline-block rounded px-1.5 py-0.5 text-[10px] font-medium border',
                confidenceColor(rule.confidence)
              )}
            >
              {rule.confidence_label}
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
            if (!isSaved && !isSaving) onSave()
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
        >
          {isSaved ? (
            <Check className="h-3.5 w-3.5" />
          ) : isSaving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <BookmarkPlus className="h-3.5 w-3.5" />
          )}
          {isSaved ? 'Saved' : isSaving ? 'Saving...' : 'Save as Rule'}
        </button>
      </div>

      {expanded && (
        <div className="border-t border-oss-border px-3 py-2.5 space-y-2.5">
          <div>
            <h5 className="text-xs text-oss-muted uppercase tracking-wider mb-1">Criteria</h5>
            <CriteriaDisplay criteria={rule.criteria} />
          </div>
          <div className="grid grid-cols-5 gap-2">
            <div className="text-center">
              <div className="text-[10px] text-oss-muted">Win Rate</div>
              <div className="font-mono text-xs text-oss-approve">{fmt(perf.win_rate * 100)}%</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] text-oss-muted">Avg Return</div>
              <div className="font-mono text-xs text-oss-text">{fmt(perf.avg_return)}%</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] text-oss-muted">Median</div>
              <div className="font-mono text-xs text-oss-text">{fmt(perf.median_return)}%</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] text-oss-muted">Sample</div>
              <div className="font-mono text-xs text-oss-text">{perf.sample_size}</div>
            </div>
            <div className="text-center">
              <div className="text-[10px] text-oss-muted">Avg Days</div>
              <div className="font-mono text-xs text-oss-text">{fmt(perf.avg_days_held)}</div>
            </div>
          </div>
          {rule.reasoning && (
            <div>
              <h5 className="text-xs text-oss-muted uppercase tracking-wider mb-1">Reasoning</h5>
              <p className="text-xs text-oss-muted">{rule.reasoning}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function CustomAnalysisSection({
  period,
  verdict,
  scanner,
}: {
  period: string
  verdict?: string
  scanner?: string
}) {
  const runAnalysis = useRunCustomAnalysis()
  const { data: pastAnalyses } = useCustomAnalyses()
  const createRule = useCreateSetupRule()

  const [prompt, setPrompt] = useState('')
  const [currentResult, setCurrentResult] = useState<CustomAnalysis | null>(null)
  const [pollingId, setPollingId] = useState<string | null>(null)
  const [pollingStartedAt, setPollingStartedAt] = useState<number | null>(null)
  const [savedRules, setSavedRules] = useState<Set<string>>(new Set())
  const [savingRule, setSavingRule] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [historyViewId, setHistoryViewId] = useState<string | null>(null)

  // Poll for results
  const { data: polledResult } = useCustomAnalysis(pollingId ?? '', pollingId != null)

  // Poll for history item
  const { data: historyResult } = useCustomAnalysis(historyViewId ?? '', !!historyViewId)

  useEffect(() => {
    if (polledResult && polledResult.status !== 'running') {
      setCurrentResult(polledResult)
      setPollingId(null)
      setPollingStartedAt(null)
    }
    if (pollingId && pollingStartedAt && Date.now() - pollingStartedAt > 5 * 60 * 1000) {
      setCurrentResult({
        analysis_id: pollingId,
        status: 'error',
        user_prompt: prompt,
        positions_analyzed: 0,
        message: 'Analysis timed out. Please try again.',
      })
      setPollingId(null)
      setPollingStartedAt(null)
    }
  }, [polledResult, pollingId, pollingStartedAt, prompt])

  const isRunning = runAnalysis.isPending || pollingId != null

  const handleRun = async () => {
    if (!prompt.trim()) return
    try {
      const data = await runAnalysis.mutateAsync({ prompt: prompt.trim(), period, verdict, scanner })
      setSavedRules(new Set())
      setHistoryViewId(null)
      if (data.status === 'running' && data.analysis_id) {
        setPollingId(data.analysis_id)
        setPollingStartedAt(Date.now())
      } else {
        setCurrentResult(data)
      }
    } catch {
      // Error handled by mutation state
    }
  }

  const handleSaveRule = useCallback(
    (rule: CustomAnalysisSuggestedRule, analysisId: string) => {
      const key = rule.name
      setSavingRule(key)
      createRule.mutate(
        {
          name: rule.name,
          criteria: rule.criteria,
          source: 'ai',
          source_analysis_id: analysisId,
          performance_at_creation: rule.performance as unknown as Record<string, unknown>,
        },
        {
          onSuccess: () => {
            setSavedRules((prev) => new Set(prev).add(key))
            setSavingRule(null)
          },
          onError: () => {
            setSavingRule(null)
          },
        }
      )
    },
    [createRule]
  )

  // Display result: current analysis OR history item
  const displayResult = historyViewId && historyResult ? historyResult : currentResult
  const displayAnalysisId = displayResult?.analysis_id ?? ''

  return (
    <div className="space-y-4">
      {/* Custom Analysis Input */}
      <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
        <div className="flex items-center gap-3 mb-3">
          <MessageSquare className="h-5 w-5 text-purple-400" />
          <div>
            <h3 className="font-medium text-oss-text">Custom Analysis</h3>
            <p className="text-xs text-oss-muted">
              Ask any question about your paper trade history. Get analysis + saveable setup rules.
            </p>
          </div>
        </div>

        {/* Prompt suggestions */}
        <div className="flex flex-wrap gap-1.5 mb-3">
          {PROMPT_SUGGESTIONS.map((suggestion, i) => (
            <button
              key={i}
              onClick={() => setPrompt(suggestion)}
              className="rounded-full px-2.5 py-1 text-[10px] text-oss-muted border border-oss-border hover:border-purple-400/40 hover:text-purple-400 transition-colors truncate max-w-[280px]"
              title={suggestion}
            >
              <Sparkles className="inline h-2.5 w-2.5 mr-1" />
              {suggestion.slice(0, 60)}{suggestion.length > 60 ? '...' : ''}
            </button>
          ))}
        </div>

        {/* Textarea + Run button */}
        <div className="flex gap-2">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Ask a question about your paper trades..."
            className="flex-1 rounded-md border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text placeholder:text-oss-muted/50 resize-none focus:outline-none focus:ring-1 focus:ring-purple-400/50"
            rows={2}
            maxLength={2000}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                handleRun()
              }
            }}
          />
          <button
            onClick={handleRun}
            disabled={isRunning || !prompt.trim()}
            className={clsx(
              'flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors self-end',
              isRunning
                ? 'bg-purple-400/30 text-purple-400 cursor-wait'
                : !prompt.trim()
                  ? 'bg-oss-bg text-oss-muted cursor-not-allowed'
                  : 'bg-purple-500 text-white hover:bg-purple-500/90'
            )}
          >
            {isRunning ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {isRunning ? 'Analyzing...' : 'Analyze'}
          </button>
        </div>
        <div className="flex items-center justify-between mt-1.5">
          <span className="text-[10px] text-oss-muted">
            {prompt.length}/2000 &middot; Cmd+Enter to run
          </span>
          {pastAnalyses && pastAnalyses.length > 0 && (
            <button
              onClick={() => setShowHistory(!showHistory)}
              className="flex items-center gap-1 text-[10px] text-oss-muted hover:text-oss-text transition-colors"
            >
              <History className="h-3 w-3" />
              {showHistory ? 'Hide' : 'Show'} history ({pastAnalyses.length})
            </button>
          )}
        </div>

        {runAnalysis.isError && (
          <div className="mt-2 text-xs text-oss-reject">
            Analysis failed: {runAnalysis.error?.message ?? 'Unknown error'}
          </div>
        )}
      </div>

      {/* History */}
      {showHistory && pastAnalyses && pastAnalyses.length > 0 && (
        <div className="rounded-lg border border-oss-border bg-oss-surface overflow-hidden">
          <div className="px-3 py-2 border-b border-oss-border">
            <h4 className="text-xs font-medium text-oss-muted uppercase tracking-wider">
              Previous Analyses
            </h4>
          </div>
          <div className="divide-y divide-oss-border">
            {pastAnalyses.slice(0, 10).map((a) => (
              <button
                key={a.analysis_id}
                onClick={() => {
                  setHistoryViewId(a.analysis_id)
                  setPrompt(a.user_prompt)
                }}
                className={clsx(
                  'w-full text-left px-3 py-2 hover:bg-oss-bg/30 transition-colors',
                  historyViewId === a.analysis_id && 'bg-purple-400/5'
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs text-oss-text truncate max-w-[400px]">
                    {a.user_prompt}
                  </span>
                  <div className="flex items-center gap-2 flex-shrink-0 ml-2">
                    <span
                      className={clsx(
                        'text-[10px] px-1.5 py-0.5 rounded',
                        a.status === 'complete'
                          ? 'text-oss-approve bg-oss-approve/10'
                          : a.status === 'error'
                            ? 'text-oss-reject bg-oss-reject/10'
                            : 'text-oss-accent bg-oss-accent/10'
                      )}
                    >
                      {a.status}
                    </span>
                    <span className="text-[10px] text-oss-muted">
                      {a.created_at?.slice(0, 10)}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-[10px] text-oss-muted">
                    {a.positions_analyzed} trades &middot; {a.suggested_rule_count} rules
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Results */}
      {displayResult && displayResult.status !== 'running' && (
        <div className="space-y-4">
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

          {/* Markdown Analysis */}
          {displayResult.analysis && (
            <div className="rounded-lg border border-oss-border bg-oss-surface p-4">
              <h4 className="text-xs text-oss-muted uppercase tracking-wider mb-2">Analysis</h4>
              <div className="prose prose-sm prose-invert max-w-none text-oss-text text-sm leading-relaxed whitespace-pre-wrap">
                {displayResult.analysis}
              </div>
            </div>
          )}

          {/* Suggested Rules */}
          {displayResult.suggested_rules && displayResult.suggested_rules.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-xs font-medium text-oss-muted uppercase tracking-wider">
                Suggested Rules ({displayResult.suggested_rules.length})
              </h4>
              {displayResult.suggested_rules.map((rule, i) => (
                <SuggestedRuleCard
                  key={rule.name + i}
                  rule={rule}
                  onSave={() => handleSaveRule(rule, displayAnalysisId)}
                  isSaving={savingRule === rule.name}
                  isSaved={savedRules.has(rule.name)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Running indicator */}
      {pollingId && (
        <div className="rounded-lg border border-purple-400/20 bg-purple-400/5 p-4 flex items-center gap-3">
          <Loader2 className="h-5 w-5 text-purple-400 animate-spin" />
          <div>
            <p className="text-sm text-oss-text">Analyzing your paper trades...</p>
            <p className="text-xs text-oss-muted">
              This typically takes 15-30 seconds. Pre-computing statistics and running AI analysis.
            </p>
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
  const { data: batchPerf } = useSetupRuleBatchPerformance()
  const createRule = useCreateSetupRule()

  const [result, setResult] = useState<PatternAnalysis | null>(null)
  const [pollingId, setPollingId] = useState<string | null>(null)
  const [pollingStartedAt, setPollingStartedAt] = useState<number | null>(null)
  const [savedArchetypes, setSavedArchetypes] = useState<Set<string>>(new Set())
  const [savingArchetype, setSavingArchetype] = useState<string | null>(null)
  const [showManualForm, setShowManualForm] = useState(false)

  // Poll for results while analysis is running
  const { data: polledResult } = usePatternAnalysis(pollingId ?? '', pollingId != null)

  useEffect(() => {
    if (polledResult && polledResult.status !== 'running') {
      setResult(polledResult)
      setPollingId(null)
      setPollingStartedAt(null)
    }
    // Timeout after 5 minutes — worker normally completes in <30 seconds
    if (pollingId && pollingStartedAt && Date.now() - pollingStartedAt > 5 * 60 * 1000) {
      setResult({
        analysis_id: pollingId,
        status: 'error',
        positions_analyzed: 0,
        archetypes: [],
        message: 'Analysis timed out. Please try again.',
      })
      setPollingId(null)
      setPollingStartedAt(null)
    }
  }, [polledResult, pollingId, pollingStartedAt])

  const isRunning = runAnalysis.isPending || pollingId != null

  const handleRun = async () => {
    try {
      const data = await runAnalysis.mutateAsync({ period, verdict, scanner })
      setSavedArchetypes(new Set())
      if (data.status === 'running' && data.analysis_id) {
        setPollingId(data.analysis_id)
        setPollingStartedAt(Date.now())
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

      {/* Custom Analysis */}
      <CustomAnalysisSection period={period} verdict={verdict} scanner={scanner} />

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
              <SetupRuleRow key={rule.rule_id} rule={rule} livePerformance={batchPerf?.performances?.[rule.rule_id] ?? null} />
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
