/**
 * Opportunity Card Component
 * 
 * Displays a single opportunity in the Conviction Queue with rank, 
 * contract info, and key metrics.
 * Per Section 9 of OSS_Opportunities_Page_Specification.
 */

import { useNavigate } from 'react-router-dom'
import type { ApproveEvaluation } from '@/lib/types'
import { UrgencyBadge } from './UrgencyBadge'
import { ScannerBadge } from './ScannerBadge'
import { ConvergenceBadge } from './ConvergenceBadge'
import { OptionTypeBadge } from './OptionTypeBadge'
import { ConvictionGauge } from './ConvictionGauge'
import { formatRelativeTime, getAgeFreshness } from '@/lib/formatTime'

interface OpportunityCardProps {
  evaluation: ApproveEvaluation
  rank: number
  className?: string
}

function RankIndicator({ rank }: { rank: number }) {
  const getRankClass = () => {
    if (rank === 1) return 'rank-indicator--1'
    if (rank === 2) return 'rank-indicator--2'
    if (rank === 3) return 'rank-indicator--3'
    return 'rank-indicator--default'
  }
  
  return (
    <div 
      className={`rank-indicator ${getRankClass()}`}
      aria-label={`Rank ${rank}`}
    >
      #{rank}
    </div>
  )
}

type Freshness = 'fresh' | 'recent' | null

function ContractInfo({ evaluation, freshness }: { evaluation: ApproveEvaluation; freshness: Freshness }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {/* Ticker and option type */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <span style={{ 
          fontSize: '18px', 
          fontWeight: 700, 
          color: 'var(--text-primary)',
          fontFamily: 'var(--font-primary)',
        }}>
          {evaluation.underlying_ticker}
        </span>
        <OptionTypeBadge type={evaluation.option_type} />
        <span style={{ 
          fontSize: '14px', 
          color: 'var(--text-secondary)',
          fontFamily: 'var(--font-primary)',
        }}>
          ${evaluation.strike} • {new Date(evaluation.expiration_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
        </span>
      </div>
      
      {/* LLM Headline */}
      {evaluation.headline && (
        <p style={{ 
          margin: 0, 
          fontSize: '13px', 
          color: 'var(--text-tertiary)',
          lineHeight: 1.4,
          maxWidth: '400px',
        }}>
          {evaluation.headline}
        </p>
      )}
      
      {/* Scanner badges */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        {evaluation.scannerSource.map((scanner) => (
          <ScannerBadge key={scanner} scanner={scanner} />
        ))}
        <ConvergenceBadge count={evaluation.scannerConvergence} />
        <UrgencyBadge urgency={evaluation.urgency} size="small" />
        {evaluation.matchedRules && evaluation.matchedRules.length > 0 && (
          <span
            title={evaluation.matchedRules.map(r => r.name).join(', ')}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              padding: '2px 8px',
              fontSize: '10px',
              fontWeight: 600,
              color: '#a78bfa',
              background: 'rgba(167, 139, 250, 0.1)',
              borderRadius: '10px',
              border: '1px solid rgba(167, 139, 250, 0.2)',
              whiteSpace: 'nowrap',
            }}
          >
            {evaluation.matchedRules.length === 1
              ? evaluation.matchedRules[0].name.length > 30
                ? evaluation.matchedRules[0].name.slice(0, 28) + '...'
                : evaluation.matchedRules[0].name
              : `${evaluation.matchedRules.length} setup matches`}
          </span>
        )}
        {(evaluation.approvalCount ?? 1) > 1 && (
          <span
            title={`Approved in ${evaluation.approvalCount} separate pipeline scans`}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              padding: '2px 8px',
              fontSize: '11px',
              fontWeight: 600,
              color: 'var(--accent-primary)',
              background: 'var(--bg-tertiary)',
              borderRadius: '10px',
              border: '1px solid var(--border-default)',
            }}
          >
            {evaluation.approvalCount}x scans
          </span>
        )}
        <span style={{
          fontSize: freshness === 'fresh' ? '13px' : freshness === 'recent' ? '12px' : '11px',
          fontWeight: freshness ? 600 : 400,
          color: freshness === 'fresh'
            ? '#34d399'
            : freshness === 'recent'
              ? '#22d3ee'
              : 'var(--text-muted)',
        }}>
          {freshness === 'fresh' && '\u25cf '}
          {formatRelativeTime(evaluation.evaluated_at)}
        </span>
      </div>
    </div>
  )
}

function MetricsZone({ evaluation }: { evaluation: ApproveEvaluation }) {
  const premium = evaluation.mid ?? 0
  const contractCost = premium * 100

  // Contract cost color by tier: LOW ≤$300 cyan, MED ≤$1000 amber, HIGH >$1000 red
  const costColor =
    contractCost <= 300
      ? 'rgba(0,229,204,0.6)'
      : contractCost <= 1000
        ? 'rgba(245,158,11,0.6)'
        : 'rgba(239,68,68,0.6)'

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '24px',
      justifyContent: 'flex-end',
    }}>
      {/* Key metrics */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', textAlign: 'right' }}>
        {/* Premium with contract cost */}
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Premium</span>
          <span>
            <span style={{
              fontSize: '13px',
              fontWeight: 600,
              fontFamily: 'var(--font-primary)',
              color: '#fff',
            }}>
              ${premium.toFixed(2)}
            </span>
            {' '}
            <span style={{
              fontSize: '12px',
              fontWeight: 600,
              fontFamily: 'var(--font-primary)',
              color: costColor,
            }}>
              (${contractCost.toLocaleString('en-US', { maximumFractionDigits: 0 })})
            </span>
          </span>
        </div>
      </div>

      {/* Conviction gauge */}
      <ConvictionGauge score={evaluation.convictionScore ?? 0} />
    </div>
  )
}

export function OpportunityCard({ evaluation, rank, className = '' }: OpportunityCardProps) {
  const navigate = useNavigate()
  const isTopRank = rank === 1
  const freshness = getAgeFreshness(evaluation.evaluated_at)

  const handleClick = () => {
    navigate(`/evaluation/${evaluation.underlying_ticker}/${evaluation.evaluation_id}`)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      handleClick()
    }
  }

  const freshnessClass = freshness ? `opportunity-card--${freshness}` : ''

  return (
    <article
      className={`opportunity-card ${isTopRank ? 'opportunity-card--top' : ''} ${freshnessClass} ${className}`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
      aria-label={`${evaluation.underlying_ticker} ${evaluation.option_type} at ${evaluation.strike}, ranked ${rank}, conviction ${Math.round(evaluation.convictionScore ?? 0)}`}
    >
      {/* Zone 1: Rank */}
      <div style={{ 
        display: 'flex', 
        flexDirection: 'column', 
        alignItems: 'center',
        gap: '8px',
      }}>
        <RankIndicator rank={rank} />
        {isTopRank && (
          <span style={{ 
            fontSize: '10px', 
            color: 'var(--accent-primary)', 
            fontWeight: 500,
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
          }}>
            TOP PICK
          </span>
        )}
      </div>
      
      {/* Zone 2: Contract Info */}
      <ContractInfo evaluation={evaluation} freshness={freshness} />
      
      {/* Zone 3: Metrics */}
      <MetricsZone evaluation={evaluation} />
    </article>
  )
}

export default OpportunityCard
