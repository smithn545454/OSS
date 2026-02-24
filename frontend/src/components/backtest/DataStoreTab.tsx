import { useState } from 'react'
import {
  Database,
  CheckCircle2,
  XCircle,
  AlertCircle,
  RefreshCw,
  HardDrive,
  Calendar,
  FileText,
  Play,
} from 'lucide-react'
import clsx from 'clsx'
import { useDataStoreStatus, useValidateDataStore } from '@/hooks/useApi'
import type { DatasetStatus, DatasetStatusLevel, ValidationCheck } from '@/lib/types'

function StatusBadge({ status }: { status: DatasetStatusLevel }) {
  const config: Record<DatasetStatusLevel, { label: string; className: string }> = {
    complete: { label: 'Complete', className: 'bg-oss-approve/10 text-oss-approve border-oss-approve/20' },
    partial: { label: 'Partial', className: 'bg-oss-watch/10 text-oss-watch border-oss-watch/20' },
    missing: { label: 'Missing', className: 'bg-oss-reject/10 text-oss-reject border-oss-reject/20' },
    error: { label: 'Error', className: 'bg-oss-reject/10 text-oss-reject border-oss-reject/20' },
    unknown: { label: 'Unknown', className: 'bg-oss-muted/10 text-oss-muted border-oss-muted/20' },
  }
  const c = config[status] || config.unknown

  return (
    <span className={clsx('inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium', c.className)}>
      {c.label}
    </span>
  )
}

function MetricCard({
  label,
  value,
  icon: Icon,
  subtitle,
}: {
  label: string
  value: string | number
  icon: React.ElementType
  subtitle?: string
}) {
  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <div className="flex items-center gap-2 mb-2">
        <Icon className="h-4 w-4 text-oss-muted" />
        <p className="text-xs text-oss-muted">{label}</p>
      </div>
      <p className="text-2xl font-bold text-oss-text">{value}</p>
      {subtitle && <p className="text-xs text-oss-muted mt-1">{subtitle}</p>}
    </div>
  )
}

function DatasetRow({ dataset }: { dataset: DatasetStatus }) {
  return (
    <tr className="border-b border-oss-border last:border-b-0">
      <td className="py-3 px-4">
        <div className="font-medium text-sm text-oss-text">{dataset.name}</div>
        <div className="text-xs text-oss-muted font-mono">{dataset.prefix}</div>
      </td>
      <td className="py-3 px-4">
        <StatusBadge status={dataset.status} />
      </td>
      <td className="py-3 px-4 text-sm text-oss-text tabular-nums">
        {dataset.file_count.toLocaleString()}
      </td>
      <td className="py-3 px-4 text-sm text-oss-text tabular-nums">
        {dataset.date_count > 0 ? dataset.date_count.toLocaleString() : '—'}
      </td>
      <td className="py-3 px-4 text-xs text-oss-muted font-mono">
        {dataset.earliest_date && dataset.latest_date ? (
          <>
            {dataset.earliest_date}
            <span className="mx-1 text-oss-border">→</span>
            {dataset.latest_date}
          </>
        ) : (
          '—'
        )}
      </td>
      <td className="py-3 px-4 text-sm text-oss-text tabular-nums text-right">
        {dataset.total_size_mb > 0 ? `${dataset.total_size_mb.toFixed(1)} MB` : '—'}
      </td>
    </tr>
  )
}

