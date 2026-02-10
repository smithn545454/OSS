/**
 * ClosedPositionsTable — Filterable, sortable table of closed positions.
 */

import { useState, useMemo } from 'react'
import type { PaperPosition, ExitReason, Verdict, QualityTier } from '@/lib/types'

type SortField = 'pnl' | 'exit_date' | 'days'
type SortDirection = 'asc' | 'desc'

interface ClosedPositionsTableProps {
  positions: PaperPosition[]
  isLoading: boolean
}

interface Filters {
  exitReason: ExitReason | 'all'
  verdict: Verdict | 'all'
  tier: QualityTier | 'all'
}

function FilterSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      <label
        style={{
          fontSize: '11px',
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
        }}
      >
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        style={{
          padding: '6px 10px',
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--text-secondary)',
          fontSize: '13px',
          cursor: 'pointer',
        }}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  )
}

export function ClosedPositionsTable({
  positions,
  isLoading,
}: ClosedPositionsTableProps) {
  const [sortField, setSortField] = useState<SortField>('exit_date')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const [filters, setFilters] = useState<Filters>({
    exitReason: 'all',
    verdict: 'all',
    tier: 'all',
  })

  const handleSort = (field: SortField) => {
    if (field === sortField) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDirection('desc')
    }
  }

  const filteredAndSorted = useMemo(() => {
    let result = [...positions]

    if (filters.exitReason !== 'all') {
      result = result.filter((p) => p.exit_reason === filters.exitReason)
    }
    if (filters.verdict !== 'all') {
      result = result.filter((p) => p.verdict_at_entry === filters.verdict)
    }
    if (filters.tier !== 'all') {
      result = result.filter(
        (p) => p.quality_tier_at_entry === filters.tier
      )
    }

    result.sort((a, b) => {
      let aVal: number | string = 0
      let bVal: number | string = 0
      switch (sortField) {
        case 'pnl':
          aVal = a.current_pnl_pct
          bVal = b.current_pnl_pct
          break
        case 'exit_date':
          aVal = a.exit_date || ''
          bVal = b.exit_date || ''
          break
        case 'days':
          aVal = a.days_held
          bVal = b.days_held
          break
      }
      if (typeof aVal === 'string') {
        return sortDirection === 'asc'
          ? aVal.localeCompare(bVal as string)
          : (bVal as string).localeCompare(aVal)
      }
      return sortDirection === 'asc'
        ? (aVal as number) - (bVal as number)
        : (bVal as number) - (aVal as number)
    })

    return result
  }, [positions, filters, sortField, sortDirection])

  const sortableHeaderStyle = (
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

  const cellStyle: React.CSSProperties = {
    padding: '10px 16px',
    fontSize: '13px',
    color: 'var(--text-secondary)',
  }

  const exitBadgeColor = (reason: string | null) => {
    switch (reason) {
      case 'PROFIT_TARGET':
        return {
          bg: 'rgba(34, 197, 94, 0.15)',
          text: 'var(--color-success-text)',
        }
      case 'STOP_LOSS':
        return {
          bg: 'rgba(239, 68, 68, 0.15)',
          text: 'var(--color-error-text)',
        }
      case 'TIME_EXIT':
        return { bg: 'rgba(234, 179, 8, 0.15)', text: '#eab308' }
      case 'EXPIRATION':
        return { bg: 'rgba(148, 163, 184, 0.15)', text: 'var(--text-muted)' }
      default:
        return { bg: 'rgba(148, 163, 184, 0.15)', text: 'var(--text-muted)' }
    }
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
      <div
        style={{
          padding: '20px 24px 16px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-end',
        }}
      >
        <h3
          style={{
            fontSize: '16px',
            fontWeight: 700,
            color: 'var(--text-primary)',
            margin: 0,
          }}
        >
          Closed Positions
          <span
            style={{
              marginLeft: '8px',
              fontSize: '13px',
              fontWeight: 400,
              color: 'var(--text-muted)',
            }}
          >
            ({filteredAndSorted.length})
          </span>
        </h3>

        {/* Filters */}
        <div style={{ display: 'flex', gap: '12px' }}>
          <FilterSelect
            label="Exit Reason"
            value={filters.exitReason}
            options={[
              { value: 'all', label: 'All' },
              { value: 'PROFIT_TARGET', label: 'Profit Target' },
              { value: 'STOP_LOSS', label: 'Stop Loss' },
              { value: 'TIME_EXIT', label: 'Time Exit' },
              { value: 'EXPIRATION', label: 'Expiration' },
              { value: 'MANUAL', label: 'Manual' },
            ]}
            onChange={(v) =>
              setFilters((f) => ({ ...f, exitReason: v }))
            }
          />
          <FilterSelect
            label="Verdict"
            value={filters.verdict}
            options={[
              { value: 'all', label: 'All' },
              { value: 'APPROVE', label: 'APPROVE' },
              { value: 'WATCH', label: 'WATCH' },
            ]}
            onChange={(v) =>
              setFilters((f) => ({ ...f, verdict: v }))
            }
          />
          <FilterSelect
            label="Tier"
            value={filters.tier}
            options={[
              { value: 'all', label: 'All' },
              { value: 'TIER_1', label: 'Tier 1' },
              { value: 'TIER_2', label: 'Tier 2' },
              { value: 'TIER_3', label: 'Tier 3' },
            ]}
            onChange={(v) =>
              setFilters((f) => ({ ...f, tier: v }))
            }
          />
        </div>
      </div>

      {isLoading ? (
        <div style={{ padding: '40px 24px' }}>
          <div
            style={{
              height: '120px',
              background: 'var(--bg-tertiary)',
              borderRadius: 'var(--radius-md)',
              animation: 'pulse 2s ease-in-out infinite',
            }}
          />
        </div>
      ) : filteredAndSorted.length === 0 ? (
        <div
          style={{
            padding: '40px 24px',
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: '14px',
          }}
        >
          {positions.length === 0
            ? 'No closed positions yet.'
            : 'No positions match the current filters.'}
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{ width: '100%', borderCollapse: 'collapse' }}
            role="table"
          >
            <thead>
              <tr>
                <th style={thStyle}>Ticker</th>
                <th style={thStyle}>Entry Date</th>
                <th
                  style={sortableHeaderStyle('exit_date')}
                  onClick={() => handleSort('exit_date')}
                >
                  Exit Date{' '}
                  {sortField === 'exit_date'
                    ? sortDirection === 'asc'
                      ? '↑'
                      : '↓'
                    : ''}
                </th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Entry $</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Exit $</th>
                <th
                  style={sortableHeaderStyle('pnl', 'right')}
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
                  style={sortableHeaderStyle('days', 'right')}
                  onClick={() => handleSort('days')}
                >
                  Days{' '}
                  {sortField === 'days'
                    ? sortDirection === 'asc'
                      ? '↑'
                      : '↓'
                    : ''}
                </th>
                <th style={{ ...thStyle, textAlign: 'right' }}>MFE</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>MAE</th>
                <th style={thStyle}>Exit</th>
                <th style={thStyle}>Verdict</th>
                <th style={thStyle}>Tier</th>
              </tr>
            </thead>
            <tbody>
              {filteredAndSorted.map((pos) => {
                const pnlColor =
                  pos.current_pnl_pct >= 0
                    ? 'var(--color-success-text)'
                    : 'var(--color-error-text)'
                const exitColors = exitBadgeColor(pos.exit_reason)

                return (
                  <tr
                    key={pos.position_id}
                    style={{
                      borderBottom: '1px solid var(--border-subtle)',
                    }}
                  >
                    <td
                      style={{
                        ...cellStyle,
                        fontWeight: 500,
                        color: 'var(--text-primary)',
                      }}
                    >
                      {pos.option_ticker}
                    </td>
                    <td style={cellStyle}>{pos.entry_date}</td>
                    <td style={cellStyle}>{pos.exit_date || '—'}</td>
                    <td
                      style={{
                        ...cellStyle,
                        textAlign: 'right',
                        fontFamily: 'monospace',
                      }}
                    >
                      ${pos.entry_price.toFixed(2)}
                    </td>
                    <td
                      style={{
                        ...cellStyle,
                        textAlign: 'right',
                        fontFamily: 'monospace',
                      }}
                    >
                      {pos.exit_price != null
                        ? `$${pos.exit_price.toFixed(2)}`
                        : '—'}
                    </td>
                    <td
                      style={{
                        ...cellStyle,
                        textAlign: 'right',
                        fontWeight: 600,
                        fontFamily: 'monospace',
                        color: pnlColor,
                      }}
                    >
                      {pos.current_pnl_pct >= 0 ? '+' : ''}
                      {pos.current_pnl_pct.toFixed(1)}%
                    </td>
                    <td
                      style={{ ...cellStyle, textAlign: 'right' }}
                    >
                      {pos.days_held}
                    </td>
                    <td
                      style={{
                        ...cellStyle,
                        textAlign: 'right',
                        fontFamily: 'monospace',
                        color: 'var(--color-success-text)',
                      }}
                    >
                      +{pos.max_favorable_excursion.toFixed(1)}%
                    </td>
                    <td
                      style={{
                        ...cellStyle,
                        textAlign: 'right',
                        fontFamily: 'monospace',
                        color: 'var(--color-error-text)',
                      }}
                    >
                      -{pos.max_adverse_excursion.toFixed(1)}%
                    </td>
                    <td style={cellStyle}>
                      <span
                        style={{
                          fontSize: '11px',
                          fontWeight: 600,
                          padding: '2px 8px',
                          borderRadius: '9999px',
                          background: exitColors.bg,
                          color: exitColors.text,
                        }}
                      >
                        {pos.exit_reason?.replace(/_/g, ' ') || '—'}
                      </span>
                    </td>
                    <td style={cellStyle}>
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
                        ...cellStyle,
                        fontSize: '12px',
                      }}
                    >
                      {pos.quality_tier_at_entry || '—'}
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
