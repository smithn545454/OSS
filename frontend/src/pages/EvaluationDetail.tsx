import { useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { usePageTitle } from '@/hooks/usePageTitle'
import {
  ArrowLeft,
  CheckCircle,
  XCircle,
  Eye,
  TrendingUp,
  DollarSign,
  Activity,
  Zap,
  Shield,
  BarChart3,
  Crosshair,
} from 'lucide-react'
import { useEvaluationDetail, useGenerateThesis, useIsTradeTracked } from '@/hooks/useApi'
import type { 
  PillarScoreDetail, 
  GateResultDetail, 
  PaperPositionDetail,
  ScannerTriggerDetail,
  TradeThesis,
  Verdict,
  QualityTier
} from '@/lib/types'
import clsx from 'clsx'
import AITradeThesis from '@/components/AITradeThesis'
import TrackTradeModal from '@/components/TrackTradeModal'
import { formatDate, formatDateTime, formatExpirationDate } from '@/lib/formatTime'
import { calculateReturnPct, getReturnColor } from '@/lib/metrics'
import TradeContextSection from '@/components/evaluation/TradeContextSection'
import UnderlyingStockDetails from '@/components/evaluation/UnderlyingStockDetails'

// ============================================================================
// Score Bar Component
// ============================================================================

interface ScoreBarProps {
  score: number
  showThresholds?: boolean
  height?: 'sm' | 'md' | 'lg'
}

function ScoreBar({ score, showThresholds = true, height = 'md' }: ScoreBarProps) {
  const heightClasses = {
    sm: 'h-2',
    md: 'h-4',
    lg: 'h-6',
  }

  const getScoreColor = (score: number) => {
    if (score >= 75) return 'bg-oss-approve'
    if (score >= 65) return 'bg-oss-watch'
    return 'bg-oss-reject'
  }

  return (
    <div className="relative">
      <div className={clsx('w-full rounded-full bg-oss-bg overflow-hidden', heightClasses[height])}>
        <div
          className={clsx('h-full rounded-full transition-all duration-700 ease-out', getScoreColor(score))}
          style={{ width: `${score}%` }}
        />
      </div>
      {showThresholds && (
        <>
          {/* Threshold markers */}
          <div 
            className="absolute top-0 bottom-0 w-0.5 bg-oss-watch/50"
            style={{ left: '65%' }}
          >
            <span className="absolute -top-5 -translate-x-1/2 text-[10px] text-oss-watch">65</span>
          </div>
          <div 
            className="absolute top-0 bottom-0 w-0.5 bg-oss-approve/50"
            style={{ left: '75%' }}
          >
            <span className="absolute -top-5 -translate-x-1/2 text-[10px] text-oss-approve">75</span>
          </div>
        </>
      )}
    </div>
  )
}

// ============================================================================
// Verdict Badge Component
// ============================================================================

interface VerdictBadgeProps {
  verdict: Verdict
  size?: 'sm' | 'md' | 'lg'
}

function VerdictBadge({ verdict, size = 'md' }: VerdictBadgeProps) {
  const config = {
    APPROVE: { icon: CheckCircle, color: 'bg-oss-approve/10 text-oss-approve border-oss-approve/30' },
    WATCH: { icon: Eye, color: 'bg-oss-watch/10 text-oss-watch border-oss-watch/30' },
    REJECT: { icon: XCircle, color: 'bg-oss-reject/10 text-oss-reject border-oss-reject/30' },
  }[verdict] || { icon: XCircle, color: 'bg-oss-muted/10 text-oss-muted border-oss-muted/30' }

  const sizeClasses = {
    sm: 'px-2 py-0.5 text-xs gap-1',
    md: 'px-3 py-1 text-sm gap-1.5',
    lg: 'px-4 py-1.5 text-base gap-2',
  }

  const Icon = config.icon

  return (
    <span className={clsx(
      'inline-flex items-center rounded-full border font-medium',
      config.color,
      sizeClasses[size]
    )}>
      <Icon className={size === 'sm' ? 'h-3 w-3' : size === 'md' ? 'h-4 w-4' : 'h-5 w-5'} />
      {verdict}
    </span>
  )
}

// ============================================================================
// Quality Tier Badge Component
// ============================================================================

function QualityTierBadge({ tier }: { tier: QualityTier | null }) {
  if (!tier) return null

  const config = {
    TIER_1: { label: 'Tier 1', color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
    TIER_2: { label: 'Tier 2', color: 'bg-sky-500/10 text-sky-400 border-sky-500/30' },
    TIER_3: { label: 'Tier 3', color: 'bg-amber-500/10 text-amber-400 border-amber-500/30' },
  }[tier] || { label: tier, color: 'bg-oss-muted/10 text-oss-muted border-oss-muted/30' }

  return (
    <span className={clsx(
      'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium',
      config.color
    )}>
      {config.label}
    </span>
  )
}

// ============================================================================
// Contract Card Component
// ============================================================================

interface ContractCardProps {
  evaluation: {
    option_ticker: string
    option_type: string
    strike: number
    expiration_date: string
    dte: number
    dte_bucket: string
    underlying_price: number
    bid: number
    ask: number
    mid: number
    spread_pct: number
    iv: number
    delta: number
    gamma: number
    theta: number
    vega: number
    open_interest: number
    volume: number
    breakeven_price: number
    required_move_pct: number
    expected_move_pct: number
    feasibility_ratio: number
  }
}

function ContractCard({ evaluation }: ContractCardProps) {
  const isCall = evaluation.option_type === 'CALL'

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="text-lg font-medium text-oss-text">Contract Details</h3>
          <p className="font-mono text-sm text-oss-muted mt-1">{evaluation.option_ticker}</p>
        </div>
        <div className={clsx(
          'rounded-lg px-3 py-1.5 text-sm font-medium',
          isCall ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'
        )}>
          {evaluation.option_type}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Strike</p>
          <p className="font-mono text-lg text-oss-text">${evaluation.strike.toFixed(2)}</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Expiration</p>
          <p className="text-sm text-oss-text">{evaluation.expiration_date}</p>
          <p className="text-xs text-oss-muted">({evaluation.dte} DTE, Bucket {evaluation.dte_bucket})</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Underlying</p>
          <p className="font-mono text-lg text-oss-text">${evaluation.underlying_price.toFixed(2)}</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Breakeven</p>
          <p className="font-mono text-lg text-oss-text">${evaluation.breakeven_price.toFixed(2)}</p>
          <p className="text-xs text-oss-muted">({evaluation.required_move_pct.toFixed(1)}% move required)</p>
        </div>
      </div>

      <div className="mt-4 pt-4 border-t border-oss-border grid grid-cols-3 md:grid-cols-6 gap-4">
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Bid/Ask</p>
          <p className="font-mono text-sm text-oss-text">
            ${evaluation.bid.toFixed(2)} / ${evaluation.ask.toFixed(2)}
          </p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Mid</p>
          <p className="font-mono text-sm text-oss-text">${evaluation.mid.toFixed(2)}</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Spread</p>
          <p className={clsx(
            'font-mono text-sm',
            evaluation.spread_pct <= 5 ? 'text-oss-approve' : 
            evaluation.spread_pct <= 8 ? 'text-oss-watch' : 'text-oss-reject'
          )}>
            {evaluation.spread_pct.toFixed(1)}%
          </p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">IV</p>
          <p className="font-mono text-sm text-oss-text">{(evaluation.iv * 100).toFixed(1)}%</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">OI</p>
          <p className="font-mono text-sm text-oss-text">{evaluation.open_interest.toLocaleString()}</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Volume</p>
          <p className="font-mono text-sm text-oss-text">{evaluation.volume.toLocaleString()}</p>
        </div>
      </div>

      <div className="mt-4 pt-4 border-t border-oss-border grid grid-cols-4 gap-4">
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Delta</p>
          <p className="font-mono text-sm text-oss-text">{evaluation.delta.toFixed(3)}</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Gamma</p>
          <p className="font-mono text-sm text-oss-text">{evaluation.gamma.toFixed(4)}</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Theta</p>
          <p className="font-mono text-sm text-oss-reject">{evaluation.theta.toFixed(3)}</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Vega</p>
          <p className="font-mono text-sm text-oss-text">{evaluation.vega.toFixed(3)}</p>
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Gate Results Panel Component
// ============================================================================

interface GateResultsPanelProps {
  gates: GateResultDetail[]
  allPassed: boolean
}

function GateResultsPanel({ gates, allPassed }: GateResultsPanelProps) {
  return (
    <div className={clsx(
      'rounded-xl border p-6',
      allPassed 
        ? 'border-oss-approve/30 bg-oss-approve/5' 
        : 'border-oss-reject/30 bg-oss-reject/5'
    )}>
      <div className="flex items-center gap-3 mb-4">
        <Shield className={clsx('h-5 w-5', allPassed ? 'text-oss-approve' : 'text-oss-reject')} />
        <h3 className="text-lg font-medium text-oss-text">
          Hard Gates {allPassed ? '✓ All Passed' : '✗ Failed'}
        </h3>
      </div>

      <div className="space-y-2">
        {gates.map((gate) => (
          <div 
            key={gate.gate_id}
            className={clsx(
              'flex items-center justify-between rounded-lg px-4 py-3',
              !gate.enabled ? 'bg-oss-bg/50 opacity-50' :
              gate.passed ? 'bg-oss-surface' : 'bg-oss-reject/10'
            )}
          >
            <div className="flex items-center gap-3">
              {gate.passed ? (
                <CheckCircle className="h-4 w-4 text-oss-approve" />
              ) : (
                <XCircle className="h-4 w-4 text-oss-reject" />
              )}
              <div>
                <p className="text-sm font-medium text-oss-text">
                  {gate.gate_id.replace(/_/g, ' ')}
                </p>
                {!gate.enabled && (
                  <p className="text-xs text-oss-muted">Disabled</p>
                )}
              </div>
            </div>
            <div className="text-right">
              <p className={clsx(
                'font-mono text-sm',
                gate.passed ? 'text-oss-text' : 'text-oss-reject'
              )}>
                {gate.measured_value.toFixed(2)} {gate.units}
              </p>
              <p className="text-xs text-oss-muted">
                {gate.operator === 'gte' ? '≥' : gate.operator === 'lte' ? '≤' : gate.operator}{' '}
                {gate.threshold_value.toFixed(2)}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ============================================================================
// Pillar Card Component
// ============================================================================

interface PillarCardProps {
  pillar: PillarScoreDetail
}

function PillarCard({ pillar }: PillarCardProps) {
  const pillarConfig = {
    DIRECTIONAL: { icon: TrendingUp, color: 'text-sky-400', label: 'Directional Edge' },
    VOLATILITY: { icon: Activity, color: 'text-purple-400', label: 'Volatility Edge' },
    STRUCTURE: { icon: BarChart3, color: 'text-amber-400', label: 'Structure & Quality' },
  }[pillar.pillar_id] || { icon: Zap, color: 'text-oss-accent', label: pillar.pillar_id }

  const Icon = pillarConfig.icon
  
  const getScoreColor = (score: number) => {
    if (score >= 70) return 'text-oss-approve'
    if (score >= 55) return 'text-oss-watch'
    return 'text-oss-reject'
  }

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
      <div className="flex items-center gap-3 mb-4">
        <Icon className={clsx('h-5 w-5', pillarConfig.color)} />
        <h4 className="font-medium text-oss-text">{pillarConfig.label}</h4>
      </div>

      <div className="flex items-baseline gap-2 mb-4">
        <span className={clsx('text-4xl font-bold', getScoreColor(pillar.score))}>
          {pillar.score.toFixed(0)}
        </span>
        <span className="text-sm text-oss-muted">/ 100</span>
      </div>

      <ScoreBar score={pillar.score} showThresholds={false} height="sm" />

      {pillar.tags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {pillar.tags.map((tag) => (
            <span 
              key={tag} 
              className="rounded-full bg-oss-bg px-2 py-0.5 text-xs text-oss-muted"
            >
              {tag.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}

      <div className="mt-4 pt-4 border-t border-oss-border">
        <p className="text-xs text-oss-muted mb-2">Top Contributors</p>
        <div className="space-y-2">
          {pillar.contributors.slice(0, 3).map((c) => (
            <div key={c.feature_name} className="flex items-center justify-between">
              <span className="text-xs text-oss-text truncate">
                {c.feature_name.replace(/_/g, ' ')}
              </span>
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-oss-muted">
                  {typeof c.raw_value === 'number'
                    ? c.raw_value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                    : typeof c.raw_value === 'object' && c.raw_value !== null
                      ? (() => {
                          const vals = Object.values(c.raw_value as Record<string, unknown>)
                          const num = vals.find((v): v is number => typeof v === 'number')
                          return num !== undefined ? num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
                        })()
                      : String(c.raw_value ?? '—')}
                </span>
                <span className={clsx(
                  'font-mono text-xs',
                  c.subscore >= 70 ? 'text-oss-approve' : 
                  c.subscore >= 50 ? 'text-oss-watch' : 'text-oss-reject'
                )}>
                  +{c.weighted_contribution.toFixed(1)}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Paper Tracking Panel Component
// ============================================================================

interface PaperTrackingPanelProps {
  position: PaperPositionDetail | null
}

function PaperTrackingPanel({ position }: PaperTrackingPanelProps) {
  if (!position) {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
        <div className="flex items-center gap-3 mb-4">
          <DollarSign className="h-5 w-5 text-oss-muted" />
          <h3 className="text-lg font-medium text-oss-text">Paper Tracking</h3>
        </div>
        <p className="text-sm text-oss-muted">No position tracked for this evaluation</p>
      </div>
    )
  }

  const isProfit = position.current_pnl_pct > 0
  const isOpen = position.status === 'OPEN'

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <DollarSign className={clsx('h-5 w-5', isProfit ? 'text-oss-approve' : 'text-oss-reject')} />
          <h3 className="text-lg font-medium text-oss-text">Paper Tracking</h3>
        </div>
        <span className={clsx(
          'rounded-full px-2.5 py-0.5 text-xs font-medium',
          isOpen ? 'bg-oss-accent/10 text-oss-accent' : 'bg-oss-muted/10 text-oss-muted'
        )}>
          {position.status}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Entry Price</p>
          <p className="font-mono text-lg text-oss-text">${position.entry_price.toFixed(2)}</p>
          <p className="text-xs text-oss-muted">{formatDate(position.entry_date)}</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Current Price</p>
          <p className="font-mono text-lg text-oss-text">${position.current_price.toFixed(2)}</p>
          <p className="text-xs text-oss-muted">{position.days_held} days held</p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">Current P&L</p>
          <p className={clsx(
            'font-mono text-lg font-semibold',
            isProfit ? 'text-oss-approve' : 'text-oss-reject'
          )}>
            {isProfit ? '+' : ''}{position.current_pnl_pct.toFixed(1)}%
          </p>
        </div>
        <div className="space-y-1">
          <p className="text-xs text-oss-muted">MFE / MAE</p>
          <p className="font-mono text-sm">
            <span className="text-oss-approve">+{position.max_favorable_excursion.toFixed(1)}%</span>
            {' / '}
            <span className="text-oss-reject">{position.max_adverse_excursion.toFixed(1)}%</span>
          </p>
        </div>
      </div>

      {!isOpen && position.exit_reason && (
        <div className="mt-4 pt-4 border-t border-oss-border">
          <div className="flex items-center gap-4">
            <div className="space-y-1">
              <p className="text-xs text-oss-muted">Exit Reason</p>
              <p className="text-sm text-oss-text">{position.exit_reason.replace(/_/g, ' ')}</p>
            </div>
            <div className="space-y-1">
              <p className="text-xs text-oss-muted">Exit Price</p>
              <p className="font-mono text-sm text-oss-text">${position.exit_price?.toFixed(2)}</p>
            </div>
            <div className="space-y-1">
              <p className="text-xs text-oss-muted">Exit Date</p>
              <p className="text-sm text-oss-text">{formatDate(position.exit_date!)}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ============================================================================
// Scanner Triggers Component
// ============================================================================

interface ScannerTriggersProps {
  triggers: ScannerTriggerDetail[]
}

function ScannerTriggers({ triggers }: ScannerTriggersProps) {
  if (triggers.length === 0) return null

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <div className="flex items-center gap-3 mb-4">
        <Zap className="h-5 w-5 text-oss-accent" />
        <h3 className="text-lg font-medium text-oss-text">Scanner Triggers</h3>
      </div>

      <div className="space-y-3">
        {triggers.map((trigger, idx) => (
          <div key={idx} className="rounded-lg bg-oss-bg p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-oss-accent">
                {trigger.scanner_type.replace(/_/g, ' ')}
              </span>
              <span className="text-xs text-oss-muted">{formatDateTime(trigger.triggered_at)}</span>
            </div>
            <div className="flex flex-wrap gap-1 mb-2">
              {trigger.reason_codes.map((code) => (
                <span 
                  key={code} 
                  className="rounded-full bg-oss-surface px-2 py-0.5 text-xs text-oss-muted"
                >
                  {code}
                </span>
              ))}
            </div>
            {Object.keys(trigger.metrics).length > 0 && (
              <div className="grid grid-cols-2 gap-2 mt-2 pt-2 border-t border-oss-border">
                {Object.entries(trigger.metrics).map(([key, value]) => (
                  <div key={key} className="flex justify-between text-xs">
                    <span className="text-oss-muted">{key.replace(/_/g, ' ')}</span>
                    <span className="font-mono text-oss-text">{typeof value === 'number' ? value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : String(value)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}


// ============================================================================
// Matched Setup Rules Panel
// ============================================================================

interface MatchedRuleDisplay {
  rule_id: string
  name: string
  mode: 'production' | 'test'
  criteria?: Record<string, unknown>
  performance_at_creation?: {
    win_rate?: number
    avg_return?: number
    sample_size?: number
    avg_days_held?: number
  }
}

function MatchedRulesPanel({ rules }: { rules: MatchedRuleDisplay[] }) {
  if (!rules || rules.length === 0) return null

  return (
    <div className="rounded-xl border border-purple-400/20 bg-purple-400/5 p-6">
      <div className="flex items-center gap-3 mb-4">
        <BarChart3 className="h-5 w-5 text-purple-400" />
        <h3 className="text-lg font-medium text-oss-text">Matching Setup Rules</h3>
        <span className="text-xs text-oss-muted">({rules.length})</span>
      </div>

      <div className="space-y-3">
        {rules.map((rule) => (
          <div key={rule.rule_id} className="rounded-lg bg-oss-surface p-4 border border-oss-border">
            <div className="flex items-center gap-2 mb-2">
              <span className="font-medium text-sm text-oss-text">{rule.name}</span>
              <span
                className={clsx(
                  'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium border',
                  rule.mode === 'production'
                    ? 'bg-purple-400/15 text-purple-400 border-purple-400/25'
                    : 'bg-oss-muted/10 text-oss-muted border-oss-border'
                )}
              >
                {rule.mode === 'production' ? 'Production' : 'Test'}
              </span>
            </div>
            {rule.performance_at_creation && (
              <div className="flex items-center gap-4 text-xs text-oss-muted mb-2">
                {rule.performance_at_creation.win_rate != null && (
                  <span>
                    WR: <span className="font-mono text-oss-approve">
                      {(rule.performance_at_creation.win_rate * 100).toFixed(1)}%
                    </span>
                  </span>
                )}
                {rule.performance_at_creation.avg_return != null && (
                  <span>
                    Avg Return: <span className="font-mono text-oss-text">
                      {rule.performance_at_creation.avg_return.toFixed(1)}%
                    </span>
                  </span>
                )}
                {rule.performance_at_creation.avg_days_held != null && (
                  <span>
                    Avg Hold: <span className="font-mono text-oss-text">
                      {rule.performance_at_creation.avg_days_held.toFixed(1)}d
                    </span>
                  </span>
                )}
                {rule.performance_at_creation.sample_size != null && (
                  <span>n={rule.performance_at_creation.sample_size}</span>
                )}
              </div>
            )}
            {rule.criteria && Object.keys(rule.criteria).length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(rule.criteria)
                  .filter(([, v]) => v != null)
                  .map(([key, val]) => (
                    <span
                      key={key}
                      className="inline-block rounded bg-oss-bg px-2 py-0.5 text-[10px] font-mono text-oss-muted border border-oss-border"
                    >
                      {key}: {Array.isArray(val) ? (val as string[]).join(', ') : String(val)}
                    </span>
                  ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}


// ============================================================================
// Decision Explanation Component
// ============================================================================

interface DecisionExplanationProps {
  decision: {
    verdict: Verdict
    final_score: number
    primary_reason_code: string
    supporting_reason_codes: string[]
    failed_gates: string[]
  }
}

function DecisionExplanation({ decision }: DecisionExplanationProps) {
  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <h3 className="text-lg font-medium text-oss-text mb-4">Decision Explanation</h3>

      <div className="space-y-4">
        <div>
          <p className="text-xs text-oss-muted mb-1">Primary Reason</p>
          <span className="inline-flex items-center rounded-lg bg-oss-bg px-3 py-1.5 text-sm font-medium text-oss-accent">
            {decision.primary_reason_code.replace(/_/g, ' ')}
          </span>
        </div>

        {decision.supporting_reason_codes.length > 0 && (
          <div>
            <p className="text-xs text-oss-muted mb-2">Supporting Reasons</p>
            <div className="flex flex-wrap gap-2">
              {decision.supporting_reason_codes.map((code) => (
                <span 
                  key={code} 
                  className="rounded-full bg-oss-bg px-2.5 py-1 text-xs text-oss-muted"
                >
                  {code.replace(/_/g, ' ')}
                </span>
              ))}
            </div>
          </div>
        )}

        {decision.failed_gates.length > 0 && (
          <div>
            <p className="text-xs text-oss-muted mb-2">Failed Gates</p>
            <div className="flex flex-wrap gap-2">
              {decision.failed_gates.map((gate) => (
                <span 
                  key={gate} 
                  className="rounded-full bg-oss-reject/10 px-2.5 py-1 text-xs text-oss-reject"
                >
                  {gate.replace(/_/g, ' ')}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="pt-4 border-t border-oss-border">
          <p className="text-xs text-oss-muted mb-2">Score Band</p>
          <div className="flex items-center gap-4">
            <div className="flex-1">
              <div className="relative h-3 rounded-full bg-oss-bg overflow-hidden">
                <div className="absolute inset-y-0 left-0 bg-oss-reject/40" style={{ width: '65%' }} />
                <div className="absolute inset-y-0 left-[65%] bg-oss-watch/40" style={{ width: '10%' }} />
                <div className="absolute inset-y-0 left-[75%] bg-oss-approve/40" style={{ width: '25%' }} />
                <div 
                  className="absolute top-0 bottom-0 w-1 bg-oss-text"
                  style={{ left: `${decision.final_score}%` }}
                />
              </div>
              <div className="flex justify-between mt-1 text-[10px] text-oss-muted">
                <span>0</span>
                <span>REJECT</span>
                <span>65</span>
                <span>WATCH</span>
                <span>75</span>
                <span>APPROVE</span>
                <span>100</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Gate Journey Component (Timeline Visualization)
// ============================================================================

interface GateJourneyProps {
  gates: GateResultDetail[]
}

function GateJourney({ gates }: GateJourneyProps) {
  // Calculate margin status for each gate
  const getMarginStatus = (gate: GateResultDetail): 'pass' | 'pass-narrow' | 'fail' => {
    if (!gate.passed) return 'fail'
    
    // Check if passed by narrow margin (within 10%)
    const threshold = gate.threshold_value
    const measured = gate.measured_value
    
    const op = gate.operator as string
    if (op === 'gte' || op === '>=') {
      const margin = (measured - threshold) / threshold
      return margin < 0.1 ? 'pass-narrow' : 'pass'
    } else if (op === 'lte' || op === '<=') {
      const margin = (threshold - measured) / threshold
      return margin < 0.1 ? 'pass-narrow' : 'pass'
    }
    
    return 'pass'
  }
  
  const enabledGates = gates.filter(g => g.enabled)

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
      <h3 className="text-lg font-medium text-oss-text mb-6">Gate Journey</h3>
      
      <div className="gate-journey">
        {enabledGates.map((gate) => {
          const status = getMarginStatus(gate)
          
          return (
            <div key={gate.gate_id} className="gate-node">
              <div className={`gate-node-circle gate-node-circle--${status}`}>
                {status === 'fail' ? '✗' : '✓'}
              </div>
              <span className="gate-node-label">
                {gate.gate_id.replace(/_/g, ' ')}
              </span>
            </div>
          )
        })}
      </div>
      
      {/* Legend */}
      <div className="flex justify-center gap-6 mt-6 pt-4 border-t border-oss-border">
        <div className="flex items-center gap-2 text-xs text-oss-muted">
          <span className="w-3 h-3 rounded-full bg-oss-approve" />
          Pass
        </div>
        <div className="flex items-center gap-2 text-xs text-oss-muted">
          <span className="w-3 h-3 rounded-full bg-oss-watch" />
          Pass (Narrow)
        </div>
        <div className="flex items-center gap-2 text-xs text-oss-muted">
          <span className="w-3 h-3 rounded-full bg-oss-reject" />
          Fail
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Hero Section Component
// ============================================================================

interface HeroSectionProps {
  evaluation: {
    underlying_ticker: string
    option_type: string
    strike: number
    expiration_date: string
    underlying_price: number
    mid: number
  }
  thetaAdjustedEV: number
  companyName?: string | null
  decision: {
    verdict: Verdict
    final_score: number
    quality_tier: QualityTier | null
  } | null
}

function HeroSection({ evaluation, thetaAdjustedEV, companyName, decision }: HeroSectionProps) {
  const isCall = evaluation.option_type === 'CALL'
  const returnPct = calculateReturnPct(thetaAdjustedEV, evaluation.mid)

  return (
    <div className="detail-hero">
      <div>
        {/* Ticker */}
        <div className="flex items-center gap-4 mb-1">
          <span className="detail-ticker">{evaluation.underlying_ticker}</span>
          <span className={clsx(
            'rounded-lg px-4 py-2 text-lg font-semibold',
            isCall ? 'bg-emerald-500/15 text-emerald-400' : 'bg-rose-500/15 text-rose-400'
          )}>
            {evaluation.option_type}
          </span>
          {decision && <VerdictBadge verdict={decision.verdict} size="lg" />}
          {decision?.quality_tier && <QualityTierBadge tier={decision.quality_tier} />}
        </div>
        {companyName && (
          <p className="text-sm text-oss-muted mb-4">{companyName}</p>
        )}

        {/* Contract details */}
        <div className="text-lg text-oss-muted" style={{ fontFamily: 'var(--font-primary)' }}>
          ${evaluation.strike} Strike · Exp {formatExpirationDate(evaluation.expiration_date)}
        </div>

        {/* Decision Metrics Row */}
        <div className="flex mt-6 pt-4 border-t border-oss-border"
             style={{ justifyContent: 'center', alignItems: 'center', gap: '80px' }}>
          {/* θ-Adj EV */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px' }}>
            <span style={{
              fontFamily: 'var(--font-primary)', fontSize: '13px',
              color: 'rgba(255, 255, 255, 0.5)', fontWeight: 400,
            }}>θ-Adj EV</span>
            <span style={{
              fontFamily: 'var(--font-primary)', fontSize: '22px',
              color: thetaAdjustedEV != null ? '#00E5CC' : 'rgba(255, 255, 255, 0.3)',
              fontWeight: 700,
            }}>
              {thetaAdjustedEV != null ? `$${Math.round(thetaAdjustedEV)}` : '—'}
            </span>
          </div>
          {/* Return % */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px' }}>
            <span style={{
              fontFamily: 'var(--font-primary)', fontSize: '13px',
              color: 'rgba(255, 255, 255, 0.5)', fontWeight: 400,
            }}>Return</span>
            <span style={{
              fontFamily: 'var(--font-primary)', fontSize: '22px',
              color: getReturnColor(returnPct), fontWeight: 700,
            }}>
              {returnPct != null ? `${returnPct.toFixed(1)}%` : '—'}
            </span>
          </div>
        </div>
      </div>

      {/* Price and Score */}
      <div className="text-right">
        <div className="mb-4">
          <span className="text-xs text-oss-muted block mb-1">Premium</span>
          <span className="font-mono text-3xl font-bold text-oss-text">
            ${evaluation.mid.toFixed(2)}
          </span>
        </div>
        {decision && (
          <div>
            <span className="text-xs text-oss-muted block mb-1">Final Score</span>
            <span className={clsx(
              'font-mono text-3xl font-bold',
              decision.final_score >= 75 ? 'text-oss-approve' :
              decision.final_score >= 65 ? 'text-oss-watch' : 'text-oss-reject'
            )}>
              {decision.final_score.toFixed(0)}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================================
// Main Evaluation Detail Page
// ============================================================================

export default function EvaluationDetail() {
  const { ticker, evaluationId } = useParams<{
    ticker: string
    evaluationId: string
  }>()

  const { data, isLoading, error } = useEvaluationDetail(
    ticker || '',
    evaluationId || ''
  )

  const optionType = data?.evaluation?.option_type
  const titleSuffix = optionType ? `${ticker} ${optionType} Evaluation Detail` : `${ticker} Evaluation`
  usePageTitle(titleSuffix)

  const generateThesis = useGenerateThesis()
  const hasTriggeredThesis = useRef(false)
  const [showTrackModal, setShowTrackModal] = useState(false)
  const { data: trackStatus } = useIsTradeTracked(evaluationId || '')

  // Auto-generate thesis on page load for APPROVE evaluations
  useEffect(() => {
    const verdict = data?.evaluation?.decision?.verdict
    const thesisStatus = data?.thesis?.status
    if (
      data &&
      evaluationId &&
      ticker &&
      verdict === 'APPROVE' &&
      thesisStatus !== 'COMPLETED' &&
      thesisStatus !== 'GENERATING' &&
      thesisStatus !== 'RATE_LIMITED' &&
      !generateThesis.isPending &&
      !hasTriggeredThesis.current
    ) {
      hasTriggeredThesis.current = true
      generateThesis.mutate({ evaluationId, ticker })
    }
  }, [data, evaluationId, ticker, generateThesis.isPending]) // eslint-disable-line react-hooks/exhaustive-deps

  if (isLoading) {
    return (
      <div className="space-y-8">
        <div className="flex items-center gap-4">
          <Link 
            to="/opportunities" 
            className="rounded-lg p-2 hover:bg-oss-surface transition-colors"
          >
            <ArrowLeft className="h-5 w-5 text-oss-muted" />
          </Link>
          <div className="h-8 w-48 animate-pulse rounded-lg bg-oss-surface" />
        </div>
        <div className="grid gap-6">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-48 animate-pulse rounded-xl border border-oss-border bg-oss-surface" />
          ))}
        </div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="space-y-8">
        <div className="flex items-center gap-4">
          <Link 
            to="/opportunities" 
            className="rounded-lg p-2 hover:bg-oss-surface transition-colors"
          >
            <ArrowLeft className="h-5 w-5 text-oss-muted" />
          </Link>
          <h1 className="text-2xl font-semibold text-oss-text">Evaluation Detail</h1>
        </div>
        <div className="rounded-xl border border-oss-reject/30 bg-oss-reject/5 p-6 text-center">
          <XCircle className="h-12 w-12 text-oss-reject mx-auto mb-4" />
          <p className="text-oss-text">Failed to load evaluation</p>
          <p className="text-sm text-oss-muted mt-2">
            {error instanceof Error ? error.message : 'Unknown error'}
          </p>
        </div>
      </div>
    )
  }

  const { evaluation, pillar_scores, gate_results, position, scanner_triggers, thesis, summary, matched_rules } = data
  const decision = evaluation.decision
  // Prefer polled data when COMPLETED, fall back to mutation result, then query data
  const mutationThesis = generateThesis.data as TradeThesis | undefined
  const effectiveThesis = (thesis?.status === 'COMPLETED' ? thesis : null) ?? mutationThesis ?? thesis

  return (
    <div className="space-y-8">
      {/* Back Link + Track Trade Button */}
      <div className="flex items-center justify-between">
        <Link
          to="/opportunities"
          className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-oss-muted hover:bg-oss-surface hover:text-oss-text transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Opportunities
        </Link>
        {decision?.verdict === 'APPROVE' && (
          trackStatus?.tracked ? (
            <Link
              to={`/trades`}
              className="inline-flex items-center gap-2 rounded-lg border border-oss-approve/30 bg-oss-approve/10 px-4 py-2 text-sm font-medium text-oss-approve"
            >
              <CheckCircle className="h-4 w-4" />
              Trade Tracked
            </Link>
          ) : (
            <button
              onClick={() => setShowTrackModal(true)}
              className="inline-flex items-center gap-2 rounded-lg bg-oss-accent px-4 py-2 text-sm font-semibold text-black hover:bg-oss-accent/90 transition-colors"
            >
              <Crosshair className="h-4 w-4" />
              Track This Trade
            </button>
          )
        )}
      </div>

      {/* Hero Section */}
      <HeroSection
        evaluation={evaluation as HeroSectionProps['evaluation']}
        thetaAdjustedEV={data.thetaAdjustedEV}
        companyName={data.company_name}
        decision={decision ? {
          verdict: decision.verdict as Verdict,
          final_score: decision.final_score,
          quality_tier: decision.quality_tier as QualityTier | null,
        } : null}
      />

      {/* Underlying Stock Details */}
      <UnderlyingStockDetails ticker={ticker!} />

      {/* Contract Card */}
      <ContractCard evaluation={evaluation as ContractCardProps['evaluation']} />

      {/* Pillar Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {pillar_scores.map((pillar) => (
          <PillarCard key={pillar.pillar_id} pillar={pillar} />
        ))}
      </div>

      {/* Trades Like This One */}
      <TradeContextSection
        optionType={evaluation.option_type}
        scanner={scanner_triggers?.[0]?.scanner_type}
        score={decision?.final_score}
        dteBucket={evaluation.dte_bucket}
      />

      {/* Decision + Scanner + AI Thesis */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {decision && (
          <DecisionExplanation decision={decision} />
        )}
        <ScannerTriggers triggers={scanner_triggers} />
      </div>

      {/* Matched Setup Rules */}
      {matched_rules && matched_rules.length > 0 && (
        <MatchedRulesPanel rules={matched_rules as MatchedRuleDisplay[]} />
      )}

      {/* AI Trade Thesis */}
      {decision?.verdict === 'APPROVE' && (
        <AITradeThesis
          thesis={effectiveThesis as TradeThesis | null}
          onGenerate={() => evaluationId && ticker && generateThesis.mutate({ evaluationId, ticker })}
          isGenerating={generateThesis.isPending}
          generateError={generateThesis.error as Error | null}
        />
      )}

      {/* Paper Tracking */}
      <PaperTrackingPanel position={position} />

      {/* Gate Journey Timeline */}
      <GateJourney gates={gate_results} />

      {/* Gate Results (Detailed) */}
      <GateResultsPanel gates={gate_results} allPassed={summary.all_gates_passed} />

      {/* Track Trade Modal */}
      {ticker && evaluationId && (
        <TrackTradeModal
          isOpen={showTrackModal}
          onClose={() => setShowTrackModal(false)}
          ticker={ticker}
          evaluationId={evaluationId}
          askPrice={evaluation.ask ?? evaluation.mid ?? 0}
          optionTicker={evaluation.option_ticker ?? ''}
        />
      )}
    </div>
  )
}
