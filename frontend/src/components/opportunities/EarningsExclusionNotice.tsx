/**
 * Earnings Exclusion Notice Component
 * 
 * Displays warning about contracts excluded due to upcoming earnings.
 * Per Section 12 of OSS_Opportunities_Page_Specification.
 */

import { useState } from 'react'
import type { EarningsExclusion } from '@/lib/types'

interface EarningsExclusionNoticeProps {
  exclusions: EarningsExclusion[]
  className?: string
}

export function EarningsExclusionNotice({ exclusions, className = '' }: EarningsExclusionNoticeProps) {
  const [isDismissed, setIsDismissed] = useState(false)
  
  // Don't show if no exclusions or dismissed
  if (!exclusions.length || isDismissed) {
    return null
  }
  
  // Show first 5 tickers, "and X more" for rest
  const visibleTickers = exclusions.slice(0, 5)
  const remainingCount = exclusions.length - 5
  
  // Count total excluded contracts
  const totalContracts = exclusions.reduce((sum, e) => sum + e.contractCount, 0)
  
  return (
    <div 
      className={`earnings-notice ${className}`}
      role="alert"
      aria-live="polite"
    >
      {/* Calendar icon */}
      <span className="earnings-notice-icon" aria-hidden="true">📅</span>
      
      {/* Notice text */}
      <div className="earnings-notice-text">
        <strong>{totalContracts} contract{totalContracts !== 1 ? 's' : ''}</strong> excluded 
        due to upcoming earnings:{' '}
        {visibleTickers.map((exc, i) => (
          <span key={exc.ticker}>
            <span 
              style={{ 
                fontWeight: 600, 
                fontFamily: 'var(--font-primary)',
              }}
              title={`Earnings: ${exc.earningsDate}`}
            >
              {exc.ticker}
            </span>
            {' '}({new Date(exc.earningsDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })})
            {i < visibleTickers.length - 1 && ', '}
          </span>
        ))}
        {remainingCount > 0 && (
          <span 
            style={{ 
              cursor: 'help',
            }}
            title={exclusions.slice(5).map(e => `${e.ticker} (${e.earningsDate})`).join(', ')}
          >
            {' '}and {remainingCount} more
          </span>
        )}
      </div>
      
      {/* Dismiss button */}
      <button
        onClick={() => setIsDismissed(true)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '24px',
          height: '24px',
          background: 'transparent',
          border: 'none',
          borderRadius: '4px',
          color: 'var(--text-muted)',
          cursor: 'pointer',
          flexShrink: 0,
        }}
        aria-label="Dismiss notice"
      >
        ✕
      </button>
    </div>
  )
}

export default EarningsExclusionNotice
