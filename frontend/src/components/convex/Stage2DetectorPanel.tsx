import { CheckCircle2, XCircle, AlertCircle, Sparkles } from 'lucide-react'
import clsx from 'clsx'

import type { ConvexStagePayload } from '@/lib/convexTypes'

/**
 * Renders Stage 2 (Catalyst Layer) with the firing detector surfaced
 * prominently. Shows per-detector pass/fail + the underlying signal
 * performance so the user can see WHY a catalyst fired (or didn't).
 *
 * The four detectors (date_known / state_based / sympathy / unusual_volume)
 * each populate a sub-criterion. Only the first three contribute to the
 * Stage 2 verdict — UV is telemetry-only (informs Stage 4 Smart Money flag).
 */
interface DetectorRow {
  key: string
  label: string
  detected: boolean
  strength?: number | null
  signals?: Array<{ name: string; passed: boolean; value: string | number }>
  note?: string
  isCatalyst: boolean
}

export function Stage2DetectorPanel({ stage }: { stage: ConvexStagePayload }) {
  const detectors = parseDetectors(stage.criteria)
  const fired = detectors.filter((d) => d.detected && d.isCatalyst)
  const primary = pickStrongest(fired)

  return (
    <div className="space-y-4">
      <p className="text-sm leading-relaxed text-oss-text">{stage.summary}</p>

      {primary && (
        <div className="rounded-lg border border-oss-approve/40 bg-oss-approve/5 p-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-oss-approve">
            <Sparkles className="h-3.5 w-3.5" /> Firing detector: {primary.label}
          </div>
          {primary.strength != null && (
            <p className="mt-1 text-xs text-oss-muted">
              Strength <span className="font-mono text-oss-text">{primary.strength.toFixed(2)}</span>
              {primary.note ? ` — ${primary.note}` : ''}
            </p>
          )}
        </div>
      )}

      <div className="space-y-2">
        {detectors.map((d) => (
          <DetectorCard key={d.key} detector={d} />
        ))}
      </div>
    </div>
  )
}

