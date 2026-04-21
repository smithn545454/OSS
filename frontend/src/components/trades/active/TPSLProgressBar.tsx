import clsx from 'clsx'

interface Props {
  kind: 'tp' | 'sl'
  progress: number | null // 0-100 or null when thesis missing
  label?: string
}

export default function TPSLProgressBar({ kind, progress, label }: Props) {
  if (progress === null) {
    return (
      <div className="text-xs text-oss-muted italic">
        Thesis pending
      </div>
    )
  }

  const clamped = Math.max(0, Math.min(100, progress))
  const isTp = kind === 'tp'

  // Track color: neutral grey. Fill intensifies as progress climbs.
  const fillColor = isTp
    ? clamped >= 80
      ? 'bg-oss-approve'
      : 'bg-oss-approve/60'
    : clamped >= 75
      ? 'bg-oss-reject'
      : 'bg-oss-watch/80'

  return (
    <div className="space-y-1">
      {label && (
        <div className="flex justify-between items-center text-xs">
          <span className="text-oss-muted">{label}</span>
          <span className={clsx('font-mono', isTp ? 'text-oss-approve-text' : 'text-oss-reject-text')}>
            {clamped.toFixed(0)}%
          </span>
        </div>
      )}
      <div className="h-1.5 rounded-full bg-oss-surface overflow-hidden">
        <div
          className={clsx('h-full rounded-full transition-all', fillColor)}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  )
}
