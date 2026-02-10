/**
 * OpenPositionGrid — Table of open positions with MFE/MAE bars.
 */

import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { X } from 'lucide-react'
import type { PaperPosition } from '@/lib/types'

type SortField = 'pnl' | 'days' | 'mfe'
type SortDirection = 'asc' | 'desc'

interface OpenPositionGridProps {
  positions: PaperPosition[]
  onClose: (positionId: string) => void
  isClosing: boolean
  isLoading: boolean
}

function parseTicker(optionTicker: string): { underlying: string; display: string } {
  // Format: O:AAPL251219C00200000
  if (optionTicker.startsWith('O:')) {
    const raw = optionTicker.slice(2)
    // Extract underlying (letters before the date digits)
    const match = raw.match(/^([A-Z]+)/)
    return {
      underlying: match ? match[1] : raw,
      display: optionTicker,
    }
  }
  return { underlying: optionTicker, display: optionTicker }
}

function MfeBar({
  mfe,
  mae,
  currentPnl,
}: {
  mfe: number
  mae: number
  currentPnl: number
}) {
  // Normalize: bar center = 0, left = -mae, right = +mfe
  const maxRange = Math.max(Math.abs(mae), Math.abs(mfe), Math.abs(currentPnl), 5)
  const maePct = Math.min((Math.abs(mae) / maxRange) * 50, 50)
  const mfePct = Math.min((Math.abs(mfe) / maxRange) * 50, 50)
  const pnlPos = (currentPnl / maxRange) * 50

  return (
    <div
      style={{
        position: 'relative',
        height: '24px',
        width: '100%',
        minWidth: '120px',
        background: 'var(--bg-tertiary)',
        borderRadius: 'var(--radius-sm)',
        overflow: 'hidden',
      }}
    >
      {/* MAE bar (left of center, red) */}
      <div
        style={{
          position: 'absolute',
          right: '50%',
          top: '4px',
          height: '16px',
          width: `${maePct}%`,
          background: 'rgba(239, 68, 68, 0.3)',
          borderRadius: '2px 0 0 2px',
        }}
      />
      {/* MFE bar (right of center, green) */}
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: '4px',
          height: '16px',
          width: `${mfePct}%`,
          background: 'rgba(34, 197, 94, 0.3)',
          borderRadius: '0 2px 2px 0',
        }}
      />
      {/* Center line */}
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: '2px',
          height: '20px',
          width: '1px',
          background: 'var(--text-muted)',
          opacity: 0.4,
        }}
      />
      {/* Current P&L marker */}
      <div
        style={{
          position: 'absolute',
          left: `${50 + pnlPos}%`,
          top: '2px',
          height: '20px',
          width: '3px',
          background:
            currentPnl >= 0
              ? 'var(--color-success-text)'
              : 'var(--color-error-text)',
          borderRadius: '1px',
          transform: 'translateX(-1px)',
        }}
      />
    </div>
  )
}

