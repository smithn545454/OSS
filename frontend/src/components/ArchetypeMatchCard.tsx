import { archetypeMeta, antiArchetypeMeta } from '@/lib/archetypeMeta'

interface ArchetypeMatchCardProps {
  archetypeId?: string | null
  matchScore?: number | null
  allFits?: Record<string, number> | null
  antiArchetypeTriggered?: string | null
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-slate-800/40 px-2 py-1">
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="font-mono text-sm text-slate-100">{value}</div>
    </div>
  )
}

function AllFitsTable({
  fits,
  highlight,
}: {
  fits: Record<string, number>
  highlight?: string | null
}) {
  const rows = Object.entries(fits).sort((a, b) => b[1] - a[1])
  if (rows.length === 0) return null
  return (
    <div className="mt-3">
      <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-400">
        All archetype fits
      </div>
      <div className="space-y-0.5">
        {rows.map(([id, fit]) => {
          const meta = archetypeMeta(id)
          const isBest = id === highlight
          return (
            <div
              key={id}
              className={`flex items-center justify-between rounded px-2 py-0.5 text-xs ${
                isBest ? 'bg-slate-800/80' : 'bg-slate-800/20'
              }`}
            >
              <span className={isBest ? meta.color : 'text-slate-300'}>
                {isBest && '★ '}
                {meta.shortLabel}
              </span>
              <span className="font-mono text-slate-200">{fit.toFixed(0)}%</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function ArchetypeMatchCard({
  archetypeId,
  matchScore,
  allFits,
  antiArchetypeTriggered,
}: ArchetypeMatchCardProps) {
  if (antiArchetypeTriggered) {
    const meta = antiArchetypeMeta(antiArchetypeTriggered)
    return (
      <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4">
        <div className="mb-1 flex items-center gap-2 text-red-300">
          <span className="text-lg">⛔</span>
          <h3 className="text-sm font-semibold uppercase tracking-wide">
            Anti-Archetype Triggered
          </h3>
        </div>
        <p className="font-bold text-red-200">{meta.label}</p>
        <p className="mt-1 text-sm text-slate-300">{meta.thesis}</p>
        <div className="mt-3 grid grid-cols-3 gap-2">
          <Stat
            label="HR200 rate"
            value={`${(meta.historicalHr200Rate * 100).toFixed(1)}%`}
          />
          <Stat
            label="Win rate"
            value={`${(meta.historicalWinRate * 100).toFixed(0)}%`}
          />
          <Stat label="Sample" value={`n=${meta.historicalN}`} />
        </div>
      </div>
    )
  }

  const hasMatch = archetypeId && matchScore != null

  if (!hasMatch) {
    const hasFits = allFits && Object.keys(allFits).length > 0
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-4">
        <h3 className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-300">
          Archetype Match
        </h3>
        <p className="text-slate-400">
          {hasFits
            ? 'No archetype met its fit threshold for this trade.'
            : 'No specific archetype matched (pre-v4.1.0 evaluation).'}
        </p>
        {hasFits && <AllFitsTable fits={allFits!} />}
      </div>
    )
  }

  const meta = archetypeMeta(archetypeId)
  return (
    <div className={`rounded-lg border p-4 ${meta.badgeClass}`}>
      <div className={`mb-1 flex items-center gap-2 ${meta.color}`}>
        <span className="text-lg">★</span>
        <h3 className="text-sm font-semibold uppercase tracking-wide">
          Archetype Match
        </h3>
      </div>
      <p className={`text-lg font-bold ${meta.color}`}>{meta.label}</p>
      <div className="mt-2 flex items-baseline gap-2">
        <span className={`font-mono text-3xl ${meta.color}`}>
          {matchScore!.toFixed(0)}
        </span>
        <span className="text-xs text-slate-400">/ 100 fit</span>
      </div>
      <p className="mt-2 text-sm text-slate-300">{meta.thesis}</p>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <Stat
          label="HR200 rate"
          value={`${(meta.historicalHr200Rate * 100).toFixed(1)}%`}
        />
        <Stat
          label="Win rate"
          value={`${(meta.historicalWinRate * 100).toFixed(0)}%`}
        />
        <Stat label="Sample" value={`n=${meta.historicalN}`} />
      </div>
      {allFits && <AllFitsTable fits={allFits} highlight={archetypeId} />}
    </div>
  )
}
