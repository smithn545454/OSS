/**
 * All APPROVEs Table Component
 * 
 * Displays all APPROVE evaluations in a sortable, filterable table.
 * Per Section 10 of OSS_Opportunities_Page_Specification.
 */

import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import type { ApproveEvaluation, UrgencyLevel, ScannerType } from '@/lib/types'
import { formatContractId } from '@/lib/convictionScore'
import { UrgencyBadge } from './UrgencyBadge'
import { ScannerBadge } from './ScannerBadge'
import { ConvictionGauge } from './ConvictionGauge'
import { OptionTypeBadge } from './OptionTypeBadge'

type SortField = 'contract' | 'conviction' | 'scanner' | 'urgency' | 'delta' | 'premium' | 'ev' | 'time'
type SortDirection = 'asc' | 'desc'

interface AllApprovesTableProps {
  evaluations: ApproveEvaluation[]
  className?: string
}

interface FilterState {
  scanner: ScannerType | 'all'
  urgency: UrgencyLevel | 'all'
  optionType: 'CALL' | 'PUT' | 'all'
}

const ITEMS_PER_PAGE = 20

function SortableHeader({ 
  label, 
  field, 
  currentSort, 
  currentDirection,
  onSort,
  align = 'left',
}: {
  label: string
  field: SortField
  currentSort: SortField
  currentDirection: SortDirection
  onSort: (field: SortField) => void
  align?: 'left' | 'right'
}) {
  const isActive = currentSort === field
  const arrow = isActive ? (currentDirection === 'asc' ? '↑' : '↓') : ''
  
  return (
    <th
      onClick={() => onSort(field)}
      style={{
        padding: '12px 16px',
        textAlign: align,
        fontWeight: 600,
        fontSize: '12px',
        color: isActive ? 'var(--accent-primary)' : 'var(--text-muted)',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        cursor: 'pointer',
        whiteSpace: 'nowrap',
        userSelect: 'none',
        borderBottom: '1px solid var(--border-default)',
        background: 'var(--bg-secondary)',
      }}
      role="columnheader"
      aria-sort={isActive ? (currentDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      {label} {arrow}
    </th>
  )
}

function FilterSelect<T extends string>({ 
  label, 
  value, 
  options, 
  onChange 
}: {
  label: string
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      <label style={{ 
        fontSize: '11px', 
        color: 'var(--text-muted)',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
      }}>
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
        {options.map(opt => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
    </div>
  )
}

export function AllApprovesTable({ evaluations, className = '' }: AllApprovesTableProps) {
  const navigate = useNavigate()
  const [sortField, setSortField] = useState<SortField>('conviction')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const [filters, setFilters] = useState<FilterState>({
    scanner: 'all',
    urgency: 'all',
    optionType: 'all',
  })
  const [page, setPage] = useState(1)
  
  const handleSort = (field: SortField) => {
    if (field === sortField) {
      setSortDirection(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDirection('desc')
    }
  }
  
  // Filter and sort
  const filteredAndSorted = useMemo(() => {
    let result = [...evaluations]
    
    // Apply filters
    if (filters.scanner !== 'all') {
      result = result.filter(e => e.scannerSource.includes(filters.scanner as ScannerType))
    }
    if (filters.urgency !== 'all') {
      result = result.filter(e => e.urgency === filters.urgency)
    }
    if (filters.optionType !== 'all') {
      result = result.filter(e => e.option_type === filters.optionType)
    }
    
    // Sort
    result.sort((a, b) => {
      let aVal: number | string = 0
      let bVal: number | string = 0
      
      switch (sortField) {
        case 'contract':
          aVal = a.underlying_ticker
          bVal = b.underlying_ticker
          break
        case 'conviction':
          aVal = a.convictionScore ?? 0
          bVal = b.convictionScore ?? 0
          break
        case 'delta':
          aVal = Math.abs(a.delta ?? 0)
          bVal = Math.abs(b.delta ?? 0)
          break
        case 'premium':
          aVal = a.mid ?? 0
          bVal = b.mid ?? 0
          break
        case 'ev':
          aVal = a.thetaAdjustedEV ?? 0
          bVal = b.thetaAdjustedEV ?? 0
          break
        case 'time':
          aVal = new Date(a.evaluated_at).getTime()
          bVal = new Date(b.evaluated_at).getTime()
          break
        default:
          return 0
      }
      
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortDirection === 'asc' 
          ? aVal.localeCompare(bVal) 
          : bVal.localeCompare(aVal)
      }
      
      return sortDirection === 'asc' ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number)
    })
    
    return result
  }, [evaluations, filters, sortField, sortDirection])
  
  // Pagination
  const totalPages = Math.ceil(filteredAndSorted.length / ITEMS_PER_PAGE)
  const paginatedData = filteredAndSorted.slice((page - 1) * ITEMS_PER_PAGE, page * ITEMS_PER_PAGE)
  
  const handleRowClick = (evaluation: ApproveEvaluation) => {
    navigate(`/evaluation/${evaluation.underlying_ticker}/${evaluation.evaluated_at}/${evaluation.evaluation_id}`)
  }
  
  return (
    <section className={className} aria-labelledby="all-approves-heading">
      {/* Section Header */}
      <div style={{ 
        display: 'flex', 
        justifyContent: 'space-between', 
        alignItems: 'flex-end',
        marginBottom: '16px',
        flexWrap: 'wrap',
        gap: '16px',
      }}>
        <div>
          <h2 
            id="all-approves-heading"
            style={{ 
              margin: '0 0 4px', 
              fontSize: '18px', 
              fontWeight: 700,
              color: 'var(--text-primary)',
            }}
          >
            All APPROVEs
          </h2>
          <p style={{ 
            margin: 0, 
            fontSize: '13px', 
            color: 'var(--text-muted)',
          }}>
            Showing {paginatedData.length} of {filteredAndSorted.length} opportunities
          </p>
        </div>
        
        {/* Filters */}
        <div style={{ display: 'flex', gap: '16px' }}>
          <FilterSelect
            label="Scanner"
            value={filters.scanner}
            options={[
              { value: 'all', label: 'All Scanners' },
              { value: 'BREAKOUT', label: 'Breakout' },
              { value: 'BREAKDOWN', label: 'Breakdown' },
              { value: 'UNUSUAL_VOLUME', label: 'Unusual Volume' },
              { value: 'COMPRESSION_EXPANSION', label: 'Compression' },
              { value: 'CHEAP_OPTIONS', label: 'Cheap Options' },
            ]}
            onChange={(v) => setFilters(f => ({ ...f, scanner: v as FilterState['scanner'] }))}
          />
          <FilterSelect
            label="Urgency"
            value={filters.urgency}
            options={[
              { value: 'all', label: 'All Urgency' },
              { value: 'act_now', label: 'Act Now' },
              { value: 'hours', label: 'Hours' },
              { value: 'patient', label: 'Patient' },
            ]}
            onChange={(v) => setFilters(f => ({ ...f, urgency: v as FilterState['urgency'] }))}
          />
          <FilterSelect
            label="Type"
            value={filters.optionType}
            options={[
              { value: 'all', label: 'Calls & Puts' },
              { value: 'CALL', label: 'Calls Only' },
              { value: 'PUT', label: 'Puts Only' },
            ]}
            onChange={(v) => setFilters(f => ({ ...f, optionType: v as FilterState['optionType'] }))}
          />
        </div>
      </div>
      
      {/* Table */}
      <div style={{ 
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-lg)',
        overflow: 'hidden',
      }}>
        <table style={{ 
          width: '100%', 
          borderCollapse: 'collapse',
        }}>
          <thead>
            <tr>
              <SortableHeader 
                label="Contract" 
                field="contract"
                currentSort={sortField}
                currentDirection={sortDirection}
                onSort={handleSort}
              />
              <SortableHeader 
                label="Dir" 
                field="contract"
                currentSort={sortField}
                currentDirection={sortDirection}
                onSort={handleSort}
              />
              <SortableHeader 
                label="Conviction" 
                field="conviction"
                currentSort={sortField}
                currentDirection={sortDirection}
                onSort={handleSort}
                align="right"
              />
              <th style={{
                padding: '12px 16px',
                textAlign: 'left',
                fontWeight: 600,
                fontSize: '12px',
                color: 'var(--text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                borderBottom: '1px solid var(--border-default)',
                background: 'var(--bg-secondary)',
              }}>
                Scanner
              </th>
              <th style={{
                padding: '12px 16px',
                textAlign: 'left',
                fontWeight: 600,
                fontSize: '12px',
                color: 'var(--text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                borderBottom: '1px solid var(--border-default)',
                background: 'var(--bg-secondary)',
              }}>
                Urgency
              </th>
              <SortableHeader 
                label="Delta" 
                field="delta"
                currentSort={sortField}
                currentDirection={sortDirection}
                onSort={handleSort}
                align="right"
              />
              <SortableHeader 
                label="Premium" 
                field="premium"
                currentSort={sortField}
                currentDirection={sortDirection}
                onSort={handleSort}
                align="right"
              />
              <SortableHeader 
                label="θ-Adj EV" 
                field="ev"
                currentSort={sortField}
                currentDirection={sortDirection}
                onSort={handleSort}
                align="right"
              />
              <SortableHeader 
                label="Time" 
                field="time"
                currentSort={sortField}
                currentDirection={sortDirection}
                onSort={handleSort}
                align="right"
              />
            </tr>
          </thead>
          <tbody>
            {paginatedData.map((evaluation) => (
              <tr
                key={evaluation.evaluation_id}
                onClick={() => handleRowClick(evaluation)}
                style={{ 
                  cursor: 'pointer',
                  borderBottom: '1px solid var(--border-subtle)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'var(--bg-hover)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                }}
              >
                <td style={{ padding: '12px 16px' }}>
                  <span style={{ 
                    fontWeight: 600, 
                    fontFamily: 'var(--font-primary)',
                    color: 'var(--text-primary)',
                  }}>
                    {formatContractId(
                      evaluation.underlying_ticker,
                      evaluation.strike,
                      evaluation.option_type,
                      evaluation.expiration_date
                    )}
                  </span>
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <OptionTypeBadge type={evaluation.option_type} />
                </td>
                <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <ConvictionGauge score={evaluation.convictionScore ?? 0} size="mini" />
                  </div>
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                    {evaluation.scannerSource.slice(0, 2).map((scanner) => (
                      <ScannerBadge key={scanner} scanner={scanner} />
                    ))}
                    {evaluation.scannerSource.length > 2 && (
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                        +{evaluation.scannerSource.length - 2}
                      </span>
                    )}
                  </div>
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <UrgencyBadge urgency={evaluation.urgency} size="small" />
                </td>
                <td style={{ 
                  padding: '12px 16px', 
                  textAlign: 'right',
                  fontFamily: 'var(--font-primary)',
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                }}>
                  {(evaluation.delta ?? 0).toFixed(2)}
                </td>
                <td style={{ 
                  padding: '12px 16px', 
                  textAlign: 'right',
                  fontFamily: 'var(--font-primary)',
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                }}>
                  ${(evaluation.mid ?? 0).toFixed(2)}
                </td>
                <td style={{ 
                  padding: '12px 16px', 
                  textAlign: 'right',
                  fontFamily: 'var(--font-primary)',
                  fontSize: '13px',
                  fontWeight: 600,
                  color: (evaluation.thetaAdjustedEV ?? 0) >= 0 
                    ? 'var(--color-success-text)' 
                    : 'var(--color-error-text)',
                }}>
                  ${(evaluation.thetaAdjustedEV ?? 0).toFixed(0)}
                </td>
                <td style={{ 
                  padding: '12px 16px', 
                  textAlign: 'right',
                  fontSize: '12px',
                  color: 'var(--text-muted)',
                }}>
                  {new Date(evaluation.evaluated_at).toLocaleTimeString('en-US', { 
                    hour: 'numeric', 
                    minute: '2-digit' 
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        
        {/* Pagination */}
        {totalPages > 1 && (
          <div style={{ 
            display: 'flex', 
            justifyContent: 'center', 
            alignItems: 'center',
            gap: '8px',
            padding: '16px',
            borderTop: '1px solid var(--border-default)',
          }}>
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
              style={{
                padding: '6px 12px',
                background: page === 1 ? 'var(--bg-tertiary)' : 'var(--bg-hover)',
                border: '1px solid var(--border-default)',
                borderRadius: 'var(--radius-sm)',
                color: page === 1 ? 'var(--text-disabled)' : 'var(--text-secondary)',
                cursor: page === 1 ? 'not-allowed' : 'pointer',
                fontSize: '13px',
              }}
            >
              Previous
            </button>
            <span style={{ 
              fontSize: '13px', 
              color: 'var(--text-muted)',
              padding: '0 8px',
            }}>
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              style={{
                padding: '6px 12px',
                background: page === totalPages ? 'var(--bg-tertiary)' : 'var(--bg-hover)',
                border: '1px solid var(--border-default)',
                borderRadius: 'var(--radius-sm)',
                color: page === totalPages ? 'var(--text-disabled)' : 'var(--text-secondary)',
                cursor: page === totalPages ? 'not-allowed' : 'pointer',
                fontSize: '13px',
              }}
            >
              Next
            </button>
          </div>
        )}
      </div>
    </section>
  )
}

export default AllApprovesTable
