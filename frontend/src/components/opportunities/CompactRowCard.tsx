/**
 * Compact Row Card Component
 *
 * Displays a single opportunity as a compact horizontal row with 5 zones:
 * Score Ring | Rank | Identity | Signals & Setup Rules | Metrics
 *
 * Per SPEC-opportunities-compact-row-redesign.md
 */

import { useNavigate } from 'react-router-dom'
import type { ApproveEvaluation, ContractQuote } from '@/lib/types'
import { ConvictionGauge } from './ConvictionGauge'
import { TierBadge, ArchetypePill, ConvictionInline } from './V5Badges'
import { formatRelativeTime, formatExpirationDate, getAgeFreshness } from '@/lib/formatTime'

interface CompactRowCardProps {
  evaluation: ApproveEvaluation
  rank: number
  liveQuote?: ContractQuote
  className?: string
}

/** Urgency config per spec §3.4: only renders for DTE ≤ 14 */
function getUrgencyConfig(dte: number): { color: string; label: string } | null {
  if (dte <= 5) return { color: '#FF4D6A', label: 'ACT NOW' }
  if (dte <= 14) return { color: '#FFB800', label: 'HOURS' }
  return null
}

export function CompactRowCard({ evaluation, rank, liveQuote, className = '' }: CompactRowCardProps) {
  const navigate = useNavigate()
  const freshness = getAgeFreshness(evaluation.evaluated_at)
  const hasSetupRules = (evaluation.matchedRules?.length ?? 0) > 0

  const handleClick = () => {
    navigate(`/evaluation/${evaluation.underlying_ticker}/${evaluation.evaluation_id}`)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      handleClick()
    }
  }

  const freshnessClass = freshness ? `compact-row-card--${freshness}` : ''
  const setupClass = hasSetupRules ? 'compact-row-card--has-setup' : ''

  return (
    <article
      className={`compact-row-card ${setupClass} ${freshnessClass} ${className}`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
      aria-label={`${evaluation.underlying_ticker} ${evaluation.option_type} at ${evaluation.strike}, ranked ${rank}, conviction ${Math.round(evaluation.convictionScore ?? 0)}`}
    >
      {/* Zone 1: Score Ring */}
      <div style={{ flexShrink: 0 }}>
        <ConvictionGauge score={evaluation.convictionScore ?? 0} size="compact" />
      </div>

      {/* Zone 2: Rank */}
      <div style={{
        width: '24px',
        textAlign: 'center',
        flexShrink: 0,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: '12px',
        fontWeight: 600,
        color: '#4A5168',
      }}>
        #{rank}
      </div>

      {/* Zone 3: Identity Block */}
      <IdentityBlock evaluation={evaluation} />

      {/* Zone 4: Signals & Setup Rules */}
      <SignalsZone evaluation={evaluation} />

      {/* Zone 5: Metrics */}
      <MetricsZone evaluation={evaluation} liveQuote={liveQuote} />
    </article>
  )
}

