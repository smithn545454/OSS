/**
 * Modal form for manually creating setup rules.
 * Criteria are organized into collapsible groups matching the rule matcher dimensions.
 */

import { useState } from 'react'
import clsx from 'clsx'
import { X, ChevronDown, ChevronRight } from 'lucide-react'
import { useCreateSetupRule } from '@/hooks/useApi'

interface ManualRuleFormProps {
  isOpen: boolean
  onClose: () => void
}

// Criteria field definition
interface CriteriaField {
  key: string
  label: string
  type: 'number' | 'select' | 'toggle'
  options?: string[] // for select type
  placeholder?: string
  step?: string
}

// Criteria group with fields
interface CriteriaGroup {
  id: string
  label: string
  defaultExpanded: boolean
  fields: CriteriaField[]
}

const CRITERIA_GROUPS: CriteriaGroup[] = [
  {
    id: 'volatility',
    label: 'Volatility Regime',
    defaultExpanded: true,
    fields: [
      { key: 'iv_percentile_min', label: 'IV Percentile Min', type: 'number', placeholder: '0', step: '1' },
      { key: 'iv_percentile_max', label: 'IV Percentile Max', type: 'number', placeholder: '100', step: '1' },
      { key: 'iv_rv_ratio_min', label: 'IV/RV Ratio Min', type: 'number', placeholder: '0.0', step: '0.05' },
      { key: 'iv_rv_ratio_max', label: 'IV/RV Ratio Max', type: 'number', placeholder: '2.0', step: '0.05' },
      { key: 'entry_iv_min', label: 'Entry IV Min', type: 'number', placeholder: '0.0', step: '0.05' },
      { key: 'entry_iv_max', label: 'Entry IV Max', type: 'number', placeholder: '2.0', step: '0.05' },
      { key: 'theta_adjusted_edge_min', label: 'Theta-Adj Edge Min', type: 'number', placeholder: '0', step: '0.5' },
    ],
  },
  {
    id: 'scanners',
    label: 'Scanners',
    defaultExpanded: true,
    fields: [
      // Scanner selection handled specially (multi-select buttons)
    ],
  },
  {
    id: 'contract',
    label: 'Contract Parameters',
    defaultExpanded: false,
    fields: [
      { key: 'dte_min', label: 'DTE Min', type: 'number', placeholder: '0', step: '1' },
      { key: 'dte_max', label: 'DTE Max', type: 'number', placeholder: '90', step: '1' },
      { key: 'option_type', label: 'Option Type', type: 'select', options: ['CALL', 'PUT'] },
      { key: 'moneyness_pct_min', label: 'Moneyness % Min', type: 'number', placeholder: '0', step: '0.5' },
      { key: 'moneyness_pct_max', label: 'Moneyness % Max', type: 'number', placeholder: '20', step: '0.5' },
    ],
  },
  {
    id: 'scores',
    label: 'Scores & Quality',
    defaultExpanded: false,
    // Supports both legacy v3 pillar minimums (for rules matched against
    // pre-v4 positions) and v4 Sharpshooter pillar minimums (for rules
    // matched against v4-era positions). Rule matcher uses whichever set
    // the position actually has.
    fields: [
      { key: 'conviction_score_min', label: 'Conviction Min', type: 'number', placeholder: '0', step: '5' },
      { key: 'pillar_directional_conviction_min', label: 'Directional Conviction Min (v4)', type: 'number', placeholder: '0', step: '5' },
      { key: 'pillar_move_potential_min', label: 'Move Potential Min (v4)', type: 'number', placeholder: '0', step: '5' },
      { key: 'pillar_trade_structure_min', label: 'Trade Structure Min (v4)', type: 'number', placeholder: '0', step: '5' },
      { key: 'pillar_premium_leverage_min', label: 'Premium Leverage Min (v3)', type: 'number', placeholder: '0', step: '5' },
      { key: 'pillar_underlying_behavior_min', label: 'Underlying Behavior Min (v3)', type: 'number', placeholder: '0', step: '5' },
      { key: 'pillar_setup_quality_min', label: 'Setup Quality Min (v3)', type: 'number', placeholder: '0', step: '5' },
      { key: 'gate_margin_min', label: 'Gate Margin Min', type: 'number', placeholder: '0', step: '0.1' },
    ],
  },
  {
    id: 'liquidity',
    label: 'Liquidity',
    defaultExpanded: false,
    fields: [
      { key: 'spread_pct_max', label: 'Spread % Max', type: 'number', placeholder: '10', step: '0.5' },
      { key: 'open_interest_min', label: 'Open Interest Min', type: 'number', placeholder: '0', step: '50' },
      { key: 'volume_min', label: 'Volume Min', type: 'number', placeholder: '0', step: '10' },
    ],
  },
  {
    id: 'underlying',
    label: 'Underlying',
    defaultExpanded: false,
    fields: [
      { key: 'underlying_price_min', label: 'Price Min', type: 'number', placeholder: '0', step: '5' },
      { key: 'underlying_price_max', label: 'Price Max', type: 'number', placeholder: '1000', step: '5' },
      { key: 'atr14_pct_min', label: 'ATR14 % Min', type: 'number', placeholder: '0', step: '0.5' },
      { key: 'atr14_pct_max', label: 'ATR14 % Max', type: 'number', placeholder: '20', step: '0.5' },
      { key: 'rs_20d_min', label: 'RS 20-Day Min', type: 'number', placeholder: '0', step: '0.5' },
      { key: 'feasibility_ratio_max', label: 'Feasibility Ratio Max', type: 'number', placeholder: '1.0', step: '0.05' },
    ],
  },
  {
    id: 'catalyst',
    label: 'Catalyst',
    defaultExpanded: false,
    fields: [
      { key: 'days_to_earnings_min', label: 'Days to Earnings Min', type: 'number', placeholder: '0', step: '1' },
      { key: 'days_to_earnings_max', label: 'Days to Earnings Max', type: 'number', placeholder: '60', step: '1' },
    ],
  },
]

