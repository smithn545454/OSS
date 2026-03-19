import { Brain, X, ArrowUp, ArrowDown, AlertTriangle, RefreshCw } from 'lucide-react'
import clsx from 'clsx'
import { useScannerAnalysis } from '@/hooks/useApi'
import type { ScannerAnalysisResponse, ScannerAnalysisGateRec, ScannerAnalysisRootCause } from '@/lib/types'

function severityColor(severity: string): string {
  if (severity === 'high') return 'bg-oss-reject'
  if (severity === 'medium') return 'bg-oss-watch'
  return 'bg-oss-muted'
}

function confidenceBadge(confidence: string) {
  const colors = {
    high: 'bg-oss-approve/15 text-oss-approve',
    medium: 'bg-oss-watch/15 text-oss-watch',
    low: 'bg-oss-reject/15 text-oss-reject',
  }
  return (
    <span className={clsx(
      'text-[10px] font-medium rounded-full px-2 py-0.5',
      colors[confidence as keyof typeof colors] || colors.low
    )}>
      {confidence} confidence
    </span>
  )
}

function GateRecommendationCard({ rec }: { rec: ScannerAnalysisGateRec }) {
  const isTighten = rec.direction === 'tighten'
  return (
    <div className="rounded-lg border border-oss-border bg-oss-bg p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className={clsx(
          'text-[10px] font-bold uppercase rounded px-1.5 py-0.5',
          isTighten
            ? 'bg-amber-500/15 text-amber-400'
            : 'bg-blue-500/15 text-blue-400'
        )}>
          {isTighten ? <ArrowUp className="inline h-3 w-3 mr-0.5" /> : <ArrowDown className="inline h-3 w-3 mr-0.5" />}
          {rec.direction}
        </span>
        <span className="text-sm font-medium text-oss-text">{rec.gate_name}</span>
      </div>
      <div className="flex items-center gap-2 font-mono text-lg">
        <span className="text-oss-muted">{rec.current_value}</span>
        <span className="text-oss-muted text-sm">&rarr;</span>
        <span className={clsx(
          'font-bold',
          isTighten ? 'text-amber-400' : 'text-blue-400'
        )}>
          {rec.suggested_value}
        </span>
      </div>
      <p className="text-xs text-oss-muted leading-relaxed">{rec.rationale}</p>
      {rec.expected_impact && (
        <p className="text-[10px] text-oss-muted/70 italic">{rec.expected_impact}</p>
      )}
    </div>
  )
}

function RootCauseItem({ cause }: { cause: ScannerAnalysisRootCause }) {
  return (
    <div className="flex items-start gap-2">
      <span className={clsx('mt-1.5 h-2 w-2 rounded-full shrink-0', severityColor(cause.severity))} />
      <div>
        <p className="text-sm text-oss-text">{cause.cause}</p>
        <p className="text-xs text-oss-muted mt-0.5">{cause.evidence}</p>
      </div>
    </div>
  )
}