export function OpenPositionGrid({
  positions,
  onClose,
  isClosing,
  isLoading,
}: OpenPositionGridProps) {
  const navigate = useNavigate()
  const [sortField, setSortField] = useState<SortField>('pnl')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const [confirmClose, setConfirmClose] = useState<string | null>(null)

  const handleSort = (field: SortField) => {
    if (field === sortField) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDirection('desc')
    }
  }

  const sorted = useMemo(() => {
    const result = [...positions]
    result.sort((a, b) => {
      let aVal = 0
      let bVal = 0
      switch (sortField) {
        case 'pnl':
          aVal = a.current_pnl_pct
          bVal = b.current_pnl_pct
          break
        case 'days':
          aVal = a.days_held
          bVal = b.days_held
          break
        case 'mfe':
          aVal = a.max_favorable_excursion
          bVal = b.max_favorable_excursion
          break
      }
      return sortDirection === 'asc' ? aVal - bVal : bVal - aVal
    })
    return result
  }, [positions, sortField, sortDirection])

  const headerStyle = (
    field: SortField,
    align: 'left' | 'right' = 'left'
  ): React.CSSProperties => ({
    padding: '12px 16px',
    textAlign: align,
    fontWeight: 600,
    fontSize: '12px',
    color:
      sortField === field
        ? 'var(--accent-primary)'
        : 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    userSelect: 'none',
    borderBottom: '1px solid var(--border-default)',
    background: 'var(--bg-secondary)',
  })

  const thStyle: React.CSSProperties = {
    padding: '12px 16px',
    fontWeight: 600,
    fontSize: '12px',
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    whiteSpace: 'nowrap',
    borderBottom: '1px solid var(--border-default)',
    background: 'var(--bg-secondary)',
  }

  return (
    <div
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-lg)',
        overflow: 'hidden',
      }}
    >
      <div style={{ padding: '20px 24px 12px' }}>
        <h3
          style={{
            fontSize: '16px',
            fontWeight: 700,
            color: 'var(--text-primary)',
            margin: 0,
          }}
        >
          Open Positions
        </h3>
      </div>

      {isLoading ? (
        <div style={{ padding: '40px 24px', textAlign: 'center' }}>
          <div
            style={{
              height: '120px',
              background: 'var(--bg-tertiary)',
              borderRadius: 'var(--radius-md)',
              animation: 'pulse 2s ease-in-out infinite',
            }}
          />
        </div>
      ) : positions.length === 0 ? (
        <div
          style={{
            padding: '40px 24px',
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: '14px',
          }}
        >
          No open positions. The pipeline will create positions on the next
          run.
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
            }}
            role="table"
          >
            <thead>
              <tr>
                <th style={thStyle}>Option Ticker</th>
                <th style={thStyle}>Verdict</th>
                <th style={thStyle}>Tier</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Entry</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Current</th>
                <th
                  style={headerStyle('pnl', 'right')}
                  onClick={() => handleSort('pnl')}
                >
                  P&L%{' '}
                  {sortField === 'pnl'
                    ? sortDirection === 'asc'
                      ? '↑'
                      : '↓'
                    : ''}
                </th>
                <th
                  style={headerStyle('days', 'right')}
                  onClick={() => handleSort('days')}
                >
                  Days{' '}
                  {sortField === 'days'
                    ? sortDirection === 'asc'
                      ? '↑'
                      : '↓'
                    : ''}
                </th>
                <th
                  style={headerStyle('mfe')}
                  onClick={() => handleSort('mfe')}
                >
                  MFE/MAE{' '}
                  {sortField === 'mfe'
                    ? sortDirection === 'asc'
                      ? '↑'
                      : '↓'
                    : ''}
                </th>
                <th style={thStyle} />
              </tr>
            </thead>
            <tbody>
              {sorted.map((pos) => {
                const { underlying } = parseTicker(pos.option_ticker)
                const pnlColor =
                  pos.current_pnl_pct >= 0
                    ? 'var(--color-success-text)'
                    : 'var(--color-error-text)'

                return (
                  <tr
                    key={pos.position_id}
                    style={{
                      borderBottom: '1px solid var(--border-subtle)',
                    }}
                  >
                    <td style={{ padding: '12px 16px' }}>
                      <button
                        onClick={() =>
                          navigate(
                            `/evaluation/${underlying}/${pos.evaluation_id}`
                          )
                        }
                        style={{
                          background: 'none',
                          border: 'none',
                          color: 'var(--accent-primary)',
                          cursor: 'pointer',
                          fontSize: '13px',
                          fontWeight: 500,
                          padding: 0,
                          textDecoration: 'underline',
                        }}
                      >
                        {pos.option_ticker}
                      </button>
                    </td>
                    <td style={{ padding: '12px 16px' }}>
                      <span
                        style={{
                          fontSize: '11px',
                          fontWeight: 600,
                          padding: '2px 8px',
                          borderRadius: '9999px',
                          background:
                            pos.verdict_at_entry === 'APPROVE'
                              ? 'rgba(34, 197, 94, 0.15)'
                              : 'rgba(234, 179, 8, 0.15)',
                          color:
                            pos.verdict_at_entry === 'APPROVE'
                              ? 'var(--color-success-text)'
                              : '#eab308',
                        }}
                      >
                        {pos.verdict_at_entry}
                      </span>
                    </td>
                    <td
                      style={{
                        padding: '12px 16px',
                        fontSize: '13px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {pos.quality_tier_at_entry || '—'}
                    </td>
                    <td
                      style={{
                        padding: '12px 16px',
                        textAlign: 'right',
                        fontSize: '13px',
                        fontFamily: 'monospace',
                      }}
                    >
                      ${pos.entry_price.toFixed(2)}
                    </td>
                    <td
                      style={{
                        padding: '12px 16px',
                        textAlign: 'right',
                        fontSize: '13px',
                        fontFamily: 'monospace',
                      }}
                    >
                      ${pos.current_price.toFixed(2)}
                    </td>
                    <td
                      style={{
                        padding: '12px 16px',
                        textAlign: 'right',
                        fontSize: '13px',
                        fontWeight: 600,
                        fontFamily: 'monospace',
                        color: pnlColor,
                      }}
                    >
                      {pos.current_pnl_pct >= 0 ? '+' : ''}
                      {pos.current_pnl_pct.toFixed(1)}%
                    </td>
                    <td
                      style={{
                        padding: '12px 16px',
                        textAlign: 'right',
                        fontSize: '13px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {pos.days_held}d
                    </td>
                    <td style={{ padding: '12px 16px', minWidth: '140px' }}>
                      <MfeBar
                        mfe={pos.max_favorable_excursion}
                        mae={pos.max_adverse_excursion}
                        currentPnl={pos.current_pnl_pct}
                      />
                    </td>
                    <td style={{ padding: '12px 16px' }}>
                      {confirmClose === pos.position_id ? (
                        <div style={{ display: 'flex', gap: '4px' }}>
                          <button
                            onClick={() => {
                              onClose(pos.position_id)
                              setConfirmClose(null)
                            }}
                            disabled={isClosing}
                            style={{
                              padding: '4px 8px',
                              fontSize: '11px',
                              background: 'var(--color-error-text)',
                              color: '#fff',
                              border: 'none',
                              borderRadius: 'var(--radius-sm)',
                              cursor: 'pointer',
                            }}
                          >
                            Confirm
                          </button>
                          <button
                            onClick={() => setConfirmClose(null)}
                            style={{
                              padding: '4px 8px',
                              fontSize: '11px',
                              background: 'var(--bg-tertiary)',
                              color: 'var(--text-secondary)',
                              border: '1px solid var(--border-default)',
                              borderRadius: 'var(--radius-sm)',
                              cursor: 'pointer',
                            }}
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setConfirmClose(pos.position_id)}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                            padding: '4px 8px',
                            fontSize: '11px',
                            background: 'transparent',
                            color: 'var(--text-muted)',
                            border: '1px solid var(--border-default)',
                            borderRadius: 'var(--radius-sm)',
                            cursor: 'pointer',
                          }}
                        >
                          <X style={{ width: 12, height: 12 }} />
                          Close
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
