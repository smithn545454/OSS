import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import PaperTrading from '../pages/PaperTrading'

// ---------------------------------------------------------------------------
// Mock child components (complex sub-trees with their own data fetching)
// ---------------------------------------------------------------------------

vi.mock('@/components/paper-trading/FilterBar', () => ({
  default: ({ values, onChange }: any) => (
    <div data-testid="filter-bar">FilterBar</div>
  ),
  useFilterParams: () => [
    { period: '30d', verdict: 'ALL', scanner: 'all', status: 'all' },
    vi.fn(),
  ],
}))

vi.mock('@/components/paper-trading/KPIStrip', () => ({
  default: (props: any) => <div data-testid="kpi-strip">KPIStrip</div>,
}))

vi.mock('@/components/paper-trading/PerformanceOverview', () => ({
  default: ({ positions }: any) => (
    <div data-testid="performance-overview">
      PerformanceOverview ({positions.length} positions)
    </div>
  ),
}))

vi.mock('@/components/paper-trading/PositionTracker', () => ({
  default: ({ positions }: any) => (
    <div data-testid="position-tracker">
      PositionTracker ({positions.length} positions)
    </div>
  ),
}))

vi.mock('@/components/paper-trading/ScoreCalibration', () => ({
  default: ({ positions }: any) => (
    <div data-testid="score-calibration">
      ScoreCalibration ({positions.length} positions)
    </div>
  ),
}))

vi.mock('@/components/paper-trading/AIStrategyAdvisor', () => ({
  default: () => <div data-testid="ai-strategy-advisor">AIStrategyAdvisor</div>,
}))

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

const mockMutate = vi.fn()

vi.mock('@/hooks/useEnrichedPositions', () => ({
  useEnrichedPositions: vi.fn(() => ({
    enrichedPositions: [
      {
        position_id: 'pos-1',
        option_ticker: 'O:AAPL250321C00175000',
        entry_price: 5.0,
        current_price: 6.5,
        current_pnl_pct: 30,
        status: 'OPEN',
        entry_date: '2025-03-01',
        evaluation_id: 'eval-1',
        verdict_at_entry: 'APPROVE',
        underlying_ticker: 'AAPL',
        conviction_score: 82,
        scanner_source: 'BREAKOUT',
      },
      {
        position_id: 'pos-2',
        option_ticker: 'O:TSLA250418P00250000',
        entry_price: 8.0,
        current_price: 7.0,
        current_pnl_pct: -12.5,
        status: 'CLOSED',
        entry_date: '2025-02-15',
        evaluation_id: 'eval-2',
        verdict_at_entry: 'APPROVE',
        underlying_ticker: 'TSLA',
        conviction_score: 71,
        scanner_source: 'UNUSUAL_VOLUME',
        exit_price: 7.0,
      },
    ],
    isLoading: false,
    error: null,
    rawMetrics: {
      totalPnl: 150,
      winRate: 50,
      avgReturn: 8.75,
      avgScore: 76.5,
      bestTrade: { ticker: 'AAPL', pnl: 30 },
      activeCount: 1,
      closedCount: 1,
    },
  })),
}))

