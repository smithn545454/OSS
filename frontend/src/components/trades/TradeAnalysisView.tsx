import { useState } from 'react'
import { Brain, Loader2, AlertTriangle, Lightbulb, Shield, ArrowRight } from 'lucide-react'
import clsx from 'clsx'
import { useTriggerTradeAnalysis, useLatestTradeAnalysis, useTradeAnalysis } from '@/hooks/useApi'

export default function TradeAnalysisView() {
  const triggerAnalysis = useTriggerTradeAnalysis()
  const { data: latest } = useLatestTradeAnalysis()
  const [pollingId, setPollingId] = useState<string | null>(null)

  // Poll if we have a running analysis
  const latestId = (latest as Record<string, unknown>)?.analysis_id as string | undefined
  const latestStatus = (latest as Record<string, unknown>)?.status as string | undefined
  const activePollingId = pollingId || (latestStatus === 'running' ? latestId : null)

  const { data: pollingData } = useTradeAnalysis(activePollingId || '', !!activePollingId)

  // Use polling data if available, otherwise latest
  const analysis = (activePollingId && pollingData) || latest
  const status = (analysis as Record<string, unknown>)?.status as string | undefined

  // Stop polling when complete
  if (pollingId && status === 'complete') {
    setPollingId(null)
  }

  const handleTrigger = () => {
    triggerAnalysis.mutate(undefined, {
      onSuccess: (data) => {
        if (data.analysis_id) {
          setPollingId(data.analysis_id)
        }
      },
    })
  }

  if (!analysis || status === 'none') {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-card p-8 text-center">
        <Brain className="h-12 w-12 text-oss-muted/30 mx-auto mb-4" />
        <p className="text-oss-muted mb-4">
          AI analysis finds patterns across your real trades — edge patterns, behavioral insights,
          and risk warnings.
        </p>
        <button
          onClick={handleTrigger}
          disabled={triggerAnalysis.isPending}
          className="rounded-lg bg-oss-accent px-6 py-2.5 text-sm font-semibold text-black hover:bg-oss-accent/90 disabled:opacity-50 transition-colors inline-flex items-center gap-2"
        >
          {triggerAnalysis.isPending ? (
            <><Loader2 className="h-4 w-4 animate-spin" /> Starting...</>
          ) : (
            <><Brain className="h-4 w-4" /> Run Analysis</>
          )}
        </button>
        {triggerAnalysis.isError && (
          <p className="text-sm text-oss-reject mt-3">
            {(triggerAnalysis.error as Error)?.message || 'Failed to start analysis'}
          </p>
        )}
      </div>
    )
  }

  if (status === 'running') {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-card p-8 text-center">
        <Loader2 className="h-10 w-10 text-oss-accent animate-spin mx-auto mb-4" />
        <p className="text-oss-text font-medium">Analyzing your trades...</p>
        <p className="text-sm text-oss-muted mt-1">This may take 30-60 seconds</p>
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div className="rounded-xl border border-oss-reject/30 bg-oss-reject/5 p-6">
        <p className="text-oss-reject font-medium mb-2">Analysis Failed</p>
        <p className="text-sm text-oss-muted">
          {String((analysis as Record<string, unknown>)?.error_message || 'Unknown error')}
        </p>
        <button
          onClick={handleTrigger}
          className="mt-3 rounded-lg bg-oss-surface px-4 py-2 text-sm text-oss-text hover:bg-oss-border transition-colors"
        >
          Try Again
        </button>
      </div>
    )
  }

  // Complete — show results
  const data = analysis as Record<string, unknown>
  const summary = data.summary as Record<string, unknown> | undefined
  const edgePatterns = (data.edge_patterns as Record<string, unknown>[]) || []
  const behavioralInsights = (data.behavioral_insights as Record<string, unknown>[]) || []
  const riskWarnings = (data.risk_warnings as Record<string, unknown>[]) || []
  const nextSteps = (data.next_steps as string[]) || []

  return (
    <div className="space-y-6">
      {/* Header + Re-run */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain className="h-5 w-5 text-oss-accent" />
          <h2 className="text-lg font-semibold text-oss-text">AI Trade Analysis</h2>
        </div>
        <button
          onClick={handleTrigger}
          disabled={triggerAnalysis.isPending}
          className="rounded-lg bg-oss-surface px-4 py-2 text-sm text-oss-text hover:bg-oss-border transition-colors inline-flex items-center gap-2 disabled:opacity-50"
        >
          {triggerAnalysis.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Brain className="h-4 w-4" />
          )}
          Re-run
        </button>
      </div>

      {/* Summary */}
      {summary && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-5">
          <p className="text-sm text-oss-text">
            {String(summary.key_observation || '')}
          </p>
        </div>
      )}

      {/* Edge Patterns */}
      {edgePatterns.length > 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-5">
          <h3 className="flex items-center gap-2 text-base font-semibold text-oss-text mb-4">
            <Lightbulb className="h-4 w-4 text-oss-accent" />
            Edge Patterns
          </h3>
          <div className="space-y-4">
            {edgePatterns.map((p, i) => (
              <div key={i} className="rounded-lg bg-oss-bg p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-medium text-oss-text">{String(p.name)}</span>
                  <div className="flex gap-3 text-xs">
                    <span className="text-oss-muted">{String(p.trade_count)} trades</span>
                    <span className={clsx(
                      'font-mono',
                      Number(p.win_rate_pct) >= 60 ? 'text-oss-approve' : 'text-oss-muted'
                    )}>
                      {Number(p.win_rate_pct).toFixed(0)}% WR
                    </span>
                    <span className={clsx(
                      'font-mono',
                      Number(p.avg_return_pct) > 0 ? 'text-oss-approve' : 'text-oss-reject'
                    )}>
                      {Number(p.avg_return_pct) > 0 ? '+' : ''}{Number(p.avg_return_pct).toFixed(1)}%
                    </span>
                  </div>
                </div>
                <p className="text-sm text-oss-muted mb-2">{String(p.description)}</p>
                <p className="text-sm text-oss-accent">{String(p.recommendation)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Behavioral Insights */}
      {behavioralInsights.length > 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-5">
          <h3 className="flex items-center gap-2 text-base font-semibold text-oss-text mb-4">
            <Lightbulb className="h-4 w-4 text-amber-400" />
            Behavioral Insights
          </h3>
          <div className="space-y-3">
            {behavioralInsights.map((ins, i) => (
              <div key={i} className={clsx(
                'rounded-lg p-4 border-l-4',
                ins.severity === 'critical' ? 'bg-oss-reject/5 border-oss-reject' :
                ins.severity === 'warning' ? 'bg-amber-500/5 border-amber-500' :
                'bg-oss-bg border-oss-accent'
              )}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs uppercase text-oss-muted font-medium">
                    {String(ins.type).replace('_', ' ')}
                  </span>
                  <span className={clsx(
                    'text-xs rounded px-1.5 py-0.5',
                    ins.severity === 'critical' ? 'bg-oss-reject/20 text-oss-reject' :
                    ins.severity === 'warning' ? 'bg-amber-500/20 text-amber-400' :
                    'bg-oss-accent/20 text-oss-accent'
                  )}>
                    {String(ins.severity)}
                  </span>
                </div>
                <p className="text-sm text-oss-text mb-1">{String(ins.finding)}</p>
                <p className="text-sm text-oss-accent">{String(ins.recommendation)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Risk Warnings */}
      {riskWarnings.length > 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-5">
          <h3 className="flex items-center gap-2 text-base font-semibold text-oss-text mb-4">
            <Shield className="h-4 w-4 text-oss-reject" />
            Risk Warnings
          </h3>
          <div className="space-y-2">
            {riskWarnings.map((w, i) => (
              <div key={i} className="flex items-start gap-3 rounded-lg bg-oss-bg p-3">
                <AlertTriangle className={clsx(
                  'h-4 w-4 mt-0.5 shrink-0',
                  w.severity === 'critical' ? 'text-oss-reject' : 'text-amber-400'
                )} />
                <div>
                  <span className="text-xs uppercase text-oss-muted">{String(w.type)}</span>
                  <p className="text-sm text-oss-text">{String(w.finding)}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Next Steps */}
      {nextSteps.length > 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-card p-5">
          <h3 className="text-base font-semibold text-oss-text mb-3">Next Steps</h3>
          <ul className="space-y-2">
            {nextSteps.map((step, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-oss-text">
                <ArrowRight className="h-4 w-4 text-oss-accent mt-0.5 shrink-0" />
                {step}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
