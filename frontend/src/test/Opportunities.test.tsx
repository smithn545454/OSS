import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Opportunities from '../pages/Opportunities'

// ---------------------------------------------------------------------------
// Mock child components (complex sub-trees)
// ---------------------------------------------------------------------------

vi.mock('@/components/opportunities', () => ({
  ContextBar: () => <div data-testid="context-bar">ContextBar</div>,
  ConvictionQueue: ({ evaluations, threshold, hasNewData, newCount, onRefresh }: any) => (
    <div data-testid="conviction-queue">
      <span data-testid="cq-count">{evaluations.length}</span>
      <span data-testid="cq-threshold">{threshold}</span>
      {hasNewData && (
        <span data-testid="cq-new">
          {newCount} new
          <button data-testid="cq-refresh" onClick={onRefresh}>Refresh</button>
        </span>
      )}
    </div>
  ),
  AllApprovesTable: ({ evaluations }: any) => (
    <div data-testid="all-approves-table">{evaluations.length} rows</div>
  ),
  WatchIntelligencePanel: () => <div data-testid="watch-panel">Watch</div>,
  EarningsExclusionNotice: ({ exclusions }: any) => (
    <div data-testid="earnings-notice">{exclusions.length} excluded</div>
  ),
}))

// ---------------------------------------------------------------------------
// Mock useApi hook
// ---------------------------------------------------------------------------

const mockRefetch = vi.fn()

vi.mock('@/hooks/useApi', () => ({
  useApproveEvaluations: vi.fn(() => ({
    data: {
      evaluations: [
        { evaluation_id: 'e1', underlying_ticker: 'AAPL' },
        { evaluation_id: 'e2', underlying_ticker: 'MSFT' },
      ],
      excludedForEarnings: [],
    },
    isLoading: false,
    error: null,
    refetch: mockRefetch,
    dataUpdatedAt: Date.now(),
  })),
}))

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Opportunities />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Opportunities Page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  // --- Normal rendering ---

  it('renders page title and subtitle', () => {
    renderPage()
    expect(screen.getByText('Opportunities')).toBeInTheDocument()
    expect(
      screen.getByText(/High-conviction options trades ranked by signal strength/),
    ).toBeInTheDocument()
  })

  it('renders ContextBar', () => {
    renderPage()
    expect(screen.getByTestId('context-bar')).toBeInTheDocument()
  })

  it('passes evaluations to ConvictionQueue', () => {
    renderPage()
    expect(screen.getByTestId('conviction-queue')).toBeInTheDocument()
    expect(screen.getByTestId('cq-count').textContent).toBe('2')
    expect(screen.getByTestId('cq-threshold').textContent).toBe('75')
  })

  it('passes evaluations to AllApprovesTable', () => {
    renderPage()
    expect(screen.getByTestId('all-approves-table').textContent).toBe('2 rows')
  })

  it('renders WatchIntelligencePanel', () => {
    renderPage()
    expect(screen.getByTestId('watch-panel')).toBeInTheDocument()
  })

  it('shows last updated footer', () => {
    renderPage()
    expect(screen.getByText(/Last updated:/)).toBeInTheDocument()
  })

  // --- Earnings Exclusion ---

  it('shows earnings notice when there are exclusions', async () => {
    const { useApproveEvaluations } = await import('@/hooks/useApi')
    vi.mocked(useApproveEvaluations).mockReturnValueOnce({
      data: {
        evaluations: [],
        excludedForEarnings: [{ ticker: 'TSLA', days_to_earnings: 3 }],
      },
      isLoading: false,
      error: null,
      refetch: mockRefetch,
      dataUpdatedAt: Date.now(),
    } as any)

    renderPage()
    expect(screen.getByTestId('earnings-notice')).toBeInTheDocument()
    expect(screen.getByTestId('earnings-notice').textContent).toContain('1 excluded')
  })

  it('hides earnings notice when no exclusions', () => {
    renderPage()
    expect(screen.queryByTestId('earnings-notice')).not.toBeInTheDocument()
  })

  // --- Loading State ---

  it('shows loading indicator when data is loading', async () => {
    const { useApproveEvaluations } = await import('@/hooks/useApi')
    vi.mocked(useApproveEvaluations).mockReturnValueOnce({
      data: undefined,
      isLoading: true,
      error: null,
      refetch: mockRefetch,
      dataUpdatedAt: 0,
    } as any)

    renderPage()
    expect(screen.getByText('Loading opportunities...')).toBeInTheDocument()
  })

  // --- Error State ---

  it('shows error state with retry button', async () => {
    const { useApproveEvaluations } = await import('@/hooks/useApi')
    vi.mocked(useApproveEvaluations).mockReturnValueOnce({
      data: undefined,
      isLoading: false,
      error: new Error('Network error'),
      refetch: mockRefetch,
      dataUpdatedAt: 0,
    } as any)

    renderPage()
    expect(screen.getByText('Unable to Load Opportunities')).toBeInTheDocument()
    expect(screen.getByText('Network error')).toBeInTheDocument()

    // Click retry
    fireEvent.click(screen.getByText('Retry'))
    expect(mockRefetch).toHaveBeenCalled()
  })

  // --- Empty State ---

  it('shows empty state when no evaluations', async () => {
    const { useApproveEvaluations } = await import('@/hooks/useApi')
    vi.mocked(useApproveEvaluations).mockReturnValueOnce({
      data: { evaluations: [], excludedForEarnings: [] },
      isLoading: false,
      error: null,
      refetch: mockRefetch,
      dataUpdatedAt: Date.now(),
    } as any)

    renderPage()
    expect(screen.getByText('No Active Opportunities')).toBeInTheDocument()
    expect(
      screen.getByText(/No contracts have passed all evaluation gates yet/),
    ).toBeInTheDocument()

    // Check for Updates button
    fireEvent.click(screen.getByText('Check for Updates'))
    expect(mockRefetch).toHaveBeenCalled()
  })
})
