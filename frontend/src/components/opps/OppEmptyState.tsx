import clsx from 'clsx'

interface OppEmptyStateProps {
  type: 'loading' | 'error' | 'empty'
  message?: string
  onRetry?: () => void
}

export function OppEmptyState({ type, message, onRetry }: OppEmptyStateProps) {
  if (type === 'loading') {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <div className="text-3xl mb-3 animate-pulse">📊</div>
        <p className="text-sm text-[var(--text-muted)]">Loading opportunities...</p>
      </div>
    )
  }

  if (type === 'error') {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center rounded-lg bg-[var(--color-error-bg)] border border-[var(--color-error)]">
        <div className="text-3xl mb-3">⚠️</div>
        <h3 className="text-base font-semibold text-[var(--color-error-text)] mb-1">
          Unable to Load Opportunities
        </h3>
        <p className="text-sm text-[var(--text-muted)] mb-4">
          {message || 'An error occurred while fetching data.'}
        </p>
        {onRetry && (
          <button
            onClick={onRetry}
            className={clsx(
              'px-4 py-2 rounded text-sm font-medium',
              'bg-[var(--color-error)] text-white',
              'hover:opacity-90 transition-opacity',
            )}
          >
            Retry
          </button>
        )}
      </div>
    )
  }

  // empty
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center rounded-lg bg-[var(--bg-secondary)] border border-[var(--border-default)]">
      <div className="text-4xl mb-4 opacity-50">🔍</div>
      <h3 className="text-lg font-semibold text-[var(--text-primary)] mb-2">
        No Active Opportunities
      </h3>
      <p className="text-sm text-[var(--text-muted)] max-w-xs mx-auto mb-4">
        No contracts have passed all evaluation gates yet. The pipeline will continue scanning.
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className={clsx(
            'px-5 py-2.5 rounded-md text-sm font-semibold',
            'bg-[var(--accent-primary)] text-[var(--bg-primary)]',
            'hover:opacity-90 transition-opacity',
          )}
        >
          Check for Updates
        </button>
      )}
    </div>
  )
}
