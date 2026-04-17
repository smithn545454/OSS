import { useState, useCallback, useMemo } from 'react'
import { usePageTitle } from '@/hooks/usePageTitle'
import {
  Check, 
  Clock, 
  ChevronDown, 
  ChevronRight, 
  Shield, 
  Activity, 
  BarChart3,
  Edit3,
  X,
  Save,
  RotateCcw,
  GitCompare,
  History,
  AlertCircle,
  Crosshair,
  Globe
} from 'lucide-react'
import { usePolicies, useActivePolicy, useActivatePolicy, useCreatePolicy, usePolicyDiff } from '@/hooks/useApi'
import { formatDate, formatDateTime } from '@/lib/formatTime'
import type { Policy, PolicyConfig as PolicyConfigType, PillarKey } from '@/lib/types'
import {
  activePillarKeys,
  compositeFormulaDescription,
  isV4PillarConfig,
  pillarIdFromKey,
  pillarMeta,
} from '@/lib/pillarMeta'
import clsx from 'clsx'

// ============================================================================
// Config Section Component
// ============================================================================

interface ConfigSectionProps {
  title: string
  icon: React.ReactNode
  children: React.ReactNode
  defaultOpen?: boolean
}

function ConfigSection({ title, icon, children, defaultOpen = true }: ConfigSectionProps) {
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
// Editable Config Field Component
// ============================================================================

interface ConfigFieldProps {
  label: string
  value: number
  unit?: string
  fieldPath: string
  isEditing: boolean
  onChange: (path: string, value: number) => void
  min?: number
  max?: number
  step?: number
  error?: string
}

function ConfigField({ 
  label, 
  value, 
  unit, 
  fieldPath, 
  isEditing, 
  onChange,
  min,
  max,
  step = 1,
  error
}: ConfigFieldProps) {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newValue = parseFloat(e.target.value)
    if (!isNaN(newValue)) {
      onChange(fieldPath, newValue)
    }
  }

  return (
    <div className={clsx(
      'flex items-center justify-between py-2',
      error && 'bg-oss-reject/5 -mx-3 px-3 rounded-lg'
    )}>
      <span className="text-sm text-oss-muted">{label}</span>
      {isEditing ? (
        <div className="flex items-center gap-2">
          <input
            type="number"
            value={value}
            onChange={handleChange}
            min={min}
            max={max}
            step={step}
            className={clsx(
              'w-24 rounded-lg border bg-oss-bg px-3 py-1.5 text-right font-mono text-sm text-oss-text',
              'focus:border-oss-accent focus:outline-none focus:ring-1 focus:ring-oss-accent',
              error ? 'border-oss-reject' : 'border-oss-border'
            )}
          />
          {unit && <span className="text-sm text-oss-muted">{unit}</span>}
          {error && (
            <span className="text-xs text-oss-reject">{error}</span>
          )}
        </div>
      ) : (
        <span className="font-mono text-sm text-oss-text">
          {typeof value === 'number' ? value.toFixed(value % 1 === 0 ? 0 : 2) : value}
          {unit && <span className="ml-1 text-oss-muted">{unit}</span>}
        </span>
      )}
    </div>
  )
}

// ============================================================================
// Editable Boolean Toggle Field Component
// ============================================================================

interface ConfigToggleProps {
  label: string
  value: boolean
  fieldPath: string
  isEditing: boolean
  onChange: (path: string, value: boolean) => void
}

function ConfigToggle({
  label,
  value,
  fieldPath,
  isEditing,
  onChange,
}: ConfigToggleProps) {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(fieldPath, e.target.checked)
  }

  return (
    <div className="flex items-center justify-between py-2">
      <span className="text-sm text-oss-muted">{label}</span>
      {isEditing ? (
        <input
          type="checkbox"
          checked={value}
          onChange={handleChange}
          className="h-4 w-4 rounded border-oss-border bg-oss-bg text-oss-accent focus:ring-oss-accent"
        />
      ) : (
        <span className="font-mono text-sm text-oss-text">
          {value ? 'enabled' : 'disabled'}
        </span>
      )}
    </div>
  )
}

// ============================================================================
// Pillar Weights Editor — regime-aware (v3 legacy or v4 Sharpshooter)
// ============================================================================

interface PillarWeightsEditorProps {
  config: PolicyConfigType
  isEditing: boolean
  onConfigChange: (path: string, value: number) => void
  errors: Record<string, string>
}

