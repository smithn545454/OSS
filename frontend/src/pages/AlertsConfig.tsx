import { useState, useCallback, useEffect } from 'react'
import { usePageTitle } from '@/hooks/usePageTitle'
import {
  Bell,
  Save,
  ChevronDown,
  ChevronRight,
  Send,
  Trash2,
  Plus,
  BarChart3,
  Target,
  Clock,
  History,
} from 'lucide-react'
import {
  useAlertConfig,
  useUpdateAlertConfig,
  useAlertPreview,
  useAlertHistory,
  useTestAlert,
} from '@/hooks/useApi'
import type { AlertConfig, WebhookChannel } from '@/lib/types'
import clsx from 'clsx'

// v5 scoring thresholds (mirrors app/services/slack.py constants).
const V5_HR_FLOOR = 7.0
const V5_HR_TIER1 = 14.0
const V5_P_FLOOR = 50.0

// ============================================================================
// Section Component
// ============================================================================

function Section({
  title,
  icon,
  children,
  defaultOpen = true,
}: {
  title: string
  icon: React.ReactNode
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen)

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between p-4 text-left"
      >
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-oss-bg p-2 text-oss-accent">{icon}</div>
          <span className="font-medium text-oss-text">{title}</span>
        </div>
        {isOpen ? (
          <ChevronDown className="h-5 w-5 text-oss-muted" />
        ) : (
          <ChevronRight className="h-5 w-5 text-oss-muted" />
        )}
      </button>
      {isOpen && <div className="border-t border-oss-border p-4">{children}</div>}
    </div>
  )
}

// ============================================================================
// HR Conviction Slider — the grand-slam knob. Displays v5 floor + TIER_1
// marks so the user knows where their threshold sits in system terms.
// ============================================================================

function HrConvictionSlider({
  value,
  onChange,
}: {
  value: number
  onChange: (v: number) => void
}) {
  const color =
    value >= V5_HR_TIER1
      ? 'text-cyan-400'
      : value >= 10
        ? 'text-green-400'
        : value >= V5_HR_FLOOR
          ? 'text-yellow-400'
          : 'text-amber-400'

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm text-oss-text">
            HR Conviction minimum <span className="text-oss-muted">(grand-slam track)</span>
          </div>
          <div className="text-xs text-oss-muted mt-0.5">
            Wilson-lower P(MFE ≥ 200%) × fit × regime — hunt home runs.
          </div>
        </div>
        <span className={clsx('font-mono text-lg font-bold', color)}>{value.toFixed(1)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={20}
        step={0.5}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-oss-accent"
      />
      <div className="flex justify-between text-xs text-oss-muted">
        <span>0 (any)</span>
        <span className="text-yellow-400">{V5_HR_FLOOR.toFixed(1)} (APPROVE floor)</span>
        <span className="text-cyan-400">{V5_HR_TIER1.toFixed(1)} (Sharpshooter)</span>
      </div>
    </div>
  )
}

// ============================================================================
// P Conviction Slider — the grinder track. Policy APPROVE floor is 50.0.
// ============================================================================

function PConvictionSlider({
  value,
  onChange,
}: {
  value: number
  onChange: (v: number) => void
}) {
  const color =
    value >= 80
      ? 'text-green-400'
      : value >= 70
        ? 'text-yellow-400'
        : value >= V5_P_FLOOR
          ? 'text-amber-400'
          : 'text-oss-muted'

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm text-oss-text">
            P Conviction minimum <span className="text-oss-muted">(grinder track)</span>
          </div>
          <div className="text-xs text-oss-muted mt-0.5">
            Wilson-lower P(win) × normalized P&L × fit × regime — high base-rate edge.
          </div>
        </div>
        <span className={clsx('font-mono text-lg font-bold', color)}>{value.toFixed(0)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-oss-accent"
      />
      <div className="flex justify-between text-xs text-oss-muted">
        <span>0 (any)</span>
        <span className="text-amber-400">{V5_P_FLOOR.toFixed(0)} (APPROVE floor)</span>
        <span>100 (strict)</span>
      </div>
    </div>
  )
}

