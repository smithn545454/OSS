import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import PaperTradingWorkstation from '../pages/PaperTradingWorkstation'

const mockSummary = {
  data: {
    positions: { open: 3, closed: 10, total: 13 },
    open_positions_summary: {
      total_pnl_pct: 12.5,
      avg_pnl_pct: 4.17,
      positions_in_profit: 2,
      positions_in_loss: 1,
    },
    performance: {
      win_rate: 60.0,
      avg_win_pct: 35.0,
      avg_loss_pct: -20.0,
      expectancy: 13.0,
    },
    recent_closes: [],
  },
  isLoading: false,
  error: null,
}

const mockOpenPositions = {
  data: {
    positions: [
      {
        position_id: 'pos-1',
        evaluation_id: 'eval-1',
        option_ticker: 'O:AAPL260320C00185000',
        entry_price: 5.0,
        entry_date: '2026-01-15',
        quantity: 1,
        verdict_at_entry: 'APPROVE',
        quality_tier_at_entry: 'TIER_1',
        exit_price: null,
        exit_date: null,
        exit_reason: null,
        current_price: 5.5,
        current_pnl_pct: 10.0,
        max_favorable_excursion: 15.0,
        max_adverse_excursion: 5.0,
        days_held: 5,
        status: 'OPEN',
        last_updated: '2026-01-20',
      },
    ],
    count: 1,
    filter: { status: 'open' },
  },
  isLoading: false,
  error: null,
}

const mockClosedPositions = {
  data: {
    positions: [],
    count: 0,
    filter: { status: 'closed' },
  },
  isLoading: false,
  error: null,
}

const mockMetrics = {
  data: {
    metrics: {
      total_positions: 13,
      open_positions: 3,
      closed_positions: 10,
      win_count: 6,
      loss_count: 4,
      win_rate: 60.0,
      loss_rate: 40.0,
      avg_win_pct: 35.0,
      avg_loss_pct: -20.0,
      best_trade_pct: 85.0,
      worst_trade_pct: -45.0,
      expectancy: 13.0,
      profit_factor: 2.63,
      avg_mfe: 40.0,
      avg_mae: 15.0,
      max_mfe: 90.0,
      max_mae: 50.0,
      mfe_capture_ratio: 87.5,
      avg_days_held: 8.5,
      max_days_held: 21,
      exit_distribution: { PROFIT_TARGET: 5, STOP_LOSS: 3, TIME_EXIT: 2 },
      tier_performance: {},
      approve_performance: { win_rate: 65, avg_return: 18, count: 8 },
      watch_performance: { win_rate: 50, avg_return: 5, count: 2 },
    },
    targets: {
      approve_win_rate: '> 55%',
      approve_avg_return: '> 25%',
    },
  },
  isLoading: false,
  error: null,
}

const mockTiers = {
  data: {
    tier_comparison: {
      TIER_1: { total: 5, closed: 4, wins: 3, losses: 1, win_rate: 75.0, avg_return: 25.0 },
      TIER_2: { total: 4, closed: 3, wins: 2, losses: 1, win_rate: 66.7, avg_return: 10.0 },
    },
    expectation: 'TIER_1 > TIER_2 > TIER_3',
  },
  isLoading: false,
  error: null,
}

const mockExits = {
  data: {
    exit_analysis: {
      PROFIT_TARGET: { count: 5, avg_return: 40.0, avg_mfe: 45.0, avg_mae: 8.0, avg_days_held: 6.0 },
    },
    insights: ['Exit strategy appears well-balanced'],
  },
  isLoading: false,
  error: null,
}

const mockAIInsights = {
  data: {
    insights: [],
    generated_at: '2026-01-20T12:00:00Z',
    data_summary: {
      positions_analyzed: 13,
      win_rate: 60.0,
      closed_positions: 10,
      calibration_report_used: null,
    },
    message: 'Need at least 5 closed positions for meaningful insights',
    llm_provider: null,
    tokens_used: 0,
  },
  isLoading: false,
  isFetching: false,
  error: null,
  refetch: vi.fn(),
}

const mockUpdatePositions = {
  mutate: vi.fn(),
  isPending: false,
}

const mockClosePosition = {
  mutate: vi.fn(),
  isPending: false,
}

// Mock all hooks
vi.mock('@/hooks/useApi', () => ({
  usePaperTradingSummary: vi.fn(() => mockSummary),
  usePaperTradingPositions: vi.fn((status?: string) =>
    status === 'closed' ? mockClosedPositions : mockOpenPositions
  ),
  usePaperTradingMetrics: vi.fn(() => mockMetrics),
  usePaperTradingTiers: vi.fn(() => mockTiers),
  usePaperTradingExits: vi.fn(() => mockExits),
  useAIInsights: vi.fn(() => mockAIInsights),
  useUpdatePositions: vi.fn(() => mockUpdatePositions),
  useClosePosition: vi.fn(() => mockClosePosition),
}))

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('PaperTradingWorkstation', () => {
  it('renders page title', () => {
    renderWithProviders(<PaperTradingWorkstation />)
    expect(screen.getByText('Paper Trading Workstation')).toBeTruthy()
  })

  it('renders all sections', () => {
    renderWithProviders(<PaperTradingWorkstation />)
    // Open Positions appears in both the stat card label and section header
    expect(screen.getAllByText('Open Positions').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Performance Analytics')).toBeTruthy()
    expect(screen.getByText('AI System Insights')).toBeTruthy()
    // "Closed Positions" section may include count suffix
    expect(screen.getByText(/Closed Positions/)).toBeTruthy()
  })

  it('shows summary stats', () => {
    renderWithProviders(<PaperTradingWorkstation />)
    // Open positions count
    expect(screen.getByText('3')).toBeTruthy()
    // Win rate
    expect(screen.getByText('60.0')).toBeTruthy()
  })

  it('renders open position ticker', () => {
    renderWithProviders(<PaperTradingWorkstation />)
    expect(screen.getByText('O:AAPL260320C00185000')).toBeTruthy()
  })

  it('shows update button', () => {
    renderWithProviders(<PaperTradingWorkstation />)
    expect(screen.getByText('Update Prices')).toBeTruthy()
  })

  it('update button triggers mutation', () => {
    renderWithProviders(<PaperTradingWorkstation />)
    const button = screen.getByText('Update Prices')
    fireEvent.click(button)
    expect(mockUpdatePositions.mutate).toHaveBeenCalled()
  })

  it('shows analytics tabs', () => {
    renderWithProviders(<PaperTradingWorkstation />)
    expect(screen.getByText('Overview')).toBeTruthy()
    expect(screen.getByText('By Tier')).toBeTruthy()
    expect(screen.getByText('By Exit')).toBeTruthy()
  })

  it('shows loading state', async () => {
    const { usePaperTradingSummary } = await import('@/hooks/useApi')
    vi.mocked(usePaperTradingSummary).mockReturnValueOnce({
      data: undefined,
      isLoading: true,
      error: null,
    } as any)

    renderWithProviders(<PaperTradingWorkstation />)
    // Should render without crashing during loading
    expect(document.body).toBeTruthy()
  })

  it('shows empty state for closed positions', () => {
    renderWithProviders(<PaperTradingWorkstation />)
    expect(screen.getByText('No closed positions yet.')).toBeTruthy()
  })
})