function PillarWeightsEditor({
  config,
  isEditing,
  onConfigChange,
  errors,
}: PillarWeightsEditorProps) {
  const isV4 = isV4PillarConfig(config.pillars)
  const keys: PillarKey[] = activePillarKeys(config.pillars)
  const sectionLabel = isV4 ? 'Pillar Weights (v4 Sharpshooter)' : 'Pillar Weights'
  const formula = config.pillars.composite_formula ?? (isV4 ? 'weighted_geometric_mean' : 'weighted_sum')

  const total = keys.reduce((sum, k) => sum + (config.pillars.weights[k] ?? 0), 0)

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h4 className="text-xs font-medium uppercase text-oss-muted">{sectionLabel}</h4>
        <span className="text-[10px] text-oss-muted italic">
          {compositeFormulaDescription(formula)}
        </span>
      </div>
      <div className="rounded-lg bg-oss-bg p-3">
        {keys.map((key) => {
          const meta = pillarMeta(pillarIdFromKey(key))
          return (
            <ConfigField
              key={key}
              label={meta.label}
              value={(config.pillars.weights[key] ?? 0) * 100}
              unit="%"
              fieldPath={`pillars.weights.${key}`}
              isEditing={isEditing}
              onChange={(path, value) => onConfigChange(path, value / 100)}
              min={0}
              max={100}
              step={2.5}
              error={errors[`pillars.weights.${key}`]}
            />
          )
        })}
      </div>
      {isEditing && (
        <p className={clsx(
          'text-xs mt-2',
          Math.abs(total - 1) > 0.01 ? 'text-oss-reject' : 'text-oss-muted',
        )}>
          Total: {(total * 100).toFixed(0)}% (should equal 100%)
          {isV4 && ' — v4 uses these as geometric-mean exponents'}
        </p>
      )}
    </div>
  )
}

// ============================================================================
// Policy Version List Component
// ============================================================================

interface PolicyVersionListProps {
  policies: Policy[]
  selectedForCompare: string | null
  onActivate: (version: string) => void
  onCompareSelect: (version: string | null) => void
}

