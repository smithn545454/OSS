/**
 * Context Bar Component
 * 
 * Displays real-time market context including SPY, VIX, and pipeline freshness.
 * Per Section 7 of OSS_Opportunities_Page_Specification.
 */

import { useMarketContext } from '@/hooks/useApi'
import type { MarketContext, MarketStatus } from '@/lib/types'

interface ContextBarProps {
  className?: string
}

const MARKET_STATUS_LABELS: Record<MarketStatus, { label: string; icon: string }> = {
  pre: { label: 'Pre-Market', icon: '🌅' },
  open: { label: 'Market Open', icon: '🟢' },
  after: { label: 'After Hours', icon: '🌙' },
  closed: { label: 'Market Closed', icon: '⏸' },
}

function formatTime(timestamp: string | null): string {
  if (!timestamp) return 'Never'
  
  const date = new Date(timestamp)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  
  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  
  return date.toLocaleDateString()
}

function getPipelineFreshnessStatus(timestamp: string | null): 'fresh' | 'stale' | 'unknown' {
  if (!timestamp) return 'unknown'
  
  const date = new Date(timestamp)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  
  // Fresh if within 30 minutes
  if (diffMins < 30) return 'fresh'
  return 'stale'
}

function MarketStatusBadge({ status }: { status: MarketStatus }) {
  const config = MARKET_STATUS_LABELS[status]
  const isOpen = status === 'open'
  
  return (
    <span 
      className={`market-status-badge ${!isOpen ? 'market-status-badge--closed' : ''}`}
      role="status"
      aria-label={`Market status: ${config.label}`}
    >
      {isOpen && <span className="market-status-dot" aria-hidden="true" />}
      <span>{config.label}</span>
    </span>
  )
}

function SPYIndicator({ data }: { data: MarketContext['spy'] }) {
  const isPositive = data.changePercent >= 0
  const arrow = isPositive ? '↑' : '↓'
  const colorStyle = {
    color: isPositive ? 'var(--color-success-text)' : 'var(--color-error-text)',
  }
  
  return (
    <div 
      style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
      aria-label={`SPY ${data.price.toFixed(2)}, ${isPositive ? 'up' : 'down'} ${Math.abs(data.changePercent).toFixed(2)}%`}
    >
      <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>SPY</span>
      <span style={{ fontWeight: 600, fontSize: '14px' }}>${data.price.toFixed(2)}</span>
      <span style={{ ...colorStyle, fontWeight: 500, fontSize: '13px' }}>
        {arrow} {Math.abs(data.changePercent).toFixed(2)}%
      </span>
    </div>
  )
}

function VIXIndicator({ data }: { data: MarketContext['vix'] }) {
  // VIX warning thresholds
  const getVIXStyle = (price: number): React.CSSProperties => {
    if (price >= 35) return { color: 'var(--color-error-text)' }
    if (price >= 25) return { color: 'var(--color-warning-text)' }
    return { color: 'var(--text-secondary)' }
  }
  
  const isUp = data.direction === 'up'
  const arrow = isUp ? '↑' : '↓'
  
  return (
    <div 
      style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
      aria-label={`VIX ${data.price.toFixed(2)}, ${isUp ? 'up' : 'down'}`}
    >
      <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>VIX</span>
      <span style={{ fontWeight: 600, fontSize: '14px', ...getVIXStyle(data.price) }}>
        {data.price.toFixed(2)}
      </span>
      <span style={{ 
        color: isUp ? 'var(--color-error-text)' : 'var(--color-success-text)',
        fontWeight: 500, 
        fontSize: '13px' 
      }}>
        {arrow} {Math.abs(data.change).toFixed(2)}
      </span>
    </div>
  )
}

function PipelineFreshness({ timestamp }: { timestamp: string | null }) {
  const status = getPipelineFreshnessStatus(timestamp)
  const timeStr = formatTime(timestamp)
  
  const statusStyle: React.CSSProperties = {
    color: status === 'fresh' 
      ? 'var(--color-success-text)' 
      : status === 'stale' 
        ? 'var(--color-warning-text)' 
        : 'var(--text-muted)',
  }
  
  return (
    <div 
      style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
      aria-label={`Pipeline last run ${timeStr}`}
    >
      <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>Pipeline</span>
      <span style={{ ...statusStyle, fontWeight: 500, fontSize: '13px' }}>
        {timeStr}
      </span>
      {status === 'stale' && (
        <span 
          style={{ 
            padding: '2px 6px', 
            background: 'var(--color-warning-bg)', 
            borderRadius: '4px',
            fontSize: '10px',
            fontWeight: 600,
            color: 'var(--color-warning-text)',
          }}
        >
          STALE
        </span>
      )}
    </div>
  )
}

export function ContextBar({ className = '' }: ContextBarProps) {
  const { data, isLoading, error } = useMarketContext()
  
  if (isLoading) {
    return (
      <div className={`context-bar ${className}`}>
        <span style={{ color: 'var(--text-muted)' }}>Loading market data...</span>
      </div>
    )
  }
  
  if (error || !data) {
    return (
      <div className={`context-bar ${className}`}>
        <span style={{ color: 'var(--text-muted)' }}>Unable to load market data</span>
      </div>
    )
  }
  
  return (
    <nav 
      className={`context-bar ${className}`}
      role="navigation"
      aria-label="Market context"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
        <MarketStatusBadge status={data.marketStatus} />
        <SPYIndicator data={data.spy} />
        <VIXIndicator data={data.vix} />
      </div>
      
      <PipelineFreshness timestamp={data.lastPipelineRun} />
    </nav>
  )
}

export default ContextBar