function ValidationPanel({
  checks,
  passed,
  isRunning,
  onRun,
}: {
  checks: ValidationCheck[] | null
  passed: boolean | null
  isRunning: boolean
  onRun: () => void
}) {
  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-oss-text">Integrity Checks</h3>
        <button
          onClick={onRun}
          disabled={isRunning}
          className="flex items-center gap-2 rounded-lg bg-oss-accent px-4 py-2 text-sm font-medium text-white hover:bg-oss-accent/90 disabled:opacity-50 transition-colors"
        >
          {isRunning ? (
            <RefreshCw className="h-4 w-4 animate-spin" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          {isRunning ? 'Running...' : 'Run Validation'}
        </button>
      </div>

      {passed !== null && (
        <div
          className={clsx(
            'flex items-center gap-2 rounded-lg px-3 py-2 mb-4 text-sm',
            passed
              ? 'bg-oss-approve/10 text-oss-approve border border-oss-approve/20'
              : 'bg-oss-reject/10 text-oss-reject border border-oss-reject/20'
          )}
        >
          {passed ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : (
            <XCircle className="h-4 w-4" />
          )}
          {passed ? 'All checks passed' : 'Some checks failed'}
        </div>
      )}

      {checks && (
        <div className="space-y-2">
          {checks.map((check) => (
            <div
              key={check.check_name}
              className="flex items-start gap-3 rounded-lg border border-oss-border p-3"
            >
              {check.passed ? (
                <CheckCircle2 className="h-4 w-4 text-oss-approve mt-0.5 shrink-0" />
              ) : (
                <XCircle className="h-4 w-4 text-oss-reject mt-0.5 shrink-0" />
              )}
              <div className="min-w-0">
                <p className="text-sm font-medium text-oss-text">
                  {check.check_name.replace(/_/g, ' ')}
                </p>
                <p className="text-xs text-oss-muted mt-0.5">{check.message}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {!checks && !isRunning && (
        <p className="text-sm text-oss-muted">Click "Run Validation" to check data integrity.</p>
      )}
    </div>
  )
}

export default function DataStoreTab() {
  const { data: status, isLoading, error } = useDataStoreStatus()
  const validateMutation = useValidateDataStore()
  const [validationResult, setValidationResult] = useState<{
    passed: boolean
    checks: ValidationCheck[]
  } | null>(null)

  const handleValidate = async () => {
    try {
      const result = await validateMutation.mutateAsync()
      setValidationResult({ passed: result.passed, checks: result.checks })
    } catch {
      // Error handled by mutation state
    }
  }

  if (isLoading) {
    return (
      <div className="rounded-lg border border-oss-border bg-oss-surface p-12 text-center">
        <RefreshCw className="h-6 w-6 mx-auto mb-2 animate-spin text-oss-accent" />
        <p className="text-sm text-oss-muted">Loading data store status...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-lg border border-oss-reject/30 bg-oss-reject/5 p-6 text-center">
        <AlertCircle className="h-8 w-8 text-oss-reject mx-auto mb-2" />
        <p className="text-sm text-oss-text">Failed to load data store status</p>
        <p className="text-xs text-oss-muted mt-1">{(error as Error).message}</p>
      </div>
    )
  }

  if (!status) return null

  const totalDates = status.datasets.reduce((sum, d) => sum + d.date_count, 0)
  const totalFiles = status.datasets.reduce((sum, d) => sum + d.file_count, 0)
  const datasetsComplete = status.datasets.filter((d) => d.status === 'complete').length

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <MetricCard
          label="Overall Status"
          value={status.overall_status === 'ready' ? 'Ready' : 'Incomplete'}
          icon={Database}
          subtitle={`${datasetsComplete}/${status.datasets.length} datasets complete`}
        />
        <MetricCard
          label="Total Size"
          value={`${status.total_size_mb.toFixed(1)} MB`}
          icon={HardDrive}
          subtitle={`${totalFiles.toLocaleString()} files`}
        />
        <MetricCard
          label="Trading Days"
          value={totalDates.toLocaleString()}
          icon={Calendar}
          subtitle="Across all datasets"
        />
        <MetricCard
          label="Bucket"
          value={status.bucket.split('-').slice(-1)[0].slice(0, 8) + '...'}
          icon={FileText}
          subtitle={status.bucket}
        />
      </div>

      {/* Dataset Inventory Table */}
      <div className="rounded-xl border border-oss-border bg-oss-surface overflow-hidden">
        <div className="px-6 py-4 border-b border-oss-border">
          <h3 className="text-lg font-semibold text-oss-text">Dataset Inventory</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-oss-border bg-oss-bg/50">
                <th className="py-2 px-4 text-left text-xs font-medium text-oss-muted uppercase tracking-wider">
                  Dataset
                </th>
                <th className="py-2 px-4 text-left text-xs font-medium text-oss-muted uppercase tracking-wider">
                  Status
                </th>
                <th className="py-2 px-4 text-left text-xs font-medium text-oss-muted uppercase tracking-wider">
                  Files
                </th>
                <th className="py-2 px-4 text-left text-xs font-medium text-oss-muted uppercase tracking-wider">
                  Dates
                </th>
                <th className="py-2 px-4 text-left text-xs font-medium text-oss-muted uppercase tracking-wider">
                  Date Range
                </th>
                <th className="py-2 px-4 text-right text-xs font-medium text-oss-muted uppercase tracking-wider">
                  Size
                </th>
              </tr>
            </thead>
            <tbody>
              {status.datasets.map((dataset) => (
                <DatasetRow key={dataset.prefix} dataset={dataset} />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Validation Panel */}
      <ValidationPanel
        checks={validationResult?.checks ?? null}
        passed={validationResult?.passed ?? null}
        isRunning={validateMutation.isPending}
        onRun={handleValidate}
      />
    </div>
  )
}