function PolicyVersionList({
  policies,
  selectedForCompare,
  onActivate,
  onCompareSelect,
}: PolicyVersionListProps) {
  return (
    <div className="space-y-2">
      {policies.map((policy) => (
        <div
          key={policy.version}
          className={clsx(
            'rounded-lg border p-4 transition-colors',
            policy.is_active
              ? 'border-oss-accent bg-oss-accent/5'
              : selectedForCompare === policy.version
              ? 'border-purple-500 bg-purple-500/5'
              : 'border-oss-border bg-oss-surface hover:border-oss-muted'
          )}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {policy.is_active ? (
                <div className="rounded-full bg-oss-accent/10 p-1 text-oss-accent">
                  <Check className="h-4 w-4" />
                </div>
              ) : (
                <div className="rounded-full bg-oss-bg p-1 text-oss-muted">
                  <Clock className="h-4 w-4" />
                </div>
              )}
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-mono font-medium text-oss-text">
                    {policy.version}
                  </span>
                  {policy.is_active && (
                    <span className="rounded-full bg-oss-accent/10 px-2 py-0.5 text-xs font-medium text-oss-accent">
                      Active
                    </span>
                  )}
                </div>
                <p className="text-xs text-oss-muted">
                  Created by {policy.created_by} •{' '}
                  {formatDate(policy.created_at)}
                </p>
              </div>
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            {!policy.is_active && (
              <button
                onClick={() => onActivate(policy.version)}
                className="rounded-lg border border-oss-border bg-oss-bg px-3 py-1.5 text-xs font-medium text-oss-text transition-colors hover:border-oss-accent hover:text-oss-accent"
              >
                Activate
              </button>
            )}
            <button
              onClick={() => onCompareSelect(
                selectedForCompare === policy.version ? null : policy.version
              )}
              className={clsx(
                'rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors',
                selectedForCompare === policy.version
                  ? 'border-purple-500 bg-purple-500/10 text-purple-400'
                  : 'border-oss-border bg-oss-bg text-oss-muted hover:border-purple-500 hover:text-purple-400'
              )}
            >
              <GitCompare className="h-3 w-3 inline mr-1" />
              {selectedForCompare === policy.version ? 'Selected' : 'Compare'}
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

// ============================================================================
// Policy Diff Modal Component
// ============================================================================

interface PolicyDiffModalProps {
  version1: string
  version2: string
  onClose: () => void
}

function PolicyDiffModal({ version1, version2, onClose }: PolicyDiffModalProps) {
  const { data: diff, isLoading } = usePolicyDiff(version1, version2)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-2xl max-h-[80vh] overflow-auto rounded-xl border border-oss-border bg-oss-surface p-6">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold text-oss-text">
            Compare Policies
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-oss-bg rounded-lg">
            <X className="h-5 w-5 text-oss-muted" />
          </button>
        </div>

        <div className="flex items-center gap-4 mb-6 text-sm">
          <span className="font-mono text-oss-accent">{version1}</span>
          <span className="text-oss-muted">vs</span>
          <span className="font-mono text-purple-400">{version2}</span>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-12 animate-pulse rounded-lg bg-oss-bg" />
            ))}
          </div>
        ) : diff?.identical ? (
          <div className="text-center py-8 text-oss-muted">
            <Check className="h-12 w-12 mx-auto mb-4 text-oss-approve" />
            <p>These policies are identical</p>
          </div>
        ) : diff?.changes && diff.changes.length > 0 ? (
          <div className="space-y-2">
            {diff.changes.map((change, idx) => (
              <div key={idx} className="rounded-lg bg-oss-bg p-4">
                <p className="text-sm font-medium text-oss-text mb-2">
                  {change.field_path}
                </p>
                <div className="flex items-center gap-4 text-sm">
                  <span className="text-oss-reject font-mono">
                    {JSON.stringify(change.old_value)}
                  </span>
                  <span className="text-oss-muted">→</span>
                  <span className="text-oss-approve font-mono">
                    {JSON.stringify(change.new_value)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-8 text-oss-muted">
            <p>No differences found</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================================
// Changelog Panel Component
// ============================================================================

interface ChangelogPanelProps {
  changelog: Policy['changelog']
}

function ChangelogPanel({ changelog }: ChangelogPanelProps) {
  const [isOpen, setIsOpen] = useState(false)

  if (!changelog || changelog.length === 0) return null

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between p-4 text-left"
      >
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-oss-bg p-2 text-purple-400">
            <History className="h-4 w-4" />
          </div>
          <span className="font-medium text-oss-text">
            Changelog ({changelog.length} changes)
          </span>
        </div>
        {isOpen ? (
          <ChevronDown className="h-5 w-5 text-oss-muted" />
        ) : (
          <ChevronRight className="h-5 w-5 text-oss-muted" />
        )}
      </button>
      {isOpen && (
        <div className="border-t border-oss-border p-4 space-y-3">
          {changelog.map((change, idx) => (
            <div key={idx} className="rounded-lg bg-oss-bg p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-oss-text">
                  {change.field_path}
                </span>
                <span className="text-xs text-oss-muted">
                  {formatDateTime(change.changed_at)}
                </span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="text-oss-reject font-mono">
                  {JSON.stringify(change.old_value)}
                </span>
                <span className="text-oss-muted">→</span>
                <span className="text-oss-approve font-mono">
                  {JSON.stringify(change.new_value)}
                </span>
                <span className="ml-auto text-oss-muted">
                  by {change.changed_by}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ============================================================================
// Editable Policy Config Component
// ============================================================================

interface EditablePolicyConfigProps {
  config: PolicyConfigType
  isEditing: boolean
  editedConfig: PolicyConfigType
  errors: Record<string, string>
  onConfigChange: (path: string, value: number | string | boolean) => void
}

function EditablePolicyConfig({ 
  config, 
  isEditing, 
  editedConfig, 
  errors,
  onConfigChange 
}: EditablePolicyConfigProps) {
  const displayConfig = isEditing ? editedConfig : config

  return (
    <div className="space-y-6">
      {/* Ticker Universe */}
      <ConfigSection title="Ticker Universe" icon={<Globe className="h-4 w-4" />}>
        <div className="space-y-3">
          <div className="rounded-lg bg-oss-bg p-3">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-sm text-oss-muted">Scanner Universe</span>
                <p className="text-xs text-oss-muted/60 mt-0.5">
                  Controls which tickers the pipeline scans each run
                </p>
              </div>
              {isEditing ? (
                <select
                  value={displayConfig.watchlist?.universe ?? 'sp500'}
                  onChange={(e) => onConfigChange('watchlist.universe', e.target.value)}
                  className="rounded-md border border-oss-border bg-oss-surface px-3 py-1.5 text-sm text-oss-text focus:border-oss-accent focus:outline-none"
                >
                  <option value="sp500">S&P 500 (~500 tickers)</option>
                  <option value="russell1000">Russell 1000 (~1,000 tickers)</option>
                </select>
              ) : (
                <span className="text-sm font-mono text-oss-text">
                  {(displayConfig.watchlist?.universe ?? 'sp500') === 'sp500'
                    ? 'S&P 500'
                    : 'Russell 1000'}
                </span>
              )}
            </div>
          </div>
        </div>
      </ConfigSection>

      {/* Scanner Config */}
      <ConfigSection title="Scanner Configuration" icon={<Activity className="h-4 w-4" />}>
        <div className="space-y-4">
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-oss-muted">
              Unusual Volume
            </h4>
            <div className="rounded-lg bg-oss-bg p-3">
              <ConfigField
                label="Volume Ratio Threshold"
                value={displayConfig.scanner.unusual_volume.volume_ratio_threshold}
                unit="×"
                fieldPath="scanner.unusual_volume.volume_ratio_threshold"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={1}
                max={10}
                step={0.1}
                error={errors['scanner.unusual_volume.volume_ratio_threshold']}
              />
              <ConfigField
                label="OI Change Threshold"
                value={displayConfig.scanner.unusual_volume.oi_change_threshold_pct}
                unit="%"
                fieldPath="scanner.unusual_volume.oi_change_threshold_pct"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={1}
                max={100}
                step={1}
                error={errors['scanner.unusual_volume.oi_change_threshold_pct']}
              />
            </div>
          </div>
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-oss-muted">
              Breakout/Breakdown
            </h4>
            <div className="rounded-lg bg-oss-bg p-3">
              <ConfigField
                label="Lookback Days"
                value={displayConfig.scanner.breakout.lookback_days}
                unit="days"
                fieldPath="scanner.breakout.lookback_days"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={5}
                max={60}
                step={1}
                error={errors['scanner.breakout.lookback_days']}
              />
            </div>
          </div>
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-oss-muted">
              Compression → Expansion
            </h4>
            <div className="rounded-lg bg-oss-bg p-3">
              <ConfigField
                label="ATR Period"
                value={displayConfig.scanner.compression.atr_period}
                fieldPath="scanner.compression.atr_period"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={5}
                max={30}
                step={1}
                error={errors['scanner.compression.atr_period']}
              />
              <ConfigField
                label="Compression Multiplier"
                value={displayConfig.scanner.compression.compression_multiplier}
                unit="×"
                fieldPath="scanner.compression.compression_multiplier"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={1}
                max={2}
                step={0.05}
                error={errors['scanner.compression.compression_multiplier']}
              />
              <ConfigField
                label="Break Percentage"
                value={displayConfig.scanner.compression.break_pct}
                unit="%"
                fieldPath="scanner.compression.break_pct"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={0.5}
                max={10}
                step={0.5}
                error={errors['scanner.compression.break_pct']}
              />
            </div>
          </div>
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-oss-muted">
              Cheap Options
            </h4>
            <div className="rounded-lg bg-oss-bg p-3">
              <ConfigField
                label="IV/RV Ratio Max"
                value={displayConfig.scanner.cheap_options.iv_rv_ratio_max}
                fieldPath="scanner.cheap_options.iv_rv_ratio_max"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={0.5}
                max={2}
                step={0.05}
                error={errors['scanner.cheap_options.iv_rv_ratio_max']}
              />
              <ConfigField
                label="IV Percentile Max"
                value={displayConfig.scanner.cheap_options.iv_percentile_max}
                unit="%"
                fieldPath="scanner.cheap_options.iv_percentile_max"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={10}
                max={100}
                step={5}
                error={errors['scanner.cheap_options.iv_percentile_max']}
              />
              <ConfigToggle
                label="Require Momentum Filter"
                value={displayConfig.scanner.cheap_options.require_momentum ?? false}
                fieldPath="scanner.cheap_options.require_momentum"
                isEditing={isEditing}
                onChange={onConfigChange}
              />
              <ConfigField
                label="RS 5d Threshold"
                value={displayConfig.scanner.cheap_options.rs_5d_threshold ?? 0}
                unit="%"
                fieldPath="scanner.cheap_options.rs_5d_threshold"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={0}
                max={10}
                step={0.25}
                error={errors['scanner.cheap_options.rs_5d_threshold']}
              />
            </div>
          </div>
        </div>
      </ConfigSection>

      {/* Contract Selection Config */}
      <ConfigSection title="Contract Selection" icon={<Crosshair className="h-4 w-4" />}>
        <div className="space-y-4">
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-oss-muted">
              Delta Targeting
            </h4>
            <div className="rounded-lg bg-oss-bg p-3">
              <ConfigField
                label="Target Delta (Calls)"
                value={displayConfig.contract_selection.target_delta_call}
                fieldPath="contract_selection.target_delta_call"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={0.10}
                max={0.60}
                step={0.05}
                error={errors['contract_selection.target_delta_call']}
              />
              <ConfigField
                label="Target Delta (Puts)"
                value={displayConfig.contract_selection.target_delta_put}
                fieldPath="contract_selection.target_delta_put"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={-0.60}
                max={-0.10}
                step={0.05}
                error={errors['contract_selection.target_delta_put']}
              />
              <ConfigField
                label="Call Delta Min"
                value={displayConfig.contract_selection.delta_bands.CALL.min_delta}
                fieldPath="contract_selection.delta_bands.CALL.min_delta"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={0.05}
                max={0.50}
                step={0.05}
                error={errors['contract_selection.delta_bands.CALL.min_delta']}
              />
              <ConfigField
                label="Call Delta Max"
                value={displayConfig.contract_selection.delta_bands.CALL.max_delta}
                fieldPath="contract_selection.delta_bands.CALL.max_delta"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={0.30}
                max={0.90}
                step={0.05}
                error={errors['contract_selection.delta_bands.CALL.max_delta']}
              />
              <ConfigField
                label="Put Delta Min"
                value={displayConfig.contract_selection.delta_bands.PUT.min_delta}
                fieldPath="contract_selection.delta_bands.PUT.min_delta"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={-0.90}
                max={-0.30}
                step={0.05}
                error={errors['contract_selection.delta_bands.PUT.min_delta']}
              />
              <ConfigField
                label="Put Delta Max"
                value={displayConfig.contract_selection.delta_bands.PUT.max_delta}
                fieldPath="contract_selection.delta_bands.PUT.max_delta"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={-0.50}
                max={-0.05}
                step={0.05}
                error={errors['contract_selection.delta_bands.PUT.max_delta']}
              />
            </div>
          </div>
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-oss-muted">
              Ranking Weights
            </h4>
            <div className="rounded-lg bg-oss-bg p-3">
              <ConfigField
                label="Liquidity"
                value={displayConfig.contract_selection.rank_weight_liquidity * 100}
                unit="%"
                fieldPath="contract_selection.rank_weight_liquidity"
                isEditing={isEditing}
                onChange={(path, value) => onConfigChange(path, value / 100)}
                min={0}
                max={100}
                step={5}
                error={errors['contract_selection.rank_weight_liquidity']}
              />
              <ConfigField
                label="Delta Proximity"
                value={displayConfig.contract_selection.rank_weight_delta * 100}
                unit="%"
                fieldPath="contract_selection.rank_weight_delta"
                isEditing={isEditing}
                onChange={(path, value) => onConfigChange(path, value / 100)}
                min={0}
                max={100}
                step={5}
                error={errors['contract_selection.rank_weight_delta']}
              />
              <ConfigField
                label="Spread Tightness"
                value={displayConfig.contract_selection.rank_weight_spread * 100}
                unit="%"
                fieldPath="contract_selection.rank_weight_spread"
                isEditing={isEditing}
                onChange={(path, value) => onConfigChange(path, value / 100)}
                min={0}
                max={100}
                step={5}
                error={errors['contract_selection.rank_weight_spread']}
              />
            </div>
            {isEditing && (
              <p className={clsx(
                'text-xs mt-2',
                Math.abs((displayConfig.contract_selection.rank_weight_liquidity +
                  displayConfig.contract_selection.rank_weight_delta +
                  displayConfig.contract_selection.rank_weight_spread) - 1) > 0.01
                  ? 'text-oss-reject'
                  : 'text-oss-muted'
              )}>
                Total: {((displayConfig.contract_selection.rank_weight_liquidity +
                  displayConfig.contract_selection.rank_weight_delta +
                  displayConfig.contract_selection.rank_weight_spread) * 100).toFixed(0)}%
                (should equal 100%)
              </p>
            )}
          </div>
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-oss-muted">
              Selection Filters
            </h4>
            <div className="rounded-lg bg-oss-bg p-3">
              <ConfigField
                label="Top K Contracts"
                value={displayConfig.contract_selection.top_k}
                fieldPath="contract_selection.top_k"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={1}
                max={10}
                step={1}
                error={errors['contract_selection.top_k']}
              />
              <ConfigField
                label="Min Mid Price"
                value={displayConfig.contract_selection.min_mid_price}
                unit="$"
                fieldPath="contract_selection.min_mid_price"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={0.05}
                max={1.00}
                step={0.05}
                error={errors['contract_selection.min_mid_price']}
              />
            </div>
          </div>
        </div>
      </ConfigSection>

      {/* Gates Config */}
      <ConfigSection title="Hard Gates" icon={<Shield className="h-4 w-4" />}>
        <div className="rounded-lg bg-oss-bg p-3">
          <ConfigField
            label="Min Open Interest"
            value={displayConfig.gates.min_open_interest}
            unit="contracts"
            fieldPath="gates.min_open_interest"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={50}
            max={1000}
            step={50}
            error={errors['gates.min_open_interest']}
          />
          <ConfigField
            label="Min Volume"
            value={displayConfig.gates.min_volume}
            unit="contracts"
            fieldPath="gates.min_volume"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={10}
            max={500}
            step={10}
            error={errors['gates.min_volume']}
          />
          <ConfigField
            label="Max Spread"
            value={displayConfig.gates.max_spread_pct}
            unit="%"
            fieldPath="gates.max_spread_pct"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={1}
            max={20}
            step={0.5}
            error={errors['gates.max_spread_pct']}
          />
          <ConfigField
            label="DTE Min"
            value={displayConfig.gates.dte_min}
            unit="days"
            fieldPath="gates.dte_min"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={1}
            max={30}
            step={1}
            error={errors['gates.dte_min']}
          />
          <ConfigField
            label="DTE Max"
            value={displayConfig.gates.dte_max}
            unit="days"
            fieldPath="gates.dte_max"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={30}
            max={365}
            step={5}
            error={errors['gates.dte_max']}
          />
          <ConfigField
            label="Move Sufficiency Max"
            value={displayConfig.gates.move_sufficiency_max}
            fieldPath="gates.move_sufficiency_max"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={0.5}
            max={3}
            step={0.05}
            error={errors['gates.move_sufficiency_max']}
          />
          <ConfigField
            label="IV Percentile Max"
            value={displayConfig.gates.iv_percentile_max}
            unit="%"
            fieldPath="gates.iv_percentile_max"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={50}
            max={100}
            step={5}
            error={errors['gates.iv_percentile_max']}
          />
          <ConfigField
            label="Breakout Volume Min"
            value={displayConfig.gates.breakout_volume_min}
            unit="×"
            fieldPath="gates.breakout_volume_min"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={1}
            max={5}
            step={0.1}
            error={errors['gates.breakout_volume_min']}
          />
          <ConfigField
            label="Theta Burden Max"
            value={displayConfig.gates.theta_burden_max}
            unit="%"
            fieldPath="gates.theta_burden_max"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={1}
            max={10}
            step={0.5}
            error={errors['gates.theta_burden_max']}
          />
        </div>
      </ConfigSection>

      {/* Scoring Config — renders v3 or v4 pillar set based on active policy */}
      <ConfigSection title="Scoring & Decision" icon={<BarChart3 className="h-4 w-4" />}>
        <div className="space-y-4">
          <PillarWeightsEditor
            config={displayConfig}
            isEditing={isEditing}
            onConfigChange={onConfigChange}
            errors={errors}
          />

          {/* Read-only subscore summary — iterates over whichever pillar
              set (v3 or v4) is populated on the active policy. */}
          {activePillarKeys(displayConfig.pillars).map((pillarKey) => {
            const pillar = displayConfig.pillars[pillarKey]
            if (!pillar) return null
            const meta = pillarMeta(pillarIdFromKey(pillarKey))
            const allSubs = [...pillar.numeric_subscores, ...pillar.categorical_subscores]
            return (
              <div key={pillarKey}>
                <h4 className="mb-2 text-xs font-medium uppercase text-oss-muted">
                  {pillar.display_name || meta.label} Subscores
                </h4>
                <div className="rounded-lg bg-oss-bg p-3 space-y-1.5">
                  {allSubs.map((sub) => (
                    <div key={sub.subscore_id} className="flex justify-between text-xs">
                      <span className="text-oss-text">{sub.display_name}</span>
                      <span className="font-mono text-oss-muted">
                        {(sub.weight * 100).toFixed(1)}%
                      </span>
                    </div>
                  ))}
                </div>
                <p className="text-[11px] mt-1 text-oss-muted italic">
                  Subscore breakpoints are tuned via the feature-outcome analysis script
                  (<code>scripts/feature_outcome_analysis.py</code>). Edit through the script,
                  then activate a new policy version.
                </p>
              </div>
            )
          })}
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-oss-muted">
              Decision Thresholds
            </h4>
            <div className="rounded-lg bg-oss-bg p-3">
              <ConfigField
                label="Approve Threshold"
                value={displayConfig.decision.approve_threshold}
                fieldPath="decision.approve_threshold"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={50}
                max={100}
                step={1}
                error={errors['decision.approve_threshold']}
              />
              <ConfigField
                label="Watch Threshold"
                value={displayConfig.decision.watch_threshold}
                fieldPath="decision.watch_threshold"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={30}
                max={90}
                step={1}
                error={errors['decision.watch_threshold']}
              />
              <ConfigField
                label="Tier 1 Min Score"
                value={displayConfig.decision.tier_1_min_score}
                fieldPath="decision.tier_1_min_score"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={70}
                max={100}
                step={1}
                error={errors['decision.tier_1_min_score']}
              />
              <ConfigField
                label="Tier 1 Min Pillar"
                value={displayConfig.decision.tier_1_min_pillar}
                fieldPath="decision.tier_1_min_pillar"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={50}
                max={90}
                step={1}
                error={errors['decision.tier_1_min_pillar']}
              />
              <ConfigField
                label="Tier 1 Max Spread"
                value={displayConfig.decision.tier_1_max_spread}
                unit="%"
                fieldPath="decision.tier_1_max_spread"
                isEditing={isEditing}
                onChange={onConfigChange}
                min={1}
                max={15}
                step={0.5}
                error={errors['decision.tier_1_max_spread']}
              />
            </div>
          </div>
        </div>
      </ConfigSection>

      {/* Paper Trading Config */}
      <ConfigSection title="Paper Trading" icon={<Activity className="h-4 w-4" />} defaultOpen={false}>
        <div className="rounded-lg bg-oss-bg p-3">
          <ConfigField
            label="Profit Target"
            value={displayConfig.tracking.profit_target_pct}
            unit="%"
            fieldPath="tracking.profit_target_pct"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={10}
            max={200}
            step={5}
            error={errors['tracking.profit_target_pct']}
          />
          <ConfigField
            label="Stop Loss"
            value={displayConfig.tracking.stop_loss_pct}
            unit="%"
            fieldPath="tracking.stop_loss_pct"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={10}
            max={100}
            step={5}
            error={errors['tracking.stop_loss_pct']}
          />
          <ConfigField
            label="Time Exit DTE"
            value={displayConfig.tracking.time_exit_dte}
            unit="days"
            fieldPath="tracking.time_exit_dte"
            isEditing={isEditing}
            onChange={onConfigChange}
            min={1}
            max={14}
            step={1}
            error={errors['tracking.time_exit_dte']}
          />
          <ConfigField
            label="Shadow Sample Rate"
            value={displayConfig.tracking.shadow_sample_rate * 100}
            unit="%"
            fieldPath="tracking.shadow_sample_rate"
            isEditing={isEditing}
            onChange={(path, value) => onConfigChange(path, value / 100)}
            min={1}
            max={20}
            step={1}
            error={errors['tracking.shadow_sample_rate']}
          />
        </div>
      </ConfigSection>
    </div>
  )
}

// ============================================================================
// Main Policy Config Page
// ============================================================================

// Helper to deep clone and set nested value
function setNestedValue(obj: PolicyConfigType, path: string, value: number | string | boolean): PolicyConfigType {
  const clone = JSON.parse(JSON.stringify(obj)) as PolicyConfigType
  const parts = path.split('.')
  let current: Record<string, unknown> = clone as unknown as Record<string, unknown>
  
  for (let i = 0; i < parts.length - 1; i++) {
    current = current[parts[i]] as Record<string, unknown>
  }
  
  current[parts[parts.length - 1]] = value
  return clone
}

export default function PolicyConfig() {
  usePageTitle('Policy')
  const { data: policiesData, isLoading: policiesLoading } = usePolicies()
  const { data: activePolicy, isLoading: activePolicyLoading } = useActivePolicy()
  const activateMutation = useActivatePolicy()
  const createMutation = useCreatePolicy()

  const [isEditing, setIsEditing] = useState(false)
  const [editedConfig, setEditedConfig] = useState<PolicyConfigType | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [selectedForCompare, setSelectedForCompare] = useState<string | null>(null)
  const [showDiffModal, setShowDiffModal] = useState(false)

  const isLoading = policiesLoading || activePolicyLoading
  const policies = policiesData?.policies || []

  // Initialize edited config when entering edit mode
  const handleStartEdit = useCallback(() => {
    if (activePolicy) {
      setEditedConfig(JSON.parse(JSON.stringify(activePolicy.config)))
      setErrors({})
      setIsEditing(true)
    }
  }, [activePolicy])

  const handleCancelEdit = useCallback(() => {
    setEditedConfig(null)
    setErrors({})
    setIsEditing(false)
  }, [])

  const handleConfigChange = useCallback((path: string, value: number | string | boolean) => {
    if (!editedConfig) return

    const newConfig = setNestedValue(editedConfig, path, value)
    setEditedConfig(newConfig)
    
    // Clear error for this field
    setErrors(prev => {
      const next = { ...prev }
      delete next[path]
      return next
    })
  }, [editedConfig])

  // Validate the config
  const validateConfig = useCallback((config: PolicyConfigType): Record<string, string> => {
    const errors: Record<string, string> = {}

    // Validate pillar weights sum to 1 — iterate whichever regime (v3 or v4)
    // the active policy uses. First populated key carries the error.
    const regimeKeys = activePillarKeys(config.pillars)
    const weightsSum = regimeKeys.reduce(
      (s, k) => s + (config.pillars.weights[k] ?? 0),
      0,
    )
    if (Math.abs(weightsSum - 1) > 0.01 && regimeKeys.length > 0) {
      errors[`pillars.weights.${regimeKeys[0]}`] = 'Weights must sum to 100%'
    }

    // Validate ranking weights sum to 1
    const rankSum = config.contract_selection.rank_weight_liquidity +
      config.contract_selection.rank_weight_delta +
      config.contract_selection.rank_weight_spread
    if (Math.abs(rankSum - 1) > 0.01) {
      errors['contract_selection.rank_weight_liquidity'] = 'Weights must sum to 100%'
    }

    // Validate call delta band
    if (config.contract_selection.delta_bands.CALL.min_delta >= config.contract_selection.delta_bands.CALL.max_delta) {
      errors['contract_selection.delta_bands.CALL.min_delta'] = 'Must be < max delta'
    }

    // Validate put delta band (both negative, min is more negative)
    if (config.contract_selection.delta_bands.PUT.min_delta >= config.contract_selection.delta_bands.PUT.max_delta) {
      errors['contract_selection.delta_bands.PUT.min_delta'] = 'Must be < max delta'
    }

    // Validate approve > watch threshold
    if (config.decision.approve_threshold <= config.decision.watch_threshold) {
      errors['decision.approve_threshold'] = 'Must be > watch threshold'
    }

    // Validate DTE range
    if (config.gates.dte_min >= config.gates.dte_max) {
      errors['gates.dte_min'] = 'Must be < DTE max'
    }

    return errors
  }, [])

  const handleSave = useCallback(async () => {
    if (!editedConfig) return
    
    const validationErrors = validateConfig(editedConfig)
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors)
      return
    }
    
    try {
      await createMutation.mutateAsync({
        config: editedConfig,
        createdBy: 'ui-user', // In a real app, this would come from auth
      })
      setIsEditing(false)
      setEditedConfig(null)
    } catch (error) {
      console.error('Failed to save policy:', error)
    }
  }, [editedConfig, validateConfig, createMutation])

  const handleActivate = useCallback((version: string) => {
    activateMutation.mutate(version)
  }, [activateMutation])

  const handleCompareSelect = useCallback((version: string | null) => {
    setSelectedForCompare(version)
    if (version && activePolicy) {
      setShowDiffModal(true)
    }
  }, [activePolicy])

  // Check if config has been modified
  const hasChanges = useMemo(() => {
    if (!editedConfig || !activePolicy) return false
    return JSON.stringify(editedConfig) !== JSON.stringify(activePolicy.config)
  }, [editedConfig, activePolicy])

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-oss-text">Policy Configuration</h1>
          <p className="mt-1 text-sm text-oss-muted">
            {isEditing 
              ? 'Edit thresholds and save as a new policy version'
              : 'View and manage policy versions. All thresholds are configurable.'
            }
          </p>
        </div>
        
        {activePolicy && !isEditing && (
          <button
            onClick={handleStartEdit}
            className="flex items-center gap-2 rounded-lg bg-oss-accent px-4 py-2 text-sm font-medium text-oss-bg transition-colors hover:bg-oss-accent/90"
          >
            <Edit3 className="h-4 w-4" />
            Edit Configuration
          </button>
        )}
        
        {isEditing && (
          <div className="flex items-center gap-3">
            <button
              onClick={handleCancelEdit}
              className="flex items-center gap-2 rounded-lg border border-oss-border px-4 py-2 text-sm font-medium text-oss-muted transition-colors hover:border-oss-muted hover:text-oss-text"
            >
              <X className="h-4 w-4" />
              Cancel
            </button>
            <button
              onClick={() => {
                if (activePolicy) {
                  setEditedConfig(JSON.parse(JSON.stringify(activePolicy.config)))
                  setErrors({})
                }
              }}
              className="flex items-center gap-2 rounded-lg border border-oss-border px-4 py-2 text-sm font-medium text-oss-muted transition-colors hover:border-oss-muted hover:text-oss-text"
            >
              <RotateCcw className="h-4 w-4" />
              Reset
            </button>
            <button
              onClick={handleSave}
              disabled={!hasChanges || Object.keys(errors).length > 0 || createMutation.isPending}
              className={clsx(
                'flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors',
                hasChanges && Object.keys(errors).length === 0
                  ? 'bg-oss-approve text-white hover:bg-oss-approve/90'
                  : 'bg-oss-muted/20 text-oss-muted cursor-not-allowed'
              )}
            >
              <Save className="h-4 w-4" />
              {createMutation.isPending ? 'Saving...' : 'Save as New Version'}
            </button>
          </div>
        )}
      </div>

      {/* Editing Notice */}
      {isEditing && hasChanges && (
        <div className="rounded-lg border border-oss-watch/30 bg-oss-watch/5 p-4 flex items-center gap-3">
          <AlertCircle className="h-5 w-5 text-oss-watch" />
          <p className="text-sm text-oss-text">
            You have unsaved changes. Saving will create a new policy version.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        {/* Policy Versions */}
        <div className="lg:col-span-1">
          <h2 className="mb-4 text-lg font-medium text-oss-text">Policy Versions</h2>
          {isLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-20 animate-pulse rounded-lg border border-oss-border bg-oss-surface"
                />
              ))}
            </div>
          ) : policies.length === 0 ? (
            <div className="rounded-lg border border-oss-border bg-oss-surface p-6 text-center">
              <p className="text-sm text-oss-muted">No policies found</p>
              <p className="mt-1 text-xs text-oss-muted">
                Run the seed script to create the default policy
              </p>
            </div>
          ) : (
            <PolicyVersionList
              policies={policies}
              selectedForCompare={selectedForCompare}
              onActivate={handleActivate}
              onCompareSelect={handleCompareSelect}
            />
          )}
        </div>

        {/* Active Policy Config */}
        <div className="lg:col-span-2">
          <h2 className="mb-4 text-lg font-medium text-oss-text">
            {isEditing ? 'Editing Configuration' : 'Active Configuration'}
            {activePolicy && (
              <span className="ml-2 font-mono text-sm text-oss-accent">
                {activePolicy.version}
              </span>
            )}
          </h2>
          {isLoading ? (
            <div className="space-y-4">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-32 animate-pulse rounded-xl border border-oss-border bg-oss-surface"
                />
              ))}
            </div>
          ) : activePolicy ? (
            <>
              <EditablePolicyConfig
                config={activePolicy.config}
                isEditing={isEditing}
                editedConfig={editedConfig || activePolicy.config}
                errors={errors}
                onConfigChange={handleConfigChange}
              />
              
              {/* Changelog */}
              {!isEditing && activePolicy.changelog && activePolicy.changelog.length > 0 && (
                <div className="mt-6">
                  <ChangelogPanel changelog={activePolicy.changelog} />
                </div>
              )}
            </>
          ) : (
            <div className="rounded-xl border border-oss-border bg-oss-surface p-6 text-center">
              <p className="text-sm text-oss-muted">No active policy</p>
              <p className="mt-1 text-xs text-oss-muted">
                Activate a policy version to view its configuration
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Diff Modal */}
      {showDiffModal && selectedForCompare && activePolicy && (
        <PolicyDiffModal
          version1={activePolicy.version}
          version2={selectedForCompare}
          onClose={() => {
            setShowDiffModal(false)
            setSelectedForCompare(null)
          }}
        />
      )}
    </div>
  )
}
