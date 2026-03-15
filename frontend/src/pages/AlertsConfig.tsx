import { useState, useCallback, useEffect } from 'react'
import {
  Bell,
  Save,
  ChevronDown,
  ChevronRight,
  Send,
  Trash2,
  Plus,
  BarChart3,
  Filter,
  Clock,
  History,
} from 'lucide-react'
import {
  useAlertConfig,
  useUpdateAlertConfig,
  useAlertPreview,
  useAlertHistory,
  useTestAlert,
  useSetupRules,
} from '@/hooks/useApi'
import type { AlertConfig, WebhookChannel, SetupRule } from '@/lib/types'
import clsx from 'clsx'

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
// Score Threshold Slider
// ============================================================================

function ScoreSlider({
  value,
  onChange,
}: {
  value: number
  onChange: (v: number) => void
}) {
  const color =
    value >= 85
      ? 'text-cyan-400'
      : value >= 75
        ? 'text-green-400'
        : value >= 65
          ? 'text-yellow-400'
          : 'text-amber-400'

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm text-oss-muted">Minimum Conviction Score</span>
        <span className={clsx('font-mono text-lg font-bold', color)}>{value}</span>
      </div>
      <input
        type="range"
        min={50}
        max={95}
        step={5}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-oss-accent"
      />
      <div className="flex justify-between text-xs text-oss-muted">
        <span>50 (more alerts)</span>
        <span>95 (fewer alerts)</span>
      </div>
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
        <div className="text-3xl font-bold text-oss-text">
          ~{estimatedAlertsPerDay}
        </div>
        <div className="text-sm text-oss-muted">
          alerts per day (based on last {daysAnalyzed} days)
        </div>
      </div>

      <div className="space-y-1.5 text-xs">
        <div className="flex justify-between text-oss-muted">
          <span>Total evaluations scanned</span>
          <span className="font-mono">{breakdown.totalEvaluations}</span>
        </div>
        <div className="flex justify-between text-oss-muted">
          <span>Below score threshold</span>
          <span className="font-mono text-oss-reject">{breakdown.belowScoreThreshold}</span>
        </div>
        <div className="flex justify-between text-oss-muted">
          <span>Failed urgency/convergence</span>
          <span className="font-mono text-amber-400">{breakdown.failedUrgencyConvergence}</span>
        </div>
        {breakdown.noMatchingSetupRule > 0 && (
          <div className="flex justify-between text-oss-muted">
            <span>No matching setup rule</span>
            <span className="font-mono text-amber-400">{breakdown.noMatchingSetupRule}</span>
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
// Setup Rule Filter
// ============================================================================

function SetupRuleFilter({
  selectedIds,
  onChange,
}: {
  selectedIds: string[]
  onChange: (ids: string[]) => void
}) {
  const { data } = useSetupRules()
  const rules: SetupRule[] = data ?? []
  const activeRules = rules.filter((r) => r.is_active)

  if (activeRules.length === 0) {
    return (
      <div className="text-sm text-oss-muted py-2">
        No active setup rules. Create rules in the Paper Trading page to use this filter.
      </div>
    )
  }

  const toggle = (ruleId: string) => {
    if (selectedIds.includes(ruleId)) {
      onChange(selectedIds.filter((id) => id !== ruleId))
    } else {
      onChange([...selectedIds, ruleId])
    }
  }

  return (
    <div className="space-y-1">
      <div className="text-xs text-oss-muted mb-2">
        {selectedIds.length === 0
          ? 'No filter — all opportunities are eligible for alerts'
          : `Alerting only when evaluation matches one of ${selectedIds.length} selected rule(s)`}
      </div>
      {activeRules.map((rule) => (
        <label
          key={rule.rule_id}
          className="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-oss-bg cursor-pointer transition-colors"
        >
          <input
            type="checkbox"
            checked={selectedIds.includes(rule.rule_id)}
            onChange={() => toggle(rule.rule_id)}
            className="rounded border-oss-border text-oss-accent focus:ring-oss-accent"
          />
          <div className="flex-1">
            <div className="text-sm text-oss-text">{rule.name}</div>
            <div className="text-xs text-oss-muted">
              {rule.mode === 'production' ? 'Production' : 'Test'} mode
            </div>
          </div>
        </label>
      ))}
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
            <th className="py-2 pr-3">Score</th>
            <th className="py-2 pr-3">Channel</th>
            <th className="py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, i) => (
            <tr key={i} className="border-b border-oss-border/50">
              <td className="py-2 pr-3 font-mono text-xs text-oss-muted">
                {entry.timestamp?.slice(11, 19) || '—'}
              </td>
              <td className="py-2 pr-3 font-medium text-oss-text">{entry.ticker}</td>
              <td className="py-2 pr-3 font-mono">{entry.conviction_score?.toFixed(0)}</td>
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
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ============================================================================
// Main Page
// ============================================================================

export default function AlertsConfig() {
  const { data: config, isLoading } = useAlertConfig()
  const updateConfig = useUpdateAlertConfig()

  // Local draft state
  const [draft, setDraft] = useState<Partial<AlertConfig> | null>(null)
  const [hasChanges, setHasChanges] = useState(false)

  // Initialize draft from loaded config
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
              Get notified when high-conviction opportunities are found
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Enable/Disable toggle */}
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

          {/* Save button */}
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
          {/* Alert Filters */}
          <Section title="Alert Filters" icon={<Bell className="h-4 w-4" />}>
            <div className="space-y-6">
              <ScoreSlider
                value={draft.score_threshold ?? 75}
                onChange={(v) => updateDraft('score_threshold', v)}
              />

              <div className="flex items-center justify-between py-2">
                <div>
                  <div className="text-sm text-oss-text">
                    Require urgency or scanner convergence
                  </div>
                  <div className="text-xs text-oss-muted mt-0.5">
                    Only alert if urgency is Act Now OR 2+ scanners fired
                  </div>
                </div>
                <button
                  onClick={() =>
                    updateDraft(
                      'require_urgency_or_convergence',
                      !draft.require_urgency_or_convergence,
                    )
                  }
                  className={clsx(
                    'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
                    draft.require_urgency_or_convergence ? 'bg-oss-accent' : 'bg-oss-border',
                  )}
                >
                  <span
                    className={clsx(
                      'inline-block h-4 w-4 rounded-full bg-white transition-transform',
                      draft.require_urgency_or_convergence ? 'translate-x-6' : 'translate-x-1',
                    )}
                  />
                </button>
              </div>
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
                <span className="text-sm text-oss-muted">Cooldown per contract (minutes)</span>
                <input
                  type="number"
                  value={draft.cooldown_minutes ?? 30}
                  onChange={(e) =>
                    updateDraft('cooldown_minutes', Math.max(1, parseInt(e.target.value) || 1))
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

          {/* Setup Rule Filter */}
          <Section
            title="Setup Rule Filter"
            icon={<Filter className="h-4 w-4" />}
            defaultOpen={false}
          >
            <SetupRuleFilter
              selectedIds={draft.setup_rule_filter_ids ?? []}
              onChange={(ids) => updateDraft('setup_rule_filter_ids', ids)}
            />
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