// ============================================================================
// Archetype-fit slider — weakest-link gate on pattern match.
// ============================================================================

function ArchetypeFitSlider({
  value,
  onChange,
}: {
  value: number
  onChange: (v: number) => void
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm text-oss-text">Minimum archetype fit</div>
          <div className="text-xs text-oss-muted mt-0.5">
            Reject trades where the matched archetype's weakest condition scores below this.
          </div>
        </div>
        <span className="font-mono text-lg font-bold text-oss-text">{value.toFixed(0)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-oss-accent"
      />
      <div className="flex justify-between text-xs text-oss-muted">
        <span>0 (any match)</span>
        <span>60 (solid fit)</span>
        <span>100 (perfect)</span>
      </div>
    </div>
  )
}

// ============================================================================
// Regime Alignment Slider — optional tailwind filter.
// ============================================================================

function RegimeAlignmentSlider({
  value,
  onChange,
}: {
  value: number
  onChange: (v: number) => void
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm text-oss-text">Minimum regime alignment</div>
          <div className="text-xs text-oss-muted mt-0.5">
            Regime multiplier is clamped to [0.5, 1.5]. 0 = accept any; 1.0 = neutral-or-better;
            1.1 = only alert with clear tailwind.
          </div>
        </div>
        <span className="font-mono text-lg font-bold text-oss-text">
          {value === 0 ? 'Off' : `${value.toFixed(2)}×`}
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={1.5}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-oss-accent"
      />
      <div className="flex justify-between text-xs text-oss-muted">
        <span>Off</span>
        <span>1.0 (neutral)</span>
        <span>1.5 (strong tailwind)</span>
      </div>
    </div>
  )
}

// ============================================================================
// Max Premium Slider (kept as optional convenience filter)
// ============================================================================

function PremiumSlider({
  value,
  enabled,
  onChangeValue,
  onToggle,
}: {
  value: number
  enabled: boolean
  onChangeValue: (v: number) => void
  onToggle: (enabled: boolean) => void
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-sm text-oss-text">Maximum option premium</span>
          <button
            onClick={() => onToggle(!enabled)}
            className={clsx(
              'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
              enabled ? 'bg-oss-accent' : 'bg-oss-border',
            )}
          >
            <span
              className={clsx(
                'inline-block h-3 w-3 rounded-full bg-white transition-transform',
                enabled ? 'translate-x-5' : 'translate-x-1',
              )}
            />
          </button>
        </div>
        <span
          className={clsx(
            'font-mono text-lg font-bold',
            enabled ? 'text-oss-text' : 'text-oss-muted',
          )}
        >
          {enabled ? `$${value.toFixed(2)}` : 'Off'}
        </span>
      </div>
      {enabled && (
        <>
          <input
            type="range"
            min={0.5}
            max={30}
            step={0.5}
            value={value}
            onChange={(e) => onChangeValue(Number(e.target.value))}
            className="w-full accent-oss-accent"
          />
          <div className="flex justify-between text-xs text-oss-muted">
            <span>$0.50</span>
            <span>$30.00</span>
          </div>
        </>
      )}
    </div>
  )
}

// ============================================================================
// Volume Preview Card
// ============================================================================

function VolumePreview() {
  const { data, isLoading } = useAlertPreview(3)

  if (isLoading) {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-4">
        <div className="animate-pulse space-y-3">
          <div className="h-4 w-32 rounded bg-oss-border" />
          <div className="h-8 w-20 rounded bg-oss-border" />
        </div>
      </div>
    )
  }

  if (!data) return null

  const { estimatedAlertsPerDay, daysAnalyzed, breakdown } = data

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-4 space-y-4">
      <div className="flex items-center gap-2">
        <BarChart3 className="h-4 w-4 text-oss-accent" />
        <span className="text-sm font-medium text-oss-text">Volume Preview</span>
      </div>

      <div>
        <div className="text-3xl font-bold text-oss-text">~{estimatedAlertsPerDay}</div>
        <div className="text-sm text-oss-muted">
          alerts per day (based on last {daysAnalyzed} days)
        </div>
      </div>

      <div className="space-y-1.5 text-xs">
        <div className="flex justify-between text-oss-muted">
          <span>Total APPROVE evaluations</span>
          <span className="font-mono">{breakdown.totalEvaluations}</span>
        </div>
        {breakdown.tier1Bypassed > 0 && (
          <div className="flex justify-between text-oss-muted">
            <span>⭐ Tier 1 bypass</span>
            <span className="font-mono text-cyan-400">{breakdown.tier1Bypassed}</span>
          </div>
        )}
        {breakdown.missingHrArchetype > 0 && (
          <div className="flex justify-between text-oss-muted">
            <span>No HR archetype</span>
            <span className="font-mono text-oss-reject">{breakdown.missingHrArchetype}</span>
          </div>
        )}
        {breakdown.regimeHeadwind > 0 && (
          <div className="flex justify-between text-oss-muted">
            <span>Regime headwind</span>
            <span className="font-mono text-oss-reject">{breakdown.regimeHeadwind}</span>
          </div>
        )}
        <div className="flex justify-between text-oss-muted">
          <span>Both tracks failed</span>
          <span className="font-mono text-oss-reject">{breakdown.bothTracksFailed}</span>
        </div>
        {breakdown.aboveMaxPremium > 0 && (
          <div className="flex justify-between text-oss-muted">
            <span>Above max premium</span>
            <span className="font-mono text-oss-reject">{breakdown.aboveMaxPremium}</span>
          </div>
        )}
        <div className="flex justify-between text-oss-text font-medium border-t border-oss-border pt-1.5 mt-1.5">
          <span>Would alert</span>
          <span className="font-mono text-oss-approve">{breakdown.wouldAlert}</span>
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Webhook Channel Manager
// ============================================================================

function WebhookManager({
  channels,
  onChange,
}: {
  channels: WebhookChannel[]
  onChange: (channels: WebhookChannel[]) => void
}) {
  const [newName, setNewName] = useState('')
  const [newUrl, setNewUrl] = useState('')
  const testAlert = useTestAlert()

  const addChannel = () => {
    if (!newName.trim() || !newUrl.trim()) return
    onChange([...channels, { channel_name: newName.trim(), url: newUrl.trim() }])
    setNewName('')
    setNewUrl('')
  }

  const removeChannel = (index: number) => {
    onChange(channels.filter((_, i) => i !== index))
  }

  return (
    <div className="space-y-4">
      {channels.map((ch, i) => (
        <div
          key={i}
          className="flex items-center gap-3 rounded-lg border border-oss-border bg-oss-bg p-3"
        >
          <div className="flex-1">
            <div className="font-medium text-sm text-oss-text">{ch.channel_name}</div>
            <div className="text-xs text-oss-muted font-mono mt-0.5">
              {ch.url_masked || (ch.url ? `...${ch.url.slice(-6)}` : '***')}
            </div>
          </div>
          <button
            onClick={() => testAlert.mutate(i)}
            disabled={testAlert.isPending}
            className="rounded-lg px-3 py-1.5 text-xs font-medium text-oss-accent border border-oss-accent/30 hover:bg-oss-accent/10 transition-colors disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => removeChannel(i)}
            className="rounded-lg px-2 py-1.5 text-xs text-oss-reject hover:bg-oss-reject/10 transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}

      <div className="space-y-2">
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Channel name (e.g. #oss-alerts)"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="flex-1 rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text placeholder-oss-muted focus:border-oss-accent focus:outline-none"
          />
          <input
            type="text"
            placeholder="Webhook URL"
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            className="flex-[2] rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text placeholder-oss-muted focus:border-oss-accent focus:outline-none"
          />
          <button
            onClick={addChannel}
            disabled={!newName.trim() || !newUrl.trim()}
            className="rounded-lg bg-oss-accent px-3 py-2 text-sm font-medium text-white hover:bg-oss-accent/90 disabled:opacity-50 transition-colors"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
      </div>

      {testAlert.isSuccess && (
        <div className="text-xs text-oss-approve">Test alert sent successfully</div>
      )}
      {testAlert.isError && (
        <div className="text-xs text-oss-reject">
          Test failed: {(testAlert.error as Error)?.message || 'Unknown error'}
        </div>
      )}
    </div>
  )
}

// ============================================================================
// Alert History Table
// ============================================================================

function AlertHistoryTable() {
  const { data, isLoading } = useAlertHistory()

  if (isLoading) {
    return <div className="text-sm text-oss-muted">Loading history...</div>
  }

  const entries = data?.entries ?? []

  if (entries.length === 0) {
    return <div className="text-sm text-oss-muted py-2">No alerts sent yet today.</div>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-oss-border text-left text-xs text-oss-muted">
            <th className="py-2 pr-3">Time</th>
            <th className="py-2 pr-3">Ticker</th>
            <th className="py-2 pr-3">Driver</th>
            <th className="py-2 pr-3">HR</th>
            <th className="py-2 pr-3">P</th>
            <th className="py-2 pr-3">Channel</th>
            <th className="py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, i) => {
            const driverLabel =
              entry.driver === 'tier_1'
                ? '⭐'
                : entry.driver === 'HR'
                  ? '🎯 HR'
                  : entry.driver === 'P'
                    ? '💰 P'
                    : '—'
            return (
              <tr key={i} className="border-b border-oss-border/50">
                <td className="py-2 pr-3 font-mono text-xs text-oss-muted">
                  {entry.timestamp?.slice(11, 19) || '—'}
                </td>
                <td className="py-2 pr-3 font-medium text-oss-text">{entry.ticker}</td>
                <td className="py-2 pr-3 text-oss-text">{driverLabel}</td>
                <td className="py-2 pr-3 font-mono text-oss-muted">
                  {entry.hr_conviction != null ? entry.hr_conviction.toFixed(1) : '—'}
                </td>
                <td className="py-2 pr-3 font-mono text-oss-muted">
                  {entry.p_conviction != null ? entry.p_conviction.toFixed(0) : '—'}
                </td>
                <td className="py-2 pr-3 text-oss-muted">{entry.channel}</td>
                <td className="py-2">
                  <span
                    className={clsx(
                      'inline-block rounded-full px-2 py-0.5 text-xs font-medium',
                      entry.status === 'sent'
                        ? 'bg-oss-approve/10 text-oss-approve'
                        : 'bg-oss-reject/10 text-oss-reject',
                    )}
                  >
                    {entry.status}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ============================================================================
// Main Page
// ============================================================================

export default function AlertsConfig() {
  usePageTitle('Alerts')
  const { data: config, isLoading } = useAlertConfig()
  const updateConfig = useUpdateAlertConfig()

  const [draft, setDraft] = useState<Partial<AlertConfig> | null>(null)
  const [hasChanges, setHasChanges] = useState(false)

  useEffect(() => {
    if (config && !draft) {
      setDraft({ ...config })
    }
  }, [config, draft])

  const updateDraft = useCallback(
    <K extends keyof AlertConfig>(key: K, value: AlertConfig[K]) => {
      setDraft((prev) => (prev ? { ...prev, [key]: value } : null))
      setHasChanges(true)
    },
    [],
  )

  const handleSave = useCallback(() => {
    if (!draft) return
    updateConfig.mutate(draft, {
      onSuccess: () => setHasChanges(false),
    })
  }, [draft, updateConfig])

  if (isLoading || !draft) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Bell className="h-6 w-6 text-oss-accent" />
          <h1 className="text-2xl font-bold text-oss-text">Slack Alerts</h1>
        </div>
        <div className="animate-pulse space-y-4">
          {[1, 2, 3].map((n) => (
            <div key={n} className="h-24 rounded-xl bg-oss-surface" />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Bell className="h-6 w-6 text-oss-accent" />
          <div>
            <h1 className="text-2xl font-bold text-oss-text">Slack Alerts</h1>
            <p className="text-sm text-oss-muted">
              Get notified on sharpshooter (⭐), HR-driven (🎯), or P-driven (💰) opportunities
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <span className="text-sm text-oss-muted">
              {draft.enabled ? 'Enabled' : 'Disabled'}
            </span>
            <button
              onClick={() => updateDraft('enabled', !draft.enabled)}
              className={clsx(
                'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
                draft.enabled ? 'bg-oss-accent' : 'bg-oss-border',
              )}
            >
              <span
                className={clsx(
                  'inline-block h-4 w-4 rounded-full bg-white transition-transform',
                  draft.enabled ? 'translate-x-6' : 'translate-x-1',
                )}
              />
            </button>
          </label>

          {hasChanges && (
            <button
              onClick={handleSave}
              disabled={updateConfig.isPending}
              className="flex items-center gap-2 rounded-lg bg-oss-accent px-4 py-2 text-sm font-medium text-white hover:bg-oss-accent/90 disabled:opacity-50 transition-colors"
            >
              <Save className="h-4 w-4" />
              {updateConfig.isPending ? 'Saving...' : 'Save'}
            </button>
          )}
        </div>
      </div>

      {updateConfig.isSuccess && !hasChanges && (
        <div className="rounded-lg bg-oss-approve/10 border border-oss-approve/20 px-4 py-2 text-sm text-oss-approve">
          Configuration saved successfully.
        </div>
      )}

      {/* Main layout: settings + preview */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Settings */}
        <div className="lg:col-span-2 space-y-4">
          {/* Conviction gates */}
          <Section title="Conviction Gates" icon={<Target className="h-4 w-4" />}>
            <div className="space-y-6">
              <HrConvictionSlider
                value={draft.hr_conviction_min ?? 10}
                onChange={(v) => updateDraft('hr_conviction_min', v)}
              />
              <PConvictionSlider
                value={draft.p_conviction_min ?? 70}
                onChange={(v) => updateDraft('p_conviction_min', v)}
              />
              <ArchetypeFitSlider
                value={draft.min_archetype_fit ?? 60}
                onChange={(v) => updateDraft('min_archetype_fit', v)}
              />

              <div className="flex items-center justify-between py-2 border-t border-oss-border/50 pt-4">
                <div>
                  <div className="text-sm text-oss-text">
                    Sharpshooter-only mode
                  </div>
                  <div className="text-xs text-oss-muted mt-0.5">
                    Require an HR archetype match — filters out pure P-driven grinders.
                  </div>
                </div>
                <button
                  onClick={() =>
                    updateDraft('require_hr_archetype', !draft.require_hr_archetype)
                  }
                  className={clsx(
                    'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
                    draft.require_hr_archetype ? 'bg-oss-accent' : 'bg-oss-border',
                  )}
                >
                  <span
                    className={clsx(
                      'inline-block h-4 w-4 rounded-full bg-white transition-transform',
                      draft.require_hr_archetype ? 'translate-x-6' : 'translate-x-1',
                    )}
                  />
                </button>
              </div>

              <div className="flex items-center justify-between py-2">
                <div>
                  <div className="text-sm text-oss-text">Tier 1 (Sharpshooter) bypass</div>
                  <div className="text-xs text-oss-muted mt-0.5">
                    Always alert on TIER_1 regardless of above thresholds.
                  </div>
                </div>
                <button
                  onClick={() => updateDraft('tier_1_bypass', !draft.tier_1_bypass)}
                  className={clsx(
                    'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
                    draft.tier_1_bypass ? 'bg-oss-accent' : 'bg-oss-border',
                  )}
                >
                  <span
                    className={clsx(
                      'inline-block h-4 w-4 rounded-full bg-white transition-transform',
                      draft.tier_1_bypass ? 'translate-x-6' : 'translate-x-1',
                    )}
                  />
                </button>
              </div>
            </div>
          </Section>

          {/* Regime + premium filters */}
          <Section
            title="Regime & Premium Filters"
            icon={<Bell className="h-4 w-4" />}
            defaultOpen={false}
          >
            <div className="space-y-6">
              <RegimeAlignmentSlider
                value={draft.min_regime_alignment ?? 0}
                onChange={(v) => updateDraft('min_regime_alignment', v)}
              />
              <PremiumSlider
                value={draft.max_premium ?? 10}
                enabled={draft.max_premium != null}
                onChangeValue={(v) => updateDraft('max_premium', v)}
                onToggle={(on) =>
                  updateDraft('max_premium', on ? (draft.max_premium ?? 10) : null)
                }
              />
            </div>
          </Section>

          {/* Rate Limiting */}
          <Section title="Rate Limiting" icon={<Clock className="h-4 w-4" />}>
            <div className="space-y-4">
              <div className="flex items-center justify-between py-2">
                <span className="text-sm text-oss-muted">Daily alert cap</span>
                <input
                  type="number"
                  value={draft.daily_cap ?? 10}
                  onChange={(e) =>
                    updateDraft('daily_cap', Math.max(1, parseInt(e.target.value) || 1))
                  }
                  min={1}
                  max={50}
                  className="w-20 rounded-lg border border-oss-border bg-oss-bg px-3 py-1.5 text-right font-mono text-sm text-oss-text focus:border-oss-accent focus:outline-none"
                />
              </div>

              <div className="flex items-center justify-between py-2">
                <span className="text-sm text-oss-muted">
                  Cooldown per contract (minutes)
                </span>
                <input
                  type="number"
                  value={draft.cooldown_minutes ?? 30}
                  onChange={(e) =>
                    updateDraft(
                      'cooldown_minutes',
                      Math.max(1, parseInt(e.target.value) || 1),
                    )
                  }
                  min={1}
                  max={120}
                  className="w-20 rounded-lg border border-oss-border bg-oss-bg px-3 py-1.5 text-right font-mono text-sm text-oss-text focus:border-oss-accent focus:outline-none"
                />
              </div>

              <div className="flex items-center justify-between py-2">
                <span className="text-sm text-oss-muted">Quiet hours (UTC)</span>
                <div className="flex items-center gap-2">
                  <input
                    type="time"
                    value={draft.quiet_hours_start ?? '22:00'}
                    onChange={(e) => updateDraft('quiet_hours_start', e.target.value)}
                    className="rounded-lg border border-oss-border bg-oss-bg px-2 py-1.5 font-mono text-sm text-oss-text focus:border-oss-accent focus:outline-none"
                  />
                  <span className="text-sm text-oss-muted">to</span>
                  <input
                    type="time"
                    value={draft.quiet_hours_end ?? '08:00'}
                    onChange={(e) => updateDraft('quiet_hours_end', e.target.value)}
                    className="rounded-lg border border-oss-border bg-oss-bg px-2 py-1.5 font-mono text-sm text-oss-text focus:border-oss-accent focus:outline-none"
                  />
                </div>
              </div>
            </div>
          </Section>

          {/* Webhook Channels */}
          <Section title="Webhook Channels" icon={<Send className="h-4 w-4" />}>
            <WebhookManager
              channels={draft.webhook_channels ?? []}
              onChange={(channels) => updateDraft('webhook_channels', channels)}
            />
          </Section>
        </div>

        {/* Right: Volume Preview */}
        <div className="space-y-4">
          <VolumePreview />
        </div>
      </div>

      {/* Alert History */}
      <Section
        title="Recent Alert History"
        icon={<History className="h-4 w-4" />}
        defaultOpen={false}
      >
        <AlertHistoryTable />
      </Section>
    </div>
  )
}