vi.mock('@/hooks/useApi', () => ({
  useUpdatePositions: vi.fn(() => ({
    mutate: mockMutate,
    isPending: false,
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
        <PaperTrading />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('PaperTrading Page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // --- Basic rendering ---

  it('renders page title "Paper Trading"', () => {
    renderPage()
    expect(screen.getByText('Paper Trading')).toBeInTheDocument()
  })

  it('renders "AI Workstation" badge', () => {
    renderPage()
    expect(screen.getByText('AI Workstation')).toBeInTheDocument()
  })

  // --- Tab labels ---

  it('renders all 4 tab labels', () => {
    renderPage()
    expect(screen.getByText('Performance Overview')).toBeInTheDocument()
    expect(screen.getByText('Position Tracker')).toBeInTheDocument()
    expect(screen.getByText('Score Calibration')).toBeInTheDocument()
    expect(screen.getByText('AI Strategy Advisor')).toBeInTheDocument()
  })

  // --- Tab switching ---

  it('shows Performance Overview tab content by default', () => {
    renderPage()
    expect(screen.getByTestId('performance-overview')).toBeInTheDocument()
    expect(screen.queryByTestId('position-tracker')).not.toBeInTheDocument()
  })

  it('switches to Position Tracker tab when clicked', () => {
    renderPage()
    fireEvent.click(screen.getByText('Position Tracker'))

    expect(screen.getByTestId('position-tracker')).toBeInTheDocument()
    expect(screen.queryByTestId('performance-overview')).not.toBeInTheDocument()
  })

  it('switches to Score Calibration tab when clicked', () => {
    renderPage()
    fireEvent.click(screen.getByText('Score Calibration'))

    expect(screen.getByTestId('score-calibration')).toBeInTheDocument()
    expect(screen.queryByTestId('performance-overview')).not.toBeInTheDocument()
  })

  it('switches to AI Strategy Advisor tab when clicked', () => {
    renderPage()
    fireEvent.click(screen.getByText('AI Strategy Advisor'))

    expect(screen.getByTestId('ai-strategy-advisor')).toBeInTheDocument()
    expect(screen.queryByTestId('performance-overview')).not.toBeInTheDocument()
  })

  // --- Loading state ---

  it('displays loading state when data is loading', async () => {
    const { useEnrichedPositions } = await import('@/hooks/useEnrichedPositions')
    vi.mocked(useEnrichedPositions).mockReturnValueOnce({
      enrichedPositions: [],
      isLoading: true,
      error: null,
      rawMetrics: {
        totalPnl: 0,
        winRate: null,
        avgReturn: 0,
        avgScore: null,
        bestTrade: null,
        activeCount: 0,
        closedCount: 0,
      },
    })

    renderPage()
    expect(screen.getByText('Loading paper trading data...')).toBeInTheDocument()
    // Tab content should NOT be visible during loading
    expect(screen.queryByTestId('performance-overview')).not.toBeInTheDocument()
  })

  // --- Sync button ---

  it('renders sync button with "Sync Prices" text', () => {
    renderPage()
    expect(screen.getByText('Sync Prices')).toBeInTheDocument()
  })

  it('calls updatePositions.mutate when sync button is clicked', () => {
    renderPage()
    fireEvent.click(screen.getByText('Sync Prices'))
    expect(mockMutate).toHaveBeenCalledTimes(1)
  })

  it('shows "Syncing..." text when sync is pending', async () => {
    const { useUpdatePositions } = await import('@/hooks/useApi')
    vi.mocked(useUpdatePositions).mockReturnValueOnce({
      mutate: mockMutate,
      isPending: true,
    } as any)

    renderPage()
    expect(screen.getByText('Syncing...')).toBeInTheDocument()
    expect(screen.queryByText('Sync Prices')).not.toBeInTheDocument()
  })

  // --- Position count ---

  it('displays position count', () => {
    renderPage()
    expect(screen.getByText('2 positions')).toBeInTheDocument()
  })

  // --- Error state ---

  it('displays error message when there is an error', async () => {
    const { useEnrichedPositions } = await import('@/hooks/useEnrichedPositions')
    vi.mocked(useEnrichedPositions).mockReturnValueOnce({
      enrichedPositions: [],
      isLoading: false,
      error: new Error('Failed to fetch'),
      rawMetrics: {
        totalPnl: 0,
        winRate: null,
        avgReturn: 0,
        avgScore: null,
        bestTrade: null,
        activeCount: 0,
        closedCount: 0,
      },
    })

    renderPage()
    expect(screen.getByText(/Failed to fetch/)).toBeInTheDocument()
  })

  // --- Sub-components are rendered ---

  it('renders FilterBar component', () => {
    renderPage()
    expect(screen.getByTestId('filter-bar')).toBeInTheDocument()
  })

  it('renders KPIStrip component', () => {
    renderPage()
    expect(screen.getByTestId('kpi-strip')).toBeInTheDocument()
  })
})