function AnalysisResults({ data }: { data: ScannerAnalysisResponse }) {
  const { analysis, metadata, data_snapshot } = data

  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="rounded-lg border border-oss-accent/20 bg-oss-accent/5 p-3">
        <div className="flex items-center gap-2 mb-2">
          <Brain className="h-3.5 w-3.5 text-oss-accent" />
          <span className="text-xs font-medium text-oss-accent">AI Analysis</span>
          {confidenceBadge(analysis.confidence)}
        </div>
        <p className="text-sm text-oss-text leading-relaxed">{analysis.summary}</p>
      </div>

      {/* Data snapshot */}
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-lg bg-oss-bg px-2.5 py-1.5 text-center">
          <p className="text-[10px] text-oss-muted">Analyzed</p>
          <p className="font-mono text-sm text-oss-text">{data_snapshot.closed_trades}</p>
        </div>
        <div className="rounded-lg bg-oss-bg px-2.5 py-1.5 text-center">
          <p className="text-[10px] text-oss-muted">Winners</p>
          <p className="font-mono text-sm text-oss-approve">{data_snapshot.winners}</p>
        </div>
        <div className="rounded-lg bg-oss-bg px-2.5 py-1.5 text-center">
          <p className="text-[10px] text-oss-muted">Losers</p>
          <p className="font-mono text-sm text-oss-reject">{data_snapshot.losers}</p>
        </div>
      </div>

      {/* Root Causes */}
      {analysis.root_causes.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-oss-muted mb-2">Root Causes</h4>
          <div className="space-y-2">
            {analysis.root_causes.map((cause, i) => (
              <RootCauseItem key={i} cause={cause} />
            ))}
          </div>
        </div>
      )}

      {/* Gate Recommendations */}
      {analysis.gate_recommendations.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-oss-muted mb-2">Gate Recommendations</h4>
          <div className="space-y-2">
            {analysis.gate_recommendations.map((rec, i) => (
              <GateRecommendationCard key={i} rec={rec} />
            ))}
          </div>
        </div>
      )}

      {/* Additional Filters */}
      {analysis.additional_filters.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-oss-muted mb-2">Additional Suggestions</h4>
          <div className="space-y-1.5">
            {analysis.additional_filters.map((filter, i) => (
              <div key={i} className="flex items-start gap-2">
                <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-oss-accent shrink-0" />
                <div>
                  <p className="text-xs text-oss-text">{filter.description}</p>
                  <p className="text-[10px] text-oss-muted">{filter.rationale}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Confidence Rationale */}
      {analysis.confidence_rationale && (
        <p className="text-[10px] text-oss-muted/70 italic border-t border-oss-border pt-2">
          {analysis.confidence_rationale}
        </p>
      )}

      {/* Metadata footer */}
      <div className="flex items-center gap-3 text-[10px] text-oss-muted/60 border-t border-oss-border pt-2">
        <span>{metadata.positions_analyzed} trades analyzed</span>
        <span>&middot;</span>
        <span>{metadata.model_used.split('-').slice(0, 3).join('-')}</span>
        <span>&middot;</span>
        <span>{metadata.tokens_used} tokens</span>
        <span>&middot;</span>
        <span>{metadata.remaining_llm_calls}/50 credits left</span>
        {metadata.cached && <span className="text-oss-accent">&middot; cached</span>}
      </div>
    </div>
  )
}

export default function ScannerAnalysisPanel({
  scannerName,
  onClose,
}: {
  scannerName: string
  onClose: () => void
}) {
  const { mutate, data, isPending, error, reset } = useScannerAnalysis()

  const displayName = scannerName.replace(/_/g, ' ')

  return (
    <div className="mt-2 rounded-xl border border-oss-accent/20 bg-oss-surface p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Brain className="h-4 w-4 text-oss-accent" />
          <h3 className="text-sm font-medium text-oss-text">
            {displayName} Analysis
          </h3>
        </div>
        <button
          onClick={onClose}
          className="text-oss-muted hover:text-oss-text transition-colors p-1 rounded"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Initial state — show run button */}
      {!data && !isPending && !error && (
        <div className="text-center py-4">
          <button
            onClick={() => mutate(scannerName)}
            className="inline-flex items-center gap-2 rounded-lg bg-oss-accent/10 hover:bg-oss-accent/20 text-oss-accent px-4 py-2 text-sm font-medium transition-colors"
          >
            <Brain className="h-4 w-4" />
            Run Analysis
          </button>
          <p className="text-[10px] text-oss-muted mt-2">
            Uses 1 AI credit (Claude Sonnet)
          </p>
        </div>
      )}

      {/* Loading */}
      {isPending && (
        <div className="py-6 text-center">
          <div className="inline-flex items-center gap-2 text-sm text-oss-muted">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-oss-accent border-t-transparent" />
            Analyzing {displayName} performance...
          </div>
          <p className="text-[10px] text-oss-muted mt-2">This may take 10-15 seconds</p>
        </div>
      )}

      {/* Error */}
      {error && !isPending && (
        <div className="py-4 text-center">
          <div className="flex items-center justify-center gap-2 text-sm text-oss-reject mb-2">
            <AlertTriangle className="h-4 w-4" />
            {error instanceof Error ? error.message : 'Analysis failed'}
          </div>
          <button
            onClick={() => { reset(); mutate(scannerName) }}
            className="inline-flex items-center gap-1.5 text-xs text-oss-muted hover:text-oss-text transition-colors"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        </div>
      )}

      {/* Results */}
      {data && !isPending && (
        <AnalysisResults data={data} />
      )}
    </div>
  )
}
