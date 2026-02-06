/**
 * Recent Runs Sidebar Component
 * 
 * Per spec section 5.4: Scrollable list of pipeline runs with selection
 */

import { AlertTriangle } from 'lucide-react'
import clsx from 'clsx'
import type { PipelineRunListItem } from '@/lib/types'

interface RecentRunsSidebarProps {
  runs: PipelineRunListItem[]
  selectedRunId: string | null
  onSelectRun: (run: PipelineRunListItem) => void
  isLoading: boolean
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleString('en-US', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function formatScannerType(type: string | null): string {
  if (!type) return 'Mixed'
  const labels: Record<string, string> = {
    UNUSUAL_VOLUME: 'Unusual Volume',
    BREAKOUT: 'Breakout Detection',
    BREAKDOWN: 'Breakdown Detection',
    COMPRESSION_EXPANSION: 'Compression',
    CHEAP_OPTIONS: 'Cheap Options',
  }
  return labels[type] || type
}

function formatNumber(n: number): string {
  return n.toLocaleString()
}

export function RecentRunsSidebar({
  runs,
  selectedRunId,
  onSelectRun,
  isLoading,
}: RecentRunsSidebarProps) {
  return (
    <div className="w-80 flex-shrink-0">
      <h2 
        className="text-[14px] font-semibold mb-4"
        style={{ color: 'var(--text-primary)' }}
      >
        Recent Runs
      </h2>

      {/* Run List */}
      <div 
        className="flex flex-col gap-2 overflow-y-auto pr-2"
        style={{ maxHeight: 'calc(100vh - 400px)' }}
      >
        {isLoading ? (
          // Loading skeletons
          Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="h-24 rounded-lg animate-pulse"
              style={{ background: 'var(--bg-elevated)' }}
            />
          ))
        ) : runs.length === 0 ? (
          // Empty state
          <div 
            className="text-center py-10"
            style={{ color: 'var(--text-muted)' }}
          >
            <p className="text-[13px]">No pipeline runs yet</p>
          </div>
        ) : (
          // Run cards
          runs.map(run => {
            const isSelected = selectedRunId === run.id
            const hasAnomaly = run.status === 'anomaly'

            return (
              <button
                key={run.id}
                onClick={() => onSelectRun(run)}
                className={clsx(
                  'run-card text-left w-full',
                  isSelected && 'selected'
                )}
              >
                {/* Timestamp Row */}
                <div className="flex items-center justify-between mb-1.5">
                  <span 
                    className="text-[12px]"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    {formatTimestamp(run.timestamp)}
                  </span>
                  {hasAnomaly && (
                    <AlertTriangle 
                      className="w-4 h-4" 
                      style={{ color: 'var(--color-error-text)' }}
                    />
                  )}
                </div>

                {/* Scanner Name */}
                <div 
                  className="text-[13px] font-medium mb-1"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  {formatScannerType(run.scanner_type)}
                </div>

                {/* Stats Row */}
                <div 
                  className="flex gap-4 text-[11px]"
                  style={{ color: 'var(--text-disabled)' }}
                >
                  <span>{formatNumber(run.total_contracts)} contracts</span>
                  <span style={{ color: 'var(--color-success-text)' }}>
                    {formatNumber(run.approved_count)} approved
                  </span>
                </div>
              </button>
            )
          })
        )}
      </div>
    </div>
  )
}
