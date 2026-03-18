import { useState } from 'react'
import { usePageTitle } from '@/hooks/usePageTitle'
import {
  Play,
  CheckCircle, 
  XCircle, 
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Clock,
  RefreshCw,
  ThumbsUp,
  ThumbsDown,
  ArrowRight,
  Activity,
  Shield
} from 'lucide-react'
import { 
  useCalibrationReports, 
  useRunCalibration, 
  useApproveSuggestion, 
  useRejectSuggestion 
} from '@/hooks/useApi'
import type { 
  CalibrationReport, 
  GateAnalysis, 
  ThresholdSuggestion, 
  ScoreBandAnalysis 
} from '@/lib/types'
import clsx from 'clsx'

// ============================================================================
// Summary Header Component
// ============================================================================

interface SummaryHeaderProps {
  report: CalibrationReport
}

function SummaryHeader({ report }: SummaryHeaderProps) {
  const isGoodWinRate = report.win_rate >= 55
  const isGoodReturn = report.avg_return >= 25

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-oss-text">Weekly Summary</h2>
          <p className="text-sm text-oss-muted">
            {report.week_start} — {report.week_end}
          </p>
        </div>
        <span className="text-xs text-oss-muted">
          Generated {new Date(report.generated_at).toLocaleString()}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Positions Closed</p>
          <p className="text-3xl font-bold text-oss-text">{report.positions_closed}</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Win Rate</p>
          <div className="flex items-center gap-2">
            <p className={clsx(
              'text-3xl font-bold',
              isGoodWinRate ? 'text-oss-approve' : 'text-oss-watch'
            )}>
              {report.win_rate.toFixed(1)}%
            </p>
            {isGoodWinRate ? (
              <CheckCircle className="h-5 w-5 text-oss-approve" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-oss-watch" />
            )}
          </div>
          <p className="text-xs text-oss-muted">Target: &gt; 55%</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Avg Return</p>
          <div className="flex items-center gap-2">
            <p className={clsx(
              'text-3xl font-bold',
              isGoodReturn ? 'text-oss-approve' : report.avg_return > 0 ? 'text-oss-watch' : 'text-oss-reject'
            )}>
              {report.avg_return > 0 ? '+' : ''}{report.avg_return.toFixed(1)}%
            </p>
            {isGoodReturn ? (
              <TrendingUp className="h-5 w-5 text-oss-approve" />
            ) : (
              <TrendingDown className="h-5 w-5 text-oss-watch" />
            )}
          </div>
          <p className="text-xs text-oss-muted">Target: &gt; 25%</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Pending Suggestions</p>
          <p className="text-3xl font-bold text-oss-accent">
            {report.suggestions.filter(s => s.status === 'PENDING').length}
          </p>
          <p className="text-xs text-oss-muted">Requires review</p>
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Gate Analysis Table Component
// ============================================================================

interface GateAnalysisTableProps {
  analyses: GateAnalysis[]
}

function GateAnalysisTable({ analyses }: GateAnalysisTableProps) {
  if (analyses.length === 0) {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
        <div className="flex items-center gap-3 mb-4">
          <Shield className="h-5 w-5 text-oss-accent" />
          <h3 className="text-lg font-medium text-oss-text">Gate Effectiveness</h3>
        </div>
        <p className="text-sm text-oss-muted text-center py-4">No gate data available</p>
      </div>
    )
  }

  const getRecommendationBadge = (rec: string) => {
    switch (rec) {
      case 'LOOSEN':
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-oss-watch/10 px-2 py-0.5 text-xs text-oss-watch">
            <TrendingDown className="h-3 w-3" />
            Loosen
          </span>
        )
      case 'TIGHTEN':
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-sky-500/10 px-2 py-0.5 text-xs text-sky-400">
            <TrendingUp className="h-3 w-3" />
            Tighten
          </span>
        )
      default:
        return (
          <span className="rounded-full bg-oss-muted/10 px-2 py-0.5 text-xs text-oss-muted">
            No Change
          </span>
        )
    }
  }

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <div className="flex items-center gap-3 mb-4">
        <Shield className="h-5 w-5 text-oss-accent" />
        <h3 className="text-lg font-medium text-oss-text">Gate Effectiveness</h3>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-oss-border">
              <th className="text-left text-xs font-medium text-oss-muted pb-3">Gate</th>
              <th className="text-right text-xs font-medium text-oss-muted pb-3">Rejections</th>
              <th className="text-right text-xs font-medium text-oss-muted pb-3">Rejection Rate</th>
              <th className="text-right text-xs font-medium text-oss-muted pb-3">False Neg</th>
              <th className="text-right text-xs font-medium text-oss-muted pb-3">Effectiveness</th>
              <th className="text-right text-xs font-medium text-oss-muted pb-3">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-oss-border">
            {analyses.map((analysis) => (
              <tr key={analysis.gate_id} className="hover:bg-oss-bg/50">
                <td className="py-3 text-sm text-oss-text">
                  {analysis.gate_id.replace(/GATE_/g, '').replace(/_/g, ' ')}
                </td>
                <td className="py-3 text-right font-mono text-sm text-oss-text">
                  {analysis.rejection_count}
                </td>
                <td className="py-3 text-right font-mono text-sm text-oss-text">
                  {analysis.rejection_rate.toFixed(1)}%
                </td>
                <td className="py-3 text-right">
                  <span className={clsx(
                    'font-mono text-sm',
                    analysis.false_negative_rate > 10 ? 'text-oss-reject' : 'text-oss-text'
                  )}>
                    {analysis.false_negative_rate.toFixed(1)}%
                  </span>
                </td>
                <td className="py-3 text-right">
                  <span className={clsx(
                    'font-mono text-sm font-medium',
                    analysis.effectiveness_score >= 50 ? 'text-oss-approve' : 
                    analysis.effectiveness_score >= 30 ? 'text-oss-watch' : 'text-oss-reject'
                  )}>
                    {analysis.effectiveness_score.toFixed(1)}
                  </span>
                </td>
                <td className="py-3 text-right">
                  {getRecommendationBadge(analysis.recommendation)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ============================================================================
// Threshold Suggestion Card Component
// ============================================================================

interface ThresholdSuggestionCardProps {
  suggestion: ThresholdSuggestion
  onApprove: () => void
  onReject: () => void
  isApproving: boolean
  isRejecting: boolean
}

function ThresholdSuggestionCard({ 
  suggestion, 
  onApprove, 
  onReject,
  isApproving,
  isRejecting
}: ThresholdSuggestionCardProps) {
  const isPending = suggestion.status === 'PENDING'
  const isApproved = suggestion.status === 'APPROVED'
  const isRejected = suggestion.status === 'REJECTED'

  return (
    <div className={clsx(
      'rounded-xl border p-5',
      isApproved ? 'border-oss-approve/30 bg-oss-approve/5' :
      isRejected ? 'border-oss-muted/30 bg-oss-bg opacity-60' :
      'border-oss-border bg-oss-surface'
    )}>
      <div className="flex items-start justify-between mb-4">
        <div>
          <h4 className="font-medium text-oss-text">
            {suggestion.gate_id.replace(/GATE_/g, '').replace(/_/g, ' ')}
          </h4>
          <p className="text-xs text-oss-muted mt-1">{suggestion.field_path}</p>
        </div>
        {isApproved && (
          <span className="inline-flex items-center gap-1 rounded-full bg-oss-approve/10 px-2 py-0.5 text-xs text-oss-approve">
            <CheckCircle className="h-3 w-3" />
            Approved
          </span>
        )}
        {isRejected && (
          <span className="inline-flex items-center gap-1 rounded-full bg-oss-muted/10 px-2 py-0.5 text-xs text-oss-muted">
            <XCircle className="h-3 w-3" />
            Rejected
          </span>
        )}
      </div>

      <div className="flex items-center gap-4 mb-4">
        <div className="flex-1">
          <p className="text-xs text-oss-muted mb-1">Current</p>
          <p className="font-mono text-lg text-oss-text">{suggestion.current_value}</p>
        </div>
        <ArrowRight className="h-5 w-5 text-oss-muted" />
        <div className="flex-1">
          <p className="text-xs text-oss-muted mb-1">Suggested</p>
          <p className="font-mono text-lg text-oss-accent">{suggestion.suggested_value.toFixed(2)}</p>
        </div>
      </div>

      <div className="rounded-lg bg-oss-bg p-3 mb-4">
        <p className="text-xs text-oss-muted mb-2">Estimated Impact</p>
        <div className="flex items-center gap-4 text-sm">
          <span className="text-oss-text">
            +{suggestion.estimated_impact.additional_approvals} approvals
          </span>
          <span className={clsx(
            suggestion.estimated_impact.estimated_win_rate_change >= 0 
              ? 'text-oss-approve' 
              : 'text-oss-reject'
          )}>
            {suggestion.estimated_impact.estimated_win_rate_change >= 0 ? '+' : ''}
            {suggestion.estimated_impact.estimated_win_rate_change.toFixed(2)}% win rate
          </span>
        </div>
      </div>

      <p className="text-sm text-oss-muted mb-4">{suggestion.reason}</p>

      {isPending && (
        <div className="flex gap-3">
          <button
            onClick={onApprove}
            disabled={isApproving}
            className="flex-1 flex items-center justify-center gap-2 rounded-lg bg-oss-approve px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-oss-approve/90 disabled:opacity-50"
          >
            {isApproving ? (
              <RefreshCw className="h-4 w-4 animate-spin" />
            ) : (
              <ThumbsUp className="h-4 w-4" />
            )}
            Approve
          </button>
          <button
            onClick={onReject}
            disabled={isRejecting}
            className="flex-1 flex items-center justify-center gap-2 rounded-lg border border-oss-border px-4 py-2 text-sm font-medium text-oss-muted transition-colors hover:border-oss-reject hover:text-oss-reject disabled:opacity-50"
          >
            {isRejecting ? (
              <RefreshCw className="h-4 w-4 animate-spin" />
            ) : (
              <ThumbsDown className="h-4 w-4" />
            )}
            Reject
          </button>
        </div>
      )}
    </div>
  )
}

// ============================================================================
// Score Band Chart Component
// ============================================================================

interface ScoreBandChartProps {
  bands: ScoreBandAnalysis[]
}

function ScoreBandChart({ bands }: ScoreBandChartProps) {
  if (bands.length === 0) {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
        <div className="flex items-center gap-3 mb-4">
          <BarChart3 className="h-5 w-5 text-oss-accent" />
          <h3 className="text-lg font-medium text-oss-text">Score Band Performance</h3>
        </div>
        <p className="text-sm text-oss-muted text-center py-4">No score band data available</p>
      </div>
    )
  }

  const maxCount = Math.max(...bands.map(b => b.count), 1)

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <div className="flex items-center gap-3 mb-4">
        <BarChart3 className="h-5 w-5 text-oss-accent" />
        <h3 className="text-lg font-medium text-oss-text">Score Band Performance</h3>
      </div>

      <div className="space-y-4">
        {bands.map((band) => (
          <div key={band.band} className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-oss-text">{band.band}</span>
              <div className="flex items-center gap-4 text-sm">
                <span className="text-oss-muted">{band.count} trades</span>
                <span className={clsx(
                  band.win_rate >= 55 ? 'text-oss-approve' : 
                  band.win_rate >= 45 ? 'text-oss-watch' : 'text-oss-reject'
                )}>
                  {band.win_rate.toFixed(0)}% win
                </span>
                <span className={clsx(
                  band.avg_return >= 0 ? 'text-oss-approve' : 'text-oss-reject'
                )}>
                  {band.avg_return >= 0 ? '+' : ''}{band.avg_return.toFixed(1)}%
                </span>
              </div>
            </div>
            <div className="h-3 rounded-full bg-oss-bg overflow-hidden">
              <div
                className={clsx(
                  'h-full rounded-full transition-all duration-500',
                  band.win_rate >= 55 ? 'bg-oss-approve' : 
                  band.win_rate >= 45 ? 'bg-oss-watch' : 'bg-oss-reject'
                )}
                style={{ width: `${(band.count / maxCount) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="mt-4 pt-4 border-t border-oss-border">
        <p className="text-xs text-oss-muted">
          Expected: Higher score bands should have higher win rates.
          APPROVE threshold at 75, WATCH at 65.
        </p>
      </div>
    </div>
  )
}

// ============================================================================
// Report List Component
// ============================================================================

interface ReportListProps {
  reports: CalibrationReport[]
  selectedReport: CalibrationReport | null
  onSelectReport: (report: CalibrationReport) => void
}

function ReportList({ reports, selectedReport, onSelectReport }: ReportListProps) {
  return (
    <div className="space-y-2">
      {reports.map((report) => (
        <button
          key={report.report_id}
          onClick={() => onSelectReport(report)}
          className={clsx(
            'w-full rounded-lg border p-4 text-left transition-colors',
            selectedReport?.report_id === report.report_id
              ? 'border-oss-accent bg-oss-accent/5'
              : 'border-oss-border bg-oss-surface hover:border-oss-muted'
          )}
        >
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-oss-text">
                {report.week_start} — {report.week_end}
              </p>
              <p className="text-xs text-oss-muted mt-1">
                {report.positions_closed} positions closed
              </p>
            </div>
            <div className="text-right">
              <p className={clsx(
                'text-sm font-medium',
                report.win_rate >= 55 ? 'text-oss-approve' : 'text-oss-watch'
              )}>
                {report.win_rate.toFixed(0)}%
              </p>
              <p className="text-xs text-oss-muted">win rate</p>
            </div>
          </div>
        </button>
      ))}
    </div>
  )
}

// ============================================================================
// Main Calibration Page
// ============================================================================

export default function Calibration() {
  usePageTitle('Calibration')
  const { data: reportsData, isLoading } = useCalibrationReports(10)
  const runCalibration = useRunCalibration()
  const approveSuggestion = useApproveSuggestion()
  const rejectSuggestion = useRejectSuggestion()

  const [selectedReport, setSelectedReport] = useState<CalibrationReport | null>(null)
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [rejectingId, setRejectingId] = useState<string | null>(null)

  const reports = reportsData?.reports || []

  // Auto-select first report
  if (!selectedReport && reports.length > 0 && !isLoading) {
    setSelectedReport(reports[0])
  }

  const handleRunCalibration = async () => {
    try {
      const result = await runCalibration.mutateAsync()
      if (result.report) {
        setSelectedReport(result.report)
      }
    } catch (error) {
      console.error('Failed to run calibration:', error)
    }
  }

  const handleApprove = async (suggestionId: string) => {
    setApprovingId(suggestionId)
    try {
      await approveSuggestion.mutateAsync(suggestionId)
    } finally {
      setApprovingId(null)
    }
  }

  const handleReject = async (suggestionId: string) => {
    setRejectingId(suggestionId)
    try {
      await rejectSuggestion.mutateAsync(suggestionId)
    } finally {
      setRejectingId(null)
    }
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-oss-text">Weekly Calibration</h1>
          <p className="mt-1 text-sm text-oss-muted">
            Analyze gate effectiveness and optimize thresholds
          </p>
        </div>
        <button
          onClick={handleRunCalibration}
          disabled={runCalibration.isPending}
          className="flex items-center gap-2 rounded-lg bg-oss-accent px-4 py-2 text-sm font-medium text-oss-bg transition-colors hover:bg-oss-accent/90 disabled:opacity-50"
        >
          {runCalibration.isPending ? (
            <RefreshCw className="h-4 w-4 animate-spin" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          Run Calibration
        </button>
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-4">
        {/* Report List */}
        <div className="lg:col-span-1">
          <h2 className="mb-4 text-lg font-medium text-oss-text">Reports</h2>
          {isLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-20 animate-pulse rounded-lg border border-oss-border bg-oss-surface"
                />
              ))}
            </div>
          ) : reports.length === 0 ? (
            <div className="rounded-lg border border-oss-border bg-oss-surface p-6 text-center">
              <Clock className="h-8 w-8 mx-auto text-oss-muted mb-2" />
              <p className="text-sm text-oss-muted">No reports yet</p>
              <p className="text-xs text-oss-muted mt-1">
                Run calibration to generate your first report
              </p>
            </div>
          ) : (
            <ReportList
              reports={reports}
              selectedReport={selectedReport}
              onSelectReport={setSelectedReport}
            />
          )}
        </div>

        {/* Report Details */}
        <div className="lg:col-span-3 space-y-6">
          {selectedReport ? (
            <>
              <SummaryHeader report={selectedReport} />
              
              <GateAnalysisTable analyses={selectedReport.gate_analyses} />
              
              {selectedReport.suggestions.length > 0 && (
                <div>
                  <h3 className="text-lg font-medium text-oss-text mb-4">
                    Threshold Suggestions
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {selectedReport.suggestions.map((suggestion) => (
                      <ThresholdSuggestionCard
                        key={suggestion.suggestion_id}
                        suggestion={suggestion}
                        onApprove={() => handleApprove(suggestion.suggestion_id)}
                        onReject={() => handleReject(suggestion.suggestion_id)}
                        isApproving={approvingId === suggestion.suggestion_id}
                        isRejecting={rejectingId === suggestion.suggestion_id}
                      />
                    ))}
                  </div>
                </div>
              )}
              
              <ScoreBandChart bands={selectedReport.score_band_analysis} />
            </>
          ) : (
            <div className="rounded-xl border border-oss-border bg-oss-surface p-12 text-center">
              <Activity className="h-12 w-12 mx-auto text-oss-muted mb-4" />
              <p className="text-oss-muted">
                {isLoading ? 'Loading reports...' : 'Select a report to view details or run a new calibration'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