function DetectorCard({ detector }: { detector: DetectorRow }) {
  const isInformational = !detector.isCatalyst
  return (
    <div
      className={clsx(
        'rounded-lg border p-3',
        detector.detected
          ? 'border-oss-approve/40 bg-oss-approve/5'
          : isInformational
            ? 'border-oss-border/60 bg-oss-bg/30'
            : 'border-oss-border bg-oss-surface',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          {detector.detected ? (
            <CheckCircle2 className="h-4 w-4 text-oss-approve" />
          ) : isInformational ? (
            <AlertCircle className="h-4 w-4 text-oss-muted" />
          ) : (
            <XCircle className="h-4 w-4 text-oss-muted" />
          )}
          <div>
            <p className="text-sm font-medium text-oss-text">{detector.label}</p>
            {isInformational && (
              <p className="text-[10px] uppercase tracking-wide text-oss-muted">
                Telemetry only — informs Stage 4
              </p>
            )}
          </div>
        </div>
        {detector.strength != null && detector.strength > 0 && (
          <span className="font-mono text-xs text-oss-muted">
            {detector.strength.toFixed(2)}
          </span>
        )}
      </div>

      {detector.note && (
        <p className="mt-2 text-xs text-oss-muted">{detector.note}</p>
      )}

      {detector.signals && detector.signals.length > 0 && (
        <div className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
          {detector.signals.map((s, i) => (
            <div
              key={i}
              className="flex items-center justify-between rounded border border-oss-border/40 bg-oss-bg/40 px-2 py-1 text-xs"
            >
              <div className="flex items-center gap-1.5">
                {s.passed ? (
                  <CheckCircle2 className="h-3 w-3 text-oss-approve" />
                ) : (
                  <XCircle className="h-3 w-3 text-oss-muted" />
                )}
                <span className="text-oss-muted">{s.name}</span>
              </div>
              <span className="font-mono text-oss-text">{s.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function parseDetectors(criteria: Record<string, unknown>): DetectorRow[] {
  return [
    parseDateKnown(criteria.date_known as Record<string, unknown> | undefined),
    parseCompression(criteria.state_based as Record<string, unknown> | undefined),
    parseSympathy(criteria.sympathy as Record<string, unknown> | undefined),
    parseUVTelemetry(
      (criteria.unusual_volume_telemetry ?? criteria.unusual_volume) as
        | Record<string, unknown>
        | undefined,
    ),
  ]
}

function parseDateKnown(raw: Record<string, unknown> | undefined): DetectorRow {
  const detected = Boolean(raw?.detected)
  const days = num(raw?.days_to_event)
  const eventType = str(raw?.event_type)
  return {
    key: 'date_known',
    label: 'Date-Known Catalyst',
    detected,
    strength: num(raw?.strength),
    isCatalyst: true,
    note: detected
      ? `${eventType ?? 'Event'} in ${days ?? '?'} days`
      : 'No scheduled event in window',
    signals: detected
      ? [
          { name: 'Days to event', passed: true, value: days ?? '—' },
          { name: 'Event type', passed: true, value: eventType ?? '—' },
        ]
      : undefined,
  }
}

function parseCompression(raw: Record<string, unknown> | undefined): DetectorRow {
  const detected = Boolean(raw?.detected)
  const signalCount = num(raw?.signals_active) ?? num(raw?.signal_count)
  const required = num(raw?.signals_required) ?? 2

  // Stage 2 compression returns a `signals` dict like:
  //   bbw_pct_low: bool, atr_ratio_low: bool, range_pct_low: bool, etc.
  const signalsObj = (raw?.signals ?? {}) as Record<string, unknown>
  const signalsList = Object.entries(signalsObj).map(([name, val]) => ({
    name: humanise(name),
    passed: Boolean(val),
    value: val ? '✓' : '✗',
  }))

  return {
    key: 'state_based',
    label: 'Compression (Coiled Spring)',
    detected,
    strength: num(raw?.strength),
    isCatalyst: true,
    note: detected
      ? `${signalCount}-of-${signalsList.length || 5} compression signals active (≥${required} required)`
      : 'Insufficient compression signals',
    signals: signalsList.length > 0 ? signalsList : undefined,
  }
}

function parseSympathy(raw: Record<string, unknown> | undefined): DetectorRow {
  const detected = Boolean(raw?.detected)
  const peer = str(raw?.peer_ticker)
  const peerMove = num(raw?.peer_move_pct)
  return {
    key: 'sympathy',
    label: 'Sector Sympathy',
    detected,
    strength: num(raw?.strength),
    isCatalyst: true,
    note: detected
      ? `Peer ${peer ?? '?'} reacted ${peerMove ? peerMove.toFixed(1) : '?'}%`
      : 'No qualifying sector peer reaction',
    signals: detected
      ? [
          { name: 'Peer', passed: true, value: peer ?? '—' },
          { name: 'Peer move %', passed: true, value: peerMove ? `${peerMove.toFixed(1)}%` : '—' },
        ]
      : undefined,
  }
}

function parseUVTelemetry(raw: Record<string, unknown> | undefined): DetectorRow {
  const detected = Boolean(raw?.detected)
  const ratio = num(raw?.volume_ratio) ?? num(raw?.magnitude)
  const skew = str(raw?.directional_skew)
  return {
    key: 'unusual_volume',
    label: 'Unusual Volume',
    detected,
    strength: num(raw?.strength),
    isCatalyst: false,
    note: detected
      ? `Today's options volume ${ratio ? ratio.toFixed(1) + '×' : ''} baseline${skew ? ` · ${humanise(skew)}` : ''}`
      : 'Today within normal options-volume range',
    signals:
      detected && ratio
        ? [
            { name: 'Volume ratio', passed: true, value: `${ratio.toFixed(1)}×` },
            { name: 'Directional skew', passed: true, value: humanise(skew ?? '—') },
          ]
        : undefined,
  }
}

function pickStrongest(detectors: DetectorRow[]): DetectorRow | null {
  if (detectors.length === 0) return null
  return [...detectors].sort(
    (a, b) => (b.strength ?? 0) - (a.strength ?? 0),
  )[0]
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

function humanise(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
