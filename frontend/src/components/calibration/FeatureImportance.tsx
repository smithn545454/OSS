import { useState } from 'react'
import { useFeatureImportance, useRunFeatureImportance, useGenerateFeatureNarrative } from '@/hooks/useApi'
import type { FeatureStats, FeaturePairStats, WeightComparison, FeatureImportanceResult } from '@/lib/types'
import clsx from 'clsx'
import { BarChart3, ChevronDown, ChevronRight, Brain, Scale, Zap, RefreshCw, Layers } from 'lucide-react'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(val: number | null | undefined, decimals = 1): string {
  if (val == null) return '\u2014'
  return val.toFixed(decimals)
}

function wrColor(wr: number): string {
  if (wr >= 60) return 'text-oss-approve'
  if (wr >= 50) return 'text-oss-watch'
  return 'text-oss-reject'
}

function corrColor(r: number, significant: boolean): string {
  if (!significant) return 'bg-oss-muted/20'
  if (r > 0) return 'bg-emerald-500/70'
  return 'bg-red-400/70'
}

function pillarBadge(pillar: string | null) {
  if (!pillar) return null
  const colors: Record<string, string> = {
    directional: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
    volatility: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
    structure: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  }
  return (
    <span className={clsx('text-[10px] px-1.5 py-0.5 rounded border', colors[pillar] || 'bg-oss-bg text-oss-muted')}>
      {pillar}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Period / Outcome Selector
// ---------------------------------------------------------------------------

function ControlBar({
  period,
  setPeriod,
  outcome,
  setOutcome,
  verdict,
  setVerdict,
  onRun,
  isRunning,
}: {
  period: string
  setPeriod: (p: string) => void
  outcome: string
  setOutcome: (o: string) => void
  verdict: string
  setVerdict: (v: string) => void
  onRun: () => void
  isRunning: boolean
}) {
  const periods = ['all', '90d', '30d']
  const outcomes = [
    { value: 'pnl', label: 'P&L' },
    { value: 'mfe', label: 'MFE' },
  ]
  const verdicts = ['all', 'APPROVE', 'WATCH']

  return (
    <div className="flex items-center gap-4 flex-wrap">
      <div className="flex gap-1 rounded-lg bg-oss-bg p-1">
        {periods.map((p) => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className={clsx(
              'rounded-md px-3 py-1 text-xs font-medium transition-colors',
              period === p ? 'bg-oss-accent/15 text-oss-accent' : 'text-oss-muted hover:text-oss-text'
            )}
          >
            {p === 'all' ? 'All Time' : p}
          </button>
        ))}
      </div>

      <div className="flex gap-1 rounded-lg bg-oss-bg p-1">
        {verdicts.map((v) => (
          <button
            key={v}
            onClick={() => setVerdict(v)}
            className={clsx(
              'rounded-md px-3 py-1 text-xs font-medium transition-colors',
              verdict === v ? 'bg-oss-accent/15 text-oss-accent' : 'text-oss-muted hover:text-oss-text'
            )}
          >
            {v === 'all' ? 'All' : v}
          </button>
        ))}
      </div>

      <div className="flex gap-1 rounded-lg bg-oss-bg p-1">
        {outcomes.map((o) => (
          <button
            key={o.value}
            onClick={() => setOutcome(o.value)}
            className={clsx(
              'rounded-md px-3 py-1 text-xs font-medium transition-colors',
              outcome === o.value ? 'bg-oss-accent/15 text-oss-accent' : 'text-oss-muted hover:text-oss-text'
            )}
          >
            {o.label}
          </button>
        ))}
      </div>

      <button
        onClick={onRun}
        disabled={isRunning}
        className={clsx(
          'flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-xs font-medium transition-colors',
          isRunning
            ? 'bg-oss-bg text-oss-muted cursor-wait'
            : 'bg-oss-accent/15 text-oss-accent hover:bg-oss-accent/25'
        )}
      >
        <RefreshCw className={clsx('h-3.5 w-3.5', isRunning && 'animate-spin')} />
        {isRunning ? 'Analyzing...' : 'Run Analysis'}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Feature Row (expandable with quintiles)
// ---------------------------------------------------------------------------

function FeatureRow({ feature, maxAbsR }: { feature: FeatureStats; maxAbsR: number }) {
  const [expanded, setExpanded] = useState(false)
  const barWidth = maxAbsR > 0 ? (Math.abs(feature.pearson_r) / maxAbsR) * 100 : 0

  return (
    <>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 rounded-lg px-3 py-2.5 bg-oss-bg hover:bg-oss-bg/80 transition-colors text-left"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-oss-muted flex-shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-oss-muted flex-shrink-0" />
        )}

        {/* Feature name + pillar badge */}
        <div className="flex items-center gap-2 min-w-[180px]">
          <span className="text-sm font-medium text-oss-text truncate">{feature.display_name}</span>
          {pillarBadge(feature.pillar)}
        </div>

        {/* Correlation bar */}
        <div className="flex-1 flex items-center gap-2 min-w-[120px]">
          <div className="flex-1 h-4 rounded bg-oss-surface overflow-hidden relative">
            <div
              className={clsx('h-full rounded transition-all', corrColor(feature.pearson_r, feature.is_significant))}
              style={{ width: `${barWidth}%` }}
            />
          </div>
          <span className="font-mono text-xs text-oss-text min-w-[50px] text-right">
            {feature.pearson_r >= 0 ? '+' : ''}{fmt(feature.pearson_r, 3)}
          </span>
        </div>

        {/* Win / Loss means */}
        <div className="text-right min-w-[130px] hidden md:block">
          <span className="text-xs text-oss-muted">
            <span className="text-oss-approve">{fmt(feature.win_mean, 2)}</span>
            {' \u2192 '}
            <span className="text-oss-reject">{fmt(feature.loss_mean, 2)}</span>
          </span>
        </div>

        {/* Significance */}
        <div className="min-w-[30px] text-center">
          {feature.is_significant ? (
            <span className="text-oss-approve text-xs font-bold">*</span>
          ) : (
            <span className="text-oss-muted text-xs">\u2014</span>
          )}
        </div>
      </button>

      {/* Expanded: Quintile table */}
      {expanded && feature.quintiles.length > 0 && (
        <div className="ml-8 mr-3 mb-2 rounded-lg border border-oss-border bg-oss-surface p-3">
          <p className="text-xs text-oss-muted mb-2 font-medium">Quintile Performance</p>
          <div className="grid grid-cols-5 gap-1 text-xs">
            {feature.quintiles.map((q) => (
              <div key={q.quintile} className="rounded-md bg-oss-bg p-2 text-center">
                <p className="text-oss-muted mb-1">
                  Q{q.quintile}
                </p>
                <p className={clsx('font-mono font-semibold text-sm', wrColor(q.win_rate))}>
                  {fmt(q.win_rate, 0)}%
                </p>
                <p className="text-oss-muted mt-0.5 font-mono">
                  {q.avg_return >= 0 ? '+' : ''}{fmt(q.avg_return, 1)}%
                </p>
                <p className="text-[10px] text-oss-muted mt-0.5">
                  n={q.n}
                </p>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-oss-muted mt-2">
            Range: {fmt(feature.quintiles[0]?.range_low, 2)} \u2192 {fmt(feature.quintiles[4]?.range_high, 2)}
            {' \u2022 '}
            Direction: {feature.direction.replace(/_/g, ' ')}
          </p>
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Feature Importance Ranking
// ---------------------------------------------------------------------------

function FeatureRanking({ features }: { features: FeatureStats[] }) {
  const maxAbsR = Math.max(...features.map((f) => Math.abs(f.pearson_r)), 0.01)

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <div className="flex items-center gap-2 mb-4">
        <BarChart3 className="h-4 w-4 text-oss-accent" />
        <h3 className="text-sm font-medium text-oss-text">Feature Importance Ranking</h3>
        <span className="text-xs text-oss-muted ml-auto">
          sorted by |correlation| with outcome
        </span>
      </div>

      {/* Header */}
      <div className="flex items-center gap-3 px-3 py-1.5 text-[10px] text-oss-muted uppercase tracking-wider mb-1">
        <div className="w-3.5" />
        <div className="min-w-[180px]">Feature</div>
        <div className="flex-1 min-w-[120px]">Correlation</div>
        <div className="min-w-[130px] text-right hidden md:block">Win Avg \u2192 Loss Avg</div>
        <div className="min-w-[30px] text-center">Sig</div>
      </div>

      <div className="space-y-1">
        {features.map((f) => (
          <FeatureRow key={f.feature_name} feature={f} maxAbsR={maxAbsR} />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Feature Pair Interactions
// ---------------------------------------------------------------------------

function InteractionsPanel({ interactions }: { interactions: FeaturePairStats[] }) {
  if (interactions.length === 0) return null

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <div className="flex items-center gap-2 mb-4">
        <Layers className="h-4 w-4 text-oss-accent" />
        <h3 className="text-sm font-medium text-oss-text">Feature Interactions</h3>
        <span className="text-xs text-oss-muted ml-auto">
          pairs where combining features creates outsized edge
        </span>
      </div>

      <div className="space-y-2">
        {interactions.slice(0, 8).map((pair, i) => (
          <div key={i} className="rounded-lg bg-oss-bg p-3">
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-medium text-oss-text">
                {pair.feature_a_display} + {pair.feature_b_display}
              </span>
              <span
                className={clsx(
                  'text-xs font-mono font-semibold px-2 py-0.5 rounded',
                  pair.interaction_lift > 5
                    ? 'bg-emerald-500/15 text-emerald-400'
                    : pair.interaction_lift > 0
                      ? 'bg-oss-accent/15 text-oss-accent'
                      : 'bg-red-500/15 text-red-400'
                )}
              >
                {pair.interaction_lift >= 0 ? '+' : ''}{fmt(pair.interaction_lift, 1)}pp lift
              </span>
            </div>
            <div className="flex gap-4 text-xs text-oss-muted">
              <span>
                Both favorable{pair.a_direction ? ` (${pair.a_direction} + ${pair.b_direction})` : ''}: <span className={wrColor(pair.both_high_wr)}>{fmt(pair.both_high_wr, 0)}% WR</span> (n={pair.both_high_n})
              </span>
              <span>
                Both unfavorable: <span className={wrColor(pair.both_low_wr)}>{fmt(pair.both_low_wr, 0)}% WR</span> (n={pair.both_low_n})
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Weight Validation
// ---------------------------------------------------------------------------

function WeightValidationPanel({ comparisons }: { comparisons: WeightComparison[] }) {
  if (comparisons.length === 0) return null

  const pillars = ['directional', 'volatility', 'structure']

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <div className="flex items-center gap-2 mb-4">
        <Scale className="h-4 w-4 text-oss-accent" />
        <h3 className="text-sm font-medium text-oss-text">Weight Validation</h3>
        <span className="text-xs text-oss-muted ml-auto">
          current weights vs empirical importance
        </span>
      </div>

      {pillars.map((pillar) => {
        const pillarComps = comparisons.filter((c) => c.pillar === pillar)
        if (pillarComps.length === 0) return null

        return (
          <div key={pillar} className="mb-4 last:mb-0">
            <p className="text-xs font-medium text-oss-muted uppercase tracking-wider mb-2">
              {pillar}
            </p>
            <div className="space-y-1.5">
              {pillarComps.map((c) => (
                <div key={c.subscore} className="flex items-center gap-3 rounded-lg bg-oss-bg px-3 py-2">
                  <span className="text-sm text-oss-text min-w-[140px]">{c.display_name}</span>

                  {/* Current weight bar */}
                  <div className="flex items-center gap-1 min-w-[80px]">
                    <div className="w-16 h-2 rounded bg-oss-surface overflow-hidden">
                      <div className="h-full rounded bg-oss-muted/40" style={{ width: `${c.current_weight * 100}%` }} />
                    </div>
                    <span className="font-mono text-xs text-oss-muted">{(c.current_weight * 100).toFixed(0)}%</span>
                  </div>

                  {/* Arrow */}
                  <span className="text-oss-muted text-xs">\u2192</span>

                  {/* Empirical weight bar */}
                  <div className="flex items-center gap-1 min-w-[80px]">
                    <div className="w-16 h-2 rounded bg-oss-surface overflow-hidden">
                      <div
                        className={clsx(
                          'h-full rounded',
                          c.recommendation === 'increase' ? 'bg-emerald-500/70' :
                          c.recommendation === 'decrease' ? 'bg-red-400/70' :
                          'bg-oss-accent/50'
                        )}
                        style={{ width: `${c.empirical_importance * 100}%` }}
                      />
                    </div>
                    <span className="font-mono text-xs text-oss-text">{(c.empirical_importance * 100).toFixed(0)}%</span>
                  </div>

                  {/* Recommendation badge */}
                  <span
                    className={clsx(
                      'text-[10px] px-1.5 py-0.5 rounded font-medium',
                      c.recommendation === 'increase' && 'bg-emerald-500/15 text-emerald-400',
                      c.recommendation === 'decrease' && 'bg-red-500/15 text-red-400',
                      c.recommendation === 'aligned' && 'bg-oss-bg text-oss-muted'
                    )}
                  >
                    {c.recommendation === 'increase' ? '\u25B2' : c.recommendation === 'decrease' ? '\u25BC' : '\u2248'}{' '}
                    {c.recommendation}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AI Narrative
// ---------------------------------------------------------------------------

function NarrativePanel({
  narrative,
  analysisId,
}: {
  narrative: string | null
  analysisId: string
}) {
  const generateMutation = useGenerateFeatureNarrative()
  const displayNarrative = generateMutation.data?.narrative ?? narrative

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <div className="flex items-center gap-2 mb-3">
        <Brain className="h-4 w-4 text-oss-accent" />
        <h3 className="text-sm font-medium text-oss-text">AI Analysis</h3>
      </div>

      {displayNarrative ? (
        <div className="text-sm text-oss-text/90 leading-relaxed whitespace-pre-wrap">{displayNarrative}</div>
      ) : (
        <div className="text-center py-4">
          {generateMutation.isError && (
            <p className="text-xs text-oss-reject mb-3">
              Failed to generate analysis. Try again.
            </p>
          )}
          <p className="text-xs text-oss-muted mb-3">Generate an AI narrative summary of these findings</p>
          <button
            onClick={() => generateMutation.mutate(analysisId)}
            disabled={generateMutation.isPending}
            className={clsx(
              'flex items-center gap-1.5 mx-auto rounded-lg px-4 py-1.5 text-xs font-medium transition-colors',
              generateMutation.isPending
                ? 'bg-oss-bg text-oss-muted cursor-wait'
                : 'bg-oss-accent/15 text-oss-accent hover:bg-oss-accent/25'
            )}
          >
            <Brain className={clsx('h-3.5 w-3.5', generateMutation.isPending && 'animate-pulse')} />
            {generateMutation.isPending ? 'Generating...' : 'Generate AI Analysis'}
          </button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function FeatureImportance() {
  const [period, setPeriod] = useState('all')
  const [outcome, setOutcome] = useState('pnl')
  const [verdict, setVerdict] = useState('all')

  const { data: cachedResult, isLoading: isLoadingCached } = useFeatureImportance()
  const runMutation = useRunFeatureImportance()

  // Use mutation result if available, otherwise fall back to cached
  const result: FeatureImportanceResult | undefined = runMutation.data ?? cachedResult

  const handleRun = () => {
    runMutation.mutate({
      period,
      outcome,
      verdict: verdict === 'all' ? undefined : verdict,
    })
  }

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h2 className="text-lg font-semibold text-oss-text">Feature Importance</h2>
          <p className="text-xs text-oss-muted mt-0.5">
            Which entry signals predict profitable trades?
          </p>
        </div>
        <ControlBar
          period={period}
          setPeriod={setPeriod}
          outcome={outcome}
          setOutcome={setOutcome}
          verdict={verdict}
          setVerdict={setVerdict}
          onRun={handleRun}
          isRunning={runMutation.isPending}
        />
      </div>

      {/* Summary stats */}
      {result && (
        <div className="flex gap-4 flex-wrap">
          <div className="rounded-xl border border-oss-border bg-oss-surface px-4 py-3">
            <p className="text-xs text-oss-muted">Positions Analyzed</p>
            <p className="font-mono text-xl font-bold text-oss-text">{result.n_positions.toLocaleString()}</p>
          </div>
          <div className="rounded-xl border border-oss-border bg-oss-surface px-4 py-3">
            <p className="text-xs text-oss-muted">Overall Win Rate</p>
            <p className={clsx('font-mono text-xl font-bold', wrColor(result.overall_win_rate))}>
              {fmt(result.overall_win_rate, 1)}%
            </p>
          </div>
          <div className="rounded-xl border border-oss-border bg-oss-surface px-4 py-3">
            <p className="text-xs text-oss-muted">Avg Return</p>
            <p className={clsx('font-mono text-xl font-bold', result.overall_avg_return >= 0 ? 'text-oss-approve' : 'text-oss-reject')}>
              {result.overall_avg_return >= 0 ? '+' : ''}{fmt(result.overall_avg_return, 1)}%
            </p>
          </div>
          <div className="rounded-xl border border-oss-border bg-oss-surface px-4 py-3">
            <p className="text-xs text-oss-muted">Significant Features</p>
            <p className="font-mono text-xl font-bold text-oss-accent">
              {result.features.filter((f) => f.is_significant).length} / {result.features.length}
            </p>
          </div>
        </div>
      )}

      {/* Loading / Empty states */}
      {isLoadingCached && !result && (
        <div className="text-center py-12 text-sm text-oss-muted">Loading cached analysis...</div>
      )}

      {!isLoadingCached && !result && (
        <div className="text-center py-12">
          <Zap className="h-8 w-8 text-oss-muted mx-auto mb-3" />
          <p className="text-sm text-oss-muted mb-1">No analysis available yet</p>
          <p className="text-xs text-oss-muted">Click "Run Analysis" to compute feature importance from your paper trading data</p>
        </div>
      )}

      {runMutation.isError && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4 text-sm text-red-400">
          {(runMutation.error as Error)?.message || 'Analysis failed'}
        </div>
      )}

      {/* Results */}
      {result && (
        <>
          <FeatureRanking features={result.features} />

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <InteractionsPanel interactions={result.interactions} />
            <WeightValidationPanel comparisons={result.weight_comparisons} />
          </div>

          <NarrativePanel narrative={result.narrative} analysisId={result.analysis_id} />
        </>
      )}
    </div>
  )
}
