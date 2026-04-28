import clsx from 'clsx'
import { Star, Zap, Shield } from 'lucide-react'

import type { ConvexStagePayload } from '@/lib/convexTypes'

interface MenuContract {
  option_ticker: string
  option_type: string
  strike: number
  expiry: string
  dte: number
  delta: number
  bid: number
  ask: number
  open_interest: number
  volume: number
  mid?: number
  spread_pct?: number
}

interface MenuEntry {
  label: 'primary' | 'stretch' | 'defensive' | string
  rationale: string
  contract: MenuContract
}

const LABEL_META: Record<string, { display: string; icon: React.ElementType; color: string }> = {
  primary: {
    display: 'Primary',
    icon: Star,
    color: 'border-oss-accent/40 bg-oss-accent/5',
  },
  stretch: {
    display: 'Stretch (More Leverage)',
    icon: Zap,
    color: 'border-oss-watch/40 bg-oss-watch/5',
  },
  defensive: {
    display: 'Defensive (More Time)',
    icon: Shield,
    color: 'border-oss-approve/40 bg-oss-approve/5',
  },
}

/**
 * Renders the Stage 4 contract menu — primary recommendation plus 0-2
 * variants (stretch / defensive) when the chain has qualifying alternatives.
 *
 * Each card shows the contract's strike/expiry/delta/spread plus the
 * rationale string explaining what tradeoff this slot represents.
 */
export function Stage4MenuPanel({ stage }: { stage: ConvexStagePayload }) {
  const menu = parseMenu(stage.criteria)
  const expectedTerminus = num(stage.criteria.expected_move_terminus)

  if (menu.length === 0) {
    // Backwards compat: criteria has selected_contract but no menu
    // (older payloads). Render a single card.
    const single = parseSingleContract(stage.criteria)
    if (!single) {
      return <p className="text-sm text-oss-muted">{stage.summary}</p>
    }
    return (
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-oss-text">{stage.summary}</p>
        <ContractCard
          entry={{
            label: 'primary',
            rationale: str(stage.criteria.strike_rationale) ?? stage.summary,
            contract: single,
          }}
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-sm leading-relaxed text-oss-text">{stage.summary}</p>

      {expectedTerminus != null && (
        <div className="rounded border border-oss-border/40 bg-oss-bg/30 p-2 text-xs text-oss-muted">
          Expected move terminus: <span className="font-mono text-oss-text">${expectedTerminus.toFixed(2)}</span>
        </div>
      )}

      <div className="space-y-3">
        {menu.map((entry) => (
          <ContractCard key={entry.contract.option_ticker} entry={entry} />
        ))}
      </div>
    </div>
  )
}

function ContractCard({ entry }: { entry: MenuEntry }) {
  const meta = LABEL_META[entry.label] ?? {
    display: entry.label,
    icon: Star,
    color: 'border-oss-border bg-oss-surface',
  }
  const Icon = meta.icon
  const c = entry.contract
  const mid = c.mid ?? (c.bid > 0 && c.ask > 0 ? (c.bid + c.ask) / 2 : null)
  const spread = c.spread_pct ?? (mid && mid > 0 ? ((c.ask - c.bid) / mid) * 100 : null)

  return (
    <div className={clsx('rounded-lg border p-4', meta.color)}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-oss-text" />
          <span className="text-xs font-bold uppercase tracking-wide text-oss-text">
            {meta.display}
          </span>
        </div>
        <div className="text-right text-xs text-oss-muted">
          <div className="font-mono text-oss-text">
            {c.option_type} ${c.strike.toFixed(2)}
          </div>
          <div>
            {c.expiry} · Δ {c.delta.toFixed(2)} · {c.dte} DTE
          </div>
        </div>
      </div>

      <p className="mt-2 text-sm leading-relaxed text-oss-text">{entry.rationale}</p>

      <dl className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <Stat label="Mid" value={mid != null ? `$${mid.toFixed(2)}` : '—'} />
        <Stat label="Spread" value={spread != null ? `${spread.toFixed(1)}%` : '—'} />
        <Stat label="OI" value={c.open_interest.toLocaleString()} />
        <Stat label="Volume" value={c.volume.toLocaleString()} />
      </dl>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-oss-muted">{label}</dt>
      <dd className="font-mono text-oss-text">{value}</dd>
    </div>
  )
}

function parseMenu(criteria: Record<string, unknown>): MenuEntry[] {
  const raw = criteria.contract_menu
  if (!Array.isArray(raw)) return []
  const out: MenuEntry[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const entry = item as Record<string, unknown>
    const label = str(entry.label)
    const rationale = str(entry.rationale)
    const contract = parseContract(entry.contract as Record<string, unknown> | undefined)
    if (!label || !rationale || !contract) continue
    out.push({ label, rationale, contract })
  }
  return out
}

function parseSingleContract(criteria: Record<string, unknown>): MenuContract | null {
  return parseContract(criteria.selected_contract as Record<string, unknown> | undefined)
}

function parseContract(raw: Record<string, unknown> | undefined): MenuContract | null {
  if (!raw) return null
  const option_ticker = str(raw.option_ticker)
  const option_type = str(raw.option_type)
  const strike = num(raw.strike)
  const expiry = str(raw.expiry)
  const dte = num(raw.dte)
  const delta = num(raw.delta)
  if (!option_ticker || !option_type || strike == null || !expiry || dte == null || delta == null) {
    return null
  }
  return {
    option_ticker,
    option_type,
    strike,
    expiry,
    dte,
    delta,
    bid: num(raw.bid) ?? 0,
    ask: num(raw.ask) ?? 0,
    open_interest: num(raw.open_interest) ?? 0,
    volume: num(raw.volume) ?? 0,
    mid: num(raw.mid) ?? undefined,
    spread_pct: num(raw.spread_pct) ?? undefined,
  }
}

function num(v: unknown): number | null {
  if (typeof v === 'number') return v
  if (typeof v === 'string') {
    const parsed = parseFloat(v)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function str(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}
