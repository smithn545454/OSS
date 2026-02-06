/**
 * Page Header Component
 * 
 * Per spec section 5.1: Title + System Status Indicator
 */

import clsx from 'clsx'

interface PageHeaderProps {
  hasAnomaly: boolean
}

export function PageHeader({ hasAnomaly }: PageHeaderProps) {
  return (
    <div className="mb-6">
      <div className="flex items-center gap-3">
        <h1 
          className="text-[28px] font-semibold tracking-tight"
          style={{ color: 'var(--text-primary)' }}
        >
          Pipeline Monitor
        </h1>
        
        {/* Status Indicator */}
        <div
          className={clsx(
            'inline-flex items-center gap-2 rounded px-2.5 py-1',
            hasAnomaly 
              ? 'bg-[var(--color-error-bg)]' 
              : 'bg-[var(--color-success-bg)]'
          )}
        >
          <span
            className={clsx(
              'status-dot',
              hasAnomaly ? 'anomaly' : 'healthy'
            )}
          />
          <span
            className={clsx(
              'text-[11px] font-medium uppercase tracking-wide',
              hasAnomaly 
                ? 'text-[var(--color-error-text)]' 
                : 'text-[var(--color-success-text)]'
            )}
          >
            {hasAnomaly ? 'Anomaly Detected' : 'System Healthy'}
          </span>
        </div>
      </div>
      
      <p 
        className="mt-2 text-[14px]"
        style={{ color: 'var(--text-disabled)' }}
      >
        Track pipeline runs and view stage-level telemetry
      </p>
    </div>
  )
}
