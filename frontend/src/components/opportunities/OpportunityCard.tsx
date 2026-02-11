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

function ContractInfo({ evaluation }: { evaluation: ApproveEvaluation }) {
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
      </div>
    </div>
  )
}

function MetricsZone({ evaluation }: { evaluation: ApproveEvaluation }) {
  return (
    <div style={{ 
      display: 'flex', 
      alignItems: 'center', 
      gap: '24px',
      justifyContent: 'flex-end',
    }}>
      {/* Key metrics */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', textAlign: 'right' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Delta</span>
          <span style={{ 
            fontSize: '13px', 
            fontWeight: 600,
            fontFamily: 'var(--font-primary)',
            color: 'var(--text-secondary)',
          }}>
            {(evaluation.delta ?? 0).toFixed(2)}
          </span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Premium</span>
          <span style={{ 
            fontSize: '13px', 
            fontWeight: 600,
            fontFamily: 'var(--font-primary)',
            color: 'var(--text-secondary)',
          }}>
            ${(evaluation.mid ?? 0).toFixed(2)}
          </span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>θ-Adj EV</span>
          <span style={{ 
            fontSize: '13px', 
            fontWeight: 600,
            fontFamily: 'var(--font-primary)',
            color: evaluation.thetaAdjustedEV >= 0 
              ? 'var(--color-success-text)' 
              : 'var(--color-error-text)',
          }}>
            ${evaluation.thetaAdjustedEV.toFixed(0)}
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
  
  const handleClick = () => {
    navigate(`/evaluation/${evaluation.underlying_ticker}/${evaluation.evaluation_id}`)
  }
  
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      handleClick()
    }
  }
  
  return (
    <article
      className={`opportunity-card ${isTopRank ? 'opportunity-card--top' : ''} ${className}`}
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
      <ContractInfo evaluation={evaluation} />
      
      {/* Zone 3: Metrics */}
      <MetricsZone evaluation={evaluation} />
    </article>
  )
}

export default OpportunityCard