function IdentityBlock({ evaluation }: { evaluation: ApproveEvaluation }) {
  const isCall = evaluation.option_type === 'CALL'

  // Format expiry: "Mar 26" (short, no year for current year)
  const expiryDisplay = formatExpirationDate(evaluation.expiration_date)
    .replace(/,\s*\d{4}$/, '') // Remove year if present for compactness

  return (
    <div style={{ minWidth: '160px', flexShrink: 0 }}>
      {/* Line 1: Ticker + Type + Strike + TOP PICK */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{
          fontSize: '16px',
          fontWeight: 700,
          color: '#E8ECF4',
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {evaluation.underlying_ticker}
        </span>

        {/* Small type badge per spec §3.1 */}
        <span style={{
          padding: '2px 6px',
          borderRadius: '4px',
          fontSize: '10px',
          fontWeight: 700,
          fontFamily: "'JetBrains Mono', monospace",
          letterSpacing: '0.05em',
          lineHeight: 1,
          backgroundColor: isCall ? '#00E5CC20' : '#FF4D6A20',
          color: isCall ? '#00E5CC' : '#FF4D6A',
          border: `1px solid ${isCall ? '#00E5CC40' : '#FF4D6A40'}`,
        }}>
          {evaluation.option_type}
        </span>

        <span style={{
          fontSize: '12px',
          color: '#8892A5',
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          ${evaluation.strike}
        </span>

        <TierBadge tier={evaluation.decision?.quality_tier} size="xs" />
      </div>

      {/* Line 2: Expiry + DTE + v5 conviction (HR / P) when present */}
      <div style={{
        marginTop: '2px',
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        fontSize: '11px',
        color: '#4A5168',
      }}>
        <span>{expiryDisplay} · {evaluation.dte}d</span>
        <ConvictionInline evaluation={evaluation} size="xs" />
      </div>
    </div>
  )
}

function SignalsZone({ evaluation }: { evaluation: ApproveEvaluation }) {
  const hasSetupRules = (evaluation.matchedRules?.length ?? 0) > 0
  const urgencyConfig = getUrgencyConfig(evaluation.dte)
  const approvalCount = evaluation.approvalCount ?? 1
  const hrArchetype = evaluation.decision?.hr_archetype_matched
  const pArchetype = evaluation.decision?.p_archetype_matched
  const hasArchetype = Boolean(hrArchetype || pArchetype)

  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '4px' }}>
      {/* Row 0: v5 archetype pills (when matched) */}
      {hasArchetype && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
          <ArchetypePill archetype={hrArchetype} kind="hr" />
          <ArchetypePill archetype={pArchetype} kind="p" />
        </div>
      )}
      {/* Row 1: Setup Rules (conditional) */}
      {hasSetupRules && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
          {evaluation.matchedRules!.map((rule) => (
            <span
              key={rule.rule_id}
              title={rule.name}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                padding: '2px 8px',
                fontSize: '10px',
                fontWeight: 600,
                color: '#A78BFA',
                background: '#7C3AED18',
                border: '1px solid #7C3AED40',
                borderRadius: '4px',
                whiteSpace: 'nowrap',
              }}
            >
              {rule.name}
            </span>
          ))}
        </div>
      )}

      {/* Row 2: Signal Badges + Urgency + Scan Count + Timestamp */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', alignItems: 'center' }}>
        {/* Scanner badges */}
        {evaluation.scannerSource.map((scanner) => (
          <span
            key={scanner}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              padding: '2px 8px',
              fontSize: '10px',
              fontWeight: 500,
              color: '#8892A5',
              background: '#1A1F2E',
              border: '1px solid #2A3040',
              borderRadius: '4px',
              whiteSpace: 'nowrap',
            }}
          >
            {formatScannerLabel(scanner)}
          </span>
        ))}

        {/* Convergence badge */}
        {evaluation.scannerConvergence >= 2 && (
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            padding: '2px 8px',
            fontSize: '10px',
            fontWeight: 500,
            color: '#8892A5',
            background: '#1A1F2E',
            border: '1px solid #2A3040',
            borderRadius: '4px',
            whiteSpace: 'nowrap',
          }}>
            {evaluation.scannerConvergence}x Convergence
          </span>
        )}

        {/* Urgency dot per spec §3.4 */}
        {urgencyConfig && (
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            padding: '2px 8px',
            fontSize: '10px',
            fontWeight: 700,
            color: urgencyConfig.color,
            background: `${urgencyConfig.color}18`,
            border: `1px solid ${urgencyConfig.color}40`,
            borderRadius: '4px',
          }}>
            <span style={{
              width: '5px',
              height: '5px',
              borderRadius: '50%',
              backgroundColor: urgencyConfig.color,
            }} />
            {urgencyConfig.label}
          </span>
        )}

        {/* Scan count badge */}
        {approvalCount > 1 && (
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            padding: '2px 8px',
            fontSize: '10px',
            fontWeight: 500,
            color: '#8892A5',
            background: '#1A1F2E',
            border: '1px solid #2A3040',
            borderRadius: '4px',
            whiteSpace: 'nowrap',
          }}>
            {approvalCount}x scans
          </span>
        )}

        {/* Timestamp — MUST always be present per spec §2.5 */}
        <span style={{
          fontSize: '10px',
          color: '#3A4058',
          marginLeft: '2px',
        }}>
          {formatRelativeTime(evaluation.evaluated_at)}
        </span>
      </div>
    </div>
  )
}

