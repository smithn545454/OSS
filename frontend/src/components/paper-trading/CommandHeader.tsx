/**
 * CommandHeader — Live stats bar for the Paper Trading Workstation.
 */

import { RefreshCw } from 'lucide-react'
import type { PaperTradingSummary } from '@/lib/types'

interface CommandHeaderProps {
  summary: PaperTradingSummary | undefined
  onUpdatePrices: () => void
  isUpdating: boolean
  isLoading: boolean
}

function StatCard({
  label,
  value,
  color,
  suffix = '',
}: {
  label: string
  value: string | number
  color?: string
  suffix?: string
}) {
  return (
    <div
      style={{
        padding: '16px',
        background: 'var(--bg-tertiary)',
        borderRadius: 'var(--radius-md)',
        minWidth: 0,
      }}
    >
      <div
        style={{
          fontSize: '11px',
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          marginBottom: '4px',
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: '24px',
          fontWeight: 700,
          color: color || 'var(--text-primary)',
        }}
      >
        {value}
        {suffix && (
          <span style={{ fontSize: '14px', fontWeight: 500, marginLeft: '2px' }}>
            {suffix}
          </span>
        )}
      </div>
    </div>
  )
}

export function CommandHeader({
  summary,
  onUpdatePrices,
  isUpdating,
  isLoading,
}: CommandHeaderProps) {
  const positions = summary?.positions
  const openSummary = summary?.open_positions_summary
  const performance = summary?.performance

  const pnlColor =
    (openSummary?.total_pnl_pct ?? 0) >= 0
      ? 'var(--color-success-text)'
      : 'var(--color-error-text)'

  const winRateColor =
    (performance?.win_rate ?? 0) >= 55
      ? 'var(--color-success-text)'
      : (performance?.win_rate ?? 0) >= 45
        ? 'var(--text-primary)'
        : 'var(--color-error-text)'

  return (
    <div
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-lg)',
        padding: '24px',
      }}
    >
      {/* Header row */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '20px',
        }}
      >
        <div>
          <h2
            style={{
              fontSize: '18px',
              fontWeight: 700,
              color: 'var(--text-primary)',
              margin: 0,
            }}
          >
            Paper Trading Workstation
          </h2>
          <p
            style={{
              fontSize: '13px',
              color: 'var(--text-muted)',
              margin: '4px 0 0',
            }}
          >
            AI-powered performance tracking &amp; system optimization
          </p>
        </div>

        <button
          onClick={onUpdatePrices}
          disabled={isUpdating}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 16px',
            background: 'var(--accent-primary)',
            color: '#fff',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            fontSize: '13px',
            fontWeight: 600,
            cursor: isUpdating ? 'not-allowed' : 'pointer',
            opacity: isUpdating ? 0.7 : 1,
          }}
        >
          <RefreshCw
            className={isUpdating ? 'animate-spin' : ''}
            style={{ width: 14, height: 14 }}
          />
          {isUpdating ? 'Updating...' : 'Update Prices'}
        </button>
      </div>

      {/* Stats grid */}
      {isLoading ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: '16px',
          }}
        >
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              style={{
                height: '80px',
                background: 'var(--bg-tertiary)',
                borderRadius: 'var(--radius-md)',
                animation: 'pulse 2s ease-in-out infinite',
              }}
            />
          ))}
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: '16px',
          }}
        >
          <StatCard
            label="Open Positions"
            value={positions?.open ?? 0}
          />
          <StatCard
            label="Total Open P&L"
            value={`${(openSummary?.total_pnl_pct ?? 0) >= 0 ? '+' : ''}${(openSummary?.total_pnl_pct ?? 0).toFixed(1)}`}
            suffix="%"
            color={pnlColor}
          />
          <StatCard
            label="Win Rate"
            value={(performance?.win_rate ?? 0).toFixed(1)}
            suffix="%"
            color={winRateColor}
          />
          <StatCard
            label="Expectancy"
            value={`${(performance?.expectancy ?? 0) >= 0 ? '+' : ''}${(performance?.expectancy ?? 0).toFixed(2)}`}
            suffix="%"
            color={
              (performance?.expectancy ?? 0) >= 0
                ? 'var(--color-success-text)'
                : 'var(--color-error-text)'
            }
          />
        </div>
      )}
    </div>
  )
}