const SCANNER_OPTIONS = ['BREAKOUT', 'COMPRESSION', 'CHEAP_OPTIONS', 'UNUSUAL_VOLUME']
const SCANNER_SHORT = { BREAKOUT: 'BRK', COMPRESSION: 'CMP', CHEAP_OPTIONS: 'CHP', UNUSUAL_VOLUME: 'UV' }

export default function ManualRuleForm({ isOpen, onClose }: ManualRuleFormProps) {
  const [name, setName] = useState('')
  const [mode, setMode] = useState<'production' | 'test'>('test')
  const [values, setValues] = useState<Record<string, string>>({})
  const [selectedScanners, setSelectedScanners] = useState<Set<string>>(new Set())
  const [scannerConfluence, setScannerConfluence] = useState(false)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    new Set(CRITERIA_GROUPS.filter((g) => g.defaultExpanded).map((g) => g.id))
  )
  const [error, setError] = useState<string | null>(null)
  const createRule = useCreateSetupRule()

  if (!isOpen) return null

  const toggleGroup = (id: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleScanner = (scanner: string) => {
    setSelectedScanners((prev) => {
      const next = new Set(prev)
      if (next.has(scanner)) next.delete(scanner)
      else next.add(scanner)
      return next
    })
  }

  const setValue = (key: string, val: string) => {
    setValues((prev) => {
      if (val === '') {
        const next = { ...prev }
        delete next[key]
        return next
      }
      return { ...prev, [key]: val }
    })
  }

  const buildCriteria = (): Record<string, unknown> => {
    const criteria: Record<string, unknown> = {}

    // Scanner criteria
    if (selectedScanners.size > 0) {
      criteria.scanners = Array.from(selectedScanners)
    }
    if (scannerConfluence) {
      criteria.scanner_confluence = true
    }

    // Number/select fields
    for (const group of CRITERIA_GROUPS) {
      for (const field of group.fields) {
        const val = values[field.key]
        if (val === undefined || val === '') continue
        if (field.type === 'number') {
          criteria[field.key] = parseFloat(val)
        } else {
          criteria[field.key] = val
        }
      }
    }

    return criteria
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!name.trim()) {
      setError('Rule name is required')
      return
    }

    const criteria = buildCriteria()
    if (Object.keys(criteria).length === 0) {
      setError('At least one criterion is required')
      return
    }

    createRule.mutate(
      {
        name: name.trim(),
        criteria,
        source: 'manual',
        mode,
      },
      {
        onSuccess: () => {
          // Reset form and close
          setName('')
          setValues({})
          setSelectedScanners(new Set())
          setScannerConfluence(false)
          setMode('test')
          setError(null)
          onClose()
        },
        onError: (err) => {
          setError(err instanceof Error ? err.message : 'Failed to create rule')
        },
      }
    )
  }

  const criteriaCount = Object.keys(buildCriteria()).length

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      <div className="relative z-10 w-full max-w-lg max-h-[85vh] flex flex-col rounded-2xl border border-oss-border bg-oss-card shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-6 pb-4">
          <div>
            <h2 className="text-lg font-semibold text-oss-text">Create Setup Rule</h2>
            <p className="text-xs text-oss-muted mt-0.5">
              Define criteria to match against opportunities
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-oss-muted hover:bg-oss-surface hover:text-oss-text transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Scrollable content */}
        <form onSubmit={handleSubmit} className="flex flex-col flex-1 overflow-hidden">
          <div className="flex-1 overflow-y-auto px-6 space-y-4">
            {/* Rule Name */}
            <div>
              <label className="block text-sm font-medium text-oss-muted mb-1.5">Rule Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Volatility Mean-Reversion Setup"
                className="w-full rounded-lg border border-oss-border bg-oss-bg px-3 py-2 text-sm text-oss-text placeholder-oss-muted/50 focus:border-oss-accent focus:outline-none focus:ring-1 focus:ring-oss-accent"
              />
            </div>

            {/* Criteria Groups */}
            {CRITERIA_GROUPS.map((group) => (
              <div key={group.id} className="rounded-lg border border-oss-border overflow-hidden">
                <button
                  type="button"
                  onClick={() => toggleGroup(group.id)}
                  className="flex items-center gap-2 w-full px-3 py-2 text-left text-sm font-medium text-oss-text hover:bg-oss-surface/50 transition-colors"
                >
                  {expandedGroups.has(group.id) ? (
                    <ChevronDown className="h-3.5 w-3.5 text-oss-muted" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 text-oss-muted" />
                  )}
                  {group.label}
                  {/* Show count of set criteria in this group */}
                  {countGroupCriteria(group, values, selectedScanners, scannerConfluence) > 0 && (
                    <span className="ml-auto text-[10px] font-mono text-oss-accent">
                      {countGroupCriteria(group, values, selectedScanners, scannerConfluence)} set
                    </span>
                  )}
                </button>

                {expandedGroups.has(group.id) && (
                  <div className="px-3 pb-3 pt-1 border-t border-oss-border space-y-3">
                    {/* Special scanner group */}
                    {group.id === 'scanners' && (
                      <>
                        <div>
                          <label className="block text-xs text-oss-muted mb-1.5">Scanners (any match)</label>
                          <div className="flex flex-wrap gap-1.5">
                            {SCANNER_OPTIONS.map((s) => (
                              <button
                                key={s}
                                type="button"
                                onClick={() => toggleScanner(s)}
                                className={clsx(
                                  'rounded px-2.5 py-1 text-xs font-medium border transition-colors',
                                  selectedScanners.has(s)
                                    ? 'bg-oss-accent/15 text-oss-accent border-oss-accent/30'
                                    : 'text-oss-muted border-oss-border hover:border-oss-muted'
                                )}
                              >
                                {SCANNER_SHORT[s as keyof typeof SCANNER_SHORT]}
                              </button>
                            ))}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setScannerConfluence(!scannerConfluence)}
                            className={clsx(
                              'relative h-5 w-9 rounded-full transition-colors',
                              scannerConfluence ? 'bg-oss-accent' : 'bg-oss-border'
                            )}
                          >
                            <span
                              className={clsx(
                                'absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform',
                                scannerConfluence ? 'translate-x-4' : 'translate-x-0.5'
                              )}
                            />
                          </button>
                          <span className="text-xs text-oss-muted">Require scanner confluence (2+)</span>
                        </div>
                      </>
                    )}

                    {/* Standard fields in 2-column grid */}
                    {group.fields.length > 0 && (
                      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
                        {group.fields.map((field) => (
                          <div key={field.key}>
                            <label className="block text-[11px] text-oss-muted mb-1">{field.label}</label>
                            {field.type === 'select' ? (
                              <select
                                value={values[field.key] ?? ''}
                                onChange={(e) => setValue(field.key, e.target.value)}
                                className="w-full rounded border border-oss-border bg-oss-bg px-2 py-1.5 text-xs font-mono text-oss-text focus:border-oss-accent focus:outline-none"
                              >
                                <option value="">Any</option>
                                {field.options?.map((opt) => (
                                  <option key={opt} value={opt}>{opt}</option>
                                ))}
                              </select>
                            ) : (
                              <input
                                type="number"
                                value={values[field.key] ?? ''}
                                onChange={(e) => setValue(field.key, e.target.value)}
                                placeholder={field.placeholder}
                                step={field.step}
                                className="w-full rounded border border-oss-border bg-oss-bg px-2 py-1.5 text-xs font-mono text-oss-text placeholder-oss-muted/40 focus:border-oss-accent focus:outline-none"
                              />
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Footer */}
          <div className="px-6 py-4 border-t border-oss-border space-y-3">
            {/* Mode toggle */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-oss-muted">Mode</span>
              <div className="flex rounded-md border border-oss-border overflow-hidden">
                <button
                  type="button"
                  onClick={() => setMode('test')}
                  className={clsx(
                    'px-3 py-1 text-xs font-medium transition-colors',
                    mode === 'test'
                      ? 'bg-oss-muted/20 text-oss-text'
                      : 'text-oss-muted hover:text-oss-text'
                  )}
                >
                  Test
                </button>
                <button
                  type="button"
                  onClick={() => setMode('production')}
                  className={clsx(
                    'px-3 py-1 text-xs font-medium transition-colors border-l border-oss-border',
                    mode === 'production'
                      ? 'bg-purple-400/20 text-purple-400'
                      : 'text-oss-muted hover:text-oss-text'
                  )}
                >
                  Production
                </button>
              </div>
            </div>

            {error && (
              <p className="text-xs text-oss-reject">{error}</p>
            )}

            <button
              type="submit"
              disabled={createRule.isPending}
              className="w-full rounded-lg bg-oss-accent py-2 text-sm font-medium text-oss-bg hover:bg-oss-accent/90 transition-colors disabled:opacity-50"
            >
              {createRule.isPending ? 'Creating...' : `Create Rule${criteriaCount > 0 ? ` (${criteriaCount} criteria)` : ''}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/** Count how many criteria are set in a group. */
function countGroupCriteria(
  group: CriteriaGroup,
  values: Record<string, string>,
  selectedScanners: Set<string>,
  scannerConfluence: boolean
): number {
  if (group.id === 'scanners') {
    return (selectedScanners.size > 0 ? 1 : 0) + (scannerConfluence ? 1 : 0)
  }
  return group.fields.filter((f) => values[f.key] !== undefined && values[f.key] !== '').length
}