function MetricsZone({ evaluation, liveQuote }: { evaluation: ApproveEvaluation; liveQuote?: ContractQuote }) {
  // Entry-time premium from the original evaluation (the decision snapshot).
  const entryPremium = evaluation.mid ?? 0
  // Prefer the server-side refreshed quote on the evaluation row; fall back to
  // the per-page live-quote hook if present; otherwise show entry alone.
  const refreshedMid = evaluation.current_mid ?? null
  const refreshedAt = evaluation.quote_refreshed_at ?? null
  const liveMid = liveQuote?.mid ?? null
  const currentPremium =
    refreshedMid != null ? refreshedMid : liveMid != null ? liveMid : entryPremium
  const hasCurrent =
    refreshedMid != null || liveMid != null
  const contractCost = currentPremium * 100
  const pctChange =
    hasCurrent && entryPremium > 0
      ? ((currentPremium - entryPremium) / entryPremium) * 100
      : 0
  // Red = more expensive (harder to enter now), green = cheaper (better entry)
  const changeColor = pctChange >= 0 ? '#FF4D6A' : '#00E676'
  const changeSign = pctChange >= 0 ? '+' : ''

  const absDelta = Math.abs(evaluation.delta ?? 0)
  const moneynessPct = evaluation.moneyness_pct ?? 0

  const labelStyle: React.CSSProperties = {
    fontSize: '9px',
    color: '#4A5168',
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
  }

  const valueStyle: React.CSSProperties = {
    fontSize: '12px',
    fontWeight: 600,
    fontFamily: "'JetBrains Mono', monospace",
    color: '#E8ECF4',
  }

  return (
    <div style={{ display: 'flex', gap: '24px', alignItems: 'center', flexShrink: 0 }}>
      {/* Premium — shows Entry (trigger price) and Now (live) side-by-side
          when a refreshed quote is available, with percent change. */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
        <span style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: '4px' }}>
          {hasCurrent ? 'NOW' : 'PREMIUM'}
          {hasCurrent && refreshedAt && (
            <span style={{
              fontSize: '8px',
              color: '#4A5168',
              textTransform: 'none',
              letterSpacing: 0,
              fontWeight: 500,
            }}>
              {formatRelativeTime(refreshedAt)}
            </span>
          )}
        </span>
        <span style={valueStyle}>${currentPremium.toFixed(2)}</span>
        {hasCurrent ? (
          <>
            <span style={{
              fontSize: '10px',
              fontFamily: "'JetBrains Mono', monospace",
              color: '#8892A5',
            }}>
              entry ${entryPremium.toFixed(2)}
            </span>
            {Math.abs(pctChange) >= 0.1 && (
              <span style={{
                fontSize: '10px',
                fontFamily: "'JetBrains Mono', monospace",
                fontWeight: 600,
                color: changeColor,
              }}>
                {changeSign}{pctChange.toFixed(1)}%
              </span>
            )}
          </>
        ) : (
          <span style={{
            fontSize: '10px',
            fontFamily: "'JetBrains Mono', monospace",
            color: '#FF4D6A80',
          }}>
            (${contractCost.toLocaleString('en-US', { maximumFractionDigits: 0 })})
          </span>
        )}
      </div>

      {/* Delta */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
        <span style={labelStyle}>DELTA</span>
        <span style={valueStyle}>{absDelta.toFixed(2)}</span>
      </div>

      {/* OTM % */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
        <span style={labelStyle}>OTM %</span>
        <span style={{
          ...valueStyle,
          color: moneynessPct > 0 ? '#8892A5' : '#FFB800',
        }}>
          {moneynessPct > 0 ? `${moneynessPct.toFixed(1)}%` : moneynessPct === 0 ? 'ATM' : 'ITM'}
        </span>
      </div>

    </div>
  )
}

/** Format scanner type to display label per spec §3.2 */
function formatScannerLabel(scanner: string): string {
  switch (scanner) {
    case 'BREAKOUT': return 'Breakout'
    case 'BREAKDOWN': return 'Breakdown'
    case 'UNUSUAL_VOLUME': return 'Unusual Vol'
    case 'COMPRESSION_EXPANSION': return 'Compression'
    case 'CHEAP_OPTIONS': return 'Cheap Options'
    default: return scanner
  }
}

export default CompactRowCard
