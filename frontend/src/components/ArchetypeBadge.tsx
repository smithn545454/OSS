import { archetypeMeta, antiArchetypeMeta } from '@/lib/archetypeMeta'

interface ArchetypeBadgeProps {
  archetypeId?: string | null
  score?: number | null
  antiArchetype?: string | null
}

export function ArchetypeBadge({ archetypeId, score, antiArchetype }: ArchetypeBadgeProps) {
  if (antiArchetype) {
    const meta = antiArchetypeMeta(antiArchetype)
    return (
      <span
        className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-medium ${meta.badgeClass}`}
        title={meta.thesis}
      >
        ⛔ {meta.shortLabel}
      </span>
    )
  }
  if (!archetypeId) {
    return <span className="text-slate-500">—</span>
  }
  const meta = archetypeMeta(archetypeId)
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-medium ${meta.badgeClass}`}
      title={meta.thesis}
    >
      ★ {meta.shortLabel}
      {score != null && <span className="ml-1 opacity-80">{score.toFixed(0)}%</span>}
    </span>
  )
}
